"""Define pure authorized-discovery orchestration contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Protocol, runtime_checkable

from .registry_core import RegistryCoreValidationError, ResourceKey
from .search_provider_module import (
    SearchAliasKey,
    SearchAuthorizationScopeBinding,
    SearchProviderError,
    SearchProviderErrorCode,
    SearchProviderPort,
    SearchQuery,
    SearchResultHit,
)

__all__ = (
    "AuthorizedDiscoveryItem",
    "AuthorizedDiscoveryRequest",
    "AuthorizedDiscoveryResult",
    "AuthorizedDiscoveryService",
    "DiscoveryAuthorizationGrant",
    "DiscoveryAuthorizationPort",
    "DiscoveryError",
    "DiscoveryErrorCode",
    "OwnerDiscoveryReadPort",
    "OwnerDiscoverySummary",
)

_MAXIMUM_SAFE_SUMMARY_LENGTH = 512


class DiscoveryErrorCode(str, Enum):
    """Classify non-enumerable authorized-discovery failures."""

    VALIDATION = "validation"
    DENIED = "discovery_not_available"
    UNAVAILABLE = "discovery_not_available"
    INDETERMINATE = "discovery_not_available"


class DiscoveryError(RegistryCoreValidationError):
    """Report a stable failure that does not enumerate protected resources."""

    def __init__(self, code: DiscoveryErrorCode) -> None:
        """Initialize one non-enumerable discovery failure.

        Args:
            code: Stable public-safe discovery failure category.
        """
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True, slots=True)
class AuthorizedDiscoveryRequest:
    """Carry caller discovery intent without authority or provider configuration.

    Args:
        query: Bounded provider-neutral query and opaque continuation cursor, if any.
    """

    query: SearchQuery

    def __post_init__(self) -> None:
        """Validate one authority-free discovery request.

        Raises:
            RegistryCoreValidationError: If query evidence is invalid.
        """
        if not isinstance(self.query, SearchQuery):
            raise RegistryCoreValidationError("invalid authorized discovery request")


@dataclass(frozen=True, slots=True)
class DiscoveryAuthorizationGrant:
    """Carry server-derived scope evidence for one authorized discovery request.

    Args:
        scope_binding: Opaque Search scope binding derived by trusted authorization.
    """

    scope_binding: SearchAuthorizationScopeBinding

    def __post_init__(self) -> None:
        """Validate one trusted scope grant.

        Raises:
            RegistryCoreValidationError: If scope evidence is invalid.
        """
        if not isinstance(self.scope_binding, SearchAuthorizationScopeBinding):
            raise RegistryCoreValidationError("invalid discovery authorization grant")


@dataclass(frozen=True, slots=True)
class OwnerDiscoverySummary:
    """Carry an owner-authoritative safe summary for one canonical resource.

    Args:
        resource_key: Canonical resource identity resolved by the owner.
        safe_summary: Owner-approved, transport-neutral summary text.
        source_updated_at: UTC instant of the owner summary evidence.
    """

    resource_key: ResourceKey
    safe_summary: str
    source_updated_at: datetime

    def __post_init__(self) -> None:
        """Validate safe owner-summary evidence.

        Raises:
            RegistryCoreValidationError: If owner summary evidence is invalid.
        """
        if not isinstance(self.resource_key, ResourceKey):
            raise RegistryCoreValidationError("invalid owner discovery resource key")
        if (
            not isinstance(self.safe_summary, str)
            or not self.safe_summary.strip()
            or len(self.safe_summary) > _MAXIMUM_SAFE_SUMMARY_LENGTH
        ):
            raise RegistryCoreValidationError("invalid owner discovery summary")
        if (
            not isinstance(self.source_updated_at, datetime)
            or self.source_updated_at.tzinfo is not UTC
        ):
            raise RegistryCoreValidationError("invalid owner discovery freshness")


@dataclass(frozen=True, slots=True)
class AuthorizedDiscoveryItem:
    """Carry one post-authorized owner-resolved discovery item.

    Args:
        summary: Owner-authoritative safe summary.
        score: Non-authoritative normalized Search score.
        projected_at: UTC Catalog projection timestamp.
        indexed_at: UTC Search index timestamp.
    """

    summary: OwnerDiscoverySummary
    score: int
    projected_at: datetime
    indexed_at: datetime

    def __post_init__(self) -> None:
        """Validate one externally safe discovery item.

        Raises:
            RegistryCoreValidationError: If discovery evidence is invalid.
        """
        if not isinstance(self.summary, OwnerDiscoverySummary) or type(self.score) is not int:
            raise RegistryCoreValidationError("invalid authorized discovery item")
        if self.score < 0 or not all(
            isinstance(value, datetime) and value.tzinfo is UTC
            for value in (self.projected_at, self.indexed_at)
        ):
            raise RegistryCoreValidationError("invalid authorized discovery item")


@dataclass(frozen=True, slots=True)
class AuthorizedDiscoveryResult:
    """Carry a bounded page of post-authorized owner summaries.

    Args:
        items: Post-authorized, owner-resolved items in provider order.
        next_cursor: Opaque continuation cursor bound by the Search provider.
        is_partial: Whether provider evidence was degraded or partial.
    """

    items: tuple[AuthorizedDiscoveryItem, ...]
    next_cursor: str | None
    is_partial: bool

    def __post_init__(self) -> None:
        """Validate one safe discovery result.

        Raises:
            RegistryCoreValidationError: If result evidence is malformed.
        """
        if type(self.items) is not tuple or any(
            not isinstance(item, AuthorizedDiscoveryItem) for item in self.items
        ):
            raise RegistryCoreValidationError("invalid authorized discovery items")
        if self.next_cursor is not None and (
            not isinstance(self.next_cursor, str) or not self.next_cursor.strip()
        ):
            raise RegistryCoreValidationError("invalid authorized discovery cursor")
        if not isinstance(self.is_partial, bool):
            raise RegistryCoreValidationError("invalid authorized discovery result")


@runtime_checkable
class DiscoveryAuthorizationPort(Protocol):
    """Define trusted live authorization checks for discovery orchestration."""

    def authorize_discovery(
        self, request: AuthorizedDiscoveryRequest
    ) -> DiscoveryAuthorizationGrant:
        """Authorize one request and derive its opaque Search scope."""

    def authorize_candidate(
        self,
        grant: DiscoveryAuthorizationGrant,
        candidate: SearchResultHit,
    ) -> bool:
        """Determine whether one Search candidate remains discoverable."""


@runtime_checkable
class OwnerDiscoveryReadPort(Protocol):
    """Define owner-authoritative safe summary resolution for discovery."""

    def get_authorized_summary(self, resource_key: ResourceKey) -> OwnerDiscoverySummary | None:
        """Resolve one safe current owner summary or no result."""


class AuthorizedDiscoveryService:
    """Orchestrate fail-closed authorized discovery over Search candidates."""

    def __init__(
        self,
        authorization_port: DiscoveryAuthorizationPort,
        search_provider: SearchProviderPort,
        owner_read_port: OwnerDiscoveryReadPort,
        search_alias: SearchAliasKey,
    ) -> None:
        """Initialize one application boundary with trusted injected collaborators.

        Args:
            authorization_port: Live authorization and scope-derivation collaborator.
            search_provider: Provider-neutral candidate Search port.
            owner_read_port: Owner-authoritative safe summary collaborator.
            search_alias: Server-configured logical Search alias.

        Raises:
            RegistryCoreValidationError: If a collaborator or alias is invalid.
        """
        if (
            not isinstance(authorization_port, DiscoveryAuthorizationPort)
            or not callable(getattr(search_provider, "search", None))
            or not isinstance(owner_read_port, OwnerDiscoveryReadPort)
            or not isinstance(search_alias, SearchAliasKey)
        ):
            raise RegistryCoreValidationError("invalid authorized discovery service")
        self._authorization_port = authorization_port
        self._search_provider = search_provider
        self._owner_read_port = owner_read_port
        self._search_alias = search_alias

    def discover(self, request: AuthorizedDiscoveryRequest) -> AuthorizedDiscoveryResult:
        """Return only post-authorized, owner-resolved Search candidates.

        Args:
            request: Authority-free bounded discovery intent.

        Returns:
            A safe bounded owner-summary page.

        Raises:
            DiscoveryError: If authorization, Search, or owner resolution cannot safely proceed.
        """
        if not isinstance(request, AuthorizedDiscoveryRequest):
            raise DiscoveryError(DiscoveryErrorCode.VALIDATION)
        try:
            grant = self._authorization_port.authorize_discovery(request)
        except Exception as error:
            raise _normalize_discovery_failure(error) from None
        if not isinstance(grant, DiscoveryAuthorizationGrant):
            raise DiscoveryError(DiscoveryErrorCode.INDETERMINATE)
        try:
            candidates = self._search_provider.search(
                self._search_alias,
                request.query,
                grant.scope_binding,
            )
        except SearchProviderError as error:
            if error.code in {SearchProviderErrorCode.UNAVAILABLE, SearchProviderErrorCode.TIMEOUT}:
                raise DiscoveryError(DiscoveryErrorCode.UNAVAILABLE) from None
            raise DiscoveryError(DiscoveryErrorCode.INDETERMINATE) from None
        except Exception:
            raise DiscoveryError(DiscoveryErrorCode.INDETERMINATE) from None
        items: list[AuthorizedDiscoveryItem] = []
        for candidate in candidates.hits:
            try:
                is_authorized = self._authorization_port.authorize_candidate(grant, candidate)
                summary = (
                    self._owner_read_port.get_authorized_summary(candidate.resource_key)
                    if is_authorized
                    else None
                )
            except Exception:
                raise DiscoveryError(DiscoveryErrorCode.INDETERMINATE) from None
            if summary is None:
                continue
            if (
                not isinstance(summary, OwnerDiscoverySummary)
                or summary.resource_key != candidate.resource_key
            ):
                raise DiscoveryError(DiscoveryErrorCode.INDETERMINATE)
            items.append(
                AuthorizedDiscoveryItem(
                    summary,
                    candidate.score,
                    candidate.projected_at,
                    candidate.indexed_at,
                )
            )
        return AuthorizedDiscoveryResult(
            tuple(items), candidates.next_cursor, candidates.is_partial
        )


def _normalize_discovery_failure(error: Exception) -> DiscoveryError:
    """Normalize authorization failures without enumerating protected resources."""
    if isinstance(error, DiscoveryError):
        return error
    return DiscoveryError(DiscoveryErrorCode.DENIED)
