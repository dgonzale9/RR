from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable

from django.db import DatabaseError
from django.db.models import Sum
from django.utils import timezone

from resource_registry.events.audit_service import AuditService
from resource_registry.models import (
    TransformationRun,
    TransformationRunStatus,
    TransformationWorkspaceLease,
    TransformationWorkspaceLeaseState,
)
from resource_registry.transformations.operational.authorization import (
    TransformationAuthorizationService,
)
from resource_registry.transformations.operational.contracts import (
    TransformationOperationalHealthSnapshot,
    TransformationOperationalHealthStatus,
)
from resource_registry.transformations.operational.governance import (
    TransformationSecurityPolicyResolver,
)
from resource_registry.transformations.operational.metrics import TransformationOperationalMetricsPort
from resource_registry.transformations.providers import TransformationProviderRegistry


class TransformationOperationalHealthService:
    """Report read-only transformation operational readiness."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        authorization_service: TransformationAuthorizationService,
        policy_resolver: TransformationSecurityPolicyResolver,
        provider_registry: TransformationProviderRegistry,
        audit_service: AuditService,
        metrics: TransformationOperationalMetricsPort,
        workspace_quota_bytes: int,
        finalization_configuration_valid: bool = True,
        finalization_worker_ready: bool = True,
        storage_availability_check: Callable[[], bool] | None = None,
        minimum_free_disk_bytes: int = 0,
    ) -> None:
        self._authorization_service = authorization_service
        self._policy_resolver = policy_resolver
        self._provider_registry = provider_registry
        self._audit_service = audit_service
        self._metrics = metrics
        self._workspace_quota_bytes = workspace_quota_bytes
        self._finalization_configuration_valid = finalization_configuration_valid
        self._finalization_worker_ready = finalization_worker_ready
        self._storage_availability_check = storage_availability_check
        self._minimum_free_disk_bytes = minimum_free_disk_bytes

    def get_health(self, *, tenant_id: str) -> TransformationOperationalHealthSnapshot:
        """Return sanitized readiness without mutating jobs or invoking providers."""
        queue_healthy = True
        queued_count = running_count = output_pending_count = 0
        finalization_active_count = stale_finalization_count = cleanup_pending_count = 0
        workspace_capacity_available = False
        try:
            queryset = TransformationRun.objects.filter(tenant_id=tenant_id)
            queued_count = queryset.filter(status=TransformationRunStatus.QUEUED).count()
            running_count = queryset.filter(status=TransformationRunStatus.RUNNING).count()
            output_pending_count = queryset.filter(
                status=TransformationRunStatus.OUTPUT_PENDING,
            ).count()
            finalization_active_count = queryset.filter(
                status=TransformationRunStatus.OUTPUT_PENDING,
                current_finalization_token__isnull=False,
            ).count()
            stale_finalization_count = queryset.filter(
                status=TransformationRunStatus.OUTPUT_PENDING,
                finalization_lease_expires_at__lt=timezone.now(),
            ).count()
            leases = TransformationWorkspaceLease.objects.filter(tenant_id=tenant_id)
            cleanup_pending_count = leases.filter(
                state=TransformationWorkspaceLeaseState.CLEANUP_PENDING,
            ).count()
            reserved = leases.exclude(
                state=TransformationWorkspaceLeaseState.RELEASED,
            ).aggregate(total=Sum("maximum_bytes"))["total"] or 0
            workspace_capacity_available = reserved < self._workspace_quota_bytes
        except DatabaseError:
            queue_healthy = False
        metrics_available = _safe_availability(self._metrics.is_available)
        audit_available = _safe_availability(self._audit_service.is_available)
        governance_available = _safe_availability(
            lambda: self._policy_resolver.is_available(tenant_id=tenant_id),
        )
        authorization_available = _safe_availability(
            self._authorization_service.is_available,
        )
        registry_available = _safe_availability(
            lambda: bool(self._provider_registry.list_provider_identities()),
        )
        storage_availability_check = self._storage_availability_check
        storage_available = (
            True
            if storage_availability_check is None
            else _safe_availability(storage_availability_check)
        )
        minimum_free_disk_available = (
            shutil.disk_usage(tempfile.gettempdir()).free >= self._minimum_free_disk_bytes
        )
        if not (
            queue_healthy
            and governance_available
            and authorization_available
            and registry_available
            and self._finalization_configuration_valid
            and workspace_capacity_available
            and self._finalization_worker_ready
            and storage_available
            and minimum_free_disk_available
        ):
            status = TransformationOperationalHealthStatus.NOT_READY
        elif not metrics_available or not audit_available:
            status = TransformationOperationalHealthStatus.DEGRADED
        else:
            status = TransformationOperationalHealthStatus.READY
        return TransformationOperationalHealthSnapshot(
            status=status,
            queue_healthy=queue_healthy,
            metrics_available=metrics_available,
            audit_available=audit_available,
            governance_available=governance_available,
            authorization_available=authorization_available,
            registry_available=registry_available,
            queued_count=queued_count,
            running_count=running_count,
            output_pending_count=output_pending_count,
            finalization_active_count=finalization_active_count,
            stale_finalization_count=stale_finalization_count,
            cleanup_pending_count=cleanup_pending_count,
            workspace_capacity_available=workspace_capacity_available,
            finalization_configuration_valid=self._finalization_configuration_valid,
            finalization_worker_ready=self._finalization_worker_ready,
            storage_available=storage_available,
            minimum_free_disk_available=minimum_free_disk_available,
        )


def _safe_availability(check: Callable[[], bool]) -> bool:
    try:
        return bool(check())
    except Exception:
        return False
