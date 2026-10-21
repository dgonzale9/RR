"""Define pure Identity/Type Module declarations and owner resolution."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .registry_core import (
    NamespaceKey,
    OwnerDescriptor,
    RegistryCoreValidationError,
    ResourceKey,
    ResourceTypeKey,
)

__all__ = (
    "AliasLifecycle",
    "ResourceAlias",
    "ResourceAliasRegistry",
    "ResourceTypeDeclaration",
    "ResourceTypeRegistry",
)


class AliasLifecycle(str, Enum):
    """Classify the command-resolution lifecycle of an immutable alias target."""

    ACTIVE = "active"
    DEPRECATED = "deprecated"
    TOMBSTONED = "tombstoned"

    @classmethod
    def _missing_(cls, value: object) -> AliasLifecycle | None:
        """Reject values outside the approved alias lifecycle states.

        Args:
            value: Candidate lifecycle value.

        Raises:
            RegistryCoreValidationError: If the lifecycle is not an accepted value.
        """
        raise RegistryCoreValidationError("invalid resource alias lifecycle")


@dataclass(frozen=True, slots=True)
class ResourceTypeDeclaration:
    """Declare the exact owner and ownership classification for one resource type.

    Args:
        resource_type: Stable resource semantics key owned by the declaration.
        owner_descriptor: Declarative owner and ownership classification for the resource type.

    Raises:
        RegistryCoreValidationError: If either declaration component is invalid.
    """

    resource_type: ResourceTypeKey
    owner_descriptor: OwnerDescriptor

    def __post_init__(self) -> None:
        """Validate the declaration components in public field order.

        Raises:
            RegistryCoreValidationError: If the resource type or owner descriptor is invalid.
        """
        if not isinstance(self.resource_type, ResourceTypeKey):
            raise RegistryCoreValidationError("invalid resource type declaration key")
        if not isinstance(self.owner_descriptor, OwnerDescriptor):
            raise RegistryCoreValidationError("invalid resource type declaration owner")


@dataclass(frozen=True, slots=True)
class ResourceTypeRegistry:
    """Resolve declared resource types without allocating identities or retaining mutable state.

    Args:
        declarations: Exact immutable type declarations with no duplicate resource type keys.

    Raises:
        RegistryCoreValidationError: If declarations are malformed or duplicate a resource type key.
    """

    declarations: tuple[ResourceTypeDeclaration, ...]

    def __post_init__(self) -> None:
        """Validate the immutable declaration registry in declaration order.

        Raises:
            RegistryCoreValidationError: If declarations are malformed or duplicate a resource type.
        """
        if type(self.declarations) is not tuple:
            raise RegistryCoreValidationError("invalid resource type registry declarations")

        registered_resource_types: set[ResourceTypeKey] = set()
        for declaration in self.declarations:
            if not isinstance(declaration, ResourceTypeDeclaration):
                raise RegistryCoreValidationError("invalid resource type registry declaration")
            if declaration.resource_type in registered_resource_types:
                raise RegistryCoreValidationError("duplicate resource type declaration")
            registered_resource_types.add(declaration.resource_type)

    def resolve_owner_descriptor(self, resource_type: ResourceTypeKey) -> OwnerDescriptor:
        """Resolve the exact declared owner descriptor for a resource type.

        Args:
            resource_type: Exact type key to resolve without alias or prefix inference.

        Returns:
            The declared immutable owner descriptor for the resource type.

        Raises:
            RegistryCoreValidationError: If the type key is invalid or has no declaration.
        """
        if not isinstance(resource_type, ResourceTypeKey):
            raise RegistryCoreValidationError("invalid resource type resolution key")
        for declaration in self.declarations:
            if declaration.resource_type == resource_type:
                return declaration.owner_descriptor
        raise RegistryCoreValidationError("unknown resource type")

    def resolve_resource_owner_descriptor(self, resource_key: ResourceKey) -> OwnerDescriptor:
        """Resolve the declared owner descriptor for an exact resource key.

        Args:
            resource_key: Exact owner-issued resource identity whose type is resolved.

        Returns:
            The declared immutable owner descriptor for the key's resource type.

        Raises:
            RegistryCoreValidationError: If the resource key is invalid or its type is unknown.
        """
        if not isinstance(resource_key, ResourceKey):
            raise RegistryCoreValidationError("invalid resource owner resolution key")
        return self.resolve_owner_descriptor(resource_key.resource_type)


@dataclass(frozen=True, slots=True)
class ResourceAlias:
    """Record one kind-normalized alternate token for an exact resource key.

    The alias kind owns normalization. This contract preserves the supplied normalized value and
    does not infer, case-fold, or otherwise normalize aliases generically.

    Args:
        namespace: Namespace in which the alias is unique.
        resource_type: Resource type in which the alias is unique.
        alias_kind: Registered kind that supplied the normalized value.
        normalized_value: Exact non-blank value normalized by the alias-kind owner.
        target_resource: Immutable target resource with matching namespace and type.
        lifecycle: Alias lifecycle that permits or blocks command resolution.

    Raises:
        RegistryCoreValidationError: If the alias fields or target scope are invalid.
    """

    namespace: NamespaceKey
    resource_type: ResourceTypeKey
    alias_kind: str
    normalized_value: str
    target_resource: ResourceKey
    lifecycle: AliasLifecycle

    def __post_init__(self) -> None:
        """Validate alias fields and prevent cross-namespace or cross-type retargeting.

        Raises:
            RegistryCoreValidationError: If a field is invalid or the target scope differs.
        """
        if not isinstance(self.namespace, NamespaceKey):
            raise RegistryCoreValidationError("invalid resource alias namespace")
        if not isinstance(self.resource_type, ResourceTypeKey):
            raise RegistryCoreValidationError("invalid resource alias type")
        if not _is_non_blank_plain_string(self.alias_kind):
            raise RegistryCoreValidationError("invalid resource alias kind")
        if not _is_non_blank_plain_string(self.normalized_value):
            raise RegistryCoreValidationError("invalid resource alias value")
        if not isinstance(self.target_resource, ResourceKey):
            raise RegistryCoreValidationError("invalid resource alias target")
        if self.target_resource.namespace != self.namespace:
            raise RegistryCoreValidationError("resource alias target namespace mismatch")
        if self.target_resource.resource_type != self.resource_type:
            raise RegistryCoreValidationError("resource alias target type mismatch")
        if not isinstance(self.lifecycle, AliasLifecycle):
            raise RegistryCoreValidationError("invalid resource alias lifecycle")


@dataclass(frozen=True, slots=True)
class ResourceAliasRegistry:
    """Resolve exact aliases without chains, retargeting, or mutable module state.

    Args:
        aliases: Exact immutable aliases with no duplicate namespace/type/kind/value tuple.

    Raises:
        RegistryCoreValidationError: If aliases are malformed or collide.
    """

    aliases: tuple[ResourceAlias, ...]

    def __post_init__(self) -> None:
        """Validate aliases and their exact collision keys in declaration order.

        Raises:
            RegistryCoreValidationError: If aliases are malformed or collide.
        """
        if type(self.aliases) is not tuple:
            raise RegistryCoreValidationError("invalid resource alias registry aliases")

        registered_aliases: set[tuple[NamespaceKey, ResourceTypeKey, str, str]] = set()
        for alias in self.aliases:
            if not isinstance(alias, ResourceAlias):
                raise RegistryCoreValidationError("invalid resource alias registry alias")
            alias_key = (
                alias.namespace,
                alias.resource_type,
                alias.alias_kind,
                alias.normalized_value,
            )
            if alias_key in registered_aliases:
                raise RegistryCoreValidationError("duplicate resource alias")
            registered_aliases.add(alias_key)

    def resolve_resource_alias(
        self,
        namespace: NamespaceKey,
        resource_type: ResourceTypeKey,
        alias_kind: str,
        normalized_value: str,
    ) -> ResourceAlias:
        """Resolve an active or deprecated exact alias without following alias chains.

        Args:
            namespace: Namespace in which to perform the exact lookup.
            resource_type: Resource type in which to perform the exact lookup.
            alias_kind: Registered alias kind supplied by the caller's trusted boundary.
            normalized_value: Exact alias value normalized by the alias-kind owner.

        Returns:
            The active or deprecated immutable alias record.

        Raises:
            RegistryCoreValidationError: If lookup fields are invalid, unknown, or tombstoned.
        """
        if not isinstance(namespace, NamespaceKey):
            raise RegistryCoreValidationError("invalid resource alias namespace")
        if not isinstance(resource_type, ResourceTypeKey):
            raise RegistryCoreValidationError("invalid resource alias type")
        if not _is_non_blank_plain_string(alias_kind):
            raise RegistryCoreValidationError("invalid resource alias kind")
        if not _is_non_blank_plain_string(normalized_value):
            raise RegistryCoreValidationError("invalid resource alias value")
        for alias in self.aliases:
            if (alias.namespace, alias.resource_type, alias.alias_kind, alias.normalized_value) == (
                namespace,
                resource_type,
                alias_kind,
                normalized_value,
            ):
                if alias.lifecycle is AliasLifecycle.TOMBSTONED:
                    raise RegistryCoreValidationError("resource alias tombstoned")
                return alias
        raise RegistryCoreValidationError("unknown resource alias")


def _is_non_blank_plain_string(candidate_value: object) -> bool:
    """Determine whether a value is a non-blank plain string without normalizing it.

    Args:
        candidate_value: Candidate alias kind or normalized alias value.

    Returns:
        Whether the value is a plain string containing a non-whitespace character.
    """
    return type(candidate_value) is str and bool(candidate_value.strip())
