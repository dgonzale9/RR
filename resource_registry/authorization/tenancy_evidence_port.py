"""Expose the opaque Registry tenancy-evidence boundary and its internal rejection contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol, runtime_checkable
from uuid import UUID

__all__ = (
    "D6EvidenceRejected",
    "RegistryEvidenceConsumer",
    "RegistryOwnedPrincipalContext",
    "RegistryOwnerKind",
    "RegistryTenancyFacts",
    "RegistryTenancyFactsSource",
    "TrustedClock",
)


class D6EvidenceRejected(Exception):  # noqa: N818
    """Report one uniform, non-disclosing Registry tenancy-evidence rejection.

    The message is fixed so that identity, membership, ownership, expiry, replay, state,
    generation, cleanup, and clock failures remain indistinguishable at the boundary.
    """

    def __init__(self) -> None:
        """Initialize the stable rejection without protected details."""
        super().__init__("registry tenancy evidence unavailable")


class RegistryOwnerKind(Enum):
    """Enumerate the closed Registry namespace owner scope."""

    ORGANIZATION = "org_owner"
    WORKSPACE = "ws_owner"


class RegistryPrincipalKind(Enum):
    """Enumerate the authoritative persisted principal representation."""

    DJANGO_USER = "django_user"
    SERVICE_ACCOUNT = "service_account"


class RegistryEvidenceConsumer(Enum):
    """Enumerate the downstream evaluations that may obtain distinct evidence."""

    D4 = "d4"
    D5 = "d5"


@dataclass(frozen=True, slots=True)
class RegistryOwnedPrincipalContext:
    """Carry the trusted Registry-owned principal context for one evaluation.

    Args:
        principal_id: Authenticated persisted principal identifier.
        principal_kind: Trusted authenticated principal representation.
        organization_id: Trusted Organization identifier entering the Registry boundary.
        workspace_id: Optional trusted Workspace identifier when the evaluation is scoped to one.
    """

    principal_id: int
    organization_id: int
    principal_kind: RegistryPrincipalKind = RegistryPrincipalKind.DJANGO_USER
    workspace_id: int | None = None

    def __post_init__(self) -> None:
        """Validate trusted positive-integer identities before session state can retain them."""
        if (
            not _is_strict_positive_integer(self.principal_id)
            or not _is_strict_positive_integer(self.organization_id)
            or not isinstance(self.principal_kind, RegistryPrincipalKind)
            or (
                self.workspace_id is not None and not _is_strict_positive_integer(self.workspace_id)
            )
        ):
            raise D6EvidenceRejected()

    def __repr__(self) -> str:
        """Return a representation without protected tenancy identities."""
        return "<protected>"


@dataclass(frozen=True, slots=True)
class RegistryTenancyFacts:
    """Carry the immutable S1 tenancy bindings resolved by Resource Registry.

    Args:
        principal_id: Authenticated persisted principal identifier.
        organization_id: Persisted and Registry-enabled Organization identifier.
        workspace_id: Optional authorized Workspace identifier.
        namespace_id: Exact authorized RegistryNamespace identifier.
        owner_kind: Closed owner scope of the RegistryNamespace.
        owner_id: Identifier of the exact namespace owner.
    """

    principal_id: int
    principal_kind: RegistryPrincipalKind
    organization_id: int
    workspace_id: int | None
    namespace_id: UUID
    owner_kind: RegistryOwnerKind
    owner_id: int

    def __repr__(self) -> str:
        """Return a representation without protected tenancy bindings."""
        return "<protected>"


def _is_strict_positive_integer(value: object) -> bool:
    """Determine whether one D6 integer identity is positive and excludes booleans."""
    return type(value) is int and value > 0


@runtime_checkable
class TrustedClock(Protocol):
    """Read the trusted UTC and process-local monotonic observations."""

    def utc_now(self) -> datetime:
        """Return one timezone-aware UTC observation."""

    def monotonic_now(self) -> float:
        """Return one finite process-local monotonic observation."""


@runtime_checkable
class RegistryTenancyFactsSource(Protocol):
    """Resolve current Registry-owned tenancy facts from authoritative Registry sources."""

    def resolve_current_tenancy(
        self, principal_context: RegistryOwnedPrincipalContext
    ) -> RegistryTenancyFacts:
        """Return the current authoritative tenancy facts or reject them uniformly."""
