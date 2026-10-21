from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from resource_registry.read_auth import ReadAuthorizationContext
from resource_registry.transformations.binding.immutable_json import freeze_json_value

TRANSFORMATION_AUTHORIZATION_CONTRACT_VERSION = "transformation-authorization/1.0"
TRANSFORMATION_AUTHORIZATION_POLICY_REVISION = 1


class TransformationPrincipalKind(StrEnum):
    """Identify the trusted principal category authorizing an operation."""

    USER = "USER"
    SERVICE = "SERVICE"


@dataclass(frozen=True)
class TransformationAuthorizationContext:
    """Carry trusted tenant, principal, permission, and resource scopes."""

    tenant_id: str
    actor: str
    principal_kind: TransformationPrincipalKind
    permissions: frozenset[str]
    correlation_id: str
    allowed_dataset_ids: frozenset[str] | None = None
    allowed_transformation_definition_ids: frozenset[str] | None = None

    @classmethod
    def from_read_context(  # noqa: PLR0913
        cls,
        *,
        context: ReadAuthorizationContext,
        actor: str,
        principal_kind: TransformationPrincipalKind,
        trusted_permissions: frozenset[str],
        correlation_id: str,
        allowed_transformation_definition_ids: frozenset[str] | None = None,
    ) -> TransformationAuthorizationContext:
        """Adapt existing read tenant and dataset scopes into transformation authorization."""
        return cls(
            tenant_id=context.tenant_id,
            actor=actor,
            principal_kind=principal_kind,
            permissions=trusted_permissions,
            correlation_id=correlation_id,
            allowed_dataset_ids=context.allowed_dataset_ids,
            allowed_transformation_definition_ids=allowed_transformation_definition_ids,
        )


@dataclass(frozen=True)
class TransformationAuthorizationDecision:
    """Represent one deterministic, sanitized authorization grant snapshot."""

    snapshot: Mapping[str, object]
    checksum: str
    authorization_policy_revision: int
    governance_policy_revision: int
    authorized_actor: str
    contract_version: str = TRANSFORMATION_AUTHORIZATION_CONTRACT_VERSION

    def __post_init__(self) -> None:
        """Recursively freeze the persisted authorization snapshot."""
        frozen = freeze_json_value(self.snapshot)
        if not isinstance(frozen, Mapping):
            raise TypeError("Authorization snapshot must be a mapping.")
        object.__setattr__(self, "snapshot", frozen)


@dataclass(frozen=True, order=True)
class TransformationProviderOperationAuthorization:
    """Identify one exactly authorized transformation provider operation."""

    provider_key: str
    provider_version: str
    operation_key: str
    operation_version: str

    def to_dict(self) -> dict[str, str]:
        """Return a canonical authorization representation."""
        return {
            "provider_key": self.provider_key,
            "provider_version": self.provider_version,
            "operation_key": self.operation_key,
            "operation_version": self.operation_version,
        }


@dataclass(frozen=True)
class ResolvedTransformationSecurityPolicy:
    """Contain the exact tenant and dataset governance intersection."""

    allowed_operations: frozenset[TransformationProviderOperationAuthorization]
    allowed_output_policy_kinds: frozenset[str]
    tenant_policy_revision: int
    policy_provenance: tuple[Mapping[str, object], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        """Freeze policy provenance mappings and ordered sequence."""
        frozen: list[Mapping[str, object]] = []
        for item in self.policy_provenance:
            value = freeze_json_value(item)
            if not isinstance(value, Mapping):
                raise TypeError("Policy provenance must contain mappings.")
            frozen.append(value)
        object.__setattr__(self, "policy_provenance", tuple(frozen))


class TransformationOperationalHealthStatus(StrEnum):
    """Classify transformation operational readiness."""

    READY = "READY"
    DEGRADED = "DEGRADED"
    NOT_READY = "NOT_READY"


class TransformationReadinessCheckStatus(StrEnum):
    """Classify one sanitized operational-readiness check result."""

    PASSED = "PASSED"
    WARNING = "WARNING"
    FAILED = "FAILED"


@dataclass(frozen=True, order=True)
class TransformationReadinessCheck:
    """Describe one deterministic operational-readiness check."""

    check_key: str
    status: TransformationReadinessCheckStatus
    code: str


@dataclass(frozen=True)
class TransformationOperationalReadinessReport:
    """Expose a deterministic, sanitized production-readiness report."""

    runtime_version: str
    compiler_version: str
    provider_versions: tuple[str, ...]
    registry_checksum: str
    configuration_checksum: str
    runtime_checksum: str
    migration_version: str
    operational_status: TransformationOperationalHealthStatus
    health_status: TransformationOperationalHealthStatus
    startup_validation: tuple[TransformationReadinessCheck, ...]
    known_warnings: tuple[str, ...]
    blocking_failures: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        """Return a stable JSON-compatible report without sensitive values."""
        return {
            "runtime_version": self.runtime_version,
            "compiler_version": self.compiler_version,
            "provider_versions": list(self.provider_versions),
            "registry_checksum": self.registry_checksum,
            "configuration_checksum": self.configuration_checksum,
            "runtime_checksum": self.runtime_checksum,
            "migration_version": self.migration_version,
            "operational_status": self.operational_status.value,
            "health_status": self.health_status.value,
            "startup_validation": [
                {
                    "check_key": check.check_key,
                    "status": check.status.value,
                    "code": check.code,
                }
                for check in self.startup_validation
            ],
            "known_warnings": list(self.known_warnings),
            "blocking_failures": list(self.blocking_failures),
        }


@dataclass(frozen=True)
class TransformationOperationalHealthSnapshot:
    """Expose sanitized transformation operational readiness."""

    status: TransformationOperationalHealthStatus
    queue_healthy: bool
    metrics_available: bool
    audit_available: bool
    governance_available: bool
    authorization_available: bool
    registry_available: bool
    queued_count: int
    running_count: int
    output_pending_count: int
    finalization_active_count: int
    stale_finalization_count: int
    cleanup_pending_count: int
    workspace_capacity_available: bool
    finalization_configuration_valid: bool
    finalization_worker_ready: bool
    storage_available: bool
    minimum_free_disk_available: bool


@dataclass(frozen=True)
class TransformationOperationalDiagnosticsSnapshot:
    """Expose tenant-scoped aggregate execution diagnostics without payloads."""

    tenant_id: str
    runs_by_status: Mapping[str, int]
    steps_by_status: Mapping[str, int]
    outputs_by_status: Mapping[str, int]
    failures_by_code: Mapping[str, int]
    finalization_claims_by_state: Mapping[str, int]
    workspace_leases_by_state: Mapping[str, int]
    startup_validation: Mapping[str, str] = field(default_factory=dict)
    readiness_failures: tuple[str, ...] = ()
    configuration_validation: Mapping[str, str] = field(default_factory=dict)
    operational_warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Recursively freeze diagnostic aggregate mappings."""
        for field_name in (
            "runs_by_status",
            "steps_by_status",
            "outputs_by_status",
            "failures_by_code",
            "finalization_claims_by_state",
            "workspace_leases_by_state",
            "startup_validation",
            "configuration_validation",
        ):
            value = freeze_json_value(getattr(self, field_name))
            object.__setattr__(self, field_name, value)
