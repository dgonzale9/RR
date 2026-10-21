from __future__ import annotations

import hashlib
from collections.abc import Mapping
from types import MappingProxyType

from resource_registry.application.transformation_runtime.configuration import (
    RuntimeConfigurationLoader,
)
from resource_registry.application.transformation_runtime.contracts import (
    RUNTIME_CONTRACT_VERSION,
    RuntimeBootstrapException,
    RuntimeBootstrapResult,
    RuntimeConfiguration,
    RuntimeIdentity,
    TransformationRuntime,
    TransformationRuntimeLifecycleStatus,
)
from resource_registry.application.transformation_runtime.provider_dependencies import (
    RuntimeProviderInputMaterializer,
    RuntimeProviderOutputWorkspace,
)
from resource_registry.application.transformation_runtime.readiness import (
    TransformationRuntimeReadinessValidator,
)
from resource_registry.events.audit_service import AuditService
from resource_registry.events.event_publisher import ProductionAppendOnlyEventPublisher
from resource_registry.policies.resolution import PolicyResolutionService
from resource_registry.security.integrity import ArtifactIntegrityVerifier
from resource_registry.services.duckdb_service import InMemoryDuckDbConnectionProvider
from resource_registry.services.registry.artifact_lifecycle_service import ArtifactLifecycleService
from resource_registry.services.registry.dataset_registry_service import DatasetRegistryService
from resource_registry.services.registry.lineage_service import LineageService
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
from resource_registry.services.transformation_run_worker_runner import (
    TransformationRunWorkerRunner,
    UnavailableTransformationCheckpointReferenceVerifier,
)
from resource_registry.services.transformation_workspace_lease_manager import (
    TransformationWorkspaceLeaseManager,
)
from resource_registry.storage.factory import DataRegistryStorageFactory
from resource_registry.storage.read_adapters import (
    ArtifactReadAdapter,
    LocalArtifactReadAdapter,
    StorageProviderArtifactReadAdapter,
)
from resource_registry.transformations.execution.attempt_persistence import (
    TransformationRunAttemptPersistenceService,
)
from resource_registry.transformations.execution.checkpoint_persistence import (
    TransformationStepCheckpointService,
)
from resource_registry.transformations.execution.service import TransformationRunExecutionService
from resource_registry.transformations.execution_plan import TransformationExecutionPlanCompiler
from resource_registry.transformations.operational.authorization import (
    TransformationAuthorizationService,
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
from resource_registry.transformations.operational.logging import (
    TransformationOperationalLogContextBuilder,
)
from resource_registry.transformations.operational.metrics import ProductionMetricsAdapter
from resource_registry.transformations.output_finalization import (
    TransformationRunOutputFinalizationService,
)
from resource_registry.transformations.providers import (
    ProviderDependencies,
    ProviderFactory,
    ProviderOperationalLimits,
    TransformationProviderIdentity,
    TransformationProviderRegistry,
    TransformationProviderRegistryState,
)
from resource_registry.transformations.providers.builtin import (
    PRODUCTION_TRANSFORMATION_PROVIDER_CATALOG as _PRODUCTION_TRANSFORMATION_PROVIDER_CATALOG,
)
from resource_registry.transformations.runs import TransformationRunCreationService
from resource_registry.utils.json_checksum import canonicalize_transformation_specification

ProviderCatalog = Mapping[TransformationProviderIdentity, ProviderFactory]
PRODUCTION_TRANSFORMATION_PROVIDER_CATALOG: ProviderCatalog = (
    _PRODUCTION_TRANSFORMATION_PROVIDER_CATALOG
)


class RuntimeCompositionRoot:
    """Construct the sole immutable transformation runtime dependency graph."""

    def __init__(
        self,
        *,
        provider_catalog: ProviderCatalog = PRODUCTION_TRANSFORMATION_PROVIDER_CATALOG,
        configuration_loader: RuntimeConfigurationLoader | None = None,
    ) -> None:
        """Initialize explicit bootstrap inputs without constructing runtime services."""
        self._provider_catalog = MappingProxyType(dict(provider_catalog))
        self._configuration_loader = configuration_loader or RuntimeConfigurationLoader()

    def bootstrap(
        self,
        *,
        configuration: RuntimeConfiguration | None = None,
        worker_overrides: Mapping[str, object] | None = None,
        worker_id: str | None = None,
    ) -> RuntimeBootstrapResult:
        """Build and validate one complete runtime or return a fail-closed result."""
        try:
            active_configuration = configuration or self._configuration_loader.load(
                worker_overrides=worker_overrides,
            )
            metrics = ProductionMetricsAdapter()
            storage_factory = DataRegistryStorageFactory()
            workspace_lease_manager = TransformationWorkspaceLeaseManager(
                storage_factory=storage_factory,
                workspace_quota_bytes=active_configuration.operational_limits.workspace_quota_bytes,
                workspace_maximum_bytes=(
                    active_configuration.operational_limits.workspace_maximum_bytes
                ),
                lease_seconds=active_configuration.output_finalization.lease_seconds,
                metrics=metrics,
            )
            provider_dependencies = self._build_provider_dependencies(
                configuration=active_configuration,
                storage_factory=storage_factory,
                workspace_lease_manager=workspace_lease_manager,
            )
            registry = self._build_registry(
                configuration=active_configuration,
                dependencies=provider_dependencies,
            )
            identity = _build_runtime_identity(
                configuration=active_configuration,
                registry=registry,
            )
            runtime = self._build_runtime(
                configuration=active_configuration,
                registry=registry,
                identity=identity,
                worker_id=worker_id,
                storage_factory=storage_factory,
                workspace_lease_manager=workspace_lease_manager,
                metrics=metrics,
            )
            status = self._validate_runtime(runtime=runtime)
            lifecycle: tuple[TransformationRuntimeLifecycleStatus, ...] = (
                TransformationRuntimeLifecycleStatus.STARTING,
                TransformationRuntimeLifecycleStatus.READY,
            )
            if status == TransformationRuntimeLifecycleStatus.DEGRADED:
                lifecycle += (TransformationRuntimeLifecycleStatus.DEGRADED,)
            return RuntimeBootstrapResult(
                status=status,
                runtime=runtime,
                has_reached_ready=True,
                lifecycle=lifecycle,
            )
        except Exception as error:
            return RuntimeBootstrapResult(
                status=TransformationRuntimeLifecycleStatus.NOT_READY,
                runtime=None,
                errors=(error.__class__.__name__,),
                has_reached_ready=False,
                lifecycle=(
                    TransformationRuntimeLifecycleStatus.STARTING,
                    TransformationRuntimeLifecycleStatus.NOT_READY,
                ),
            )

    def bootstrap_or_raise(
        self,
        *,
        configuration: RuntimeConfiguration | None = None,
        worker_overrides: Mapping[str, object] | None = None,
        worker_id: str | None = None,
    ) -> TransformationRuntime:
        """Return a ready runtime or raise a sanitized bootstrap exception."""
        result = self.bootstrap(
            configuration=configuration,
            worker_overrides=worker_overrides,
            worker_id=worker_id,
        )
        if result.runtime is None or not result.has_reached_ready:
            raise RuntimeBootstrapException("Transformation runtime bootstrap failed closed.")
        return result.runtime

    def _build_registry(
        self,
        *,
        configuration: RuntimeConfiguration,
        dependencies: ProviderDependencies,
    ) -> TransformationProviderRegistry:
        registry = TransformationProviderRegistry()
        for enabled in configuration.enabled_providers:
            factory = self._provider_catalog.get(enabled.identity)
            if factory is None:
                raise RuntimeBootstrapException(
                    "Configured transformation provider is not in the approved catalog.",
                )
            provider = factory(dependencies)
            if provider.provider_identity != enabled.identity:
                raise RuntimeBootstrapException(
                    "Transformation provider factory returned an unexpected identity.",
                )
            registry.register_provider(provider=provider)
        registry.validate()
        expected_checksum = configuration.expected_registry_manifest_checksum
        if expected_checksum is not None and registry.manifest_checksum != expected_checksum:
            raise RuntimeBootstrapException(
                "Transformation provider registry manifest checksum does not match configuration.",
            )
        registry.freeze()
        return registry

    def _build_provider_dependencies(
        self,
        *,
        configuration: RuntimeConfiguration,
        storage_factory: DataRegistryStorageFactory,
        workspace_lease_manager: TransformationWorkspaceLeaseManager,
    ) -> ProviderDependencies:
        """Construct the immutable provider I/O and compute ports at the composition boundary."""
        local_provider = storage_factory.create_provider("local")
        default_provider = storage_factory.create_provider()
        read_adapters: list[ArtifactReadAdapter] = [
            LocalArtifactReadAdapter(storage_provider=local_provider),
        ]
        provider_name = str(getattr(default_provider, "PROVIDER_NAME", "")).lower()
        if provider_name == "azure_blob":
            read_adapters.append(
                StorageProviderArtifactReadAdapter(
                    storage_provider=default_provider,
                    schemes=("azure://", "https://"),
                ),
            )
        integrity_verifier = ArtifactIntegrityVerifier()
        return ProviderDependencies(
            input_materializer=RuntimeProviderInputMaterializer(
                read_adapters=tuple(read_adapters),
                integrity_verifier=integrity_verifier,
                maximum_input_bytes=configuration.operational_limits.maximum_input_bytes,
                minimum_free_disk_bytes=configuration.operational_limits.minimum_free_disk_bytes,
                materialization_timeout_seconds=(
                    configuration.operational_limits.materialization_timeout_seconds
                ),
            ),
            output_workspace=RuntimeProviderOutputWorkspace(
                storage_provider=default_provider,
                integrity_verifier=integrity_verifier,
                lease_manager=workspace_lease_manager,
                minimum_free_disk_bytes=configuration.operational_limits.minimum_free_disk_bytes,
            ),
            duckdb_connections=InMemoryDuckDbConnectionProvider(),
            operational_limits=ProviderOperationalLimits(
                maximum_input_bytes=configuration.operational_limits.maximum_input_bytes,
                maximum_output_bytes=configuration.operational_limits.maximum_output_bytes,
                maximum_row_count=configuration.operational_limits.maximum_row_count,
                maximum_source_columns=(
                    configuration.operational_limits.maximum_source_columns
                ),
                maximum_selected_columns=(
                    configuration.operational_limits.maximum_selected_columns
                ),
                provider_timeout_seconds=(
                    configuration.operational_limits.provider_timeout_seconds
                ),
                materialization_timeout_seconds=(
                    configuration.operational_limits.materialization_timeout_seconds
                ),
            ),
        )

    def _build_runtime(  # noqa: PLR0913
        self,
        *,
        configuration: RuntimeConfiguration,
        registry: TransformationProviderRegistry,
        identity: RuntimeIdentity,
        worker_id: str | None,
        storage_factory: DataRegistryStorageFactory,
        workspace_lease_manager: TransformationWorkspaceLeaseManager,
        metrics: ProductionMetricsAdapter,
    ) -> TransformationRuntime:
        audit_service = AuditService(publisher=ProductionAppendOnlyEventPublisher())
        log_context_builder = TransformationOperationalLogContextBuilder()
        policy_resolution_service = PolicyResolutionService(audit_service=audit_service)
        security_policy_resolver = TransformationSecurityPolicyResolver(
            policy_resolution_service=policy_resolution_service,
        )
        authorization_service = TransformationAuthorizationService(
            policy_resolver=security_policy_resolver,
            provider_registry=registry,
        )
        run_creation_service = TransformationRunCreationService()
        authorized_creation_service = AuthorizedTransformationRunCreationService(
            authorization_service=authorization_service,
            creation_service=run_creation_service,
            audit_service=audit_service,
            metrics=metrics,
            log_context_builder=log_context_builder,
        )
        attempt_persistence = TransformationRunAttemptPersistenceService()
        checkpoint_persistence = TransformationStepCheckpointService()
        job_service = TransformationRunJobService(
            attempt_persistence=attempt_persistence,
            max_retries=configuration.worker.max_retries,
            audit_service=audit_service,
        )
        execution_engine = TransformationRunExecutionService(
            provider_registry=registry,
            persistence=checkpoint_persistence,
        )
        execution_coordinator = TransformationRunExecutionCoordinator(
            execution_service=execution_engine,
            job_service=job_service,
            metrics=metrics,
        )
        checkpoint_verifier = UnavailableTransformationCheckpointReferenceVerifier()
        worker_runner = TransformationRunWorkerRunner(
            job_service=job_service,
            coordinator=execution_coordinator,
            provider_registry=registry,
            checkpoint_reference_verifier=checkpoint_verifier,
            metrics=metrics,
            log_context_builder=log_context_builder,
            worker_id=worker_id,
            poll_interval_seconds=configuration.worker.poll_interval_seconds,
            lease_seconds=configuration.worker.lease_seconds,
            heartbeat_seconds=configuration.worker.heartbeat_seconds,
            execution_timeout_seconds=configuration.operational_limits.provider_timeout_seconds,
            recovery_batch_size=configuration.worker.recovery_batch_size,
        )
        output_source_adapters: list[ArtifactReadAdapter] = []
        output_source_provider = storage_factory.create_provider()
        if str(getattr(output_source_provider, "PROVIDER_NAME", "")).lower() == "azure_blob":
            output_source_adapters.append(
                StorageProviderArtifactReadAdapter(
                    storage_provider=output_source_provider,
                    schemes=("azure://", "https://"),
                ),
            )
        artifact_lifecycle_service = ArtifactLifecycleService(audit_service=audit_service)
        output_finalization_service = TransformationRunOutputFinalizationService(
            storage_factory=storage_factory,
            source_read_adapters=tuple(output_source_adapters),
            integrity_verifier=ArtifactIntegrityVerifier(),
            artifact_lifecycle_service=artifact_lifecycle_service,
            dataset_registry_service=DatasetRegistryService(),
            lineage_service=LineageService(),
            audit_service=audit_service,
            metrics=metrics,
            log_context_builder=log_context_builder,
        )
        finalization_job_service = TransformationOutputFinalizationJobService(
            finalization_service=output_finalization_service,
            max_retries=configuration.output_finalization.max_retries,
            per_tenant_concurrency=configuration.output_finalization.per_tenant_concurrency,
            audit_service=audit_service,
        )
        finalization_coordinator = TransformationOutputFinalizationCoordinator(
            finalization_service=output_finalization_service,
            job_service=finalization_job_service,
            metrics=metrics,
            workspace_lease_manager=workspace_lease_manager,
        )
        finalization_worker_runner = TransformationOutputFinalizationWorkerRunner(
            job_service=finalization_job_service,
            coordinator=finalization_coordinator,
            workspace_lease_manager=workspace_lease_manager,
            metrics=metrics,
            log_context_builder=log_context_builder,
            poll_interval_seconds=configuration.output_finalization.poll_interval_seconds,
            lease_seconds=configuration.output_finalization.lease_seconds,
            heartbeat_seconds=configuration.output_finalization.heartbeat_seconds,
            timeout_seconds=configuration.output_finalization.timeout_seconds,
            recovery_batch_size=configuration.output_finalization.recovery_batch_size,
            worker_id=worker_id,
        )
        def storage_availability_check() -> bool:
            return _is_storage_available(storage_factory=storage_factory)
        readiness_validator = TransformationRuntimeReadinessValidator(
            configuration=configuration,
            identity=identity,
            provider_registry=registry,
            authorization_service=authorization_service,
            metrics=metrics,
            audit_service=audit_service,
            storage_availability_check=storage_availability_check,
            governance_configuration_check=lambda: security_policy_resolver is not None,
        )
        health_service = TransformationOperationalHealthService(
            authorization_service=authorization_service,
            policy_resolver=security_policy_resolver,
            provider_registry=registry,
            audit_service=audit_service,
            metrics=metrics,
            workspace_quota_bytes=configuration.operational_limits.workspace_quota_bytes,
            storage_availability_check=storage_availability_check,
            minimum_free_disk_bytes=configuration.operational_limits.minimum_free_disk_bytes,
        )
        diagnostics_service = TransformationOperationalDiagnosticsService(
            authorization_service=authorization_service,
            provider_registry=registry,
            readiness_report_provider=readiness_validator.validate,
        )
        return TransformationRuntime(
            configuration=configuration,
            provider_registry=registry,
            execution_plan_compiler=TransformationExecutionPlanCompiler(
                provider_registry=registry,
            ),
            execution_engine=execution_engine,
            output_finalization_service=output_finalization_service,
            authorization_service=authorization_service,
            authorized_creation_service=authorized_creation_service,
            security_policy_resolver=security_policy_resolver,
            job_service=job_service,
            execution_coordinator=execution_coordinator,
            worker_runner=worker_runner,
            output_finalization_job_service=finalization_job_service,
            output_finalization_coordinator=finalization_coordinator,
            output_finalization_worker_runner=finalization_worker_runner,
            workspace_lease_manager=workspace_lease_manager,
            metrics=metrics,
            audit_service=audit_service,
            health_service=health_service,
            diagnostics_service=diagnostics_service,
            readiness_validator=readiness_validator,
            identity=identity,
        )

    def _validate_runtime(
        self,
        *,
        runtime: TransformationRuntime,
    ) -> TransformationRuntimeLifecycleStatus:
        if runtime.provider_registry.state != TransformationProviderRegistryState.FROZEN:
            raise RuntimeBootstrapException("Transformation provider registry is not frozen.")
        if runtime.configuration.health.require_provider and not (
            runtime.provider_registry.list_provider_identities()
        ):
            raise RuntimeBootstrapException("At least one approved provider is required.")
        metrics_available = runtime.metrics.is_available()
        audit_available = runtime.audit_service.is_available()
        if not metrics_available or not audit_available:
            if not runtime.configuration.health.allow_degraded_observability:
                raise RuntimeBootstrapException(
                    "Required transformation observability is unavailable.",
                )
            return TransformationRuntimeLifecycleStatus.DEGRADED
        return TransformationRuntimeLifecycleStatus.READY


def _build_runtime_identity(
    *,
    configuration: RuntimeConfiguration,
    registry: TransformationProviderRegistry,
) -> RuntimeIdentity:
    configuration_payload = configuration.to_checksum_dict()
    configuration_checksum = _checksum(configuration_payload)
    runtime_checksum = _checksum(
        {
            "runtime_contract_version": RUNTIME_CONTRACT_VERSION,
            "registry_manifest_checksum": registry.manifest_checksum,
            "configuration": configuration_payload,
        },
    )
    return RuntimeIdentity(
        runtime_contract_version=RUNTIME_CONTRACT_VERSION,
        registry_manifest_checksum=registry.manifest_checksum,
        configuration_checksum=configuration_checksum,
        runtime_checksum=runtime_checksum,
    )


def _checksum(value: Mapping[str, object]) -> str:
    canonical = canonicalize_transformation_specification(value)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _is_storage_available(*, storage_factory: DataRegistryStorageFactory) -> bool:
    provider = storage_factory.create_provider()
    _ = provider.exists("_transformation_runtime_readiness_probe")
    return True
