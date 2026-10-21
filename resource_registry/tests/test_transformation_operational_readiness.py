from __future__ import annotations

import json
import uuid
from dataclasses import FrozenInstanceError
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.test import override_settings

from resource_registry.application.transformation_runtime import (
    RuntimeBootstrapResult,
    RuntimeCompositionRoot,
    TransformationRuntime,
    TransformationRuntimeLifecycleStatus,
)
from resource_registry.application.transformation_runtime.configuration import (
    RuntimeConfigurationLoader,
)
from resource_registry.application.transformation_runtime.readiness import (
    TransformationRuntimeReadinessValidator,
)
from resource_registry.tests.test_transformation_runtime_composition import _configuration
from resource_registry.transformations.operational import (
    TransformationAuthorizationContext,
    TransformationOperationalAlertCatalog,
    TransformationOperationalHealthStatus,
    TransformationPrincipalKind,
    TransformationReadinessCheckStatus,
)
from resource_registry.transformations.operational.authorization import (
    VIEW_TRANSFORMATION_DIAGNOSTICS_PERMISSION,
)
from resource_registry.transformations.providers.builtin.local_duckdb_tabular import (
    PROVIDER_IDENTITY,
)

pytestmark = pytest.mark.django_db(transaction=True)

_LATEST_MIGRATION = "0021_transformationworkspacelease_and_more"
_SHA256_HEX_LENGTH = 64


def _build_runtime() -> TransformationRuntime:
    return RuntimeCompositionRoot().bootstrap_or_raise(
        configuration=_configuration(PROVIDER_IDENTITY),
        worker_id="readiness-test",
    )


def _build_validator(
    runtime: TransformationRuntime,
    *,
    storage_available: bool = True,
    database_available: bool = True,
    migrations_current: bool = True,
) -> TransformationRuntimeReadinessValidator:
    return TransformationRuntimeReadinessValidator(
        configuration=runtime.configuration,
        identity=runtime.identity,
        provider_registry=runtime.provider_registry,
        authorization_service=runtime.authorization_service,
        metrics=runtime.metrics,
        audit_service=runtime.audit_service,
        storage_availability_check=lambda: storage_available,
        governance_configuration_check=lambda: True,
        database_availability_check=lambda: database_available,
        migration_state_provider=lambda: (_LATEST_MIGRATION, migrations_current),
    )


def test_startup_validation_returns_deterministic_sanitized_ready_report() -> None:
    runtime = _build_runtime()
    validator = _build_validator(runtime)

    first = validator.validate()
    second = validator.validate()

    assert first == second
    assert first.operational_status == TransformationOperationalHealthStatus.READY
    assert first.health_status == TransformationOperationalHealthStatus.READY
    assert first.provider_versions == ("local_duckdb_tabular@1.0.0",)
    assert first.migration_version == _LATEST_MIGRATION
    assert not first.blocking_failures
    assert all(
        check.status == TransformationReadinessCheckStatus.PASSED
        for check in first.startup_validation
    )
    serialized = first.to_dict()
    assert "configuration_checksum" in serialized
    assert not {"secret", "path", "plan", "checkpoint", "payload"}.intersection(serialized)
    with pytest.raises(FrozenInstanceError):
        first.runtime_checksum = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    (
        "storage_available",
        "database_available",
        "migrations_current",
        "expected_failure",
    ),
    (
        (False, True, True, "storage_connectivity_failed"),
        (True, False, True, "database_connectivity_failed"),
        (True, True, False, "migration_level_failed"),
    ),
)
def test_startup_validation_fails_closed_on_blocking_dependencies(
    storage_available: bool,
    database_available: bool,
    migrations_current: bool,
    expected_failure: str,
) -> None:
    report = _build_validator(
        _build_runtime(),
        storage_available=storage_available,
        database_available=database_available,
        migrations_current=migrations_current,
    ).validate()

    assert report.operational_status == TransformationOperationalHealthStatus.NOT_READY
    assert expected_failure in report.blocking_failures


def test_deployment_validation_rejects_unapproved_runtime_and_registry_checksums() -> None:
    report = _build_validator(_build_runtime()).validate(
        expected_runtime_checksum="0" * _SHA256_HEX_LENGTH,
        expected_registry_checksum="0" * _SHA256_HEX_LENGTH,
    )

    assert report.operational_status == TransformationOperationalHealthStatus.NOT_READY
    assert report.blocking_failures == (
        "registry_checksum_failed",
        "runtime_checksum_failed",
    )


def test_deployment_command_emits_sanitized_machine_readable_report() -> None:
    runtime = _build_runtime()
    bootstrap = RuntimeBootstrapResult(
        status=TransformationRuntimeLifecycleStatus.READY,
        runtime=runtime,
        has_reached_ready=True,
    )
    stdout = StringIO()
    with patch(
        "resource_registry.management.commands.validate_transformation_runtime."
        "RuntimeCompositionRoot",
    ) as composition_root:
        composition_root.return_value.bootstrap.return_value = bootstrap
        call_command("validate_transformation_runtime", stdout=stdout)

    report = json.loads(stdout.getvalue())
    assert report["operational_status"] == "READY"
    assert report["provider_versions"] == ["local_duckdb_tabular@1.0.0"]
    assert "blocking_failures" in report
    assert "secret" not in stdout.getvalue().lower()


def test_missing_production_configuration_fails_before_runtime_composition() -> None:
    with override_settings(RESOURCE_REGISTRY_TRANSFORMATION_MAX_INPUT_BYTES=None):
        with pytest.raises(ValueError, match="absent"):
            RuntimeConfigurationLoader().load()


def test_diagnostics_exposes_only_sanitized_readiness_aggregates() -> None:
    runtime = _build_runtime()
    context = TransformationAuthorizationContext(
        tenant_id=str(uuid.uuid4()),
        actor="service:operations",
        principal_kind=TransformationPrincipalKind.SERVICE,
        permissions=frozenset({VIEW_TRANSFORMATION_DIAGNOSTICS_PERMISSION}),
        correlation_id="readiness-diagnostics",
    )

    diagnostics = runtime.diagnostics_service.get_diagnostics(context=context)

    assert diagnostics.startup_validation["database_connectivity"] == "PASSED"
    assert diagnostics.configuration_validation["mandatory_limits"] == "PASSED"
    assert diagnostics.readiness_failures == ()
    assert diagnostics.operational_warnings == ()


def test_alert_catalog_is_complete_deterministic_and_immutable() -> None:
    definitions = TransformationOperationalAlertCatalog().list_definitions()

    assert definitions == tuple(sorted(definitions))
    assert {definition.alert_key for definition in definitions} == {
        "checksum_mismatch",
        "cleanup_backlog",
        "governance_failure",
        "migration_mismatch",
        "provider_failures",
        "retry_exhaustion",
        "runtime_not_ready",
        "stale_claims",
        "storage_unavailable",
        "workspace_quota_exceeded",
    }
    assert all(definition.trigger and definition.operator_action for definition in definitions)
