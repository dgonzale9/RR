from __future__ import annotations

from resource_registry.services.transformation_run_job_service import TransformationRunJobService
from resource_registry.services.transformation_run_worker_contracts import (
    ClaimedTransformationRun,
    TransformationRunCoordinatorResult,
)
from resource_registry.services.transformation_run_worker_metrics import (
    TransformationRunWorkerMetricsPort,
)
from resource_registry.transformations.execution.service import TransformationRunExecutionService


class TransformationRunExecutionCoordinator:
    """Coordinate Story 5 execution with one Story 6 lifecycle decision."""

    def __init__(
        self,
        *,
        execution_service: TransformationRunExecutionService,
        job_service: TransformationRunJobService,
        metrics: TransformationRunWorkerMetricsPort,
    ) -> None:
        self._execution_service = execution_service
        self._job_service = job_service
        self._metrics = metrics

    def execute_claimed_run(
        self,
        *,
        claimed: ClaimedTransformationRun,
    ) -> TransformationRunCoordinatorResult:
        """Execute outside claim transactions and persist the fenced lifecycle result."""
        attempt = self._execution_service.execute_attempt(
            request=claimed.execution_request,
        )
        self._metrics.record_attempt(
            outcome=attempt.outcome.value,
            error_code=attempt.failure.code if attempt.failure is not None else "",
            execution_ms=attempt.execution_ms,
            reused_count=attempt.reused_count,
        )
        failure = attempt.failure
        if (
            failure is not None
            and failure.provider_key is not None
            and failure.operation_key is not None
        ):
            self._metrics.record_provider_failure(
                provider_key=failure.provider_key,
                operation_key=failure.operation_key,
                error_code=failure.code,
            )
            if failure.code == "capability_drift":
                self._metrics.record_capability_drift(
                    provider_key=failure.provider_key,
                    operation_key=failure.operation_key,
                )
        lifecycle = self._job_service.apply_attempt_result(
            claimed=claimed,
            result=attempt,
        )
        self._metrics.record_lifecycle(action=lifecycle.action)
        return TransformationRunCoordinatorResult(attempt=attempt, lifecycle=lifecycle)
