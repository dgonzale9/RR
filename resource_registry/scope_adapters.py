"""Translate REST and assistant request context into transport-independent scope inputs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast
from uuid import UUID

from resource_registry.authorization.contracts import RegistryAuthorizationContext
from resource_registry.authorization.exceptions import RegistryAuthorizationContextError
from resource_registry.authorization.registry_authorization_context_service import (
    RegistryAuthorizationContextService,
)
from resource_registry.namespace_selection import (
    ActiveRegistryWorkspace,
    RegistryScopeSelection,
    RegistryScopeSelectionError,
    RegistryScopeSelectors,
    ScopeMode,
    select_registry_namespace,
)
from resource_registry.scope_policy import RegistryScopePolicy, get_registry_scope_policy

REGISTRY_SCOPE_HEADER = "X-Registry-Scope"


def get_request_scope_policy(request: object, *, assistant: bool = False) -> RegistryScopePolicy:
    """Build the rollout decision from configuration and a REST caller's explicit opt-in.

    Args:
        request: Request with optional headers.
        assistant: Ignore REST opt-ins for assistant and BIF operations.

    Returns:
        A policy snapshot for the current boundary operation.
    """
    return RegistryScopePolicy(
        mode=get_registry_scope_policy(),
        explicit_opt_in=not assistant and REGISTRY_SCOPE_HEADER in _get_headers(request),
    )


def select_request_registry_namespace(
    *,
    request: object,
    namespace_selectors: Sequence[object] = (),
    workspace_id: int | None = None,
    visibility_scope: str | None = None,
    assistant: bool = False,
    authorization_context: RegistryAuthorizationContext | None = None,
) -> RegistryScopeSelection:
    """Adapt trusted request context and authorize before invoking pure namespace selection.

    Args:
        request: Authenticated request with host-resolved Organization and active Workspace.
        namespace_selectors: Body or persisted namespace assertions.
        workspace_id: Optional body or persisted Workspace assertion.
        visibility_scope: Optional publication ownership assertion.
        assistant: Force the active Workspace and ignore REST selector headers.
        authorization_context: Already-resolved authorization; otherwise build it at this boundary.

    Returns:
        The selected namespace and persisted owner identifiers.

    Raises:
        RegistryScopeSelectionError: If authorization, normalization, or selection fails.
    """
    try:
        context = authorization_context or RegistryAuthorizationContextService().build_context(
            user=getattr(request, "user", None),
            organization=getattr(request, "organization", None),
        )
    except RegistryAuthorizationContextError as error:
        raise RegistryScopeSelectionError(
            "registry_scope_not_authorized",
            "The Registry scope is not authorized.",
            is_authorization_failure=True,
        ) from error
    headers = _get_headers(request)
    mode = "workspace" if assistant else headers.get(REGISTRY_SCOPE_HEADER, "workspace")
    if mode not in {"workspace", "organization", "explicit"}:
        raise RegistryScopeSelectionError("registry_scope_invalid", "Invalid Registry scope mode.")
    selectors = (
        namespace_selectors if assistant else (*namespace_selectors, headers.get("X-Tenant-ID"))
    )
    try:
        namespace_ids = frozenset(UUID(str(value)) for value in selectors if value is not None)
    except (ValueError, TypeError, AttributeError) as error:
        raise RegistryScopeSelectionError(
            "registry_scope_invalid", "A valid namespace UUID is required."
        ) from error
    return select_registry_namespace(
        selectors=RegistryScopeSelectors(
            mode=cast(ScopeMode, mode),
            namespace_ids=namespace_ids,
            workspace_id=workspace_id,
            visibility_scope=visibility_scope,
        ),
        active_workspace=_get_active_workspace(request),
        authorization_context=context,
    )


def validate_registry_destination(
    *,
    request: object,
    namespace_id: str,
    workspace_id: int | None = None,
    visibility_scope: str | None = None,
    surface: str,
    assistant: bool = False,
    authorization_context: RegistryAuthorizationContext | None = None,
) -> None:
    """Adapt an already-authorized destination for shared policy validation.

    Args:
        request: Request with trusted tenant context.
        namespace_id: Independently authorized namespace UUID.
        workspace_id: Optional persisted Workspace assertion.
        visibility_scope: Optional destination ownership assertion.
        surface: Internal adapter name for sanitized observation.
        assistant: Ignore REST headers for assistant and BIF validation.
        authorization_context: Optional previously resolved authorization.

    Raises:
        RegistryScopeSelectionError: If enforced scope validation fails.
    """
    get_request_scope_policy(request, assistant=assistant).validate_selection(
        validate=lambda: select_request_registry_namespace(
            request=request,
            namespace_selectors=(namespace_id,),
            workspace_id=workspace_id,
            visibility_scope=visibility_scope,
            assistant=assistant,
            authorization_context=authorization_context,
        ),
        surface=surface,
    )


def _get_headers(request: object) -> Mapping[str, str]:
    return cast(Mapping[str, str], getattr(request, "headers", {}))


def _get_active_workspace(request: object) -> ActiveRegistryWorkspace | None:
    workspace = getattr(request, "workspace", None)
    workspace_id = getattr(workspace, "pk", None)
    organization_id = getattr(workspace, "organization_id", None)
    if type(workspace_id) is not int or type(organization_id) is not int:
        return None
    return ActiveRegistryWorkspace(
        workspace_id=workspace_id,
        organization_id=organization_id,
        is_active=bool(getattr(workspace, "is_active", False)),
    )
