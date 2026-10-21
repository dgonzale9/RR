"""Expose pure immutable Provider identity and descriptor contracts."""

from .adapters import (
    ProviderAdapterError,
    ProviderAdapterErrorCategory,
    ProviderAdapterPort,
    ProviderAdapterRequest,
    ProviderAdapterResult,
)
from .bindings import CertifiedProviderCapabilityBinding, CertifiedProviderCapabilityBindingSet
from .certification import (
    ProviderCertificationEvidenceRef,
    ProviderCertificationProfileRef,
    ProviderCertificationRecord,
)
from .configuration import OpaqueSecretReference, ProviderConfiguration, ProviderConfigurationRef
from .contracts import (
    ProviderDescriptor,
    ProviderFunctionalClassification,
    ProviderIdentity,
    ProviderKey,
    ProviderLifecycleStatus,
    ProviderOriginClassification,
    ProviderVersion,
)
from .errors import (
    ProviderCertificationBindingValidationError,
    ProviderContractValidationError,
    ProviderHealthObservationValidationError,
)
from .health import (
    ProviderHealthObservation,
    ProviderHealthObservationCategory,
    ProviderHealthObservationScope,
    ProviderHealthObservationSet,
)
from .registry import (
    FrozenProviderRegistry,
    ProviderRegistryBuildResult,
    ProviderRegistryBuildStatus,
    ProviderRegistryManifest,
    build_provider_registry_manifest,
    freeze_provider_registry_manifest,
    resolve_registered_provider,
)

__all__ = (
    "CertifiedProviderCapabilityBinding",
    "CertifiedProviderCapabilityBindingSet",
    "FrozenProviderRegistry",
    "OpaqueSecretReference",
    "ProviderAdapterError",
    "ProviderAdapterErrorCategory",
    "ProviderAdapterPort",
    "ProviderAdapterRequest",
    "ProviderAdapterResult",
    "ProviderCertificationBindingValidationError",
    "ProviderCertificationEvidenceRef",
    "ProviderCertificationProfileRef",
    "ProviderCertificationRecord",
    "ProviderConfiguration",
    "ProviderConfigurationRef",
    "ProviderContractValidationError",
    "ProviderDescriptor",
    "ProviderFunctionalClassification",
    "ProviderHealthObservation",
    "ProviderHealthObservationCategory",
    "ProviderHealthObservationScope",
    "ProviderHealthObservationSet",
    "ProviderHealthObservationValidationError",
    "ProviderIdentity",
    "ProviderKey",
    "ProviderLifecycleStatus",
    "ProviderOriginClassification",
    "ProviderRegistryBuildResult",
    "ProviderRegistryBuildStatus",
    "ProviderRegistryManifest",
    "ProviderVersion",
    "build_provider_registry_manifest",
    "freeze_provider_registry_manifest",
    "resolve_registered_provider",
)
