"""Exercise Registry scope selection with PostgreSQL and the real HTTP middleware."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from django.contrib.auth.models import Permission, User
from django.contrib.sessions.backends.signed_cookies import SessionStore
from guardian.shortcuts import assign_perm
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from BotServer.workspace_scope import invalidate_organization_host_cache
from BotServer.workspace_session import WorkspaceScopedSession
from Organization.models import Organization, OrganizationHost, Workspace, WorkspaceMembership
from resource_registry.assistant.namespace_context import AssistantNamespaceContextService
from resource_registry.assistant.runtime_adapter import DataRegistryExplorationRoutingAdapter
from resource_registry.authorization.registry_authorization_context_service import (
    RegistryAuthorizationContextService,
)
from resource_registry.bifs.read_only_namespace_admission import (
    ReadOnlyBifNamespaceAdmissionService,
)
from resource_registry.models import LargeFileUploadSession
from resource_registry.namespace_selection import RegistryScopeSelectionError
from resource_registry.read_auth.exceptions import DataRegistryAuthorizationError
from resource_registry.scope_adapters import select_request_registry_namespace
from resource_registry.scope_policy import get_registry_scope_policy
from resource_registry.tests.test_assistant_namespace_context import (
    _RuntimeClient,
    _WorkspaceManager,
)
from resource_registry.tests.test_large_file_upload_boundary import _FakeProvider
from resource_registry.uploads.services import LargeFileUploadService

pytestmark = pytest.mark.django_db


@dataclass
class ScopeEnvironment:
    """Hold an authenticated caller and two independently owned Registry namespaces."""

    user: User
    organization: Organization
    first: Workspace
    second: Workspace
    client: APIClient

    def build_request(self) -> SimpleNamespace:
        """Build a BIF request using trusted context and an empty conversation.

        Returns:
            A request scoped to the first Workspace.
        """
        return SimpleNamespace(
            user=self.user,
            organization=self.organization,
            workspace=self.first,
            session={},
            headers={},
        )


@pytest.fixture(autouse=True)
def strict_scope_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enable the new contract for these tests.

    Args:
        monkeypatch: Environment restoration fixture.
    """
    monkeypatch.setenv("RESOURCE_REGISTRY_SCOPE_POLICY", "strict")


@pytest.fixture
def environment() -> ScopeEnvironment:
    """Create persisted tenant fixtures and a token-authenticated HTTP client.

    Returns:
        Two authorized Workspaces and their host Organization.
    """
    creator = User.objects.create_user(username="scope-fixture-creator")
    user = User.objects.create_user(username="scope-caller")
    organization = Organization.objects.create(
        name="Scope Organization",
        slug="scope-organization",
        last_modified_by=creator,
        resource_registry_enabled=True,
    )
    invalidate_organization_host_cache("testserver")
    OrganizationHost.objects.create(organization=organization, fqdn="testserver")
    assign_perm("view_organization", user, organization)
    workspaces = []
    for slug in ("alpha", "beta"):
        workspace = Workspace.objects.create(
            organization=organization,
            name=slug.title(),
            slug=slug,
            last_modified_by=creator,
            resource_registry_enabled=True,
        )
        WorkspaceMembership.objects.create(workspace=workspace, user=user, role="member")
        workspaces.append(workspace)
    client = APIClient()
    token = Token.objects.create(user=user)
    client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
    client.defaults["HTTP_X_WORKSPACE"] = workspaces[0].slug
    return ScopeEnvironment(user, organization, workspaces[0], workspaces[1], client)


def test_guardian_open_workspace_access_does_not_grant_upload_capability(
    environment: ScopeEnvironment,
) -> None:
    """Admit OPEN through Guardian while preserving the independent upload permission.

    Args:
        environment: Persisted scope fixtures.
    """
    WorkspaceMembership.objects.filter(user=environment.user).delete()
    Workspace.objects.filter(pk=environment.first.pk).update(state="open")
    context = RegistryAuthorizationContextService().build_context(
        user=environment.user,
        organization=environment.organization,
    )
    assert context.authorized_namespaces.contains_namespace(
        environment.first.resource_registry_namespace.pk
    )
    response = environment.client.post(
        "/api/v1/resource-registry/uploads/",
        {"filename": "rows.csv", "content_type": "text/csv", "size_bytes": 12},
        format="json",
        HTTP_IDEMPOTENCY_KEY="scope-upload",
    )
    assert response.status_code == 403
    assert response.data["code"] == "UPLOAD_CAPABILITY_DENIED"


@pytest.mark.parametrize("mode", ("workspace", "organization", "explicit"))
def test_http_scope_modes_reach_only_the_selected_namespace(
    environment: ScopeEnvironment, mode: str
) -> None:
    """Select the correct owner using real DRF authentication and middleware.

    Args:
        environment: Persisted scope fixtures.
        mode: Public scope mode to exercise.
    """
    headers = {"HTTP_X_REGISTRY_SCOPE": mode}
    expected = str(environment.first.resource_registry_namespace.pk)
    if mode == "organization":
        expected = str(environment.organization.resource_registry_organization_namespace.pk)
        headers["HTTP_X_WORKSPACE"] = "inaccessible-workspace"
    if mode == "explicit":
        expected = str(environment.second.resource_registry_namespace.pk)
        headers["HTTP_X_TENANT_ID"] = expected
        headers["HTTP_X_WORKSPACE"] = "inaccessible-workspace"
    runs = Mock()
    runs.list_runs.return_value = []
    with patch(
        "resource_registry.api.transformation.views.get_transformation_rest_application_services",
        return_value=SimpleNamespace(runs=runs),
    ):
        response = environment.client.get("/api/v1/transformations/runs/", **headers)
    assert response.status_code == 200, response.data
    assert runs.list_runs.call_args.kwargs["context"].tenant_id == expected


@pytest.mark.parametrize("mode", ("workspace", "explicit"))
def test_conflicting_selectors_never_reach_execution(
    environment: ScopeEnvironment, mode: str
) -> None:
    """Reject namespace conflicts before any transformation service executes.

    Args:
        environment: Persisted scope fixtures.
        mode: Scope mode under test.
    """
    request = environment.build_request()
    request.headers = {
        "X-Registry-Scope": mode,
        "X-Tenant-ID": str(environment.first.resource_registry_namespace.pk),
    }
    with pytest.raises(RegistryScopeSelectionError, match="Conflicting"):
        select_request_registry_namespace(
            request=request,
            namespace_selectors=(environment.second.resource_registry_namespace.pk,),
        )


def test_foreign_organization_namespace_is_rejected(environment: ScopeEnvironment) -> None:
    """Reject an explicit namespace from another host Organization.

    Args:
        environment: Persisted scope fixtures.
    """
    foreign = Organization.objects.create(
        name="Foreign",
        slug="foreign",
        last_modified_by=environment.user,
        resource_registry_enabled=True,
    )
    response = environment.client.get(
        "/api/v1/transformations/runs/",
        HTTP_X_REGISTRY_SCOPE="explicit",
        HTTP_X_TENANT_ID=str(foreign.resource_registry_organization_namespace.pk),
    )
    assert response.status_code == 403
    assert response.data["code"] == "registry_scope_not_authorized"


@pytest.mark.parametrize("policy", ("legacy", "observe", "strict"))
def test_rollout_preserves_legacy_selection_until_strict(
    environment: ScopeEnvironment,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    policy: str,
) -> None:
    """Observe a different authorized destination without exposing identifiers in logs.

    Args:
        environment: Persisted scope fixtures.
        monkeypatch: Policy override fixture.
        caplog: Captured observation messages.
        policy: Rollout mode under test.
    """
    monkeypatch.setenv("RESOURCE_REGISTRY_SCOPE_POLICY", policy)
    runs = Mock()
    runs.list_runs.return_value = []
    second_namespace = str(environment.second.resource_registry_namespace.pk)
    with (
        caplog.at_level(logging.INFO, logger="resource_registry.scope_policy"),
        patch(
            "resource_registry.api.transformation.views.get_transformation_rest_application_services",
            return_value=SimpleNamespace(runs=runs),
        ),
    ):
        response = environment.client.get(
            "/api/v1/transformations/runs/",
            HTTP_X_TENANT_ID=second_namespace,
        )
    assert response.status_code == (400 if policy == "strict" else 200)
    if policy == "strict":
        runs.list_runs.assert_not_called()
    else:
        assert runs.list_runs.call_args.kwargs["context"].tenant_id == second_namespace
    events = [
        record.getMessage()
        for record in caplog.records
        if record.name == "resource_registry.scope_policy"
    ]
    assert bool(events) == (policy == "observe")
    assert all(second_namespace not in event for event in events)


def test_invalid_policy_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid silently disabling strict admission after a configuration typo.

    Args:
        monkeypatch: Environment restoration fixture.
    """
    monkeypatch.setenv("RESOURCE_REGISTRY_SCOPE_POLICY", "strcit")
    assert get_registry_scope_policy() == "strict"


@pytest.mark.parametrize("policy", ("legacy", "observe", "strict"))
def test_chat_lists_datasets_in_request_workspace_without_selection_prompt(
    environment: ScopeEnvironment,
    monkeypatch: pytest.MonkeyPatch,
    policy: str,
) -> None:
    """Use the request Workspace even with multiple choices and stale conversation state.

    Args:
        environment: Persisted scope fixtures.
        monkeypatch: Policy override fixture.
        policy: REST rollout mode that must not affect assistant selection.
    """
    monkeypatch.setenv("RESOURCE_REGISTRY_SCOPE_POLICY", policy)
    request = environment.build_request()
    old_namespace = str(environment.second.resource_registry_namespace.pk)
    request.resource_registry_tenant_id = old_namespace
    request.session = {
        "resource_registry_tenant_id": old_namespace,
        "resource_registry_persona_workspace_namespace_id": old_namespace,
        "resource_registry_selected_dataset_ids": ["old-dataset"],
        "chat_history": ["preserve"],
    }
    client = _RuntimeClient()
    client.persona.workspaces = _WorkspaceManager((environment.first.pk, environment.second.pk))
    client.results = [{"success": True, "data": {"datasets": []}}]

    result = DataRegistryExplorationRoutingAdapter().try_route(
        client=client, request=request, statement="List available datasets."
    )

    assert result is not None
    current_namespace = str(environment.first.resource_registry_namespace.pk)
    assert client.calls == [("resource_registry_list_datasets", {"tenant_id": current_namespace})]
    assert "Which Workspace" not in result.response_text
    assert request.session["resource_registry_tenant_id"] == current_namespace
    assert "resource_registry_selected_dataset_ids" not in request.session
    assert request.session["chat_history"] == ["preserve"]


@pytest.mark.parametrize("policy", ("legacy", "observe", "strict"))
@pytest.mark.parametrize("invalid_workspace", ("missing", "unauthorized", "foreign", "unsupported"))
def test_chat_rejects_invalid_request_workspace_without_falling_back(
    environment: ScopeEnvironment,
    monkeypatch: pytest.MonkeyPatch,
    policy: str,
    invalid_workspace: str,
) -> None:
    """Reject invalid request scope despite authorized conversation and Persona alternatives.

    Args:
        environment: Persisted scope fixtures.
        monkeypatch: Policy override fixture.
        policy: REST rollout mode that must not affect assistant selection.
        invalid_workspace: Invalid request context to exercise.
    """
    monkeypatch.setenv("RESOURCE_REGISTRY_SCOPE_POLICY", policy)
    request = environment.build_request()
    request.session["resource_registry_tenant_id"] = str(
        environment.second.resource_registry_namespace.pk
    )
    client = _RuntimeClient()
    client.persona.workspaces = _WorkspaceManager((environment.second.pk,))
    if invalid_workspace == "missing":
        request.workspace = None
    elif invalid_workspace == "unauthorized":
        WorkspaceMembership.objects.filter(
            user=environment.user, workspace=environment.first
        ).delete()
    elif invalid_workspace == "foreign":
        request.workspace = SimpleNamespace(
            pk=environment.first.pk, organization_id=environment.organization.pk + 1, is_active=True
        )

    result = DataRegistryExplorationRoutingAdapter().try_route(
        client=client, request=request, statement="List available datasets."
    )

    assert result is not None
    assert result.request_payload["error_code"] == "resource_registry_persona_workspace_unavailable"
    assert client.calls == []
    assert "Which Workspace" not in result.response_text
    assert "resource_registry_tenant_id" not in request.session


@pytest.mark.parametrize("policy", ("legacy", "observe", "strict"))
@pytest.mark.parametrize("persona_workspace_count", (1, 2))
def test_chat_requires_interface_switch_without_executing_a_bif(
    environment: ScopeEnvironment,
    persona_workspace_count: int,
    monkeypatch: pytest.MonkeyPatch,
    policy: str,
) -> None:
    """Stop a cross-Workspace chat instruction before executing any tool.

    Args:
        environment: Persisted scope fixtures.
        persona_workspace_count: Number of Workspaces configured on the Persona.
        monkeypatch: Policy override fixture.
        policy: REST rollout mode that must not affect assistant selection.
    """
    monkeypatch.setenv("RESOURCE_REGISTRY_SCOPE_POLICY", policy)
    request = environment.build_request()
    client = _RuntimeClient()
    configured = (environment.first.pk, environment.second.pk)[:persona_workspace_count]
    client.persona.workspaces = _WorkspaceManager(configured)
    result = DataRegistryExplorationRoutingAdapter().try_route(
        client=client,
        request=request,
        statement="Use Beta and list its datasets.",
    )
    assert result is not None
    assert client.calls == []
    assert "interface" in str(result).lower()
    assert request.session.get("resource_registry_tenant_id") != str(
        environment.second.resource_registry_namespace.pk
    )


@pytest.mark.parametrize("policy", ("legacy", "observe", "strict"))
@pytest.mark.parametrize("omitted_namespace", (None, "", " "))
def test_bif_arguments_and_headers_cannot_override_active_workspace(
    environment: ScopeEnvironment,
    monkeypatch: pytest.MonkeyPatch,
    policy: str,
    omitted_namespace: str | None,
) -> None:
    """Reject a BIF's different destination even with an explicit HTTP scope header.

    Args:
        environment: Persisted scope fixtures.
        monkeypatch: Policy override fixture.
        policy: REST rollout mode that must not affect assistant selection.
        omitted_namespace: Supported representations of an omitted BIF namespace argument.
    """
    monkeypatch.setenv("RESOURCE_REGISTRY_SCOPE_POLICY", policy)
    request = environment.build_request()
    request.headers = {"X-Registry-Scope": "explicit"}
    service = ReadOnlyBifNamespaceAdmissionService()
    with pytest.raises(DataRegistryAuthorizationError, match="interface"):
        service.admit_namespace(
            request=request,
            requested_namespace_id=str(environment.second.resource_registry_namespace.pk),
        )
    assert service.admit_namespace(
        request=request, requested_namespace_id=omitted_namespace
    ) == str(environment.first.resource_registry_namespace.pk)


def test_old_namespace_state_is_cleared_for_identical_slugs_across_organizations(
    environment: ScopeEnvironment,
) -> None:
    """Discard Registry state from a colliding global session bucket without touching chat history.

    Args:
        environment: Persisted scope fixtures.
    """
    store = SessionStore()
    previous = Workspace(pk=999999, organization_id=999999, slug=environment.first.slug)
    old_session = WorkspaceScopedSession(store, previous)
    old_session["resource_registry_tenant_id"] = "00000000-0000-0000-0000-000000000001"
    old_session["resource_registry_selected_dataset_ids"] = ["old-dataset"]
    old_session["resource_registry_pending_derived_dataset_plan"] = {"stale": True}
    old_session["resource_registry_campaign_dataset_preflight:old"] = {"stale": True}
    old_session["unrelated_chat_history"] = "unchanged"
    request = environment.build_request()
    request.session = WorkspaceScopedSession(store, environment.first)
    resolution = AssistantNamespaceContextService().resolve(
        request=request, statement="List datasets"
    )
    assert resolution.selection_changed
    assert request.session["resource_registry_tenant_id"] == str(
        environment.first.resource_registry_namespace.pk
    )
    assert "resource_registry_selected_dataset_ids" not in request.session
    assert "resource_registry_pending_derived_dataset_plan" not in request.session
    assert "resource_registry_campaign_dataset_preflight:old" not in request.session
    assert request.session["unrelated_chat_history"] == "unchanged"


@pytest.mark.parametrize("revocation", ("membership", "workspace", "organization"))
def test_revocation_invalidates_registry_conversation(
    environment: ScopeEnvironment, revocation: str
) -> None:
    """Remove Registry state when the active namespace loses admission.

    Args:
        environment: Persisted scope fixtures.
        revocation: Authorization boundary to revoke.
    """
    request = environment.build_request()
    request.session = {
        "resource_registry_tenant_id": str(environment.first.resource_registry_namespace.pk),
        "resource_registry_selected_dataset_ids": ["old"],
    }
    if revocation == "membership":
        WorkspaceMembership.objects.filter(
            user=environment.user, workspace=environment.first
        ).delete()
    elif revocation == "workspace":
        Workspace.objects.filter(pk=environment.first.pk).update(resource_registry_enabled=False)
    else:
        environment.organization.resource_registry_enabled = False
        environment.organization.save(update_fields=["resource_registry_enabled"])
    result = AssistantNamespaceContextService().resolve(request=request, statement="List datasets")
    assert result.status != "selected"
    assert "resource_registry_selected_dataset_ids" not in request.session


def test_upload_creation_derives_workspace_and_preserves_replay_and_ownership(
    environment: ScopeEnvironment,
) -> None:
    """Keep durable upload identity stable and enforce scope on existing uploads.

    Args:
        environment: Persisted scope fixtures.
    """
    environment.user.user_permissions.add(
        Permission.objects.get(codename="create_large_file_upload")
    )
    service = LargeFileUploadService(provider=_FakeProvider())
    payload = {"filename": "rows.csv", "content_type": "text/csv", "size_bytes": 12}
    endpoint = "/api/v1/resource-registry/uploads/"
    with patch(
        "resource_registry.api.uploads.views.get_large_file_upload_service", return_value=service
    ):
        created = environment.client.post(
            endpoint, payload, format="json", HTTP_IDEMPOTENCY_KEY="scope-create"
        )
        assert created.status_code == 201, created.data
        repeated = environment.client.post(
            endpoint, payload, format="json", HTTP_IDEMPOTENCY_KEY="scope-create"
        )
        assert repeated.status_code == 200
        assert created.data["upload_id"] == repeated.data["upload_id"]
        upload_id = created.data["upload_id"]
        mismatch = environment.client.get(
            f"{endpoint}{upload_id}/", HTTP_X_WORKSPACE=environment.second.slug
        )
        assert mismatch.status_code == 400
        explicit = environment.client.get(
            f"{endpoint}{upload_id}/",
            HTTP_X_WORKSPACE=environment.second.slug,
            HTTP_X_REGISTRY_SCOPE="explicit",
        )
        assert explicit.status_code == 200
        cancelled = environment.client.post(f"{endpoint}{upload_id}/cancel/")
        assert cancelled.status_code == 200
    upload = LargeFileUploadSession.objects.get(pk=upload_id)
    assert upload.workspace_id == environment.first.pk
    assert upload.registry_namespace_id == environment.first.resource_registry_namespace.pk


def test_shared_middleware_rejection_remains_outside_registry(
    environment: ScopeEnvironment,
) -> None:
    """Document the pre-adapter limitation for session-authenticated requests.

    Args:
        environment: Persisted scope fixtures.
    """
    client = APIClient()
    client.force_login(environment.user)
    response = client.get(
        "/api/v1/transformations/runs/",
        HTTP_X_REGISTRY_SCOPE="organization",
        HTTP_X_WORKSPACE="inaccessible-workspace",
    )
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_workspace"


def test_bif_session_update_does_not_authorize_old_manager_artifacts(
    environment: ScopeEnvironment,
) -> None:
    """Reject old manager state after a BIF has already updated session namespace markers.

    Args:
        environment: Persisted authorized Workspace fixtures.
    """
    request = environment.build_request()
    current_namespace = str(environment.first.resource_registry_namespace.pk)
    old_namespace = str(environment.second.resource_registry_namespace.pk)
    plan_key = "resource_registry_pending_derived_dataset_plan"
    selection_key = "resource_registry_selected_dataset_ids"
    old_dataset = "00000000-0000-0000-0000-000000000010"
    plan = {"tenant_id": old_namespace, "dataset_id": old_dataset, "threshold": "100"}
    request.session = {"resource_registry_tenant_id": old_namespace, plan_key: plan}
    client = _RuntimeClient()
    client.context_mgr.entries = {
        plan_key: {"type": "json", "value": plan},
        selection_key: {"type": "json", "value": [old_dataset]},
        "unrelated_history": {"type": "json", "value": ["preserve"]},
    }
    assert (
        ReadOnlyBifNamespaceAdmissionService().admit_namespace(
            request=request,
            requested_namespace_id=None,
        )
        == current_namespace
    )
    assert plan_key in client.context_mgr.entries
    result = DataRegistryExplorationRoutingAdapter().try_route(
        client=client,
        request=request,
        statement="yes, derived dataset name: Result",
    )
    assert client.calls == []
    assert result is None or not result.tool_calls
    assert plan_key not in client.context_mgr.entries
    assert selection_key not in client.context_mgr.entries
    assert "unrelated_history" in client.context_mgr.entries
