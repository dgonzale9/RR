from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import pytest
from django.db import close_old_connections, connection
from django.utils import timezone

from resource_registry.events.audit_service import AuditService
from resource_registry.events.event_publisher import InMemoryEventPublisher
from resource_registry.models import (
    TransformationRun,
    TransformationRunOutputStatus,
    TransformationRunStatus,
    TransformationWorkspaceLease,
    TransformationWorkspaceLeaseState,
)
from resource_registry.services.transformation_output_finalization_worker_contracts import (
    TransformationOutputFinalizationLifecycleAction,
)
from resource_registry.services.transformation_workspace_lease_manager import (
    TransformationWorkspaceLeaseManager,
)
from resource_registry.storage.factory import DataRegistryStorageFactory
from resource_registry.tests.test_transformation_output_finalization import (
    _create_output_pending_run,
    _standalone_policy,
)
from resource_registry.tests.transformation_runtime_test_helpers import (
    build_test_output_finalization_job_service,
    build_test_output_finalization_service,
)
from resource_registry.transformations.output_finalization import (
    TransformationOutputFinalizationResult,
)

pytestmark = pytest.mark.django_db(transaction=True)


def _run(tmp_path: Path) -> TransformationRun:
    source = tmp_path / "source.parquet"
    source.write_bytes(b"durable finalization")
    return _create_output_pending_run(source_path=source, policy=_standalone_policy())


def test_atomic_claim_persists_independent_finalization_fence(tmp_path: Path) -> None:
    run = _run(tmp_path)

    claimed = build_test_output_finalization_job_service().claim_next_run(
        worker_id="finalizer-one",
        lease_seconds=30,
        timeout_seconds=120,
        tenant_id=str(run.tenant_id),
    )

    assert claimed is not None
    run.refresh_from_db()
    assert run.status == TransformationRunStatus.OUTPUT_PENDING
    assert str(run.current_finalization_token) == claimed.claim.token
    assert run.current_attempt_token is None
    assert run.finalization_attempt_number == 1
    assert run.finalization_retry_count == 0
    assert run.finalization_lease_expires_at is not None
    assert run.finalization_deadline_at is not None
    assert run.finalization_lease_expires_at <= run.finalization_deadline_at


def test_finalization_claim_rejects_tampered_authorization(tmp_path: Path) -> None:
    run = _run(tmp_path)
    run.authorization_snapshot_json = {
        **run.authorization_snapshot_json,
        "authorized_actor": "forged-actor",
    }
    run.save(update_fields=["authorization_snapshot_json"])

    event_publisher = InMemoryEventPublisher()
    claimed = build_test_output_finalization_job_service(
        audit_service=AuditService(publisher=event_publisher),
    ).claim_next_run(
        worker_id="finalizer-one",
        lease_seconds=30,
        timeout_seconds=120,
        tenant_id=str(run.tenant_id),
    )

    assert claimed is None
    run.refresh_from_db()
    assert run.status == TransformationRunStatus.FAILED
    assert run.error_code == "invalid_authorization_snapshot"
    assert run.current_finalization_token is None
    events = event_publisher.list_events()
    assert len(events) == 1
    assert events[0].transition == "OUTPUT_FINALIZATION_AUTHORITY_INVALID"
    assert events[0].metadata["error_code"] == "invalid_authorization_snapshot"


def test_expired_finalization_recovery_revalidates_authority_before_completion(
    tmp_path: Path,
) -> None:
    run = _run(tmp_path)
    service = build_test_output_finalization_job_service()
    claimed = service.claim_next_run(
        worker_id="finalizer-one",
        lease_seconds=30,
        timeout_seconds=120,
        tenant_id=str(run.tenant_id),
    )
    assert claimed is not None
    output = run.outputs.get()
    output.status = TransformationRunOutputStatus.MATERIALIZED
    output.checksum_algorithm = "sha256"
    output.checksum_value = "a" * 64
    output.size_bytes = 1
    output.canonical_uri = "local://managed/result.parquet"
    output.save()
    run.refresh_from_db()
    tampered_authorization = {
        **run.authorization_snapshot_json,
        "authorized_actor": "forged-actor",
    }
    now = timezone.now()
    TransformationRun.objects.filter(id=run.id).update(
        authorization_snapshot_json=tampered_authorization,
        finalization_claimed_at=now - timedelta(seconds=3),
        finalization_heartbeat_at=now - timedelta(seconds=2),
        finalization_lease_expires_at=now - timedelta(seconds=1),
    )

    recoveries = service.recover_expired_runs(batch_size=1, tenant_id=str(run.tenant_id))

    assert len(recoveries) == 1
    assert recoveries[0].action == TransformationOutputFinalizationLifecycleAction.FAILED
    assert recoveries[0].error_code == "invalid_authorization_snapshot"
    run.refresh_from_db()
    assert run.status == TransformationRunStatus.FAILED
    assert run.current_finalization_token is None


def test_terminal_outputs_complete_without_claim(tmp_path: Path) -> None:
    run = _run(tmp_path)
    output = run.outputs.get()
    output.status = TransformationRunOutputStatus.MATERIALIZED
    output.checksum_algorithm = "sha256"
    output.checksum_value = "a" * 64
    output.size_bytes = 1
    output.canonical_uri = "local://managed/result.parquet"
    output.save()

    claimed = build_test_output_finalization_job_service().claim_next_run(
        worker_id="finalizer-one",
        lease_seconds=30,
        timeout_seconds=120,
    )

    run.refresh_from_db()
    assert claimed is None
    assert run.status == TransformationRunStatus.SUCCEEDED
    assert run.current_finalization_token is None


def test_retryable_result_increments_only_finalization_retry(tmp_path: Path) -> None:
    run = _run(tmp_path)
    service = build_test_output_finalization_job_service(max_retries=2)
    claimed = service.claim_next_run(
        worker_id="finalizer-one",
        lease_seconds=30,
        timeout_seconds=120,
    )
    assert claimed is not None
    result = TransformationOutputFinalizationResult(
        run_id=run.id,
        tenant_id=run.tenant_id,
        status=TransformationRunStatus.OUTPUT_PENDING,
        materialized_output_count=0,
        registered_output_count=0,
        failed_output_count=0,
        is_retryable=True,
        error_code="output_storage_failed",
    )

    lifecycle = service.apply_result(claimed=claimed, result=result)

    run.refresh_from_db()
    assert lifecycle.action == TransformationOutputFinalizationLifecycleAction.REQUEUED
    assert run.finalization_retry_count == 1
    assert run.retry_count == 0
    assert run.current_finalization_token is None


def test_stale_worker_cannot_finalize_newer_claim(tmp_path: Path) -> None:
    run = _run(tmp_path)
    job_service = build_test_output_finalization_job_service()
    stale = job_service.claim_next_run(
        worker_id="stale-finalizer",
        lease_seconds=1,
        timeout_seconds=120,
    )
    assert stale is not None
    now = timezone.now()
    TransformationRun.objects.filter(id=run.id).update(
        finalization_claimed_at=now - timedelta(seconds=3),
        finalization_heartbeat_at=now - timedelta(seconds=2),
        finalization_lease_expires_at=now - timedelta(seconds=1),
    )
    job_service.recover_expired_runs(batch_size=1)
    current = job_service.claim_next_run(
        worker_id="current-finalizer",
        lease_seconds=30,
        timeout_seconds=120,
    )
    assert current is not None

    with pytest.raises(Exception, match="stale"):
        build_test_output_finalization_service().finalize_run(
            tenant_id=run.tenant_id,
            run_id=run.id,
            claim=stale.claim,
        )

    run.refresh_from_db()
    assert str(run.current_finalization_token) == current.claim.token


def test_expired_claim_with_terminal_outputs_completes_without_retry(tmp_path: Path) -> None:
    run = _run(tmp_path)
    service = build_test_output_finalization_job_service()
    claimed = service.claim_next_run(
        worker_id="expired-finalizer",
        lease_seconds=30,
        timeout_seconds=120,
    )
    assert claimed is not None
    output = run.outputs.get()
    output.status = TransformationRunOutputStatus.MATERIALIZED
    output.checksum_algorithm = "sha256"
    output.checksum_value = "a" * 64
    output.size_bytes = 1
    output.canonical_uri = "local://managed/result.parquet"
    output.save()
    now = timezone.now()
    TransformationRun.objects.filter(id=run.id).update(
        finalization_claimed_at=now - timedelta(seconds=3),
        finalization_heartbeat_at=now - timedelta(seconds=2),
        finalization_lease_expires_at=now - timedelta(seconds=1),
    )

    recovery = service.recover_expired_runs(batch_size=1)[0]

    run.refresh_from_db()
    assert recovery.action == TransformationOutputFinalizationLifecycleAction.SUCCEEDED
    assert run.status == TransformationRunStatus.SUCCEEDED
    assert run.finalization_retry_count == 0


def test_retry_exhaustion_fails_without_changing_execution_retry(tmp_path: Path) -> None:
    run = _run(tmp_path)
    service = build_test_output_finalization_job_service(max_retries=0)
    claimed = service.claim_next_run(
        worker_id="finalizer-one",
        lease_seconds=30,
        timeout_seconds=120,
    )
    assert claimed is not None
    result = TransformationOutputFinalizationResult(
        run_id=run.id,
        tenant_id=run.tenant_id,
        status=TransformationRunStatus.OUTPUT_PENDING,
        materialized_output_count=0,
        registered_output_count=0,
        failed_output_count=0,
        is_retryable=True,
        error_code="output_storage_failed",
    )

    lifecycle = service.apply_result(claimed=claimed, result=result)

    run.refresh_from_db()
    assert lifecycle.action == TransformationOutputFinalizationLifecycleAction.FAILED
    assert run.status == TransformationRunStatus.FAILED
    assert run.retry_count == 0
    assert run.current_finalization_token is None


def test_workspace_lease_generation_fences_stale_cleanup(tmp_path: Path) -> None:
    run = _run(tmp_path)
    attempt = run.step_attempts.get()
    manager = TransformationWorkspaceLeaseManager(
        storage_factory=DataRegistryStorageFactory(),
        workspace_quota_bytes=1_000,
        workspace_maximum_bytes=100,
        lease_seconds=60,
    )
    first = manager.allocate(
        run_id=str(run.id),
        attempt_idempotency_token=attempt.attempt_idempotency_token,
        output_logical_name="result",
        storage_provider="local",
        workspace_key="workspace/result.parquet",
    )
    manager.commit(
        lease_id=first.lease_id,
        lease_generation=first.lease_generation,
        checksum="a" * 64,
        size_bytes=10,
    )
    run.status = TransformationRunStatus.FAILED
    run.completed_at = timezone.now()
    run.save()
    assert manager.request_cleanup_for_run(run_id=str(run.id)) == 1
    assert manager.recover_and_cleanup(worker_id="cleanup-one", batch_size=1) == 1
    second = manager.allocate(
        run_id=str(run.id),
        attempt_idempotency_token=attempt.attempt_idempotency_token,
        output_logical_name="result",
        storage_provider="local",
        workspace_key="workspace/result.parquet",
    )

    assert second.lease_generation == first.lease_generation + 1
    with pytest.raises(ValueError, match="stale"):
        manager.commit(
            lease_id=first.lease_id,
            lease_generation=first.lease_generation,
            checksum="a" * 64,
            size_bytes=10,
        )
    lease = TransformationWorkspaceLease.objects.get(id=second.lease_id)
    assert lease.state == TransformationWorkspaceLeaseState.ACTIVE


def test_workspace_quota_fails_closed(tmp_path: Path) -> None:
    run = _run(tmp_path)
    attempt = run.step_attempts.get()
    manager = TransformationWorkspaceLeaseManager(
        storage_factory=DataRegistryStorageFactory(),
        workspace_quota_bytes=100,
        workspace_maximum_bytes=100,
        lease_seconds=60,
    )
    manager.allocate(
        run_id=str(run.id),
        attempt_idempotency_token=attempt.attempt_idempotency_token,
        output_logical_name="result",
        storage_provider="local",
        workspace_key="workspace/result.parquet",
    )
    attempt.attempt_idempotency_token = "e" * 64
    attempt.step_id = "second"
    attempt.pk = None
    attempt.id = None
    attempt.step_index = 1
    attempt.save(force_insert=True)

    with pytest.raises(ValueError, match="quota"):
        manager.allocate(
            run_id=str(run.id),
            attempt_idempotency_token=attempt.attempt_idempotency_token,
            output_logical_name="second",
            storage_provider="local",
            workspace_key="workspace/second.parquet",
        )


def test_postgresql_concurrent_workers_claim_run_once(tmp_path: Path) -> None:
    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL row-locking coverage requires PostgreSQL.")
    run = _run(tmp_path)

    def claim(worker_id: str) -> str | None:
        close_old_connections()
        try:
            claimed = build_test_output_finalization_job_service().claim_next_run(
                worker_id=worker_id,
                lease_seconds=30,
                timeout_seconds=120,
                tenant_id=str(run.tenant_id),
            )
            return claimed.claim.token if claimed is not None else None
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        tokens = tuple(executor.map(claim, ("finalizer-one", "finalizer-two")))

    assert sum(token is not None for token in tokens) == 1
