"""Verify actionable Registry access failures without weakening admission boundaries."""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import UUID

import pytest

from resource_registry.assistant.namespace_context import (
    AssistantNamespaceResolution,
    AssistantWorkspaceChoice,
)
from resource_registry.assistant.runtime_adapter import DataRegistryExplorationRoutingAdapter
from resource_registry.authorization.contracts import (
    AuthorizedNamespace,
    AuthorizedNamespaceSet,
    AuthorizedWorkspaceSet,
    RegistryAuthorizationContext,
)
from resource_registry.tests.bif_contract_helpers import (
    FakeContextManager,
    FakePersonaBifRelationshipManager,
)

_CONTEXT_BUILDER = (
    "resource_registry.assistant.runtime_adapter.RegistryAuthorizationContextService.build_context"
)


def _build_client(workspace_ids: tuple[int, ...] = (7,)) -> SimpleNamespace:
    workspace_manager = Mock()
    workspace_manager.values_list.return_value = workspace_ids
    return SimpleNamespace(
        persona=SimpleNamespace(
            pk=42,
            workspaces=workspace_manager,
            builtinfunctionpersonarelationship_set=FakePersonaBifRelationshipManager(
                ("resource_registry_list_datasets",),
            ),
        ),
        context_mgr=FakeContextManager(),
        callGenericFunction=Mock(
            side_effect=AssertionError("Denied requests must not call tools.")
        ),
    )


def _build_request() -> SimpleNamespace:
    return SimpleNamespace(
        user=SimpleNamespace(pk=49, is_authenticated=True, is_active=True),
        organization=SimpleNamespace(pk=34, resource_registry_enabled=True, is_active=True),
        session={"unrelated_conversation": "keep me"},
    )


def _build_context(
    workspace_ids: tuple[int, ...] = (7,), *, provisioned: bool = True
) -> RegistryAuthorizationContext:
    return RegistryAuthorizationContext(
        authorized_workspaces=AuthorizedWorkspaceSet(
            organization_id=34, user_id=49, workspace_ids=frozenset(workspace_ids)
        ),
        authorized_namespaces=AuthorizedNamespaceSet(
            mappings=tuple(
                AuthorizedNamespace(namespace_id=UUID(int=workspace_id), workspace_id=workspace_id)
                for workspace_id in workspace_ids
                if provisioned
            )
        ),
    )


@pytest.mark.parametrize("authenticated,persisted", [(False, True), (True, False)])
def test_unverified_identity_requests_sign_in_without_claiming_expiration(
    authenticated: bool, persisted: bool
) -> None:
    """Reject absent authentication or identity with recovery guidance and no tools.

    Args:
        authenticated: Whether the request reports successful authentication.
        persisted: Whether it carries a persisted user identifier.
    """
    request = _build_request()
    request.user.is_authenticated = authenticated
    request.user.pk = 49 if persisted else None
    client = _build_client()

    result = DataRegistryExplorationRoutingAdapter().try_route(
        client=client, request=request, statement="List available datasets."
    )

    assert result is not None
    assert result.request_payload["error_code"] == "resource_registry_authentication_required"
    assert "Sign out, then sign in again" in result.response_text
    assert "cookies for this site" in result.response_text
    assert "expired" not in result.response_text
    assert result.tool_calls == ()
    client.callGenericFunction.assert_not_called()
    assert request.session == {"unrelated_conversation": "keep me"}


@pytest.mark.parametrize(
    "configured,authorized,provisioned,expected_code",
    [
        ((), (7,), True, "resource_registry_persona_workspace_not_configured"),
        ((7,), (8,), True, "resource_registry_persona_workspace_unavailable"),
        ((7,), (), True, "resource_registry_persona_workspace_unavailable"),
        ((7,), (7,), False, "resource_registry_persona_workspace_unavailable"),
    ],
)
def test_workspace_failures_distinguish_configuration_from_unavailable_access(
    configured: tuple[int, ...],
    authorized: tuple[int, ...],
    provisioned: bool,
    expected_code: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Keep wrong-organization, permission, and provisioning denials private and closed.

    Args:
        configured: Workspaces explicitly assigned to the Persona.
        authorized: Workspaces admitted for this request's organization and user.
        provisioned: Whether the admitted Workspaces have namespace mappings.
        expected_code: Diagnostic category expected for the failed admission.
        caplog: Captured logs for checking safe diagnostic fields.
    """
    request = _build_request()
    request.session["resource_registry_tenant_id"] = "private-stale-context"
    request.COOKIES = {"sessionid": "private-session-cookie"}
    request.META = {"HTTP_AUTHORIZATION": "Bearer private-token"}
    client = _build_client(configured)
    client.persona.name = "private-persona-name"
    namespace_service = Mock()
    adapter = DataRegistryExplorationRoutingAdapter(namespace_context_service=namespace_service)

    with (
        patch(_CONTEXT_BUILDER, return_value=_build_context(authorized, provisioned=provisioned)),
        patch.object(logging.getLogger("app-logger"), "propagate", True),
        caplog.at_level(logging.WARNING, logger="app-logger"),
    ):
        result = adapter.try_route(
            client=client, request=request, statement="List available datasets."
        )

    assert result is not None
    assert result.request_payload["error_code"] == expected_code
    if configured:
        assert "authorized Workspace supported by this Persona" in result.response_text
        assert "interface" in result.response_text
        namespace_service.clear_context.assert_called_once_with(request)
    else:
        assert "Ask an administrator to assign an enabled Workspace" in result.response_text
        assert "Sign out" not in result.response_text
    assert result.tool_calls == ()
    namespace_service.resolve.assert_not_called()
    client.callGenericFunction.assert_not_called()
    assert request.session["unrelated_conversation"] == "keep me"
    record = next(record for record in caplog.records if record.name == "app-logger")
    assert record.__dict__["registry_error_code"] == expected_code
    assert record.__dict__["registry_actor_id"] == 49
    assert record.__dict__["registry_organization_id"] == 34
    assert record.__dict__["registry_persona_id"] == 42
    for sensitive_value in (
        "private-stale-context",
        "private-session-cookie",
        "private-token",
        "private-persona-name",
    ):
        assert sensitive_value not in result.response_text
        assert sensitive_value not in json.dumps(result.request_payload)
        assert sensitive_value not in str(record.__dict__)


@pytest.mark.parametrize(
    "source", ["request", "session", "conversation", "persona", "request_persona"]
)
def test_malformed_legacy_context_directs_recovery_to_the_correct_owner(source: str) -> None:
    """Distinguish broken conversation state from persistent Persona configuration.

    Args:
        source: Location holding the malformed dedicated legacy tenant value.
    """
    request = _build_request()
    client = _build_client()
    del client.persona.workspaces  # Exercise the legacy compatibility path explicitly.
    invalid_context = {"resource_registry_tenant_id": "private-invalid-value"}
    if source == "request":
        request.resource_registry_tenant_id = invalid_context["resource_registry_tenant_id"]
    elif source == "session":
        request.session.update(invalid_context)
    elif source == "conversation":
        client.context_mgr.get_all_registry_entries = Mock(return_value=invalid_context)
    elif source == "persona":
        client.persona.metadata = invalid_context
    else:
        request.persona = SimpleNamespace(metadata=invalid_context)
    namespace_service = Mock()
    namespace_service.resolve.return_value = AssistantNamespaceResolution(status="unavailable")
    adapter = DataRegistryExplorationRoutingAdapter(namespace_context_service=namespace_service)

    with patch(_CONTEXT_BUILDER, return_value=_build_context()):
        result = adapter.try_route(
            client=client, request=request, statement="List available datasets."
        )

    assert result is not None
    if source in {"persona", "request_persona"}:
        assert (
            result.request_payload["error_code"]
            == "resource_registry_persona_context_misconfigured"
        )
        assert "Ask an administrator" in result.response_text
        assert "Sign out" not in result.response_text
    else:
        assert result.request_payload["error_code"] == "invalid_resource_registry_tenant_context"
        assert "Sign out, then sign in again" in result.response_text
        assert "start a new conversation" in result.response_text
    assert "private-invalid-value" not in result.response_text
    assert result.tool_calls == ()
    client.callGenericFunction.assert_not_called()


@pytest.mark.parametrize("organization_state", ["missing", "disabled"])
def test_organization_failures_explain_site_or_enablement_action(organization_state: str) -> None:
    """Explain organization failures and preserve cleanup for disabled organizations.

    Args:
        organization_state: Whether organization resolution or enablement failed.
    """
    request = _build_request()
    client = _build_client()
    request.session["resource_registry_active_dataset_id"] = str(UUID(int=7))
    if organization_state == "missing":
        request.organization = None
    else:
        request.organization.resource_registry_enabled = False

    result = DataRegistryExplorationRoutingAdapter().try_route(
        client=client, request=request, statement="List available datasets."
    )

    assert result is not None
    if organization_state == "missing":
        assert result.request_payload["error_code"] == "resource_registry_organization_unavailable"
        assert result.request_payload["status"] == "organization_context_unavailable"
        assert "Open the chat from your organization's site" in result.response_text
    else:
        assert result.request_payload["error_code"] == "resource_registry_organization_disabled"
        assert result.request_payload["status"] == "no_authorized_namespace"
        assert "Resource Registry is disabled" in result.response_text
        assert "sign in" not in result.response_text
        assert "resource_registry_active_dataset_id" not in request.session
    assert request.session["unrelated_conversation"] == "keep me"
    assert result.tool_calls == ()
    client.callGenericFunction.assert_not_called()


def test_authorized_listing_does_not_request_sign_in_for_an_unused_stale_legacy_hint() -> None:
    """Use canonical admission and preserve empty-list behavior despite an obsolete hint."""
    request = _build_request()
    request.session["resource_registry_tenant_id"] = "obsolete-legacy-context"
    client = _build_client()
    client.callGenericFunction.side_effect = None
    client.callGenericFunction.return_value = json.dumps(
        {"success": True, "data": {"datasets": []}}
    )
    namespace_service = Mock()
    namespace_service.resolve.return_value = AssistantNamespaceResolution(
        status="selected",
        selected=AssistantWorkspaceChoice(7, "Main", "main", str(UUID(int=7))),
    )
    adapter = DataRegistryExplorationRoutingAdapter(namespace_context_service=namespace_service)

    with patch(_CONTEXT_BUILDER, return_value=_build_context()):
        result = adapter.try_route(
            client=client, request=request, statement="List available datasets."
        )

    assert result is not None
    assert "No authorized registered datasets were found" in result.response_text
    assert "sign in" not in result.response_text
    assert "error_code" not in result.request_payload
    client.callGenericFunction.assert_called_once_with(
        request, "resource_registry_list_datasets", {"tenant_id": str(UUID(int=7))}
    )


def test_multiple_authorized_workspaces_request_selection_instead_of_session_recovery() -> None:
    """Keep explicit Workspace selection separate from authentication failure."""
    request = _build_request()
    client = _build_client((7, 8))
    namespace_service = Mock()
    namespace_service.resolve.return_value = AssistantNamespaceResolution(
        status="multiple",
        choices=(
            AssistantWorkspaceChoice(7, "First", "first", str(UUID(int=7))),
            AssistantWorkspaceChoice(8, "Second", "second", str(UUID(int=8))),
        ),
    )
    adapter = DataRegistryExplorationRoutingAdapter(namespace_context_service=namespace_service)

    with patch(_CONTEXT_BUILDER, return_value=_build_context((7, 8))):
        result = adapter.try_route(
            client=client, request=request, statement="List available datasets."
        )

    assert result is not None
    assert result.request_payload["status"] == "workspace_selection_required"
    assert result.request_payload["workspace_ids"] == [7, 8]
    assert "Which Workspace would you like to use?" in result.response_text
    assert "sign in" not in result.response_text
    assert (
        namespace_service.resolve.call_args.kwargs["require_explicit_workspace_selection"] is True
    )
    client.callGenericFunction.assert_not_called()


def test_no_namespace_returns_access_guidance_without_requesting_a_tenant_identifier() -> None:
    """Keep missing generic namespace admission closed with administrator guidance."""
    request = _build_request()
    client = _build_client()
    del client.persona.workspaces
    namespace_service = Mock()
    namespace_service.resolve.return_value = AssistantNamespaceResolution(status="none")
    adapter = DataRegistryExplorationRoutingAdapter(namespace_context_service=namespace_service)

    with patch(_CONTEXT_BUILDER, return_value=_build_context(())):
        result = adapter.try_route(
            client=client, request=request, statement="List available datasets."
        )

    assert result is not None
    assert result.request_payload["error_code"] == "resource_registry_namespace_unavailable"
    assert result.request_payload["status"] == "no_authorized_namespace"
    assert "Ask an administrator" in result.response_text
    assert "tenant" not in result.response_text.lower()
    client.callGenericFunction.assert_not_called()
