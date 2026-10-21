from __future__ import annotations

from datetime import timedelta
from threading import Event
from unittest.mock import MagicMock

from django.test import SimpleTestCase
from django.utils import timezone

from resource_registry.services.transformation_output_finalization_worker_contracts import (
    ClaimedTransformationOutputFinalization,
    TransformationOutputFinalizationQueueSnapshot,
)
from resource_registry.services.transformation_output_finalization_worker_runner import (
    TransformationOutputFinalizationWorkerRunner,
)
from resource_registry.transformations.operational.logging import (
    TransformationOperationalLogContextBuilder,
)
from resource_registry.transformations.operational.metrics import NoOpMetricsAdapter
from resource_registry.transformations.output_finalization import (
    TransformationOutputFinalizationClaim,
)

_EXPECTED_CLEANED_LEASES = 2


class TransformationOutputFinalizationWorkerRunnerTests(SimpleTestCase):
    """Verify bounded worker-loop delegation without ORM mutation."""

    def test_idle_one_shot_recovers_before_claim_and_returns(self) -> None:
        job_service = MagicMock()
        job_service.get_queue_snapshot.return_value = _queue_snapshot()
        job_service.recover_expired_runs.return_value = ()
        job_service.claim_next_run.return_value = None
        workspace_manager = MagicMock()
        workspace_manager.recover_and_cleanup.return_value = 2
        runner = _runner(job_service=job_service, workspace_manager=workspace_manager)

        stats = runner.run(once=True, stop_event=Event())

        assert stats.claimed_runs == 0
        assert stats.recovered_runs == 0
        assert stats.cleaned_leases == _EXPECTED_CLEANED_LEASES
        job_service.recover_expired_runs.assert_called_once()
        workspace_manager.recover_and_cleanup.assert_called_once()
        job_service.claim_next_run.assert_called_once()

    def test_claimed_job_delegates_to_coordinator(self) -> None:
        now = timezone.now()
        claimed = ClaimedTransformationOutputFinalization(
            claim=TransformationOutputFinalizationClaim(
                tenant_id="00000000-0000-0000-0000-000000000001",
                run_id="00000000-0000-0000-0000-000000000002",
                worker_id="finalizer-one",
                token="00000000-0000-0000-0000-000000000003",
                attempt_number=1,
                claimed_at=now,
                lease_expires_at=now + timedelta(seconds=30),
                deadline_at=now + timedelta(seconds=120),
                correlation_id="correlation",
            ),
            lease_lost_event=Event(),
        )
        job_service = MagicMock()
        job_service.get_queue_snapshot.return_value = _queue_snapshot()
        job_service.recover_expired_runs.return_value = ()
        job_service.claim_next_run.return_value = claimed
        job_service.renew_lease.return_value = True
        workspace_manager = MagicMock()
        workspace_manager.recover_and_cleanup.return_value = 0
        coordinator = MagicMock()
        coordinator.finalize_claimed_run.side_effect = RuntimeError("interrupted")
        runner = _runner(
            job_service=job_service,
            workspace_manager=workspace_manager,
            coordinator=coordinator,
        )

        stats = runner.run(once=True)

        assert stats.claimed_runs == 1
        coordinator.finalize_claimed_run.assert_called_once_with(claimed=claimed)


def _runner(
    *,
    job_service: MagicMock,
    workspace_manager: MagicMock,
    coordinator: MagicMock | None = None,
) -> TransformationOutputFinalizationWorkerRunner:
    return TransformationOutputFinalizationWorkerRunner(
        job_service=job_service,
        coordinator=coordinator or MagicMock(),
        workspace_lease_manager=workspace_manager,
        metrics=NoOpMetricsAdapter(),
        log_context_builder=TransformationOperationalLogContextBuilder(),
        poll_interval_seconds=0.1,
        lease_seconds=30,
        heartbeat_seconds=10,
        timeout_seconds=120,
        recovery_batch_size=5,
        worker_id="finalizer-one",
    )


def _queue_snapshot() -> TransformationOutputFinalizationQueueSnapshot:
    return TransformationOutputFinalizationQueueSnapshot(
        pending_count=0,
        active_count=0,
        stale_count=0,
        cleanup_pending_count=0,
        oldest_pending_age_seconds=0,
    )
