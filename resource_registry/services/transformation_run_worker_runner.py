from __future__ import annotations

import uuid
from dataclasses import dataclass
from threading import Event

from resource_registry.services.transformation_run_execution_coordinator import (
    TransformationRunExecutionCoordinator,
)
from resource_registry.services.transformation_run_job_service import TransformationRunJobService
from resource_registry.services.transformation_run_worker_contracts import ClaimedTransformationRun
from resource_registry.services.transformation_run_worker_metrics import (
    TransformationRunWorkerMetricsPort,
)
from resource_registry.services.transformation_run_worker_runtime import TransformationRunHeartbeat
from resource_registry.transformations.execution.contracts import CheckpointReferenceAvailability
from resource_registry.transformations.execution.ports import (
    TransformationCheckpointReferenceVerifier,
)
from resource_registry.transformations.operational.logging import (
    TransformationOperationalLogContextBuilder,
    get_transformation_operational_logger,
)
from resource_registry.transformations.providers import (
    TransformationProviderOutputReference,
    TransformationProviderRegistry,
)

_logger = get_transformation_operational_logger()


class UnavailableTransformationCheckpointReferenceVerifier:
    """Fail closed when no production reference verifier is configured."""

    def verify_references(
        self,
        *,
        references: tuple[TransformationProviderOutputReference, ...],
    ) -> CheckpointReferenceAvailability:
        _ = references
        return CheckpointReferenceAvailability.UNKNOWN


@dataclass(frozen=True)
class TransformationRunWorkerStats:
    """Summarize one worker process invocation."""

    claimed_runs: int
    processed_runs: int
    recovered_runs: int


class TransformationRunWorkerRunner:
    """Poll and execute transformation runs in a dedicated worker process."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        job_service: TransformationRunJobService,
        coordinator: TransformationRunExecutionCoordinator,
        provider_registry: TransformationProviderRegistry,
        checkpoint_reference_verifier: TransformationCheckpointReferenceVerifier,
        metrics: TransformationRunWorkerMetricsPort,
        log_context_builder: TransformationOperationalLogContextBuilder,
        worker_id: str | None = None,
        poll_interval_seconds: float,
        lease_seconds: int,
        heartbeat_seconds: int,
        execution_timeout_seconds: float | int,
        recovery_batch_size: int,
    ) -> None:
        self._job_service = job_service
        self._metrics = metrics
        self._coordinator = coordinator
        self._provider_registry = provider_registry
        self._checkpoint_reference_verifier = checkpoint_reference_verifier
        self._log_context_builder = log_context_builder
        self._worker_id = worker_id or f"worker:{uuid.uuid4()}"
        self._poll_interval_seconds = max(float(poll_interval_seconds), 0.1)
        self._lease_seconds = max(int(lease_seconds), 1)
        self._heartbeat_seconds = max(int(heartbeat_seconds), 1)
        if self._heartbeat_seconds >= self._lease_seconds:
            raise ValueError("Heartbeat interval must be shorter than the lease duration.")
        self._execution_timeout_seconds = execution_timeout_seconds
        self._recovery_batch_size = max(int(recovery_batch_size), 1)

    def run(
        self,
        *,
        once: bool = False,
        max_jobs: int | None = None,
        tenant_id: str | None = None,
        stop_event: Event | None = None,
    ) -> TransformationRunWorkerStats:
        """Run until one-shot, job-limit, or cooperative shutdown conditions apply."""
        active_stop_event = stop_event or Event()
        claimed_runs = 0
        processed_runs = 0
        recovered_runs = 0
        max_jobs_limit = None if max_jobs is None or int(max_jobs) <= 0 else int(max_jobs)
        while not active_stop_event.is_set():
            if max_jobs_limit is not None and processed_runs >= max_jobs_limit:
                break
            claimed, recovered = self.run_once(tenant_id=tenant_id)
            claimed_runs += claimed
            processed_runs += claimed
            recovered_runs += recovered
            if once:
                break
            if claimed == 0:
                active_stop_event.wait(self._poll_interval_seconds)
        return TransformationRunWorkerStats(
            claimed_runs=claimed_runs,
            processed_runs=processed_runs,
            recovered_runs=recovered_runs,
        )

    def run_once(self, *, tenant_id: str | None = None) -> tuple[int, int]:
        """Recover expired work and process at most one newly claimed run."""
        queue_metrics = self._job_service.get_queue_metrics(tenant_id=tenant_id)
        self._metrics.record_queue(
            queued_count=queue_metrics.queued_count,
            running_count=queue_metrics.running_count,
            output_pending_count=queue_metrics.output_pending_count,
            oldest_queued_age_seconds=queue_metrics.oldest_queued_age_seconds,
        )
        self._job_service.cancel_requested_queued_runs(tenant_id=tenant_id)
        recoveries = self._job_service.recover_expired_runs(
            batch_size=self._recovery_batch_size,
            tenant_id=tenant_id,
        )
        for recovery in recoveries:
            self._metrics.record_recovery(action=recovery.action)
            _logger.info(
                "Transformation worker recovered expired run.",
                extra=self._log_context_builder.build(
                    values={
                        "tenant_id": recovery.tenant_id or "",
                        "run_id": recovery.run_id,
                        "definition_id": recovery.definition_id or "",
                        "definition_version_id": recovery.definition_version_id or "",
                        "attempt_number": recovery.attempt_number,
                        "outcome": recovery.action.value,
                        "retry_count": recovery.retry_count,
                        "stable_error_code": recovery.error_code or "",
                        "correlation_id": recovery.correlation_id or "",
                    },
                ),
            )
        claimed = self._job_service.claim_next_run(
            worker_id=self._worker_id,
            checkpoint_reference_verifier=self._checkpoint_reference_verifier,
            lease_seconds=self._lease_seconds,
            execution_timeout_seconds=self._execution_timeout_seconds,
            tenant_id=tenant_id,
        )
        self._metrics.record_claim(claimed=claimed is not None)
        if claimed is None:
            return 0, len(recoveries)
        heartbeat = TransformationRunHeartbeat(
            renew_lease=lambda: self._job_service.renew_lease(
                claimed=claimed,
                lease_seconds=self._lease_seconds,
            ),
            interval_seconds=self._heartbeat_seconds,
            lease_lost_event=claimed.lease_lost_event,
        )
        heartbeat.start()
        try:
            result = self._coordinator.execute_claimed_run(claimed=claimed)
        except Exception as error:
            _logger.error(
                "Transformation worker execution escaped lifecycle coordination.",
                extra={
                    **self._log_context(claimed=claimed),
                    **self._log_context_builder.sanitize_exception(error),
                },
            )
        else:
            _logger.info(
                "Transformation worker processed run.",
                extra=self._log_context_builder.build(
                    values={
                        **self._log_context(claimed=claimed),
                        "outcome": result.lifecycle.action.value,
                        "retry_count": result.lifecycle.retry_count,
                        "stable_error_code": result.lifecycle.error_code or "",
                        "duration_ms": result.attempt.execution_ms,
                    },
                ),
            )
        finally:
            heartbeat.stop()
        return 1, len(recoveries)

    def _log_context(self, *, claimed: ClaimedTransformationRun) -> dict[str, object]:
        request = claimed.execution_request
        return self._log_context_builder.build(
            values={
                "tenant_id": request.tenant_id,
                "run_id": request.run_id,
                "definition_id": request.plan.definition_id,
                "definition_version_id": request.plan.definition_version_id,
                "worker_id": claimed.worker_id,
                "attempt_number": request.attempt_number,
                "correlation_id": request.correlation_id,
            },
        )
