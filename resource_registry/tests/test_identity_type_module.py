"""Verify the ERR-1.1-S1 Identity/Type Module contracts."""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from uuid import UUID

import pytest

from resource_registry.identity_type_module import (
    AliasLifecycle,
    ResourceAlias,
    ResourceAliasRegistry,
    ResourceTypeDeclaration,
    ResourceTypeRegistry,
)
from resource_registry.registry_core import (
    NamespaceKey,
    OwnerDescriptor,
    OwnershipClassification,
    RegistryCoreValidationError,
    ResourceKey,
    ResourceTypeKey,
)

_MODULE_SOURCE_PATH = Path(__file__).resolve().parents[1] / "identity_type_module.py"


def _build_resource_type_declaration() -> ResourceTypeDeclaration:
    """Build a valid Domain-backed resource type declaration.

    Returns:
        A valid immutable resource type declaration.
    """
    return ResourceTypeDeclaration(
        resource_type=ResourceTypeKey("atlas.catalog.document"),
        owner_descriptor=OwnerDescriptor("document-catalog", OwnershipClassification.DOMAIN_BACKED),
    )


def _build_resource_key() -> ResourceKey:
    """Build a resource key matching the valid type declaration.

    Returns:
        A valid resource key for the declared type.
    """
    return ResourceKey(
        NamespaceKey(UUID("8dbf68a1-9567-4d74-931f-c36c4247f80b")),
        ResourceTypeKey("atlas.catalog.document"),
        "document-1",
    )


def _build_resource_alias(lifecycle: AliasLifecycle = AliasLifecycle.ACTIVE) -> ResourceAlias:
    """Build a valid exact alias record.

    Args:
        lifecycle: Lifecycle state to assign to the immutable alias.

    Returns:
        A valid resource alias record.
    """
    resource_key = _build_resource_key()
    return ResourceAlias(
        namespace=resource_key.namespace,
        resource_type=resource_key.resource_type,
        alias_kind="slug",
        normalized_value="document-one",
        target_resource=resource_key,
        lifecycle=lifecycle,
    )


def test_identity_type_module_exposes_only_its_public_contracts() -> None:
    """Expose declarations and resolution from the authorized module boundary."""
    from resource_registry import identity_type_module

    assert identity_type_module.__all__ == (
        "AliasLifecycle",
        "ResourceAlias",
        "ResourceAliasRegistry",
        "ResourceTypeDeclaration",
        "ResourceTypeRegistry",
    )
    assert ResourceTypeDeclaration.__module__ == "resource_registry.identity_type_module"
    assert ResourceTypeRegistry.__module__ == "resource_registry.identity_type_module"


def test_resource_alias_resolves_exact_scope_and_preserves_lifecycle() -> None:
    """Resolve only exact active and deprecated aliases without target conversion."""
    active_alias = _build_resource_alias()
    deprecated_alias = _build_resource_alias(AliasLifecycle.DEPRECATED)
    registry = ResourceAliasRegistry((active_alias,))

    assert ResourceAlias.__slots__ == (
        "namespace",
        "resource_type",
        "alias_kind",
        "normalized_value",
        "target_resource",
        "lifecycle",
    )
    assert (
        registry.resolve_resource_alias(
            active_alias.namespace,
            active_alias.resource_type,
            "slug",
            "document-one",
        )
        == active_alias
    )
    assert deprecated_alias.lifecycle is AliasLifecycle.DEPRECATED
    assert hash(active_alias) == hash(_build_resource_alias())
    with pytest.raises(FrozenInstanceError):
        active_alias.normalized_value = "other"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("alias", "message"),
    (
        (None, "invalid resource alias registry alias"),
        ((_build_resource_alias(), _build_resource_alias()), "duplicate resource alias"),
    ),
)
def test_resource_alias_registry_rejects_invalid_and_duplicate_aliases(
    alias: object, message: str
) -> None:
    """Reject malformed alias registries and exact alias collisions deterministically."""
    aliases = alias if isinstance(alias, tuple) else (alias,)
    with pytest.raises(RegistryCoreValidationError, match=f"^{message}$"):
        ResourceAliasRegistry(aliases)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("lifecycle", "message"),
    (
        (AliasLifecycle.TOMBSTONED, "resource alias tombstoned"),
        (AliasLifecycle.ACTIVE, "unknown resource alias"),
    ),
)
def test_resource_alias_registry_fails_closed_for_tombstoned_and_unknown_aliases(
    lifecycle: AliasLifecycle, message: str
) -> None:
    """Reject tombstoned aliases and exact lookup misses without fallback behavior."""
    alias = _build_resource_alias(lifecycle)
    registry = ResourceAliasRegistry((alias,))
    lookup_value = "document-one" if lifecycle is AliasLifecycle.TOMBSTONED else "unknown"

    with pytest.raises(RegistryCoreValidationError, match=f"^{message}$"):
        registry.resolve_resource_alias(alias.namespace, alias.resource_type, "slug", lookup_value)


def test_resource_type_declaration_preserves_immutable_type_and_owner_values() -> None:
    """Construct a structural declaration without resolving an owner at construction time."""
    declaration = _build_resource_type_declaration()

    assert tuple(field.name for field in fields(ResourceTypeDeclaration)) == (
        "resource_type",
        "owner_descriptor",
    )
    assert ResourceTypeDeclaration.__slots__ == ("resource_type", "owner_descriptor")
    assert declaration == _build_resource_type_declaration()
    assert hash(declaration) == hash(_build_resource_type_declaration())
    assert repr(declaration).startswith("ResourceTypeDeclaration(resource_type=ResourceTypeKey(")
    with pytest.raises(FrozenInstanceError):
        declaration.resource_type = ResourceTypeKey("atlas.catalog.other")  # type: ignore[misc]


@pytest.mark.parametrize(
    ("resource_type", "owner_descriptor", "message"),
    (
        (
            None,
            OwnerDescriptor("document-catalog", OwnershipClassification.DOMAIN_BACKED),
            "invalid resource type declaration key",
        ),
        (
            ResourceTypeKey("atlas.catalog.document"),
            None,
            "invalid resource type declaration owner",
        ),
        (None, None, "invalid resource type declaration key"),
    ),
)
def test_resource_type_declaration_validates_components_in_field_order(
    resource_type: object, owner_descriptor: object, message: str
) -> None:
    """Reject invalid declaration components with stable field-order precedence."""
    with pytest.raises(RegistryCoreValidationError, match=f"^{message}$"):
        ResourceTypeDeclaration(resource_type, owner_descriptor)  # type: ignore[arg-type]


def test_resource_type_registry_resolves_exact_type_and_resource_key_owners() -> None:
    """Resolve declarations by exact ResourceTypeKey without allocating domain identities."""
    declaration = _build_resource_type_declaration()
    registry = ResourceTypeRegistry((declaration,))

    assert registry.declarations == (declaration,)
    assert (
        registry.resolve_owner_descriptor(declaration.resource_type) == declaration.owner_descriptor
    )
    assert (
        registry.resolve_resource_owner_descriptor(_build_resource_key())
        == declaration.owner_descriptor
    )
    assert registry == ResourceTypeRegistry((declaration,))
    assert hash(registry) == hash(ResourceTypeRegistry((declaration,)))
    assert ResourceTypeRegistry.__slots__ == ("declarations",)
    with pytest.raises(FrozenInstanceError):
        registry.declarations = ()  # type: ignore[misc]


@pytest.mark.parametrize(
    ("declarations", "message"),
    (
        ([], "invalid resource type registry declarations"),
        ((None,), "invalid resource type registry declaration"),
        (
            (_build_resource_type_declaration(), _build_resource_type_declaration()),
            "duplicate resource type declaration",
        ),
    ),
)
def test_resource_type_registry_rejects_invalid_and_duplicate_declarations(
    declarations: object, message: str
) -> None:
    """Reject malformed declarations and duplicate exact resource types deterministically."""
    with pytest.raises(RegistryCoreValidationError, match=f"^{message}$"):
        ResourceTypeRegistry(declarations)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("resource_type", "message"),
    (
        (None, "invalid resource type resolution key"),
        (ResourceTypeKey("atlas.catalog.unknown"), "unknown resource type"),
    ),
)
def test_resource_type_registry_fails_closed_for_invalid_or_unknown_type(
    resource_type: object, message: str
) -> None:
    """Reject invalid or undeclared type resolution without alias or prefix fallback."""
    registry = ResourceTypeRegistry((_build_resource_type_declaration(),))

    with pytest.raises(RegistryCoreValidationError, match=f"^{message}$"):
        registry.resolve_owner_descriptor(resource_type)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("resource_key", "message"),
    (
        (None, "invalid resource owner resolution key"),
        (
            ResourceKey(
                NamespaceKey("8dbf68a1-9567-4d74-931f-c36c4247f80b"),
                ResourceTypeKey("atlas.catalog.unknown"),
                "document-1",
            ),
            "unknown resource type",
        ),
    ),
)
def test_resource_type_registry_fails_closed_for_invalid_or_unknown_resource_key(
    resource_key: object, message: str
) -> None:
    """Reject invalid resource owner resolution without probing an external owner."""
    registry = ResourceTypeRegistry((_build_resource_type_declaration(),))

    with pytest.raises(RegistryCoreValidationError, match=f"^{message}$"):
        registry.resolve_resource_owner_descriptor(resource_key)  # type: ignore[arg-type]


def test_identity_type_module_has_no_runtime_or_consumer_dependencies() -> None:
    """Keep the authorized module independent of framework, I/O, and consumer behavior."""
    source_tree = ast.parse(_MODULE_SOURCE_PATH.read_text(encoding="utf-8"))
    imported_module_names = {
        imported.name
        for node in ast.walk(source_tree)
        if isinstance(node, ast.Import)
        for imported in node.names
    }
    imported_module_names.update(
        node.module
        for node in ast.walk(source_tree)
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module is not None
    )

    assert imported_module_names == {"__future__", "dataclasses", "enum"}
