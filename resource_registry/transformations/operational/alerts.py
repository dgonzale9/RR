from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class TransformationOperationalAlertSeverity(StrEnum):
    """Classify operator response urgency."""

    CRITICAL = "CRITICAL"
    WARNING = "WARNING"


@dataclass(frozen=True, order=True)
class TransformationOperationalAlertDefinition:
    """Define one vendor-neutral transformation operational alert."""

    alert_key: str
    severity: TransformationOperationalAlertSeverity
    trigger: str
    operator_action: str


_ALERT_DEFINITIONS = (
    TransformationOperationalAlertDefinition(
        "checksum_mismatch",
        TransformationOperationalAlertSeverity.CRITICAL,
        "Any configured runtime or registry checksum differs from the composed identity.",
        "Stop rollout and restore the approved configuration or provider manifest.",
    ),
    TransformationOperationalAlertDefinition(
        "cleanup_backlog",
        TransformationOperationalAlertSeverity.WARNING,
        "Workspace cleanup backlog remains above the deployment threshold.",
        "Run cleanup recovery and investigate storage deletion failures.",
    ),
    TransformationOperationalAlertDefinition(
        "governance_failure",
        TransformationOperationalAlertSeverity.CRITICAL,
        "Required tenant governance cannot be resolved.",
        "Restore an active validated tenant policy before enabling execution.",
    ),
    TransformationOperationalAlertDefinition(
        "migration_mismatch",
        TransformationOperationalAlertSeverity.CRITICAL,
        "The resource_registry migration leaf is not applied.",
        "Stop workers and apply the approved migration set before restart.",
    ),
    TransformationOperationalAlertDefinition(
        "provider_failures",
        TransformationOperationalAlertSeverity.WARNING,
        "Provider failures exceed the deployment error-rate threshold.",
        "Inspect sanitized failure codes and verify provider and input integrity.",
    ),
    TransformationOperationalAlertDefinition(
        "retry_exhaustion",
        TransformationOperationalAlertSeverity.WARNING,
        "Transformation execution or finalization retries are exhausted.",
        "Inspect durable attempts and correct the stable failure before replay.",
    ),
    TransformationOperationalAlertDefinition(
        "runtime_not_ready",
        TransformationOperationalAlertSeverity.CRITICAL,
        "Transformation runtime health reports NOT_READY.",
        "Keep polling disabled and resolve every blocking readiness failure.",
    ),
    TransformationOperationalAlertDefinition(
        "stale_claims",
        TransformationOperationalAlertSeverity.WARNING,
        "Expired execution or finalization claims remain recoverable.",
        "Run deterministic stale recovery and verify worker health.",
    ),
    TransformationOperationalAlertDefinition(
        "storage_unavailable",
        TransformationOperationalAlertSeverity.CRITICAL,
        "The configured storage provider connectivity check fails.",
        "Restore storage configuration and connectivity before worker startup.",
    ),
    TransformationOperationalAlertDefinition(
        "workspace_quota_exceeded",
        TransformationOperationalAlertSeverity.WARNING,
        "Workspace reservations reach the configured tenant quota.",
        "Clear eligible workspaces or revise the approved capacity configuration.",
    ),
)


class TransformationOperationalAlertCatalog:
    """Expose immutable vendor-neutral alert specifications."""

    def list_definitions(self) -> tuple[TransformationOperationalAlertDefinition, ...]:
        """Return alert definitions in deterministic key order."""
        return tuple(sorted(_ALERT_DEFINITIONS))
