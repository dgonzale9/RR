"""Verify pure Catalog Module projection-store contracts."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from resource_registry.catalog_module import (
    CatalogGeneration,
    CatalogGenerationKey,
    CatalogProjectionKey,
    CatalogProjectionRecord,
    CatalogProjectionStore,
)
from resource_registry.identity_type_module import ResourceTypeDeclaration, ResourceTypeRegistry
from resource_registry.namespace_module import NamespaceDeclaration, NamespaceRegistry
from resource_registry.registry_core import (
    NamespaceKey,
    OwnerDescriptor,
    OwnershipClassification,
    ProjectionTombstoneState,
    RegistryCoreValidationError,
    ResourceKey,
    ResourceTypeKey,
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_MODULE_SOURCE_PATH = _REPOSITORY_ROOT / "resource_registry" / "catalog_module.py"
_PROHIBITED_IMPORT_ROOTS = frozenset(
    {
        "django",
        "BotPlatform",
        "BotServer",
        "Embeddings",
        "MCPServer",
        "Organization",
        "resource_registry.api",
        "resource_registry.application",
        "resource_registry.authorization",
        "resource_registry.bifs",
        "resource_registry.events",
        "resource_registry.migrations",
        "resource_registry.models",
        "resource_registry.providers",
        "resource_registry.read",
        "resource_registry.read_auth",
        "resource_registry.services",
        "resource_registry.storage",
        "resource_registry.transformations",
    }
)


def _build_namespace() -> NamespaceKey:
    """Build an admitted namespace fixture.

    Returns:
        An exact Registry namespace key.
    """
    return NamespaceKey(UUID("4dbf68a1-9567-4d74-931f-c36c4247f80b"))


def _build_resource_key(resource_id: str = "catalog-1") -> ResourceKey:
    """Build an exact owner-issued Catalog fixture key.

    Args:
        resource_id: Exact owner-issued resource identifier.

    Returns:
        A valid resource key.
    """
    return ResourceKey(_build_namespace(), ResourceTypeKey("atlas.catalog.document"), resource_id)


def _build_owner_descriptor() -> OwnerDescriptor:
    """Build the declared domain owner for Catalog fixture resources.

    Returns:
        A valid domain-backed owner descriptor.
    """
    return OwnerDescriptor("document-catalog", OwnershipClassification.DOMAIN_BACKED)


def _build_type_registry() -> ResourceTypeRegistry:
    """Build the type registry admitting the Catalog fixture type.

    Returns:
        A valid immutable type registry.
    """
    return ResourceTypeRegistry(
        (
            ResourceTypeDeclaration(
                ResourceTypeKey("atlas.catalog.document"), _build_owner_descriptor()
            ),
        )
    )


def _build_namespace_registry() -> NamespaceRegistry:
    """Build the namespace registry admitting the Catalog fixture namespace.

    Returns:
        A valid immutable namespace registry.
    """
    return NamespaceRegistry((NamespaceDeclaration(_build_namespace(), 42),))


def _build_generation(value: str = "catalog-v1") -> CatalogGeneration:
    """Build an isolated Catalog generation fixture.

    Args:
        value: Exact Catalog generation identifier.

    Returns:
        A valid Catalog generation.
    """
    return CatalogGeneration(CatalogGenerationKey(value), "1")


def _build_projection(
    resource_id: str = "catalog-1",
    generation: CatalogGeneration | None = None,
    tombstone_state: ProjectionTombstoneState = ProjectionTombstoneState.PRESENT,
) -> CatalogProjectionRecord:
    """Build a valid immutable Catalog projection fixture.

    Args:
        resource_id: Exact owner-issued resource identifier.
        generation: Optional explicit generation for the projection.
        tombstone_state: Whether the fixture carries present or tombstone evidence.

    Returns:
        A valid Catalog projection record.
    """
    projected_generation = _build_generation() if generation is None else generation
    tombstoned_at: datetime | None = None
    tombstone_reason_category: str | None = None
    if tombstone_state is ProjectionTombstoneState.TOMBSTONE:
        tombstoned_at = datetime(2026, 7, 29, 12, tzinfo=UTC)
        tombstone_reason_category = "source-deleted"
    return CatalogProjectionRecord(
        generation=projected_generation,
        projection_key=CatalogProjectionKey(f"projection-{resource_id}"),
        resource_key=_build_resource_key(resource_id),
        source_authority=_build_owner_descriptor(),
        projection_revision="projection-r1",
        source_event_position="event-1",
        projected_at=datetime(2026, 7, 29, 11, tzinfo=UTC),
        source_updated_at=datetime(2026, 7, 29, 10, tzinfo=UTC),
        tombstone_state=tombstone_state,
        tombstoned_at=tombstoned_at,
        tombstone_reason_category=tombstone_reason_category,
    )


def _build_store(*projections: CatalogProjectionRecord) -> CatalogProjectionStore:
    """Build a validated pure Catalog store.

    Args:
        projections: Immutable records to store.

    Returns:
        A valid Catalog projection store.
    """
    return CatalogProjectionStore(_build_type_registry(), _build_namespace_registry(), projections)


def test_catalog_module_exports_only_authorized_contracts() -> None:
    """Expose only the Catalog Module's pure public values and store."""
    from resource_registry import catalog_module

    assert catalog_module.__all__ == (
        "CatalogGeneration",
        "CatalogGenerationKey",
        "CatalogProjectionKey",
        "CatalogProjectionRecord",
        "CatalogProjectionStore",
    )
    assert tuple(field.name for field in fields(CatalogProjectionRecord)) == (
        "generation",
        "projection_key",
        "resource_key",
        "source_authority",
        "projection_revision",
        "source_event_position",
        "projected_at",
        "source_updated_at",
        "tombstone_state",
        "tombstoned_at",
        "tombstone_reason_category",
    )


def test_catalog_projection_values_are_immutable_and_preserve_exact_evidence() -> None:
    """Preserve immutable generation, identity, freshness, and source evidence."""
    projection = _build_projection()

    assert projection == _build_projection()
    assert hash(projection) == hash(_build_projection())
    with pytest.raises(FrozenInstanceError):
        projection.projection_revision = "other"  # type: ignore[misc]


def test_catalog_store_resolves_exact_records_and_lists_deterministically() -> None:
    """Resolve only explicit generation/resource pairs and preserve deterministic list order."""
    first_projection = _build_projection("catalog-b")
    second_projection = _build_projection("catalog-a")
    store = _build_store(first_projection, second_projection)

    assert (
        store.resolve_projection(first_projection.generation.key, first_projection.resource_key)
        == first_projection
    )
    assert store.list_projections(first_projection.generation.key) == (
        second_projection,
        first_projection,
    )


def test_catalog_store_isolates_generations_and_tombstones() -> None:
    """Keep identical resource projections isolated and omit tombstones unless requested."""
    first_generation = _build_generation("catalog-v1")
    second_generation = _build_generation("catalog-v2")
    present_projection = _build_projection("catalog-1", first_generation)
    tombstone_projection = _build_projection(
        "catalog-1", second_generation, ProjectionTombstoneState.TOMBSTONE
    )
    store = _build_store(present_projection, tombstone_projection)

    assert store.resolve_projection(first_generation.key, present_projection.resource_key) == (
        present_projection
    )
    assert store.resolve_projection(second_generation.key, tombstone_projection.resource_key) == (
        tombstone_projection
    )
    assert store.list_projections(second_generation.key) == ()
    assert store.list_projections(second_generation.key, include_tombstones=True) == (
        tombstone_projection,
    )


@pytest.mark.parametrize(
    ("factory", "message"),
    (
        (lambda: CatalogGenerationKey(" "), "invalid catalog generation key"),
        (lambda: CatalogProjectionKey(" "), "invalid catalog projection key"),
        (
            lambda: CatalogProjectionRecord(
                generation=_build_generation(),
                projection_key=CatalogProjectionKey("projection-1"),
                resource_key=_build_resource_key(),
                source_authority=_build_owner_descriptor(),
                projection_revision="r1",
                source_event_position="event-1",
                projected_at=datetime(2026, 7, 29, 11),
                source_updated_at=datetime(2026, 7, 29, 10, tzinfo=UTC),
            ),
            "invalid catalog projection projected at",
        ),
        (
            lambda: CatalogProjectionRecord(
                generation=_build_generation(),
                projection_key=CatalogProjectionKey("projection-1"),
                resource_key=_build_resource_key(),
                source_authority=_build_owner_descriptor(),
                projection_revision="r1",
                source_event_position="event-1",
                projected_at=datetime(2026, 7, 29, 11, tzinfo=UTC),
                source_updated_at=datetime(2026, 7, 29, 10, tzinfo=UTC),
                tombstoned_at=datetime(2026, 7, 29, 12, tzinfo=UTC),
            ),
            "present catalog projection has tombstone evidence",
        ),
    ),
)
def test_catalog_projection_values_fail_closed_for_invalid_evidence(
    factory: object,
    message: str,
) -> None:
    """Reject malformed generation, identity, freshness, and tombstone evidence.

    Args:
        factory: Zero-argument callable constructing an invalid Catalog value.
        message: Stable validation message expected from the invalid value.
    """
    with pytest.raises(RegistryCoreValidationError, match=f"^{message}$"):
        factory()  # type: ignore[operator]


def test_catalog_store_rejects_owner_mismatch_and_duplicate_identities() -> None:
    """Reject source-authority mismatches and ambiguous identities within a generation."""
    projection = _build_projection()
    mismatched_projection = CatalogProjectionRecord(
        generation=projection.generation,
        projection_key=projection.projection_key,
        resource_key=projection.resource_key,
        source_authority=OwnerDescriptor("other-owner", OwnershipClassification.DOMAIN_BACKED),
        projection_revision=projection.projection_revision,
        source_event_position=projection.source_event_position,
        projected_at=projection.projected_at,
        source_updated_at=projection.source_updated_at,
    )

    with pytest.raises(
        RegistryCoreValidationError, match="^catalog projection source authority mismatch$"
    ):
        _build_store(mismatched_projection)
    with pytest.raises(RegistryCoreValidationError, match="^duplicate catalog projection key$"):
        _build_store(projection, projection)


def test_catalog_store_rejects_missing_records_and_invalid_list_inputs() -> None:
    """Fail closed for unknown exact records and malformed list filters."""
    projection = _build_projection()
    store = _build_store(projection)

    with pytest.raises(RegistryCoreValidationError, match="^unknown catalog projection$"):
        store.resolve_projection(projection.generation.key, _build_resource_key("missing"))
    with pytest.raises(
        RegistryCoreValidationError, match="^invalid catalog projection tombstone inclusion$"
    ):
        store.list_projections(projection.generation.key, include_tombstones=1)  # type: ignore[arg-type]


def test_catalog_module_has_clean_import_and_no_runtime_dependencies() -> None:
    """Keep the Catalog Module independent of runtime, persistence, and consumer layers."""
    environment = os.environ.copy()
    environment.pop("DJANGO_SETTINGS_MODULE", None)
    process = subprocess.run(
        [sys.executable, "-c", "import resource_registry.catalog_module"],
        cwd=_REPOSITORY_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert process.returncode == 0, process.stderr
    source_tree = ast.parse(_MODULE_SOURCE_PATH.read_text(encoding="utf-8"))
    imported_modules = {
        imported_name.name
        for node in ast.walk(source_tree)
        if isinstance(node, ast.Import)
        for imported_name in node.names
    }
    imported_modules.update(
        node.module
        for node in ast.walk(source_tree)
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module is not None
    )
    assert not any(
        imported_module in _PROHIBITED_IMPORT_ROOTS
        or any(
            imported_module.startswith(f"{prohibited_import_root}.")
            for prohibited_import_root in _PROHIBITED_IMPORT_ROOTS
        )
        for imported_module in imported_modules
    )
