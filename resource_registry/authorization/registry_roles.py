"""Map authenticated users to Resource Registry capability roles."""

from __future__ import annotations

from BotServer.util.user_group_management import UserGroupManagement


def derive_registry_roles(user: object | None) -> tuple[str, ...]:
    """Return the existing Resource Registry role mapping for one user.

    Args:
        user: Authenticated Django user or a request user double.

    Returns:
        Registry capability roles for the user.
    """
    if user is None:
        return ("restricted_reader",)
    if getattr(user, "is_superuser", False):
        return ("admin",)
    if UserGroupManagement.is_user_admin(user):  # type: ignore[no-untyped-call]
        return ("admin",)
    if (
        UserGroupManagement.is_user_scoped_admin(user)  # type: ignore[no-untyped-call]
        or UserGroupManagement.is_user_power_user(user)  # type: ignore[no-untyped-call]
        or UserGroupManagement.is_user_publisher(user)  # type: ignore[no-untyped-call]
        or UserGroupManagement.is_user_bif_manager(user)  # type: ignore[no-untyped-call]
    ):
        return ("reader",)
    return ("restricted_reader",)
