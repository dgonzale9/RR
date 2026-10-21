from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from django.db import close_old_connections
from django.utils import timezone

from resource_registry.models import (
    TransformationRun,
    TransformationRunOutputStatus,
    TransformationRunStatus,
    TransformationRunStepAttempt,
    TransformationRunStepAttemptStatus,
)
from resource_registry.services.transformation_run_execution_coordinator import (
    TransformationRunExecutionCoordinator,
)
from resource_registry.services.transformation_run_worker_contracts import (
    ClaimedTransformationRun,
    TransformationRunLifecycleAction,
)
from resource_registry.tests.test_transformation_attempt_persistence import _create_run, _Verifier
from resource_registry.tests.test_transformation_run_execution_service import _Provider
from resource_registry.tests.transformation_runtime_test_helpers import (
    build_test_job_service,
    freeze_test_provider_registry,
)
from resource_registry.transformations.execution.checkpoint_persistence import (
    TransformationStepCheckpointService,
)
from resource_registry.transformations.execution.contracts import (
    TransformationExecutionFailure,
    TransformationRunAttemptExecutionOutcome,
    TransformationRunAttemptExecutionResult,
)
from resource_registry.transformations.execution.service import TransformationRunExecutionService
from resource_registry.transformations.operational.metrics import NoOpMetricsAdapter
from resource_registry.transformations.providers import (
    ProviderSideEffectDisposition,
    TransformationProviderRegistry,
)

pytestmark = pytest.mark.django_db(transaction=True)


def _claim(
    run: TransformationRun,
    *,
    worker_id: str = "worker-one",
) -> ClaimedTransformationRun | None:
    return build_test_job_service().claim_next_run(
        worker_id=worker_id,
        checkpoint_reference_verifier=_Verifier(),
        lease_seconds=120,
        execution_timeout_seconds=0,
        tenant_id=str(run.tenant_id),
    )


def _persist_retryable_failure(
    claimed: ClaimedTransformationRun,
) -> TransformationRunAttemptExecutionResult:
    request = claimed.execution_request
    step = TransformationRunStepAttempt.objects.get(id=request.step_attempts[0].step_attempt_id)
    step.status = TransformationRunStepAttemptStatus.FAILED
    step.is_retryable = True
    step.error_code = "provider_execution_failed"
    step.error_message = "Provider execution failed."
    step.completed_at = timezone.now()
    step.save()
    failure = TransformationExecutionFailure(
        code="provider_execution_failed",
        message="Provider execution failed.",
        retryable=True,
        side_effect_disposition=ProviderSideEffectDisposition.NOT_STARTED,
        step_id=step.step_id,
        step_index=step.step_index,
    )
    return TransformationRunAttemptExecutionResult(
        run_id=request.run_id,
        attempt_number=request.attempt_number,
        attempt_token=request.attempt_token,
        outcome=TransformationRunAttemptExecutionOutcome.FAILED,
        completed_count=0,
        failed_count=1,
        cancelled_count=0,
        reused_count=0,
        final_reference_count=0,
        retryable=True,
        execution_ms=10,
        failure=failure,
    )


def test_atomic_claim_initializes_exact_attempt_and_operational_fence() -> None:
    run = _create_run()

    claimed = _claim(run)

    assert claimed is not None
    run.refresh_from_db()
    step = TransformationRunStepAttempt.objects.get(run=run)
    assert run.status == TransformationRunStatus.RUNNING
    assert run.claim_owner == "worker-one"
    assert run.current_attempt_token == step.run_attempt_token
    assert str(run.current_attempt_token) == claimed.execution_request.attempt_token
    assert run.claimed_at == run.heartbeat_at
    assert run.lease_expires_at > run.heartbeat_at


def test_claim_rejects_tampered_authorization_before_attempt_initialization() -> None:
    run = _create_run()
    run.authorization_snapshot_json = {
        **run.authorization_snapshot_json,
        "authorized_actor": "forged-actor",
    }
    run.save(update_fields=["authorization_snapshot_json"])

    claimed = _claim(run)

    assert claimed is None
    run.refresh_from_db()
    assert run.status == TransformationRunStatus.FAILED
    assert run.error_code == "invalid_authorization_snapshot"
    assert not TransformationRunStepAttempt.objects.filter(run=run).exists()


def test_successful_attempt_moves_to_output_pending_without_finalizing_outputs() -> None:
    run = _create_run()
    claimed = _claim(run)
    assert claimed is not None
    registry = TransformationProviderRegistry()
    registry.register_provider(provider=_Provider())
    freeze_test_provider_registry(registry)
    job_service = build_test_job_service()
    coordinator = TransformationRunExecutionCoordinator(
        execution_service=TransformationRunExecutionService(
            provider_registry=registry,
            persistence=TransformationStepCheckpointService(),
        ),
        job_service=job_service,
        metrics=NoOpMetricsAdapter(),
    )

    result = coordinator.execute_claimed_run(claimed=claimed)

    run.refresh_from_db()
    outputs = tuple(run.outputs.all())
    assert result.lifecycle.action == TransformationRunLifecycleAction.OUTPUT_PENDING
    assert run.status == TransformationRunStatus.OUTPUT_PENDING
    assert run.current_attempt_token is None
    assert run.claim_owner is None
    assert run.completed_at is None
    assert all(output.status == TransformationRunOutputStatus.PENDING for output in outputs)
    assert all(output.producing_step_attempt_id is not None for output in outputs)


def test_expired_all_successful_attempt_recovers_without_provider_reexecution() -> None:
    run = _create_run()
    claimed = _claim(run)
    assert claimed is not None
    provider = _Provider()
    registry = TransformationProviderRegistry()
    registry.register_provider(provider=provider)
    freeze_test_provider_registry(registry)
    attempt = TransformationRunExecutionService(
        provider_registry=registry,
        persistence=TransformationStepCheckpointService(),
    ).execute_attempt(request=claimed.execution_request)
    assert attempt.outcome.value == "SUCCEEDED"
    TransformationRun.objects.filter(id=run.id).update(
        claimed_at=timezone.now() - timedelta(seconds=3),
        heartbeat_at=timezone.now() - timedelta(seconds=2),
        lease_expires_at=timezone.now() - timedelta(seconds=1),
    )

    recoveries = build_test_job_service().recover_expired_runs(batch_size=1)

    run.refresh_from_db()
    assert recoveries[0].action == TransformationRunLifecycleAction.OUTPUT_PENDING
    assert run.status == TransformationRunStatus.OUTPUT_PENDING
    assert len(provider.requests) == 1


def test_expired_pre_invocation_attempt_requeues_once_and_preserves_history() -> None:
    run = _create_run()
    claimed = _claim(run)
    assert claimed is not None
    TransformationRun.objects.filter(id=run.id).update(
        claimed_at=timezone.now() - timedelta(seconds=3),
        heartbeat_at=timezone.now() - timedelta(seconds=2),
        lease_expires_at=timezone.now() - timedelta(seconds=1),
    )

    recoveries = build_test_job_service(max_retries=2).recover_expired_runs(batch_size=1)

    run.refresh_from_db()
    step = TransformationRunStepAttempt.objects.get(run=run)
    assert recoveries[0].action == TransformationRunLifecycleAction.REQUEUED
    assert run.status == TransformationRunStatus.QUEUED
    assert run.retry_count == 1
    assert run.current_attempt_token is None
    assert step.status == TransformationRunStepAttemptStatus.CANCELLED


def test_expired_ambiguous_non_idempotent_invocation_fails_closed() -> None:
    run = _create_run(is_idempotent=False)
    claimed = _claim(run)
    assert claimed is not None
    step = TransformationRunStepAttempt.objects.get(run=run)
    step.status = TransformationRunStepAttemptStatus.RUNNING
    step.invocation_may_have_started = True
    step.started_at = timezone.now()
    step.save()
    TransformationRun.objects.filter(id=run.id).update(
        claimed_at=timezone.now() - timedelta(seconds=3),
        heartbeat_at=timezone.now() - timedelta(seconds=2),
        lease_expires_at=timezone.now() - timedelta(seconds=1),
    )

    recovery = build_test_job_service(max_retries=3).recover_expired_runs(batch_size=1)[0]

    run.refresh_from_db()
    step.refresh_from_db()
    assert recovery.action == TransformationRunLifecycleAction.FAILED
    assert recovery.error_code == "side_effect_ambiguous"
    assert run.status == TransformationRunStatus.FAILED
    assert run.retry_count == 0
    assert step.status == TransformationRunStepAttemptStatus.FAILED
    assert step.side_effect_disposition == "UNKNOWN"


def test_cancellation_precedes_expired_attempt_recovery() -> None:
    run = _create_run()
    claimed = _claim(run)
    assert claimed is not None
    TransformationRun.objects.filter(id=run.id).update(
        cancellation_requested_at=timezone.now(),
        claimed_at=timezone.now() - timedelta(seconds=3),
        heartbeat_at=timezone.now() - timedelta(seconds=2),
        lease_expires_at=timezone.now() - timedelta(seconds=1),
    )

    recovery = build_test_job_service().recover_expired_runs(batch_size=1)[0]

    run.refresh_from_db()
    assert recovery.action == TransformationRunLifecycleAction.CANCELLED
    assert run.status == TransformationRunStatus.CANCELLED
    assert run.retry_count == 0


def test_retryable_failure_requeues_and_only_job_service_increments_retry_count() -> None:
    run = _create_run()
    claimed = _claim(run)
    assert claimed is not None
    result = _persist_retryable_failure(claimed)

    lifecycle = build_test_job_service(max_retries=2).apply_attempt_result(
        claimed=claimed,
        result=result,
    )

    run.refresh_from_db()
    assert lifecycle.action == TransformationRunLifecycleAction.REQUEUED
    assert run.status == TransformationRunStatus.QUEUED
    assert run.retry_count == 1


def test_retry_exhaustion_terminalizes_without_incrementing_again() -> None:
    run = _create_run()
    claimed = _claim(run)
    assert claimed is not None
    result = _persist_retryable_failure(claimed)

    lifecycle = build_test_job_service(max_retries=0).apply_attempt_result(
        claimed=claimed,
        result=result,
    )

    run.refresh_from_db()
    assert lifecycle.action == TransformationRunLifecycleAction.FAILED
    assert lifecycle.error_code == "retry_exhausted"
    assert run.status == TransformationRunStatus.FAILED
    assert run.retry_count == 0
    assert run.completed_at is not None


def test_stale_worker_cannot_renew_or_overwrite_requeued_run() -> None:
    run = _create_run()
    claimed = _claim(run)
    assert claimed is not None
    result = _persist_retryable_failure(claimed)
    service = build_test_job_service(max_retries=2)
    service.apply_attempt_result(claimed=claimed, result=result)

    assert service.renew_lease(claimed=claimed, lease_seconds=120) is False
    stale_lifecycle = service.apply_attempt_result(claimed=claimed, result=result)

    run.refresh_from_db()
    assert stale_lifecycle.action == TransformationRunLifecycleAction.STALE
    assert run.status == TransformationRunStatus.QUEUED
    assert run.retry_count == 1


def test_concurrent_workers_claim_distinct_runs() -> None:
    first = _create_run()
    second = _create_run()

    def claim_one(worker_id: str) -> str | None:
        close_old_connections()
        try:
            claimed = build_test_job_service().claim_next_run(
                worker_id=worker_id,
                checkpoint_reference_verifier=_Verifier(),
                lease_seconds=120,
                execution_timeout_seconds=0,
            )
            return claimed.execution_request.run_id if claimed is not None else None
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        claimed_ids = set(executor.map(claim_one, ("worker-a", "worker-b")))

    assert claimed_ids == {str(first.id), str(second.id)}
