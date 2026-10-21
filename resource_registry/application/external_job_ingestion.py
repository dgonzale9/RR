from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from resource_registry.application.artifact_context import ArtifactContextBuilder
from resource_registry.application.commands import IngestExternalJobResultCommand
from resource_registry.application.exceptions import ValidationError
from resource_registry.events.audit_service import AuditService
from resource_registry.models import (
    DatasetArtifact,
    DatasetLineage,
    DatasetProfile,
    DatasetSchema,
    DatasetVersionStatus,
    ExternalDatasetJob,
    ExternalDatasetJobStatus,
)
from resource_registry.policies.provenance import enforcement_reject
from resource_registry.providers.services import ExternalDatasetProviderService
from resource_registry.security.exceptions import (
    ArtifactGuardrailViolation,
    ArtifactIntegrityError,
)
from resource_registry.security.policies import ArtifactMaterializationPolicyResolver
from resource_registry.services.registry.dataset_registry_service import DatasetRegistryService
from resource_registry.services.registry.lineage_service import LineageService
from resource_registry.storage.exceptions import (
    ArtifactMaterializationError,
    UnsupportedArtifactReadError,
)


@dataclass(frozen=True)
class IngestExternalJobResultResult:
    dataset_id: Any
    dataset_version_id: Any
    artifact_id: Any
    schema_id: Any | None
    profile_id: Any | None
    lineage_id: Any | None
    external_dataset_job_id: Any
    source_artifact_uri: str
    row_count: int
    column_count: int
    was_idempotent: bool


class ExternalJobResultIngestionFacade:
    """Explicit command-driven ingestion for successful external provider jobs."""

    def __init__(
        self,
        *,
        external_provider_service: ExternalDatasetProviderService,
        artifact_context_builder: ArtifactContextBuilder | None = None,
        registry_service: DatasetRegistryService | None = None,
        lineage_service: LineageService | None = None,
        audit_service: AuditService | None = None,
        materialization_policy_resolver: ArtifactMaterializationPolicyResolver | None = None,
    ) -> None:
        self._external_provider_service = external_provider_service
        self._artifact_context_builder = artifact_context_builder or ArtifactContextBuilder()
        self._registry_service = registry_service or DatasetRegistryService()
        self._lineage_service = lineage_service or LineageService()
        self._audit_service = audit_service or AuditService()
        self._materialization_policy_resolver = (
            materialization_policy_resolver or ArtifactMaterializationPolicyResolver()
        )

    def ingest(self, command: IngestExternalJobResultCommand) -> IngestExternalJobResultResult:
        job = ExternalDatasetJob.objects.select_related("dataset", "output_dataset_version").get(
            id=command.external_dataset_job_id,
        )
        try:
            self._validate_command(command=command, job=job)

            if job.output_dataset_version is not None:
                existing_source_uri = str(job.output_dataset_version.source_uri)
                result = self._build_existing_result(job=job, source_uri=existing_source_uri)
                self._safe_audit(
                    lambda: self._audit_service.record_external_job_result_deduplicated(
                        tenant_id=command.tenant_id,
                        dataset_id=str(job.dataset.id),
                        metadata={
                            "external_job_id": str(job.id),
                            "dataset_version_id": str(job.output_dataset_version_id),
                        },
                    ),
                )
                return result

            provider_result = self._external_provider_service.fetch_job_result(job=job)
            if command.provider_name and provider_result.provider_name != command.provider_name:
                raise ValidationError("Provider mismatch for external job ingestion.")
            source_uri = provider_result.artifact_uri
            self._validate_artifact_uri(source_uri=source_uri)
            content_hash = self._compute_content_hash(
                source_uri=source_uri,
                provider_result=provider_result,
            )

            existing_version = (
                job.dataset.versions.filter(content_hash=content_hash)
                .order_by("-version_number")
                .first()
            )
            if existing_version is not None:
                job.output_dataset_version = existing_version
                job.save(update_fields=["output_dataset_version", "updated_at"])
                result = self._build_existing_result(job=job, source_uri=source_uri)
                self._safe_audit(
                    lambda: self._audit_service.record_external_job_result_deduplicated(
                        tenant_id=command.tenant_id,
                        dataset_id=str(job.dataset.id),
                        metadata={
                            "external_job_id": str(job.id),
                            "dataset_version_id": str(existing_version.id),
                            "reason": "content_hash_match",
                        },
                    ),
                )
                return result

            context = self._build_context_if_available(
                source_uri=source_uri,
                preview_limit=command.preview_limit,
                storage_provider_key=self._resolve_storage_provider_key(provider_result),
                artifact_metadata=provider_result.artifact_metadata,
                expected_checksum=self._extract_checksum(provider_result.artifact_metadata),
                tenant_id=str(command.tenant_id),
                dataset_id=str(job.dataset.id),
            )
            version_number = self._next_version_number(dataset=job.dataset)
            persistence = None
            if context is not None:
                persistence = self._registry_service.create_version_bundle_from_context(
                    dataset=job.dataset,
                    version_number=version_number,
                    content_hash=content_hash,
                    context=context,
                    status=DatasetVersionStatus.READY,
                )
                dataset_version = persistence.dataset_version
            else:
                dataset_version = self._registry_service.create_dataset_version(
                    dataset=job.dataset,
                    version_number=version_number,
                    source_uri=source_uri,
                    content_hash=content_hash,
                    status=DatasetVersionStatus.READY,
                    source_etag=self._extract_etag(provider_result.artifact_metadata),
                )

            artifact = self._registry_service.attach_artifact(
                dataset_version=dataset_version,
                artifact_role="PRIMARY",
                uri=source_uri,
                format=self._guess_artifact_format(source_uri=source_uri),
                size_bytes=int(provider_result.artifact_metadata.get("size_bytes") or 0),
                checksum=self._extract_checksum(provider_result.artifact_metadata),
                storage_provider=provider_result.provider_name,
                metadata_json={
                    "external_job_id": str(job.id),
                    "provider_job_id": provider_result.provider_job_id,
                },
            )

            lineage = self._lineage_service.record_lineage(
                downstream_dataset_version=dataset_version,
                upstream_external_uri=source_uri,
                lineage_type=command.lineage_type,
                transformation_name=command.transformation_name,
                transformation_hash=command.transformation_hash,
                job_id=job.id,
                metadata_json={
                    "provider_name": provider_result.provider_name,
                    "provider_job_id": provider_result.provider_job_id,
                },
            )

            job.output_dataset_version = dataset_version
            job.save(update_fields=["output_dataset_version", "updated_at"])

            schema_id = persistence.dataset_schema.id if persistence is not None else None
            profile_id = persistence.dataset_profile.id if persistence is not None else None
            row_count = (
                int(context.get("summary", {}).get("row_count", 0)) if context is not None else 0
            )
            column_count = (
                int(context.get("summary", {}).get("column_count", 0)) if context is not None else 0
            )
            result = IngestExternalJobResultResult(
                dataset_id=job.dataset.id,
                dataset_version_id=dataset_version.id,
                artifact_id=artifact.id,
                schema_id=schema_id,
                profile_id=profile_id,
                lineage_id=lineage.id,
                external_dataset_job_id=job.id,
                source_artifact_uri=source_uri,
                row_count=row_count,
                column_count=column_count,
                was_idempotent=False,
            )
            self._safe_audit(
                lambda: self._audit_service.record_external_job_result_ingested(
                    tenant_id=command.tenant_id,
                    dataset_id=str(job.dataset.id),
                    metadata={
                        "external_job_id": str(job.id),
                        "dataset_version_id": str(dataset_version.id),
                        "source_artifact_uri": source_uri,
                        "schema_persisted": schema_id is not None,
                        "profile_persisted": profile_id is not None,
                    },
                ),
            )
            return result
        except Exception:
            error_type = "Exception"
            exc = sys.exc_info()[1]
            if exc is not None:
                error_type = exc.__class__.__name__
            self._safe_audit(
                lambda: self._audit_service.record_external_job_result_ingestion_failed(
                    tenant_id=command.tenant_id,
                    dataset_id=str(getattr(job, "dataset_id", command.dataset_id)),
                    metadata={
                        "external_job_id": str(command.external_dataset_job_id),
                        "error_type": error_type,
                    },
                ),
            )
            raise

    def _validate_command(
        self,
        *,
        command: IngestExternalJobResultCommand,
        job: ExternalDatasetJob,
    ) -> None:
        if str(job.dataset.tenant_id) != str(command.tenant_id):
            raise ValidationError("Tenant mismatch for external job ingestion.")
        if str(job.dataset.id) != str(command.dataset_id):
            raise ValidationError("Dataset mismatch for external job ingestion.")
        if job.status != ExternalDatasetJobStatus.SUCCEEDED:
            raise ValidationError("External job must be in SUCCEEDED status for ingestion.")
        if not job.provider or not job.provider_job_ref:
            raise ValidationError("External job provider references are missing.")

    def _build_existing_result(
        self,
        *,
        job: ExternalDatasetJob,
        source_uri: str,
    ) -> IngestExternalJobResultResult:
        dataset_version = job.output_dataset_version
        artifact = (
            DatasetArtifact.objects.filter(dataset_version=dataset_version)
            .order_by("-created_at")
            .first()
        )
        schema = (
            DatasetSchema.objects.filter(dataset_version=dataset_version)
            .order_by("-created_at")
            .first()
        )
        profile = (
            DatasetProfile.objects.filter(dataset_version=dataset_version)
            .order_by("-computed_at")
            .first()
        )
        lineage = (
            DatasetLineage.objects.filter(downstream_dataset_version=dataset_version)
            .order_by("-recorded_at")
            .first()
        )
        row_count = int(profile.metrics_json.get("row_count", 0)) if profile is not None else 0
        column_count = (
            int(profile.metrics_json.get("column_count", 0)) if profile is not None else 0
        )
        return IngestExternalJobResultResult(
            dataset_id=job.dataset.id,
            dataset_version_id=dataset_version.id,
            artifact_id=artifact.id if artifact is not None else None,
            schema_id=schema.id if schema is not None else None,
            profile_id=profile.id if profile is not None else None,
            lineage_id=lineage.id if lineage is not None else None,
            external_dataset_job_id=job.id,
            source_artifact_uri=source_uri,
            row_count=row_count,
            column_count=column_count,
            was_idempotent=True,
        )

    def _build_context_if_available(
        self,
        *,
        source_uri: str,
        preview_limit: int,
        storage_provider_key: str | None,
        artifact_metadata: dict[str, Any] | None,
        expected_checksum: str | None,
        tenant_id: str,
        dataset_id: str,
    ) -> dict[str, Any] | None:
        try:
            resolved_materialization_policy = (
                self._materialization_policy_resolver.resolve_with_provenance(
                    tenant_id=tenant_id,
                    dataset_id=dataset_id,
                )
            )
            materialization_policy = resolved_materialization_policy.policy
            provenance = resolved_materialization_policy.provenance
            self._safe_audit(
                lambda: self._audit_service.record_artifact_materialization_policy_resolved(
                    tenant_id=tenant_id,
                    dataset_id=dataset_id,
                    metadata={
                        "allow_local_files": materialization_policy.allow_local_files,
                        "allow_remote_uris": materialization_policy.allow_remote_uris,
                        "require_checksum": materialization_policy.require_checksum,
                    },
                ),
            )
            self._safe_audit(
                lambda: self._audit_service.record_policy_provenance_resolved(
                    tenant_id=tenant_id,
                    dataset_id=dataset_id,
                    metadata={
                        "policy_key": provenance.policy_key,
                        "source": str(provenance.source),
                        "revision": provenance.revision,
                        "rules_hash": provenance.rules_hash,
                        "resolution_path": provenance.resolution_path,
                    },
                ),
            )
            build_result = self._artifact_context_builder.build_context_with_integrity(
                artifact_uri=source_uri,
                preview_limit=preview_limit,
                storage_provider_key=storage_provider_key,
                artifact_metadata=artifact_metadata or {},
                expected_checksum=expected_checksum,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                materialization_policy=materialization_policy,
            )
            self._safe_audit(
                lambda: self._audit_service.record_policy_enforcement_recorded(
                    tenant_id=tenant_id,
                    dataset_id=dataset_id,
                    metadata={
                        "policy_key": "artifact_materialization_default",
                        "policy_source": (
                            str(build_result.policy_provenance.source)
                            if build_result.policy_provenance is not None
                            else None
                        ),
                        "policy_revision": (
                            build_result.policy_provenance.revision
                            if build_result.policy_provenance is not None
                            else None
                        ),
                        "enforcement_result": "ALLOW",
                        "enforcement_reason": "guardrails_passed",
                    },
                ),
            )
            if build_result.integrity_verification is not None:
                self._safe_audit(
                    lambda: self._audit_service.record_artifact_integrity_verified(
                        tenant_id=tenant_id,
                        dataset_id=dataset_id,
                        metadata={
                            "source_artifact_uri": source_uri,
                            "algorithm": build_result.integrity_verification.algorithm,
                        },
                    ),
                )
            return build_result.context
        except ArtifactGuardrailViolation as error:
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
                    tenant_id=tenant_id,
                    dataset_id=dataset_id,
                    metadata={
                        "source_artifact_uri": source_uri,
                        "error_type": error_type,
                        "violation": violation_metadata,
                    },
                ),
            )
            self._safe_audit(
                lambda: self._audit_service.record_policy_enforcement_recorded(
                    tenant_id=tenant_id,
                    dataset_id=dataset_id,
                    metadata={
                        "policy_key": reject_context.policy_key,
                        "policy_source": reject_context.policy_source,
                        "policy_revision": reject_context.policy_revision,
                        "enforcement_result": reject_context.enforcement_result,
                        "enforcement_reason": reject_context.enforcement_reason,
                    },
                ),
            )
            self._safe_audit(
                lambda: self._audit_service.record_artifact_guardrail_rejected(
                    tenant_id=tenant_id,
                    dataset_id=dataset_id,
                    metadata={
                        "source_artifact_uri": source_uri,
                        "error_type": error_type,
                    },
                ),
            )
            raise
        except ArtifactIntegrityError as error:
            error_type = error.__class__.__name__
            self._safe_audit(
                lambda: self._audit_service.record_artifact_integrity_failed(
                    tenant_id=tenant_id,
                    dataset_id=dataset_id,
                    metadata={
                        "source_artifact_uri": source_uri,
                        "error_type": error_type,
                    },
                ),
            )
            raise
        except (UnsupportedArtifactReadError, ArtifactMaterializationError, FileNotFoundError):
            return None

    def _next_version_number(self, *, dataset) -> int:
        latest = dataset.versions.order_by("-version_number").first()
        return 1 if latest is None else int(latest.version_number) + 1

    def _guess_artifact_format(self, *, source_uri: str) -> str:
        suffix = Path(source_uri).suffix.lower()
        if suffix == ".csv":
            return "CSV"
        if suffix == ".parquet":
            return "PARQUET"
        if suffix == ".xlsx":
            return "XLSX"
        if suffix == ".json":
            return "JSON"
        if suffix in {".jsonl", ".ndjson"}:
            return "JSONL"
        return "OTHER"

    def _compute_content_hash(self, *, source_uri: str, provider_result) -> str:
        checksum = self._extract_checksum(provider_result.artifact_metadata)
        if checksum:
            return checksum
        etag = self._extract_etag(provider_result.artifact_metadata)
        if etag:
            return etag
        return hashlib.sha256(source_uri.encode("utf-8")).hexdigest()

    def _extract_checksum(self, artifact_metadata: dict[str, Any]) -> str | None:
        checksum = artifact_metadata.get("checksum")
        if checksum:
            return str(checksum)
        return None

    def _extract_etag(self, artifact_metadata: dict[str, Any]) -> str | None:
        etag = artifact_metadata.get("etag")
        if etag:
            return str(etag)
        return None

    def _resolve_storage_provider_key(self, provider_result) -> str | None:
        provider_from_metadata = provider_result.artifact_metadata.get("provider")
        if provider_from_metadata:
            return str(provider_from_metadata)
        return None

    def _validate_artifact_uri(self, *, source_uri: str) -> None:
        if "sig=" in source_uri.lower():
            raise ValidationError("Refusing to ingest SAS token-bearing artifact URI.")

    def _safe_audit(self, audit_call) -> None:
        try:
            audit_call()
        except Exception:
            return
