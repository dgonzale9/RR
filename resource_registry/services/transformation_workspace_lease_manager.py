from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from resource_registry.models import (
    TransformationRunStatus,
    TransformationRunStepAttempt,
    TransformationWorkspaceLease,
    TransformationWorkspaceLeaseState,
)
from resource_registry.storage.exceptions import StorageObjectNotFoundError
from resource_registry.storage.factory import DataRegistryStorageFactory
from resource_registry.transformations.operational.metrics import (
    TransformationOperationalMetricsPort,
)


@dataclass(frozen=True)
class WorkspaceLease:
    """Expose immutable provider-neutral workspace ownership."""

    lease_id: str
    lease_generation: int
    workspace_key: str
    maximum_bytes: int
    expires_at: datetime


class TransformationWorkspaceLeaseManager:
    """Own durable workspace allocation, quota, fencing, expiry, and cleanup."""

    def __init__(
        self,
        *,
        storage_factory: DataRegistryStorageFactory,
        workspace_quota_bytes: int,
        workspace_maximum_bytes: int,
        lease_seconds: int,
        metrics: TransformationOperationalMetricsPort | None = None,
    ) -> None:
        if workspace_quota_bytes <= 0 or workspace_maximum_bytes <= 0 or lease_seconds <= 0:
            raise ValueError("Workspace limits and lease duration must be positive.")
        if workspace_maximum_bytes > workspace_quota_bytes:
            raise ValueError("Workspace object maximum cannot exceed the tenant quota.")
        self._storage_factory = storage_factory
        self._workspace_quota_bytes = int(workspace_quota_bytes)
        self._workspace_maximum_bytes = int(workspace_maximum_bytes)
        self._lease_seconds = int(lease_seconds)
        self._metrics = metrics

    def allocate(
        self,
        *,
        run_id: str,
        attempt_idempotency_token: str,
        output_logical_name: str,
        storage_provider: str,
        workspace_key: str,
    ) -> WorkspaceLease:
        """Allocate or idempotently return one deterministic workspace lease."""
        with transaction.atomic():
            step_attempt = (
                TransformationRunStepAttempt.objects.select_for_update()
                .select_related("run")
                .get(
                    run_id=UUID(run_id),
                    attempt_idempotency_token=attempt_idempotency_token,
                )
            )
            existing = (
                TransformationWorkspaceLease.objects.select_for_update()
                .filter(
                    step_attempt=step_attempt,
                    output_logical_name=output_logical_name,
                )
                .first()
            )
            if (
                existing is not None
                and existing.state != TransformationWorkspaceLeaseState.RELEASED
            ):
                if (
                    existing.storage_provider != storage_provider
                    or existing.workspace_key != workspace_key
                ):
                    raise ValueError(
                        "Workspace lease identity conflicts with deterministic output.",
                    )
                return _lease_contract(existing)
            reserved = (
                TransformationWorkspaceLease.objects.filter(
                    tenant_id=step_attempt.run.tenant_id,
                    state__in=[
                        TransformationWorkspaceLeaseState.ACTIVE,
                        TransformationWorkspaceLeaseState.COMMITTED,
                        TransformationWorkspaceLeaseState.EXPIRED,
                        TransformationWorkspaceLeaseState.CLEANUP_PENDING,
                    ],
                ).aggregate(total=Sum("maximum_bytes"))["total"]
                or 0
            )
            if reserved + self._workspace_maximum_bytes > self._workspace_quota_bytes:
                self._record_metric(
                    leased_bytes=int(reserved),
                    outcome="quota_rejected",
                    error_code="workspace_quota_exhausted",
                )
                raise ValueError("Tenant transformation workspace quota is exhausted.")
            if existing is None:
                lease = TransformationWorkspaceLease.objects.create(
                    tenant_id=step_attempt.run.tenant_id,
                    run=step_attempt.run,
                    step_attempt=step_attempt,
                    run_attempt_number=step_attempt.run_attempt_number,
                    step_id=step_attempt.step_id,
                    output_logical_name=output_logical_name,
                    storage_provider=storage_provider,
                    workspace_key=workspace_key,
                    maximum_bytes=self._workspace_maximum_bytes,
                    expires_at=timezone.now() + timedelta(seconds=self._lease_seconds),
                )
            else:
                existing.lease_generation += 1
                existing.state = TransformationWorkspaceLeaseState.ACTIVE
                existing.checksum = None
                existing.size_bytes = None
                existing.maximum_bytes = self._workspace_maximum_bytes
                existing.cleanup_owner = None
                existing.cleanup_requested_at = None
                existing.released_at = None
                existing.committed_at = None
                existing.expires_at = timezone.now() + timedelta(seconds=self._lease_seconds)
                existing.save()
                lease = existing
            self._record_metric(
                leased_bytes=int(reserved) + self._workspace_maximum_bytes,
                outcome="allocated",
            )
            return _lease_contract(lease)

    def commit(
        self,
        *,
        lease_id: str,
        lease_generation: int,
        checksum: str,
        size_bytes: int,
    ) -> None:
        """Commit verified workspace metadata through its exact lease generation."""
        if size_bytes < 0:
            raise ValueError("Workspace output size cannot be negative.")
        with transaction.atomic():
            lease = TransformationWorkspaceLease.objects.select_for_update().get(id=lease_id)
            _require_generation(lease=lease, lease_generation=lease_generation)
            if size_bytes > lease.maximum_bytes:
                raise ValueError("Workspace output exceeds its immutable byte limit.")
            if lease.state == TransformationWorkspaceLeaseState.COMMITTED:
                if lease.checksum != checksum or lease.size_bytes != size_bytes:
                    raise ValueError("Committed workspace integrity metadata differs.")
                return
            if lease.state != TransformationWorkspaceLeaseState.ACTIVE:
                raise ValueError("Workspace lease is not active.")
            lease.state = TransformationWorkspaceLeaseState.COMMITTED
            lease.checksum = checksum
            lease.size_bytes = size_bytes
            lease.committed_at = timezone.now()
            lease.save()

    def request_cleanup_for_run(self, *, run_id: str) -> int:
        """Schedule runtime workspace cleanup after a terminal run transition."""
        now = timezone.now()
        return TransformationWorkspaceLease.objects.filter(
            run_id=UUID(run_id),
            run__status__in=[TransformationRunStatus.SUCCEEDED, TransformationRunStatus.FAILED],
            state__in=[
                TransformationWorkspaceLeaseState.ACTIVE,
                TransformationWorkspaceLeaseState.COMMITTED,
                TransformationWorkspaceLeaseState.EXPIRED,
            ],
        ).update(
            state=TransformationWorkspaceLeaseState.CLEANUP_PENDING,
            cleanup_requested_at=now,
            updated_at=now,
        )

    def recover_and_cleanup(
        self,
        *,
        worker_id: str,
        batch_size: int,
        tenant_id: str | None = None,
    ) -> int:
        """Expire safe leases and idempotently clean the durable cleanup backlog."""
        now = timezone.now()
        terminal = TransformationWorkspaceLease.objects.filter(
            run__status__in=[
                TransformationRunStatus.SUCCEEDED,
                TransformationRunStatus.FAILED,
                TransformationRunStatus.CANCELLED,
            ],
            state__in=[
                TransformationWorkspaceLeaseState.ACTIVE,
                TransformationWorkspaceLeaseState.COMMITTED,
                TransformationWorkspaceLeaseState.EXPIRED,
            ],
        )
        expired_unpublished = TransformationWorkspaceLease.objects.filter(
            state=TransformationWorkspaceLeaseState.ACTIVE,
            expires_at__lt=now,
        ).exclude(run__status=TransformationRunStatus.OUTPUT_PENDING)
        if tenant_id is not None:
            terminal = terminal.filter(tenant_id=tenant_id)
            expired_unpublished = expired_unpublished.filter(tenant_id=tenant_id)
        terminal.update(
            state=TransformationWorkspaceLeaseState.CLEANUP_PENDING,
            cleanup_requested_at=now,
            updated_at=now,
        )
        expired_unpublished.update(
            state=TransformationWorkspaceLeaseState.CLEANUP_PENDING,
            cleanup_requested_at=now,
            updated_at=now,
        )
        with transaction.atomic():
            queryset = TransformationWorkspaceLease.objects.filter(
                state=TransformationWorkspaceLeaseState.CLEANUP_PENDING,
            )
            if tenant_id is not None:
                queryset = queryset.filter(tenant_id=tenant_id)
            leases = tuple(
                queryset.order_by("cleanup_requested_at", "id").select_for_update(skip_locked=True)[
                    : max(int(batch_size), 1)
                ],
            )
            identities = tuple(
                (lease.id, lease.lease_generation, lease.storage_provider, lease.workspace_key)
                for lease in leases
            )
            for lease in leases:
                lease.cleanup_owner = worker_id
                lease.save(update_fields=["cleanup_owner", "updated_at"])
        cleaned = 0
        for lease_id, generation, provider_name, workspace_key in identities:
            try:
                provider = self._storage_factory.create_provider(provider_name)
                try:
                    provider.head(provider.get_uri(workspace_key))
                except StorageObjectNotFoundError:
                    pass
                else:
                    provider.delete(provider.get_uri(workspace_key))
                self._release(lease_id=lease_id, lease_generation=generation, worker_id=worker_id)
                self._record_metric(leased_bytes=None, outcome="cleaned")
                cleaned += 1
            except Exception:
                self._record_cleanup_failure(
                    lease_id=lease_id,
                    lease_generation=generation,
                    worker_id=worker_id,
                )
                self._record_metric(
                    leased_bytes=None,
                    outcome="cleanup_failed",
                    error_code="workspace_cleanup_failed",
                )
        return cleaned

    def _release(self, *, lease_id: UUID, lease_generation: int, worker_id: str) -> None:
        with transaction.atomic():
            lease = TransformationWorkspaceLease.objects.select_for_update().get(id=lease_id)
            _require_generation(lease=lease, lease_generation=lease_generation)
            if lease.cleanup_owner != worker_id:
                return
            lease.state = TransformationWorkspaceLeaseState.RELEASED
            lease.released_at = timezone.now()
            lease.cleanup_owner = None
            lease.last_error_code = None
            lease.save()

    def _record_metric(
        self,
        *,
        leased_bytes: int | None,
        outcome: str,
        error_code: str = "",
    ) -> None:
        if self._metrics is None:
            return
        try:
            self._metrics.record_workspace(
                leased_bytes=leased_bytes,
                outcome=outcome,
                error_code=error_code,
            )
        except Exception:
            return

    def _record_cleanup_failure(
        self,
        *,
        lease_id: UUID,
        lease_generation: int,
        worker_id: str,
    ) -> None:
        with transaction.atomic():
            lease = TransformationWorkspaceLease.objects.select_for_update().get(id=lease_id)
            _require_generation(lease=lease, lease_generation=lease_generation)
            if lease.cleanup_owner != worker_id:
                return
            lease.cleanup_owner = None
            lease.cleanup_retry_count += 1
            lease.last_error_code = "workspace_cleanup_failed"
            lease.save()


def _require_generation(
    *,
    lease: TransformationWorkspaceLease,
    lease_generation: int,
) -> None:
    if lease.lease_generation != lease_generation:
        raise ValueError("Workspace lease generation is stale.")


def _lease_contract(lease: TransformationWorkspaceLease) -> WorkspaceLease:
    return WorkspaceLease(
        lease_id=str(lease.id),
        lease_generation=lease.lease_generation,
        workspace_key=lease.workspace_key,
        maximum_bytes=lease.maximum_bytes,
        expires_at=lease.expires_at,
    )
