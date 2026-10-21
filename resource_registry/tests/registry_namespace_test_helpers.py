"""Test factories for Registry Namespace database-backed tests."""

from __future__ import annotations

import uuid

from django.contrib.auth import get_user_model

from Organization.models import Organization, Workspace

User = get_user_model()


def create_workspace(*, suffix: str | None = None) -> Workspace:
    """Create one isolated Organization and Workspace for a test."""
    unique_suffix = suffix or uuid.uuid4().hex[:12]
    user = User.objects.create_user(username=f"registry-user-{unique_suffix}")
    organization = Organization.objects.create(
        name=f"Registry Organization {unique_suffix}",
        slug=f"registry-organization-{unique_suffix}",
        last_modified_by=user,
    )
    return Workspace.objects.create(
        organization=organization,
        name=f"Registry Workspace {unique_suffix}",
        slug=f"registry-workspace-{unique_suffix}",
        last_modified_by=user,
    )
