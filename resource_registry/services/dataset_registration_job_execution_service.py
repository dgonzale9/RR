"""Execute claimed upload-backed dataset registration jobs."""

from __future__ import annotations

import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.utils.text import slugify

from resource_registry.application.commands import RegisterDatasetCommand
from resource_registry.application.dataset_registration import (
    DatasetRegistrationFacade,
    RegisterDatasetResult,
)
from resource_registry.application.exceptions import ValidationError
from resource_registry.models import Dataset, DatasetRegistrationJob, DatasetSourceType
from resource_registry.services.csv_upload_materialization_service import (
    CsvUploadExecutionError,
    CsvUploadMaterializationService,
    MaterializedCsvUpload,
)
from resource_registry.services.dataset_registration_job_service import (
    DatasetRegistrationJobService,
)
from resource_registry.services.destination_assignment_removal_service import (
    DestinationAssignmentRemovalService,
)


class DatasetRegistrationJobExecutionService:
    """Execute one claimed dataset registration through the existing facade."""

    def __init__(
        self,
        *,
        job_service: DatasetRegistrationJobService | None = None,
        registration_facade: DatasetRegistrationFacade | None = None,
        materialization_service: CsvUploadMaterializationService | None = None,
        destination_assignment_removal_service: DestinationAssignmentRemovalService | None = None,
    ) -> None:
        self._job_service = job_service or DatasetRegistrationJobService()
        self._registration_facade = registration_facade or DatasetRegistrationFacade()
        self._materialization_service = materialization_service or CsvUploadMaterializationService()
        self._destination_assignment_removal_service = (
            destination_assignment_removal_service or DestinationAssignmentRemovalService()
        )

    def execute_job(
        self,
        *,
        tenant_id: str,
        registration_job_id: str,
    ) -> DatasetRegistrationJob:
        """Materialize, register, and finalize one claimed CSV job."""
        job = self._job_service.get_job(
            tenant_id=tenant_id,
            registration_job_id=registration_job_id,
        )
        started_at = time.perf_counter()
        try:
            with self._materialization_service.materialize(job=job) as source:
                if self._job_service.is_cancellation_requested(
                    tenant_id=tenant_id,
                    registration_job_id=registration_job_id,
                ):
                    current = self._job_service.get_job(
                        tenant_id=tenant_id,
                        registration_job_id=registration_job_id,
                    )
                    return self._job_service.mark_cancelled(job=current)
                with transaction.atomic():
                    command = self._build_registration_command(job=job, source=source)
                    result = self._registration_facade.register(
                        command,
                        pre_uploaded_source_metadata=source.source_metadata,
                        canonical_provider_key=source.storage_provider,
                    )
                    result_payload = self._to_result_payload(result)
                    self._destination_assignment_removal_service.record_job_outputs(
                        publication_id=self._get_staged_publication_id(job=job),
                        result_payload=result_payload,
                    )
            execution_ms = int((time.perf_counter() - started_at) * 1000)
            return self._job_service.mark_completed(
                job=job,
                result_payload=result_payload,
                execution_ms=execution_ms,
            )
        except CsvUploadExecutionError as error:
            return self._job_service.mark_failed(
                job=job,
                error_code=error.code,
                error_message=str(error),
                execution_ms=int((time.perf_counter() - started_at) * 1000),
                retryable=error.retryable,
            )
        except ValidationError as error:
            return self._job_service.mark_failed(
                job=job,
                error_code=str(error.code or "csv_validation_error"),
                error_message="Dataset registration validation failed.",
                execution_ms=int((time.perf_counter() - started_at) * 1000),
            )
        except Exception:
            return self._job_service.mark_failed(
                job=job,
                error_code="csv_registration_error",
                error_message="Unable to execute dataset registration job.",
                execution_ms=int((time.perf_counter() - started_at) * 1000),
                retryable=False,
            )

    @staticmethod
    def _build_registration_command(
        *,
        job: DatasetRegistrationJob,
        source: MaterializedCsvUpload,
    ) -> RegisterDatasetCommand:
        source_filename = Path(source.original_filename)
        base_name = source_filename.stem.strip() or "Dataset"
        base_slug = slugify(base_name) or "dataset"
        extension = source_filename.suffix.lower().lstrip(".")
        format_suffix = f"-{extension}"
        slug = f"{base_slug[: 120 - len(format_suffix)]}{format_suffix}"
        legacy_slug = base_slug[:120]
        legacy_dataset = Dataset.objects.filter(
            tenant_id=source.tenant_id,
            slug=legacy_slug,
            source_type=DatasetSourceType.FILE_UPLOAD,
        ).first()
        if (
            legacy_dataset is not None
            and legacy_dataset.versions.exists()
            and not legacy_dataset.versions.exclude(
                source_uri__iendswith=source_filename.suffix.lower()
            ).exists()
        ):
            slug = legacy_slug
        submission = job.registration_submission
        return RegisterDatasetCommand(
            tenant_id=str(source.tenant_id),
            slug=slug,
            name=source.original_filename,
            path=source.local_path,
            source_type="FILE_UPLOAD",
            organization_id=submission.organization_id,
            workspace_id=(
                None if submission.visibility_scope == "ORGANIZATION" else submission.workspace_id
            ),
            visibility_scope=submission.visibility_scope,
            created_by=job.created_by,
            storage_provider=("AZURE_BLOB" if source.storage_provider == "azure_blob" else "LOCAL"),
            storage_mode="LOCAL_REFERENCE",
            mime_type=source.content_type,
            ingest_all_sheets=False,
        )

    @staticmethod
    def _to_result_payload(result: RegisterDatasetResult) -> dict[str, Any]:
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

    @staticmethod
    def _get_staged_publication_id(*, job: DatasetRegistrationJob) -> UUID | None:
        """Resolve the optional staged publication associated with one upload-backed job."""
        submission = getattr(job, "registration_submission", None)
        if submission is None:
            return None
        try:
            publication = submission.staged_resource_publication
        except ObjectDoesNotExist:
            return None
        return cast(UUID, publication.id)
