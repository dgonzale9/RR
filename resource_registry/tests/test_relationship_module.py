"""Verify ERR-1.6-S1 structural cross-domain relationship contracts."""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from typing import cast
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
from resource_registry.relationship_module import (
    CrossDomainRelationship,
    CrossDomainRelationshipRegistry,
)

_MODULE_SOURCE_PATH = Path(__file__).resolve().parents[1] / "relationship_module.py"


def _build_source_key() -> ResourceKey:
    """Build an admitted exact source key.

    Returns:
        A valid exact source resource identity.
    """
    return ResourceKey(
        NamespaceKey(UUID("8dbf68a1-9567-4d74-931f-c36c4247f80b")),
        ResourceTypeKey("atlas.catalog.document"),
        "document-1",
    )


def _build_target_key() -> ResourceKey:
    """Build an admitted exact cross-Namespace target key.

    Returns:
        A valid exact target resource identity.
    """
    return ResourceKey(
        NamespaceKey(UUID("1dbf68a1-9567-4d74-931f-c36c4247f80b")),
        ResourceTypeKey("atlas.catalog.collection"),
        "collection-1",
    )


def _build_type_registry() -> ResourceTypeRegistry:
    """Build declarations for the relationship endpoints.

    Returns:
        A type registry admitting both fixture endpoint types.
    """
    return ResourceTypeRegistry(
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


def _build_namespace_registry() -> NamespaceRegistry:
    """Build declarations for both endpoint namespaces.

    Returns:
        A namespace registry admitting both fixture endpoint namespaces.
    """
    return NamespaceRegistry(
        (
            NamespaceDeclaration(_build_source_key().namespace, 42),
            NamespaceDeclaration(_build_target_key().namespace, 7),
        )
    )


def _build_relationship() -> CrossDomainRelationship:
    """Build one valid directed cross-domain relationship.

    Returns:
        A valid immutable relationship value.
    """
    return CrossDomainRelationship(_build_source_key(), "references", _build_target_key())


def _build_registry(
    relationships: tuple[CrossDomainRelationship, ...] | None = None,
) -> CrossDomainRelationshipRegistry:
    """Build a valid relationship registry.

    Args:
        relationships: Exact declared relationships, defaulting to one valid relationship.

    Returns:
        A relationship registry with admitted dependencies.
    """
    return CrossDomainRelationshipRegistry(
        _build_type_registry(),
        _build_namespace_registry(),
        (_build_relationship(),) if relationships is None else relationships,
    )


def test_relationship_module_exposes_only_structural_public_contracts() -> None:
    """Expose only the approved structural relationship contracts."""
    from resource_registry import relationship_module

    assert relationship_module.__all__ == (
        "CrossDomainRelationship",
        "CrossDomainRelationshipRegistry",
    )
    assert tuple(field.name for field in fields(CrossDomainRelationship)) == (
        "source_resource_key",
        "relationship_type",
        "target_resource_key",
    )
    assert CrossDomainRelationship.__slots__ == (
        "source_resource_key",
        "relationship_type",
        "target_resource_key",
    )


def test_relationship_registry_preserves_exact_directed_identity_and_order() -> None:
    """Resolve only exact directed tuples while preserving declared relationship order."""
    relationship = _build_relationship()
    reverse_relationship = CrossDomainRelationship(
        relationship.target_resource_key,
        relationship.relationship_type,
        relationship.source_resource_key,
    )
    registry = _build_registry((relationship, reverse_relationship))

    assert registry.relationships == (relationship, reverse_relationship)
    assert (
        registry.resolve_relationship(
            relationship.source_resource_key,
            "references",
            relationship.target_resource_key,
        )
        == relationship
    )
    assert (
        registry.resolve_relationship(
            reverse_relationship.source_resource_key,
            "references",
            reverse_relationship.target_resource_key,
        )
        == reverse_relationship
    )
    assert hash(relationship) == hash(_build_relationship())
    with pytest.raises(FrozenInstanceError):
        relationship.relationship_type = "uses"  # type: ignore[misc]


def test_relationship_registry_allows_exact_self_relationships() -> None:
    """Permit self-relationships because their semantics belong to an owning domain."""
    source_key = _build_source_key()
    relationship = CrossDomainRelationship(source_key, "related", source_key)

    assert (
        _build_registry((relationship,)).resolve_relationship(source_key, "related", source_key)
        == relationship
    )


@pytest.mark.parametrize(
    ("source_resource_key", "relationship_type", "target_resource_key", "message"),
    (
        (
            None,
            "references",
            _build_target_key(),
            "invalid cross-domain relationship source resource key",
        ),
        (_build_source_key(), None, _build_target_key(), "invalid cross-domain relationship type"),
        (_build_source_key(), "   ", _build_target_key(), "invalid cross-domain relationship type"),
        (
            _build_source_key(),
            "references",
            None,
            "invalid cross-domain relationship target resource key",
        ),
    ),
)
def test_relationship_validates_fields_in_order(
    source_resource_key: object,
    relationship_type: object,
    target_resource_key: object,
    message: str,
) -> None:
    """Reject malformed relationship values with stable field-order precedence."""
    with pytest.raises(RegistryCoreValidationError, match=f"^{message}$"):
        CrossDomainRelationship(
            cast(ResourceKey, source_resource_key),
            cast(str, relationship_type),
            cast(ResourceKey, target_resource_key),
        )


def test_relationship_registry_rejects_invalid_dependencies_and_duplicate_tuples() -> None:
    """Fail closed for malformed registries and exact directed identity collisions."""
    relationship = _build_relationship()

    with pytest.raises(
        RegistryCoreValidationError, match="^invalid cross-domain relationship type registry$"
    ):
        CrossDomainRelationshipRegistry(None, _build_namespace_registry(), ())  # type: ignore[arg-type]
    with pytest.raises(
        RegistryCoreValidationError, match="^invalid cross-domain relationship namespace registry$"
    ):
        CrossDomainRelationshipRegistry(_build_type_registry(), None, ())  # type: ignore[arg-type]
    with pytest.raises(RegistryCoreValidationError, match="^duplicate cross-domain relationship$"):
        _build_registry((relationship, relationship))


def test_relationship_registry_rejects_unknown_endpoint_type_and_namespace() -> None:
    """Admit each endpoint independently through completed public module boundaries."""
    unknown_type_target = ResourceKey(
        _build_target_key().namespace,
        ResourceTypeKey("atlas.catalog.unknown"),
        "unknown-1",
    )
    unknown_namespace_target = ResourceKey(
        NamespaceKey(UUID("2dbf68a1-9567-4d74-931f-c36c4247f80b")),
        _build_target_key().resource_type,
        "unknown-2",
    )

    with pytest.raises(RegistryCoreValidationError, match="^unknown resource type$"):
        _build_registry(
            (CrossDomainRelationship(_build_source_key(), "references", unknown_type_target),)
        )
    with pytest.raises(RegistryCoreValidationError, match="^unknown namespace$"):
        _build_registry(
            (CrossDomainRelationship(_build_source_key(), "references", unknown_namespace_target),)
        )


def test_relationship_registry_rejects_partial_or_unknown_exact_resolution() -> None:
    """Reject partial relationship identity and exact lookup misses without fallback."""
    relationship = _build_relationship()
    registry = _build_registry()

    with pytest.raises(
        RegistryCoreValidationError, match="^invalid cross-domain relationship type$"
    ):
        registry.resolve_relationship(
            relationship.source_resource_key, "", relationship.target_resource_key
        )
    with pytest.raises(RegistryCoreValidationError, match="^unknown cross-domain relationship$"):
        registry.resolve_relationship(
            relationship.source_resource_key, "uses", relationship.target_resource_key
        )


def test_relationship_module_preserves_the_framework_independent_dependency_boundary() -> None:
    """Keep structural relationships independent of Django and domain runtime modules."""
    module_source = _MODULE_SOURCE_PATH.read_text(encoding="utf-8")
    imported_modules = {
        node.names[0].name.split(".")[0]
        for node in ast.walk(ast.parse(module_source))
        if isinstance(node, ast.Import)
    }
    imported_modules.update(
        node.module.split(".")[0]
        for node in ast.walk(ast.parse(module_source))
        if isinstance(node, ast.ImportFrom) and node.module is not None
    )

    assert "django" not in imported_modules
    assert "resource_registry.models" not in module_source
    assert "resource_registry.services" not in module_source
    assert "resource_registry.events" not in module_source
