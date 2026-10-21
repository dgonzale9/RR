"""Manage upload-backed CSV registration job lifecycle and claims."""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

from django.conf import settings
from django.db import DatabaseError, transaction
from django.utils import timezone

from resource_registry.models import (
    DatasetRegistrationJob,
    DatasetRegistrationJobStatus,
    LargeFileUploadStatus,
    RegistrationJobType,
    RegistrationSubmissionStatus,
)
from resource_registry.services.payload_sanitizer import sanitize_payload
from resource_registry.storage.factory import DataRegistryStorageFactory


class DatasetRegistrationJobService:
    """Promote, claim, and finalize authorized CSV registration jobs."""

    _terminal_statuses: ClassVar[set[str]] = {
        DatasetRegistrationJobStatus.COMPLETED,
        DatasetRegistrationJobStatus.FAILED,
        DatasetRegistrationJobStatus.CANCELLED,
    }

    def __init__(self, *, max_retries: int | None = None) -> None:
        self._max_retries = max(
            int(
                max_retries
                if max_retries is not None
                else getattr(settings, "RESOURCE_REGISTRY_CSV_JOB_MAX_RETRIES", 3)
            ),
            0,
        )

    def get_job(self, *, tenant_id: str, registration_job_id: str) -> DatasetRegistrationJob:
        """Return one tenant-scoped CSV registration job."""
        return DatasetRegistrationJob.objects.get(id=registration_job_id, tenant_id=tenant_id)

    def activate_submitted_jobs(
        self,
        *,
        batch_size: int,
        tenant_id: str | None = None,
    ) -> int:
        """Promote only canonically valid submitted CSV jobs to queued."""
        effective_batch_size = max(int(batch_size), 1)
        now = timezone.now()
        with transaction.atomic():
            queryset = (
                DatasetRegistrationJob.objects.filter(
                    status=DatasetRegistrationJobStatus.SUBMITTED,
                )
                .select_related("registration_submission__upload_session")
                .order_by("created_at", "id")
            )
            if tenant_id:
                queryset = queryset.filter(tenant_id=tenant_id)
            try:
                candidates = list(
                    queryset.select_for_update(skip_locked=True, of=("self",))[
                        :effective_batch_size
                    ]
                )
            except DatabaseError:
                candidates = list(queryset.select_for_update(of=("self",))[:effective_batch_size])
            cancelled_ids = [
                job.id for job in candidates if job.cancellation_requested_at is not None
            ]
            invalid_ids = [
                job.id
                for job in candidates
                if job.cancellation_requested_at is None
                and not self._has_valid_submitted_upload_provenance(job)
            ]
            queued_ids = [
                job.id
                for job in candidates
                if job.cancellation_requested_at is None
                and self._has_valid_submitted_upload_provenance(job)
            ]
            if cancelled_ids:
                DatasetRegistrationJob.objects.filter(
                    id__in=cancelled_ids,
                    status=DatasetRegistrationJobStatus.SUBMITTED,
                ).update(status=DatasetRegistrationJobStatus.CANCELLED, completed_at=now)
            if invalid_ids:
                DatasetRegistrationJob.objects.filter(
                    id__in=invalid_ids,
                    status=DatasetRegistrationJobStatus.SUBMITTED,
                ).update(
                    status=DatasetRegistrationJobStatus.FAILED,
                    completed_at=now,
                    error_code="csv_registration_provenance_invalid",
                    error_message="CSV registration provenance is invalid.",
                )
            if not queued_ids:
                return 0
            return DatasetRegistrationJob.objects.filter(
                id__in=queued_ids,
                status=DatasetRegistrationJobStatus.SUBMITTED,
            ).update(status=DatasetRegistrationJobStatus.QUEUED)

    @staticmethod
    def _has_valid_submitted_upload_provenance(job: DatasetRegistrationJob) -> bool:
        submission = getattr(job, "registration_submission", None)
        upload = getattr(submission, "upload_session", None)
        maximum_size = int(
            getattr(settings, "RESOURCE_REGISTRY_UPLOAD_MAX_FILE_SIZE_BYTES", 0) or 0
        )
        actual_size = int(getattr(upload, "actual_size_bytes", 0) or 0)
        supported_providers = {
            DataRegistryStorageFactory.LOCAL_PROVIDER,
            DataRegistryStorageFactory.AZURE_BLOB_PROVIDER,
        }
        return bool(
            submission is not None
            and upload is not None
            and str(submission.status) == RegistrationSubmissionStatus.SUBMITTED
            and str(submission.job_type) == RegistrationJobType.DATASET_REGISTRATION
            and str(upload.status) == LargeFileUploadStatus.COMPLETED
            and str(upload.resolved_file_format) in {"CSV", "JSON", "JSONL", "PARQUET"}
            and str(upload.storage_provider).strip().lower() in supported_providers
            and bool(str(upload.storage_object_key).strip())
            and actual_size > 0
            and (maximum_size <= 0 or actual_size <= maximum_size)
            and submission.organization_id == upload.organization_id
            and submission.workspace_id == upload.workspace_id
            and submission.registry_namespace_id == upload.registry_namespace_id
            and job.tenant_id == upload.registry_namespace_id
        )

    def claim_queued_jobs(
        self,
        *,
        batch_size: int,
        tenant_id: str | None = None,
    ) -> list[DatasetRegistrationJob]:
        """Atomically transition a bounded set of queued CSV jobs to running."""
        effective_batch_size = max(int(batch_size), 1)
        claimed_at = timezone.now()
        with transaction.atomic():
            queryset = DatasetRegistrationJob.objects.filter(
                status=DatasetRegistrationJobStatus.QUEUED,
            ).order_by("created_at", "id")
            if tenant_id:
                queryset = queryset.filter(tenant_id=tenant_id)
            try:
                candidates = list(
                    queryset.select_for_update(skip_locked=True)[:effective_batch_size]
                )
            except DatabaseError:
                candidates = list(queryset.select_for_update()[:effective_batch_size])
            selected_ids = [job.id for job in candidates]
            if not selected_ids:
                return []
            DatasetRegistrationJob.objects.filter(
                id__in=selected_ids,
                status=DatasetRegistrationJobStatus.QUEUED,
            ).update(status=DatasetRegistrationJobStatus.RUNNING, started_at=claimed_at)
            return list(
                DatasetRegistrationJob.objects.filter(
                    id__in=selected_ids,
                    status=DatasetRegistrationJobStatus.RUNNING,
                    started_at=claimed_at,
                ).order_by("created_at", "id")
            )

    def request_cancellation(
        self,
        *,
        tenant_id: str,
        registration_job_id: str,
    ) -> DatasetRegistrationJob:
        """Persist tenant-scoped cancellation intent."""
        requested_at = timezone.now()
        with transaction.atomic():
            job = DatasetRegistrationJob.objects.select_for_update().get(
                id=registration_job_id,
                tenant_id=tenant_id,
            )
            if job.status in self._terminal_statuses:
                return job
            job.cancellation_requested_at = job.cancellation_requested_at or requested_at
            update_fields = ["cancellation_requested_at"]
            if job.status in {
                DatasetRegistrationJobStatus.SUBMITTED,
                DatasetRegistrationJobStatus.QUEUED,
            }:
                job.status = DatasetRegistrationJobStatus.CANCELLED
                job.completed_at = requested_at
                update_fields.extend(["status", "completed_at"])
            job.save(update_fields=update_fields)
            return job

    def is_cancellation_requested(self, *, tenant_id: str, registration_job_id: str) -> bool:
        """Return whether cancellation intent exists for a tenant-scoped job."""
        return DatasetRegistrationJob.objects.filter(
            id=registration_job_id,
            tenant_id=tenant_id,
            cancellation_requested_at__isnull=False,
        ).exists()

    def mark_cancelled(self, *, job: DatasetRegistrationJob) -> DatasetRegistrationJob:
        """Transition a nonterminal CSV registration job to cancelled."""
        with transaction.atomic():
            current = DatasetRegistrationJob.objects.select_for_update().get(pk=job.pk)
            if current.status in self._terminal_statuses:
                return current
            now = timezone.now()
            current.status = DatasetRegistrationJobStatus.CANCELLED
            current.cancellation_requested_at = current.cancellation_requested_at or now
            current.completed_at = now
            current.save(update_fields=["status", "cancellation_requested_at", "completed_at"])
            return current

    def mark_completed(
        self,
        *,
        job: DatasetRegistrationJob,
        result_payload: dict[str, Any],
        execution_ms: int,
    ) -> DatasetRegistrationJob:
        """Atomically persist a sanitized terminal success result."""
        with transaction.atomic():
            current = DatasetRegistrationJob.objects.select_for_update().get(pk=job.pk)
            if current.status in self._terminal_statuses:
                return current
            current.status = DatasetRegistrationJobStatus.COMPLETED
            current.result_payload_json = sanitize_payload(result_payload)
            current.error_code = None
            current.error_message = None
            current.execution_ms = execution_ms
            current.completed_at = timezone.now()
            current.save(
                update_fields=[
                    "status",
                    "result_payload_json",
                    "error_code",
                    "error_message",
                    "execution_ms",
                    "completed_at",
                ]
            )
            return current

    def mark_failed(
        self,
        *,
        job: DatasetRegistrationJob,
        error_code: str,
        error_message: str,
        execution_ms: int = 0,
        retryable: bool = False,
    ) -> DatasetRegistrationJob:
        """Persist a sanitized terminal failure or bounded safe retry."""
        with transaction.atomic():
            current = DatasetRegistrationJob.objects.select_for_update().get(pk=job.pk)
            if current.status in self._terminal_statuses:
                return current
            if current.cancellation_requested_at is not None:
                return self.mark_cancelled(job=current)
            sanitized = sanitize_payload({"error": error_message})
            current.error_code = str(error_code).strip()[:100]
            sanitized_message = str(sanitized.get("error") or "").strip()
            current.error_message = sanitized_message[:255] or None
            current.execution_ms = execution_ms
            if retryable and current.retry_count < self._max_retries:
                current.status = DatasetRegistrationJobStatus.QUEUED
                current.retry_count += 1
                current.started_at = None
                current.completed_at = None
            else:
                current.status = DatasetRegistrationJobStatus.FAILED
                current.completed_at = timezone.now()
            current.save(
                update_fields=[
                    "status",
                    "retry_count",
                    "started_at",
                    "completed_at",
                    "error_code",
                    "error_message",
                    "execution_ms",
                ]
            )
            return current

    def recover_stale_running_jobs(
        self,
        *,
        stale_before: datetime,
        batch_size: int,
        tenant_id: str | None = None,
    ) -> int:
        """Fail stale CSV jobs terminally because persistence progress is unknown."""
        with transaction.atomic():
            queryset = DatasetRegistrationJob.objects.filter(
                status=DatasetRegistrationJobStatus.RUNNING,
                started_at__lt=stale_before,
            ).order_by("started_at", "id")
            if tenant_id:
                queryset = queryset.filter(tenant_id=tenant_id)
            try:
                candidates = list(queryset.select_for_update(skip_locked=True)[:batch_size])
            except DatabaseError:
                candidates = list(queryset.select_for_update()[:batch_size])
            ids = [job.id for job in candidates]
            if not ids:
                return 0
            return DatasetRegistrationJob.objects.filter(
                id__in=ids,
                status=DatasetRegistrationJobStatus.RUNNING,
            ).update(
                status=DatasetRegistrationJobStatus.FAILED,
                completed_at=timezone.now(),
                error_code="csv_worker_restart_recovery",
                error_message="CSV registration worker did not complete the claimed job.",
            )
