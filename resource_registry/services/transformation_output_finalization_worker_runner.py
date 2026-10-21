from __future__ import annotations

import hashlib
import uuid
from threading import Event

from resource_registry.services.transformation_output_finalization_coordinator import (
    TransformationOutputFinalizationCoordinator,
)
from resource_registry.services.transformation_output_finalization_job_service import (
    TransformationOutputFinalizationJobService,
)
from resource_registry.services.transformation_output_finalization_worker_contracts import (
    ClaimedTransformationOutputFinalization,
    TransformationOutputFinalizationWorkerStats,
)
from resource_registry.services.transformation_run_worker_runtime import TransformationRunHeartbeat
from resource_registry.services.transformation_workspace_lease_manager import (
    TransformationWorkspaceLeaseManager,
)
from resource_registry.transformations.operational.logging import (
    TransformationOperationalLogContextBuilder,
    get_transformation_operational_logger,
)
from resource_registry.transformations.operational.metrics import TransformationOperationalMetricsPort

_logger = get_transformation_operational_logger()


class TransformationOutputFinalizationWorkerRunner:
    """Poll and finalize durable output-pending runs one at a time."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        job_service: TransformationOutputFinalizationJobService,
        coordinator: TransformationOutputFinalizationCoordinator,
        workspace_lease_manager: TransformationWorkspaceLeaseManager,
        metrics: TransformationOperationalMetricsPort,
        log_context_builder: TransformationOperationalLogContextBuilder,
        poll_interval_seconds: float,
        lease_seconds: int,
        heartbeat_seconds: int,
        timeout_seconds: float,
        recovery_batch_size: int,
        worker_id: str | None = None,
    ) -> None:
        self._job_service = job_service
        self._coordinator = coordinator
        self._workspace_lease_manager = workspace_lease_manager
        self._metrics = metrics
        self._log_context_builder = log_context_builder
        self._poll_interval_seconds = max(float(poll_interval_seconds), 0.1)
        self._lease_seconds = max(int(lease_seconds), 1)
        self._heartbeat_seconds = max(int(heartbeat_seconds), 1)
        if self._heartbeat_seconds >= self._lease_seconds:
            raise ValueError("Finalization heartbeat must be shorter than its lease.")
        if timeout_seconds <= 0:
            raise ValueError("Finalization timeout must be positive.")
        self._timeout_seconds = float(timeout_seconds)
        self._recovery_batch_size = max(int(recovery_batch_size), 1)
        self._worker_id = worker_id or f"output-finalizer:{uuid.uuid4()}"

    def run(
        self,
        *,
        once: bool = False,
        max_jobs: int | None = None,
        tenant_id: str | None = None,
        stop_event: Event | None = None,
    ) -> TransformationOutputFinalizationWorkerStats:
        """Run until one-shot, job-limit, or cooperative shutdown conditions apply."""
        active_stop_event = stop_event or Event()
        claimed_runs = processed_runs = recovered_runs = cleaned_leases = 0
        limit = None if max_jobs is None or max_jobs <= 0 else int(max_jobs)
        while not active_stop_event.is_set():
            if limit is not None and processed_runs >= limit:
                break
            claimed, recovered, cleaned = self.run_once(tenant_id=tenant_id)
            claimed_runs += claimed
            processed_runs += claimed
            recovered_runs += recovered
            cleaned_leases += cleaned
            if once:
                break
            if claimed == 0:
                active_stop_event.wait(self._poll_interval_seconds)
        return TransformationOutputFinalizationWorkerStats(
            claimed_runs=claimed_runs,
            processed_runs=processed_runs,
            recovered_runs=recovered_runs,
            cleaned_leases=cleaned_leases,
        )

    def run_once(self, *, tenant_id: str | None = None) -> tuple[int, int, int]:
        """Recover stale work and process at most one finalization claim."""
        snapshot = self._job_service.get_queue_snapshot(tenant_id=tenant_id)
        self._metrics.record_finalization_queue(
            pending_count=snapshot.pending_count,
            active_count=snapshot.active_count,
            stale_count=snapshot.stale_count,
            cleanup_pending_count=snapshot.cleanup_pending_count,
            oldest_pending_age_seconds=snapshot.oldest_pending_age_seconds,
        )
        recoveries = self._job_service.recover_expired_runs(
            batch_size=self._recovery_batch_size,
            tenant_id=tenant_id,
        )
        cleaned = self._workspace_lease_manager.recover_and_cleanup(
            worker_id=self._worker_id,
            batch_size=self._recovery_batch_size,
            tenant_id=tenant_id,
        )
        claimed = self._job_service.claim_next_run(
            worker_id=self._worker_id,
            lease_seconds=self._lease_seconds,
            timeout_seconds=self._timeout_seconds,
            tenant_id=tenant_id,
        )
        self._metrics.record_finalization_claim(claimed=claimed is not None)
        if claimed is None:
            return 0, len(recoveries), cleaned
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
            try:
                result = self._coordinator.finalize_claimed_run(claimed=claimed)
            except Exception as error:
                _logger.error(
                    "Transformation output finalization escaped lifecycle coordination.",
                    extra={
                        **self._claim_log_context(claimed=claimed),
                        **self._log_context_builder.sanitize_exception(error),
                    },
                )
            else:
                _logger.info(
                    "Transformation output finalization worker processed run.",
                    extra=self._log_context_builder.build(
                        values={
                            **self._claim_log_context(claimed=claimed),
                            "retry_count": result.retry_count,
                            "outcome": result.action.value,
                            "stable_error_code": result.error_code or "",
                        },
                    ),
                )
        finally:
            heartbeat.stop()
        return 1, len(recoveries), cleaned

    def _claim_log_context(
        self,
        *,
        claimed: ClaimedTransformationOutputFinalization,
    ) -> dict[str, object]:
        claim = claimed.claim
        return {
            "tenant_id": claim.tenant_id,
            "run_id": claim.run_id,
            "worker_id": self._worker_id,
            "attempt_number": claim.attempt_number,
            "correlation_id": claim.correlation_id,
            "finalization_token_digest": hashlib.sha256(
                claim.token.encode("utf-8"),
            ).hexdigest()[:16],
        }
