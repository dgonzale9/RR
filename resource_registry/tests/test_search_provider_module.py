"""Verify pure provider-neutral Search port and generation contracts."""

from __future__ import annotations

import ast
import base64
import hashlib
import hmac
import os
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
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
    SearchDocument,
    SearchDocumentField,
    SearchDocumentFieldKind,
)
from resource_registry.search_provider_module import (
    InMemorySearchProvider,
    SearchAliasKey,
    SearchAuthorizationScopeBinding,
    SearchDocumentWrite,
    SearchGenerationKey,
    SearchProviderCapability,
    SearchProviderError,
    SearchProviderErrorCode,
    SearchQuery,
    SearchQueryFilter,
    SearchQueryResult,
    SearchResultHit,
    SearchSortDirection,
    SearchSortField,
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_MODULE_SOURCE_PATH = _REPOSITORY_ROOT / "resource_registry" / "search_provider_module.py"
_SCOPE_FINGERPRINT = f"scope:sha256:{'a' * 64}"
_OTHER_SCOPE_FINGERPRINT = f"scope:sha256:{'b' * 64}"
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


def _build_document(
    document_number: int,
    schema_generation: str = "search-v1",
    tombstone: bool = False,
) -> SearchDocument:
    """Build safe generic Search evidence without a domain adapter."""
    projection = CatalogProjectionRecord(
        CatalogGeneration(CatalogGenerationKey("catalog-v1"), "1"),
        CatalogProjectionKey(f"projection-{document_number}"),
        ResourceKey(
            NamespaceKey(UUID("6dbf68a1-9567-4d74-931f-c36c4247f80b")),
            ResourceTypeKey("atlas.catalog.document"),
            f"document-{document_number}",
        ),
        OwnerDescriptor("document-catalog", OwnershipClassification.DOMAIN_BACKED),
        "revision-1",
        "event-1",
        datetime(2026, 7, 29, 11, tzinfo=UTC),
        datetime(2026, 7, 29, 10, tzinfo=UTC),
        ProjectionTombstoneState.TOMBSTONE if tombstone else ProjectionTombstoneState.PRESENT,
        datetime(2026, 7, 29, 12, tzinfo=UTC) if tombstone else None,
        "source-deleted" if tombstone else None,
    )
    fields = (
        ()
        if tombstone
        else (
            SearchDocumentField(
                "title",
                SearchDocumentFieldKind.TEXT,
                f"Atlas document {document_number}",
                "owner.title",
            ),
            SearchDocumentField(
                "classification", SearchDocumentFieldKind.FILTER, "internal", "owner.classification"
            ),
            SearchDocumentField(
                "updated",
                SearchDocumentFieldKind.SORT,
                f"2026-07-2{document_number}",
                "owner.updated",
            ),
        )
    )
    return SearchDocument(f"document-id-{document_number}", projection, schema_generation, fields)


def _build_active_provider(
    indexed_at_clock: Callable[[], datetime] | None = None,
    query_failure: SearchProviderErrorCode | None = None,
    return_partial: bool = False,
) -> tuple[InMemorySearchProvider, SearchAliasKey, SearchGenerationKey]:
    """Build one active generation with three deterministic fixture documents."""
    provider = InMemorySearchProvider(indexed_at_clock, query_failure, return_partial)
    generation = SearchGenerationKey("generation-v1")
    alias = SearchAliasKey("catalog-search")
    provider.create_generation(generation, "search-v1")
    provider.begin_generation(generation)
    for document_number in range(1, 4):
        provider.index_document(
            generation,
            _build_document(document_number),
            document_number,
            f"write-{document_number}",
        )
    provider.validate_generation(generation)
    provider.activate_generation(alias, generation)
    return provider, alias, generation


def test_provider_indexes_queries_and_paginates_deterministically() -> None:
    """Return deterministic lexical/filter candidates through one explicit alias."""
    indexed_at = datetime(2026, 7, 29, 13, tzinfo=UTC)
    provider, alias, generation = _build_active_provider(lambda: indexed_at)
    scope = SearchAuthorizationScopeBinding(_SCOPE_FINGERPRINT)
    query = SearchQuery(
        "atlas",
        (SearchQueryFilter("classification", "internal"),),
        (SearchSortField("updated", SearchSortDirection.DESCENDING),),
        page_size=2,
    )

    first_page = provider.search(alias, query, scope)
    second_page = provider.search(
        alias,
        SearchQuery(
            query.text, query.filters, query.sort_fields, query.page_size, first_page.next_cursor
        ),
        scope,
    )

    assert first_page.generation == generation
    assert tuple(hit.document_id for hit in first_page.hits) == ("document-id-3", "document-id-2")
    assert first_page.hits[0].resource_key.resource_id == "document-3"
    assert first_page.hits[0].projection_key == "projection-3"
    assert first_page.hits[0].source_revision == "revision-1"
    assert first_page.hits[0].source_event_position == "event-1"
    assert first_page.hits[0].projected_at == datetime(2026, 7, 29, 11, tzinfo=UTC)
    assert first_page.hits[0].indexed_at == indexed_at
    assert tuple(hit.document_id for hit in second_page.hits) == ("document-id-1",)
    assert second_page.next_cursor is None


def test_provider_rejects_cursor_scope_query_and_tampering() -> None:
    """Fail closed when an opaque cursor crosses scope, query, or integrity evidence."""
    provider, alias, _ = _build_active_provider()
    scope = SearchAuthorizationScopeBinding(_SCOPE_FINGERPRINT)
    first_page = provider.search(alias, SearchQuery("atlas", page_size=1), scope)
    assert first_page.next_cursor is not None

    with pytest.raises(SearchProviderError, match="^stale_or_invalid_cursor$"):
        provider.search(
            alias, SearchQuery("different", page_size=1, cursor=first_page.next_cursor), scope
        )
    with pytest.raises(SearchProviderError, match="^stale_or_invalid_cursor$"):
        provider.search(
            alias,
            SearchQuery("atlas", page_size=1, cursor=first_page.next_cursor),
            SearchAuthorizationScopeBinding(_OTHER_SCOPE_FINGERPRINT),
        )
    with pytest.raises(SearchProviderError, match="^stale_or_invalid_cursor$"):
        provider.search(
            alias, SearchQuery("atlas", page_size=1, cursor=f"{first_page.next_cursor}x"), scope
        )


def test_provider_rejects_forged_and_signed_non_object_cursors() -> None:
    """Reject cursors signed with another key and signed payloads without an object root."""
    provider, alias, _ = _build_active_provider()
    scope = SearchAuthorizationScopeBinding(_SCOPE_FINGERPRINT)
    first_page = provider.search(alias, SearchQuery("atlas", page_size=1), scope)
    assert first_page.next_cursor is not None
    payload, _ = base64.urlsafe_b64decode(first_page.next_cursor.encode("ascii")).rsplit(b".", 1)
    forged_signature = (
        hmac.new(b"public-module-secret", payload, hashlib.sha256).hexdigest().encode("ascii")
    )
    forged_cursor = base64.urlsafe_b64encode(payload + b"." + forged_signature).decode("ascii")
    signed_list_payload = b"[]"
    signed_list_signature = (
        hmac.new(provider._cursor_secret, signed_list_payload, hashlib.sha256)
        .hexdigest()
        .encode("ascii")
    )
    signed_list_cursor = base64.urlsafe_b64encode(
        signed_list_payload + b"." + signed_list_signature
    ).decode("ascii")

    for cursor in (forged_cursor, signed_list_cursor):
        with pytest.raises(SearchProviderError, match="^stale_or_invalid_cursor$"):
            provider.search(alias, SearchQuery("atlas", page_size=1, cursor=cursor), scope)


def test_provider_rejects_security_field_filters_and_tombstones_with_content() -> None:
    """Keep caller filters out of security fields and reject malformed tombstone documents."""
    provider = InMemorySearchProvider()
    generation = SearchGenerationKey("generation-security")
    alias = SearchAliasKey("catalog-search")
    ordinary_document = _build_document(1)
    security_document = SearchDocument(
        ordinary_document.document_id,
        ordinary_document.projection,
        ordinary_document.index_schema_generation,
        (
            *ordinary_document.fields,
            SearchDocumentField(
                "visibility", SearchDocumentFieldKind.SECURITY, "namespace:read", "owner.visibility"
            ),
            SearchDocumentField(
                "visibility", SearchDocumentFieldKind.FILTER, "namespace:read", "owner.visibility"
            ),
        ),
    )
    tombstone = _build_document(2, tombstone=True)
    unsafe_tombstone = SearchDocument(
        tombstone.document_id,
        tombstone.projection,
        tombstone.index_schema_generation,
        ordinary_document.fields,
    )
    provider.create_generation(generation, "search-v1")
    provider.begin_generation(generation)
    provider.index_document(generation, security_document, 1, "security-document")
    with pytest.raises(SearchProviderError, match="^validation$"):
        provider.index_document(generation, unsafe_tombstone, 2, "unsafe-tombstone")
    provider.validate_generation(generation)
    provider.activate_generation(alias, generation)
    with pytest.raises(SearchProviderError, match="^validation$"):
        provider.search(
            alias,
            SearchQuery("atlas", (SearchQueryFilter("visibility", "namespace:read"),)),
            SearchAuthorizationScopeBinding(_SCOPE_FINGERPRINT),
        )


def test_query_contract_rejects_boolean_bounds_and_prohibited_syntax() -> None:
    """Reject malformed scalar values and provider-style query syntax."""
    with pytest.raises(RegistryCoreValidationError, match="^invalid search page size$"):
        SearchQuery("atlas", page_size=True)
    with pytest.raises(RegistryCoreValidationError, match="^invalid search query text$"):
        SearchQuery("*")
    with pytest.raises(RegistryCoreValidationError, match="^invalid search document write$"):
        SearchDocumentWrite(_build_document(1), True, "write")
    with pytest.raises(RegistryCoreValidationError, match="^invalid search sort fields$"):
        SearchQuery(
            "atlas",
            sort_fields=(SearchSortField("updated", SearchSortDirection.ASCENDING),) * 11,
        )
    with pytest.raises(
        RegistryCoreValidationError, match="^invalid search requested capabilities$"
    ):
        SearchQuery(
            "atlas",
            requested_capabilities=(SearchProviderCapability.FACETS,) * 11,
        )


def test_result_contract_rejects_oversized_hits_and_sort_evidence() -> None:
    """Reject public result evidence that exceeds fixed page and sort limits."""
    provider, alias, generation = _build_active_provider()
    hit = provider.search(
        alias, SearchQuery("atlas"), SearchAuthorizationScopeBinding(_SCOPE_FINGERPRINT)
    ).hits[0]
    with pytest.raises(RegistryCoreValidationError, match="^invalid search result hits$"):
        SearchQueryResult((hit,) * 101, None, generation)
    with pytest.raises(RegistryCoreValidationError, match="^invalid search result sort values$"):
        SearchResultHit(
            hit.document_id,
            hit.resource_key,
            hit.projection_key,
            hit.score,
            ("value",) * 11,
            hit.index_schema_generation,
            hit.source_revision,
            hit.source_event_position,
            hit.projected_at,
            hit.source_updated_at,
            hit.indexed_at,
        )


def test_provider_returns_normalized_failures_and_partial_readiness() -> None:
    """Classify unavailable, timeout, and partial fixture outcomes without provider leakage."""
    alias = SearchAliasKey("catalog-search")
    scope = SearchAuthorizationScopeBinding(_SCOPE_FINGERPRINT)
    for failure in (SearchProviderErrorCode.UNAVAILABLE, SearchProviderErrorCode.TIMEOUT):
        provider = InMemorySearchProvider(query_failure=failure)
        with pytest.raises(SearchProviderError, match=f"^{failure.value}$"):
            provider.search(alias, SearchQuery("atlas"), scope)

    provider, alias, _ = _build_active_provider(return_partial=True)
    assert provider.search(alias, SearchQuery("atlas"), scope).is_partial
    assert provider.health(SearchGenerationKey("generation-v1")).is_degraded


def test_provider_rejects_untyped_generation_alias_and_delete_revision() -> None:
    """Reject malformed lifecycle identities and boolean delete revisions before state changes."""
    provider = InMemorySearchProvider()
    with pytest.raises(SearchProviderError, match="^validation$"):
        provider.create_generation("generation-v1", "search-v1")  # type: ignore[arg-type]

    generation = SearchGenerationKey("generation-v1")
    provider.create_generation(generation, "search-v1")
    provider.begin_generation(generation)
    with pytest.raises(SearchProviderError, match="^validation$"):
        provider.delete_document(generation, "document-id", cast(int, True), "delete")
    provider.validate_generation(generation)
    with pytest.raises(SearchProviderError, match="^validation$"):
        provider.activate_generation("catalog-search", generation)  # type: ignore[arg-type]

    with pytest.raises(SearchProviderError, match="^validation$"):
        provider.bulk_index("generation-v1", ())  # type: ignore[arg-type]

    with pytest.raises(SearchProviderError, match="^retired_generation$"):
        provider.bulk_index(SearchGenerationKey("missing"), ())
    bulk_generation = SearchGenerationKey("generation-bulk")
    provider.create_generation(bulk_generation, "search-v1")
    with pytest.raises(SearchProviderError, match="^generation_not_ready$"):
        provider.bulk_index(bulk_generation, ())
    provider.begin_generation(bulk_generation)
    assert provider.bulk_index(bulk_generation, ()) == ()


def test_provider_requires_validated_generation_and_supports_atomic_rollback() -> None:
    """Prevent premature activation and restore the exact retained predecessor alias target."""
    provider, alias, first_generation = _build_active_provider()
    second_generation = SearchGenerationKey("generation-v2")
    provider.create_generation(second_generation, "search-v1")
    with pytest.raises(SearchProviderError, match="^generation_not_ready$"):
        provider.activate_generation(alias, second_generation)
    provider.begin_generation(second_generation)
    provider.index_document(second_generation, _build_document(4), 1, "write-4")
    provider.validate_generation(second_generation)
    provider.activate_generation(alias, second_generation)

    assert (
        provider.search(
            alias, SearchQuery("atlas"), SearchAuthorizationScopeBinding(_SCOPE_FINGERPRINT)
        ).generation
        == second_generation
    )
    provider.rollback_alias(alias)
    assert (
        provider.search(
            alias, SearchQuery("atlas"), SearchAuthorizationScopeBinding(_SCOPE_FINGERPRINT)
        ).generation
        == first_generation
    )


def test_provider_retires_an_older_retained_generation_during_a_third_cutover() -> None:
    """Keep only one rollback predecessor after successive alias activations."""
    provider, alias, first_generation = _build_active_provider()
    second_generation = SearchGenerationKey("generation-v2-retention")
    third_generation = SearchGenerationKey("generation-v3-retention")
    for generation, document_number in ((second_generation, 4), (third_generation, 5)):
        provider.create_generation(generation, "search-v1")
        provider.begin_generation(generation)
        provider.index_document(
            generation, _build_document(document_number), 1, f"write-{document_number}"
        )
        provider.validate_generation(generation)
        provider.activate_generation(alias, generation)

    with pytest.raises(SearchProviderError, match="^retired_generation$"):
        provider.health(first_generation)
    provider.rollback_alias(alias)
    assert (
        provider.search(
            alias, SearchQuery("atlas"), SearchAuthorizationScopeBinding(_SCOPE_FINGERPRINT)
        ).generation
        == second_generation
    )


def test_provider_clears_an_expired_rollback_target_before_later_cutover() -> None:
    """Allow a later cutover after expiring the current retained rollback target."""
    provider, alias, first_generation = _build_active_provider()
    second_generation = SearchGenerationKey("generation-v2-expiry")
    third_generation = SearchGenerationKey("generation-v3-expiry")
    for generation, document_number in ((second_generation, 4), (third_generation, 5)):
        provider.create_generation(generation, "search-v1")
        provider.begin_generation(generation)
        provider.index_document(
            generation, _build_document(document_number), 1, f"write-{document_number}"
        )
        provider.validate_generation(generation)
        if generation == second_generation:
            provider.activate_generation(alias, generation)

    provider.retire_generation(first_generation)
    with pytest.raises(SearchProviderError, match="^generation_not_ready$"):
        provider.rollback_alias(alias)
    provider.activate_generation(alias, third_generation)
    provider.rollback_alias(alias)
    assert (
        provider.search(
            alias, SearchQuery("atlas"), SearchAuthorizationScopeBinding(_SCOPE_FINGERPRINT)
        ).generation
        == second_generation
    )


def test_provider_prevents_stale_writes_and_tombstones_from_disclosing_content() -> None:
    """Keep newer document evidence from regression and remove tombstoned content."""
    provider = InMemorySearchProvider()
    generation = SearchGenerationKey("generation-v1")
    alias = SearchAliasKey("catalog-search")
    document = _build_document(1)
    provider.create_generation(generation, "search-v1")
    provider.begin_generation(generation)
    assert provider.index_document(generation, document, 2, "write-current").applied
    assert not provider.index_document(generation, document, 1, "write-stale").applied
    assert provider.index_document(
        generation, _build_document(1, tombstone=True), 3, "delete-current"
    ).applied
    provider.validate_generation(generation)
    provider.activate_generation(alias, generation)

    result = provider.search(
        alias, SearchQuery("atlas"), SearchAuthorizationScopeBinding(_SCOPE_FINGERPRINT)
    )
    assert result.hits == ()


def test_provider_rejects_schema_conflicts_idempotency_conflicts_and_optional_capabilities() -> (
    None
):
    """Reject incompatible generation/document and optional-capability evidence safely."""
    provider = InMemorySearchProvider()
    generation = SearchGenerationKey("generation-v1")
    provider.create_generation(generation, "search-v1")
    provider.begin_generation(generation)
    with pytest.raises(SearchProviderError, match="^generation_schema_mismatch$"):
        provider.index_document(generation, _build_document(1, "search-v2"), 1, "write-1")
    document = _build_document(1)
    provider.index_document(generation, document, 1, "write-1")
    with pytest.raises(SearchProviderError, match="^integrity$"):
        provider.index_document(generation, document, 2, "write-1")
    provider.validate_generation(generation)
    provider.activate_generation(SearchAliasKey("catalog-search"), generation)
    with pytest.raises(SearchProviderError, match="^unsupported_search_capability$"):
        provider.search(
            SearchAliasKey("catalog-search"),
            SearchQuery("atlas", requested_capabilities=(SearchProviderCapability.FACETS,)),
            SearchAuthorizationScopeBinding(_SCOPE_FINGERPRINT),
        )


def test_provider_supports_bulk_delete_schema_and_rebuild_operations() -> None:
    """Expose the required provider-neutral write and rebuild operations."""
    provider, _, source_generation = _build_active_provider()
    target_generation = SearchGenerationKey("generation-v2")
    with pytest.raises(SearchProviderError, match="^validation$"):
        provider.create_or_update_schema([], "search-v1")  # type: ignore[arg-type]
    with pytest.raises(SearchProviderError, match="^validation$"):
        provider.create_or_update_schema(target_generation, "")
    provider.create_or_update_schema(target_generation, "search-v1")
    provider.begin_generation(target_generation)
    with pytest.raises(SearchProviderError, match="^validation$"):
        provider.index_document(target_generation, _build_document(4), 1, "")
    with pytest.raises(SearchProviderError, match="^validation$"):
        provider.delete_document(target_generation, "", 1, "delete-4")
    with pytest.raises(SearchProviderError, match="^validation$"):
        provider.delete_document(target_generation, "document-id-4", 1, "")
    provider.rebuild_index(source_generation, target_generation)
    bulk_results = provider.bulk_index(
        target_generation,
        (SearchDocumentWrite(_build_document(4), 4, "write-4"),),
    )
    assert bulk_results[0].applied
    assert provider.delete_document(target_generation, "document-id-4", 5, "delete-4").applied
    provider.validate_generation(target_generation)


def test_rebuild_preserves_tombstone_evidence_against_stale_target_writes() -> None:
    """Prevent a rebuilt generation from restoring a document older than a source tombstone."""
    provider = InMemorySearchProvider()
    source_generation = SearchGenerationKey("generation-source")
    target_generation = SearchGenerationKey("generation-target")
    document = _build_document(1)
    provider.create_generation(source_generation, "search-v1")
    provider.begin_generation(source_generation)
    provider.index_document(source_generation, document, 1, "write-current")
    provider.index_document(
        source_generation, _build_document(1, tombstone=True), 2, "delete-current"
    )
    provider.validate_generation(source_generation)
    provider.create_generation(target_generation, "search-v1")
    provider.begin_generation(target_generation)
    provider.rebuild_index(source_generation, target_generation)

    assert not provider.index_document(target_generation, document, 1, "write-stale").applied


def test_rebuild_replaces_a_prepopulated_target_with_the_exact_source_snapshot() -> None:
    """Remove target-only documents that are absent from a tombstoned source snapshot."""
    provider = InMemorySearchProvider()
    source_generation = SearchGenerationKey("generation-source-snapshot")
    target_generation = SearchGenerationKey("generation-target-snapshot")
    alias = SearchAliasKey("catalog-search")
    document = _build_document(1)
    provider.create_generation(source_generation, "search-v1")
    provider.begin_generation(source_generation)
    provider.index_document(source_generation, document, 1, "source-write")
    provider.index_document(
        source_generation, _build_document(1, tombstone=True), 2, "source-delete"
    )
    provider.validate_generation(source_generation)
    provider.create_generation(target_generation, "search-v1")
    provider.begin_generation(target_generation)
    provider.index_document(target_generation, document, 1, "target-stale")
    provider.rebuild_index(source_generation, target_generation)
    provider.validate_generation(target_generation)
    provider.activate_generation(alias, target_generation)

    assert (
        provider.search(
            alias, SearchQuery("atlas"), SearchAuthorizationScopeBinding(_SCOPE_FINGERPRINT)
        ).hits
        == ()
    )


def test_cursor_is_invalidated_after_alias_cutover_and_rollback() -> None:
    """Reject a prior-generation cursor after either logical alias transition."""
    provider, alias, first_generation = _build_active_provider()
    scope = SearchAuthorizationScopeBinding(_SCOPE_FINGERPRINT)
    first_page = provider.search(alias, SearchQuery("atlas", page_size=1), scope)
    assert first_page.next_cursor is not None
    second_generation = SearchGenerationKey("generation-cursor-v2")
    provider.create_generation(second_generation, "search-v1")
    provider.begin_generation(second_generation)
    provider.index_document(second_generation, _build_document(4), 1, "write-v2")
    provider.validate_generation(second_generation)
    provider.activate_generation(alias, second_generation)
    provider.rollback_alias(alias)
    assert provider.search(alias, SearchQuery("atlas"), scope).generation == first_generation
    with pytest.raises(SearchProviderError, match="^stale_or_invalid_cursor$"):
        provider.search(
            alias, SearchQuery("atlas", page_size=1, cursor=first_page.next_cursor), scope
        )


def test_provider_fails_retires_and_rejects_unknown_query_fields() -> None:
    """Reject failed/retired activation and unknown provider-neutral query fields."""
    provider, alias, _ = _build_active_provider()
    failed_generation = SearchGenerationKey("generation-failed")
    provider.create_generation(failed_generation, "search-v1")
    provider.fail_generation(failed_generation)
    with pytest.raises(SearchProviderError, match="^generation_not_ready$"):
        provider.activate_generation(alias, failed_generation)
    provider.retire_generation(failed_generation)
    with pytest.raises(SearchProviderError, match="^retired_generation$"):
        provider.health(failed_generation)
    with pytest.raises(SearchProviderError, match="^validation$"):
        provider.search(
            alias,
            SearchQuery("atlas", (SearchQueryFilter("unknown", "value"),)),
            SearchAuthorizationScopeBinding(_SCOPE_FINGERPRINT),
        )


def test_provider_rejects_untyped_scope_and_equal_revision_delete_conflicts() -> None:
    """Fail closed for malformed scope evidence and conflicting same-revision deletion."""
    provider, alias, _ = _build_active_provider()
    with pytest.raises(SearchProviderError, match="^validation$"):
        provider.search(alias, SearchQuery("atlas"), "untyped-scope")  # type: ignore[arg-type]

    building_generation = SearchGenerationKey("generation-delete-conflict")
    document = _build_document(1)
    provider.create_generation(building_generation, "search-v1")
    provider.begin_generation(building_generation)
    provider.index_document(building_generation, document, 2, "write-current")
    with pytest.raises(SearchProviderError, match="^integrity$"):
        provider.delete_document(
            building_generation,
            document.document_id,
            2,
            "delete-conflict",
        )
    with pytest.raises(SearchProviderError, match="^validation$"):
        provider.search(
            alias,
            SearchQuery(
                "atlas", sort_fields=(SearchSortField("unknown", SearchSortDirection.ASCENDING),)
            ),
            SearchAuthorizationScopeBinding(_SCOPE_FINGERPRINT),
        )


@pytest.mark.parametrize(
    "scope_value",
    (
        "tenant:tenant-123",
        "acl:role=administrator",
        "actor:user-42",
        "policy:classification-read",
        "permission:resource.read",
        "scope:fixture",
        "scope:sha256:invalid",
    ),
)
def test_scope_binding_rejects_authorization_payloads_and_non_fingerprints(
    scope_value: str,
) -> None:
    """Reject authorization payloads and malformed values at the opaque scope boundary."""
    with pytest.raises(
        RegistryCoreValidationError, match="^invalid search authorization scope binding$"
    ):
        SearchAuthorizationScopeBinding(scope_value)


def test_provider_rejects_equal_revision_conflicts_after_a_tombstone() -> None:
    """Reject reverse-order write and tombstone conflicts after a recorded deletion."""
    provider = InMemorySearchProvider()
    generation = SearchGenerationKey("generation-tombstone-conflict")
    tombstone = _build_document(1, tombstone=True)
    provider.create_generation(generation, "search-v1")
    provider.begin_generation(generation)
    provider.index_document(generation, tombstone, 2, "delete-first")

    with pytest.raises(SearchProviderError, match="^integrity$"):
        provider.index_document(generation, _build_document(1), 2, "write-conflict")
    with pytest.raises(SearchProviderError, match="^integrity$"):
        provider.index_document(generation, tombstone, 2, "delete-conflict")


def test_provider_rejects_cross_operation_and_distinct_tombstone_retries() -> None:
    """Allow only identical tombstone retries for retired same-revision evidence."""
    provider = InMemorySearchProvider()
    generation = SearchGenerationKey("generation-cross-operation-conflict")
    tombstone = _build_document(1, tombstone=True)
    provider.create_generation(generation, "search-v1")
    provider.begin_generation(generation)
    provider.index_document(generation, tombstone, 2, "same")
    assert not provider.index_document(generation, tombstone, 2, "same").applied
    with pytest.raises(SearchProviderError, match="^integrity$"):
        provider.delete_document(generation, tombstone.document_id, 2, "same")

    direct_delete_generation = SearchGenerationKey("generation-delete-tombstone-conflict")
    provider.create_generation(direct_delete_generation, "search-v1")
    provider.begin_generation(direct_delete_generation)
    provider.delete_document(direct_delete_generation, tombstone.document_id, 2, "same")
    with pytest.raises(SearchProviderError, match="^integrity$"):
        provider.index_document(direct_delete_generation, tombstone, 2, "same")


def test_provider_allows_only_identical_write_and_direct_delete_retries() -> None:
    """Classify exact same-operation write and delete repeats as idempotent no-ops."""
    provider = InMemorySearchProvider()
    generation = SearchGenerationKey("generation-identical-retries")
    document = _build_document(1)
    provider.create_generation(generation, "search-v1")
    provider.begin_generation(generation)
    assert provider.index_document(generation, document, 2, "write-same").applied
    assert not provider.index_document(generation, document, 2, "write-same").applied
    assert provider.delete_document(generation, document.document_id, 3, "delete-same").applied
    assert not provider.delete_document(generation, document.document_id, 3, "delete-same").applied


def test_provider_module_has_clean_import_and_no_runtime_dependencies() -> None:
    """Keep Search provider contracts independent of runtime and provider implementations."""
    environment = os.environ.copy()
    environment.pop("DJANGO_SETTINGS_MODULE", None)
    process = subprocess.run(
        [sys.executable, "-c", "import resource_registry.search_provider_module"],
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
        or any(imported_module.startswith(f"{root}.") for root in _PROHIBITED_IMPORT_ROOTS)
        for imported_module in imported_modules
    )
