"""Poll and execute authorized CSV registration jobs."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from threading import Event
from uuid import UUID

from django.conf import settings
from django.utils import timezone

from resource_registry.models import DatasetRegistrationJob
from resource_registry.services.dataset_registration_job_execution_service import (
    DatasetRegistrationJobExecutionService,
)
from resource_registry.services.dataset_registration_job_service import (
    DatasetRegistrationJobService,
)
from resource_registry.services.destination_assignment_removal_service import (
    DestinationAssignmentRemovalService,
)

_logger = logging.getLogger("app-logger")


@dataclass(frozen=True)
class DatasetRegistrationWorkerRunStats:
    """Summarize one CSV worker invocation."""

    claimed_jobs: int
    processed_jobs: int


class DatasetRegistrationJobWorkerRunner:
    """Promote, claim, and execute CSV registration jobs outside requests."""

    def __init__(
        self,
        *,
        job_service: DatasetRegistrationJobService | None = None,
        execution_service: DatasetRegistrationJobExecutionService | None = None,
        poll_interval_seconds: float | None = None,
        batch_size: int | None = None,
        stale_running_seconds: int | None = None,
        destination_assignment_removal_service: DestinationAssignmentRemovalService | None = None,
    ) -> None:
        self._job_service = job_service or DatasetRegistrationJobService()
        self._execution_service = execution_service or DatasetRegistrationJobExecutionService(
            job_service=self._job_service,
        )
        self._poll_interval_seconds = max(
            float(
                poll_interval_seconds
                if poll_interval_seconds is not None
                else getattr(
                    settings, "RESOURCE_REGISTRY_CSV_JOB_WORKER_POLL_INTERVAL_SECONDS", 2.0
                )
            ),
            0.1,
        )
        self._batch_size = max(
            int(
                batch_size
                if batch_size is not None
                else getattr(settings, "RESOURCE_REGISTRY_CSV_JOB_WORKER_BATCH_SIZE", 5)
            ),
            1,
        )
        self._stale_running_seconds = max(
            int(
                stale_running_seconds
                if stale_running_seconds is not None
                else getattr(settings, "RESOURCE_REGISTRY_CSV_JOB_STALE_RUNNING_SECONDS", 30 * 60)
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
    ) -> DatasetRegistrationWorkerRunStats:
        """Run polling cycles until stopped or the optional limit is reached."""
        claimed_jobs = 0
        processed_jobs = 0
        limit = None if max_jobs is None or int(max_jobs) <= 0 else int(max_jobs)
        active_stop_event = stop_event or Event()
        while not active_stop_event.is_set():
            if limit is not None and processed_jobs >= limit:
                break
            remaining = limit - processed_jobs if limit is not None else self._batch_size
            cycle_claimed = self.run_once(
                batch_size=min(self._batch_size, max(int(remaining), 1)),
                tenant_id=tenant_id,
            )
            claimed_jobs += cycle_claimed
            processed_jobs += cycle_claimed
            if once:
                break
            if cycle_claimed == 0:
                active_stop_event.wait(self._poll_interval_seconds)
        return DatasetRegistrationWorkerRunStats(
            claimed_jobs=claimed_jobs,
            processed_jobs=processed_jobs,
        )

    def run_once(
        self,
        *,
        batch_size: int | None = None,
        tenant_id: str | None = None,
    ) -> int:
        """Promote, claim, and execute one bounded worker cycle."""
        effective_batch_size = max(int(batch_size or self._batch_size), 1)
        self._recover_stale_running_jobs(tenant_id=tenant_id)
        promoted = self._job_service.activate_submitted_jobs(
            batch_size=effective_batch_size,
            tenant_id=tenant_id,
        )
        if promoted:
            _logger.info(
                "CSV registration worker promoted submitted jobs.",
                extra={"promoted_jobs": promoted, "tenant_id": tenant_id or ""},
            )
        claimed = self._job_service.claim_queued_jobs(
            batch_size=effective_batch_size,
            tenant_id=tenant_id,
        )
        for job in claimed:
            self._execute_claimed_job(job=job)
        return len(claimed)

    def _execute_claimed_job(self, *, job: DatasetRegistrationJob) -> None:
        tenant_id = str(job.tenant_id)
        job_id = str(job.id)
        if self._job_service.is_cancellation_requested(
            tenant_id=tenant_id,
            registration_job_id=job_id,
        ):
            executed = self._job_service.mark_cancelled(job=job)
            self._finalize_staged_detachment_after_terminal_job(job=executed)
            self._log_processed_job(job=executed)
            return
        try:
            executed = self._execution_service.execute_job(
                tenant_id=tenant_id,
                registration_job_id=job_id,
            )
        except Exception:
            current = self._job_service.get_job(
                tenant_id=tenant_id,
                registration_job_id=job_id,
            )
            if self._job_service.is_cancellation_requested(
                tenant_id=tenant_id,
                registration_job_id=job_id,
            ):
                executed = self._job_service.mark_cancelled(job=current)
            else:
                executed = self._job_service.mark_failed(
                    job=current,
                    error_code="csv_registration_error",
                    error_message="Unable to execute CSV registration job.",
                    retryable=False,
                )
        self._log_processed_job(job=executed)
        self._finalize_staged_detachment_after_terminal_job(job=executed)

    def _finalize_staged_detachment_after_terminal_job(
        self,
        *,
        job: DatasetRegistrationJob,
    ) -> None:
        """Finalize a pending publication removal after a terminal CSV job.

        Args:
            job: Terminal CSV registration job associated with a submission.
        """
        if job.registration_submission_id is None:
            return
        try:
            detachment_finalizer = (
                self._destination_assignment_removal_service.finalize_detachment_for_dataset_registration_job
            )
            detachment_finalizer(registration_job_id=UUID(str(job.id)))
        except Exception:
            _logger.warning(
                "CSV registration left staged destination detachment pending.",
                extra={"registration_job_id": str(job.id)},
            )

    def _recover_stale_running_jobs(self, *, tenant_id: str | None) -> None:
        if self._stale_running_seconds <= 0:
            return
        recovered = self._job_service.recover_stale_running_jobs(
            stale_before=timezone.now() - timedelta(seconds=self._stale_running_seconds),
            batch_size=self._batch_size,
            tenant_id=tenant_id,
        )
        if recovered:
            _logger.warning(
                "CSV registration worker failed stale running jobs.",
                extra={"recovered_jobs": recovered},
            )

    @staticmethod
    def _log_processed_job(*, job: DatasetRegistrationJob) -> None:
        _logger.info(
            "CSV registration worker processed job.",
            extra={
                "registration_job_id": str(job.id),
                "tenant_id": str(job.tenant_id),
                "job_status": str(job.status),
                "error_code": str(job.error_code or ""),
                "execution_ms": int(job.execution_ms or 0),
            },
        )
