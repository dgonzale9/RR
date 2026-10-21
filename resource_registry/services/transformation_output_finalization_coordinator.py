from __future__ import annotations

from resource_registry.services.transformation_output_finalization_job_service import (
    TransformationOutputFinalizationJobService,
)
from resource_registry.services.transformation_output_finalization_worker_contracts import (
    ClaimedTransformationOutputFinalization,
    TransformationOutputFinalizationLifecycleResult,
)
from resource_registry.services.transformation_workspace_lease_manager import (
    TransformationWorkspaceLeaseManager,
)
from resource_registry.transformations.operational.metrics import TransformationOperationalMetricsPort
from resource_registry.transformations.output_finalization import (
    TransformationRunOutputFinalizationService,
)


class TransformationOutputFinalizationCoordinator:
    """Coordinate Story 7 with exactly one fenced worker lifecycle decision."""

    def __init__(
        self,
        *,
        finalization_service: TransformationRunOutputFinalizationService,
        job_service: TransformationOutputFinalizationJobService,
        metrics: TransformationOperationalMetricsPort,
        workspace_lease_manager: TransformationWorkspaceLeaseManager,
    ) -> None:
        self._finalization_service = finalization_service
        self._job_service = job_service
        self._metrics = metrics
        self._workspace_lease_manager = workspace_lease_manager

    def finalize_claimed_run(
        self,
        *,
        claimed: ClaimedTransformationOutputFinalization,
    ) -> TransformationOutputFinalizationLifecycleResult:
        """Invoke Story 7 outside claim transactions and apply its immutable result."""
        claim = claimed.claim
        result = self._finalization_service.finalize_run(
            tenant_id=claim.tenant_id,
            run_id=claim.run_id,
            claim=claim,
        )
        lifecycle = self._job_service.apply_result(claimed=claimed, result=result)
        if lifecycle.action.value in {"SUCCEEDED", "FAILED"}:
            self._workspace_lease_manager.request_cleanup_for_run(run_id=claim.run_id)
        self._metrics.record_finalization_lifecycle(
            action=lifecycle.action.value,
            error_code=lifecycle.error_code or "",
        )
        return lifecycle
