from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

from django.conf import settings
from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from resource_registry.authorization.submission_authority import (
    SUBMISSION_AUTHORITY_METADATA_KEY,
    SubmissionAuthorityEnvelope,
)
from resource_registry.models import (
    DatasetIntelligenceJob,
    DatasetIntelligenceJobOperation,
    DatasetIntelligenceJobStatus,
)
from resource_registry.services.async_job_idempotency import (
    build_async_job_idempotency_key,
    build_async_job_request_fingerprint,
)
from resource_registry.services.dataset_version_pinning import pin_job_dataset_version
from resource_registry.services.payload_sanitizer import sanitize_payload


class DatasetIntelligenceJobService:
    """Manages DatasetIntelligenceJob lifecycle with sanitized payload persistence."""

    _terminal_statuses: ClassVar[set[str]] = {
        DatasetIntelligenceJobStatus.COMPLETED,
        DatasetIntelligenceJobStatus.FAILED,
        DatasetIntelligenceJobStatus.CANCELLED,
    }
    _active_statuses: ClassVar[set[str]] = {
        DatasetIntelligenceJobStatus.QUEUED,
        DatasetIntelligenceJobStatus.RUNNING,
    }

    def __init__(self, *, max_retries: int | None = None) -> None:
        self._max_retries = max(
            int(
                max_retries
                if max_retries is not None
                else getattr(settings, "RESOURCE_REGISTRY_INTELLIGENCE_JOB_MAX_RETRIES", 3)
            ),
            0,
        )

    def create_job(
        self,
        *,
        tenant_id: str,
        operation: str,
        request_payload: dict[str, Any],
        job_spec: dict[str, Any] | None = None,
        dataset_id: str | None = None,
        version_id: str | None = None,
        export_plan_id: str | None = None,
        created_by: Any = None,
        metadata: dict[str, Any] | None = None,
        submission_authority: SubmissionAuthorityEnvelope | None = None,
    ) -> DatasetIntelligenceJob:
        normalized_operation = str(operation or "").strip().lower()
        self._validate_operation(normalized_operation)
        if dataset_id:
            version_id, submission_authority = pin_job_dataset_version(
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                version_id=version_id,
                submission_authority=submission_authority,
            )
        sanitized_request_payload = self._sanitize_payload(request_payload)
        sanitized_job_spec = self._sanitize_payload(job_spec or {})
        sanitized_metadata = self._sanitize_payload(metadata or {})
        sanitized_metadata.pop(SUBMISSION_AUTHORITY_METADATA_KEY, None)
        if submission_authority is not None:
            sanitized_metadata[SUBMISSION_AUTHORITY_METADATA_KEY] = submission_authority.to_dict()
        request_fingerprint = self._build_request_fingerprint(
            tenant_id=tenant_id,
            operation=normalized_operation,
            request_payload=sanitized_request_payload,
            job_spec=sanitized_job_spec,
            dataset_id=dataset_id,
            version_id=version_id,
            export_plan_id=export_plan_id,
            authority_scope=(
                submission_authority.idempotency_scope()
                if submission_authority is not None
                else None
            ),
        )
        legacy_request_fingerprint = (
            self._build_request_fingerprint(
                tenant_id=tenant_id,
                operation=normalized_operation,
                request_payload=sanitized_request_payload,
                job_spec=sanitized_job_spec,
                dataset_id=dataset_id,
                version_id=version_id,
                export_plan_id=export_plan_id,
            )
            if submission_authority is not None
            else None
        )
        idempotency_key = build_async_job_idempotency_key(
            job_family="dataset_intelligence_job",
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
                if legacy_request_fingerprint is not None:
                    legacy_job = self._find_active_job_by_fingerprint(
                        tenant_id=tenant_id,
                        request_fingerprint=legacy_request_fingerprint,
                        lock=True,
                    )
                    if (
                        legacy_job is not None
                        and submission_authority is not None
                        and legacy_job.created_by_id == submission_authority.actor_id
                    ):
                        return legacy_job

                return DatasetIntelligenceJob.objects.create(
                    tenant_id=tenant_id,
                    operation=normalized_operation,
                    dataset_id=dataset_id,
                    version_id=version_id,
                    export_plan_id=export_plan_id,
                    status=DatasetIntelligenceJobStatus.QUEUED,
                    request_payload_json=sanitized_request_payload,
                    job_spec_json=sanitized_job_spec,
                    idempotency_key=idempotency_key,
                    request_fingerprint=request_fingerprint,
                    created_by=self._resolve_created_by(created_by),
                    metadata_json=sanitized_metadata,
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

    def get_job(self, *, tenant_id: str, intelligence_job_id: str) -> DatasetIntelligenceJob:
        return DatasetIntelligenceJob.objects.get(id=intelligence_job_id, tenant_id=tenant_id)

    def request_cancellation(
        self,
        *,
        tenant_id: str,
        intelligence_job_id: str,
    ) -> DatasetIntelligenceJob:
        """Persist a tenant-scoped cancellation request for an intelligence job."""
        requested_at = timezone.now()
        with transaction.atomic():
            job = DatasetIntelligenceJob.objects.select_for_update().get(
                id=intelligence_job_id,
                tenant_id=tenant_id,
            )
            if job.status in self._terminal_statuses:
                return job

            if job.cancellation_requested_at is None:
                job.cancellation_requested_at = requested_at
            update_fields = ["cancellation_requested_at"]

            if job.status == DatasetIntelligenceJobStatus.QUEUED:
                job.status = DatasetIntelligenceJobStatus.CANCELLED
                job.completed_at = requested_at
                update_fields.extend(["status", "completed_at"])

            job.save(update_fields=update_fields)
            return job

    def is_cancellation_requested(self, *, tenant_id: str, intelligence_job_id: str) -> bool:
        """Return whether a tenant-scoped intelligence job has cancellation intent."""
        return DatasetIntelligenceJob.objects.filter(
            id=intelligence_job_id,
            tenant_id=tenant_id,
            cancellation_requested_at__isnull=False,
        ).exists()

    def mark_cancelled(self, *, job: DatasetIntelligenceJob) -> DatasetIntelligenceJob:
        """Transition an eligible intelligence job to the terminal cancelled state."""
        cancelled_at = timezone.now()
        if isinstance(job, DatasetIntelligenceJob) and job.pk:
            with transaction.atomic():
                current_job = DatasetIntelligenceJob.objects.select_for_update().get(pk=job.pk)
                return self._mark_cancelled_without_lock(
                    job=current_job,
                    cancelled_at=cancelled_at,
                )

        return self._mark_cancelled_without_lock(job=job, cancelled_at=cancelled_at)

    def _mark_cancelled_without_lock(
        self,
        *,
        job: DatasetIntelligenceJob,
        cancelled_at: datetime,
    ) -> DatasetIntelligenceJob:
        if job.status in self._terminal_statuses:
            return job

        if getattr(job, "cancellation_requested_at", None) is None:
            job.cancellation_requested_at = cancelled_at
        job.status = DatasetIntelligenceJobStatus.CANCELLED
        job.completed_at = cancelled_at
        job.save(update_fields=["status", "cancellation_requested_at", "completed_at"])
        return job

    def mark_running(self, *, job: DatasetIntelligenceJob) -> DatasetIntelligenceJob:
        job.status = DatasetIntelligenceJobStatus.RUNNING
        job.started_at = timezone.now()
        job.save(update_fields=["status", "started_at"])
        return job

    def claim_queued_jobs(
        self,
        *,
        batch_size: int,
        tenant_id: str | None = None,
    ) -> list[DatasetIntelligenceJob]:
        """Atomically claim queued intelligence jobs for worker execution.

        Args:
            batch_size: Maximum number of jobs to claim.
            tenant_id: Optional tenant scope for targeted worker runs.

        Returns:
            List of jobs transitioned from ``queued`` to ``running``.
        """
        limited_batch_size = max(int(batch_size), 1)
        claimed_started_at = timezone.now()
        with transaction.atomic():
            queryset = DatasetIntelligenceJob.objects.filter(
                status=DatasetIntelligenceJobStatus.QUEUED,
            )
            if tenant_id:
                queryset = queryset.filter(tenant_id=tenant_id)
            queryset = queryset.order_by("created_at")
            try:
                candidates = list(queryset.select_for_update(skip_locked=True)[:limited_batch_size])
            except DatabaseError:
                candidates = list(queryset.select_for_update()[:limited_batch_size])

            if not candidates:
                return []

            candidate_ids = [job.id for job in candidates]
            (
                DatasetIntelligenceJob.objects.filter(
                    id__in=candidate_ids,
                    status=DatasetIntelligenceJobStatus.QUEUED,
                ).update(
                    status=DatasetIntelligenceJobStatus.RUNNING,
                    started_at=claimed_started_at,
                )
            )
            claimed_jobs = list(
                DatasetIntelligenceJob.objects.filter(
                    id__in=candidate_ids,
                    status=DatasetIntelligenceJobStatus.RUNNING,
                    started_at=claimed_started_at,
                ).order_by("created_at"),
            )
        return claimed_jobs

    def recover_stale_running_jobs(
        self,
        *,
        stale_before,
        batch_size: int,
        tenant_id: str | None = None,
    ) -> int:
        """Transition stale running intelligence jobs to failed for restart recovery.

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
            queryset = DatasetIntelligenceJob.objects.filter(
                status=DatasetIntelligenceJobStatus.RUNNING,
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
                recovered_count += DatasetIntelligenceJob.objects.filter(
                    id__in=cancellation_ids,
                    status=DatasetIntelligenceJobStatus.RUNNING,
                    started_at__lt=stale_before,
                ).update(
                    status=DatasetIntelligenceJobStatus.CANCELLED,
                    completed_at=recovered_at,
                    error_code=None,
                    error_message=None,
                )
            if retryable_ids:
                recovered_count += DatasetIntelligenceJob.objects.filter(
                    id__in=retryable_ids,
                    status=DatasetIntelligenceJobStatus.RUNNING,
                    started_at__lt=stale_before,
                    cancellation_requested_at__isnull=True,
                ).update(
                    status=DatasetIntelligenceJobStatus.QUEUED,
                    retry_count=F("retry_count") + 1,
                    started_at=None,
                    completed_at=None,
                    error_code="worker_restart_recovery",
                    error_message="Requeued stale running job after worker restart.",
                )
            if exhausted_ids:
                recovered_count += DatasetIntelligenceJob.objects.filter(
                    id__in=exhausted_ids,
                    status=DatasetIntelligenceJobStatus.RUNNING,
                    started_at__lt=stale_before,
                    cancellation_requested_at__isnull=True,
                ).update(
                    status=DatasetIntelligenceJobStatus.FAILED,
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
        job: DatasetIntelligenceJob,
        result_payload: dict[str, Any],
        decision_trace: dict[str, Any] | list[Any] | None = None,
        logical_result: dict[str, Any] | None = None,
        execution_ms: int | None = None,
    ) -> DatasetIntelligenceJob:
        if isinstance(job, DatasetIntelligenceJob) and job.pk:
            with transaction.atomic():
                current_job = DatasetIntelligenceJob.objects.select_for_update().get(pk=job.pk)
                return self._mark_completed_without_lock(
                    job=current_job,
                    result_payload=result_payload,
                    decision_trace=decision_trace,
                    logical_result=logical_result,
                    execution_ms=execution_ms,
                )

        return self._mark_completed_without_lock(
            job=job,
            result_payload=result_payload,
            decision_trace=decision_trace,
            logical_result=logical_result,
            execution_ms=execution_ms,
        )

    def _mark_completed_without_lock(
        self,
        *,
        job: DatasetIntelligenceJob,
        result_payload: dict[str, Any],
        decision_trace: dict[str, Any] | list[Any] | None = None,
        logical_result: dict[str, Any] | None = None,
        execution_ms: int | None = None,
    ) -> DatasetIntelligenceJob:
        if job.status in self._terminal_statuses:
            return job
        if getattr(job, "cancellation_requested_at", None) is not None:
            return self._mark_cancelled_without_lock(job=job, cancelled_at=timezone.now())

        job.status = DatasetIntelligenceJobStatus.COMPLETED
        job.result_payload_json = self._sanitize_payload(result_payload)
        if decision_trace is not None:
            if isinstance(decision_trace, list):
                job.decision_trace_json = {"trace": sanitize_payload(decision_trace)}
            else:
                job.decision_trace_json = self._sanitize_payload(decision_trace)
        if logical_result is not None:
            job.logical_result_json = self._sanitize_payload(logical_result)
        job.error_code = None
        job.error_message = None
        job.execution_ms = execution_ms if execution_ms is not None else 0
        job.completed_at = timezone.now()
        update_fields = [
            "status",
            "result_payload_json",
            "error_code",
            "error_message",
            "execution_ms",
            "completed_at",
        ]
        if decision_trace is not None:
            update_fields.append("decision_trace_json")
        if logical_result is not None:
            update_fields.append("logical_result_json")
        job.save(update_fields=update_fields)
        return job

    def mark_failed(
        self,
        *,
        job: DatasetIntelligenceJob,
        error_code: str | None,
        error_message: str | None,
        execution_ms: int | None = None,
        retryable: bool = False,
    ) -> DatasetIntelligenceJob:
        if isinstance(job, DatasetIntelligenceJob) and job.pk:
            with transaction.atomic():
                current_job = DatasetIntelligenceJob.objects.select_for_update().get(pk=job.pk)
                return self._mark_failed_without_lock(
                    job=current_job,
                    error_code=error_code,
                    error_message=error_message,
                    execution_ms=execution_ms,
                    retryable=retryable,
                )

        return self._mark_failed_without_lock(
            job=job,
            error_code=error_code,
            error_message=error_message,
            execution_ms=execution_ms,
            retryable=retryable,
        )

    def _mark_failed_without_lock(
        self,
        *,
        job: DatasetIntelligenceJob,
        error_code: str | None,
        error_message: str | None,
        execution_ms: int | None = None,
        retryable: bool = False,
    ) -> DatasetIntelligenceJob:
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
            job.status = DatasetIntelligenceJobStatus.QUEUED
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

        job.status = DatasetIntelligenceJobStatus.FAILED
        job.completed_at = timezone.now()
        job.save(
            update_fields=[
                "status",
                "error_code",
                "error_message",
                "execution_ms",
                "completed_at",
            ],
        )
        return job

    def _validate_operation(self, operation: str) -> None:
        allowed = {choice[0] for choice in DatasetIntelligenceJobOperation.choices}
        if operation not in allowed:
            raise ValueError("Unsupported intelligence job operation.")

    def _build_request_fingerprint(
        self,
        *,
        tenant_id: str,
        operation: str,
        request_payload: dict[str, Any],
        job_spec: dict[str, Any],
        dataset_id: str | None,
        version_id: str | None,
        export_plan_id: str | None,
        authority_scope: dict[str, object] | None = None,
    ) -> str:
        fingerprint_payload: dict[str, Any] = {
            "job_family": "dataset_intelligence_job",
            "tenant_id": str(tenant_id),
            "operation": operation,
            "dataset_id": str(dataset_id) if dataset_id else None,
            "version_id": str(version_id) if version_id else None,
            "export_plan_id": str(export_plan_id) if export_plan_id else None,
            "request_payload": request_payload,
            "job_spec": job_spec,
        }
        if authority_scope is not None:
            fingerprint_payload["submission_authority"] = authority_scope
        return build_async_job_request_fingerprint(fingerprint_payload)

    def _find_active_job_by_fingerprint(
        self,
        *,
        tenant_id: str,
        request_fingerprint: str,
        lock: bool,
    ) -> DatasetIntelligenceJob | None:
        queryset = DatasetIntelligenceJob.objects.filter(
            tenant_id=tenant_id,
            request_fingerprint=request_fingerprint,
            status__in=self._active_statuses,
        ).order_by("created_at")
        if lock:
            queryset = queryset.select_for_update()
        return queryset.first()

    def _resolve_created_by(self, candidate: Any):
        if candidate is None:
            return None
        if getattr(candidate, "pk", None) is None:
            return None
        return candidate

    def _sanitize_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        sanitized = sanitize_payload(payload)
        return sanitized if isinstance(sanitized, dict) else {}
