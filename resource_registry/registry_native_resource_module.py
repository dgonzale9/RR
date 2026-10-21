"""Define pure Registry-native resource registration and resolution contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .identity_type_module import ResourceTypeRegistry
from .namespace_module import NamespaceRegistry
from .registry_core import (
    MetadataEnvelope,
    MetadataScope,
    OwnershipClassification,
    RegistryCoreValidationError,
    ResourceKey,
)

__all__ = (
    "RegistryNativeResource",
    "RegistryNativeResourceLifecycle",
    "RegistryNativeResourceRegistry",
)


class RegistryNativeResourceLifecycle(str, Enum):
    """Classify the bounded generic lifecycle of a Registry-native resource."""

    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"

    @classmethod
    def _missing_(cls, value: object) -> RegistryNativeResourceLifecycle | None:
        """Reject values outside the bounded generic resource lifecycle.

        Args:
            value: Candidate lifecycle value.

        Raises:
            RegistryCoreValidationError: If the lifecycle is not accepted.
        """
        raise RegistryCoreValidationError("invalid registry-native resource lifecycle")


@dataclass(frozen=True, slots=True)
class RegistryNativeResource:
    """Represent one immutable Registry-native aggregate within a bounded lifecycle.

    Args:
        resource_key: Exact owner-issued key for the Registry-native aggregate.
        lifecycle: Generic bounded lifecycle state for the aggregate.
        metadata: Exact resource-scoped metadata envelopes attached to the aggregate.

    Raises:
        RegistryCoreValidationError: If identity, lifecycle, or metadata association is invalid.
    """

    resource_key: ResourceKey
    lifecycle: RegistryNativeResourceLifecycle
    metadata: tuple[MetadataEnvelope, ...]

    def __post_init__(self) -> None:
        """Validate immutable aggregate fields in public field order.

        Raises:
            RegistryCoreValidationError: If a field or metadata association is invalid.
        """
        if not isinstance(self.resource_key, ResourceKey):
            raise RegistryCoreValidationError("invalid registry-native resource key")
        if not isinstance(self.lifecycle, RegistryNativeResourceLifecycle):
            raise RegistryCoreValidationError("invalid registry-native resource lifecycle")
        if type(self.metadata) is not tuple:
            raise RegistryCoreValidationError("invalid registry-native resource metadata")
        for metadata_envelope in self.metadata:
            if not isinstance(metadata_envelope, MetadataEnvelope):
                raise RegistryCoreValidationError("invalid registry-native resource metadata")
            if (
                metadata_envelope.scope is not MetadataScope.RESOURCE
                or metadata_envelope.subject != self.resource_key
            ):
                raise RegistryCoreValidationError(
                    "registry-native resource metadata subject mismatch"
                )


@dataclass(frozen=True, slots=True)
class RegistryNativeResourceRegistry:
    """Register and resolve approved Registry-native resources without mutable state.

    Args:
        resource_type_registry: Identity/Type registry used to validate native ownership.
        namespace_registry: Namespace registry used to validate resource admission.
        resources: Exact immutable native resources with no duplicate ResourceKeys.

    Raises:
        RegistryCoreValidationError: If dependencies, resources, or ownership are invalid.
    """

    resource_type_registry: ResourceTypeRegistry
    namespace_registry: NamespaceRegistry
    resources: tuple[RegistryNativeResource, ...]

    def __post_init__(self) -> None:
        """Validate registry dependencies, admission, ownership, and identity collisions.

        Raises:
            RegistryCoreValidationError: If a dependency, resource, owner, or key is invalid.
        """
        if not isinstance(self.resource_type_registry, ResourceTypeRegistry):
            raise RegistryCoreValidationError("invalid registry-native resource type registry")
        if not isinstance(self.namespace_registry, NamespaceRegistry):
            raise RegistryCoreValidationError("invalid registry-native namespace registry")
        if type(self.resources) is not tuple:
            raise RegistryCoreValidationError("invalid registry-native resources")

        registered_resource_keys: set[ResourceKey] = set()
        for resource in self.resources:
            if not isinstance(resource, RegistryNativeResource):
                raise RegistryCoreValidationError("invalid registry-native resource")
            owner_descriptor = self.namespace_registry.admit_resource_key(
                resource.resource_key,
                self.resource_type_registry,
            )
            if (
                owner_descriptor.ownership_classification
                is not OwnershipClassification.REGISTRY_NATIVE
            ):
                raise RegistryCoreValidationError("resource type is not registry-native")
            if resource.resource_key in registered_resource_keys:
                raise RegistryCoreValidationError("duplicate registry-native resource key")
            registered_resource_keys.add(resource.resource_key)

    def resolve_resource(self, resource_key: ResourceKey) -> RegistryNativeResource:
        """Resolve one admitted Registry-native resource by exact composite identity.

        Args:
            resource_key: Exact resource key to validate and resolve.

        Returns:
            The immutable Registry-native aggregate with the exact key.

        Raises:
            RegistryCoreValidationError: If the key is invalid, not native, or not registered.
        """
        if not isinstance(resource_key, ResourceKey):
            raise RegistryCoreValidationError("invalid registry-native resource resolution key")
        owner_descriptor = self.namespace_registry.admit_resource_key(
            resource_key,
            self.resource_type_registry,
        )
        if owner_descriptor.ownership_classification is not OwnershipClassification.REGISTRY_NATIVE:
            raise RegistryCoreValidationError("resource type is not registry-native")
        for resource in self.resources:
            if resource.resource_key == resource_key:
                return resource
        raise RegistryCoreValidationError("unknown registry-native resource")
