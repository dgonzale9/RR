"""Immutable contracts for Registry Namespace admission."""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, field
from uuid import UUID


@dataclass(frozen=True)
class AuthorizedNamespace:
    """Associate one authorized namespace with immutable Workspace or Organization provenance."""

    namespace_id: UUID
    workspace_id: int | None


@dataclass(frozen=True)
class AuthorizedWorkspaceSet:
    """Carry the complete immutable Workspace admission result for one user and Organization."""

    organization_id: int
    user_id: int
    workspace_ids: frozenset[int]

    def __post_init__(self) -> None:
        """Normalize Workspace identifiers into a deeply immutable set."""
        object.__setattr__(
            self,
            "workspace_ids",
            frozenset(int(workspace_id) for workspace_id in self.workspace_ids),
        )

    def contains_workspace(self, workspace_id: int) -> bool:
        """Return whether the Workspace is included in the authorization result.

        Args:
            workspace_id: Workspace primary key to test.

        Returns:
            True when the Workspace is authorized.
        """
        return workspace_id in self.workspace_ids


@dataclass(frozen=True)
class AuthorizedNamespaceSet:
    """Carry immutable Workspace-to-namespace mappings for one authorization result."""

    mappings: tuple[AuthorizedNamespace, ...]
    workspace_ids: frozenset[int] = field(init=False)
    namespace_ids: frozenset[UUID] = field(init=False)

    def __post_init__(self) -> None:
        """Normalize ordering and reject ambiguous Workspace or namespace mappings."""
        normalized_mappings = tuple(
            sorted(self.mappings, key=lambda mapping: mapping.namespace_id.int),
        )
        workspace_ids = tuple(
            mapping.workspace_id
            for mapping in normalized_mappings
            if mapping.workspace_id is not None
        )
        namespace_ids = tuple(mapping.namespace_id for mapping in normalized_mappings)
        if len(set(workspace_ids)) != len(workspace_ids):
            raise ValueError("Authorized namespace mappings contain a duplicate Workspace.")
        if len(set(namespace_ids)) != len(namespace_ids):
            raise ValueError("Authorized namespace mappings contain a duplicate namespace UUID.")
        object.__setattr__(self, "mappings", normalized_mappings)
        object.__setattr__(self, "workspace_ids", frozenset(workspace_ids))
        object.__setattr__(self, "namespace_ids", frozenset(namespace_ids))

    def contains_namespace(self, namespace_id: UUID | str) -> bool:
        """Return whether the namespace UUID is authorized.

        Args:
            namespace_id: Namespace UUID to test.

        Returns:
            True when the namespace is authorized; otherwise False.
        """
        parsed_namespace_id = _parse_namespace_id(namespace_id)
        return (
            parsed_namespace_id in self.namespace_ids if parsed_namespace_id is not None else False
        )

    def contains_workspace(self, workspace_id: int) -> bool:
        """Return whether the Workspace has an authorized namespace mapping.

        Args:
            workspace_id: Workspace primary key to test.

        Returns:
            True when a persisted authorized mapping exists.
        """
        return workspace_id in self.workspace_ids

    def resolve_workspace(self, namespace_id: UUID | str) -> int | None:
        """Resolve the authorized Workspace ID for a namespace UUID.

        Args:
            namespace_id: Authorized namespace UUID.

        Returns:
            The owning Workspace ID, or None when the UUID is invalid or unauthorized.
        """
        parsed_namespace_id = _parse_namespace_id(namespace_id)
        if parsed_namespace_id is None:
            return None
        namespace_integer = parsed_namespace_id.int
        index = bisect_left(
            self.mappings,
            namespace_integer,
            key=lambda mapping: mapping.namespace_id.int,
        )
        if index >= len(self.mappings):
            return None
        mapping = self.mappings[index]
        return mapping.workspace_id if mapping.namespace_id == parsed_namespace_id else None


@dataclass(frozen=True)
class RegistryAuthorizationContext:
    """Combine immutable Workspace and namespace admission results."""

    authorized_workspaces: AuthorizedWorkspaceSet
    authorized_namespaces: AuthorizedNamespaceSet


def _parse_namespace_id(namespace_id: UUID | str) -> UUID | None:
    try:
        return UUID(str(namespace_id))
    except (AttributeError, TypeError, ValueError):
        return None
