"""Provide Workspace membership helpers for Resource Registry tests."""

from django.contrib.auth.models import User

from Organization.models import Workspace, WorkspaceMembership


def add_workspace_user(*, workspace: Workspace, user: User) -> None:
    """Assign a user to a Workspace's standard member role.

    Args:
        workspace: Workspace whose member role receives the user.
        user: Persisted user receiving Workspace access.
    """
    WorkspaceMembership.objects.update_or_create(
        workspace=workspace,
        user=user,
        defaults={"role": WorkspaceMembership.Role.MEMBER},
    )


def add_workspace_owner(*, workspace: Workspace, user: User) -> None:
    """Assign a user to a Workspace's owner role.

    Args:
        workspace: Workspace whose owner role receives the user.
        user: Persisted user receiving Workspace owner access.
    """
    WorkspaceMembership.objects.update_or_create(
        workspace=workspace,
        user=user,
        defaults={"role": WorkspaceMembership.Role.OWNER},
    )


def remove_workspace_user(*, workspace: Workspace, user: User) -> None:
    """Remove a user from a Workspace's role membership.

    Args:
        workspace: Workspace whose role membership loses the user.
        user: Persisted user losing Workspace access.
    """
    WorkspaceMembership.objects.filter(workspace=workspace, user=user).delete()


remove_workspace_access = remove_workspace_user
