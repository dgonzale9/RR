"""Assistant namespace resolution and Workspace selection tests."""

from __future__ import annotations

import json
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from guardian.shortcuts import remove_perm

from Organization.models import Organization, OrganizationMembership, Workspace
from resource_registry.assistant.namespace_context import AssistantNamespaceContextService
from resource_registry.assistant.persona_workspace_scope import (
    PERSONA_WORKSPACE_SELECTION_SESSION_KEY,
)
from resource_registry.assistant.runtime_adapter import DataRegistryExplorationRoutingAdapter
from resource_registry.authorization.registry_authorization_context_service import (
    RegistryAuthorizationContextService,
)
from resource_registry.bifs.read_only_namespace_admission import (
    ReadOnlyBifNamespaceAdmissionService,
)
from resource_registry.services.registry_namespace_provisioning_service import (
    RegistryNamespaceProvisioningService,
)
from resource_registry.tests.bif_contract_helpers import (
    FakePersonaBifRelationshipManager,
)
from resource_registry.tests.workspace_permission_helpers import add_workspace_user

User = get_user_model()


def _ensure_owner_object_permissions() -> None:
    """Create permissions required by the secureable-model signal in an isolated test DB."""
    for model in (Organization, Workspace):
        content_type = ContentType.objects.get_for_model(model)
        for action in ("view", "change", "delete"):
            Permission.objects.get_or_create(
                content_type=content_type,
                codename=f"{action}_{model._meta.model_name}",
                defaults={"name": f"Can {action} {model._meta.verbose_name}"},
            )


class _ContextManager:
    """Provide the empty context-manager surface required by deterministic listing."""

    def __init__(self) -> None:
        """Initialize mutable registry entries for context-clearing assertions."""
        self.entries: dict[str, object] = {}
        self.removed_keys: list[str] = []

    def get_all_registry_entries(self, request: object) -> dict[str, object]:
        """Return no legacy runtime context.

        Args:
            request: Current assistant request.

        Returns:
            An empty context mapping.
        """
        _ = request
        return dict(self.entries)

    def remove_registry_entry(self, request: object, key: str) -> None:
        """Remove and record one registry entry.

        Args:
            request: Current assistant request.
            key: Registry entry key to remove.
        """
        _ = request
        self.entries.pop(key, None)
        self.removed_keys.append(key)


class _WorkspaceManager:
    """Expose the Persona Workspace IDs used by the scoped runtime policy."""

    def __init__(self, workspace_ids: tuple[int, ...]) -> None:
        """Store the configured Workspace identifiers."""
        self._workspace_ids = workspace_ids

    def values_list(self, _field_name: str, *, flat: bool) -> tuple[int, ...]:
        """Return the configured Workspace identifiers."""
        assert flat is True
        return self._workspace_ids


class _RuntimeClient:
    """Record deterministic BIF calls made by the assistant adapter."""

    def __init__(self) -> None:
        """Initialize a configurable Persona and an empty result queue."""
        self.persona = SimpleNamespace(
            name="Workspace Data Analyst",
            builtinfunctionpersonarelationship_set=FakePersonaBifRelationshipManager(
                ("resource_registry_list_datasets",),
            ),
        )
        self.context_mgr = _ContextManager()
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.results: list[dict[str, object]] = []

    def callGenericFunction(
        self,
        request: object,
        function_name: str,
        function_args: dict[str, object],
    ) -> str:
        """Return the next configured BIF payload and record canonical arguments.

        Args:
            request: Current assistant request.
            function_name: Dispatched BIF name.
            function_args: Canonical BIF arguments.

        Returns:
            A JSON-encoded configured payload.
        """
        _ = request
        self.calls.append((function_name, dict(function_args)))
        return json.dumps(self.results.pop(0))


class AssistantNamespaceContextTests(TestCase):
    """Verify chat namespace selection derives only from authorized Workspaces."""

    def setUp(self) -> None:
        """Create one member with two authorized namespace mappings."""
        _ensure_owner_object_permissions()
        self.user = User.objects.create_user(username="assistant-namespace-user")
        self.organization = Organization.objects.create(
            name="Assistant Namespace Organization",
            slug="assistant-namespace-organization",
            last_modified_by=self.user,
        )
        OrganizationMembership.objects.filter(
            organization=self.organization,
            user=self.user,
        ).delete()
        remove_perm(Organization.MANAGE_PERMISSION_CODENAME, self.user, self.organization)
        remove_perm(Organization.VIEW_PERMISSION_CODENAME, self.user, self.organization)
        self.first_workspace = self._create_workspace("first-workspace")
        self.second_workspace = self._create_workspace("second-workspace")
        self.foreign_workspace = self._create_workspace("foreign-workspace", assigned=False)
        Organization.objects.filter(pk=self.organization.pk).update(resource_registry_enabled=True)
        Workspace.objects.filter(
            pk__in=(self.first_workspace.pk, self.second_workspace.pk, self.foreign_workspace.pk),
        ).update(resource_registry_enabled=True)
        self.organization.refresh_from_db()
        self.first_workspace.refresh_from_db()
        self.second_workspace.refresh_from_db()
        self.foreign_workspace.refresh_from_db()
        provisioning_service = RegistryNamespaceProvisioningService()
        self.first_namespace = provisioning_service.provision_new_namespace(
            self.first_workspace.pk,
        )
        self.second_namespace = provisioning_service.provision_new_namespace(
            self.second_workspace.pk,
        )
        self.foreign_namespace = provisioning_service.provision_new_namespace(
            self.foreign_workspace.pk,
        )

    def test_multiple_authorized_workspaces_require_request_workspace(self) -> None:
        """Reject missing request scope instead of offering conversation choices."""
        request = self._request()
        resolution = AssistantNamespaceContextService().resolve(
            request=request, statement="List the datasets available to me."
        )
        self.assertEqual(resolution.status, "workspace_change_required")
        self.assertEqual(resolution.choices, ())
        self.assertIsNone(resolution.selected)
        self.assertFalse(hasattr(request, "resource_registry_tenant_id"))

    def test_adapter_requires_request_workspace_for_organization_follow_up(self) -> None:
        """Keep organization follow-ups from bypassing missing request scope."""
        request = self._request()
        client = _RuntimeClient()
        adapter = DataRegistryExplorationRoutingAdapter()
        for statement in (
            "List all datasets available to me by organization and workspace.",
            "Which Organization do these Workspaces belong to?",
        ):
            result = adapter.try_route(client=client, request=request, statement=statement)
            assert result is not None
            self.assertEqual(
                result.request_payload["error_code"], "resource_registry_workspace_change_required"
            )
            self.assertNotIn("Which Workspace", result.response_text)
        self.assertEqual(client.calls, [])

    def test_adapter_repeats_request_context_guidance_for_follow_up(self) -> None:
        """Keep retries closed until the request supplies an authorized Workspace."""
        request = self._request()
        client = _RuntimeClient()
        adapter = DataRegistryExplorationRoutingAdapter()
        for statement in ("List all datasets available to me.", "Try again"):
            result = adapter.try_route(client=client, request=request, statement=statement)
            assert result is not None
            self.assertEqual(
                result.request_payload["error_code"], "resource_registry_workspace_change_required"
            )
            self.assertIn("interface", result.response_text)
            self.assertNotIn("Which Workspace", result.response_text)
        self.assertEqual(client.calls, [])

    def test_scoped_persona_uses_request_workspace_before_registry_bif_call(self) -> None:
        """Use request scope immediately despite stale conversation namespace hints."""
        request = self._request(current_workspace_slug="second-workspace")
        request.resource_registry_tenant_id = self.first_namespace.id
        request.session["resource_registry_tenant_id"] = self.first_namespace.id
        client = _RuntimeClient()
        client.persona.workspaces = _WorkspaceManager(
            (self.first_workspace.pk, self.second_workspace.pk)
        )
        client.results = [{"success": True, "data": {"datasets": []}}]
        result = DataRegistryExplorationRoutingAdapter().try_route(
            client=client, request=request, statement="List all datasets available to me."
        )
        assert result is not None
        self.assertNotIn("Which Workspace", result.response_text)
        self.assertEqual(
            client.calls,
            [("resource_registry_list_datasets", {"tenant_id": self.second_namespace.id})],
        )
        self.assertIs(request.persona, client.persona)
        self.assertEqual(
            request.session[PERSONA_WORKSPACE_SELECTION_SESSION_KEY], self.second_namespace.id
        )

    def test_adapter_routes_registry_bif_for_organization_member_in_open_workspace(self) -> None:
        """Route a scoped Registry Persona through an enabled OPEN Workspace namespace."""
        organization_member = User.objects.create_user(username="assistant-open-workspace-member")
        OrganizationMembership.objects.create(
            organization=self.organization,
            user=organization_member,
        )
        self.foreign_workspace.state = Workspace.State.OPEN
        self.foreign_workspace.save(update_fields=["state"])
        request = SimpleNamespace(
            user=organization_member,
            organization=self.organization,
            workspace=self.foreign_workspace,
            session={},
        )
        client = _RuntimeClient()
        client.persona.workspaces = _WorkspaceManager((self.foreign_workspace.pk,))
        client.results = [{"success": True, "data": {"datasets": []}}]

        result = DataRegistryExplorationRoutingAdapter().try_route(
            client=client,
            request=request,
            statement="List the datasets available to me.",
        )

        assert result is not None
        self.assertEqual(
            client.calls,
            [("resource_registry_list_datasets", {"tenant_id": self.foreign_namespace.id})],
        )

    def test_adapter_rejects_legacy_tenant_context_without_an_organization(self) -> None:
        """Ignore legacy tenant hints when the trusted Organization cannot be resolved."""
        request = SimpleNamespace(
            user=self.user,
            organization=None,
            session={},
        )
        client = _RuntimeClient()

        result = DataRegistryExplorationRoutingAdapter().try_route(
            client=client,
            request=request,
            statement="List all datasets available to me.",
        )

        assert result is not None
        self.assertEqual(result.request_payload["status"], "organization_context_unavailable")
        self.assertIn("Open the chat from your organization's site", result.response_text)
        self.assertNotIn("tenant", result.response_text.casefold())
        self.assertEqual(client.calls, [])

    def test_administrator_cannot_fall_back_to_organization_without_workspace(self) -> None:
        """Require request Workspace scope even for administrators."""
        _ensure_owner_object_permissions()
        administrator = User.objects.create_user(username="organization-namespace-administrator")
        administrator.is_superuser = True
        administrator.save(update_fields=["is_superuser"])
        organization = Organization.objects.create(
            name="Organization Namespace Only",
            slug="organization-namespace-only",
            last_modified_by=administrator,
        )
        organization.resource_registry_enabled = True
        organization.save(update_fields=["resource_registry_enabled"])
        request = SimpleNamespace(
            user=administrator,
            organization=organization,
            session={},
        )

        resolution = AssistantNamespaceContextService().resolve(
            request=request,
            statement="List the datasets available to me.",
        )

        self.assertEqual(resolution.status, "workspace_change_required")
        self.assertIsNone(resolution.selected)
        self.assertNotIn("resource_registry_tenant_id", request.session)

    def test_organization_member_cannot_fall_back_without_workspace(self) -> None:
        """Require request Workspace scope even when an Organization namespace is authorized."""
        organization_member = User.objects.create_user(username="assistant-organization-reader")
        OrganizationMembership.objects.create(
            organization=self.organization,
            user=organization_member,
        )
        organization_namespace = (
            RegistryNamespaceProvisioningService().provision_new_organization_namespace(
                self.organization.pk,
            )
        )
        request = SimpleNamespace(
            user=organization_member,
            organization=self.organization,
            session={},
        )

        resolution = AssistantNamespaceContextService().resolve(
            request=request,
            statement="List the datasets available to me.",
        )

        self.assertEqual(resolution.status, "workspace_change_required")
        self.assertIsNone(resolution.selected)
        self.assertNotIn("resource_registry_tenant_id", request.session)

    def test_disabled_organization_clears_assistant_namespace_selection(self) -> None:
        """Fail closed and remove prior selection when Organization access is disabled."""
        request = self._request()
        request.resource_registry_tenant_id = self.first_namespace.id
        request.session["resource_registry_tenant_id"] = self.first_namespace.id
        request.session["resource_registry_workspace_id"] = self.first_workspace.pk
        request.session["resource_registry_active_dataset_id"] = (
            "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        )
        self.organization.resource_registry_enabled = False
        self.organization.save(update_fields=["resource_registry_enabled"])

        resolution = AssistantNamespaceContextService().resolve(
            request=request,
            statement="List the datasets available to me.",
        )

        self.assertEqual(resolution.status, "none")
        self.assertTrue(resolution.selection_changed)
        self.assertFalse(hasattr(request, "resource_registry_tenant_id"))
        self.assertNotIn("resource_registry_tenant_id", request.session)
        self.assertNotIn("resource_registry_workspace_id", request.session)
        self.assertNotIn("resource_registry_active_dataset_id", request.session)

    def test_disabled_workspaces_clear_assistant_namespace_selection(self) -> None:
        """Remove stale selection when Workspace enablement leaves no authorized Namespace."""
        request = self._request()
        request.resource_registry_tenant_id = self.first_namespace.id
        request.session.update(
            {
                "resource_registry_tenant_id": self.first_namespace.id,
                "resource_registry_workspace_id": self.first_workspace.pk,
                "resource_registry_active_dataset_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            }
        )
        Workspace.objects.filter(pk__in=(self.first_workspace.pk, self.second_workspace.pk)).update(
            resource_registry_enabled=False
        )

        resolution = AssistantNamespaceContextService().resolve(
            request=request,
            statement="List the datasets available to me.",
        )

        self.assertEqual(resolution.status, "none")
        self.assertTrue(resolution.selection_changed)
        self.assertFalse(hasattr(request, "resource_registry_tenant_id"))
        self.assertNotIn("resource_registry_tenant_id", request.session)
        self.assertNotIn("resource_registry_workspace_id", request.session)
        self.assertNotIn("resource_registry_active_dataset_id", request.session)

    def test_operation_context_rechecks_workspace_activity_before_selection(self) -> None:
        """Reject a Workspace deactivated after authorization but before presentation resolution."""
        request = self._request(current_workspace_slug="first-workspace")
        request.resource_registry_tenant_id = self.first_namespace.id
        request.session.update(
            {
                "resource_registry_tenant_id": self.first_namespace.id,
                "resource_registry_workspace_id": self.first_workspace.pk,
                "resource_registry_active_dataset_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            }
        )
        authorization_context = RegistryAuthorizationContextService().build_context(
            user=self.user,
            organization=self.organization,
        )
        service = AssistantNamespaceContextService()
        operation_context = service._create_routing_operation_context(
            request=request,
            authorization_context=authorization_context,
            persona_workspace_scope=None,
            configured_persona_workspace_ids=frozenset({self.first_workspace.pk}),
        )
        Workspace.objects.filter(pk=self.first_workspace.pk).update(is_active=False)

        resolution = service._resolve_with_routing_operation_context(
            request=request,
            statement="List the datasets available to me.",
            operation_context=operation_context,
            required_workspace_ids=frozenset({self.first_workspace.pk}),
            require_explicit_workspace_selection=False,
        )

        self.assertEqual(resolution.status, "workspace_change_required")
        self.assertTrue(resolution.selection_changed)
        self.assertFalse(hasattr(request, "resource_registry_tenant_id"))
        self.assertNotIn("resource_registry_tenant_id", request.session)
        self.assertNotIn("resource_registry_workspace_id", request.session)
        self.assertNotIn("resource_registry_active_dataset_id", request.session)

    def test_adapter_clears_runtime_context_when_organization_is_disabled(self) -> None:
        """Remove dataset-only session and runtime entries after Organization access is disabled."""
        request = self._request()
        request.session["resource_registry_active_dataset_id"] = (
            "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        )
        client = _RuntimeClient()
        client.context_mgr.entries = {
            "resource_registry_active_dataset_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        }
        self.organization.resource_registry_enabled = False
        self.organization.save(update_fields=["resource_registry_enabled"])

        result = DataRegistryExplorationRoutingAdapter().try_route(
            client=client,
            request=request,
            statement="List the datasets available to me.",
        )

        assert result is not None
        self.assertNotIn("resource_registry_active_dataset_id", request.session)
        self.assertNotIn("resource_registry_active_dataset_id", client.context_mgr.entries)
        self.assertEqual(client.calls, [])

    def test_adapter_rejects_organization_listing_without_request_workspace(self) -> None:
        """Reject a listing request that lacks request Workspace scope."""
        _ensure_owner_object_permissions()
        administrator = User.objects.create_user(username="organization-listing-administrator")
        administrator.is_superuser = True
        administrator.save(update_fields=["is_superuser"])
        organization = Organization.objects.create(
            name="Organization Listing Only",
            slug="organization-listing-only",
            last_modified_by=administrator,
        )
        organization.resource_registry_enabled = True
        organization.save(update_fields=["resource_registry_enabled"])
        request = SimpleNamespace(
            user=administrator,
            organization=organization,
            session={},
        )
        client = _RuntimeClient()
        client.results = [{"success": True, "data": {"datasets": []}}]

        result = DataRegistryExplorationRoutingAdapter().try_route(
            client=client,
            request=request,
            statement="List the datasets available to me.",
        )

        assert result is not None
        self.assertEqual(client.calls, [])
        self.assertEqual(
            result.request_payload["error_code"], "resource_registry_workspace_change_required"
        )
        self.assertNotIn("resource_registry_tenant_id", request.session)

    def test_adapter_repeats_access_guidance_for_pending_listing_without_namespace(self) -> None:
        """Keep a retry request deterministic when the user has no authorized Workspace."""
        user_without_workspace = User.objects.create_user(username="assistant-no-workspace-user")
        request = SimpleNamespace(
            user=user_without_workspace,
            organization=self.organization,
            session={},
        )
        client = _RuntimeClient()
        adapter = DataRegistryExplorationRoutingAdapter()

        first_result = adapter.try_route(
            client=client,
            request=request,
            statement="List the datasets available to me.",
        )
        retry_result = adapter.try_route(
            client=client,
            request=request,
            statement="try again",
        )

        assert first_result is not None
        assert retry_result is not None
        self.assertEqual(first_result.request_payload["status"], "no_authorized_namespace")
        self.assertEqual(retry_result.request_payload["status"], "no_authorized_namespace")
        self.assertIn("check your access", retry_result.response_text)
        self.assertIn("Workspace setup", retry_result.response_text)
        self.assertEqual(client.calls, [])

    def test_request_workspace_resolves_authorized_namespace(self) -> None:
        """Use the header-resolved request Workspace as an authorized selection hint."""
        request = self._request(current_workspace_slug="second-workspace")

        resolution = AssistantNamespaceContextService().resolve(
            request=request,
            statement="List the datasets available to me.",
        )

        self.assertEqual(resolution.status, "selected")
        assert resolution.selected is not None
        self.assertEqual(resolution.selected.namespace_id, self.second_namespace.id)
        self.assertEqual(
            request.session["resource_registry_workspace_id"], self.second_workspace.pk
        )

    def test_request_workspace_replaces_prior_authorized_namespace(self) -> None:
        """Let the header-resolved Workspace replace stale Registry session binding."""
        request = self._request(current_workspace_slug="second-workspace")
        request.resource_registry_tenant_id = self.first_namespace.id
        request.session.update(
            {
                "resource_registry_tenant_id": self.first_namespace.id,
                "resource_registry_workspace_id": self.first_workspace.pk,
                "resource_registry_active_dataset_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                f"resource_registry_active_dataset_id:{self.first_namespace.id}": (
                    "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
                ),
                "resource_registry_selected_dataset_ids": ["aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"],
                "resource_registry_pending_derived_dataset_plan": {"status": "pending"},
                "resource_registry_scopes": ["resource_registry.dataset.read"],
            }
        )

        resolution = AssistantNamespaceContextService().resolve(
            request=request,
            statement="List the datasets available to me.",
        )

        self.assertTrue(resolution.selection_changed)
        assert resolution.selected is not None
        self.assertEqual(resolution.selected.namespace_id, self.second_namespace.id)
        self.assertEqual(request.session["resource_registry_tenant_id"], self.second_namespace.id)
        self.assertEqual(
            request.session["resource_registry_workspace_id"], self.second_workspace.pk
        )
        self.assertNotIn("resource_registry_active_dataset_id", request.session)
        self.assertNotIn(
            f"resource_registry_active_dataset_id:{self.first_namespace.id}",
            request.session,
        )
        self.assertNotIn("resource_registry_selected_dataset_ids", request.session)
        self.assertNotIn("resource_registry_pending_derived_dataset_plan", request.session)
        self.assertEqual(
            request.session["resource_registry_scopes"],
            ["resource_registry.dataset.read"],
        )
        self.assertEqual(
            ReadOnlyBifNamespaceAdmissionService().admit_namespace(
                request=request,
                requested_namespace_id=self.second_namespace.id,
            ),
            self.second_namespace.id,
        )

    def test_workspace_name_cannot_replace_request_workspace(self) -> None:
        """Require an interface change when chat names another authorized Workspace."""
        request = self._request(current_workspace_slug="first-workspace")
        request.session["resource_registry_tenant_id"] = self.first_namespace.id
        request.session["resource_registry_workspace_id"] = self.first_workspace.pk
        resolution = AssistantNamespaceContextService().resolve(
            request=request, statement="Use Second Workspace and list its datasets."
        )
        self.assertEqual(resolution.status, "workspace_change_required")
        self.assertIsNone(resolution.selected)
        self.assertEqual(request.workspace, self.first_workspace)
        self.assertEqual(request.session["resource_registry_tenant_id"], self.first_namespace.id)

    def test_unauthorized_workspace_hint_never_grants_namespace_access(self) -> None:
        """Ignore a caller-selected Workspace that is outside the member ACL."""
        request = self._request(current_workspace_slug="foreign-workspace")

        resolution = AssistantNamespaceContextService().resolve(
            request=request,
            statement="List the datasets available to me.",
        )

        self.assertEqual(resolution.status, "workspace_change_required")
        self.assertNotIn(
            self.foreign_namespace.id,
            {choice.namespace_id for choice in resolution.choices},
        )

    def test_unauthorized_request_workspace_cannot_use_previous_selection(self) -> None:
        """Reject unauthorized request scope despite an authorized session hint."""
        request = self._request(current_workspace_slug="foreign-workspace")
        request.session["resource_registry_tenant_id"] = self.first_namespace.id
        request.session["resource_registry_workspace_id"] = self.first_workspace.pk
        resolution = AssistantNamespaceContextService().resolve(
            request=request, statement="Use Foreign Workspace and list its datasets."
        )
        self.assertEqual(resolution.status, "workspace_change_required")
        self.assertTrue(resolution.selection_changed)
        self.assertIsNone(resolution.selected)
        self.assertNotIn("resource_registry_tenant_id", request.session)

    def test_adapter_reports_missing_request_scope_without_workspace_choices(self) -> None:
        """Keep missing request scope deterministic and free of Workspace choices."""
        result = DataRegistryExplorationRoutingAdapter().try_route(
            client=_RuntimeClient(),
            request=self._request(),
            statement="List the datasets available to me.",
        )

        assert result is not None
        self.assertIn("interface", result.response_text)
        self.assertNotIn("Which Workspace", result.response_text)
        self.assertNotIn("tenant", result.response_text.lower())
        self.assertNotIn(self.first_namespace.id, result.response_text)
        self.assertNotIn(self.second_namespace.id, result.response_text)

    def test_interface_workspace_selection_allows_dataset_listing_retry(self) -> None:
        """Allow listing after the interface supplies an authorized request Workspace."""
        request = self._request()
        client = _RuntimeClient()
        adapter = DataRegistryExplorationRoutingAdapter()
        first_result = adapter.try_route(
            client=client,
            request=request,
            statement="List the datasets available to me.",
        )
        client.results = [{"success": True, "data": {"datasets": []}}]

        request.workspace = self.second_workspace
        second_result = adapter.try_route(
            client=client,
            request=request,
            statement="List the datasets available to me.",
        )

        assert first_result is not None
        assert second_result is not None
        self.assertEqual(
            client.calls,
            [
                (
                    "resource_registry_list_datasets",
                    {"tenant_id": self.second_namespace.id},
                )
            ],
        )

    def test_adapter_clears_runtime_registry_entries_when_workspace_changes(self) -> None:
        """Prevent active Dataset context from leaking across an authorized Workspace switch."""
        request = self._request(current_workspace_slug="second-workspace")
        request.session["resource_registry_tenant_id"] = self.first_namespace.id
        request.session["resource_registry_workspace_id"] = self.first_workspace.pk
        client = _RuntimeClient()
        client.context_mgr.entries = {
            "resource_registry_tenant_id": self.first_namespace.id,
            "resource_registry_active_dataset_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "unrelated": "preserved",
        }
        client.results = [{"success": True, "data": {"datasets": []}}]

        result = DataRegistryExplorationRoutingAdapter().try_route(
            client=client,
            request=request,
            statement="List the datasets available to me.",
        )

        assert result is not None
        self.assertEqual(
            client.calls,
            [("resource_registry_list_datasets", {"tenant_id": self.second_namespace.id})],
        )
        self.assertNotIn("resource_registry_tenant_id", client.context_mgr.entries)
        self.assertNotIn("resource_registry_active_dataset_id", client.context_mgr.entries)
        self.assertEqual(client.context_mgr.entries["unrelated"], "preserved")

    def test_request_workspace_switch_is_confirmed_without_model_fallback(self) -> None:
        """Confirm a Workspace already selected through the request and clear old context."""
        request = self._request(current_workspace_slug="second-workspace")
        request.session["resource_registry_tenant_id"] = self.first_namespace.id
        request.session["resource_registry_workspace_id"] = self.first_workspace.pk
        request.session["resource_registry_active_dataset_id"] = (
            "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        )
        client = _RuntimeClient()
        client.context_mgr.entries = {
            "resource_registry_active_dataset_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        }

        result = DataRegistryExplorationRoutingAdapter().try_route(
            client=client,
            request=request,
            statement="switch to second-workspace (second-workspace)",
        )

        assert result is not None
        self.assertEqual(result.request_payload["status"], "workspace_selected")
        self.assertIn("Second Workspace", result.response_text)
        self.assertNotIn(self.second_namespace.id, result.response_text)
        self.assertEqual(request.session["resource_registry_tenant_id"], self.second_namespace.id)
        self.assertEqual(
            request.session["resource_registry_workspace_id"], self.second_workspace.pk
        )
        self.assertNotIn("resource_registry_active_dataset_id", request.session)
        self.assertNotIn("resource_registry_active_dataset_id", client.context_mgr.entries)
        self.assertEqual(client.calls, [])

    def test_standalone_workspace_commands_remain_deterministic(self) -> None:
        """Confirm standalone commands using authorized names, slugs, and Markdown."""
        for statement in (
            "Use Second Workspace.",
            "Please select the workspace `second-workspace`!",
            "Work in Second Workspace",
            "Change to **Second Workspace** (`second-workspace`).",
            "Use the - Second Workspace (`second-workspace`) Workspace.",
        ):
            with self.subTest(statement=statement):
                client = _RuntimeClient()
                result = DataRegistryExplorationRoutingAdapter().try_route(
                    client=client,
                    request=self._request(current_workspace_slug="second-workspace"),
                    statement=statement,
                )

                assert result is not None
                self.assertEqual(result.request_payload["status"], "workspace_selected")
                self.assertEqual(client.calls, [])

    def test_delegated_tasks_continue_after_workspace_resolution(self) -> None:
        """Keep workspace context and negated use instructions from consuming creative tasks."""
        for statement in (
            "ORCHESTRATION MODE Route: Concept-first. "
            "Use the - Second Workspace (`second-workspace`) Workspace. "
            "Generate three creative concepts per audience. Do not use placeholder assets.",
            "ORCHESTRATION MODE Continue the concept generation task now that Workspace "
            "is switched to **Second Workspace** (`second-workspace`). "
            "Do not use placeholder assets. Generate three creative concepts per audience.",
            "Use Second Workspace and generate three creative concepts per audience.",
            "Use Second Workspace.\nGenerate three creative concepts per audience.",
            "Do not use Second Workspace.",
        ):
            with self.subTest(statement=statement):
                request = self._request(current_workspace_slug="second-workspace")
                client = _RuntimeClient()
                result = DataRegistryExplorationRoutingAdapter().try_route(
                    client=client,
                    request=request,
                    statement=statement,
                )

                self.assertIsNone(result)
                self.assertEqual(client.calls, [])

    def test_workspace_selection_with_listing_executes_the_requested_task(self) -> None:
        """List datasets in the authorized workspace instead of stopping at confirmation."""
        request = self._request(current_workspace_slug="second-workspace")
        client = _RuntimeClient()
        client.results = [{"success": True, "data": {"datasets": []}}]

        result = DataRegistryExplorationRoutingAdapter().try_route(
            client=client,
            request=request,
            statement="Use Second Workspace and list its datasets.",
        )

        assert result is not None
        self.assertNotEqual(result.request_payload.get("status"), "workspace_selected")
        self.assertEqual(
            client.calls,
            [("resource_registry_list_datasets", {"tenant_id": self.second_namespace.id})],
        )

    def _create_workspace(self, slug: str, *, assigned: bool = True) -> Workspace:
        """Create and optionally assign one active Workspace.

        Args:
            slug: Workspace slug and display-name source.
            assigned: Whether the test member receives direct access.

        Returns:
            The persisted Workspace.
        """
        workspace = Workspace.objects.create(
            organization=self.organization,
            name=slug.replace("-", " ").title(),
            slug=slug,
            state=Workspace.State.PRIVATE,
            last_modified_by=self.user,
        )
        if assigned:
            add_workspace_user(workspace=workspace, user=self.user)
        else:
            remove_perm(Workspace.MANAGE_PERMISSION_CODENAME, self.user, workspace)
            remove_perm(Workspace.VIEW_PERMISSION_CODENAME, self.user, workspace)
        return workspace

    def _request(self, *, current_workspace_slug: str | None = None) -> SimpleNamespace:
        """Build a minimal authenticated assistant request.

        Args:
            current_workspace_slug: Optional header-resolved Workspace slug.

        Returns:
            Request-shaped object with mutable session state.
        """
        workspace = None
        if current_workspace_slug is not None:
            workspace = Workspace.objects.get(
                organization=self.organization,
                slug=current_workspace_slug,
            )
        return SimpleNamespace(
            user=self.user,
            organization=self.organization,
            workspace=workspace,
            session={},
        )
