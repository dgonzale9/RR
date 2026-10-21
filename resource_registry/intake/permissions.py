"""Authorize global staged-intake operations and scoped destination removal."""

from __future__ import annotations

from django.contrib.auth.models import AnonymousUser, User
from django.db.models import Q, QuerySet

from BotServer.util.user_group_management import UserGroupManagement
from Organization.models import Organization
from Organization.organization_acl import get_workspaces_for_user, user_can_manage_organization
from resource_registry.models import StagedResourcePublication

MANAGE_STAGED_RESOURCE_INTAKES_PERMISSION = "resource_registry.manage_stagedresourceintake"


def can_manage_staged_resource_intakes(user: User | AnonymousUser) -> bool:
    """Determine whether a user can operate staged intakes across all destinations.

    Args:
        user: Authenticated Django user requesting the operation.

    Returns:
        True when the active staff user is a superuser or holds the global intake permission.
    """
    return bool(
        user.is_active
        and user.is_staff
        and (user.is_superuser or user.has_perm(MANAGE_STAGED_RESOURCE_INTAKES_PERMISSION))
    )


def can_remove_scoped_destination_assignments(
    user: User | AnonymousUser,
    organization: Organization | None,
) -> bool:
    """Allow a scoped administrator to remove assignments on the current Organization host.

    Args:
        user: Staff user requesting destination removal.
        organization: Organization resolved from the trusted request host.

    Returns:
        True when the user has scoped administrator authority for this Organization.
    """
    return bool(
        user.is_authenticated
        and user.is_active
        and user.is_staff
        and organization is not None
        and organization.pk is not None
        and organization.is_active
        and UserGroupManagement.is_user_scoped_admin(user)  # type: ignore[no-untyped-call]
        and user_can_manage_organization(user, organization)
    )


def can_remove_global_destination_assignments(user: User | AnonymousUser) -> bool:
    """Allow global intake operators and platform administrators to remove assignments.

    Args:
        user: Staff user requesting destination removal.

    Returns:
        True when the user has global destination-removal authority.
    """
    return bool(
        can_manage_staged_resource_intakes(user)
        or (
            user.is_authenticated
            and user.is_active
            and user.is_staff
            and UserGroupManagement.is_user_admin(user)  # type: ignore[no-untyped-call]
        )
    )


def get_removable_staged_resource_publications(
    user: User | AnonymousUser,
    organization: Organization | None,
) -> QuerySet[StagedResourcePublication]:
    """Find publications removable by a global operator or current-host scoped administrator.

    Args:
        user: Staff user requesting destination removal.
        organization: Organization resolved from the trusted request host.

    Returns:
        Publications within the user's removal authority.
    """
    if can_remove_global_destination_assignments(user):
        return StagedResourcePublication.objects.all()
    if not can_remove_scoped_destination_assignments(user, organization):
        return StagedResourcePublication.objects.none()
    assert organization is not None
    manageable_workspaces = get_workspaces_for_user(user, organization, only_manageable=True)
    return StagedResourcePublication.objects.filter(organization=organization).filter(
        Q(workspace__isnull=True) | Q(workspace__in=manageable_workspaces)
    )
