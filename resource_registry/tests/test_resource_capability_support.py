"""Verify ERR-3.2 pure immutable Resource capability-support declarations."""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from pathlib import Path
from uuid import UUID

import pytest

from resource_registry.capability_definitions import (
    CapabilityContractRef,
    CapabilityKey,
    CapabilityVersion,
)
from resource_registry.identity_type_module import ResourceTypeDeclaration, ResourceTypeRegistry
from resource_registry.registry_core import (
    NamespaceKey,
    OwnerDescriptor,
    OwnershipClassification,
    ResourceKey,
    ResourceTypeKey,
)
from resource_registry.resource_capability_support import (
    ResourceCapabilitySupportDeclaration,
    ResourceCapabilitySupportDeclarationSet,
    ResourceCapabilitySupportValidationError,
    ResourceTypeCapabilitySupportDeclaration,
)

_CONTRACTS_PATH = (
    Path(__file__).resolve().parents[1] / "resource_capability_support" / "contracts.py"
)


def _build_resource_type() -> ResourceTypeKey:
    """Build one exact resource type fixture.

    Returns:
        A valid resource type key.
    """
    return ResourceTypeKey("atlas.catalog.document")


def _build_owner_descriptor() -> OwnerDescriptor:
    """Build the authoritative owner for the fixture resource type.

    Returns:
        A valid domain-backed owner descriptor.
    """
    return OwnerDescriptor("document-catalog", OwnershipClassification.DOMAIN_BACKED)


def _build_type_registry() -> ResourceTypeRegistry:
    """Build one exact resource type and owner declaration registry.

    Returns:
        A registry containing the fixture type declaration.
    """
    return ResourceTypeRegistry(
        (ResourceTypeDeclaration(_build_resource_type(), _build_owner_descriptor()),)
    )


def _build_resource() -> ResourceKey:
    """Build one exact resource fixture under the fixture type.

    Returns:
        A valid exact resource key.
    """
    return ResourceKey(
        NamespaceKey(UUID("8dbf68a1-9567-4d74-931f-c36c4247f80b")),
        _build_resource_type(),
        "document-1",
    )


def _build_contract_reference(version: str = "1.0.0") -> CapabilityContractRef:
    """Build one exact portable capability contract fixture.

    Args:
        version: Exact semantic version to include.

    Returns:
        A valid immutable capability contract reference.
    """
    return CapabilityContractRef(
        CapabilityKey("atlas.document.content.read"), CapabilityVersion(version)
    )


def _build_type_declaration() -> ResourceTypeCapabilitySupportDeclaration:
    """Build one valid ResourceType support declaration.

    Returns:
        A valid immutable type support declaration.
    """
    return ResourceTypeCapabilitySupportDeclaration(
        _build_resource_type(), _build_owner_descriptor(), _build_contract_reference()
    )


def _build_resource_declaration() -> ResourceCapabilitySupportDeclaration:
    """Build one valid Resource support declaration.

    Returns:
        A valid immutable resource support declaration.
    """
    return ResourceCapabilitySupportDeclaration(
        _build_resource(), _build_owner_descriptor(), _build_contract_reference()
    )


def test_support_package_exposes_only_approved_pure_contracts() -> None:
    """Expose the approved support declaration vocabulary only."""
    from resource_registry import resource_capability_support

    assert resource_capability_support.__all__ == (
        "ResourceCapabilitySupportValidationError",
        "ResourceTypeCapabilitySupportDeclaration",
        "ResourceCapabilitySupportDeclaration",
        "ResourceCapabilitySupportDeclarationSet",
    )
    assert ResourceTypeCapabilitySupportDeclaration.__slots__ == (
        "resource_type",
        "owner_descriptor",
        "contract_ref",
    )
    assert ResourceCapabilitySupportDeclaration.__slots__ == (
        "resource",
        "owner_descriptor",
        "contract_ref",
    )


def test_declaration_set_admits_exact_resource_type_and_resource_support() -> None:
    """Admit distinct exact type and resource support declarations without resolution."""
    type_declaration = _build_type_declaration()
    resource_declaration = _build_resource_declaration()

    declaration_set = ResourceCapabilitySupportDeclarationSet(
        (type_declaration, resource_declaration), _build_type_registry()
    )

    assert declaration_set.declarations == (type_declaration, resource_declaration)
    assert type_declaration.contract_ref.capability_version == CapabilityVersion("1.0.0")
    assert resource_declaration.resource == _build_resource()
    with pytest.raises(FrozenInstanceError):
        resource_declaration.contract_ref = _build_contract_reference("1.0.1")  # type: ignore[misc]


@pytest.mark.parametrize(
    ("declaration", "message"),
    (
        (None, "invalid capability support declaration"),
        (
            ResourceTypeCapabilitySupportDeclaration(
                ResourceTypeKey("atlas.catalog.unknown"),
                _build_owner_descriptor(),
                _build_contract_reference(),
            ),
            "unknown capability support target type",
        ),
        (
            ResourceTypeCapabilitySupportDeclaration(
                _build_resource_type(),
                OwnerDescriptor("other-owner", OwnershipClassification.DOMAIN_BACKED),
                _build_contract_reference(),
            ),
            "capability support owner mismatch",
        ),
    ),
)
def test_declaration_set_rejects_invalid_or_non_authoritative_declarations(
    declaration: object, message: str
) -> None:
    """Reject malformed declarations, unknown types, and owner mismatches.

    Args:
        declaration: Candidate declaration to validate.
        message: Exact expected validation message.
    """
    with pytest.raises(ResourceCapabilitySupportValidationError, match=f"^{message}$"):
        ResourceCapabilitySupportDeclarationSet((declaration,), _build_type_registry())  # type: ignore[arg-type]


def test_declaration_set_rejects_duplicate_exact_target_and_contract_associations() -> None:
    """Reject duplicate associations while retaining distinct exact contract versions."""
    type_declaration = _build_type_declaration()

    with pytest.raises(
        ResourceCapabilitySupportValidationError, match="^duplicate capability support declaration$"
    ):
        ResourceCapabilitySupportDeclarationSet(
            (type_declaration, type_declaration), _build_type_registry()
        )

    later_version = ResourceTypeCapabilitySupportDeclaration(
        _build_resource_type(), _build_owner_descriptor(), _build_contract_reference("1.0.1")
    )
    declaration_set = ResourceCapabilitySupportDeclarationSet(
        (type_declaration, later_version), _build_type_registry()
    )

    assert declaration_set.declarations == (type_declaration, later_version)


@pytest.mark.parametrize(
    ("target", "owner", "contract", "message"),
    (
        (
            None,
            _build_owner_descriptor(),
            _build_contract_reference(),
            "invalid resource type support target",
        ),
        (
            _build_resource_type(),
            None,
            _build_contract_reference(),
            "invalid resource type support owner",
        ),
        (
            _build_resource_type(),
            _build_owner_descriptor(),
            None,
            "invalid resource type support contract",
        ),
    ),
)
def test_type_declaration_rejects_malformed_components(
    target: object, owner: object, contract: object, message: str
) -> None:
    """Reject malformed ResourceType support declaration components.

    Args:
        target: Candidate ResourceType target.
        owner: Candidate declaring owner.
        contract: Candidate exact capability contract reference.
        message: Exact expected validation message.
    """
    with pytest.raises(ResourceCapabilitySupportValidationError, match=f"^{message}$"):
        ResourceTypeCapabilitySupportDeclaration(target, owner, contract)  # type: ignore[arg-type]


def test_support_contracts_have_no_runtime_or_deferred_story_dependencies() -> None:
    """Keep support declarations independent from runtime and deferred-story boundaries."""
    syntax_tree = ast.parse(_CONTRACTS_PATH.read_text(encoding="utf-8"))
    imported_modules = {
        alias.name.split(".", maxsplit=1)[0]
        for node in ast.walk(syntax_tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_modules.update(
        (node.module or "").split(".", maxsplit=1)[0]
        for node in ast.walk(syntax_tree)
        if isinstance(node, ast.ImportFrom) and node.level == 0
    )

    assert imported_modules.isdisjoint(
        {
            "django",
            "BotPlatform",
            "BotServer",
            "MCPServer",
            "CloudServices",
            "models",
            "providers",
            "bifs",
            "execution",
            "catalog",
            "search",
            "settings",
        }
    )
