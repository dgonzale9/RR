"""Intersect existing tenancy ACLs with dataset-specific maintenance grants."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from uuid import UUID

from django.contrib.auth.models import User
from django.db.models import Q, QuerySet
from guardian.shortcuts import get_objects_for_user, get_users_with_perms

from BotServer.util.user_group_management import UserGroupManagement
from Organization.models import Organization, OrganizationMembership, Workspace, WorkspaceMembership
from Organization.organization_acl import can_user_manage_workspace, user_can_manage_organization
from resource_registry.authorization.exceptions import RegistryAuthorizationContextError
from resource_registry.authorization.registry_authorization_context_service import (
    RegistryAuthorizationContextService,
)
from resource_registry.management_domain.errors import DatasetManagementError
from resource_registry.models import Dataset, DatasetManagementConfiguration, RegistryNamespace


class DatasetManagementAuthorization:
    """Authorize maintenance against the trusted host and an admitted namespace."""

    def __init__(self, user: User, organization: Organization | None) -> None:
        """Build current admission.

        Args:
            user: Authenticated persisted actor.
            organization: Organization resolved by trusted host middleware.

        Raises:
            DatasetManagementError: If the actor or host is not admitted.
        """
        self.user: User = user
        self.organization: Organization | None = organization
        try:
            self.context = RegistryAuthorizationContextService().build_context(
                user=user, organization=organization
            )
        except (RegistryAuthorizationContextError, ValueError, TypeError) as error:
            raise DatasetManagementError(
                "scope_denied", "This destination is unavailable.", 403
            ) from error
        self.namespace_ids: frozenset[UUID] = frozenset(
            mapping.namespace_id for mapping in self.context.authorized_namespaces.mappings
        )

    def namespaces(self) -> QuerySet[RegistryNamespace]:
        """Return currently admitted destinations for selectors and mutations."""
        return RegistryNamespace.objects.filter(pk__in=self.namespace_ids).select_related(
            "workspace__organization", "organization"
        )

    def get_namespace(self, identifier: UUID | str) -> RegistryNamespace:
        """Resolve one admitted destination or conceal an inaccessible identifier."""
        namespace = self.namespaces().filter(pk=identifier).first()
        if namespace is None:
            raise DatasetManagementError("not_found", "Destination not found.", 404)
        return namespace

    def datasets(self, *, include_archived: bool = False) -> QuerySet[Dataset]:
        """Return datasets within admitted tenancy, including visible organization data."""
        if not self.namespace_ids:
            return Dataset.objects.none()
        organization_namespaces = RegistryNamespace.objects.filter(
            organization=self.organization
        ).values("id")
        queryset = Dataset.objects.filter(organization=self.organization).filter(
            Q(tenant_id__in=self.namespace_ids)
            | Q(
                visibility_scope="ORGANIZATION",
                workspace__isnull=True,
                tenant_id__in=organization_namespaces,
            )
        )
        if not include_archived:
            queryset = queryset.filter(archived_at__isnull=True).exclude(status="ARCHIVED")
        return queryset

    def can_manage_namespace(self, namespace: RegistryNamespace) -> bool:
        """Check the existing owner/management ACL without requiring staff."""
        if namespace.id not in self.namespace_ids:
            return False
        if namespace.organization_id:
            return bool(user_can_manage_organization(self.user, self.organization))
        if namespace.workspace is None or self.organization is None:
            return False
        return bool(
            can_user_manage_workspace(
                SimpleNamespace(user=self.user), namespace.workspace, self.organization
            )
        )

    def require_enabled(self) -> None:
        """Reject mutations until the organization has enabled the compatible rollout."""
        if not DatasetManagementConfiguration.objects.filter(
            organization=self.organization, enabled=True
        ).exists():
            raise DatasetManagementError(
                "management_disabled", "Dataset management is not enabled.", 403
            )

    def require_create(self, namespace: RegistryNamespace) -> None:
        """Require destination admission and a manager or scoped creation grant."""
        if namespace.id not in self.namespace_ids or not (
            self.can_manage_namespace(namespace)
            or self.user.has_perm("resource_registry.create_datasets", namespace)
        ):
            raise DatasetManagementError(
                "create_denied", "Dataset creation is not permitted here.", 403
            )

    def require_edit(self, dataset: Dataset) -> None:
        """Require live scope access and dataset editing authority."""
        namespace = self.get_namespace(dataset.tenant_id)
        if dataset.organization_id != getattr(self.organization, "pk", None) or not (
            self.can_manage_namespace(namespace)
            or self.user.has_perm("resource_registry.change_dataset", dataset)
        ):
            raise DatasetManagementError("edit_denied", "Dataset editing is not permitted.", 403)

    def require_manage(self, dataset: Dataset) -> None:
        """Require management of the dataset's actual owning destination."""
        namespace = self.get_namespace(dataset.tenant_id)
        if dataset.organization_id != getattr(
            self.organization, "pk", None
        ) or not self.can_manage_namespace(namespace):
            raise DatasetManagementError(
                "manager_required", "A destination manager must perform this action.", 403
            )

    def eligible_users(self, namespace: RegistryNamespace) -> QuerySet[User]:
        """Limit responsible-user choices to active members of the destination."""
        if self.organization is None:
            return User.objects.none()
        organization_members = OrganizationMembership.objects.filter(
            organization=self.organization
        ).values("user_id")
        organization_grantees = cast(
            QuerySet[User],
            get_users_with_perms(
                self.organization, only_with_perms_in=["view_organization", "manage_organization"]
            ),
        )
        scope = Q(pk__in=organization_members) | Q(pk__in=organization_grantees.values("pk"))
        administrators = Q(
            groups__name__in=[
                UserGroupManagement.GROUP_ADMIN,
                UserGroupManagement.GROUP_SCOPED_ADMIN,
            ]
        )
        organization_managers = cast(
            QuerySet[User],
            get_users_with_perms(self.organization, only_with_perms_in=["manage_organization"]),
        ).values("pk")
        if namespace.workspace:
            workspace_members = WorkspaceMembership.objects.filter(
                workspace=namespace.workspace
            ).values("user_id")
            workspace_grantees = cast(
                QuerySet[User],
                get_users_with_perms(
                    namespace.workspace, only_with_perms_in=["view_workspace", "manage_workspace"]
                ),
            )
            if namespace.workspace and namespace.workspace.state == Workspace.State.OPEN:
                scope |= Q(pk__in=workspace_members) | Q(pk__in=workspace_grantees.values("pk"))
            else:
                organization_owners = OrganizationMembership.objects.filter(
                    organization=self.organization, role=OrganizationMembership.Role.OWNER
                ).values("user_id")
                scope = (
                    Q(pk__in=workspace_members)
                    | Q(pk__in=workspace_grantees.values("pk"))
                    | Q(pk__in=organization_owners)
                    | Q(pk__in=organization_managers)
                )
        return User.objects.filter(scope | administrators, is_active=True).distinct()

    def editable_datasets(self, namespace: RegistryNamespace) -> QuerySet[Dataset]:
        """Restrict target selectors to datasets the actor can currently edit."""
        queryset = self.datasets().filter(tenant_id=namespace.id, source_type="FILE_UPLOAD")
        if self.can_manage_namespace(namespace):
            return queryset
        return cast(
            QuerySet[Dataset],
            get_objects_for_user(
                self.user,
                "resource_registry.change_dataset",
                klass=queryset,
                accept_global_perms=False,
            ),
        )
