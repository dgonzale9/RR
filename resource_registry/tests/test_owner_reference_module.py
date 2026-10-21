"""Verify ERR-1.7-S1 owner-issued resource-reference protocol contracts."""

from __future__ import annotations

import ast
from pathlib import Path
from uuid import UUID

from resource_registry.owner_reference_module import OwnerIssuedResourceKeyAdapter
from resource_registry.registry_core import NamespaceKey, ResourceKey, ResourceTypeKey

_MODULE_SOURCE_PATH = Path(__file__).resolve().parents[1] / "owner_reference_module.py"


class _ConformingOwnerAdapter:
    """Provide a structural test double for the owner-issued key protocol."""

    declared_resource_type = ResourceTypeKey("atlas.catalog.document")

    def resolve_owner_issued_resource_key(self, owner_resource_id: str) -> ResourceKey:
        """Return a key preserving the supplied exact owner identifier.

        Args:
            owner_resource_id: Exact identifier supplied to the authoritative owner boundary.

        Returns:
            A key whose type and resource ID satisfy the protocol shape.
        """
        return ResourceKey(
            NamespaceKey(UUID("8dbf68a1-9567-4d74-931f-c36c4247f80b")),
            self.declared_resource_type,
            owner_resource_id,
        )


class _NonConformingOwnerAdapter:
    """Provide an incomplete structural test double."""

    declared_resource_type = ResourceTypeKey("atlas.catalog.document")


def test_owner_reference_module_exposes_only_the_owner_issued_key_protocol() -> None:
    """Expose only the approved minimal owner-reference public contract."""
    from resource_registry import owner_reference_module

    assert owner_reference_module.__all__ == ("OwnerIssuedResourceKeyAdapter",)
    assert OwnerIssuedResourceKeyAdapter.__module__ == "resource_registry.owner_reference_module"


def test_owner_issued_key_protocol_accepts_only_conforming_adapter_shapes() -> None:
    """Identify structural adapter conformance without locating or invoking owner services."""
    adapter = _ConformingOwnerAdapter()

    assert isinstance(adapter, OwnerIssuedResourceKeyAdapter)
    assert not isinstance(_NonConformingOwnerAdapter(), OwnerIssuedResourceKeyAdapter)
    resource_key = adapter.resolve_owner_issued_resource_key("document-1")
    assert resource_key.resource_type == adapter.declared_resource_type
    assert resource_key.resource_id == "document-1"


def test_owner_reference_protocol_remains_framework_and_domain_independent() -> None:
    """Keep the shared owner-reference protocol free of infrastructure and domain imports."""
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
    assert "resource_registry.application" not in module_source
    assert "resource_registry.services" not in module_source
    assert "resource_registry.events" not in module_source
