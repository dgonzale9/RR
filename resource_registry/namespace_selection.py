"""Select one authorized Registry namespace without consulting session hints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias
from uuid import UUID

from resource_registry.authorization.contracts import RegistryAuthorizationContext

ScopeMode: TypeAlias = Literal["workspace", "organization", "explicit"]


class RegistryScopeSelectionError(ValueError):
    """Report a sanitized selection error independently of HTTP transport."""

    def __init__(self, code: str, message: str, *, is_authorization_failure: bool = False) -> None:
        """Initialize the public failure.

        Args:
            code: Stable failure code.
            message: Public message without resource identifiers.
            is_authorization_failure: Distinguish denied access from invalid selectors.
        """
        super().__init__(message)
        self.code: str = code
        self.is_authorization_failure: bool = is_authorization_failure


@dataclass(frozen=True)
class RegistryScopeSelection:
    """Carry an admitted namespace and its immutable owner identifiers."""

    namespace_id: str
    organization_id: int
    workspace_id: int | None


@dataclass(frozen=True)
class RegistryScopeSelectors:
    """Carry normalized destination assertions independently of request transport."""

    mode: ScopeMode = "workspace"
    namespace_ids: frozenset[UUID] = frozenset()
    workspace_id: int | None = None
    visibility_scope: str | None = None


@dataclass(frozen=True)
class ActiveRegistryWorkspace:
    """Carry the trusted active Workspace identity without depending on ORM models."""

    workspace_id: int
    organization_id: int
    is_active: bool


def select_registry_namespace(
    *,
    selectors: RegistryScopeSelectors,
    active_workspace: ActiveRegistryWorkspace | None,
    authorization_context: RegistryAuthorizationContext,
) -> RegistryScopeSelection:
    """Select one namespace from an already-authorized set and normalized assertions.

    Args:
        selectors: Normalized explicit destination assertions.
        active_workspace: Server-resolved active Workspace identity, if available.
        authorization_context: Current authorization supplied by the calling boundary.

    Returns:
        The authorized namespace and its persisted owner identifiers.

    Raises:
        RegistryScopeSelectionError: If selection is conflicting, missing, or unauthorized.
    """
    context = authorization_context
    mode = selectors.mode
    namespace_ids = selectors.namespace_ids
    workspace_id = selectors.workspace_id
    visibility_scope = selectors.visibility_scope
    if mode not in {"workspace", "organization", "explicit"}:
        raise RegistryScopeSelectionError("registry_scope_invalid", "Invalid Registry scope mode.")
    if len(namespace_ids) > 1:
        raise RegistryScopeSelectionError(
            "registry_scope_conflict", "Conflicting Registry scope selectors are not allowed."
        )
    if workspace_id is not None and (type(workspace_id) is not int or workspace_id <= 0):
        raise RegistryScopeSelectionError("registry_scope_invalid", "Invalid Workspace selector.")

    mappings = context.authorized_namespaces.mappings
    if mode == "workspace":
        if (
            active_workspace is None
            or active_workspace.organization_id != context.authorized_workspaces.organization_id
            or not active_workspace.is_active
        ):
            raise RegistryScopeSelectionError(
                "registry_workspace_required", "Select an authorized Workspace in the interface."
            )
        active_id = active_workspace.workspace_id
        candidates = tuple(mapping for mapping in mappings if mapping.workspace_id == active_id)
    elif mode == "organization":
        candidates = tuple(mapping for mapping in mappings if mapping.workspace_id is None)
    else:
        if not namespace_ids and workspace_id is None:
            raise RegistryScopeSelectionError(
                "registry_scope_selection_required", "An explicit Registry destination is required."
            )
        candidates = tuple(
            mapping
            for mapping in mappings
            if (not namespace_ids or mapping.namespace_id in namespace_ids)
            and (workspace_id is None or mapping.workspace_id == workspace_id)
        )
    if len(candidates) != 1:
        raise RegistryScopeSelectionError(
            "registry_scope_not_authorized",
            "The Registry scope is not authorized.",
            is_authorization_failure=True,
        )
    selected = candidates[0]
    selected_scope = "ORGANIZATION" if selected.workspace_id is None else "WORKSPACE"
    if (
        (namespace_ids and selected.namespace_id not in namespace_ids)
        or (workspace_id is not None and selected.workspace_id != workspace_id)
        or (visibility_scope is not None and visibility_scope != selected_scope)
    ):
        raise RegistryScopeSelectionError(
            "registry_scope_conflict", "The destination does not match the selected Registry scope."
        )
    return RegistryScopeSelection(
        namespace_id=str(selected.namespace_id),
        organization_id=context.authorized_workspaces.organization_id,
        workspace_id=selected.workspace_id,
    )
