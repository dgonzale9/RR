"""Verify pure scope contracts, rollout substitution, and isolated conversation cleanup."""

from __future__ import annotations

import ast
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest

from resource_registry.authorization.contracts import (
    AuthorizedNamespace,
    AuthorizedNamespaceSet,
    AuthorizedWorkspaceSet,
    RegistryAuthorizationContext,
)
from resource_registry.conversation_state import clear_registry_context
from resource_registry.namespace_selection import (
    ActiveRegistryWorkspace,
    RegistryScopeSelectionError,
    RegistryScopeSelectors,
    ScopeMode,
    select_registry_namespace,
)
from resource_registry.scope_policy import RegistryScopePolicy, ScopePolicy


@pytest.fixture
def authorization() -> RegistryAuthorizationContext:
    """Build an immutable authorization result without ORM or transport dependencies.

    Returns:
        Two authorized Workspaces and one Organization namespace.
    """
    return RegistryAuthorizationContext(
        authorized_workspaces=AuthorizedWorkspaceSet(
            organization_id=10,
            user_id=20,
            workspace_ids=frozenset({1, 2}),
        ),
        authorized_namespaces=AuthorizedNamespaceSet(
            mappings=(
                AuthorizedNamespace(namespace_id=UUID(int=1), workspace_id=1),
                AuthorizedNamespace(namespace_id=UUID(int=2), workspace_id=2),
                AuthorizedNamespace(namespace_id=UUID(int=3), workspace_id=None),
            )
        ),
    )


@pytest.mark.parametrize(
    ("mode", "explicit_workspace", "expected_namespace"),
    (("workspace", None, 1), ("organization", None, 3), ("explicit", 2, 2)),
)
def test_selection_uses_supplied_authorization_without_a_request(
    authorization: RegistryAuthorizationContext,
    mode: ScopeMode,
    explicit_workspace: int | None,
    expected_namespace: int,
) -> None:
    """Select each public scope from plain immutable inputs.

    Args:
        authorization: Authorized identities supplied by the caller.
        mode: Scope under test.
        explicit_workspace: Optional explicit destination.
        expected_namespace: Expected UUID integer component.
    """
    selected = select_registry_namespace(
        selectors=RegistryScopeSelectors(mode=mode, workspace_id=explicit_workspace),
        active_workspace=ActiveRegistryWorkspace(1, 10, True),
        authorization_context=authorization,
    )
    assert selected.namespace_id == str(UUID(int=expected_namespace))
    assert selected.organization_id == 10


@pytest.mark.parametrize("destination", (2, 99))
def test_selection_rejects_conflicting_or_unauthorized_destination(
    authorization: RegistryAuthorizationContext,
    destination: int,
) -> None:
    """Reject both an authorized wrong active destination and an unknown namespace.

    Args:
        authorization: Authorized namespace set.
        destination: Namespace UUID integer component.
    """
    mode: ScopeMode = "workspace" if destination == 2 else "explicit"
    with pytest.raises(RegistryScopeSelectionError) as failure:
        select_registry_namespace(
            selectors=RegistryScopeSelectors(
                mode=mode, namespace_ids=frozenset({UUID(int=destination)})
            ),
            active_workspace=ActiveRegistryWorkspace(1, 10, True),
            authorization_context=authorization,
        )
    assert failure.value.is_authorization_failure == (destination == 99)


@pytest.mark.parametrize("mode", ("legacy", "observe", "strict"))
@pytest.mark.parametrize("explicit_opt_in", (False, True))
def test_rollout_selects_consistently_and_sanitizes_discrepancies(
    mode: ScopePolicy,
    explicit_opt_in: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Apply one policy to interchangeable selection callbacks without logging destinations.

    Args:
        mode: Environment policy.
        explicit_opt_in: Caller adoption of the current contract.
        caplog: Captured logs.
    """
    current = Mock(return_value="current-sensitive-namespace")
    legacy = Mock(return_value="legacy-sensitive-namespace")
    policy = RegistryScopePolicy(mode, explicit_opt_in)
    with caplog.at_level(logging.INFO, logger="resource_registry.scope_policy"):
        selected = policy.resolve_selection(
            select_current=current,
            select_legacy=legacy,
            surface="test",
        )
    enforced = mode == "strict" or explicit_opt_in
    assert selected == (current.return_value if enforced else legacy.return_value)
    assert legacy.call_count == (0 if enforced else 1)
    assert current.call_count == (0 if mode == "legacy" and not explicit_opt_in else 1)
    events = [
        record for record in caplog.records if record.name == "resource_registry.scope_policy"
    ]
    assert bool(events) == (mode == "observe" and not explicit_opt_in)
    assert all("sensitive-namespace" not in record.getMessage() for record in events)


@pytest.mark.parametrize("mode", ("legacy", "observe", "strict"))
def test_rollout_never_swallows_selected_authorization_failure(mode: ScopePolicy) -> None:
    """Propagate admission denial from the selected path in every rollout mode.

    Args:
        mode: Environment policy under test.
    """
    denial = RegistryScopeSelectionError(
        "registry_scope_not_authorized",
        "Denied",
        is_authorization_failure=True,
    )
    with pytest.raises(RegistryScopeSelectionError) as failure:
        RegistryScopePolicy(mode).resolve_selection(
            select_current=Mock(side_effect=denial),
            select_legacy=Mock(side_effect=denial),
            surface="test",
        )
    assert failure.value is denial


@pytest.mark.parametrize("mode", ("legacy", "observe", "strict"))
def test_missing_selector_requires_current_admission_in_every_mode(mode: ScopePolicy) -> None:
    """Reject missing-selector derivation failures without falling back to legacy hints.

    Args:
        mode: Environment policy under test.
    """
    legacy = Mock(return_value=1)
    with pytest.raises(RegistryScopeSelectionError):
        RegistryScopePolicy(mode).resolve_selection(
            select_current=Mock(side_effect=RegistryScopeSelectionError("missing", "Select a W")),
            select_legacy=legacy,
            requires_current=True,
            surface="test",
        )
    legacy.assert_not_called()


def test_cleanup_preserves_unrelated_state_without_assistant_services() -> None:
    """Clear Registry key families and hints while preserving the rest of the conversation."""
    request = SimpleNamespace(
        resource_registry_tenant_id="stale",
        resource_registry_persona_workspace_namespace_id="stale",
        session={
            "resource_registry_tenant_id": "stale",
            "resource_registry_selected_dataset_ids:stale": ["dataset"],
            "resource_registry_campaign_dataset_preflight": {"pending": True},
            "chat_history": ["keep"],
        },
    )
    assert clear_registry_context(request) == 3
    assert request.session == {"chat_history": ["keep"]}
    assert not hasattr(request, "resource_registry_tenant_id")
    assert not hasattr(request, "resource_registry_persona_workspace_namespace_id")
    assert clear_registry_context(request) == 0


@pytest.mark.parametrize("module", ("namespace_selection", "scope_policy", "conversation_state"))
def test_scope_core_does_not_depend_on_framework_or_request_adapters(module: str) -> None:
    """Keep selection, rollout, and cleanup independent of framework service implementations.

    Args:
        module: Registry module whose dependency direction is protected.
    """
    root = Path(__file__).resolve().parents[1]
    syntax = ast.parse((root / f"{module}.py").read_text())
    imported_modules = {
        node.module or "" for node in ast.walk(syntax) if isinstance(node, ast.ImportFrom)
    } | {
        alias.name
        for node in ast.walk(syntax)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    prohibited = (
        "django",
        "rest_framework",
        "Organization",
        "BotServer",
        "resource_registry.scope_adapters",
        "resource_registry.assistant",
        "resource_registry.authorization.registry_authorization_context_service",
    )
    assert not any(
        imported == prefix or imported.startswith(f"{prefix}.")
        for imported in imported_modules
        for prefix in prohibited
    )
