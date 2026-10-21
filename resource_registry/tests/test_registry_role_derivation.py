"""Verify Resource Registry capability roles derived from platform groups."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import Group, User

from BotServer.util.user_group_management import UserGroupManagement
from resource_registry.authorization.registry_roles import derive_registry_roles


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("group_names", "is_superuser", "expected_role"),
    [
        ((UserGroupManagement.GROUP_SCOPED_ADMIN,), False, "reader"),
        (
            (UserGroupManagement.GROUP_SCOPED_ADMIN, UserGroupManagement.GROUP_POWER_USER),
            False,
            "reader",
        ),
        (
            (UserGroupManagement.GROUP_SCOPED_ADMIN, UserGroupManagement.GROUP_ADMIN),
            False,
            "admin",
        ),
        ((UserGroupManagement.GROUP_SCOPED_ADMIN,), True, "admin"),
        ((), False, "restricted_reader"),
    ],
)
def test_derive_registry_roles_for_scoped_administrator(
    group_names: tuple[str, ...], is_superuser: bool, expected_role: str
) -> None:
    """Apply scoped reader access without overriding broader role precedence."""
    user = User.objects.create_user(username="registry-role-user", is_superuser=is_superuser)
    for group_name in group_names:
        group, _ = Group.objects.get_or_create(name=group_name)
        user.groups.add(group)

    assert derive_registry_roles(user) == (expected_role,)
