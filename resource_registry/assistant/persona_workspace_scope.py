"""Derive the immutable Resource Registry workspace scope for capable Personas."""

from __future__ import annotations

from dataclasses import dataclass

from resource_registry.authorization.contracts import RegistryAuthorizationContext
from resource_registry.bifs.manifest import RESOURCE_REGISTRY_BIF_MANIFEST
from resource_registry.namespace_selection import RegistryScopeSelectionError
from resource_registry.scope_adapters import (
    get_request_scope_policy,
    select_request_registry_namespace,
)

PERSONA_WORKSPACE_SELECTION_SESSION_KEY = "resource_registry_persona_workspace_namespace_id"
PERSONA_WORKSPACE_SELECTION_REQUEST_ATTRIBUTE = "resource_registry_persona_workspace_namespace_id"
PERSONA_WORKSPACE_SELECTION_REQUIRED_MESSAGE = "Resource Registry workspace selection is required."


class PersonaWorkspaceScopeError(ValueError):
    """Report an invalid Resource Registry persona workspace configuration without disclosure."""


class PersonaWorkspaceNotConfiguredError(PersonaWorkspaceScopeError):
    """Report a Registry-capable Persona with no configured Workspace."""


@dataclass(frozen=True)
class PersonaWorkspaceScope:
    """Describe the sole authorized workspace and namespace for a Resource Registry persona."""

    workspace_id: int
    namespace_id: str


def resolve_persona_workspace_scope(
    *,
    request: object,
    authorization_context: RegistryAuthorizationContext,
    persona: object | None = None,
    require_selected_workspace: bool = True,
) -> PersonaWorkspaceScope | None:
    """Resolve the forced workspace scope for a Resource Registry-capable Persona.

    Args:
        request: Authenticated request carrying the selected Persona.
        authorization_context: Server-derived Registry authorization result.
        persona: Optional configured Persona when it is held outside the request.
        require_selected_workspace: Require an explicit conversation workspace selection when
            more than one configured workspace is authorized.

    Returns:
        The forced workspace scope, or None when no Persona or Workspace-scoped Persona is
        present.

    Raises:
        PersonaWorkspaceNotConfiguredError: If no Workspace is assigned to the Persona.
        PersonaWorkspaceScopeError: If the persona configuration or user access is invalid.
    """
    persona = persona or getattr(request, "persona", None)
    if persona is None:
        return None
    relationship_manager = getattr(persona, "builtinfunctionpersonarelationship_set", None)
    relationship_filter = getattr(relationship_manager, "filter", None)
    if not callable(relationship_filter):
        return None
    if not relationship_filter(
        builtin_function__name__in=tuple(RESOURCE_REGISTRY_BIF_MANIFEST)
    ).exists():
        return None
    workspace_manager = getattr(persona, "workspaces", None)
    values_list = getattr(workspace_manager, "values_list", None)
    if not callable(values_list):
        return None
    configured_ids = frozenset(int(value) for value in values_list("pk", flat=True))
    if not configured_ids:
        raise PersonaWorkspaceNotConfiguredError("Resource Registry workspace is not configured.")
    scopes = tuple(
        PersonaWorkspaceScope(mapping.workspace_id, str(mapping.namespace_id))
        for mapping in authorization_context.authorized_namespaces.mappings
        if mapping.workspace_id is not None
        and mapping.workspace_id in configured_ids
        and authorization_context.authorized_workspaces.contains_workspace(mapping.workspace_id)
    )
    if not scopes:
        raise PersonaWorkspaceScopeError("Resource Registry workspace is not authorized.")
    if get_request_scope_policy(request, assistant=True).is_enforced:
        try:
            selected = select_request_registry_namespace(
                request=request,
                assistant=True,
                authorization_context=authorization_context,
            )
        except RegistryScopeSelectionError as error:
            raise PersonaWorkspaceScopeError(str(error)) from error
        for scope in scopes:
            if scope.namespace_id == selected.namespace_id:
                return scope
        raise PersonaWorkspaceScopeError(
            "Select a Workspace supported by this Persona in the interface."
        )
    if len(scopes) == 1:
        return scopes[0]
    if not require_selected_workspace:
        return scopes[0]
    selected_namespace = _get_explicitly_selected_namespace(request)
    for scope in scopes:
        if scope.namespace_id == selected_namespace:
            return scope
    raise PersonaWorkspaceScopeError(PERSONA_WORKSPACE_SELECTION_REQUIRED_MESSAGE)


def _get_explicitly_selected_namespace(request: object) -> str:
    """Return a server-recorded explicit workspace selection for the current conversation.

    Args:
        request: Authenticated request carrying transient and session conversation state.

    Returns:
        The explicitly selected namespace identifier, or an empty string when unavailable.
    """
    request_selection = str(
        getattr(request, PERSONA_WORKSPACE_SELECTION_REQUEST_ATTRIBUTE, "")
    ).strip()
    session = getattr(request, "session", None)
    session_selection = str(
        session.get(PERSONA_WORKSPACE_SELECTION_SESSION_KEY, "") if session is not None else ""
    ).strip()
    selected_namespace = request_selection or session_selection
    request_namespace = str(getattr(request, "resource_registry_tenant_id", "")).strip()
    return (
        selected_namespace if selected_namespace and selected_namespace == request_namespace else ""
    )
