"""Verify Creative Assembly's forced Resource Registry workspace boundary."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest

from resource_registry.assistant.persona_workspace_scope import (
    PERSONA_WORKSPACE_SELECTION_SESSION_KEY,
    PersonaWorkspaceNotConfiguredError,
    PersonaWorkspaceScopeError,
    resolve_persona_workspace_scope,
)
from resource_registry.authorization.contracts import (
    AuthorizedNamespace,
    AuthorizedNamespaceSet,
    AuthorizedWorkspaceSet,
    RegistryAuthorizationContext,
)
from resource_registry.tests.bif_contract_helpers import FakePersonaBifRelationshipManager


class _RelationshipManager:
    """Expose the Registry function assignment required by the policy."""

    def filter(self, **_values: object) -> _RelationshipManager:
        """Return the configured relationship query result."""
        return self

    def exists(self) -> bool:
        """Return that a Registry function is assigned."""
        return True


class _WorkspaceManager:
    """Expose configured Persona workspace identifiers."""

    def __init__(self, workspace_ids: tuple[int, ...]) -> None:
        """Store immutable configured workspace identifiers."""
        self._workspace_ids = workspace_ids

    def values_list(self, _field_name: str, *, flat: bool) -> tuple[int, ...]:
        """Return configured workspace identifiers."""
        assert flat is True
        return self._workspace_ids


def _context(*workspace_ids: int) -> RegistryAuthorizationContext:
    """Build one authorized context with namespaces matching its workspaces."""
    namespaces = tuple(
        AuthorizedNamespace(namespace_id=UUID(int=workspace_id), workspace_id=workspace_id)
        for workspace_id in workspace_ids
    )
    return RegistryAuthorizationContext(
        authorized_workspaces=AuthorizedWorkspaceSet(
            organization_id=1,
            user_id=2,
            workspace_ids=frozenset(workspace_ids),
        ),
        authorized_namespaces=AuthorizedNamespaceSet(mappings=namespaces),
    )


def test_resolve_scope_uses_request_workspace_supported_by_persona() -> None:
    """Admit the request Workspace when it is configured on the Persona."""
    request = SimpleNamespace(
        workspace=SimpleNamespace(pk=7, organization_id=1, is_active=True),
        persona=SimpleNamespace(
            workspaces=_WorkspaceManager((7,)),
            builtinfunctionpersonarelationship_set=_RelationshipManager(),
        ),
    )

    scope = resolve_persona_workspace_scope(
        request=request,
        authorization_context=_context(7, 8),
    )

    assert scope is not None
    assert scope.workspace_id == 7
    assert scope.namespace_id == str(UUID(int=7))


@pytest.mark.parametrize("workspace_ids", ((), (7, 8)))
def test_resolve_scope_rejects_personas_without_an_authorized_workspace(
    workspace_ids: tuple[int, ...],
) -> None:
    """Fail closed when no configured Persona workspace is authorized."""
    request = SimpleNamespace(
        persona=SimpleNamespace(
            workspaces=_WorkspaceManager(workspace_ids),
            builtinfunctionpersonarelationship_set=_RelationshipManager(),
        )
    )

    expected_exception = (
        PersonaWorkspaceScopeError if workspace_ids else PersonaWorkspaceNotConfiguredError
    )
    with pytest.raises(expected_exception):
        resolve_persona_workspace_scope(
            request=request,
            authorization_context=_context(),
        )


def test_resolve_scope_uses_request_workspace_over_conversation_selection() -> None:
    """Use request scope when a multi-Workspace Persona has stale conversation selection."""
    request = SimpleNamespace(
        workspace=SimpleNamespace(pk=7, organization_id=1, is_active=True),
        resource_registry_tenant_id=str(UUID(int=8)),
        session={PERSONA_WORKSPACE_SELECTION_SESSION_KEY: str(UUID(int=8))},
        persona=SimpleNamespace(
            workspaces=_WorkspaceManager((7, 8)),
            builtinfunctionpersonarelationship_set=_RelationshipManager(),
        ),
    )

    scope = resolve_persona_workspace_scope(
        request=request,
        authorization_context=_context(7, 8),
    )

    assert scope is not None
    assert scope.workspace_id == 7


def test_resolve_scope_rejects_stale_namespace_without_request_workspace() -> None:
    """Reject a stale namespace hint when no request Workspace is available."""
    request = SimpleNamespace(
        resource_registry_tenant_id=str(UUID(int=8)),
        session={},
        persona=SimpleNamespace(
            workspaces=_WorkspaceManager((7, 8)),
            builtinfunctionpersonarelationship_set=_RelationshipManager(),
        ),
    )

    with pytest.raises(PersonaWorkspaceScopeError, match="interface"):
        resolve_persona_workspace_scope(
            request=request,
            authorization_context=_context(7, 8),
        )


def test_non_registry_persona_does_not_enable_persona_workspace_scope() -> None:
    """Leave generic Registry consumers on the existing organization visibility path."""
    request = SimpleNamespace(
        persona=SimpleNamespace(
            workspaces=_WorkspaceManager((7,)),
            builtinfunctionpersonarelationship_set=FakePersonaBifRelationshipManager(
                ("creative_assembly_create_campaign_asset",),
            ),
        )
    )

    assert (
        resolve_persona_workspace_scope(
            request=request,
            authorization_context=_context(7),
        )
        is None
    )
