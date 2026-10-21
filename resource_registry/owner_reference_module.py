"""Define framework-independent owner-issued resource-reference contracts."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .registry_core import ResourceKey, ResourceTypeKey

__all__ = ("OwnerIssuedResourceKeyAdapter",)


@runtime_checkable
class OwnerIssuedResourceKeyAdapter(Protocol):
    """Resolve exact owner-issued identifiers to canonical resource keys.

    Concrete implementations belong to the authoritative resource-owner boundary. They must verify
    the owner-issued identifier before returning a key and must not allocate a Registry surrogate.
    Consumers independently admit returned keys through Namespace and Identity/Type boundaries.

    Attributes:
        declared_resource_type: Exact resource type issued by the owner adapter.
    """

    @property
    def declared_resource_type(self) -> ResourceTypeKey:
        """Return the exact resource type issued by this owner adapter.

        Returns:
            The immutable exact resource type for every resolved key.
        """
        ...

    def resolve_owner_issued_resource_key(self, owner_resource_id: str) -> ResourceKey:
        """Resolve one exact owner-issued resource identifier.

        Args:
            owner_resource_id: Exact opaque identifier verified by the authoritative owner.

        Returns:
            The canonical owner-issued resource key with the same identifier and declared type.

        Raises:
            Exception: If the identifier is invalid, unknown, or cannot be resolved safely.
        """
        ...
