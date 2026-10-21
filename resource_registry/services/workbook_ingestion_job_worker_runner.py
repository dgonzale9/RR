from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from threading import Event
from uuid import UUID

from django.conf import settings
from django.utils import timezone

from resource_registry.models import WorkbookIngestionJob
from resource_registry.services.destination_assignment_removal_service import (
    DestinationAssignmentRemovalService,
)
from resource_registry.services.workbook_ingestion_job_execution_service import (
    WorkbookIngestionJobExecutionService,
)
from resource_registry.services.workbook_ingestion_job_service import WorkbookIngestionJobService

_logger = logging.getLogger("app-logger")


@dataclass(frozen=True)
class WorkbookIngestionWorkerRunStats:
    claimed_jobs: int
    processed_jobs: int


class WorkbookIngestionJobWorkerRunner:
    """Poll and execute queued workbook ingestion jobs."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        job_service: WorkbookIngestionJobService | None = None,
        execution_service: WorkbookIngestionJobExecutionService | None = None,
        poll_interval_seconds: float | None = None,
        batch_size: int | None = None,
        stale_running_seconds: int | None = None,
        execution_timeout_seconds: float | int | None = None,
        destination_assignment_removal_service: DestinationAssignmentRemovalService | None = None,
    ) -> None:
        self._job_service = job_service or WorkbookIngestionJobService()
        self._execution_service = execution_service or WorkbookIngestionJobExecutionService(
            job_service=self._job_service,
            execution_timeout_seconds=execution_timeout_seconds,
        )
        self._poll_interval_seconds = max(
            float(
                poll_interval_seconds
                if poll_interval_seconds is not None
                else getattr(
                    settings,
                    "RESOURCE_REGISTRY_WORKBOOK_JOB_WORKER_POLL_INTERVAL_SECONDS",
                    2.0,
                )
            ),
            0.1,
        )
        self._batch_size = max(
            int(
                batch_size
                if batch_size is not None
                else getattr(settings, "RESOURCE_REGISTRY_WORKBOOK_JOB_WORKER_BATCH_SIZE", 2)
            ),
            1,
        )
        self._stale_running_seconds = max(
            int(
                stale_running_seconds
                if stale_running_seconds is not None
                else getattr(settings, "RESOURCE_REGISTRY_WORKBOOK_JOB_STALE_RUNNING_SECONDS", 60 * 60)
            ),
            0,
        )
        self._destination_assignment_removal_service = (
            destination_assignment_removal_service or DestinationAssignmentRemovalService()
        )

    def run(
        self,
        *,
        once: bool = False,
        max_jobs: int | None = None,
        tenant_id: str | None = None,
        stop_event: Event | None = None,
    ) -> WorkbookIngestionWorkerRunStats:
        processed_jobs = 0
        claimed_jobs = 0
        max_jobs_limit = None if max_jobs is None or int(max_jobs) <= 0 else int(max_jobs)
        active_stop_event = stop_event or Event()
        while True:
            if active_stop_event.is_set():
                break
            if max_jobs_limit is not None and processed_jobs >= max_jobs_limit:
                break
            remaining_jobs = (
                max_jobs_limit - processed_jobs if max_jobs_limit is not None else self._batch_size
            )
            cycle_batch_size = min(self._batch_size, max(int(remaining_jobs), 1))
            cycle_claimed = self.run_once(batch_size=cycle_batch_size, tenant_id=tenant_id)
            claimed_jobs += cycle_claimed
            processed_jobs += cycle_claimed
            if once:
                break
            if cycle_claimed == 0:
                active_stop_event.wait(self._poll_interval_seconds)
        return WorkbookIngestionWorkerRunStats(
            claimed_jobs=claimed_jobs,
            processed_jobs=processed_jobs,
        )

    def run_once(
        self,
        *,
        batch_size: int | None = None,
        tenant_id: str | None = None,
    ) -> int:
        effective_batch_size = max(int(batch_size or self._batch_size), 1)
        self._recover_stale_running_jobs(tenant_id=tenant_id)
        self._job_service.activate_submitted_jobs(
            batch_size=effective_batch_size,
            tenant_id=tenant_id,
        )
        claimed_jobs = self._job_service.claim_queued_jobs(
            batch_size=effective_batch_size,
            tenant_id=tenant_id,
        )
        for job in claimed_jobs:
            self._execute_claimed_job(job=job)
        return len(claimed_jobs)

    def _execute_claimed_job(self, *, job: WorkbookIngestionJob) -> None:
        if (
            self._job_service.is_cancellation_requested(
                tenant_id=str(job.tenant_id),
                ingestion_job_id=str(job.id),
            )
            is True
        ):
            executed_job = self._job_service.mark_cancelled(job=job)
            self._finalize_staged_detachment_after_cancellation(job=executed_job)
            self._log_processed_job(job=executed_job)
            return

        try:
            executed_job = self._execution_service.execute_job(
                tenant_id=str(job.tenant_id),
                ingestion_job_id=str(job.id),
            )
        except Exception:
            failed_job = self._job_service.get_job(
                tenant_id=str(job.tenant_id),
                ingestion_job_id=str(job.id),
            )
            if (
                self._job_service.is_cancellation_requested(
                    tenant_id=str(job.tenant_id),
                    ingestion_job_id=str(job.id),
                )
                is True
            ):
                executed_job = self._job_service.mark_cancelled(job=failed_job)
                self._finalize_staged_detachment_after_cancellation(job=executed_job)
            else:
                executed_job = self._job_service.mark_failed(
                    job=failed_job,
                    error_code="workbook_ingestion_error",
                    error_message="Unable to execute workbook ingestion job.",
                    retryable=(getattr(failed_job, "registration_submission_id", None) is None),
                )
        if (
            self._job_service.is_cancellation_requested(
                tenant_id=str(job.tenant_id),
                ingestion_job_id=str(job.id),
            )
            is True
        ):
            current_job = self._job_service.get_job(
                tenant_id=str(job.tenant_id),
                ingestion_job_id=str(job.id),
            )
            executed_job = self._job_service.mark_cancelled(job=current_job)
            self._finalize_staged_detachment_after_cancellation(job=executed_job)
        self._log_processed_job(job=executed_job)

    def _finalize_staged_detachment_after_cancellation(self, *, job: WorkbookIngestionJob) -> None:
        """Finalize a pending publication removal after a terminal worker cancellation."""
        registration_submission_id = getattr(job, "registration_submission_id", None)
        if registration_submission_id is None:
            return
        try:
            self._destination_assignment_removal_service.finalize_detachment_for_ingestion_job(
                ingestion_job_id=UUID(str(job.id))
            )
        except Exception:
            _logger.warning(
                "Workbook cancellation left staged destination detachment pending.",
                extra={"ingestion_job_id": str(job.id)},
            )

    def _log_processed_job(self, *, job: WorkbookIngestionJob) -> None:
        _logger.info(
            "Workbook ingestion worker processed job.",
            extra={
                "ingestion_job_id": str(job.id),
                "tenant_id": str(job.tenant_id),
                "workbook_group_id": (
                    str(job.workbook_group_id) if job.workbook_group_id else None
                ),
                "job_status": str(job.status),
                "error_code": str(job.error_code or ""),
                "execution_ms": int(job.execution_ms or 0),
            },
        )

    def _recover_stale_running_jobs(self, *, tenant_id: str | None = None) -> None:
        if self._stale_running_seconds <= 0:
            return
        stale_before = timezone.now() - timedelta(seconds=self._stale_running_seconds)
        recovered_count = self._job_service.recover_stale_running_jobs(
            stale_before=stale_before,
            batch_size=self._batch_size,
            tenant_id=tenant_id,
        )
        if recovered_count <= 0:
            return
        _logger.info(
            "Workbook ingestion worker recovered stale running jobs.",
            extra={
                "recovered_stale_running_jobs": recovered_count,
                "stale_running_seconds": self._stale_running_seconds,
            },
        )
