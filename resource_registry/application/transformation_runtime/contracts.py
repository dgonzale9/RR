from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from resource_registry.events.audit_service import AuditService
from resource_registry.services.transformation_output_finalization_coordinator import (
    TransformationOutputFinalizationCoordinator,
)
from resource_registry.services.transformation_output_finalization_job_service import (
    TransformationOutputFinalizationJobService,
)
from resource_registry.services.transformation_output_finalization_worker_runner import (
    TransformationOutputFinalizationWorkerRunner,
)
from resource_registry.services.transformation_run_execution_coordinator import (
    TransformationRunExecutionCoordinator,
)
from resource_registry.services.transformation_run_job_service import TransformationRunJobService
from resource_registry.services.transformation_run_worker_runner import TransformationRunWorkerRunner
from resource_registry.services.transformation_workspace_lease_manager import (
    TransformationWorkspaceLeaseManager,
)
from resource_registry.transformations.execution.service import TransformationRunExecutionService
from resource_registry.transformations.execution_plan import TransformationExecutionPlanCompiler
from resource_registry.transformations.operational.authorization import (
    TransformationAuthorizationService,
)
from resource_registry.transformations.operational.contracts import (
    TransformationOperationalReadinessReport,
)
from resource_registry.transformations.operational.creation import (
    AuthorizedTransformationRunCreationService,
)
from resource_registry.transformations.operational.diagnostics import (
    TransformationOperationalDiagnosticsService,
)
from resource_registry.transformations.operational.governance import (
    TransformationSecurityPolicyResolver,
)
from resource_registry.transformations.operational.health import (
    TransformationOperationalHealthService,
)
from resource_registry.transformations.operational.metrics import (
    TransformationOperationalMetricsPort,
)
from resource_registry.transformations.output_finalization import (
    TransformationRunOutputFinalizationService,
)
from resource_registry.transformations.providers import (
    TransformationProviderIdentity,
    TransformationProviderRegistry,
)

RUNTIME_CONTRACT_VERSION = "transformation-runtime/1.0"
_SHA256_HEX_LENGTH = 64


class TransformationRuntimeLifecycleStatus(StrEnum):
    """Identify one bootstrap or shutdown lifecycle status."""

    STARTING = "STARTING"
    READY = "READY"
    DEGRADED = "DEGRADED"
    NOT_READY = "NOT_READY"
    STOPPED = "STOPPED"


@dataclass(frozen=True, order=True)
class EnabledTransformationProvider:
    """Select one exact provider version from the explicit source-code catalog."""

    provider_key: str
    provider_version: str

    @property
    def identity(self) -> TransformationProviderIdentity:
        """Return the exact validated provider identity."""
        return TransformationProviderIdentity(self.provider_key, self.provider_version)

    def to_dict(self) -> dict[str, str]:
        """Return the canonical configuration representation."""
        return {
            "provider_key": self.provider_key,
            "provider_version": self.provider_version,
        }


@dataclass(frozen=True)
class TransformationWorkerRuntimeConfiguration:
    """Contain deterministic worker execution and recovery configuration."""

    poll_interval_seconds: float
    max_retries: int
    lease_seconds: int
    heartbeat_seconds: int
    execution_timeout_seconds: float
    recovery_batch_size: int


@dataclass(frozen=True)
class TransformationOperationalLimitsConfiguration:
    """Contain mandatory execution-affecting provider and workspace limits."""

    maximum_input_bytes: int
    maximum_output_bytes: int
    maximum_row_count: int
    maximum_source_columns: int
    maximum_selected_columns: int
    provider_timeout_seconds: float
    materialization_timeout_seconds: float
    workspace_quota_bytes: int
    workspace_maximum_bytes: int
    minimum_free_disk_bytes: int

    def __post_init__(self) -> None:
        """Fail closed unless every production limit is explicitly positive."""
        values = (
            self.maximum_input_bytes,
            self.maximum_output_bytes,
            self.maximum_row_count,
            self.maximum_source_columns,
            self.maximum_selected_columns,
            self.provider_timeout_seconds,
            self.materialization_timeout_seconds,
            self.workspace_quota_bytes,
            self.workspace_maximum_bytes,
            self.minimum_free_disk_bytes,
        )
        if any(value <= 0 for value in values):
            raise ValueError("Transformation operational limits must be explicitly positive.")
        if self.workspace_maximum_bytes > self.workspace_quota_bytes:
            raise ValueError("Workspace object maximum cannot exceed the workspace quota.")
        if self.workspace_maximum_bytes > self.maximum_output_bytes:
            raise ValueError("Workspace object maximum cannot exceed provider output bytes.")


@dataclass(frozen=True)
class TransformationOutputFinalizationRuntimeConfiguration:
    """Contain durable output-finalization worker limits."""

    poll_interval_seconds: float
    max_retries: int
    lease_seconds: int
    heartbeat_seconds: int
    timeout_seconds: float
    recovery_batch_size: int
    per_process_concurrency: int
    per_tenant_concurrency: int

    def __post_init__(self) -> None:
        """Validate the supported one-at-a-time finalization worker contract."""
        if self.poll_interval_seconds <= 0 or self.timeout_seconds <= 0:
            raise ValueError("Finalization poll interval and timeout must be positive.")
        if self.max_retries < 0:
            raise ValueError("Finalization retry limit cannot be negative.")
        if self.lease_seconds <= 0 or self.heartbeat_seconds <= 0:
            raise ValueError("Finalization lease and heartbeat must be positive.")
        if self.heartbeat_seconds >= self.lease_seconds:
            raise ValueError("Finalization heartbeat must be shorter than its lease.")
        if self.recovery_batch_size <= 0 or self.per_tenant_concurrency <= 0:
            raise ValueError("Finalization recovery and tenant concurrency must be positive.")
        if self.per_process_concurrency != 1:
            raise ValueError("The durable finalization runner supports process concurrency one.")


@dataclass(frozen=True)
class TransformationMetricsRuntimeConfiguration:
    """Select the logical transformation metrics adapter."""

    adapter_key: str = "production"


@dataclass(frozen=True)
class TransformationAuditRuntimeConfiguration:
    """Select the logical transformation audit publisher."""

    publisher_key: str = "production"


@dataclass(frozen=True)
class TransformationHealthRuntimeConfiguration:
    """Configure process-level readiness expectations."""

    require_provider: bool = True
    allow_degraded_observability: bool = True


@dataclass(frozen=True)
class RuntimeConfiguration:
    """Contain normalized immutable transformation runtime configuration."""

    enabled_providers: tuple[EnabledTransformationProvider, ...]
    worker: TransformationWorkerRuntimeConfiguration
    operational_limits: TransformationOperationalLimitsConfiguration
    output_finalization: TransformationOutputFinalizationRuntimeConfiguration
    metrics: TransformationMetricsRuntimeConfiguration = field(
        default_factory=TransformationMetricsRuntimeConfiguration,
    )
    audit: TransformationAuditRuntimeConfiguration = field(
        default_factory=TransformationAuditRuntimeConfiguration,
    )
    health: TransformationHealthRuntimeConfiguration = field(
        default_factory=TransformationHealthRuntimeConfiguration,
    )
    expected_registry_manifest_checksum: str | None = None

    def __post_init__(self) -> None:
        """Normalize provider ordering and validate portable runtime settings."""
        providers = tuple(sorted(self.enabled_providers))
        if len(set(providers)) != len(providers):
            raise ValueError("Enabled transformation provider identities must be unique.")
        for provider in providers:
            _ = provider.identity
        object.__setattr__(self, "enabled_providers", providers)
        if self.worker.poll_interval_seconds <= 0:
            raise ValueError("Worker poll interval must be positive.")
        if self.worker.max_retries < 0:
            raise ValueError("Worker retry limit cannot be negative.")
        if self.worker.lease_seconds <= 0 or self.worker.heartbeat_seconds <= 0:
            raise ValueError("Worker lease and heartbeat values must be positive.")
        if self.worker.heartbeat_seconds >= self.worker.lease_seconds:
            raise ValueError("Worker heartbeat must be shorter than its lease.")
        if self.worker.execution_timeout_seconds < 0:
            raise ValueError("Worker execution timeout cannot be negative.")
        if self.worker.recovery_batch_size <= 0:
            raise ValueError("Worker recovery batch size must be positive.")
        if self.metrics.adapter_key != "production":
            raise ValueError("Unsupported transformation metrics adapter.")
        if self.audit.publisher_key != "production":
            raise ValueError("Unsupported transformation audit publisher.")
        expected_checksum = self.expected_registry_manifest_checksum
        if expected_checksum is not None and (
            len(expected_checksum) != _SHA256_HEX_LENGTH
            or any(character not in "0123456789abcdef" for character in expected_checksum)
        ):
            raise ValueError("Expected registry manifest checksum must be lowercase SHA-256.")

    def to_checksum_dict(self) -> dict[str, object]:
        """Return only normalized identity-bearing, non-secret configuration."""
        return {
            "enabled_providers": [provider.to_dict() for provider in self.enabled_providers],
            "worker": {
                "poll_interval_seconds": self.worker.poll_interval_seconds,
                "max_retries": self.worker.max_retries,
                "lease_seconds": self.worker.lease_seconds,
                "heartbeat_seconds": self.worker.heartbeat_seconds,
                "execution_timeout_seconds": self.worker.execution_timeout_seconds,
                "recovery_batch_size": self.worker.recovery_batch_size,
            },
            "operational_limits": {
                "maximum_input_bytes": self.operational_limits.maximum_input_bytes,
                "maximum_output_bytes": self.operational_limits.maximum_output_bytes,
                "maximum_row_count": self.operational_limits.maximum_row_count,
                "maximum_source_columns": self.operational_limits.maximum_source_columns,
                "maximum_selected_columns": self.operational_limits.maximum_selected_columns,
                "provider_timeout_seconds": self.operational_limits.provider_timeout_seconds,
                "materialization_timeout_seconds": (
                    self.operational_limits.materialization_timeout_seconds
                ),
                "workspace_quota_bytes": self.operational_limits.workspace_quota_bytes,
                "workspace_maximum_bytes": self.operational_limits.workspace_maximum_bytes,
                "minimum_free_disk_bytes": self.operational_limits.minimum_free_disk_bytes,
            },
            "output_finalization": {
                "poll_interval_seconds": self.output_finalization.poll_interval_seconds,
                "max_retries": self.output_finalization.max_retries,
                "lease_seconds": self.output_finalization.lease_seconds,
                "heartbeat_seconds": self.output_finalization.heartbeat_seconds,
                "timeout_seconds": self.output_finalization.timeout_seconds,
                "recovery_batch_size": self.output_finalization.recovery_batch_size,
                "per_process_concurrency": self.output_finalization.per_process_concurrency,
                "per_tenant_concurrency": self.output_finalization.per_tenant_concurrency,
            },
            "metrics": {"adapter_key": self.metrics.adapter_key},
            "audit": {"publisher_key": self.audit.publisher_key},
            "health": {
                "require_provider": self.health.require_provider,
                "allow_degraded_observability": self.health.allow_degraded_observability,
            },
            "expected_registry_manifest_checksum": (self.expected_registry_manifest_checksum),
        }


@dataclass(frozen=True)
class RuntimeIdentity:
    """Identify one deterministic transformation runtime composition."""

    runtime_contract_version: str
    registry_manifest_checksum: str
    configuration_checksum: str
    runtime_checksum: str


class TransformationRuntimeReadinessValidatorPort(Protocol):
    """Validate one composed runtime for production deployment."""

    def validate(
        self,
        *,
        expected_runtime_checksum: str | None = None,
        expected_registry_checksum: str | None = None,
    ) -> TransformationOperationalReadinessReport:
        """Return one deterministic sanitized readiness report."""


@dataclass(frozen=True)
class TransformationRuntime:
    """Expose one immutable, fully composed transformation runtime graph."""

    configuration: RuntimeConfiguration
    provider_registry: TransformationProviderRegistry
    execution_plan_compiler: TransformationExecutionPlanCompiler
    execution_engine: TransformationRunExecutionService
    output_finalization_service: TransformationRunOutputFinalizationService
    authorization_service: TransformationAuthorizationService
    authorized_creation_service: AuthorizedTransformationRunCreationService
    security_policy_resolver: TransformationSecurityPolicyResolver
    job_service: TransformationRunJobService
    execution_coordinator: TransformationRunExecutionCoordinator
    worker_runner: TransformationRunWorkerRunner
    output_finalization_job_service: TransformationOutputFinalizationJobService
    output_finalization_coordinator: TransformationOutputFinalizationCoordinator
    output_finalization_worker_runner: TransformationOutputFinalizationWorkerRunner
    workspace_lease_manager: TransformationWorkspaceLeaseManager
    metrics: TransformationOperationalMetricsPort
    audit_service: AuditService
    health_service: TransformationOperationalHealthService
    diagnostics_service: TransformationOperationalDiagnosticsService
    readiness_validator: TransformationRuntimeReadinessValidatorPort
    identity: RuntimeIdentity


@dataclass(frozen=True)
class RuntimeBootstrapResult:
    """Return the terminal result of one runtime bootstrap attempt."""

    status: TransformationRuntimeLifecycleStatus
    runtime: TransformationRuntime | None
    errors: tuple[str, ...] = ()
    has_reached_ready: bool = False
    lifecycle: tuple[TransformationRuntimeLifecycleStatus, ...] = (
        TransformationRuntimeLifecycleStatus.STARTING,
    )


@dataclass(frozen=True)
class RuntimeShutdownResult:
    """Return the best-effort result of an orderly runtime shutdown."""

    status: TransformationRuntimeLifecycleStatus
    errors: tuple[str, ...] = ()


class RuntimeBootstrapException(RuntimeError):  # noqa: N818 - ADR-approved contract name
    """Raise when runtime bootstrap cannot safely produce a ready graph."""
