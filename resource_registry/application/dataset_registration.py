from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from django.db import IntegrityError
from django.utils.text import slugify

from resource_registry.application.commands import RegisterDatasetCommand
from resource_registry.application.exceptions import (
    DataRegistryApplicationError,
    DatasetRegistrationError,
    DuplicateDatasetError,
    ValidationError,
)
from resource_registry.application.idempotency import (
    DatasetRegistrationIdempotencyService,
    IdempotencyResolution,
)
from resource_registry.application.storage_strategy import (
    DatasetStorageMode,
    DatasetStoragePlan,
    DatasetStorageStrategy,
)
from resource_registry.application.validators import RegisterDatasetCommandValidator
from resource_registry.events.audit_service import AuditService
from resource_registry.management_domain.source_writes import require_source_write
from resource_registry.models import (
    Dataset,
    DatasetStatus,
    WorkbookIngestionGroup,
    WorkbookIngestionJobStatus,
)
from resource_registry.policies.exceptions import InvalidPolicyConfigurationError
from resource_registry.policies.provenance import enforcement_allow, enforcement_reject
from resource_registry.security.artifact_guardrails import ArtifactGuardrailValidator
from resource_registry.security.exceptions import ArtifactGuardrailViolation
from resource_registry.security.policies import ArtifactMaterializationPolicyResolver
from resource_registry.services.canonical_dataset_builder import CanonicalDatasetBuilder
from resource_registry.services.dataset_context_builder import DatasetContextBuilder
from resource_registry.services.dataset_formats import normalize_dataset_format
from resource_registry.services.excel_dataset_adapter import (
    EmptyExcelWorksheetError,
    ExcelColumnLimitExceededError,
    ExcelDatasetAdapter,
    ExcelFileSizeLimitExceededError,
    ExcelRowLimitExceededError,
    UnsupportedExcelWorkbookError,
    WorksheetNotFoundError,
)
from resource_registry.services.registry.artifact_lifecycle_service import ArtifactLifecycleService
from resource_registry.services.registry.dataset_registry_service import DatasetRegistryService
from resource_registry.services.registry.workbook_group_service import WorkbookIngestionGroupService
from resource_registry.services.schema_quality_assessor import SchemaQualityAssessor
from resource_registry.services.workbook_ingestion_job_service import WorkbookIngestionJobService
from resource_registry.storage.base import StorageObjectMetadata, to_dataset_artifact_fields
from resource_registry.storage.exceptions import UnsafeStorageUriError
from resource_registry.storage.factory import DataRegistryStorageFactory

DATASET_NAME_MAX_LENGTH = 255
_logger = logging.getLogger("app-logger")


@dataclass(frozen=True)
class RegisterDatasetResult:
    dataset_id: Any
    dataset_version_id: Any
    schema_id: Any
    profile_id: Any
    artifact_id: Any
    row_count: int
    column_count: int
    source_format: str
    file_size_bytes: int
    workbook_group_id: Any | None = None


@dataclass(frozen=True)
class SkippedWorksheet:
    sheet_name: str
    code: str
    message: str


@dataclass(frozen=True)
class RegisterDatasetBatchResult:
    created: list[RegisterDatasetResult]
    deduplicated: list[RegisterDatasetResult]
    skipped: list[SkippedWorksheet]
    total_processed: int
    total_created: int
    total_deduplicated: int
    total_skipped: int


@dataclass(frozen=True)
class StartWorkbookIngestionResult:
    ingestion_job_id: str
    status: str


@dataclass(frozen=True)
class WorkbookIngestionJobStatusResult:
    ingestion_job_id: str
    status: str
    tenant_id: str
    workbook_group_id: str | None
    created_at: str
    started_at: str | None
    completed_at: str | None
    error_code: str | None
    error_message: str | None
    retry_count: int


@dataclass(frozen=True)
class WorkbookIngestionJobResult:
    ingestion_job_id: str
    status: str
    result: RegisterDatasetBatchResult | None
    error_code: str | None
    error_message: str | None


class DatasetRegistrationFacade:
    """Application workflow for dataset registration."""

    def __init__(
        self,
        *,
        context_builder: DatasetContextBuilder | None = None,
        registry_service: DatasetRegistryService | None = None,
        validator: RegisterDatasetCommandValidator | None = None,
        idempotency_service: DatasetRegistrationIdempotencyService | None = None,
        audit_service: AuditService | None = None,
        storage_strategy: DatasetStorageStrategy | None = None,
        artifact_lifecycle_service: ArtifactLifecycleService | None = None,
        workbook_group_service: WorkbookIngestionGroupService | None = None,
        materialization_policy_resolver: ArtifactMaterializationPolicyResolver | None = None,
        guardrail_validator: ArtifactGuardrailValidator | None = None,
        canonical_dataset_builder: CanonicalDatasetBuilder | None = None,
        storage_factory: DataRegistryStorageFactory | None = None,
        workbook_job_service: WorkbookIngestionJobService | None = None,
        schema_quality_assessor: SchemaQualityAssessor | None = None,
    ) -> None:
        self._context_builder = context_builder or DatasetContextBuilder()
        self._registry_service = registry_service or DatasetRegistryService()
        self._validator = validator or RegisterDatasetCommandValidator()
        self._idempotency_service = idempotency_service or DatasetRegistrationIdempotencyService()
        self._audit_service = audit_service or AuditService()
        self._storage_strategy = storage_strategy or DatasetStorageStrategy()
        self._artifact_lifecycle_service = artifact_lifecycle_service or ArtifactLifecycleService(
            audit_service=self._audit_service,
        )
        self._workbook_group_service = workbook_group_service or WorkbookIngestionGroupService()
        self._materialization_policy_resolver = (
            materialization_policy_resolver or ArtifactMaterializationPolicyResolver()
        )
        self._guardrail_validator = guardrail_validator or ArtifactGuardrailValidator()
        self._canonical_dataset_builder = canonical_dataset_builder or CanonicalDatasetBuilder()
        self._storage_factory = storage_factory or DataRegistryStorageFactory()
        self._excel_adapter = ExcelDatasetAdapter()
        self._workbook_job_service = workbook_job_service or WorkbookIngestionJobService()
        self._schema_quality_assessor = schema_quality_assessor or SchemaQualityAssessor()

    def start_workbook_ingestion(
        self,
        command: RegisterDatasetCommand,
    ) -> StartWorkbookIngestionResult:
        self._validator.validate(command, allow_existing_slug=True)
        job = self._workbook_job_service.create_job(
            tenant_id=str(command.tenant_id),
            command_payload=self._command_to_payload(command),
            created_by=command.created_by,
            metadata={
                "source_slug_base": command.slug,
                "ingest_all_sheets": bool(command.ingest_all_sheets),
                "path": command.path,
            },
        )
        return StartWorkbookIngestionResult(
            ingestion_job_id=str(job.id),
            status=str(job.status),
        )

    def get_workbook_ingestion_status(
        self,
        *,
        tenant_id: str,
        ingestion_job_id: str,
    ) -> WorkbookIngestionJobStatusResult:
        job = self._workbook_job_service.get_job(
            tenant_id=tenant_id,
            ingestion_job_id=ingestion_job_id,
        )
        return WorkbookIngestionJobStatusResult(
            ingestion_job_id=str(job.id),
            status=str(job.status),
            tenant_id=str(job.tenant_id),
            workbook_group_id=(str(job.workbook_group_id) if job.workbook_group_id else None),
            created_at=job.created_at.isoformat(),
            started_at=job.started_at.isoformat() if job.started_at else None,
            completed_at=job.completed_at.isoformat() if job.completed_at else None,
            error_code=str(job.error_code) if job.error_code else None,
            error_message=str(job.error_message) if job.error_message else None,
            retry_count=int(job.retry_count or 0),
        )

    def get_workbook_ingestion_result(
        self,
        *,
        tenant_id: str,
        ingestion_job_id: str,
    ) -> WorkbookIngestionJobResult:
        job = self._workbook_job_service.get_job(
            tenant_id=tenant_id,
            ingestion_job_id=ingestion_job_id,
        )
        if str(job.status) in {
            WorkbookIngestionJobStatus.COMPLETED,
            WorkbookIngestionJobStatus.COMPLETED_WITH_SKIPS,
        }:
            return WorkbookIngestionJobResult(
                ingestion_job_id=str(job.id),
                status=str(job.status),
                result=self._result_from_payload(dict(job.result_payload_json or {})),
                error_code=None,
                error_message=None,
            )
        if str(job.status) == WorkbookIngestionJobStatus.FAILED:
            return WorkbookIngestionJobResult(
                ingestion_job_id=str(job.id),
                status=str(job.status),
                result=None,
                error_code=str(job.error_code) if job.error_code else None,
                error_message=str(job.error_message) if job.error_message else None,
            )
        raise ValidationError(
            "The workbook ingestion job has not completed yet.",
            status=409,
            code="workbook_ingestion_not_completed",
        )

    def register_workbook(
        self,
        command: RegisterDatasetCommand,
        *,
        pre_uploaded_source_metadata: StorageObjectMetadata | None = None,
        canonical_provider_key: str | None = None,
    ) -> RegisterDatasetBatchResult:
        workbook_mode = self._is_xlsx_path(command.path) and command.ingest_all_sheets
        if not workbook_mode:
            single_result = self.register(
                command,
                pre_uploaded_source_metadata=pre_uploaded_source_metadata,
                canonical_provider_key=canonical_provider_key,
            )
            return RegisterDatasetBatchResult(
                created=[single_result],
                deduplicated=[],
                skipped=[],
                total_processed=1,
                total_created=1,
                total_deduplicated=0,
                total_skipped=0,
            )

        precomputed_content_hash = self._idempotency_service.compute_content_hash(command.path)
        shared_source_metadata = pre_uploaded_source_metadata
        resolved_provider_key = canonical_provider_key
        if (
            shared_source_metadata is None
            and str(command.storage_mode).strip().upper() == DatasetStorageMode.MANAGED_UPLOAD.value
        ):
            shared_source_metadata, resolved_provider_key = self._upload_workbook_source_once(
                command=command,
                content_hash=precomputed_content_hash,
            )

        source_reference = (
            str(shared_source_metadata.uri)
            if shared_source_metadata is not None
            else str(command.path)
        )
        source_file_name = (
            str(shared_source_metadata.metadata.get("source_file_name") or "")
            if shared_source_metadata is not None
            else ""
        ) or Path(command.path).name

        workbook_group = self._workbook_group_service.get_or_create_group(
            tenant_id=str(command.tenant_id),
            source_uri=source_reference,
            source_hash=precomputed_content_hash,
            source_file_name=source_file_name,
            source_storage_provider=(
                str(shared_source_metadata.provider)
                if shared_source_metadata is not None
                else str(command.storage_provider or "")
            ),
            metadata_json={
                "source_slug_base": command.slug,
                "ingest_all_sheets": bool(command.ingest_all_sheets),
            },
        )

        sheet_names = self._resolve_target_sheets_for_registration(
            command=command,
            workbook_group=workbook_group,
        )
        proposed_slugs: set[str] = set()
        for sheet_name in sheet_names:
            proposed_slug = self._derive_sheet_slug(
                base_slug=command.slug, sheet_name=sheet_name, used_slugs=proposed_slugs
            )
            RegisterDatasetCommandValidator.require_explicit_update_if_enabled(
                command.tenant_id, proposed_slug
            )
        created: list[RegisterDatasetResult] = []
        deduplicated: list[RegisterDatasetResult] = []
        skipped: list[SkippedWorksheet] = []
        used_slugs: set[str] = set()

        self._safe_audit(
            lambda: self._audit_service.record_dataset_registered(
                tenant_id=str(command.tenant_id),
                dataset_id="workbook_batch",
                dataset_version_id=None,
                actor=self._extract_actor(command.created_by),
                metadata={
                    "mode": "workbook_batch_started",
                    "sheet_count": len(sheet_names),
                    "path": source_reference,
                },
            ),
        )

        sheet_count = len(sheet_names)
        for sheet_name in sheet_names:
            sheet_slug = self._derive_sheet_slug(
                base_slug=command.slug,
                sheet_name=sheet_name,
                used_slugs=used_slugs,
            )
            sheet_dataset_name = self._derive_sheet_dataset_name(
                base_name=command.name,
                sheet_name=sheet_name,
                sheet_count=sheet_count,
            )
            RegisterDatasetCommandValidator.require_explicit_update_if_enabled(
                command.tenant_id, sheet_slug
            )
            idempotency_resolution = self._idempotency_service.resolve(
                tenant_id=command.tenant_id,
                slug=sheet_slug,
                path=command.path,
                precomputed_content_hash=precomputed_content_hash,
            )
            sheet_command = RegisterDatasetCommand(
                **{
                    **command.__dict__,
                    "slug": sheet_slug,
                    "name": sheet_dataset_name,
                    "sheet_name": sheet_name,
                },
            )
            try:
                result = self.register(
                    sheet_command,
                    precomputed_content_hash=precomputed_content_hash,
                    workbook_group=workbook_group,
                    pre_uploaded_source_metadata=shared_source_metadata,
                    canonical_provider_key=resolved_provider_key,
                )
            except ValidationError as error:
                if self._is_skippable_sheet_validation_error(error):
                    error_code = str(error.code or "validation_error")
                    skipped.append(
                        SkippedWorksheet(
                            sheet_name=sheet_name,
                            code=error_code,
                            message=str(error),
                        ),
                    )
                    self._safe_audit(
                        lambda sheet_name=sheet_name, error_code=error_code: (
                            self._audit_service.record_dataset_registered(
                                tenant_id=str(command.tenant_id),
                                dataset_id="workbook_batch",
                                dataset_version_id=None,
                                actor=self._extract_actor(command.created_by),
                                metadata={
                                    "mode": "workbook_sheet_skipped",
                                    "sheet_name": sheet_name,
                                    "code": error_code,
                                },
                            )
                        ),
                    )
                    continue
                self._workbook_group_service.mark_failed(group=workbook_group)
                raise
            except Exception:
                self._workbook_group_service.mark_failed(group=workbook_group)
                raise

            if idempotency_resolution.existing_version is not None:
                deduplicated.append(result)
                self._safe_audit(
                    lambda result=result, sheet_name=sheet_name, sheet_slug=sheet_slug: (
                        self._audit_service.record_dataset_registered(
                            tenant_id=str(command.tenant_id),
                            dataset_id=str(result.dataset_id),
                            dataset_version_id=str(result.dataset_version_id),
                            actor=self._extract_actor(command.created_by),
                            metadata={
                                "mode": "workbook_sheet_deduplicated",
                                "sheet_name": sheet_name,
                                "slug": sheet_slug,
                            },
                        )
                    ),
                )
            else:
                created.append(result)
                self._safe_audit(
                    lambda result=result, sheet_name=sheet_name, sheet_slug=sheet_slug: (
                        self._audit_service.record_dataset_registered(
                            tenant_id=str(command.tenant_id),
                            dataset_id=str(result.dataset_id),
                            dataset_version_id=str(result.dataset_version_id),
                            actor=self._extract_actor(command.created_by),
                            metadata={
                                "mode": "workbook_sheet_created",
                                "sheet_name": sheet_name,
                                "slug": sheet_slug,
                            },
                        )
                    ),
                )

        if not created and not deduplicated:
            self._workbook_group_service.mark_failed(group=workbook_group)
            raise ValidationError(
                "The workbook does not contain any valid worksheet for ingestion.",
                status=400,
                code="workbook_has_no_valid_sheets",
            )

        processed = len(created) + len(deduplicated) + len(skipped)
        self._workbook_group_service.mark_completed(
            group=workbook_group,
            total_sheets=len(sheet_names),
            total_created=len(created),
            total_deduplicated=len(deduplicated),
            total_skipped=len(skipped),
        )
        self._safe_audit(
            lambda: self._audit_service.record_dataset_registered(
                tenant_id=str(command.tenant_id),
                dataset_id="workbook_batch",
                dataset_version_id=None,
                actor=self._extract_actor(command.created_by),
                metadata={
                    "mode": "workbook_batch_completed",
                    "total_processed": processed,
                    "total_created": len(created),
                    "total_deduplicated": len(deduplicated),
                    "total_skipped": len(skipped),
                },
            ),
        )
        return RegisterDatasetBatchResult(
            created=created,
            deduplicated=deduplicated,
            skipped=skipped,
            total_processed=processed,
            total_created=len(created),
            total_deduplicated=len(deduplicated),
            total_skipped=len(skipped),
        )

    def register(
        self,
        command: RegisterDatasetCommand,
        *,
        precomputed_content_hash: str | None = None,
        workbook_group: WorkbookIngestionGroup | None = None,
        pre_uploaded_source_metadata: StorageObjectMetadata | None = None,
        canonical_provider_key: str | None = None,
    ) -> RegisterDatasetResult:
        self._validator.validate(command, allow_existing_slug=True)
        persisted_source_reference = (
            str(pre_uploaded_source_metadata.uri)
            if pre_uploaded_source_metadata is not None
            else str(command.path)
        )
        guardrail_format_reference = (
            str(pre_uploaded_source_metadata.metadata.get("source_file_name") or "")
            if pre_uploaded_source_metadata is not None
            else ""
        ) or persisted_source_reference
        try:
            resolved_materialization_policy = (
                self._materialization_policy_resolver.resolve_with_provenance(
                    tenant_id=str(command.tenant_id),
                )
            )
            materialization_policy = resolved_materialization_policy.policy
            provenance = resolved_materialization_policy.provenance
            if provenance is None:
                raise InvalidPolicyConfigurationError(
                    "Materialization policy resolution did not include provenance.",
                )
            self._safe_audit(
                lambda: self._audit_service.record_artifact_materialization_policy_resolved(
                    tenant_id=str(command.tenant_id),
                    metadata={
                        "allow_local_files": materialization_policy.allow_local_files,
                        "allow_remote_uris": materialization_policy.allow_remote_uris,
                        "require_checksum": materialization_policy.require_checksum,
                    },
                ),
            )
            self._safe_audit(
                lambda: self._audit_service.record_policy_provenance_resolved(
                    tenant_id=str(command.tenant_id),
                    metadata={
                        "policy_key": provenance.policy_key,
                        "source": str(provenance.source),
                        "revision": provenance.revision,
                        "rules_hash": provenance.rules_hash,
                        "resolution_path": provenance.resolution_path,
                    },
                ),
            )
            self._guardrail_validator.validate_before_materialization(
                uri=persisted_source_reference,
                artifact_format=self._guess_artifact_format(guardrail_format_reference),
                policy=materialization_policy,
                size_bytes=(
                    pre_uploaded_source_metadata.size_bytes
                    if pre_uploaded_source_metadata is not None
                    else self._safe_file_size(command.path)
                ),
                storage_provider=(
                    pre_uploaded_source_metadata.provider
                    if pre_uploaded_source_metadata is not None
                    else command.storage_provider
                ),
                metadata=(
                    pre_uploaded_source_metadata.metadata
                    if pre_uploaded_source_metadata is not None
                    else None
                ),
                checksum=(
                    pre_uploaded_source_metadata.checksum
                    if pre_uploaded_source_metadata is not None
                    else None
                ),
                policy_source=str(provenance.source),
                policy_revision=provenance.revision,
            )
            allow_context = enforcement_allow(
                policy_key="artifact_materialization_default",
                policy_source=str(provenance.source),
                policy_revision=provenance.revision,
                reason="guardrails_passed",
            )
            self._safe_audit(
                lambda: self._audit_service.record_policy_enforcement_recorded(
                    tenant_id=str(command.tenant_id),
                    metadata={
                        "policy_key": allow_context.policy_key,
                        "policy_source": allow_context.policy_source,
                        "policy_revision": allow_context.policy_revision,
                        "enforcement_result": allow_context.enforcement_result,
                        "enforcement_reason": allow_context.enforcement_reason,
                    },
                ),
            )
            context = self._context_builder.build_context(
                command.path,
                preview_limit=command.preview_limit,
                sheet_name=command.sheet_name,
                mime_type=command.mime_type,
            )
            context = self._attach_schema_quality_context(context)
            schema_quality = dict(context.get("schema_quality", {}))
            if str(schema_quality.get("confidence", "")).lower() == "low" and not bool(
                command.allow_low_confidence_schema
            ):
                raise ValidationError(
                    "Detected low-confidence schema. "
                    "Set allow_low_confidence_schema=True to override this safety check.",
                    status=400,
                    code="low_confidence_schema",
                )
            idempotency = self._idempotency_service.resolve(
                tenant_id=command.tenant_id,
                slug=command.slug,
                path=command.path,
                precomputed_content_hash=precomputed_content_hash,
            )
        except WorksheetNotFoundError as error:
            raise ValidationError(
                "The requested Excel worksheet was not found.",
                status=400,
                code="worksheet_not_found",
            ) from error
        except EmptyExcelWorksheetError as error:
            raise ValidationError(
                "The selected Excel worksheet does not contain tabular data.",
                status=400,
                code="empty_dataset",
            ) from error
        except UnsupportedExcelWorkbookError as error:
            raise ValidationError(
                "The Excel workbook is not supported.",
                status=400,
                code="unsupported_excel_workbook",
            ) from error
        except ExcelRowLimitExceededError as error:
            raise ValidationError(
                "The Excel worksheet exceeds the maximum supported row count.",
                status=413,
                code="excel_row_limit_exceeded",
            ) from error
        except ExcelColumnLimitExceededError as error:
            raise ValidationError(
                "The Excel worksheet exceeds the maximum supported column count.",
                status=413,
                code="excel_column_limit_exceeded",
            ) from error
        except ExcelFileSizeLimitExceededError as error:
            raise ValidationError(
                "The Excel workbook exceeds the maximum supported file size.",
                status=413,
                code="excel_file_size_limit_exceeded",
            ) from error
        except (ArtifactGuardrailViolation, InvalidPolicyConfigurationError) as error:
            error_type = error.__class__.__name__
            violation_metadata = getattr(error, "violation_metadata", {})
            reject_context = enforcement_reject(
                policy_key="artifact_materialization_default",
                policy_source=violation_metadata.get("policy_source"),
                policy_revision=violation_metadata.get("policy_revision"),
                reason=violation_metadata.get("violated_rule") or error_type,
            )
            self._safe_audit(
                lambda: self._audit_service.record_artifact_materialization_policy_rejected(
                    tenant_id=str(command.tenant_id),
                    metadata={
                        "error_type": error_type,
                        "path": persisted_source_reference,
                    },
                ),
            )
            self._safe_audit(
                lambda: self._audit_service.record_policy_enforcement_recorded(
                    tenant_id=str(command.tenant_id),
                    metadata={
                        "policy_key": reject_context.policy_key,
                        "policy_source": reject_context.policy_source,
                        "policy_revision": reject_context.policy_revision,
                        "enforcement_result": reject_context.enforcement_result,
                        "enforcement_reason": reject_context.enforcement_reason,
                    },
                ),
            )
            raise DatasetRegistrationError(
                "Dataset registration blocked by artifact materialization policy.",
            ) from error
        actor = self._extract_actor(command.created_by)
        try:
            if idempotency.existing_version is not None:
                existing_result = self._build_existing_result(
                    context=context,
                    resolution=idempotency,
                )
                self._activate_registered_dataset(idempotency.dataset, advance_version=False)
                self._safe_audit(
                    lambda: self._audit_service.record_dataset_deduplicated(
                        tenant_id=command.tenant_id,
                        dataset_id=str(existing_result.dataset_id),
                        dataset_version_id=str(existing_result.dataset_version_id),
                        actor=actor,
                        metadata={
                            "slug": command.slug,
                            "source_format": existing_result.source_format,
                            "file_size_bytes": existing_result.file_size_bytes,
                        },
                    ),
                )
                self._safe_audit(
                    lambda: self._audit_service.record_dataset_registered(
                        tenant_id=command.tenant_id,
                        dataset_id=str(existing_result.dataset_id),
                        dataset_version_id=str(existing_result.dataset_version_id),
                        actor=actor,
                        metadata={
                            "slug": command.slug,
                            "deduplicated": True,
                        },
                    ),
                )
                return existing_result

            source = context.get("source", {})
            if pre_uploaded_source_metadata is not None:
                source = dict(source)
                source["source_reused"] = True
            version_number = (
                self._idempotency_service.get_next_version_number(
                    dataset=idempotency.dataset,
                )
                if idempotency.dataset is not None
                else 1
            )
            storage_plan = self._storage_strategy.create_plan(
                command=command,
                version_number=version_number,
            )
            storage_metadata: StorageObjectMetadata | None = pre_uploaded_source_metadata
            if storage_metadata is not None:
                storage_plan = DatasetStoragePlan(
                    mode=DatasetStorageMode.MANAGED_UPLOAD,
                    should_upload=False,
                    provider=None,
                    provider_key=canonical_provider_key or storage_metadata.provider,
                    destination_path=None,
                )
            if storage_plan.should_upload:
                if storage_plan.provider is None or storage_plan.destination_path is None:
                    raise DatasetRegistrationError("Managed upload plan is incomplete.")
                storage_metadata = storage_plan.provider.put(
                    command.path,
                    storage_plan.destination_path,
                )
                self._ensure_safe_persisted_uri(storage_metadata.uri)

            dataset = None
            persistence_result = None
            canonical_artifact = None
            dataset_was_created = idempotency.dataset is None
            try:
                dataset = idempotency.dataset or self._registry_service.create_dataset(
                    tenant_id=command.tenant_id,
                    slug=command.slug,
                    name=command.name,
                    source_type=command.source_type,
                    organization_id=command.organization_id,
                    workspace_id=command.workspace_id,
                    visibility_scope=command.visibility_scope,
                    created_by=command.created_by,
                    description=command.description,
                    tags=command.tags,
                    owner_team=command.owner_team,
                    workbook_group=workbook_group,
                )
                if idempotency.dataset is not None and workbook_group is not None:
                    if getattr(dataset, "workbook_group_id", None) is None:
                        dataset.workbook_group = workbook_group
                        dataset.save(update_fields=["workbook_group"])
                if idempotency.dataset is not None:
                    version_number = self._idempotency_service.get_next_version_number(
                        dataset=dataset,
                    )
                persistence_context = self._replace_materialized_source_reference(
                    context=context,
                    storage_metadata=pre_uploaded_source_metadata,
                )
                persistence_result = self._registry_service.create_version_bundle_from_context(
                    dataset=dataset,
                    version_number=version_number,
                    content_hash=idempotency.content_hash,
                    context=persistence_context,
                )

                artifact_source = dict(persistence_context.get("source", source))
                source_artifact = self._attach_artifact(
                    command=command,
                    source=artifact_source,
                    dataset_version=persistence_result.dataset_version,
                    storage_metadata=storage_metadata,
                    workbook_group=workbook_group,
                )
                canonical_artifact = self._attach_canonical_artifact(
                    command=command,
                    source=source,
                    dataset=dataset,
                    dataset_version=persistence_result.dataset_version,
                    source_artifact=source_artifact,
                    storage_plan=storage_plan,
                    workbook_group=workbook_group,
                )
                self._activate_registered_dataset(dataset)
            except Exception:
                self._rollback_failed_registration(
                    command=command,
                    dataset=dataset,
                    dataset_was_created=dataset_was_created,
                    persistence_result=persistence_result,
                    canonical_artifact=canonical_artifact,
                )
                if storage_metadata is not None and storage_plan.provider is not None:
                    self._artifact_lifecycle_service.cleanup_orphan_upload(
                        storage_provider=storage_plan.provider,
                        uri=storage_metadata.uri,
                        tenant_id=command.tenant_id,
                        dataset_id=str(dataset.id) if dataset is not None else None,
                        dataset_version_id=(
                            str(persistence_result.dataset_version.id)
                            if persistence_result is not None
                            else None
                        ),
                        metadata={"reason": "db_persistence_failed"},
                    )
                raise

            result = self._build_new_result(
                context=context,
                dataset_id=dataset.id,
                dataset_version_id=persistence_result.dataset_version.id,
                schema_id=persistence_result.dataset_schema.id,
                profile_id=persistence_result.dataset_profile.id,
                artifact_id=source_artifact.id,
                workbook_group_id=getattr(dataset, "workbook_group_id", None),
            )
            self._safe_audit(
                lambda: self._audit_service.record_dataset_version_created(
                    tenant_id=command.tenant_id,
                    dataset_id=str(dataset.id),
                    dataset_version_id=str(persistence_result.dataset_version.id),
                    actor=actor,
                    metadata={
                        "slug": command.slug,
                        "source_type": command.source_type,
                        "content_hash": idempotency.content_hash,
                    },
                ),
            )
            self._safe_audit(
                lambda: self._audit_service.record_dataset_schema_created(
                    tenant_id=command.tenant_id,
                    dataset_id=str(dataset.id),
                    dataset_version_id=str(persistence_result.dataset_version.id),
                    metadata={
                        "schema_id": str(persistence_result.dataset_schema.id),
                        "column_count": result.column_count,
                    },
                ),
            )
            self._safe_audit(
                lambda: self._audit_service.record_dataset_profile_created(
                    tenant_id=command.tenant_id,
                    dataset_id=str(dataset.id),
                    dataset_version_id=str(persistence_result.dataset_version.id),
                    metadata={
                        "profile_id": str(persistence_result.dataset_profile.id),
                        "row_count": result.row_count,
                    },
                ),
            )
            if storage_metadata is None:
                self._safe_audit(
                    lambda: self._audit_service.record_dataset_artifact_registered(
                        tenant_id=command.tenant_id,
                        dataset_id=str(dataset.id),
                        dataset_version_id=str(persistence_result.dataset_version.id),
                        metadata={
                            "artifact_id": str(source_artifact.id),
                            "storage_provider": getattr(source_artifact, "storage_provider", None),
                            "artifact_role": getattr(
                                source_artifact,
                                "artifact_role",
                                command.artifact_role,
                            ),
                            "canonical_artifact_id": str(canonical_artifact.id),
                        },
                    ),
                )
            if storage_metadata is not None:
                self._safe_audit(
                    lambda: self._audit_service.record_dataset_artifact_uploaded(
                        tenant_id=command.tenant_id,
                        dataset_id=str(dataset.id),
                        dataset_version_id=str(persistence_result.dataset_version.id),
                        metadata={
                            "artifact_id": str(source_artifact.id),
                            "canonical_artifact_id": str(canonical_artifact.id),
                            "storage_provider": storage_metadata.provider,
                            "size_bytes": storage_metadata.size_bytes,
                        },
                    ),
                )
            self._safe_audit(
                lambda: self._audit_service.record_dataset_registered(
                    tenant_id=command.tenant_id,
                    dataset_id=str(dataset.id),
                    dataset_version_id=str(persistence_result.dataset_version.id),
                    actor=actor,
                    metadata={
                        "slug": command.slug,
                        "source_format": result.source_format,
                        "file_size_bytes": result.file_size_bytes,
                    },
                ),
            )
            return result
        except DataRegistryApplicationError:
            raise
        except IntegrityError as error:
            raise DuplicateDatasetError(
                "Dataset registration violates uniqueness constraints.",
            ) from error
        except UnsafeStorageUriError as error:
            raise DatasetRegistrationError("Unsafe storage URI for dataset artifact.") from error
        except Exception as error:  # pragma: no cover
            raise DatasetRegistrationError("Dataset registration failed.") from error

    def _build_existing_result(
        self,
        *,
        context: dict[str, Any],
        resolution: IdempotencyResolution,
    ) -> RegisterDatasetResult:
        if resolution.dataset is None:
            raise DatasetRegistrationError("Dataset missing for idempotent registration.")
        if (
            resolution.existing_version is None
            or resolution.existing_schema is None
            or resolution.existing_profile is None
        ):
            raise DatasetRegistrationError("Incomplete existing registration metadata.")

        return self._build_new_result(
            context=context,
            dataset_id=resolution.dataset.id,
            dataset_version_id=resolution.existing_version.id,
            schema_id=resolution.existing_schema.id,
            profile_id=resolution.existing_profile.id,
            artifact_id=resolution.existing_artifact.id if resolution.existing_artifact else None,
            workbook_group_id=getattr(resolution.dataset, "workbook_group_id", None),
        )

    def _build_new_result(
        self,
        *,
        context: dict[str, Any],
        dataset_id: Any,
        dataset_version_id: Any,
        schema_id: Any,
        profile_id: Any,
        artifact_id: Any,
        workbook_group_id: Any | None = None,
    ) -> RegisterDatasetResult:
        summary = context.get("summary", {})
        source = context.get("source", {})
        return RegisterDatasetResult(
            dataset_id=dataset_id,
            dataset_version_id=dataset_version_id,
            schema_id=schema_id,
            profile_id=profile_id,
            artifact_id=artifact_id,
            row_count=int(summary.get("row_count", 0)),
            column_count=int(summary.get("column_count", 0)),
            source_format=str(source.get("format", "")),
            file_size_bytes=int(source.get("file_size_bytes", 0)),
            workbook_group_id=workbook_group_id,
        )

    def _rollback_failed_registration(
        self,
        *,
        command: RegisterDatasetCommand,
        dataset: Any | None,
        dataset_was_created: bool,
        persistence_result: Any | None,
        canonical_artifact: Any | None,
    ) -> None:
        if canonical_artifact is not None:
            try:
                provider = self._storage_factory.create_provider(
                    getattr(canonical_artifact, "storage_provider", None),
                )
                self._artifact_lifecycle_service.cleanup_orphan_upload(
                    storage_provider=provider,
                    uri=str(canonical_artifact.uri),
                    tenant_id=str(command.tenant_id),
                    dataset_id=str(getattr(dataset, "id", "")) or None,
                    dataset_version_id=str(
                        getattr(getattr(persistence_result, "dataset_version", None), "id", "")
                    )
                    or None,
                    metadata={"reason": "registration_rollback"},
                )
            except Exception:
                _logger.warning(
                    "Canonical artifact rollback cleanup failed.",
                    extra={
                        "tenant_id": str(command.tenant_id),
                        "dataset_id": str(getattr(dataset, "id", "")),
                        "dataset_version_id": str(
                            getattr(
                                getattr(persistence_result, "dataset_version", None),
                                "id",
                                "",
                            )
                        ),
                    },
                )
        if persistence_result is not None:
            version_delete = getattr(persistence_result.dataset_version, "delete", None)
            if callable(version_delete):
                version_delete()
        versions = getattr(dataset, "versions", None)
        has_versions = True
        if versions is not None and callable(getattr(versions, "exists", None)):
            has_versions = bool(versions.exists())
        dataset_delete = getattr(dataset, "delete", None)
        if dataset_was_created and not has_versions and callable(dataset_delete):
            dataset_delete()

    def _activate_registered_dataset(
        self, dataset: Any | None, *, advance_version: bool = True
    ) -> None:
        """Activate a registration while preserving manual archives and replayed restorations.

        Args:
            dataset: Persisted dataset returned by registration.
            advance_version: Select the new version only for a fresh publication.
        """
        if dataset is None:
            return
        if isinstance(dataset, Dataset):
            require_source_write(dataset.external_source_id)
            dataset.refresh_from_db(fields=["archive_reason", "status", "archived_at"])
            if dataset.archive_reason == "MANUAL":
                return
            if advance_version or not dataset.current_version_id:
                current = (
                    dataset.versions.filter(status="READY").order_by("-version_number").first()
                )
                if current is not None:
                    dataset.current_version = current
                    dataset.revision += 1
                    dataset.save(update_fields=["current_version", "revision"])
        if (
            getattr(dataset, "status", None) == DatasetStatus.ACTIVE
            and getattr(
                dataset,
                "archived_at",
                None,
            )
            is None
        ):
            return
        dataset.status = DatasetStatus.ACTIVE
        dataset.archived_at = None
        save = getattr(dataset, "save", None)
        if callable(save):
            save(update_fields=["status", "archived_at", "updated_at"])

    def _safe_audit(self, audit_call: Callable[..., object]) -> None:
        try:
            audit_call()
        except Exception:
            return

    def _attach_artifact(
        self,
        *,
        command: RegisterDatasetCommand,
        source: dict[str, Any],
        dataset_version: Any,
        storage_metadata: StorageObjectMetadata | None,
        workbook_group: WorkbookIngestionGroup | None = None,
    ) -> Any:
        if storage_metadata is not None:
            source_format = str(source.get("format", "other")).upper()
            artifact_fields = to_dataset_artifact_fields(
                storage_metadata,
                artifact_role=command.artifact_role,
                artifact_format=source_format,
            )
            uri = str(artifact_fields["uri"])
            self._ensure_safe_persisted_uri(uri)
            metadata_json = dict(artifact_fields.get("metadata_json", {}))
            metadata_json.update(
                {
                    "file_name": source.get("file_name"),
                    "managed_upload": True,
                    "source_reused": bool(source.get("source_reused", False)),
                    "schema_detection": source.get("schema_detection", {}),
                    "schema_quality": source.get("schema_quality", {}),
                    "workbook_group_id": str(getattr(workbook_group, "id", "")),
                    "registered_via": "dataset_registration_facade",
                },
            )
            return self._artifact_lifecycle_service.register_uploaded_artifact(
                dataset_version=dataset_version,
                artifact_role=artifact_fields["artifact_role"],
                uri=uri,
                format=artifact_fields["format"],
                size_bytes=int(artifact_fields.get("size_bytes") or 0),
                checksum=artifact_fields.get("checksum"),
                storage_provider=artifact_fields.get("storage_provider"),
                workbook_group=workbook_group,
                sheet_name=str(source.get("sheet_name") or "").strip() or None,
                metadata_json=metadata_json,
            )

        return self._registry_service.attach_artifact(
            dataset_version=dataset_version,
            artifact_role=command.artifact_role,
            uri=str(source.get("path", command.path)),
            format=str(source.get("format", "other")).upper(),
            size_bytes=int(source.get("file_size_bytes", 0)),
            storage_provider=command.storage_provider,
            workbook_group=workbook_group,
            sheet_name=str(source.get("sheet_name") or "").strip() or None,
            metadata_json={
                "file_name": source.get("file_name"),
                "managed_upload": False,
                "schema_detection": source.get("schema_detection", {}),
                "schema_quality": source.get("schema_quality", {}),
                "workbook_group_id": str(getattr(workbook_group, "id", "")),
                "registered_via": "dataset_registration_facade",
            },
        )

    def _attach_canonical_artifact(
        self,
        *,
        command: RegisterDatasetCommand,
        source: dict[str, Any],
        dataset: Any,
        dataset_version: Any,
        source_artifact: Any,
        storage_plan: Any,
        workbook_group: WorkbookIngestionGroup | None = None,
    ) -> Any:
        normalized_at = datetime.now(UTC).isoformat()
        normalization_version = "v1"
        source_format = str(source.get("format", ""))
        source_path = str(source.get("path", command.path))
        if not Path(source_path).exists():
            return source_artifact

        with self._canonical_dataset_builder.build(
            source_path=source_path,
            source_format=source_format,
            sheet_name=str(source.get("sheet_name") or "").strip() or None,
        ) as canonical:
            provider = storage_plan.provider or self._storage_factory.create_provider(
                storage_plan.provider_key,
            )
            destination_path = (
                f"resource_registry/canonical/{command.tenant_id}/"
                f"{dataset.id}/{dataset_version.id}/dataset.parquet"
            )
            storage_metadata = provider.put(canonical.canonical_path, destination_path)
            uri = str(storage_metadata.uri)
            self._ensure_safe_persisted_uri(uri)

            metadata_json = {
                "is_canonical": True,
                "canonical_format": "PARQUET",
                "source_artifact_id": str(getattr(source_artifact, "id", "")),
                "source_artifact_uri": str(getattr(source_artifact, "uri", "")),
                "workbook_group_id": str(getattr(workbook_group, "id", "")),
                "sheet_name": str(source.get("sheet_name") or "").strip() or None,
                "source_format": source_format,
                "schema_detection": source.get("schema_detection", {}),
                "schema_quality": source.get("schema_quality", {}),
                "normalization_timestamp": normalized_at,
                "normalization_version": normalization_version,
                **dict(canonical.metadata or {}),
            }
            try:
                canonical_artifact = self._registry_service.attach_artifact(
                    dataset_version=dataset_version,
                    artifact_role="SNAPSHOT",
                    uri=uri,
                    format="PARQUET",
                    size_bytes=int(storage_metadata.size_bytes or 0),
                    checksum=storage_metadata.checksum,
                    storage_provider=storage_metadata.provider,
                    workbook_group=workbook_group,
                    sheet_name=str(source.get("sheet_name") or "").strip() or None,
                    source_artifact=source_artifact,
                    metadata_json=metadata_json,
                )
            except Exception:
                self._artifact_lifecycle_service.cleanup_orphan_upload(
                    storage_provider=provider,
                    uri=uri,
                    tenant_id=str(command.tenant_id),
                    dataset_id=str(getattr(dataset, "id", "")) or None,
                    dataset_version_id=str(getattr(dataset_version, "id", "")) or None,
                    metadata={"reason": "canonical_artifact_persistence_failed"},
                )
                raise

        source_metadata = dict(getattr(source_artifact, "metadata_json", {}) or {})
        source_metadata.update(
            {
                "canonical_artifact_id": str(getattr(canonical_artifact, "id", "")),
                "canonical_artifact_uri": str(getattr(canonical_artifact, "uri", "")),
                "normalization_timestamp": normalized_at,
                "normalization_version": normalization_version,
            },
        )
        try:
            source_artifact.metadata_json = source_metadata
            if callable(getattr(source_artifact, "save", None)):
                source_artifact.save(update_fields=["metadata_json"])
        except Exception:
            pass
        return canonical_artifact

    def _resolve_target_sheets(self, command: RegisterDatasetCommand) -> list[str]:
        requested_sheet = str(command.sheet_name or "").strip() or None
        if requested_sheet is not None:
            return [requested_sheet]
        return self._excel_adapter.list_worksheets(command.path)

    def _resolve_target_sheets_for_registration(
        self,
        *,
        command: RegisterDatasetCommand,
        workbook_group: WorkbookIngestionGroup,
    ) -> list[str]:
        try:
            return self._resolve_target_sheets(command)
        except UnsupportedExcelWorkbookError as error:
            self._workbook_group_service.mark_failed(group=workbook_group)
            raise ValidationError(
                "The Excel workbook is not supported.",
                status=400,
                code="unsupported_excel_workbook",
            ) from error

    def _derive_sheet_slug(
        self,
        *,
        base_slug: str,
        sheet_name: str,
        used_slugs: set[str],
    ) -> str:
        raw_suffix = slugify(sheet_name) or "sheet"
        max_length = 120
        safe_base = str(base_slug).strip("-")
        root_budget = max_length - len(raw_suffix) - 1
        truncated_base = safe_base[:root_budget].rstrip("-")
        if not truncated_base:
            truncated_base = "dataset"
        root_slug = f"{truncated_base}-{raw_suffix}"
        candidate = root_slug
        index = 2
        while candidate in used_slugs:
            suffix = f"-{index}"
            candidate = f"{root_slug[: max_length - len(suffix)]}{suffix}"
            index += 1
        used_slugs.add(candidate)
        return candidate

    def _derive_sheet_dataset_name(
        self,
        *,
        base_name: str,
        sheet_name: str,
        sheet_count: int,
    ) -> str:
        safe_base_name = str(base_name or "").strip()
        safe_sheet_name = str(sheet_name or "").strip()
        if sheet_count <= 1:
            return self._truncate_dataset_name(safe_base_name or safe_sheet_name or "Dataset")
        if not safe_base_name:
            return self._truncate_dataset_name(safe_sheet_name or "Dataset")
        if not safe_sheet_name:
            return self._truncate_dataset_name(safe_base_name)
        return self._truncate_dataset_name(f"{safe_base_name} — {safe_sheet_name}")

    def _truncate_dataset_name(self, value: str) -> str:
        safe_value = str(value or "").strip()
        if len(safe_value) <= DATASET_NAME_MAX_LENGTH:
            return safe_value
        return safe_value[:DATASET_NAME_MAX_LENGTH].rstrip()

    def _is_xlsx_path(self, path: str) -> bool:
        return Path(path).suffix.lower() == ".xlsx"

    def _is_skippable_sheet_validation_error(self, error: ValidationError) -> bool:
        return str(error.code or "") in {
            "worksheet_not_found",
            "empty_dataset",
            "excel_row_limit_exceeded",
            "excel_column_limit_exceeded",
        }

    def _upload_workbook_source_once(
        self,
        *,
        command: RegisterDatasetCommand,
        content_hash: str,
    ) -> tuple[StorageObjectMetadata, str | None]:
        initial_plan = self._storage_strategy.create_plan(command=command, version_number=1)
        provider = initial_plan.provider
        if provider is None:
            provider = self._storage_factory.create_provider(initial_plan.provider_key)
        provider_key = initial_plan.provider_key or getattr(provider, "PROVIDER_NAME", None)
        destination_path = self._build_workbook_source_destination_path(
            tenant_id=str(command.tenant_id),
            base_slug=str(command.slug),
            content_hash=content_hash,
            source_path=str(command.path),
        )
        metadata = provider.put(str(command.path), destination_path)
        self._ensure_safe_persisted_uri(str(metadata.uri))
        return metadata, provider_key

    def _build_workbook_source_destination_path(
        self,
        *,
        tenant_id: str,
        base_slug: str,
        content_hash: str,
        source_path: str,
    ) -> str:
        suffix = Path(source_path).suffix.lower() or ".bin"
        safe_slug = slugify(base_slug) or "dataset"
        return f"datasets/{tenant_id}/{safe_slug}/workbooks/{content_hash}/original{suffix}"

    def _ensure_safe_persisted_uri(self, uri: str) -> None:
        if "sig=" in uri.lower():
            raise UnsafeStorageUriError("Refusing to persist SAS token-bearing URI.")

    def _extract_actor(self, actor: Any) -> str | None:
        if actor is None:
            return None
        actor_id = getattr(actor, "id", None)
        if actor_id is not None:
            return str(actor_id)
        return str(actor)

    def _command_to_payload(self, command: RegisterDatasetCommand) -> dict[str, Any]:
        return {
            "tenant_id": str(command.tenant_id),
            "slug": str(command.slug),
            "name": str(command.name),
            "path": str(command.path),
            "source_type": str(command.source_type),
            "description": command.description,
            "tags": list(command.tags),
            "owner_team": command.owner_team,
            "artifact_role": str(command.artifact_role),
            "storage_provider": command.storage_provider,
            "storage_mode": str(command.storage_mode),
            "preview_limit": int(command.preview_limit),
            "sheet_name": command.sheet_name,
            "mime_type": command.mime_type,
            "ingest_all_sheets": bool(command.ingest_all_sheets),
            "allow_low_confidence_schema": bool(command.allow_low_confidence_schema),
        }

    def _result_from_payload(self, payload: dict[str, Any]) -> RegisterDatasetBatchResult:
        created = [
            RegisterDatasetResult(
                dataset_id=item.get("dataset_id"),
                dataset_version_id=item.get("dataset_version_id"),
                schema_id=item.get("schema_id"),
                profile_id=item.get("profile_id"),
                artifact_id=item.get("artifact_id"),
                row_count=int(item.get("row_count") or 0),
                column_count=int(item.get("column_count") or 0),
                source_format=str(item.get("source_format") or ""),
                file_size_bytes=int(item.get("file_size_bytes") or 0),
                workbook_group_id=item.get("workbook_group_id"),
            )
            for item in payload.get("created", [])
            if isinstance(item, dict)
        ]
        deduplicated = [
            RegisterDatasetResult(
                dataset_id=item.get("dataset_id"),
                dataset_version_id=item.get("dataset_version_id"),
                schema_id=item.get("schema_id"),
                profile_id=item.get("profile_id"),
                artifact_id=item.get("artifact_id"),
                row_count=int(item.get("row_count") or 0),
                column_count=int(item.get("column_count") or 0),
                source_format=str(item.get("source_format") or ""),
                file_size_bytes=int(item.get("file_size_bytes") or 0),
                workbook_group_id=item.get("workbook_group_id"),
            )
            for item in payload.get("deduplicated", [])
            if isinstance(item, dict)
        ]
        skipped = [
            SkippedWorksheet(
                sheet_name=str(item.get("sheet_name") or ""),
                code=str(item.get("code") or ""),
                message=str(item.get("message") or ""),
            )
            for item in payload.get("skipped", [])
            if isinstance(item, dict)
        ]
        return RegisterDatasetBatchResult(
            created=created,
            deduplicated=deduplicated,
            skipped=skipped,
            total_processed=int(payload.get("total_processed") or 0),
            total_created=int(payload.get("total_created") or len(created)),
            total_deduplicated=int(payload.get("total_deduplicated") or len(deduplicated)),
            total_skipped=int(payload.get("total_skipped") or len(skipped)),
        )

    def _attach_schema_quality_context(self, context: dict[str, Any]) -> dict[str, Any]:
        quality = self._schema_quality_assessor.assess(context)
        merged_context = dict(context)
        source = dict(merged_context.get("source", {}))
        existing_detection = source.get("schema_detection", {})
        detection_warnings: list[str] = []
        if isinstance(existing_detection, dict):
            raw_warnings = existing_detection.get("warnings", [])
            if isinstance(raw_warnings, list):
                detection_warnings = [str(item) for item in raw_warnings]
        warnings = tuple(dict.fromkeys([*detection_warnings, *list(quality.warnings)]))
        schema_quality_payload = {
            "score": round(float(quality.score), 4),
            "confidence": str(quality.confidence),
            "warnings": list(warnings),
            "metrics": dict(quality.metrics),
        }
        source["schema_quality"] = schema_quality_payload
        if isinstance(existing_detection, dict):
            existing_detection["warnings"] = list(warnings)
            source["schema_detection"] = existing_detection
        merged_context["source"] = source
        merged_context["schema_quality"] = schema_quality_payload
        return merged_context

    def _replace_materialized_source_reference(
        self,
        *,
        context: dict[str, Any],
        storage_metadata: StorageObjectMetadata | None,
    ) -> dict[str, Any]:
        if storage_metadata is None:
            return context
        persistent_context = dict(context)
        source = dict(persistent_context.get("source", {}))
        source["path"] = str(storage_metadata.uri)
        source["file_name"] = str(
            storage_metadata.metadata.get("source_file_name") or source.get("file_name") or ""
        )
        source["storage_provider"] = str(storage_metadata.provider)
        source["source_reused"] = True
        if storage_metadata.metadata.get("upload_id"):
            source["upload_id"] = str(storage_metadata.metadata["upload_id"])
        persistent_context["source"] = source
        return persistent_context

    def _guess_artifact_format(self, path: str) -> str:
        suffix = Path(path).suffix.lower()
        if suffix == ".csv":
            return "csv"
        if suffix == ".parquet":
            return "parquet"
        if suffix == ".xlsx":
            return "xlsx"
        return normalize_dataset_format(suffix)

    def _safe_file_size(self, path: str) -> int | None:
        candidate = Path(path)
        if candidate.exists() and candidate.is_file():
            return int(candidate.stat().st_size)
        return None
