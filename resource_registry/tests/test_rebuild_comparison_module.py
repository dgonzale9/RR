"""Verify pure ERR-2.6 rebuild and comparison contracts."""

from __future__ import annotations

import ast
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
from resource_registry.projection_ingestion_module import (
    ProjectionIngestionEvidence,
    ProjectionIngestionSourceKind,
)
from resource_registry.rebuild_comparison_module import (
    ProjectionComparisonBaseline,
    ProjectionDriftKind,
    ProjectionParityFact,
    RebuildComparisonError,
    RebuildComparisonErrorCode,
    RebuildComparisonService,
    RebuildFixture,
    SearchResultComparisonFixture,
)
from resource_registry.registry_core import (
    NamespaceKey,
    OwnerDescriptor,
    OwnershipClassification,
    ProjectionTombstoneState,
    ResourceKey,
    ResourceTypeKey,
)
from resource_registry.search_document_module import SearchDocument
from resource_registry.search_provider_module import (
    SearchAuthorizationScopeBinding,
    SearchGenerationKey,
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_MODULE_SOURCE_PATH = _REPOSITORY_ROOT / "resource_registry" / "rebuild_comparison_module.py"
_PROHIBITED_IMPORT_ROOTS = frozenset(
    {
        "django",
        "BotPlatform",
        "BotServer",
        "Embeddings",
        "MCPServer",
        "resource_registry.api",
        "resource_registry.bifs",
        "resource_registry.models",
        "resource_registry.read",
        "resource_registry.read_auth",
        "resource_registry.services",
        "resource_registry.storage",
        "resource_registry.workers",
    }
)


def _build_namespace() -> NamespaceKey:
    """Build an admitted Namespace fixture.

    Returns:
        A valid exact Namespace key.
    """
    return NamespaceKey(UUID("5dbf68a1-9567-4d74-931f-c36c4247f80b"))


def _build_resource_key(resource_id: str = "rebuild-1") -> ResourceKey:
    """Build a canonical fixture resource key.

    Args:
        resource_id: Exact owner-issued fixture resource identity.

    Returns:
        A valid resource key.
    """
    return ResourceKey(_build_namespace(), ResourceTypeKey("atlas.catalog.document"), resource_id)


def _build_owner() -> OwnerDescriptor:
    """Build the declared source owner fixture.

    Returns:
        A valid domain-backed owner descriptor.
    """
    return OwnerDescriptor("document-catalog", OwnershipClassification.DOMAIN_BACKED)


def _build_store() -> CatalogProjectionStore:
    """Build an empty Catalog store that admits fixture projections.

    Returns:
        A valid empty Catalog projection store.
    """
    resource_type = ResourceTypeKey("atlas.catalog.document")
    return CatalogProjectionStore(
        ResourceTypeRegistry((ResourceTypeDeclaration(resource_type, _build_owner()),)),
        NamespaceRegistry((NamespaceDeclaration(_build_namespace(), 42),)),
        (),
    )


def _build_fixture(
    catalog_generation_value: str = "catalog-rebuild-v1",
    search_generation_value: str = "search-rebuild-v1",
) -> RebuildFixture:
    """Build one valid isolated rebuild fixture.

    Args:
        catalog_generation_value: Exact Catalog target-generation identifier.
        search_generation_value: Exact Search target-generation identifier.

    Returns:
        A valid immutable rebuild fixture.
    """
    generation = CatalogGeneration(CatalogGenerationKey(catalog_generation_value), "1")
    projection = CatalogProjectionRecord(
        generation=generation,
        projection_key=CatalogProjectionKey("projection-rebuild-1"),
        resource_key=_build_resource_key(),
        source_authority=_build_owner(),
        projection_revision="revision-1",
        source_event_position="event-1",
        projected_at=datetime(2026, 7, 29, 11, tzinfo=UTC),
        source_updated_at=datetime(2026, 7, 29, 10, tzinfo=UTC),
        tombstone_state=ProjectionTombstoneState.PRESENT,
    )
    evidence = ProjectionIngestionEvidence(
        "event-1",
        "idempotency-1",
        ProjectionIngestionSourceKind.SNAPSHOT,
        1,
        generation.key,
        projection,
    )
    document = SearchDocument("document-rebuild-1", projection, "search-schema-v1", ())
    return RebuildFixture(
        "snapshot-20260729",
        _build_store(),
        generation.key,
        SearchGenerationKey(search_generation_value),
        "search-schema-v1",
        (evidence,),
        (document,),
    )


def test_rebuild_fixture_is_deterministic_and_does_not_activate_generations() -> None:
    """Build equal fixture evidence without changing aliases or runtime state."""
    service = RebuildComparisonService()

    first_result = service.rebuild_fixture(_build_fixture())
    second_result = service.rebuild_fixture(_build_fixture())

    assert first_result.report == second_result.report
    assert first_result.report.is_parity is True
    assert first_result.report.projection_count == 1
    assert first_result.report.tombstone_count == 0


def test_comparison_reports_count_freshness_and_checksum_drift() -> None:
    """Report bounded drift categories without returning projection details."""
    service = RebuildComparisonService()
    result = service.rebuild_fixture(_build_fixture())
    baseline = ProjectionComparisonBaseline(
        result.fixture.catalog_generation,
        result.fixture.search_generation,
        "0" * 64,
        2,
        0,
        datetime(2026, 7, 29, 9, tzinfo=UTC),
        result.facts,
    )

    report = service.compare_rebuild(baseline, result)

    assert report.is_parity is False
    assert tuple(finding.kind for finding in report.findings) == (
        ProjectionDriftKind.COUNT_MISMATCH,
        ProjectionDriftKind.FRESHNESS_MISMATCH,
        ProjectionDriftKind.CHECKSUM_MISMATCH,
    )
    assert all(finding.resource_key is None for finding in report.findings)


def test_comparison_reports_resource_revision_tombstone_and_freshness_drift() -> None:
    """Report exact safe resource differences without returning owner details."""
    service = RebuildComparisonService()
    result = service.rebuild_fixture(_build_fixture())
    candidate_fact = result.facts[0]
    baseline = ProjectionComparisonBaseline(
        result.fixture.catalog_generation,
        result.fixture.search_generation,
        result.report.checksum,
        result.report.projection_count,
        result.report.tombstone_count,
        result.report.freshness_at,
        (
            ProjectionParityFact(
                candidate_fact.resource_key,
                "expected-revision",
                True,
                datetime(2026, 7, 29, 9, tzinfo=UTC),
            ),
        ),
    )

    report = service.compare_rebuild(baseline, result)

    assert tuple(finding.kind for finding in report.findings) == (
        ProjectionDriftKind.REVISION_MISMATCH,
        ProjectionDriftKind.TOMBSTONE_MISMATCH,
        ProjectionDriftKind.FRESHNESS_MISMATCH,
    )
    assert all(finding.resource_key == candidate_fact.resource_key for finding in report.findings)


def test_comparison_rejects_cross_generation_context() -> None:
    """Reject a baseline that cannot compare the candidate generation pair."""
    service = RebuildComparisonService()
    result = service.rebuild_fixture(_build_fixture())
    baseline = ProjectionComparisonBaseline(
        CatalogGenerationKey("other-catalog"),
        result.fixture.search_generation,
        result.report.checksum,
        result.report.projection_count,
        result.report.tombstone_count,
        result.report.freshness_at,
    )

    with pytest.raises(RebuildComparisonError) as error:
        service.compare_rebuild(baseline, result)

    assert error.value.code is RebuildComparisonErrorCode.CONTEXT_MISMATCH


def test_result_comparison_requires_identical_safe_context() -> None:
    """Reject differing scope evidence and report only order drift for equal contexts."""
    service = RebuildComparisonService()
    expected = SearchResultComparisonFixture(
        "query-fixture-1",
        SearchAuthorizationScopeBinding("scope:sha256:" + "a" * 64),
        SearchGenerationKey("search-rebuild-v1"),
        "search-schema-v1",
        ("resource_id",),
        (_build_resource_key("one"), _build_resource_key("two")),
    )
    reordered = SearchResultComparisonFixture(
        "query-fixture-1",
        SearchAuthorizationScopeBinding("scope:sha256:" + "a" * 64),
        SearchGenerationKey("search-rebuild-v1"),
        "search-schema-v1",
        ("resource_id",),
        (_build_resource_key("two"), _build_resource_key("one")),
    )

    finding = service.compare_result_references(expected, reordered)

    assert finding is not None
    assert finding.kind is ProjectionDriftKind.RESULT_ORDER_MISMATCH
    incompatible = SearchResultComparisonFixture(
        "query-fixture-1",
        SearchAuthorizationScopeBinding("scope:sha256:" + "b" * 64),
        SearchGenerationKey("search-rebuild-v1"),
        "search-schema-v1",
        ("resource_id",),
        expected.resource_keys,
    )
    with pytest.raises(RebuildComparisonError) as error:
        service.compare_result_references(expected, incompatible)
    assert error.value.code is RebuildComparisonErrorCode.CONTEXT_MISMATCH


def test_rebuild_module_has_no_runtime_or_domain_imports() -> None:
    """Keep ERR-2.6 independent from runtime, provider, and domain adoption layers."""
    module_tree = ast.parse(_MODULE_SOURCE_PATH.read_text(encoding="utf-8"))
    imported_modules = {
        alias.name
        for node in ast.walk(module_tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_modules.update(
        node.module
        for node in ast.walk(module_tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    )

    for imported_module in imported_modules:
        assert not any(
            imported_module == prohibited_root or imported_module.startswith(f"{prohibited_root}.")
            for prohibited_root in _PROHIBITED_IMPORT_ROOTS
        )
