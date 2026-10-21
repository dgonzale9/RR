"""Verify pure fail-closed authorized-discovery orchestration."""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from resource_registry.authorized_discovery_module import (
    AuthorizedDiscoveryRequest,
    AuthorizedDiscoveryService,
    DiscoveryAuthorizationGrant,
    DiscoveryError,
    DiscoveryErrorCode,
    OwnerDiscoverySummary,
)
from resource_registry.registry_core import NamespaceKey, ResourceKey, ResourceTypeKey
from resource_registry.search_provider_module import (
    SearchAliasKey,
    SearchAuthorizationScopeBinding,
    SearchGenerationKey,
    SearchProviderPort,
    SearchQuery,
    SearchQueryResult,
    SearchResultHit,
)

_SCOPE_FINGERPRINT = f"scope:sha256:{'c' * 64}"
_MODULE_SOURCE_PATH = (
    Path(__file__).resolve().parents[2] / "resource_registry" / "authorized_discovery_module.py"
)
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


def _build_candidate(resource_id: str = "document-1") -> SearchResultHit:
    """Build one safe deterministic Search candidate for orchestration tests."""
    timestamp = datetime(2026, 7, 29, 12, tzinfo=UTC)
    return SearchResultHit(
        f"search-{resource_id}",
        ResourceKey(
            NamespaceKey(UUID("6dbf68a1-9567-4d74-931f-c36c4247f80b")),
            ResourceTypeKey("atlas.catalog.document"),
            resource_id,
        ),
        f"projection-{resource_id}",
        1,
        (),
        "search-v1",
        "revision-1",
        "event-1",
        timestamp,
        timestamp,
        timestamp,
    )


class _FixtureAuthorizationPort:
    """Provide deterministic live authorization fixture behavior."""

    def __init__(self, allow_candidates: bool = True, deny_request: bool = False) -> None:
        """Initialize one fixture authorization outcome.

        Args:
            allow_candidates: Whether post-search candidate checks allow discovery.
            deny_request: Whether pre-search authorization denies the request.
        """
        self.allow_candidates = allow_candidates
        self.deny_request = deny_request
        self.authorized_candidate_count = 0

    def authorize_discovery(
        self, request: AuthorizedDiscoveryRequest
    ) -> DiscoveryAuthorizationGrant:
        """Authorize one request or raise a non-enumerable fixture denial.

        Args:
            request: Authority-free discovery intent.

        Returns:
            Server-derived opaque scope evidence.

        Raises:
            PermissionError: If fixture pre-search authorization denies.
        """
        if self.deny_request:
            raise PermissionError("denied")
        return DiscoveryAuthorizationGrant(SearchAuthorizationScopeBinding(_SCOPE_FINGERPRINT))

    def authorize_candidate(
        self,
        grant: DiscoveryAuthorizationGrant,
        candidate: SearchResultHit,
    ) -> bool:
        """Return the fixture post-search authorization outcome.

        Args:
            grant: Server-derived scope evidence.
            candidate: Candidate being reauthorized.

        Returns:
            Whether the candidate remains discoverable.
        """
        self.authorized_candidate_count += 1
        return self.allow_candidates


class _FixtureSearchPort:
    """Capture provider-neutral Search calls and return fixed candidates."""

    def __init__(self, result: SearchQueryResult) -> None:
        """Initialize a deterministic Search result.

        Args:
            result: Candidate result returned by every search.
        """
        self.result = result
        self.search_call_count = 0
        self.received_scope: SearchAuthorizationScopeBinding | None = None

    def search(
        self,
        alias: SearchAliasKey,
        query: SearchQuery,
        scope_binding: SearchAuthorizationScopeBinding,
    ) -> SearchQueryResult:
        """Capture one authorized candidate-search invocation.

        Args:
            alias: Server-configured logical alias.
            query: Bounded provider-neutral query.
            scope_binding: Server-derived opaque scope.

        Returns:
            Fixed candidate result.
        """
        self.search_call_count += 1
        self.received_scope = scope_binding
        return self.result


class _FixtureOwnerReadPort:
    """Resolve safe owner summaries for known fixture resources."""

    def __init__(self, should_fail: bool = False) -> None:
        """Initialize owner-read fixture behavior.

        Args:
            should_fail: Whether resolution raises an unavailable fixture failure.
        """
        self.should_fail = should_fail
        self.read_call_count = 0

    def get_authorized_summary(self, resource_key: ResourceKey) -> OwnerDiscoverySummary | None:
        """Resolve one owner-approved fixture summary.

        Args:
            resource_key: Canonical resource reference.

        Returns:
            Safe summary evidence for the resource.

        Raises:
            RuntimeError: If fixture owner resolution is unavailable.
        """
        self.read_call_count += 1
        if self.should_fail:
            raise RuntimeError("owner unavailable")
        return OwnerDiscoverySummary(
            resource_key, "safe owner summary", datetime(2026, 7, 29, 12, tzinfo=UTC)
        )


def _build_search_result(*candidates: SearchResultHit) -> SearchQueryResult:
    """Build one fixture candidate page.

    Args:
        candidates: Candidates emitted by the fixture Search port.

    Returns:
        Provider-neutral candidate page.
    """
    return SearchQueryResult(candidates, "opaque-next-cursor", SearchGenerationKey("generation-v1"))


def test_discovery_denies_before_search_when_pre_authorization_fails() -> None:
    """Avoid Search invocation when trusted pre-search authorization denies."""
    authorization = _FixtureAuthorizationPort(deny_request=True)
    search = _FixtureSearchPort(_build_search_result(_build_candidate()))
    service = AuthorizedDiscoveryService(
        authorization,
        cast(SearchProviderPort, search),
        _FixtureOwnerReadPort(),
        SearchAliasKey("catalog-search"),
    )

    with pytest.raises(DiscoveryError, match="^discovery_not_available$"):
        service.discover(AuthorizedDiscoveryRequest(SearchQuery("atlas")))
    assert search.search_call_count == 0


def test_discovery_returns_only_post_authorized_owner_resolved_candidates() -> None:
    """Return owner summaries only after live post-search authorization."""
    candidate = _build_candidate()
    authorization = _FixtureAuthorizationPort()
    search = _FixtureSearchPort(_build_search_result(candidate))
    owner_read = _FixtureOwnerReadPort()
    service = AuthorizedDiscoveryService(
        authorization,
        cast(SearchProviderPort, search),
        owner_read,
        SearchAliasKey("catalog-search"),
    )

    result = service.discover(AuthorizedDiscoveryRequest(SearchQuery("atlas")))

    assert result.items[0].summary.resource_key == candidate.resource_key
    assert result.items[0].summary.safe_summary == "safe owner summary"
    assert search.received_scope == SearchAuthorizationScopeBinding(_SCOPE_FINGERPRINT)
    assert authorization.authorized_candidate_count == 1
    assert owner_read.read_call_count == 1


def test_discovery_omits_post_denied_candidates_without_owner_read() -> None:
    """Avoid owner resolution and disclosure for candidates denied after Search."""
    authorization = _FixtureAuthorizationPort(allow_candidates=False)
    search = _FixtureSearchPort(_build_search_result(_build_candidate()))
    owner_read = _FixtureOwnerReadPort()
    service = AuthorizedDiscoveryService(
        authorization,
        cast(SearchProviderPort, search),
        owner_read,
        SearchAliasKey("catalog-search"),
    )

    result = service.discover(AuthorizedDiscoveryRequest(SearchQuery("atlas")))

    assert result.items == ()
    assert owner_read.read_call_count == 0


def test_discovery_fails_closed_when_owner_resolution_is_unavailable() -> None:
    """Reject a page instead of exposing candidates when owner read fails."""
    service = AuthorizedDiscoveryService(
        _FixtureAuthorizationPort(),
        cast(SearchProviderPort, _FixtureSearchPort(_build_search_result(_build_candidate()))),
        _FixtureOwnerReadPort(should_fail=True),
        SearchAliasKey("catalog-search"),
    )

    with pytest.raises(DiscoveryError, match="^discovery_not_available$") as error:
        service.discover(AuthorizedDiscoveryRequest(SearchQuery("atlas")))
    assert error.value.code is DiscoveryErrorCode.INDETERMINATE


def test_authorized_discovery_module_has_no_runtime_or_transport_imports() -> None:
    """Keep Authorized Discovery as a pure application contract boundary."""
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
    assert not {
        imported_module
        for imported_module in imported_modules
        for prohibited_root in _PROHIBITED_IMPORT_ROOTS
        if imported_module == prohibited_root or imported_module.startswith(f"{prohibited_root}.")
    }
