from __future__ import annotations

from collections.abc import Callable

from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from resource_registry.application.transformation_runtime.contracts import (
    RuntimeConfiguration,
    RuntimeIdentity,
)
from resource_registry.events.audit_service import AuditService
from resource_registry.transformations.execution_plan import EXECUTION_PLAN_CONTRACT_VERSION
from resource_registry.transformations.operational.authorization import (
    TransformationAuthorizationService,
)
from resource_registry.transformations.operational.contracts import (
    TransformationOperationalHealthStatus,
    TransformationOperationalReadinessReport,
    TransformationReadinessCheck,
    TransformationReadinessCheckStatus,
)
from resource_registry.transformations.operational.metrics import (
    TransformationOperationalMetricsPort,
)
from resource_registry.transformations.providers import (
    TransformationProviderRegistry,
    TransformationProviderRegistryState,
)

_SHA256_HEX_LENGTH = 64
_MIGRATION_APP_LABEL = "resource_registry"


class TransformationRuntimeReadinessValidator:
    """Validate production startup dependencies without exposing sensitive configuration."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        configuration: RuntimeConfiguration,
        identity: RuntimeIdentity,
        provider_registry: TransformationProviderRegistry,
        authorization_service: TransformationAuthorizationService,
        metrics: TransformationOperationalMetricsPort,
        audit_service: AuditService,
        storage_availability_check: Callable[[], bool],
        governance_configuration_check: Callable[[], bool],
        database_availability_check: Callable[[], bool] | None = None,
        migration_state_provider: Callable[[], tuple[str, bool]] | None = None,
    ) -> None:
        self._configuration = configuration
        self._identity = identity
        self._provider_registry = provider_registry
        self._authorization_service = authorization_service
        self._metrics = metrics
        self._audit_service = audit_service
        self._storage_availability_check = storage_availability_check
        self._governance_configuration_check = governance_configuration_check
        self._database_availability_check = database_availability_check or _is_database_available
        self._migration_state_provider = migration_state_provider or _get_migration_state

    def validate(
        self,
        *,
        expected_runtime_checksum: str | None = None,
        expected_registry_checksum: str | None = None,
    ) -> TransformationOperationalReadinessReport:
        """Return a deterministic fail-closed deployment-readiness report."""
        migration_version, migrations_current = self._safe_migration_state()
        activated_providers = tuple(
            f"{identity.provider_key}@{identity.provider_version}"
            for identity in self._provider_registry.list_provider_identities()
        )
        configured_providers = tuple(
            f"{provider.provider_key}@{provider.provider_version}"
            for provider in self._configuration.enabled_providers
        )
        checks = (
            self._check(
                "authorization_configuration",
                self._safe_check(self._authorization_service.is_available),
            ),
            self._observability_check(
                "audit_configuration",
                self._safe_check(self._audit_service.is_available),
            ),
            self._check(
                "configuration_checksum", _is_sha256(self._identity.configuration_checksum)
            ),
            self._check(
                "database_connectivity", self._safe_check(self._database_availability_check)
            ),
            self._check(
                "governance_configuration",
                self._safe_check(self._governance_configuration_check),
            ),
            self._check("health_configuration", self._configuration.health.require_provider),
            self._check("mandatory_limits", self._has_mandatory_limits()),
            self._check("migration_level", migrations_current),
            self._observability_check(
                "metrics_configuration",
                self._safe_check(self._metrics.is_available),
            ),
            self._check(
                "provider_activation",
                bool(activated_providers) and activated_providers == configured_providers,
            ),
            self._check("provider_catalog", bool(activated_providers)),
            self._check(
                "provider_registry",
                self._provider_registry.state == TransformationProviderRegistryState.FROZEN,
            ),
            self._check(
                "registry_checksum",
                self._checksum_matches(
                    actual=self._identity.registry_manifest_checksum,
                    expected=(
                        expected_registry_checksum
                        or self._configuration.expected_registry_manifest_checksum
                    ),
                ),
            ),
            self._check(
                "runtime_checksum",
                self._checksum_matches(
                    actual=self._identity.runtime_checksum,
                    expected=expected_runtime_checksum,
                ),
            ),
            self._check("storage_connectivity", self._safe_check(self._storage_availability_check)),
            self._check("workspace_quota", self._has_valid_workspace_quota()),
        )
        ordered_checks = tuple(sorted(checks))
        failures = tuple(
            check.code
            for check in ordered_checks
            if check.status == TransformationReadinessCheckStatus.FAILED
        )
        warnings = tuple(
            check.code
            for check in ordered_checks
            if check.status == TransformationReadinessCheckStatus.WARNING
        )
        status = TransformationOperationalHealthStatus.READY
        if failures:
            status = TransformationOperationalHealthStatus.NOT_READY
        elif warnings:
            status = TransformationOperationalHealthStatus.DEGRADED
        return TransformationOperationalReadinessReport(
            runtime_version=self._identity.runtime_contract_version,
            compiler_version=f"execution-plan/{EXECUTION_PLAN_CONTRACT_VERSION}",
            provider_versions=activated_providers,
            registry_checksum=self._identity.registry_manifest_checksum,
            configuration_checksum=self._identity.configuration_checksum,
            runtime_checksum=self._identity.runtime_checksum,
            migration_version=migration_version,
            operational_status=status,
            health_status=status,
            startup_validation=ordered_checks,
            known_warnings=warnings,
            blocking_failures=failures,
        )

    def _safe_migration_state(self) -> tuple[str, bool]:
        try:
            return self._migration_state_provider()
        except Exception:
            return "unavailable", False

    def _has_mandatory_limits(self) -> bool:
        limits = self._configuration.operational_limits
        return all(
            value > 0
            for value in (
                limits.maximum_input_bytes,
                limits.maximum_output_bytes,
                limits.maximum_row_count,
                limits.maximum_source_columns,
                limits.maximum_selected_columns,
                limits.provider_timeout_seconds,
                limits.materialization_timeout_seconds,
                limits.workspace_quota_bytes,
                limits.workspace_maximum_bytes,
                limits.minimum_free_disk_bytes,
            )
        )

    def _has_valid_workspace_quota(self) -> bool:
        limits = self._configuration.operational_limits
        return (
            limits.workspace_maximum_bytes <= limits.workspace_quota_bytes
            and limits.workspace_maximum_bytes <= limits.maximum_output_bytes
        )

    def _observability_check(self, check_key: str, passed: bool) -> TransformationReadinessCheck:
        if passed:
            return self._check(check_key, True)
        if self._configuration.health.allow_degraded_observability:
            return TransformationReadinessCheck(
                check_key=check_key,
                status=TransformationReadinessCheckStatus.WARNING,
                code=f"{check_key}_unavailable",
            )
        return self._check(check_key, False)

    @staticmethod
    def _check(check_key: str, passed: bool) -> TransformationReadinessCheck:
        return TransformationReadinessCheck(
            check_key=check_key,
            status=(
                TransformationReadinessCheckStatus.PASSED
                if passed
                else TransformationReadinessCheckStatus.FAILED
            ),
            code=f"{check_key}_{'passed' if passed else 'failed'}",
        )

    @staticmethod
    def _safe_check(check: Callable[[], bool]) -> bool:
        try:
            return bool(check())
        except Exception:
            return False

    @staticmethod
    def _checksum_matches(*, actual: str, expected: str | None) -> bool:
        return _is_sha256(actual) and (expected is None or actual == expected)


def _is_sha256(value: str) -> bool:
    return len(value) == _SHA256_HEX_LENGTH and all(
        character in "0123456789abcdef" for character in value
    )


def _is_database_available() -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        row = cursor.fetchone()
    return bool(row == (1,))


def _get_migration_state() -> tuple[str, bool]:
    executor = MigrationExecutor(connection)
    leaf_nodes = tuple(
        sorted(
            node
            for node in executor.loader.graph.leaf_nodes(_MIGRATION_APP_LABEL)
            if node[0] == _MIGRATION_APP_LABEL
        ),
    )
    if not leaf_nodes:
        return "unavailable", False
    latest_version = leaf_nodes[-1][1]
    migrations_current = all(node in executor.loader.applied_migrations for node in leaf_nodes)
    return latest_version, migrations_current
