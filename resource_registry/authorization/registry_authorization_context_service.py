"""Build immutable Registry Namespace admission from canonical Workspace authorization."""

from __future__ import annotations

from django.contrib.auth.models import AnonymousUser, User
from django.db.models import Q, QuerySet

from BotServer.util.user_group_management import UserGroupManagement
from Organization.models import Organization, Workspace
from Organization.organization_acl import get_workspaces_for_user, user_can_access_organization
from resource_registry.authorization.contracts import (
    AuthorizedNamespace,
    AuthorizedNamespaceSet,
    AuthorizedWorkspaceSet,
    RegistryAuthorizationContext,
)
from resource_registry.authorization.exceptions import (
    RegistryAuthorizationAuthenticationRequiredError,
    RegistryAuthorizationOrganizationDisabledError,
    RegistryAuthorizationOrganizationRequiredError,
)
from resource_registry.services.registry_namespace_resolution_service import (
    RegistryNamespaceResolutionService,
)


class RegistryAuthorizationContextService:
    """Orchestrate canonical Workspace authorization and persisted namespace resolution."""

    def __init__(
        self,
        *,
        namespace_resolution_service: RegistryNamespaceResolutionService | None = None,
    ) -> None:
        """Initialize the service with an optional persistence resolver.

        Args:
            namespace_resolution_service: Persistence-only Registry Namespace resolver.
        """
        self._namespace_resolution_service = (
            namespace_resolution_service or RegistryNamespaceResolutionService()
        )

    def build_context(
        self,
        *,
        user: User | AnonymousUser | None,
        organization: Organization | None,
    ) -> RegistryAuthorizationContext:
        """Build one immutable Registry authorization context.

        Args:
            user: Authenticated persisted Django user.
            organization: Trusted host-resolved Organization.

        Returns:
            Immutable authorized Workspace and namespace sets.

        Raises:
            RegistryAuthorizationAuthenticationRequiredError: If the user is unauthenticated or
                is not persisted.
            RegistryAuthorizationOrganizationRequiredError: If the Organization is missing or
                is not persisted.
            RegistryAuthorizationOrganizationDisabledError: If Resource Registry access is disabled
                for the Organization.
        """
        user_id = getattr(user, "pk", None)
        if (
            not getattr(user, "is_authenticated", False)
            or not getattr(user, "is_active", False)
            or not isinstance(user_id, int)
        ):
            raise RegistryAuthorizationAuthenticationRequiredError(
                "Registry authorization requires an authenticated persisted user.",
            )
        if organization is None or not isinstance(organization.pk, int) or not organization.is_active:
            raise RegistryAuthorizationOrganizationRequiredError(
                "Registry authorization requires a persisted Organization.",
            )
        if not organization.resource_registry_enabled:
            raise RegistryAuthorizationOrganizationDisabledError(
                "Resource Registry access is disabled for the Organization.",
            )

        authorized_workspaces = self._get_authorized_registry_workspaces(
            user=user,
            organization=organization,
        )
        workspace_ids = frozenset(
            int(workspace_id) for workspace_id in authorized_workspaces.values_list("pk", flat=True)
        )
        authorized_workspaces = AuthorizedWorkspaceSet(
            organization_id=int(organization.pk),
            user_id=user_id,
            workspace_ids=workspace_ids,
        )
        resolved_namespaces = self._namespace_resolution_service.resolve_namespaces_for_workspaces(
            workspace_ids
        )
        organization_namespace = (
            self._namespace_resolution_service.resolve_organization_namespace(int(organization.pk))
            if self._can_access_organization_namespace(user=user, organization=organization)
            else None
        )
        authorized_namespaces = AuthorizedNamespaceSet(
            mappings=tuple(
                AuthorizedNamespace(
                    namespace_id=mapping.namespace_id,
                    workspace_id=mapping.workspace_id,
                )
                for mapping in (*resolved_namespaces, organization_namespace)
                if mapping is not None
            ),
        )
        return RegistryAuthorizationContext(
            authorized_workspaces=authorized_workspaces,
            authorized_namespaces=authorized_namespaces,
        )

    @staticmethod
    def _get_authorized_registry_workspaces(
        *,
        user: User,
        organization: Organization,
    ) -> QuerySet[Workspace]:
        """Return active Registry-enabled Workspaces authorized by Registry policy.

        Registry retains the canonical Workspace ACL as its baseline. In addition, an
        Organization access through memberships or Guardian admits OPEN Workspaces,
        matching the public Workspace ACL. Registry enablement remains mandatory.
        """
        if user.is_superuser or UserGroupManagement.is_user_admin(
            user
        ):  # type: ignore[no-untyped-call]
            return Workspace.objects.filter(
                organization=organization,
                is_active=True,
                resource_registry_enabled=True,
            )

        authorized_workspace_ids = get_workspaces_for_user(user, organization).values("pk")
        workspace_filter = Q(pk__in=authorized_workspace_ids)
        if user_can_access_organization(user, organization):
            workspace_filter |= Q(state=Workspace.State.OPEN)
        return Workspace.objects.filter(
            workspace_filter,
            organization=organization,
            is_active=True,
            resource_registry_enabled=True,
        ).distinct()

    @staticmethod
    def _can_access_organization_namespace(
        *,
        user: User,
        organization: Organization,
    ) -> bool:
        """Return whether a user may read through an Organization-owned namespace.

        The existing Organization ACL admits administrators, Organization memberships, and
        Guardian ``view_organization`` permissions. Mutation boundaries retain their independent
        administrator and capability checks.

        Args:
            user: Authenticated persisted user requesting Resource Registry access.
            organization: Trusted enabled Organization boundary.

        Returns:
            True when the user may access the Organization through its canonical ACL.
        """
        return user_can_access_organization(user, organization)
