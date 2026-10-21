"""Verify the ERR-1.2-S1 Namespace Module contracts."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from uuid import UUID

import pytest

from resource_registry.identity_type_module import ResourceTypeDeclaration, ResourceTypeRegistry
from resource_registry.namespace_module import NamespaceDeclaration, NamespaceRegistry
from resource_registry.registry_core import (
    NamespaceKey,
    OwnerDescriptor,
    OwnershipClassification,
    RegistryCoreValidationError,
    ResourceKey,
    ResourceTypeKey,
)

_WORKSPACE_ID = 42


def _build_namespace() -> NamespaceKey:
    """Build a valid namespace fixture.

    Returns:
        A valid namespace key.
    """
    return NamespaceKey(UUID("8dbf68a1-9567-4d74-931f-c36c4247f80b"))


def _build_type_registry() -> ResourceTypeRegistry:
    """Build an Identity/Type registry compatible with the namespace fixture.

    Returns:
        A valid resource type registry.
    """
    return ResourceTypeRegistry(
        (
            ResourceTypeDeclaration(
                ResourceTypeKey("atlas.catalog.document"),
                OwnerDescriptor("document-catalog", OwnershipClassification.DOMAIN_BACKED),
            ),
        )
    )


def _build_registry() -> NamespaceRegistry:
    """Build a valid immutable Namespace registry.

    Returns:
        A valid namespace registry.
    """
    return NamespaceRegistry((NamespaceDeclaration(_build_namespace(), _WORKSPACE_ID),))


def test_namespace_module_exports_immutable_mapping_contracts() -> None:
    """Expose only the authorized Namespace Module public contracts."""
    from resource_registry import namespace_module

    assert namespace_module.__all__ == ("NamespaceDeclaration", "NamespaceRegistry")
    assert NamespaceDeclaration.__slots__ == ("namespace", "workspace_id")
    assert tuple(field.name for field in fields(NamespaceDeclaration)) == (
        "namespace",
        "workspace_id",
    )


def test_namespace_registry_resolves_in_both_directions_and_admits_registered_types() -> None:
    """Resolve an immutable mapping and admit a key through Identity/Type resolution."""
    registry = _build_registry()
    resource_key = ResourceKey(
        _build_namespace(), ResourceTypeKey("atlas.catalog.document"), "id-1"
    )

    assert registry.resolve_namespace_for_workspace(_WORKSPACE_ID) == _build_namespace()
    assert registry.resolve_workspace_for_namespace(_build_namespace()) == _WORKSPACE_ID
    assert registry.admit_resource_key(resource_key, _build_type_registry()) == OwnerDescriptor(
        "document-catalog", OwnershipClassification.DOMAIN_BACKED
    )
    assert hash(registry) == hash(_build_registry())
    with pytest.raises(FrozenInstanceError):
        registry.declarations = ()  # type: ignore[misc]


def test_namespace_registry_resolution_is_independent_of_declaration_order() -> None:
    """Resolve each immutable mapping identically regardless of declaration order."""
    first_declaration = NamespaceDeclaration(_build_namespace(), _WORKSPACE_ID)
    second_declaration = NamespaceDeclaration(
        NamespaceKey("1dbf68a1-9567-4d74-931f-c36c4247f80b"), 7
    )
    forward_registry = NamespaceRegistry((first_declaration, second_declaration))
    reverse_registry = NamespaceRegistry((second_declaration, first_declaration))

    assert forward_registry.resolve_namespace_for_workspace(7) == second_declaration.namespace
    assert reverse_registry.resolve_namespace_for_workspace(7) == second_declaration.namespace


def test_namespace_registry_allows_multiple_declared_resource_types() -> None:
    """Admit distinct registered types within one declared namespace."""
    resource_type_registry = ResourceTypeRegistry(
        (
            ResourceTypeDeclaration(
                ResourceTypeKey("atlas.catalog.document"),
                OwnerDescriptor("document-catalog", OwnershipClassification.DOMAIN_BACKED),
            ),
            ResourceTypeDeclaration(
                ResourceTypeKey("atlas.catalog.collection"),
                OwnerDescriptor("collection-catalog", OwnershipClassification.DOMAIN_BACKED),
            ),
        )
    )
    collection_key = ResourceKey(
        _build_namespace(), ResourceTypeKey("atlas.catalog.collection"), "collection-1"
    )

    assert _build_registry().admit_resource_key(collection_key, resource_type_registry) == (
        OwnerDescriptor("collection-catalog", OwnershipClassification.DOMAIN_BACKED)
    )


@pytest.mark.parametrize(
    ("namespace", "workspace_id", "message"),
    (
        (None, _WORKSPACE_ID, "invalid namespace declaration key"),
        (_build_namespace(), 0, "invalid namespace declaration workspace"),
        (_build_namespace(), True, "invalid namespace declaration workspace"),
        (None, 0, "invalid namespace declaration key"),
    ),
)
def test_namespace_declaration_validates_fields_in_order(
    namespace: object, workspace_id: object, message: str
) -> None:
    """Reject invalid namespace declaration fields with stable precedence."""
    with pytest.raises(RegistryCoreValidationError, match=f"^{message}$"):
        NamespaceDeclaration(namespace, workspace_id)  # type: ignore[arg-type]


def test_namespace_registry_rejects_namespace_and_workspace_collisions() -> None:
    """Reject one-to-many and many-to-one immutable mapping collisions."""
    declaration = NamespaceDeclaration(_build_namespace(), _WORKSPACE_ID)
    other_namespace = NamespaceKey("1dbf68a1-9567-4d74-931f-c36c4247f80b")

    with pytest.raises(RegistryCoreValidationError, match="^duplicate namespace declaration$"):
        NamespaceRegistry((declaration, declaration))
    with pytest.raises(RegistryCoreValidationError, match="^duplicate namespace workspace$"):
        NamespaceRegistry((declaration, NamespaceDeclaration(other_namespace, _WORKSPACE_ID)))


@pytest.mark.parametrize(
    ("workspace_id", "message"),
    ((None, "invalid namespace workspace"), (7, "unknown namespace workspace")),
)
def test_namespace_registry_fails_closed_for_invalid_or_unknown_workspace(
    workspace_id: object, message: str
) -> None:
    """Reject invalid and unknown Workspace namespace resolution requests."""
    with pytest.raises(RegistryCoreValidationError, match=f"^{message}$"):
        _build_registry().resolve_namespace_for_workspace(workspace_id)  # type: ignore[arg-type]


def test_namespace_registry_fails_closed_before_type_resolution_for_unknown_namespace() -> None:
    """Reject an unregistered namespace before querying the completed type registry."""
    resource_key = ResourceKey(
        NamespaceKey("1dbf68a1-9567-4d74-931f-c36c4247f80b"),
        ResourceTypeKey("atlas.catalog.document"),
        "id-1",
    )

    with pytest.raises(RegistryCoreValidationError, match="^unknown namespace$"):
        _build_registry().admit_resource_key(resource_key, _build_type_registry())
