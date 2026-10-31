from __future__ import annotations

import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.utils.text import slugify

from resource_registry.application.commands import RegisterDatasetCommand
from resource_registry.application.dataset_registration import (
    DatasetRegistrationFacade,
    RegisterDatasetBatchResult,
    RegisterDatasetResult,
)
from resource_registry.application.exceptions import ValidationError
from resource_registry.models import WorkbookIngestionJob
from resource_registry.services.async_job_execution_timeout import (
    build_execution_timeout_message,
    has_execution_timeout_elapsed,
    normalize_execution_timeout_seconds,
)
from resource_registry.services.destination_assignment_removal_service import (
    DestinationAssignmentRemovalService,
)
from resource_registry.services.workbook_ingestion_job_service import WorkbookIngestionJobService
from resource_registry.services.workbook_upload_materialization_service import (
    MaterializedWorkbookUpload,
    WorkbookUploadExecutionError,
    WorkbookUploadMaterializationService,
)


class WorkbookIngestionJobExecutionService:
    """Execute queued workbook ingestion jobs outside request threads."""

    def __init__(
        self,
        *,
        job_service: WorkbookIngestionJobService | None = None,
        registration_facade: DatasetRegistrationFacade | None = None,
        upload_materialization_service: WorkbookUploadMaterializationService | None = None,
        destination_assignment_removal_service: DestinationAssignmentRemovalService | None = None,
        execution_timeout_seconds: float | int | None = None,
    ) -> None:
        self._job_service = job_service or WorkbookIngestionJobService()
        configured_timeout = (
            execution_timeout_seconds
            if execution_timeout_seconds is not None
            else getattr(settings, "RESOURCE_REGISTRY_WORKBOOK_JOB_EXECUTION_TIMEOUT_SECONDS", 0)
        )
        self._execution_timeout_seconds = normalize_execution_timeout_seconds(configured_timeout)
        self._registration_facade = registration_facade or DatasetRegistrationFacade(
            workbook_job_service=self._job_service,
        )
        self._upload_materialization_service = (
            upload_materialization_service or WorkbookUploadMaterializationService()
        )
        self._destination_assignment_removal_service = (
            destination_assignment_removal_service or DestinationAssignmentRemovalService()
        )

    def execute_job(
        self,
        *,
        tenant_id: str,
        ingestion_job_id: str,
    ) -> WorkbookIngestionJob:
        job = self._job_service.get_job(tenant_id=tenant_id, ingestion_job_id=ingestion_job_id)
        self._job_service.mark_running(job=job)
        registration_submission_id = getattr(job, "registration_submission_id", None)
        is_direct_upload_job = isinstance(registration_submission_id, UUID)
        started_at = time.perf_counter()
        try:
            if is_direct_upload_job:
                with self._upload_materialization_service.materialize(job=job) as source:
                    command = self._build_upload_registration_command(job=job, source=source)
                    with transaction.atomic():
                        result = self._registration_facade.register_workbook(
                            command,
                            pre_uploaded_source_metadata=source.source_metadata,
                            canonical_provider_key=source.storage_provider,
                        )
                        payload = self._to_result_payload(result)
                        publication_id = self._get_staged_publication_id(job=job)
                        self._destination_assignment_removal_service.record_job_outputs(
                            publication_id=publication_id,
                            result_payload=payload,
                        )
            else:
                command = RegisterDatasetCommand(**dict(job.request_payload_json or {}))
                with transaction.atomic():
                    result = self._registration_facade.register_workbook(command)
                    payload = self._to_result_payload(result)
                    publication_id = self._get_staged_publication_id(job=job)
                    self._destination_assignment_removal_service.record_job_outputs(
                        publication_id=publication_id,
                        result_payload=payload,
                    )
            elapsed_seconds = time.perf_counter() - started_at
            execution_ms = int(elapsed_seconds * 1000)
            workbook_group_id = self._extract_workbook_group_id(payload)
            if (
                self._job_service.is_cancellation_requested(
                    tenant_id=tenant_id,
                    ingestion_job_id=ingestion_job_id,
                )
                is True
            ):
                current_job = self._job_service.get_job(
                    tenant_id=tenant_id,
                    ingestion_job_id=ingestion_job_id,
                )
                cancelled_job = self._job_service.mark_cancelled(job=current_job)
                if publication_id is not None:
                    self._destination_assignment_removal_service.finalize_detachment(
                        publication_id=publication_id
                    )
                return cancelled_job
            if (
                has_execution_timeout_elapsed(
                    elapsed_seconds=elapsed_seconds,
                    timeout_seconds=self._execution_timeout_seconds,
                )
                and not is_direct_upload_job
            ):
                return self._mark_execution_timeout(
                    job=job,
                    execution_ms=execution_ms,
                )
            return self._job_service.mark_completed(
                job=job,
                result_payload=payload,
                workbook_group_id=workbook_group_id,
                has_skips=bool(result.total_skipped > 0),
                execution_ms=execution_ms,
            )
        except WorkbookUploadExecutionError as error:
            execution_ms = int((time.perf_counter() - started_at) * 1000)
            return self._job_service.mark_failed(
                job=job,
                error_code=error.code,
                error_message=str(error),
                execution_ms=execution_ms,
                retryable=error.retryable,
            )
        except ValidationError as error:
            execution_ms = int((time.perf_counter() - started_at) * 1000)
            if (
                self._job_service.is_cancellation_requested(
                    tenant_id=tenant_id,
                    ingestion_job_id=ingestion_job_id,
                )
                is True
            ):
                current_job = self._job_service.get_job(
                    tenant_id=tenant_id,
                    ingestion_job_id=ingestion_job_id,
                )
                return self._job_service.mark_cancelled(job=current_job)
            return self._job_service.mark_failed(
                job=job,
                error_code=str(error.code or "workbook_validation_error"),
                error_message=str(error),
                execution_ms=execution_ms,
            )
        except Exception:
            elapsed_seconds = time.perf_counter() - started_at
            execution_ms = int(elapsed_seconds * 1000)
            if (
                self._job_service.is_cancellation_requested(
                    tenant_id=tenant_id,
                    ingestion_job_id=ingestion_job_id,
                )
                is True
            ):
                current_job = self._job_service.get_job(
                    tenant_id=tenant_id,
                    ingestion_job_id=ingestion_job_id,
                )
                return self._job_service.mark_cancelled(job=current_job)
            if (
                has_execution_timeout_elapsed(
                    elapsed_seconds=elapsed_seconds,
                    timeout_seconds=self._execution_timeout_seconds,
                )
                and not is_direct_upload_job
            ):
                return self._mark_execution_timeout(
                    job=job,
                    execution_ms=execution_ms,
                )
            return self._job_service.mark_failed(
                job=job,
                error_code="workbook_ingestion_error",
                error_message="Unable to execute workbook ingestion job.",
                execution_ms=execution_ms,
                retryable=not is_direct_upload_job,
            )

    @staticmethod
    def _build_upload_registration_command(
        *,
        job: WorkbookIngestionJob,
        source: MaterializedWorkbookUpload,
    ) -> RegisterDatasetCommand:
        base_name = Path(source.original_filename).stem.strip() or "Workbook"
        base_slug = slugify(base_name) or "workbook"
        submission = job.registration_submission
        return RegisterDatasetCommand(
            tenant_id=str(source.tenant_id),
            slug=base_slug[:120],
            name=base_name,
            path=source.local_path,
            source_type="FILE_UPLOAD",
            organization_id=(submission.organization_id if submission is not None else None),
            workspace_id=(
                None
                if submission is None or submission.visibility_scope == "ORGANIZATION"
                else submission.workspace_id
            ),
            visibility_scope=(
                submission.visibility_scope if submission is not None else "WORKSPACE"
            ),
            created_by=job.created_by,
            storage_provider=("AZURE_BLOB" if source.storage_provider == "azure_blob" else "LOCAL"),
            storage_mode="LOCAL_REFERENCE",
            mime_type=source.content_type,
            ingest_all_sheets=True,
        )

    def _mark_execution_timeout(
        self,
        *,
        job: WorkbookIngestionJob,
        execution_ms: int,
    ) -> WorkbookIngestionJob:
        return self._job_service.mark_failed(
            job=job,
            error_code="execution_timeout",
            error_message=build_execution_timeout_message(
                job_family="Workbook ingestion job",
                timeout_seconds=self._execution_timeout_seconds,
            ),
            execution_ms=execution_ms,
            retryable=True,
        )

    def _to_result_payload(self, result: RegisterDatasetBatchResult) -> dict[str, Any]:
        return {
            "created": [self._to_dataset_result_payload(item) for item in result.created],
            "deduplicated": [self._to_dataset_result_payload(item) for item in result.deduplicated],
            "skipped": [asdict(item) for item in result.skipped],
            "total_processed": int(result.total_processed),
            "total_created": int(result.total_created),
            "total_deduplicated": int(result.total_deduplicated),
            "total_skipped": int(result.total_skipped),
        }

    @staticmethod
    def _to_dataset_result_payload(result: RegisterDatasetResult) -> dict[str, Any]:
        payload = asdict(result)
        for identifier_key in (
            "dataset_id",
            "dataset_version_id",
            "schema_id",
            "profile_id",
            "artifact_id",
            "workbook_group_id",
        ):
            identifier = payload.get(identifier_key)
            payload[identifier_key] = str(identifier) if identifier is not None else None
        return payload

    def _extract_workbook_group_id(self, payload: dict[str, Any]) -> str | None:
        for collection_key in ("created", "deduplicated"):
            collection = payload.get(collection_key)
            if not isinstance(collection, list):
                continue
            for item in collection:
                if not isinstance(item, dict):
                    continue
                group_id = item.get("workbook_group_id") or item.get("metadata_json", {}).get(
                    "workbook_group_id"
                )
                if group_id:
                    return str(group_id)
        return None

    @staticmethod
    def _get_staged_publication_id(*, job: WorkbookIngestionJob) -> UUID | None:
        """Resolve the optional staged publication associated with one upload-backed job."""
        submission = getattr(job, "registration_submission", None)
        if submission is None:
            return None
        try:
            publication = submission.staged_resource_publication
        except ObjectDoesNotExist:
            return None
        return cast(UUID, publication.id)
