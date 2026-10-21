from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from threading import Event

from django.conf import settings
from django.utils import timezone

from resource_registry.authorization.worker_execution_authority import (
    WorkerExecutionAuthorityError,
    WorkerExecutionAuthorityService,
)
from resource_registry.models import DatasetIntelligenceJob
from resource_registry.services.dataset_intelligence_job_execution_service import (
    DatasetIntelligenceJobExecutionService,
)
from resource_registry.services.dataset_intelligence_job_service import DatasetIntelligenceJobService

_logger = logging.getLogger("app-logger")


@dataclass(frozen=True)
class IntelligenceJobWorkerRunStats:
    """Summarize intelligence worker runner outcomes."""

    claimed_jobs: int
    processed_jobs: int


class DatasetIntelligenceJobWorkerRunner:
    """Poll and execute queued dataset intelligence jobs outside request threads."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        job_service: DatasetIntelligenceJobService | None = None,
        execution_service: DatasetIntelligenceJobExecutionService | None = None,
        poll_interval_seconds: float | None = None,
        batch_size: int | None = None,
        stale_running_seconds: int | None = None,
        execution_timeout_seconds: float | int | None = None,
        authority_service: WorkerExecutionAuthorityService | None = None,
    ) -> None:
        self._job_service = job_service or DatasetIntelligenceJobService()
        self._execution_service = execution_service or DatasetIntelligenceJobExecutionService(
            job_service=self._job_service,
            execution_timeout_seconds=execution_timeout_seconds,
        )
        self._authority_service = authority_service or WorkerExecutionAuthorityService()
        self._poll_interval_seconds = max(
            float(
                poll_interval_seconds
                if poll_interval_seconds is not None
                else getattr(
                    settings,
                    "RESOURCE_REGISTRY_INTELLIGENCE_JOB_WORKER_POLL_INTERVAL_SECONDS",
                    2.0,
                )
            ),
            0.1,
        )
        self._batch_size = max(
            int(
                batch_size
                if batch_size is not None
                else getattr(settings, "RESOURCE_REGISTRY_INTELLIGENCE_JOB_WORKER_BATCH_SIZE", 5)
            ),
            1,
        )
        self._stale_running_seconds = max(
            int(
                stale_running_seconds
                if stale_running_seconds is not None
                else getattr(
                    settings,
                    "RESOURCE_REGISTRY_INTELLIGENCE_JOB_STALE_RUNNING_SECONDS",
                    30 * 60,
                )
            ),
            0,
        )

    def run(
        self,
        *,
        once: bool = False,
        max_jobs: int | None = None,
        tenant_id: str | None = None,
        stop_event: Event | None = None,
    ) -> IntelligenceJobWorkerRunStats:
        """Run the polling loop until limits or shutdown conditions are met."""
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
            cycle_claimed = self.run_once(
                batch_size=cycle_batch_size,
                tenant_id=tenant_id,
            )
            claimed_jobs += cycle_claimed
            processed_jobs += cycle_claimed
            if once:
                break
            if cycle_claimed == 0:
                active_stop_event.wait(self._poll_interval_seconds)

        return IntelligenceJobWorkerRunStats(
            claimed_jobs=claimed_jobs,
            processed_jobs=processed_jobs,
        )

    def run_once(
        self,
        *,
        batch_size: int | None = None,
        tenant_id: str | None = None,
    ) -> int:
        """Claim and execute one intelligence worker cycle."""
        effective_batch_size = max(int(batch_size or self._batch_size), 1)
        self._recover_stale_running_jobs(tenant_id=tenant_id)
        claimed_jobs = self._job_service.claim_queued_jobs(
            batch_size=effective_batch_size,
            tenant_id=tenant_id,
        )
        for job in claimed_jobs:
            self._execute_claimed_job(job=job)
        return len(claimed_jobs)

    def _execute_claimed_job(self, *, job: DatasetIntelligenceJob) -> None:
        if (
            self._job_service.is_cancellation_requested(
                tenant_id=str(job.tenant_id),
                intelligence_job_id=str(job.id),
            )
            is True
        ):
            executed_job = self._job_service.mark_cancelled(job=job)
            self._log_processed_job(job=executed_job)
            return

        try:
            execution_authority = self._authority_service.build_intelligence_context(job=job)
        except WorkerExecutionAuthorityError as error:
            executed_job = self._job_service.mark_failed(
                job=job,
                error_code=error.code,
                error_message="Intelligence job execution authority validation failed.",
                retryable=False,
            )
            self._log_processed_job(job=executed_job)
            return
        context = execution_authority.to_read_authorization_context()
        try:
            executed_job = self._execution_service.execute_job(
                context=context,
                intelligence_job_id=str(job.id),
            )
        except Exception:
            failed_job = self._job_service.get_job(
                tenant_id=str(job.tenant_id),
                intelligence_job_id=str(job.id),
            )
            if (
                self._job_service.is_cancellation_requested(
                    tenant_id=str(job.tenant_id),
                    intelligence_job_id=str(job.id),
                )
                is True
            ):
                executed_job = self._job_service.mark_cancelled(job=failed_job)
            else:
                executed_job = self._job_service.mark_failed(
                    job=failed_job,
                    error_code="intelligence_execution_error",
                    error_message="Unable to execute intelligence job.",
                    retryable=True,
                )
        if (
            self._job_service.is_cancellation_requested(
                tenant_id=str(job.tenant_id),
                intelligence_job_id=str(job.id),
            )
            is True
        ):
            current_job = self._job_service.get_job(
                tenant_id=str(job.tenant_id),
                intelligence_job_id=str(job.id),
            )
            executed_job = self._job_service.mark_cancelled(job=current_job)
        self._log_processed_job(job=executed_job)

    def _log_processed_job(self, *, job: DatasetIntelligenceJob) -> None:
        _logger.info(
            "Dataset intelligence worker processed job.",
            extra={
                "intelligence_job_id": str(job.id),
                "tenant_id": str(job.tenant_id),
                "operation": str(job.operation),
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
            "Dataset intelligence worker recovered stale running jobs.",
            extra={
                "recovered_stale_running_jobs": recovered_count,
                "stale_running_seconds": self._stale_running_seconds,
            },
        )
