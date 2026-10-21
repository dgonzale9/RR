"""Authoritative transformation runtime composition contracts and services."""

from resource_registry.application.transformation_runtime.composition import (
    PRODUCTION_TRANSFORMATION_PROVIDER_CATALOG,
    RuntimeCompositionRoot,
)
from resource_registry.application.transformation_runtime.configuration import (
    RuntimeConfigurationLoader,
)
from resource_registry.application.transformation_runtime.contracts import (
    RUNTIME_CONTRACT_VERSION,
    EnabledTransformationProvider,
    RuntimeBootstrapException,
    RuntimeBootstrapResult,
    RuntimeConfiguration,
    RuntimeIdentity,
    RuntimeShutdownResult,
    TransformationAuditRuntimeConfiguration,
    TransformationHealthRuntimeConfiguration,
    TransformationMetricsRuntimeConfiguration,
    TransformationOperationalLimitsConfiguration,
    TransformationOutputFinalizationRuntimeConfiguration,
    TransformationRuntime,
    TransformationRuntimeLifecycleStatus,
    TransformationRuntimeReadinessValidatorPort,
    TransformationWorkerRuntimeConfiguration,
)
from resource_registry.application.transformation_runtime.shutdown import (
    RuntimeShutdownCoordinator,
)

__all__ = [
    "PRODUCTION_TRANSFORMATION_PROVIDER_CATALOG",
    "RUNTIME_CONTRACT_VERSION",
    "EnabledTransformationProvider",
    "RuntimeBootstrapException",
    "RuntimeBootstrapResult",
    "RuntimeCompositionRoot",
    "RuntimeConfiguration",
    "RuntimeConfigurationLoader",
    "RuntimeIdentity",
    "RuntimeShutdownCoordinator",
    "RuntimeShutdownResult",
    "TransformationAuditRuntimeConfiguration",
    "TransformationHealthRuntimeConfiguration",
    "TransformationMetricsRuntimeConfiguration",
    "TransformationOperationalLimitsConfiguration",
    "TransformationOutputFinalizationRuntimeConfiguration",
    "TransformationRuntime",
    "TransformationRuntimeLifecycleStatus",
    "TransformationRuntimeReadinessValidatorPort",
    "TransformationWorkerRuntimeConfiguration",
]
