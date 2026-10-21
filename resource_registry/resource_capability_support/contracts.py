"""Define pure immutable Resource and ResourceType capability-support declarations."""

from __future__ import annotations

from dataclasses import dataclass

from resource_registry.capability_definitions import CapabilityContractRef
from resource_registry.identity_type_module import ResourceTypeRegistry
from resource_registry.registry_core import (
    OwnerDescriptor,
    RegistryCoreValidationError,
    ResourceKey,
    ResourceTypeKey,
)

from .errors import ResourceCapabilitySupportValidationError

__all__ = (  # noqa: RUF022
    "ResourceTypeCapabilitySupportDeclaration",
    "ResourceCapabilitySupportDeclaration",
    "ResourceCapabilitySupportDeclarationSet",
)


@dataclass(frozen=True, slots=True)
class ResourceTypeCapabilitySupportDeclaration:
    """Declare one exact capability contract supported by one ResourceType owner.

    Args:
        resource_type: Exact type whose owner declares semantic capability support.
        owner_descriptor: Exact descriptor of the type owner that declares support.
        contract_ref: Exact portable capability contract version that is supported.

    Raises:
        ResourceCapabilitySupportValidationError: If a declaration component is invalid.
    """

    resource_type: ResourceTypeKey
    owner_descriptor: OwnerDescriptor
    contract_ref: CapabilityContractRef

    def __post_init__(self) -> None:
        """Validate declaration components without resolving ownership.

        Raises:
            ResourceCapabilitySupportValidationError: If a declaration component is invalid.
        """
        if not isinstance(self.resource_type, ResourceTypeKey):
            raise ResourceCapabilitySupportValidationError("invalid resource type support target")
        if not isinstance(self.owner_descriptor, OwnerDescriptor):
            raise ResourceCapabilitySupportValidationError("invalid resource type support owner")
        if not isinstance(self.contract_ref, CapabilityContractRef):
            raise ResourceCapabilitySupportValidationError("invalid resource type support contract")


@dataclass(frozen=True, slots=True)
class ResourceCapabilitySupportDeclaration:
    """Declare one exact capability contract supported by one Resource owner.

    Args:
        resource: Exact resource whose owner declares semantic capability support.
        owner_descriptor: Exact descriptor of the resource type owner that declares support.
        contract_ref: Exact portable capability contract version that is supported.

    Raises:
        ResourceCapabilitySupportValidationError: If a declaration component is invalid.
    """

    resource: ResourceKey
    owner_descriptor: OwnerDescriptor
    contract_ref: CapabilityContractRef

    def __post_init__(self) -> None:
        """Validate declaration components without resolving ownership.

        Raises:
            ResourceCapabilitySupportValidationError: If a declaration component is invalid.
        """
        if not isinstance(self.resource, ResourceKey):
            raise ResourceCapabilitySupportValidationError("invalid resource support target")
        if not isinstance(self.owner_descriptor, OwnerDescriptor):
            raise ResourceCapabilitySupportValidationError("invalid resource support owner")
        if not isinstance(self.contract_ref, CapabilityContractRef):
            raise ResourceCapabilitySupportValidationError("invalid resource support contract")


CapabilitySupportDeclaration = (
    ResourceTypeCapabilitySupportDeclaration | ResourceCapabilitySupportDeclaration
)


@dataclass(frozen=True, slots=True)
class ResourceCapabilitySupportDeclarationSet:
    """Validate immutable owner-scoped Resource and ResourceType support declarations.

    Args:
        declarations: Exact immutable support declarations in declaration order.
        resource_type_registry: Existing exact type-to-owner declarations used only for validation.

    Raises:
        ResourceCapabilitySupportValidationError: If declarations are malformed, duplicate, or
            captured under an owner other than the target type's declared owner.
    """

    declarations: tuple[CapabilitySupportDeclaration, ...]
    resource_type_registry: ResourceTypeRegistry

    def __post_init__(self) -> None:
        """Validate declaration exactness, owner provenance, and duplicate associations.

        Raises:
            ResourceCapabilitySupportValidationError: If declarations are invalid or owners do not
                match their exact target type.
        """
        if type(self.declarations) is not tuple:
            raise ResourceCapabilitySupportValidationError(
                "invalid capability support declarations"
            )
        if not isinstance(self.resource_type_registry, ResourceTypeRegistry):
            raise ResourceCapabilitySupportValidationError(
                "invalid capability support type registry"
            )

        registered_associations: set[
            tuple[ResourceTypeKey | ResourceKey, CapabilityContractRef]
        ] = set()
        for declaration in self.declarations:
            if not isinstance(
                declaration,
                ResourceTypeCapabilitySupportDeclaration | ResourceCapabilitySupportDeclaration,
            ):
                raise ResourceCapabilitySupportValidationError(
                    "invalid capability support declaration"
                )

            target, expected_owner = self._resolve_target_and_owner(declaration)
            if declaration.owner_descriptor != expected_owner:
                raise ResourceCapabilitySupportValidationError("capability support owner mismatch")

            association = (target, declaration.contract_ref)
            if association in registered_associations:
                raise ResourceCapabilitySupportValidationError(
                    "duplicate capability support declaration"
                )
            registered_associations.add(association)

    def _resolve_target_and_owner(
        self, declaration: CapabilitySupportDeclaration
    ) -> tuple[ResourceTypeKey | ResourceKey, OwnerDescriptor]:
        """Resolve the declaration target and its exact existing owner descriptor.

        Args:
            declaration: Valid typed declaration whose owner provenance is being checked.

        Returns:
            The exact declaration target and owner descriptor from the type registry.

        Raises:
            ResourceCapabilitySupportValidationError: If the declaration target type is unknown.
        """
        try:
            if isinstance(declaration, ResourceTypeCapabilitySupportDeclaration):
                return (
                    declaration.resource_type,
                    self.resource_type_registry.resolve_owner_descriptor(declaration.resource_type),
                )
            return (
                declaration.resource,
                self.resource_type_registry.resolve_resource_owner_descriptor(declaration.resource),
            )
        except RegistryCoreValidationError:
            raise ResourceCapabilitySupportValidationError(
                "unknown capability support target type"
            ) from None
