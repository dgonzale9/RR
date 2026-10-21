"""Verify pure provider-neutral SearchDocument builder contracts."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from dataclasses import FrozenInstanceError
from dataclasses import fields as dataclass_fields
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from resource_registry.catalog_module import (
    CatalogGeneration,
    CatalogGenerationKey,
    CatalogProjectionKey,
    CatalogProjectionRecord,
)
from resource_registry.registry_core import (
    NamespaceKey,
    OwnerDescriptor,
    OwnershipClassification,
    ProjectionTombstoneState,
    RegistryCoreValidationError,
    ResourceKey,
    ResourceTypeKey,
)
from resource_registry.search_document_module import (
    SearchDocumentBuilder,
    SearchDocumentBuildStatus,
    SearchDocumentField,
    SearchDocumentFieldKind,
    SearchDocumentSchema,
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_MODULE_SOURCE_PATH = _REPOSITORY_ROOT / "resource_registry" / "search_document_module.py"
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
        "resource_registry.workers",
    }
)


def _build_projection(
    projection_schema_version: str = "1",
    tombstone_state: ProjectionTombstoneState = ProjectionTombstoneState.PRESENT,
    generation_key: str = "catalog-v1",
) -> CatalogProjectionRecord:
    """Build immutable Catalog evidence for a generic admitted resource type.

    Args:
        projection_schema_version: Exact Catalog projection schema version.
        tombstone_state: Whether the projection carries removal evidence.
        generation_key: Exact isolated Catalog generation identity.

    Returns:
        A valid Catalog projection without a domain adapter.
    """
    tombstoned_at: datetime | None = None
    tombstone_reason_category: str | None = None
    if tombstone_state is ProjectionTombstoneState.TOMBSTONE:
        tombstoned_at = datetime(2026, 7, 29, 12, tzinfo=UTC)
        tombstone_reason_category = "source-deleted"
    return CatalogProjectionRecord(
        CatalogGeneration(CatalogGenerationKey(generation_key), projection_schema_version),
        CatalogProjectionKey("catalog-projection-1"),
        ResourceKey(
            NamespaceKey(UUID("6dbf68a1-9567-4d74-931f-c36c4247f80b")),
            ResourceTypeKey("atlas.catalog.document"),
            "document-1",
        ),
        OwnerDescriptor("document-catalog", OwnershipClassification.DOMAIN_BACKED),
        "revision-1",
        "event-1",
        datetime(2026, 7, 29, 11, tzinfo=UTC),
        datetime(2026, 7, 29, 10, tzinfo=UTC),
        tombstone_state,
        tombstoned_at,
        tombstone_reason_category,
    )


def _build_schema(
    fields: tuple[tuple[str, SearchDocumentFieldKind], ...] = (
        ("title", SearchDocumentFieldKind.TEXT),
        ("classification", SearchDocumentFieldKind.FILTER),
        ("category", SearchDocumentFieldKind.FACET),
        ("updated", SearchDocumentFieldKind.SORT),
        ("score", SearchDocumentFieldKind.RANKING),
        ("visibility", SearchDocumentFieldKind.SECURITY),
    ),
) -> SearchDocumentSchema:
    """Build an exact approved schema for the generic fixture type.

    Args:
        fields: Exact approved field name/kind pairs.

    Returns:
        An immutable schema declaration.
    """
    return SearchDocumentSchema(ResourceTypeKey("atlas.catalog.document"), "1", fields)


def _build_field(name: str, kind: SearchDocumentFieldKind, value: str) -> SearchDocumentField:
    """Build safe field evidence with explicit owner-field provenance.

    Args:
        name: Exact schema-approved name.
        kind: Exact schema-approved classification.
        value: Bounded safe value.

    Returns:
        An immutable SearchDocument field.
    """
    return SearchDocumentField(name, kind, value, f"owner.{name}")


def test_search_document_builds_deterministically_and_preserves_catalog_evidence() -> None:
    """Build only declared fields while retaining exact non-authoritative Catalog evidence."""
    builder = SearchDocumentBuilder((_build_schema(),))
    projection = _build_projection()
    fields = (
        _build_field("title", SearchDocumentFieldKind.TEXT, "Safe title"),
        _build_field("classification", SearchDocumentFieldKind.FILTER, "internal"),
        _build_field("visibility", SearchDocumentFieldKind.SECURITY, "namespace:read"),
    )

    document = builder.build_document(projection, "search-v1", fields)
    same_document = builder.build_document(projection, "search-v1", fields)

    assert document.document_id == same_document.document_id
    assert (
        document.document_id
        != builder.build_document(
            _build_projection(generation_key="catalog-v2"), "search-v1", fields
        ).document_id
    )
    assert document.projection is projection
    assert document.projection.resource_key == projection.resource_key
    assert document.projection.source_authority == projection.source_authority
    assert document.projection.source_event_position == "event-1"
    assert document.fields == fields
    assert tuple(field.name for field in dataclass_fields(document)) == (
        "document_id",
        "projection",
        "index_schema_generation",
        "fields",
    )
    with pytest.raises(FrozenInstanceError):
        document.document_id = "replacement"  # type: ignore[misc]


def test_search_document_rejects_unknown_or_unapproved_evidence_and_quarantines_it() -> None:
    """Reject schema ambiguity and classify unsafe field or tombstone evidence as quarantined."""
    builder = SearchDocumentBuilder((_build_schema(),))
    unapproved_field = (_build_field("private_payload", SearchDocumentFieldKind.TEXT, "secret"),)

    result = builder.build_document_result(_build_projection(), "search-v1", unapproved_field)
    unknown_schema = builder.build_document_result(_build_projection("2"), "search-v1", ())
    tombstone = builder.build_document_result(
        _build_projection(tombstone_state=ProjectionTombstoneState.TOMBSTONE),
        "search-v1",
        (_build_field("title", SearchDocumentFieldKind.TEXT, "removed"),),
    )

    assert result.status is SearchDocumentBuildStatus.QUARANTINED
    assert unknown_schema.status is SearchDocumentBuildStatus.QUARANTINED
    assert tombstone.status is SearchDocumentBuildStatus.QUARANTINED
    assert (
        builder.build_document(
            _build_projection(tombstone_state=ProjectionTombstoneState.TOMBSTONE), "search-v1", ()
        ).fields
        == ()
    )


def test_search_document_classifies_bound_failures_without_silent_truncation() -> None:
    """Classify text, facet, and canonical document-size limits as oversize outcomes."""
    text_fields = tuple((f"text-{index}", SearchDocumentFieldKind.TEXT) for index in range(9))
    text_builder = SearchDocumentBuilder((_build_schema(text_fields),))
    text_result = text_builder.build_document_result(
        _build_projection(),
        "search-v1",
        tuple(_build_field(name, kind, "t." * 1024) for name, kind in text_fields),
    )
    facet_fields = tuple((f"facet-{index}", SearchDocumentFieldKind.FACET) for index in range(101))
    facet_builder = SearchDocumentBuilder((_build_schema(facet_fields),))
    facet_result = facet_builder.build_document_result(
        _build_projection(),
        "search-v1",
        tuple(_build_field(name, kind, "category") for name, kind in facet_fields),
    )
    document_fields = tuple(
        (f"filter-{index}", SearchDocumentFieldKind.FILTER) for index in range(128)
    )
    document_builder = SearchDocumentBuilder((_build_schema(document_fields),))
    document_result = document_builder.build_document_result(
        _build_projection(),
        "search-v1",
        tuple(_build_field(name, kind, "v." * 300) for name, kind in document_fields),
    )

    assert text_result.status is SearchDocumentBuildStatus.OVERSIZE
    assert facet_result.status is SearchDocumentBuildStatus.OVERSIZE
    assert document_result.status is SearchDocumentBuildStatus.OVERSIZE
    assert text_result.document is None
    assert facet_result.document is None
    assert document_result.document is None


def test_search_document_rejects_invalid_field_values_and_result_conflicts() -> None:
    """Reject malformed values before they can form safe projection output."""
    with pytest.raises(RegistryCoreValidationError, match="^invalid search document field value$"):
        _build_field("title", SearchDocumentFieldKind.TEXT, "x" * 2049)
    with pytest.raises(RegistryCoreValidationError, match="^invalid search document schema field$"):
        SearchDocumentSchema(
            ResourceTypeKey("atlas.catalog.document"),
            "1",
            (("title", "text"),),  # type: ignore[arg-type]
        )
    with pytest.raises(RegistryCoreValidationError, match="^invalid search document schema field$"):
        SearchDocumentSchema(
            ResourceTypeKey("atlas.catalog.document"),
            "1",
            (("title",),),  # type: ignore[arg-type]
        )
    with pytest.raises(RegistryCoreValidationError, match="^invalid search document source field$"):
        SearchDocumentField("title", SearchDocumentFieldKind.TEXT, "x", "p" * 2049)


@pytest.mark.parametrize(
    ("name", "kind", "value", "source_field"),
    (
        ("title", SearchDocumentFieldKind.TEXT, "api_key=not-safe", "owner.title"),
        ("title", SearchDocumentFieldKind.TEXT, "c2VjcmV0LXZhbHVl", "owner.title"),
        ("title", SearchDocumentFieldKind.TEXT, "safe", "owner.api_key"),
        (
            "visibility",
            SearchDocumentFieldKind.SECURITY,
            "user:alice,group:administrators",
            "owner.visibility",
        ),
    ),
)
def test_search_document_rejects_sensitive_or_non_reference_content(
    name: str,
    kind: SearchDocumentFieldKind,
    value: str,
    source_field: str,
) -> None:
    """Reject secret, base64, ACL, and unsafe-source content before document construction.

    Args:
        name: Candidate schema-approved field name.
        kind: Candidate field classification.
        value: Candidate unsafe field value.
        source_field: Candidate unsafe owner-field provenance identifier.
    """
    with pytest.raises(RegistryCoreValidationError, match="^unsafe search document field content$"):
        SearchDocumentField(name, kind, value, source_field)


def test_search_document_rejects_sensitive_schema_field_names() -> None:
    """Reject a schema that could admit an ACL or secret-oriented field."""
    with pytest.raises(RegistryCoreValidationError, match="^invalid search document schema field$"):
        _build_schema((("full_acl", SearchDocumentFieldKind.SECURITY),))


def test_search_document_module_has_clean_import_and_no_runtime_dependencies() -> None:
    """Keep SearchDocument contracts independent of providers, persistence, and runtime layers."""
    environment = os.environ.copy()
    environment.pop("DJANGO_SETTINGS_MODULE", None)
    process = subprocess.run(
        [sys.executable, "-c", "import resource_registry.search_document_module"],
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
