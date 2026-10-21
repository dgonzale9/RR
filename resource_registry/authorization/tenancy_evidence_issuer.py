"""Re-evaluate Registry-owned tenancy and admit or reject one D6 evidence evaluation.

The issuer accepts no caller-supplied Organization, Workspace, namespace, owner scope, principal,
or evaluation identity. Every binding is resolved from Registry-owned admission facts and is
re-evaluated at issuance and again at consumption.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import UUID

from resource_registry.authorization.tenancy_evidence_port import (
    D6EvidenceRejected,
    RegistryOwnedPrincipalContext,
    RegistryOwnerKind,
    RegistryPrincipalKind,
    RegistryTenancyFacts,
)

__all__ = (
    "RegistryAdmissionFacts",
    "RegistryAdmissionSource",
    "RegistryTenancyEvidenceIssuer",
)


@dataclass(frozen=True, slots=True)
class RegistryAdmissionFacts:
    """Carry the current Registry-owned admission observations for one principal context.

    Args:
        principal_is_persisted: Whether the authenticated principal is currently persisted.
        organization_id: Currently resolved Organization identifier.
        organization_is_enabled: Whether Resource Registry access is enabled for the Organization.
        workspace_id: Currently resolved Workspace identifier.
        workspace_is_active: Whether the Workspace is currently active.
        workspace_is_enabled: Whether Resource Registry access is enabled for the Workspace.
        workspace_organization_id: Organization that currently owns the Workspace.
        has_authorized_membership: Whether the principal currently holds authorized membership.
        namespace_id: Currently authorized RegistryNamespace identifier.
        namespace_owner_kind: Closed owner scope of the RegistryNamespace.
        namespace_owner_id: Identifier of the exact RegistryNamespace owner.
    """

    principal_is_persisted: bool
    principal_id: int | None
    principal_kind: RegistryPrincipalKind | None
    organization_id: int | None
    organization_is_enabled: bool
    workspace_id: int | None
    workspace_is_active: bool
    workspace_is_enabled: bool
    workspace_organization_id: int | None
    has_authorized_membership: bool
    namespace_id: UUID | None
    namespace_owner_kind: RegistryOwnerKind | None
    namespace_owner_id: int | None

    def __repr__(self) -> str:
        """Return a representation without protected tenancy observations."""
        return "<protected>"


@runtime_checkable
class RegistryAdmissionSource(Protocol):
    """Read current Registry-owned admission facts from authoritative Registry sources."""

    def read_admission(
        self, principal_context: RegistryOwnedPrincipalContext
    ) -> RegistryAdmissionFacts:
        """Return the current Registry admission facts for one trusted principal context."""


class RegistryTenancyEvidenceIssuer:
    """Resolve the exact S1 tenancy bindings that Registry evidence may carry.

    Args:
        admission_source: Registry-owned source of current authoritative admission facts.

    Raises:
        D6EvidenceRejected: If the admission source is invalid.
    """

    __slots__ = ("_admission_source",)

    def __init__(self, *, admission_source: RegistryAdmissionSource) -> None:
        """Initialize the issuer with its Registry-owned admission source."""
        if not callable(getattr(admission_source, "read_admission", None)):
            raise D6EvidenceRejected()
        self._admission_source = admission_source

    def resolve_current_tenancy(
        self, principal_context: RegistryOwnedPrincipalContext
    ) -> RegistryTenancyFacts:
        """Re-evaluate current tenancy and return the exact authorized S1 bindings.

        Args:
            principal_context: Trusted Registry-owned principal context for one evaluation.

        Returns:
            The immutable S1 tenancy bindings currently authorized for this evaluation.

        Raises:
            D6EvidenceRejected: If any current tenancy fact is missing, disabled, unauthorized,
                or inconsistent with the exact namespace owner scope.
        """
        if not isinstance(principal_context, RegistryOwnedPrincipalContext):
            raise D6EvidenceRejected()
        admission = self._read_admission(principal_context)
        if not admission.principal_is_persisted:
            raise D6EvidenceRejected()
        if (
            not _is_strict_positive_integer(principal_context.principal_id)
            or not _is_strict_positive_integer(admission.principal_id)
            or admission.principal_id != principal_context.principal_id
            or not isinstance(admission.principal_kind, RegistryPrincipalKind)
            or admission.principal_kind is not principal_context.principal_kind
        ):
            raise D6EvidenceRejected()
        if (
            not _is_strict_positive_integer(principal_context.organization_id)
            or not _is_strict_positive_integer(admission.organization_id)
            or admission.organization_id != principal_context.organization_id
        ):
            raise D6EvidenceRejected()
        if not admission.organization_is_enabled:
            raise D6EvidenceRejected()
        workspace_id = principal_context.workspace_id
        if workspace_id is not None:
            if (
                not _is_strict_positive_integer(workspace_id)
                or not _is_strict_positive_integer(admission.workspace_id)
                or admission.workspace_id != workspace_id
                or not admission.workspace_is_active
                or not admission.workspace_is_enabled
                or not _is_strict_positive_integer(admission.workspace_organization_id)
                or admission.workspace_organization_id != admission.organization_id
            ):
                raise D6EvidenceRejected()
        if not admission.has_authorized_membership:
            raise D6EvidenceRejected()
        namespace_id = admission.namespace_id
        owner_kind = admission.namespace_owner_kind
        owner_id = admission.namespace_owner_id
        if (
            not isinstance(namespace_id, UUID)
            or not isinstance(owner_kind, RegistryOwnerKind)
            or not _is_strict_positive_integer(owner_id)
        ):
            raise D6EvidenceRejected()
        if workspace_id is None:
            if (
                owner_kind is not RegistryOwnerKind.ORGANIZATION
                or owner_id != admission.organization_id
            ):
                raise D6EvidenceRejected()
        elif owner_kind is not RegistryOwnerKind.WORKSPACE or owner_id != workspace_id:
            raise D6EvidenceRejected()
        return RegistryTenancyFacts(
            principal_id=admission.principal_id,
            principal_kind=admission.principal_kind,
            organization_id=admission.organization_id,
            workspace_id=workspace_id,
            namespace_id=namespace_id,
            owner_kind=owner_kind,
            owner_id=owner_id,
        )

    def _read_admission(
        self, principal_context: RegistryOwnedPrincipalContext
    ) -> RegistryAdmissionFacts:
        """Read current Registry admission facts and fail closed on any ordinary failure."""
        try:
            admission = self._admission_source.read_admission(principal_context)
        except Exception:
            admission = None
        if not isinstance(admission, RegistryAdmissionFacts):
            raise D6EvidenceRejected()
        return admission


def _is_strict_positive_integer(value: object) -> bool:
    """Determine whether one authoritative D6 integer identity excludes boolean values."""
    return type(value) is int and value > 0
