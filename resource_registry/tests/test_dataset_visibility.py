"""Integration contracts for Dataset Workspace and Organization visibility."""

from __future__ import annotations

from uuid import UUID

import pytest
from django.contrib.auth.models import User

from resource_registry.models import Dataset, DatasetVisibilityScope, RegistryNamespace
from resource_registry.read.dataset_read_service import DatasetReadService
from Organization.models import Organization, Workspace

pytestmark = pytest.mark.django_db


def test_workspace_reads_include_same_organization_global_datasets_only() -> None:
    """Return Organization-global datasets without cross-organization leakage."""
    user = User.objects.create_user(username="dataset-visibility-user")
    organization = Organization.objects.create(
        name="Visibility Organization",
        slug="visibility-organization",
        last_modified_by=user,
    )
    other_organization = Organization.objects.create(
        name="Other Visibility Organization",
        slug="other-visibility-organization",
        last_modified_by=user,
    )
    first_workspace = Workspace.objects.create(
        organization=organization,
        name="First Workspace",
        slug="first-workspace",
        last_modified_by=user,
    )
    second_workspace = Workspace.objects.create(
        organization=organization,
        name="Second Workspace",
        slug="second-workspace",
        last_modified_by=user,
    )
    other_workspace = Workspace.objects.create(
        organization=other_organization,
        name="Other Workspace",
        slug="other-workspace",
        last_modified_by=user,
    )
    first_namespace = RegistryNamespace.objects.create(
        id=UUID("10000000-0000-4000-8000-000000000001"),
        workspace=first_workspace,
    )
    second_namespace = RegistryNamespace.objects.create(
        id=UUID("10000000-0000-4000-8000-000000000002"),
        workspace=second_workspace,
    )
    other_namespace = RegistryNamespace.objects.create(
        id=UUID("10000000-0000-4000-8000-000000000003"),
        workspace=other_workspace,
    )
    Dataset.objects.create(
        tenant_id=first_namespace.id,
        organization=organization,
        workspace=first_workspace,
        visibility_scope=DatasetVisibilityScope.WORKSPACE,
        slug="workspace-only",
        name="Workspace only",
        source_type="FILE_UPLOAD",
    )
    Dataset.objects.create(
        tenant_id=first_namespace.id,
        organization=organization,
        visibility_scope=DatasetVisibilityScope.ORGANIZATION,
        slug="organization-global",
        name="Organization global",
        source_type="FILE_UPLOAD",
    )
    Dataset.objects.create(
        tenant_id=first_namespace.id,
        visibility_scope=DatasetVisibilityScope.WORKSPACE,
        slug="unmapped-legacy",
        name="Unmapped legacy",
        source_type="FILE_UPLOAD",
    )
    Dataset.objects.create(
        tenant_id=other_namespace.id,
        organization=other_organization,
        visibility_scope=DatasetVisibilityScope.ORGANIZATION,
        slug="other-organization-global",
        name="Other organization global",
        source_type="FILE_UPLOAD",
    )

    first_slugs = [
        dataset.slug
        for dataset in DatasetReadService().list_datasets(tenant_id=str(first_namespace.id))
    ]
    second_slugs = [
        dataset.slug
        for dataset in DatasetReadService().list_datasets(tenant_id=str(second_namespace.id))
    ]

    assert first_slugs == ["organization-global", "workspace-only"]
    assert second_slugs == ["organization-global"]
