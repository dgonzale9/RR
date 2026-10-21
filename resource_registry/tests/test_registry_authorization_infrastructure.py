"""Registry authorization infrastructure contract and orchestration tests."""

from __future__ import annotations

import uuid
from dataclasses import FrozenInstanceError

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group
from django.test import TestCase
from guardian.shortcuts import assign_perm, remove_perm

from BotServer.util.user_group_management import UserGroupManagement
from Organization.models import Organization, OrganizationMembership, Workspace
from resource_registry.authorization.contracts import (
    AuthorizedNamespace,
    AuthorizedNamespaceSet,
    AuthorizedWorkspaceSet,
)
from resource_registry.authorization.exceptions import (
    RegistryAuthorizationAuthenticationRequiredError,
    RegistryAuthorizationOrganizationDisabledError,
    RegistryAuthorizationOrganizationRequiredError,
)
from resource_registry.authorization.registry_authorization_context_service import (
    RegistryAuthorizationContextService,
)
from resource_registry.services.registry_namespace_provisioning_service import (
    RegistryNamespaceProvisioningService,
)
from resource_registry.services.registry_namespace_resolution_service import (
    RegistryNamespaceResolutionService,
)
from resource_registry.tests.workspace_permission_helpers import (
    add_workspace_owner,
    add_workspace_user,
)

User = get_user_model()


class RegistryAuthorizationInfrastructureTests(TestCase):
    """Verify immutable authorization sets and Workspace-to-namespace orchestration."""

    def setUp(self) -> None:
        """Create isolated users, Organizations, Workspaces, and namespace mappings."""
        self.user = User.objects.create_user(username="registry-authorization-user")
        self.owner = User.objects.create_user(username="registry-authorization-owner")
        self.organization = self._create_organization("registry-authorization-one")
        self.other_organization = self._create_organization("registry-authorization-two")
        for organization in (self.organization, self.other_organization):
            OrganizationMembership.objects.filter(
                organization=organization, user=self.user
            ).delete()
            remove_perm(Organization.MANAGE_PERMISSION_CODENAME, self.user, organization)
            remove_perm(Organization.VIEW_PERMISSION_CODENAME, self.user, organization)
        self.mapped_workspace = self._create_workspace(
            organization=self.organization,
            slug="mapped-workspace",
        )
        self.unmapped_workspace = self._create_workspace(
            organization=self.organization,
            slug="unmapped-workspace",
        )
        self.unassigned_workspace = self._create_workspace(
            organization=self.organization,
            slug="unassigned-workspace",
        )
        remove_perm(
            Workspace.MANAGE_PERMISSION_CODENAME,
            self.user,
            self.unassigned_workspace,
        )
        remove_perm(
            Workspace.VIEW_PERMISSION_CODENAME,
            self.user,
            self.unassigned_workspace,
        )
        self.inactive_workspace = self._create_workspace(
            organization=self.organization,
            slug="inactive-workspace",
            is_active=False,
        )
        self.other_organization_workspace = self._create_workspace(
            organization=self.other_organization,
            slug="other-organization-workspace",
        )
        add_workspace_user(workspace=self.mapped_workspace, user=self.user)
        add_workspace_user(workspace=self.unmapped_workspace, user=self.user)
        add_workspace_user(workspace=self.inactive_workspace, user=self.user)
        add_workspace_user(workspace=self.other_organization_workspace, user=self.user)
        add_workspace_owner(workspace=self.mapped_workspace, user=self.owner)
        Organization.objects.filter(
            pk__in=(self.organization.pk, self.other_organization.pk),
        ).update(
            resource_registry_enabled=True,
        )
        Workspace.objects.filter(
            pk__in=(
                self.mapped_workspace.pk,
                self.unmapped_workspace.pk,
                self.unassigned_workspace.pk,
                self.inactive_workspace.pk,
                self.other_organization_workspace.pk,
            ),
        ).update(resource_registry_enabled=True)
        self.organization.refresh_from_db()
        self.other_organization.refresh_from_db()
        for workspace in (
            self.mapped_workspace,
            self.unmapped_workspace,
            self.unassigned_workspace,
            self.inactive_workspace,
            self.other_organization_workspace,
        ):
            workspace.refresh_from_db()
        provisioning_service = RegistryNamespaceProvisioningService()
        self.mapped_namespace = provisioning_service.provision_new_namespace(
            self.mapped_workspace.pk,
        )
        self.unassigned_namespace = provisioning_service.provision_new_namespace(
            self.unassigned_workspace.pk,
        )
        self.inactive_namespace = provisioning_service.provision_new_namespace(
            self.inactive_workspace.pk,
        )
        self.other_namespace = provisioning_service.provision_new_namespace(
            self.other_organization_workspace.pk,
        )

    def test_authorized_workspace_set_is_frozen_and_hashable(self) -> None:
        """Prevent mutation and permit stable request-lifetime value semantics."""
        authorized = AuthorizedWorkspaceSet(
            organization_id=self.organization.pk,
            user_id=self.user.pk,
            workspace_ids={self.mapped_workspace.pk},  # type: ignore[arg-type]
        )
        self.assertTrue(authorized.contains_workspace(self.mapped_workspace.pk))
        self.assertIsInstance(authorized.workspace_ids, frozenset)
        self.assertIsInstance(hash(authorized), int)
        with self.assertRaises(FrozenInstanceError):
            authorized.user_id = 999  # type: ignore[misc]

    def test_authorized_namespace_set_is_frozen_hashable_and_efficiently_queryable(self) -> None:
        """Expose immutable membership and reverse Workspace resolution operations."""
        namespace_id = uuid.UUID(self.mapped_namespace.id)
        authorized = AuthorizedNamespaceSet(
            mappings=(
                AuthorizedNamespace(
                    workspace_id=self.mapped_workspace.pk,
                    namespace_id=namespace_id,
                ),
            ),
        )
        self.assertTrue(authorized.contains_namespace(namespace_id))
        self.assertTrue(authorized.contains_namespace(str(namespace_id)))
        self.assertTrue(authorized.contains_workspace(self.mapped_workspace.pk))
        self.assertEqual(authorized.resolve_workspace(namespace_id), self.mapped_workspace.pk)
        self.assertIsNone(authorized.resolve_workspace(uuid.uuid4()))
        self.assertIsInstance(hash(authorized), int)
        with self.assertRaises(FrozenInstanceError):
            authorized.mappings = ()  # type: ignore[misc]

    def test_empty_authorized_namespace_set_has_no_membership_or_resolution(self) -> None:
        """Return safe negative results for every operation on an empty set."""
        authorized = AuthorizedNamespaceSet(mappings=())
        self.assertFalse(authorized.contains_namespace(uuid.uuid4()))
        self.assertFalse(authorized.contains_workspace(self.mapped_workspace.pk))
        self.assertIsNone(authorized.resolve_workspace(uuid.uuid4()))

    def test_authorized_namespace_set_rejects_duplicate_identity_or_ownership(self) -> None:
        """Reject ambiguous duplicate namespaces and duplicate Workspace mappings."""
        namespace_id = uuid.uuid4()
        with self.assertRaisesRegex(ValueError, "duplicate namespace"):
            AuthorizedNamespaceSet(
                mappings=(
                    AuthorizedNamespace(namespace_id=namespace_id, workspace_id=1),
                    AuthorizedNamespace(namespace_id=namespace_id, workspace_id=2),
                ),
            )
        with self.assertRaisesRegex(ValueError, "duplicate Workspace"):
            AuthorizedNamespaceSet(
                mappings=(
                    AuthorizedNamespace(namespace_id=uuid.uuid4(), workspace_id=1),
                    AuthorizedNamespace(namespace_id=uuid.uuid4(), workspace_id=1),
                ),
            )

    def test_authorized_namespace_equality_includes_workspace_ownership(self) -> None:
        """Keep value equality sensitive to both namespace identity and Workspace ownership."""
        namespace_id = uuid.uuid4()
        first = AuthorizedNamespace(namespace_id=namespace_id, workspace_id=1)
        second = AuthorizedNamespace(namespace_id=namespace_id, workspace_id=2)
        self.assertNotEqual(first, second)

    def test_bulk_resolution_uses_one_query_and_ignores_missing_and_duplicate_inputs(self) -> None:
        """Resolve only persisted mappings without N+1 Workspace queries."""
        with self.assertNumQueries(1):
            mappings = RegistryNamespaceResolutionService().resolve_namespaces_for_workspaces(
                [
                    self.mapped_workspace.pk,
                    self.mapped_workspace.pk,
                    self.unmapped_workspace.pk,
                ],
            )
        self.assertEqual(len(mappings), 1)
        self.assertEqual(mappings[0].workspace_id, self.mapped_workspace.pk)
        self.assertEqual(str(mappings[0].namespace_id), self.mapped_namespace.id)

    def test_context_contains_only_authorized_workspaces_and_persisted_mappings(self) -> None:
        """Compose direct membership and namespace persistence without ownership inference."""
        context = RegistryAuthorizationContextService().build_context(
            user=self.user,
            organization=self.organization,
        )
        self.assertSetEqual(
            set(context.authorized_workspaces.workspace_ids),
            {self.mapped_workspace.pk, self.unmapped_workspace.pk},
        )
        self.assertSetEqual(
            set(context.authorized_namespaces.workspace_ids),
            {self.mapped_workspace.pk},
        )
        self.assertTrue(
            context.authorized_namespaces.contains_namespace(self.mapped_namespace.id),
        )
        self.assertFalse(
            context.authorized_namespaces.contains_namespace(self.unassigned_namespace.id),
        )
        self.assertFalse(
            context.authorized_namespaces.contains_namespace(self.inactive_namespace.id),
        )
        self.assertFalse(context.authorized_namespaces.contains_namespace(self.other_namespace.id))

    def test_workspace_owner_receives_the_workspace_namespace(self) -> None:
        """Authorize a Workspace owner through the group-based canonical ACL."""
        context = RegistryAuthorizationContextService().build_context(
            user=self.owner,
            organization=self.organization,
        )

        self.assertSetEqual(
            set(context.authorized_workspaces.workspace_ids),
            {self.mapped_workspace.pk},
        )
        self.assertTrue(context.authorized_namespaces.contains_namespace(self.mapped_namespace.id))

    def test_only_admin_context_includes_all_active_organization_workspaces(self) -> None:
        """Authorize every active Workspace only for the platform administrator role."""
        user = User.objects.create_user(username="registry-authorization-admin")
        group, _ = Group.objects.get_or_create(name=UserGroupManagement.GROUP_ADMIN)
        user.groups.add(group)

        context = RegistryAuthorizationContextService().build_context(
            user=user,
            organization=self.organization,
        )

        self.assertSetEqual(
            set(context.authorized_workspaces.workspace_ids),
            {
                self.mapped_workspace.pk,
                self.unmapped_workspace.pk,
                self.unassigned_workspace.pk,
            },
        )

    def test_scoped_admin_context_includes_active_organization_workspaces(self) -> None:
        """Authorize active Organization Workspaces for the existing scoped-admin role."""
        user = User.objects.create_user(username="registry-authorization-scoped-admin")
        group, _ = Group.objects.get_or_create(name=UserGroupManagement.GROUP_SCOPED_ADMIN)
        user.groups.add(group)

        context = RegistryAuthorizationContextService().build_context(
            user=user,
            organization=self.organization,
        )

        self.assertSetEqual(
            set(context.authorized_workspaces.workspace_ids),
            {
                self.mapped_workspace.pk,
                self.unmapped_workspace.pk,
                self.unassigned_workspace.pk,
            },
        )
        self.assertSetEqual(
            set(context.authorized_namespaces.namespace_ids),
            {
                uuid.UUID(self.mapped_namespace.id),
                uuid.UUID(self.unassigned_namespace.id),
            },
        )

    def test_organization_member_can_select_organization_namespace_without_workspace(
        self,
    ) -> None:
        """Admit an Organization member to read global Datasets without Workspace access."""
        organization_namespace = (
            RegistryNamespaceProvisioningService().provision_new_organization_namespace(
                self.organization.pk,
            )
        )
        organization_member = User.objects.create_user(username="organization-registry-reader")
        OrganizationMembership.objects.create(
            organization=self.organization,
            user=organization_member,
        )

        context = RegistryAuthorizationContextService().build_context(
            user=organization_member,
            organization=self.organization,
        )

        self.assertSetEqual(set(context.authorized_workspaces.workspace_ids), set())
        self.assertSetEqual(set(context.authorized_namespaces.workspace_ids), set())
        self.assertTrue(context.authorized_namespaces.contains_namespace(organization_namespace.id))

    def test_organization_member_receives_an_enabled_open_workspace_namespace(self) -> None:
        """Authorize an Organization member to read from an enabled OPEN Workspace."""
        organization_member = User.objects.create_user(username="registry-open-workspace-member")
        OrganizationMembership.objects.create(
            organization=self.organization,
            user=organization_member,
        )
        open_workspace = Workspace.objects.create(
            organization=self.organization,
            name="Open Workspace",
            slug="open-workspace",
            state=Workspace.State.OPEN,
            resource_registry_enabled=True,
            last_modified_by=self.user,
        )
        open_namespace = open_workspace.resource_registry_namespace

        context = RegistryAuthorizationContextService().build_context(
            user=organization_member,
            organization=self.organization,
        )

        self.assertSetEqual(set(context.authorized_workspaces.workspace_ids), {open_workspace.pk})
        self.assertTrue(context.authorized_namespaces.contains_namespace(open_namespace.id))

    def test_organization_member_cannot_access_a_private_workspace_without_membership(self) -> None:
        """Keep a PRIVATE Workspace namespace unavailable without direct authorization."""
        organization_member = User.objects.create_user(username="registry-private-workspace-member")
        OrganizationMembership.objects.create(
            organization=self.organization,
            user=organization_member,
        )

        context = RegistryAuthorizationContextService().build_context(
            user=organization_member,
            organization=self.organization,
        )

        self.assertFalse(
            context.authorized_namespaces.contains_namespace(self.unassigned_namespace.id),
        )

    def test_open_workspace_is_excluded_when_inactive_or_registry_disabled(self) -> None:
        """Preserve Registry enablement and activity checks for OPEN Workspaces."""
        organization_member = User.objects.create_user(username="registry-open-workspace-denied")
        OrganizationMembership.objects.create(
            organization=self.organization,
            user=organization_member,
        )
        inactive_workspace = Workspace.objects.create(
            organization=self.organization,
            name="Inactive Open Workspace",
            slug="inactive-open-workspace",
            state=Workspace.State.OPEN,
            is_active=False,
            resource_registry_enabled=True,
            last_modified_by=self.user,
        )
        disabled_workspace = Workspace.objects.create(
            organization=self.organization,
            name="Disabled Open Workspace",
            slug="disabled-open-workspace",
            state=Workspace.State.OPEN,
            resource_registry_enabled=False,
            last_modified_by=self.user,
        )
        inactive_namespace = inactive_workspace.resource_registry_namespace

        context = RegistryAuthorizationContextService().build_context(
            user=organization_member,
            organization=self.organization,
        )

        self.assertFalse(context.authorized_namespaces.contains_namespace(inactive_namespace.id))
        self.assertFalse(context.authorized_workspaces.contains_workspace(disabled_workspace.pk))

    def test_workspace_object_permissions_admit_the_workspace_namespace(self) -> None:
        """Retain Registry admission for Guardian Workspace viewers and managers."""
        viewer = User.objects.create_user(username="registry-workspace-viewer")
        manager = User.objects.create_user(username="registry-workspace-manager")
        assign_perm(Workspace.VIEW_PERMISSION_CODENAME, viewer, self.unassigned_workspace)
        assign_perm(Workspace.MANAGE_PERMISSION_CODENAME, manager, self.unassigned_workspace)

        viewer_context = RegistryAuthorizationContextService().build_context(
            user=viewer,
            organization=self.organization,
        )
        manager_context = RegistryAuthorizationContextService().build_context(
            user=manager,
            organization=self.organization,
        )

        self.assertTrue(
            viewer_context.authorized_namespaces.contains_namespace(self.unassigned_namespace.id)
        )
        self.assertTrue(
            manager_context.authorized_namespaces.contains_namespace(self.unassigned_namespace.id)
        )

    def test_organization_view_permission_admits_the_organization_namespace(self) -> None:
        """Authorize Guardian Organization viewers to read Organization-global Datasets."""
        organization_namespace = (
            RegistryNamespaceProvisioningService().provision_new_organization_namespace(
                self.organization.pk,
            )
        )
        viewer = User.objects.create_user(username="registry-organization-viewer")
        assign_perm(Organization.VIEW_PERMISSION_CODENAME, viewer, self.organization)

        context = RegistryAuthorizationContextService().build_context(
            user=viewer,
            organization=self.organization,
        )

        self.assertSetEqual(set(context.authorized_workspaces.workspace_ids), set())
        self.assertTrue(context.authorized_namespaces.contains_namespace(organization_namespace.id))

    def test_context_requires_authenticated_user_and_persisted_organization(self) -> None:
        """Fail closed when either trusted admission input is missing."""
        service = RegistryAuthorizationContextService()
        with self.assertRaises(RegistryAuthorizationAuthenticationRequiredError):
            service.build_context(user=AnonymousUser(), organization=self.organization)
        with self.assertRaises(RegistryAuthorizationAuthenticationRequiredError):
            service.build_context(
                user=User(username="unsaved-registry-authorization-user"),
                organization=self.organization,
            )
        with self.assertRaises(RegistryAuthorizationOrganizationRequiredError):
            service.build_context(user=self.user, organization=None)

    def test_context_fails_closed_when_organization_is_disabled(self) -> None:
        """Reject all namespace admission when Organization access is disabled."""
        self.organization.resource_registry_enabled = False
        self.organization.save(update_fields=["resource_registry_enabled"])

        with self.assertRaises(RegistryAuthorizationOrganizationDisabledError):
            RegistryAuthorizationContextService().build_context(
                user=self.user,
                organization=self.organization,
            )

    def test_context_excludes_disabled_workspace_namespaces(self) -> None:
        """Exclude a Workspace namespace after its Resource Registry access is disabled."""
        self.mapped_workspace.resource_registry_enabled = False
        self.mapped_workspace.save(update_fields=["resource_registry_enabled"])

        context = RegistryAuthorizationContextService().build_context(
            user=self.user,
            organization=self.organization,
        )

        self.assertFalse(context.authorized_namespaces.contains_namespace(self.mapped_namespace.id))

    def _create_organization(self, slug: str) -> Organization:
        """Create an Organization owned by the test user."""
        return Organization.objects.create(
            name=slug.replace("-", " ").title(),
            slug=slug,
            last_modified_by=self.user,
        )

    def _create_workspace(
        self,
        *,
        organization: Organization,
        slug: str,
        is_active: bool = True,
    ) -> Workspace:
        """Create an active Workspace in the supplied Organization."""
        return Workspace.objects.create(
            organization=organization,
            name=slug.replace("-", " ").title(),
            slug=slug,
            is_active=is_active,
            state=Workspace.State.PRIVATE,
            last_modified_by=self.user,
        )
