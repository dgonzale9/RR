from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

from django.conf import settings
from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import Count, F
from django.utils import timezone

from resource_registry.models import (
    LargeFileUploadStatus,
    RegistrationJobType,
    RegistrationSubmissionStatus,
    WorkbookIngestionJob,
    WorkbookIngestionJobStatus,
)
from resource_registry.services.async_job_idempotency import (
    build_async_job_idempotency_key,
    build_async_job_request_fingerprint,
)
from resource_registry.services.payload_sanitizer import sanitize_payload


class WorkbookIngestionJobService:
    """Manage async workbook ingestion job lifecycle and queued claiming."""

    _terminal_statuses: ClassVar[set[str]] = {
        WorkbookIngestionJobStatus.COMPLETED,
        WorkbookIngestionJobStatus.COMPLETED_WITH_SKIPS,
        WorkbookIngestionJobStatus.FAILED,
        WorkbookIngestionJobStatus.CANCELLED,
    }
    _active_statuses: ClassVar[set[str]] = {
        WorkbookIngestionJobStatus.QUEUED,
        WorkbookIngestionJobStatus.RUNNING,
    }

    def __init__(
        self,
        *,
        max_concurrent_global: int | None = None,
        max_concurrent_per_tenant: int | None = None,
        max_retries: int | None = None,
    ) -> None:
        self._max_concurrent_global = max(
            int(
                max_concurrent_global
                if max_concurrent_global is not None
                else getattr(settings, "RESOURCE_REGISTRY_WORKBOOK_MAX_CONCURRENT_GLOBAL", 5)
            ),
            1,
        )
        self._max_concurrent_per_tenant = max(
            int(
                max_concurrent_per_tenant
                if max_concurrent_per_tenant is not None
                else getattr(settings, "RESOURCE_REGISTRY_WORKBOOK_MAX_CONCURRENT_PER_TENANT", 1)
            ),
            1,
        )
        self._max_retries = max(
            int(
                max_retries
                if max_retries is not None
                else getattr(settings, "RESOURCE_REGISTRY_WORKBOOK_JOB_MAX_RETRIES", 3)
            ),
            0,
        )

    def create_job(
        self,
        *,
        tenant_id: str,
        command_payload: dict[str, Any],
        created_by: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> WorkbookIngestionJob:
        sanitized_command_payload = self._sanitize_payload(command_payload)
        request_fingerprint = self._build_request_fingerprint(
            tenant_id=tenant_id,
            command_payload=sanitized_command_payload,
        )
        idempotency_key = build_async_job_idempotency_key(
            job_family="workbook_ingestion_job",
            request_fingerprint=request_fingerprint,
        )
        try:
            with transaction.atomic():
                existing_job = self._find_active_job_by_fingerprint(
                    tenant_id=tenant_id,
                    request_fingerprint=request_fingerprint,
                    lock=True,
                )
                if existing_job is not None:
                    return existing_job

                return WorkbookIngestionJob.objects.create(
                    tenant_id=tenant_id,
                    status=WorkbookIngestionJobStatus.QUEUED,
                    request_payload_json=sanitized_command_payload,
                    idempotency_key=idempotency_key,
                    request_fingerprint=request_fingerprint,
                    created_by=self._resolve_created_by(created_by),
                    metadata_json=self._sanitize_payload(metadata or {}),
                )
        except IntegrityError:
            existing_job = self._find_active_job_by_fingerprint(
                tenant_id=tenant_id,
                request_fingerprint=request_fingerprint,
                lock=False,
            )
            if existing_job is not None:
                return existing_job
            raise

    def get_job(self, *, tenant_id: str, ingestion_job_id: str) -> WorkbookIngestionJob:
        return WorkbookIngestionJob.objects.get(id=ingestion_job_id, tenant_id=tenant_id)

    def request_cancellation(
        self,
        *,
        tenant_id: str,
        ingestion_job_id: str,
    ) -> WorkbookIngestionJob:
        """Persist a tenant-scoped cancellation request for a workbook ingestion job."""
        requested_at = timezone.now()
        with transaction.atomic():
            job = WorkbookIngestionJob.objects.select_for_update().get(
                id=ingestion_job_id,
                tenant_id=tenant_id,
            )
            if job.status in self._terminal_statuses:
                return job

            if job.cancellation_requested_at is None:
                job.cancellation_requested_at = requested_at
            update_fields = ["cancellation_requested_at"]

            if job.status in {
                WorkbookIngestionJobStatus.SUBMITTED,
                WorkbookIngestionJobStatus.QUEUED,
            }:
                job.status = WorkbookIngestionJobStatus.CANCELLED
                job.completed_at = requested_at
                update_fields.extend(["status", "completed_at"])

            job.save(update_fields=update_fields)
            return job

    def is_cancellation_requested(self, *, tenant_id: str, ingestion_job_id: str) -> bool:
        """Return whether a tenant-scoped workbook job has cancellation intent."""
        return WorkbookIngestionJob.objects.filter(
            id=ingestion_job_id,
            tenant_id=tenant_id,
            cancellation_requested_at__isnull=False,
        ).exists()

    def mark_cancelled(self, *, job: WorkbookIngestionJob) -> WorkbookIngestionJob:
        """Transition an eligible workbook job to the terminal cancelled state."""
        cancelled_at = timezone.now()
        if isinstance(job, WorkbookIngestionJob) and job.pk:
            with transaction.atomic():
                current_job = WorkbookIngestionJob.objects.select_for_update().get(pk=job.pk)
                return self._mark_cancelled_without_lock(
                    job=current_job,
                    cancelled_at=cancelled_at,
                )

        return self._mark_cancelled_without_lock(job=job, cancelled_at=cancelled_at)

    def _mark_cancelled_without_lock(
        self,
        *,
        job: WorkbookIngestionJob,
        cancelled_at: datetime,
    ) -> WorkbookIngestionJob:
        if job.status in self._terminal_statuses:
            return job

        if getattr(job, "cancellation_requested_at", None) is None:
            job.cancellation_requested_at = cancelled_at
        job.status = WorkbookIngestionJobStatus.CANCELLED
        job.completed_at = cancelled_at
        job.save(update_fields=["status", "cancellation_requested_at", "completed_at"])
        return job

    def mark_running(self, *, job: WorkbookIngestionJob) -> WorkbookIngestionJob:
        job.status = WorkbookIngestionJobStatus.RUNNING
        job.started_at = timezone.now()
        job.save(update_fields=["status", "started_at"])
        return job

    def activate_submitted_jobs(
        self,
        *,
        batch_size: int,
        tenant_id: str | None = None,
    ) -> int:
        """Promote linked submitted jobs into the existing queued claim lifecycle.

        Args:
            batch_size: Maximum submitted jobs to inspect in one cycle.
            tenant_id: Optional canonical Namespace scope.

        Returns:
            Number of valid submitted jobs promoted to queued.
        """
        effective_batch_size = max(int(batch_size), 1)
        now = timezone.now()
        with transaction.atomic():
            queryset = (
                WorkbookIngestionJob.objects.filter(
                    status=WorkbookIngestionJobStatus.SUBMITTED,
                )
                .select_related(
                    "registration_submission__upload_session",
                )
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
            if not candidates:
                return 0
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
                WorkbookIngestionJob.objects.filter(
                    id__in=cancelled_ids,
                    status=WorkbookIngestionJobStatus.SUBMITTED,
                ).update(
                    status=WorkbookIngestionJobStatus.CANCELLED,
                    completed_at=now,
                )
            if invalid_ids:
                WorkbookIngestionJob.objects.filter(
                    id__in=invalid_ids,
                    status=WorkbookIngestionJobStatus.SUBMITTED,
                ).update(
                    status=WorkbookIngestionJobStatus.FAILED,
                    completed_at=now,
                    error_code="workbook_registration_provenance_invalid",
                    error_message="Workbook registration provenance is invalid.",
                )
            if not queued_ids:
                return 0
            return WorkbookIngestionJob.objects.filter(
                id__in=queued_ids,
                status=WorkbookIngestionJobStatus.SUBMITTED,
            ).update(status=WorkbookIngestionJobStatus.QUEUED)

    @staticmethod
    def _has_valid_submitted_upload_provenance(job: WorkbookIngestionJob) -> bool:
        submission = getattr(job, "registration_submission", None)
        if submission is None:
            return False
        upload = getattr(submission, "upload_session", None)
        if upload is None:
            return False
        return bool(
            str(submission.status) == RegistrationSubmissionStatus.SUBMITTED
            and str(submission.job_type) == RegistrationJobType.WORKBOOK_INGESTION
            and str(upload.status) == LargeFileUploadStatus.COMPLETED
            and str(upload.resolved_file_format) == "XLSX"
            and bool(str(upload.storage_provider).strip())
            and bool(str(upload.storage_object_key).strip())
            and int(upload.actual_size_bytes or 0) > 0
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
    ) -> list[WorkbookIngestionJob]:
        effective_batch_size = max(int(batch_size), 1)
        claimed_started_at = timezone.now()
        with transaction.atomic():
            base_queryset = WorkbookIngestionJob.objects.filter(
                status=WorkbookIngestionJobStatus.QUEUED,
            )
            if tenant_id:
                base_queryset = base_queryset.filter(tenant_id=tenant_id)
            ordered_queryset = base_queryset.order_by("created_at")
            try:
                candidates = list(
                    ordered_queryset.select_for_update(skip_locked=True)[:effective_batch_size],
                )
            except DatabaseError:
                candidates = list(ordered_queryset.select_for_update()[:effective_batch_size])
            if not candidates:
                return []

            running_global = WorkbookIngestionJob.objects.filter(
                status=WorkbookIngestionJobStatus.RUNNING,
            ).count()
            global_slots = max(self._max_concurrent_global - running_global, 0)
            if global_slots <= 0:
                return []

            candidate_tenant_ids = {str(job.tenant_id) for job in candidates}
            running_by_tenant = {
                str(row["tenant_id"]): int(row["count"])
                for row in WorkbookIngestionJob.objects.filter(
                    status=WorkbookIngestionJobStatus.RUNNING,
                    tenant_id__in=candidate_tenant_ids,
                )
                .values("tenant_id")
                .annotate(count=Count("id"))
            }
            selected_ids: list[str] = []
            selected_by_tenant: dict[str, int] = {}
            for candidate in candidates:
                if len(selected_ids) >= global_slots:
                    break
                candidate_tenant = str(candidate.tenant_id)
                in_flight = running_by_tenant.get(candidate_tenant, 0) + selected_by_tenant.get(
                    candidate_tenant, 0
                )
                if in_flight >= self._max_concurrent_per_tenant:
                    continue
                selected_ids.append(str(candidate.id))
                selected_by_tenant[candidate_tenant] = (
                    selected_by_tenant.get(candidate_tenant, 0) + 1
                )
            if not selected_ids:
                return []

            (
                WorkbookIngestionJob.objects.filter(
                    id__in=selected_ids,
                    status=WorkbookIngestionJobStatus.QUEUED,
                ).update(
                    status=WorkbookIngestionJobStatus.RUNNING,
                    started_at=claimed_started_at,
                )
            )
            return list(
                WorkbookIngestionJob.objects.filter(
                    id__in=selected_ids,
                    status=WorkbookIngestionJobStatus.RUNNING,
                    started_at=claimed_started_at,
                ).order_by("created_at"),
            )

    def recover_stale_running_jobs(
        self,
        *,
        stale_before: datetime,
        batch_size: int,
        tenant_id: str | None = None,
    ) -> int:
        """Transition stale running workbook jobs to failed for restart recovery.

        Args:
            stale_before: Threshold timestamp. Jobs started before this are stale.
            batch_size: Maximum number of jobs to recover.
            tenant_id: Optional tenant scope for targeted worker runs.

        Returns:
            The number of jobs transitioned to ``failed``.
        """
        limited_batch_size = max(int(batch_size), 1)
        recovered_at = timezone.now()
        with transaction.atomic():
            queryset = WorkbookIngestionJob.objects.filter(
                status=WorkbookIngestionJobStatus.RUNNING,
                started_at__lt=stale_before,
            )
            if tenant_id:
                queryset = queryset.filter(tenant_id=tenant_id)
            queryset = queryset.order_by("started_at", "created_at")
            try:
                stale_candidates = list(
                    queryset.select_for_update(skip_locked=True)[:limited_batch_size],
                )
            except DatabaseError:
                stale_candidates = list(queryset.select_for_update()[:limited_batch_size])

            if not stale_candidates:
                return 0

            cancellation_ids = [
                job.id
                for job in stale_candidates
                if getattr(job, "cancellation_requested_at", None) is not None
            ]
            retryable_ids = [
                job.id
                for job in stale_candidates
                if getattr(job, "cancellation_requested_at", None) is None
                and int(getattr(job, "retry_count", 0) or 0) < self._max_retries
            ]
            exhausted_ids = [
                job.id
                for job in stale_candidates
                if getattr(job, "cancellation_requested_at", None) is None
                and int(getattr(job, "retry_count", 0) or 0) >= self._max_retries
            ]
            recovered_count = 0
            if cancellation_ids:
                recovered_count += WorkbookIngestionJob.objects.filter(
                    id__in=cancellation_ids,
                    status=WorkbookIngestionJobStatus.RUNNING,
                    started_at__lt=stale_before,
                ).update(
                    status=WorkbookIngestionJobStatus.CANCELLED,
                    completed_at=recovered_at,
                    error_code=None,
                    error_message=None,
                )
            if retryable_ids:
                recovered_count += WorkbookIngestionJob.objects.filter(
                    id__in=retryable_ids,
                    status=WorkbookIngestionJobStatus.RUNNING,
                    started_at__lt=stale_before,
                    cancellation_requested_at__isnull=True,
                ).update(
                    status=WorkbookIngestionJobStatus.QUEUED,
                    retry_count=F("retry_count") + 1,
                    started_at=None,
                    completed_at=None,
                    error_code="worker_restart_recovery",
                    error_message="Requeued stale running job after worker restart.",
                )
            if exhausted_ids:
                recovered_count += WorkbookIngestionJob.objects.filter(
                    id__in=exhausted_ids,
                    status=WorkbookIngestionJobStatus.RUNNING,
                    started_at__lt=stale_before,
                    cancellation_requested_at__isnull=True,
                ).update(
                    status=WorkbookIngestionJobStatus.FAILED,
                    completed_at=recovered_at,
                    error_code="worker_restart_recovery_exhausted",
                    error_message=(
                        "Stale running job exhausted retry attempts after worker restart."
                    ),
                )
            return recovered_count

    def mark_completed(
        self,
        *,
        job: WorkbookIngestionJob,
        result_payload: dict[str, Any],
        workbook_group_id: str | None = None,
        has_skips: bool = False,
        execution_ms: int | None = None,
    ) -> WorkbookIngestionJob:
        if isinstance(job, WorkbookIngestionJob) and job.pk:
            with transaction.atomic():
                current_job = WorkbookIngestionJob.objects.select_for_update().get(pk=job.pk)
                return self._mark_completed_without_lock(
                    job=current_job,
                    result_payload=result_payload,
                    workbook_group_id=workbook_group_id,
                    has_skips=has_skips,
                    execution_ms=execution_ms,
                )

        return self._mark_completed_without_lock(
            job=job,
            result_payload=result_payload,
            workbook_group_id=workbook_group_id,
            has_skips=has_skips,
            execution_ms=execution_ms,
        )

    def _mark_completed_without_lock(
        self,
        *,
        job: WorkbookIngestionJob,
        result_payload: dict[str, Any],
        workbook_group_id: str | None = None,
        has_skips: bool = False,
        execution_ms: int | None = None,
    ) -> WorkbookIngestionJob:
        if job.status in self._terminal_statuses:
            return job
        if getattr(job, "cancellation_requested_at", None) is not None:
            return self._mark_cancelled_without_lock(job=job, cancelled_at=timezone.now())

        job.status = (
            WorkbookIngestionJobStatus.COMPLETED_WITH_SKIPS
            if has_skips
            else WorkbookIngestionJobStatus.COMPLETED
        )
        job.result_payload_json = self._sanitize_payload(result_payload)
        job.error_code = None
        job.error_message = None
        job.execution_ms = execution_ms if execution_ms is not None else 0
        job.completed_at = timezone.now()
        if workbook_group_id:
            job.workbook_group_id = workbook_group_id
        job.save(
            update_fields=[
                "status",
                "result_payload_json",
                "error_code",
                "error_message",
                "execution_ms",
                "completed_at",
                "workbook_group",
            ],
        )
        return job

    def mark_failed(  # noqa: PLR0913
        self,
        *,
        job: WorkbookIngestionJob,
        error_code: str | None,
        error_message: str | None,
        execution_ms: int | None = None,
        increment_retry: bool = False,
        retryable: bool = False,
    ) -> WorkbookIngestionJob:
        if isinstance(job, WorkbookIngestionJob) and job.pk:
            with transaction.atomic():
                current_job = WorkbookIngestionJob.objects.select_for_update().get(pk=job.pk)
                return self._mark_failed_without_lock(
                    job=current_job,
                    error_code=error_code,
                    error_message=error_message,
                    execution_ms=execution_ms,
                    increment_retry=increment_retry,
                    retryable=retryable,
                )

        return self._mark_failed_without_lock(
            job=job,
            error_code=error_code,
            error_message=error_message,
            execution_ms=execution_ms,
            increment_retry=increment_retry,
            retryable=retryable,
        )

    def _mark_failed_without_lock(  # noqa: PLR0913
        self,
        *,
        job: WorkbookIngestionJob,
        error_code: str | None,
        error_message: str | None,
        execution_ms: int | None = None,
        increment_retry: bool = False,
        retryable: bool = False,
    ) -> WorkbookIngestionJob:
        if job.status in self._terminal_statuses:
            return job
        if getattr(job, "cancellation_requested_at", None) is not None:
            return self._mark_cancelled_without_lock(job=job, cancelled_at=timezone.now())

        sanitized_error = self._sanitize_payload({"error": str(error_message or "")})
        current_retry_count = int(getattr(job, "retry_count", 0) or 0)
        job.error_code = str(error_code).strip() if error_code else None
        job.error_message = str(sanitized_error.get("error") or "").strip() or None
        job.execution_ms = execution_ms if execution_ms is not None else 0
        if retryable and current_retry_count < self._max_retries:
            job.status = WorkbookIngestionJobStatus.QUEUED
            job.retry_count = current_retry_count + 1
            job.started_at = None
            job.completed_at = None
            job.save(
                update_fields=[
                    "status",
                    "retry_count",
                    "started_at",
                    "completed_at",
                    "error_code",
                    "error_message",
                    "execution_ms",
                ],
            )
            return job

        job.status = WorkbookIngestionJobStatus.FAILED
        job.completed_at = timezone.now()
        if increment_retry:
            job.retry_count = current_retry_count + 1
        job.save(
            update_fields=[
                "status",
                "error_code",
                "error_message",
                "execution_ms",
                "completed_at",
                "retry_count",
            ],
        )
        return job

    def _resolve_created_by(self, candidate: Any) -> Any | None:
        if candidate is None:
            return None
        if getattr(candidate, "pk", None) is None:
            return None
        return candidate

    def _build_request_fingerprint(
        self,
        *,
        tenant_id: str,
        command_payload: dict[str, Any],
    ) -> str:
        return build_async_job_request_fingerprint(
            {
                "job_family": "workbook_ingestion_job",
                "tenant_id": str(tenant_id),
                "command_payload": command_payload,
            },
        )

    def _find_active_job_by_fingerprint(
        self,
        *,
        tenant_id: str,
        request_fingerprint: str,
        lock: bool,
    ) -> WorkbookIngestionJob | None:
        queryset = WorkbookIngestionJob.objects.filter(
            tenant_id=tenant_id,
            request_fingerprint=request_fingerprint,
            status__in=self._active_statuses,
        ).order_by("created_at")
        if lock:
            queryset = queryset.select_for_update()
        return queryset.first()

    def _sanitize_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        sanitized = sanitize_payload(payload)
        return sanitized if isinstance(sanitized, dict) else {}
