from __future__ import annotations

import subprocess
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path
from threading import Event

import pytest
from django.test import override_settings

from resource_registry.application.transformation_runtime import (
    EnabledTransformationProvider,
    RuntimeCompositionRoot,
    RuntimeConfiguration,
    RuntimeShutdownCoordinator,
    TransformationHealthRuntimeConfiguration,
    TransformationOperationalLimitsConfiguration,
    TransformationOutputFinalizationRuntimeConfiguration,
    TransformationRuntimeLifecycleStatus,
    TransformationWorkerRuntimeConfiguration,
)
from resource_registry.application.transformation_runtime.configuration import (
    RuntimeConfigurationLoader,
)
from resource_registry.transformations.providers import (
    ResolvedTransformationOperationIdentity,
    TransformationOperationCapabilityDescriptor,
    TransformationProviderCapabilityDescriptor,
    TransformationProviderExecutionRequest,
    TransformationProviderExecutionResult,
    TransformationProviderIdentity,
    TransformationProviderRegistry,
    TransformationProviderRegistryState,
    TransformationProviderRuntimeContext,
    TransformationProviderValidationRequest,
    TransformationProviderValidationResult,
)
from resource_registry.transformations.providers.builtin.local_duckdb_tabular import (
    PROVIDER_IDENTITY,
)
from resource_registry.transformations.providers.exceptions import (
    TransformationProviderRegistryLifecycleError,
)

_SHA256_HEX_LENGTH = 64


class _RuntimeTestProvider:
    """Expose one deterministic provider descriptor without execution behavior."""

    def __init__(self, *, provider_key: str, provider_version: str) -> None:
        identity = TransformationProviderIdentity(provider_key, provider_version)
        operation_identity = ResolvedTransformationOperationIdentity(
            provider_key=provider_key,
            provider_version=provider_version,
            operation_key="transform",
            operation_version="1",
        )
        self._descriptor = TransformationProviderCapabilityDescriptor(
            identity=identity,
            operations=(
                TransformationOperationCapabilityDescriptor(
                    identity=operation_identity,
                    supported_input_formats=("PARQUET",),
                    supported_output_formats=("PARQUET",),
                    is_idempotent=True,
                    is_checkpoint_reuse_safe=True,
                    is_checkpoint_reconciliation_safe=False,
                    supports_cooperative_cancellation=True,
                ),
            ),
        )

    @property
    def provider_identity(self) -> TransformationProviderIdentity:
        """Return the exact test provider identity."""
        return self._descriptor.identity

    @property
    def capability_descriptor(self) -> TransformationProviderCapabilityDescriptor:
        """Return the immutable test capability descriptor."""
        return self._descriptor

    def validate_operation(
        self,
        *,
        request: TransformationProviderValidationRequest,
    ) -> TransformationProviderValidationResult:
        """Return a valid result if a composition regression invokes validation."""
        _ = request
        return TransformationProviderValidationResult(is_valid=True)

    def execute(
        self,
        *,
        request: TransformationProviderExecutionRequest,
        runtime_context: TransformationProviderRuntimeContext,
    ) -> TransformationProviderExecutionResult:
        """Reject use outside composition tests."""
        _ = request, runtime_context
        raise AssertionError("Runtime composition must not execute providers.")


def _configuration(
    *identities: TransformationProviderIdentity,
    require_provider: bool = True,
) -> RuntimeConfiguration:
    return RuntimeConfiguration(
        enabled_providers=tuple(
            EnabledTransformationProvider(
                provider_key=identity.provider_key,
                provider_version=identity.provider_version,
            )
            for identity in identities
        ),
        worker=TransformationWorkerRuntimeConfiguration(
            poll_interval_seconds=0.1,
            max_retries=3,
            lease_seconds=120,
            heartbeat_seconds=30,
            execution_timeout_seconds=0,
            recovery_batch_size=10,
        ),
        operational_limits=_operational_limits(),
        output_finalization=_finalization_configuration(),
        health=TransformationHealthRuntimeConfiguration(
            require_provider=require_provider,
            allow_degraded_observability=True,
        ),
    )


def _operational_limits() -> TransformationOperationalLimitsConfiguration:
    return TransformationOperationalLimitsConfiguration(
        maximum_input_bytes=10_000_000,
        maximum_output_bytes=10_000_000,
        maximum_row_count=100_000,
        maximum_source_columns=1_000,
        maximum_selected_columns=1_000,
        provider_timeout_seconds=60,
        materialization_timeout_seconds=60,
        workspace_quota_bytes=100_000_000,
        workspace_maximum_bytes=10_000_000,
        minimum_free_disk_bytes=1,
    )


def _finalization_configuration() -> TransformationOutputFinalizationRuntimeConfiguration:
    return TransformationOutputFinalizationRuntimeConfiguration(
        poll_interval_seconds=0.1,
        max_retries=3,
        lease_seconds=120,
        heartbeat_seconds=30,
        timeout_seconds=600,
        recovery_batch_size=10,
        per_process_concurrency=1,
        per_tenant_concurrency=2,
    )


def _required_runtime_settings() -> dict[str, int | float]:
    return {
        "RESOURCE_REGISTRY_TRANSFORMATION_MAX_INPUT_BYTES": 10_000_000,
        "RESOURCE_REGISTRY_TRANSFORMATION_MAX_OUTPUT_BYTES": 10_000_000,
        "RESOURCE_REGISTRY_TRANSFORMATION_MAX_ROW_COUNT": 100_000,
        "RESOURCE_REGISTRY_TRANSFORMATION_MAX_SOURCE_COLUMNS": 1_000,
        "RESOURCE_REGISTRY_TRANSFORMATION_MAX_SELECTED_COLUMNS": 1_000,
        "RESOURCE_REGISTRY_TRANSFORMATION_PROVIDER_TIMEOUT_SECONDS": 60,
        "RESOURCE_REGISTRY_TRANSFORMATION_MATERIALIZATION_TIMEOUT_SECONDS": 60,
        "RESOURCE_REGISTRY_TRANSFORMATION_WORKSPACE_QUOTA_BYTES": 100_000_000,
        "RESOURCE_REGISTRY_TRANSFORMATION_WORKSPACE_MAXIMUM_BYTES": 10_000_000,
        "RESOURCE_REGISTRY_TRANSFORMATION_MINIMUM_FREE_DISK_BYTES": 1,
        "RESOURCE_REGISTRY_TRANSFORMATION_FINALIZATION_POLL_INTERVAL_SECONDS": 0.1,
        "RESOURCE_REGISTRY_TRANSFORMATION_FINALIZATION_MAX_RETRIES": 3,
        "RESOURCE_REGISTRY_TRANSFORMATION_FINALIZATION_LEASE_SECONDS": 120,
        "RESOURCE_REGISTRY_TRANSFORMATION_FINALIZATION_HEARTBEAT_SECONDS": 30,
        "RESOURCE_REGISTRY_TRANSFORMATION_FINALIZATION_TIMEOUT_SECONDS": 600,
        "RESOURCE_REGISTRY_TRANSFORMATION_FINALIZATION_RECOVERY_BATCH_SIZE": 10,
        "RESOURCE_REGISTRY_TRANSFORMATION_FINALIZATION_PER_PROCESS_CONCURRENCY": 1,
        "RESOURCE_REGISTRY_TRANSFORMATION_FINALIZATION_PER_TENANT_CONCURRENCY": 2,
    }


def _composition_root(
    *providers: _RuntimeTestProvider,
) -> RuntimeCompositionRoot:
    return RuntimeCompositionRoot(
        provider_catalog={
            provider.provider_identity: (
                lambda _dependencies, active_provider=provider: active_provider
            )
            for provider in providers
        },
    )


def test_bootstrap_builds_one_immutable_graph_with_shared_frozen_registry() -> None:
    provider = _RuntimeTestProvider(provider_key="provider", provider_version="1")
    result = _composition_root(provider).bootstrap(
        configuration=_configuration(provider.provider_identity),
        worker_id="test-worker",
    )

    assert result.runtime is not None
    assert result.has_reached_ready is True
    assert result.status in {
        TransformationRuntimeLifecycleStatus.READY,
        TransformationRuntimeLifecycleStatus.DEGRADED,
    }
    assert result.lifecycle[:2] == (
        TransformationRuntimeLifecycleStatus.STARTING,
        TransformationRuntimeLifecycleStatus.READY,
    )
    runtime = result.runtime
    registry = runtime.provider_registry
    assert registry.state == TransformationProviderRegistryState.FROZEN
    assert runtime.execution_plan_compiler._provider_registry is registry
    assert runtime.execution_engine._provider_registry is registry
    assert runtime.authorization_service._provider_registry is registry
    assert runtime.worker_runner._provider_registry is registry
    assert runtime.output_finalization_coordinator._finalization_service is (
        runtime.output_finalization_service
    )
    assert runtime.output_finalization_worker_runner._workspace_lease_manager is (
        runtime.workspace_lease_manager
    )
    assert runtime.health_service._provider_registry is registry
    assert runtime.diagnostics_service._provider_registry is registry
    with pytest.raises(FrozenInstanceError):
        runtime.identity = runtime.identity  # type: ignore[misc]


def test_bootstrap_fails_closed_before_worker_readiness_without_provider() -> None:
    result = RuntimeCompositionRoot().bootstrap(configuration=_configuration())

    assert result.runtime is None
    assert result.status == TransformationRuntimeLifecycleStatus.NOT_READY
    assert result.has_reached_ready is False
    assert result.lifecycle == (
        TransformationRuntimeLifecycleStatus.STARTING,
        TransformationRuntimeLifecycleStatus.NOT_READY,
    )


def test_production_provider_activation_reaches_readiness(tmp_path: Path) -> None:
    enabled = (
        {
            "provider_key": "local_duckdb_tabular",
            "provider_version": "1.0.0",
        },
    )
    with override_settings(
        RESOURCE_REGISTRY_LOCAL_STORAGE_DIR=str(tmp_path),
        RESOURCE_REGISTRY_TRANSFORMATION_ENABLED_PROVIDERS=enabled,
        **_required_runtime_settings(),
    ):
        configuration = RuntimeConfigurationLoader().load()
        result = RuntimeCompositionRoot().bootstrap(
            configuration=configuration,
        )

    assert result.runtime is not None
    assert result.has_reached_ready is True
    assert tuple(item.identity for item in configuration.enabled_providers) == (PROVIDER_IDENTITY,)
    assert result.runtime.provider_registry.list_provider_identities() == (PROVIDER_IDENTITY,)


def test_missing_mandatory_operational_limits_fail_configuration_loading() -> None:
    with override_settings(RESOURCE_REGISTRY_TRANSFORMATION_MAX_INPUT_BYTES=None):
        with pytest.raises(ValueError, match="absent"):
            RuntimeConfigurationLoader().load()


def test_registry_lifecycle_is_irreversible_and_checksum_is_available_when_validated() -> None:
    provider = _RuntimeTestProvider(provider_key="provider", provider_version="1")
    registry = TransformationProviderRegistry()

    assert registry.state == TransformationProviderRegistryState.BUILDING
    registry.register_provider(provider=provider)
    registry.validate()
    assert registry.state == TransformationProviderRegistryState.VALIDATED
    assert len(registry.manifest_checksum) == _SHA256_HEX_LENGTH
    registry.freeze()
    assert registry.state == TransformationProviderRegistryState.FROZEN
    with pytest.raises(TransformationProviderRegistryLifecycleError):
        registry.validate()


def test_configuration_validation_rejects_duplicates_and_invalid_worker_lifecycle() -> None:
    identity = TransformationProviderIdentity("provider", "1")
    with pytest.raises(ValueError, match="unique"):
        _configuration(identity, identity)
    with pytest.raises(ValueError, match="shorter"):
        RuntimeConfiguration(
            enabled_providers=(),
            worker=TransformationWorkerRuntimeConfiguration(
                poll_interval_seconds=1,
                max_retries=0,
                lease_seconds=10,
                heartbeat_seconds=10,
                execution_timeout_seconds=0,
                recovery_batch_size=1,
            ),
            operational_limits=_operational_limits(),
            output_finalization=_finalization_configuration(),
        )


def test_unknown_catalog_identity_and_manifest_mismatch_fail_closed() -> None:
    identity = TransformationProviderIdentity("missing", "1")
    missing = RuntimeCompositionRoot().bootstrap(configuration=_configuration(identity))
    assert missing.status == TransformationRuntimeLifecycleStatus.NOT_READY

    provider = _RuntimeTestProvider(provider_key="provider", provider_version="1")
    invalid_checksum_configuration = RuntimeConfiguration(
        enabled_providers=(
            EnabledTransformationProvider(
                provider_key="provider",
                provider_version="1",
            ),
        ),
        worker=_configuration(provider.provider_identity).worker,
        operational_limits=_operational_limits(),
        output_finalization=_finalization_configuration(),
        expected_registry_manifest_checksum="0" * _SHA256_HEX_LENGTH,
    )
    mismatch = _composition_root(provider).bootstrap(
        configuration=invalid_checksum_configuration,
    )
    assert mismatch.status == TransformationRuntimeLifecycleStatus.NOT_READY


def test_configuration_and_runtime_checksums_are_deterministic() -> None:
    provider = _RuntimeTestProvider(provider_key="provider", provider_version="1")
    configuration = _configuration(provider.provider_identity)

    first = _composition_root(provider).bootstrap_or_raise(configuration=configuration)
    second = _composition_root(provider).bootstrap_or_raise(configuration=configuration)

    assert first.identity == second.identity
    assert len(first.identity.configuration_checksum) == _SHA256_HEX_LENGTH
    assert len(first.identity.runtime_checksum) == _SHA256_HEX_LENGTH


def test_registry_checksum_ignores_provider_registration_order() -> None:
    first_provider = _RuntimeTestProvider(provider_key="alpha", provider_version="1")
    second_provider = _RuntimeTestProvider(provider_key="beta", provider_version="1")
    configuration = _configuration(
        second_provider.provider_identity,
        first_provider.provider_identity,
    )

    first = _composition_root(first_provider, second_provider).bootstrap_or_raise(
        configuration=configuration,
    )
    second = _composition_root(second_provider, first_provider).bootstrap_or_raise(
        configuration=configuration,
    )

    assert first.identity.registry_manifest_checksum == (second.identity.registry_manifest_checksum)


def test_frozen_registry_rejects_late_registration() -> None:
    provider = _RuntimeTestProvider(provider_key="provider", provider_version="1")
    runtime = _composition_root(provider).bootstrap_or_raise(
        configuration=_configuration(provider.provider_identity),
    )

    with pytest.raises(TransformationProviderRegistryLifecycleError):
        runtime.provider_registry.register_provider(provider=provider)


def test_shutdown_stops_polling_without_mutating_runtime_graph() -> None:
    provider = _RuntimeTestProvider(provider_key="provider", provider_version="1")
    runtime = _composition_root(provider).bootstrap_or_raise(
        configuration=_configuration(provider.provider_identity),
    )
    stop_event = Event()

    result = RuntimeShutdownCoordinator().shutdown(
        runtime=runtime,
        stop_event=stop_event,
    )

    assert stop_event.is_set()
    assert result.status == TransformationRuntimeLifecycleStatus.STOPPED
    assert runtime.provider_registry.state == TransformationProviderRegistryState.FROZEN


def test_runtime_checksum_is_stable_in_a_separate_process() -> None:
    configuration = _configuration(require_provider=False)
    local_runtime = RuntimeCompositionRoot().bootstrap_or_raise(configuration=configuration)
    script = """
import django
django.setup()
from resource_registry.application.transformation_runtime import RuntimeCompositionRoot
from resource_registry.tests.test_transformation_runtime_composition import _configuration
runtime = RuntimeCompositionRoot().bootstrap_or_raise(
    configuration=_configuration(require_provider=False),
)
print(runtime.identity.runtime_checksum)
"""
    output = subprocess.check_output(
        [sys.executable, "-c", script],
        text=True,
    )

    assert output.strip().splitlines()[-1] == local_runtime.identity.runtime_checksum
