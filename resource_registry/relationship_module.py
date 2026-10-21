"""Define pure structural cross-domain relationship contracts."""

from __future__ import annotations

from dataclasses import dataclass

from .identity_type_module import ResourceTypeRegistry
from .namespace_module import NamespaceRegistry
from .registry_core import RegistryCoreValidationError, ResourceKey

__all__ = ("CrossDomainRelationship", "CrossDomainRelationshipRegistry")


@dataclass(frozen=True, slots=True)
class CrossDomainRelationship:
    """Represent one immutable directed relationship between exact resource identities.

    Args:
        source_resource_key: Exact source resource identity.
        relationship_type: Opaque exact relationship type without Registry-defined semantics.
        target_resource_key: Exact target resource identity.

    Raises:
        RegistryCoreValidationError: If an endpoint or relationship type is invalid.
    """

    source_resource_key: ResourceKey
    relationship_type: str
    target_resource_key: ResourceKey

    def __post_init__(self) -> None:
        """Validate relationship fields in their public declaration order.

        Raises:
            RegistryCoreValidationError: If an endpoint or relationship type is invalid.
        """
        if not isinstance(self.source_resource_key, ResourceKey):
            raise RegistryCoreValidationError(
                "invalid cross-domain relationship source resource key"
            )
        if not _is_non_blank_plain_string(self.relationship_type):
            raise RegistryCoreValidationError("invalid cross-domain relationship type")
        if not isinstance(self.target_resource_key, ResourceKey):
            raise RegistryCoreValidationError(
                "invalid cross-domain relationship target resource key"
            )


@dataclass(frozen=True, slots=True)
class CrossDomainRelationshipRegistry:
    """Admit and resolve only declared structural cross-domain relationships.

    Args:
        resource_type_registry: Completed Identity/Type registry for endpoint-type admission.
        namespace_registry: Completed Namespace registry for endpoint-namespace admission.
        relationships: Immutable declared directed relationships without duplicate exact tuples.

    Raises:
        RegistryCoreValidationError: If dependencies or relationships are invalid or duplicate.
    """

    resource_type_registry: ResourceTypeRegistry
    namespace_registry: NamespaceRegistry
    relationships: tuple[CrossDomainRelationship, ...]

    def __post_init__(self) -> None:
        """Validate dependencies, both endpoints, and exact relationship uniqueness.

        Raises:
            RegistryCoreValidationError: If dependencies or relationships are invalid or duplicate.
        """
        if not isinstance(self.resource_type_registry, ResourceTypeRegistry):
            raise RegistryCoreValidationError("invalid cross-domain relationship type registry")
        if not isinstance(self.namespace_registry, NamespaceRegistry):
            raise RegistryCoreValidationError(
                "invalid cross-domain relationship namespace registry"
            )
        if type(self.relationships) is not tuple:
            raise RegistryCoreValidationError("invalid cross-domain relationships")

        relationship_keys: set[tuple[ResourceKey, str, ResourceKey]] = set()
        for relationship in self.relationships:
            if not isinstance(relationship, CrossDomainRelationship):
                raise RegistryCoreValidationError("invalid cross-domain relationship")
            self._admit_resource_key(relationship.source_resource_key)
            self._admit_resource_key(relationship.target_resource_key)
            relationship_key = _relationship_key(relationship)
            if relationship_key in relationship_keys:
                raise RegistryCoreValidationError("duplicate cross-domain relationship")
            relationship_keys.add(relationship_key)

    def resolve_relationship(
        self,
        source_resource_key: ResourceKey,
        relationship_type: str,
        target_resource_key: ResourceKey,
    ) -> CrossDomainRelationship:
        """Resolve one declared relationship by its exact directed identity tuple.

        Args:
            source_resource_key: Exact source endpoint to admit and match.
            relationship_type: Exact opaque relationship type to match.
            target_resource_key: Exact target endpoint to admit and match.

        Returns:
            The declared immutable relationship with the matching exact tuple.

        Raises:
            RegistryCoreValidationError: If inputs are invalid, endpoints are unadmitted, or no
                exact relationship exists.
        """
        if not isinstance(source_resource_key, ResourceKey):
            raise RegistryCoreValidationError(
                "invalid cross-domain relationship source resource key"
            )
        if not _is_non_blank_plain_string(relationship_type):
            raise RegistryCoreValidationError("invalid cross-domain relationship type")
        if not isinstance(target_resource_key, ResourceKey):
            raise RegistryCoreValidationError(
                "invalid cross-domain relationship target resource key"
            )
        self._admit_resource_key(source_resource_key)
        self._admit_resource_key(target_resource_key)
        relationship_key = (source_resource_key, relationship_type, target_resource_key)
        for relationship in self.relationships:
            if _relationship_key(relationship) == relationship_key:
                return relationship
        raise RegistryCoreValidationError("unknown cross-domain relationship")

    def _admit_resource_key(self, resource_key: ResourceKey) -> None:
        """Admit one endpoint through the completed Namespace and Identity/Type boundaries.

        Args:
            resource_key: Exact endpoint identity to admit.

        Raises:
            RegistryCoreValidationError: If the endpoint namespace or type is unadmitted.
        """
        self.namespace_registry.admit_resource_key(resource_key, self.resource_type_registry)


def _relationship_key(
    relationship: CrossDomainRelationship,
) -> tuple[ResourceKey, str, ResourceKey]:
    """Build the exact directed identity tuple for one relationship.

    Args:
        relationship: Valid immutable relationship value.

    Returns:
        The source/type/target identity tuple without normalization.
    """
    return (
        relationship.source_resource_key,
        relationship.relationship_type,
        relationship.target_resource_key,
    )


def _is_non_blank_plain_string(candidate_value: object) -> bool:
    """Determine whether a value is a non-blank plain string without normalization.

    Args:
        candidate_value: Candidate opaque relationship type.

    Returns:
        Whether the candidate is a plain string containing a non-whitespace character.
    """
    return type(candidate_value) is str and bool(candidate_value.strip())
