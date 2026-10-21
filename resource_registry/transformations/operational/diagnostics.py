from __future__ import annotations

from collections.abc import Callable

from django.db import models
from django.db.models import Count
from django.db.models.query import QuerySet

from resource_registry.models import (
    TransformationRun,
    TransformationRunOutput,
    TransformationRunStepAttempt,
    TransformationWorkspaceLease,
)
from resource_registry.transformations.operational.authorization import (
    TransformationAuthorizationService,
)
from resource_registry.transformations.operational.contracts import (
    TransformationAuthorizationContext,
    TransformationOperationalDiagnosticsSnapshot,
    TransformationOperationalReadinessReport,
)
from resource_registry.transformations.providers import TransformationProviderRegistry


class TransformationOperationalDiagnosticsService:
    """Read sanitized aggregate diagnostics from durable lifecycle rows only."""

    def __init__(
        self,
        *,
        authorization_service: TransformationAuthorizationService,
        provider_registry: TransformationProviderRegistry,
        readiness_report_provider: Callable[[], TransformationOperationalReadinessReport]
        | None = None,
    ) -> None:
        self._authorization_service = authorization_service
        self._provider_registry = provider_registry
        self._readiness_report_provider = readiness_report_provider

    def get_diagnostics(
        self,
        *,
        context: TransformationAuthorizationContext,
    ) -> TransformationOperationalDiagnosticsSnapshot:
        """Return tenant-scoped counts without reading plans, checkpoints, or payloads."""
        self._authorization_service.authorize_diagnostics(context=context)
        runs = TransformationRun.objects.filter(tenant_id=context.tenant_id)
        steps = TransformationRunStepAttempt.objects.filter(
            run__tenant_id=context.tenant_id,
        )
        outputs = TransformationRunOutput.objects.filter(run__tenant_id=context.tenant_id)
        leases = TransformationWorkspaceLease.objects.filter(tenant_id=context.tenant_id)
        readiness_report = (
            self._readiness_report_provider()
            if self._readiness_report_provider is not None
            else None
        )
        startup_validation = (
            {check.check_key: check.status.value for check in readiness_report.startup_validation}
            if readiness_report is not None
            else {}
        )
        configuration_keys = {
            "audit_configuration",
            "configuration_checksum",
            "health_configuration",
            "governance_configuration",
            "mandatory_limits",
            "metrics_configuration",
            "provider_activation",
            "provider_catalog",
            "registry_checksum",
            "runtime_checksum",
            "workspace_quota",
        }
        return TransformationOperationalDiagnosticsSnapshot(
            tenant_id=context.tenant_id,
            runs_by_status=_count_by(runs, field_name="status"),
            steps_by_status=_count_by(steps, field_name="status"),
            outputs_by_status=_count_by(outputs, field_name="status"),
            failures_by_code=_count_by(
                runs.exclude(error_code__isnull=True),
                field_name="error_code",
            ),
            finalization_claims_by_state={
                "active": runs.filter(current_finalization_token__isnull=False).count(),
                "unclaimed": runs.filter(
                    status="OUTPUT_PENDING",
                    current_finalization_token__isnull=True,
                ).count(),
            },
            workspace_leases_by_state=_count_by(leases, field_name="state"),
            startup_validation=startup_validation,
            readiness_failures=(
                readiness_report.blocking_failures if readiness_report is not None else ()
            ),
            configuration_validation={
                key: value for key, value in startup_validation.items() if key in configuration_keys
            },
            operational_warnings=(
                readiness_report.known_warnings if readiness_report is not None else ()
            ),
        )


def _count_by(queryset: QuerySet[models.Model], *, field_name: str) -> dict[str, int]:
    rows = queryset.values(field_name).annotate(total=Count("id")).order_by(field_name)
    return {str(row[field_name] or "unspecified"): int(row["total"]) for row in rows}
