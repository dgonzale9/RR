"""Verify scoped dataset management and immutable version selection."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.contrib.messages.storage.base import BaseStorage
from django.http import HttpRequest
from guardian.shortcuts import assign_perm

from Organization.models import Organization, Workspace
from resource_registry.application.dataset_registration import DatasetRegistrationFacade
from resource_registry.management_domain.authorization import DatasetManagementAuthorization
from resource_registry.management_domain.comparison import compare_dataset_schemas
from resource_registry.management_domain.errors import DatasetManagementError
from resource_registry.management_domain.lifecycle import DatasetLifecycleService
from resource_registry.models import (
    Dataset,
    DatasetManagementConfiguration,
    DatasetVersion,
    RegistryNamespace,
)
from resource_registry.read.version_read_service import DatasetVersionReadService
from resource_registry.tests.workspace_permission_helpers import (
    add_workspace_owner,
    add_workspace_user,
)


class ManagementRequest(HttpRequest):
    """Represent the host-resolved request supplied by tenant middleware."""

    organization: Organization
    user: User
    _messages: BaseStorage


@pytest.fixture
def management_scope(db: None) -> tuple[User, User, Organization, RegistryNamespace, Dataset]:
    """Create an enabled namespace with a manager, collaborator, and dataset."""
    manager = User.objects.create_user(username="dataset-manager", is_staff=True)
    editor = User.objects.create_user(username="dataset-editor", is_staff=True)
    organization = Organization.objects.create(
        name="Management",
        slug="management",
        last_modified_by=manager,
        resource_registry_enabled=True,
    )
    workspace = Workspace.objects.create(
        organization=organization,
        name="Management",
        slug="management",
        last_modified_by=manager,
        resource_registry_enabled=True,
    )
    add_workspace_owner(workspace=workspace, user=manager)
    add_workspace_user(workspace=workspace, user=editor)
    namespace, _ = RegistryNamespace.objects.get_or_create(workspace=workspace)
    DatasetManagementConfiguration.objects.create(organization=organization, enabled=True)
    dataset = Dataset.objects.create(
        tenant_id=namespace.id,
        organization=organization,
        workspace=workspace,
        slug="sales",
        name="Sales",
        source_type="FILE_UPLOAD",
        status="ACTIVE",
        created_by=manager,
    )
    return manager, editor, organization, namespace, dataset


def test_dataset_tracks_current_version_independently_of_version_number(
    management_scope: tuple[User, User, Organization, RegistryNamespace, Dataset],
) -> None:
    """Read the selected published version even when newer history exists."""

    _, _, _, namespace, dataset = management_scope
    first = DatasetVersion.objects.create(
        dataset=dataset,
        version_number=1,
        source_uri="local://first.csv",
        content_hash="first",
        status="READY",
    )
    DatasetVersion.objects.create(
        dataset=dataset,
        version_number=2,
        source_uri="local://second.csv",
        content_hash="second",
        status="READY",
    )
    dataset.current_version = first
    dataset.save(update_fields=["current_version"])
    result = DatasetVersionReadService().get_latest_version(
        tenant_id=str(namespace.id),
        dataset_id=str(dataset.id),
    )
    assert result.id == str(first.id)


def test_editor_permission_does_not_grant_access_to_another_scope(
    management_scope: tuple[User, User, Organization, RegistryNamespace, Dataset],
) -> None:
    """Require namespace membership in addition to an object grant."""

    manager, _, organization, _, dataset = management_scope
    outsider = User.objects.create_user(username="outsider")
    assign_perm("resource_registry.change_dataset", outsider, dataset)
    with pytest.raises(DatasetManagementError):
        DatasetManagementAuthorization(outsider, organization).require_edit(dataset)
    DatasetManagementAuthorization(manager, organization).require_manage(dataset)


def test_metadata_edit_requires_revision_and_keeps_identity(
    management_scope: tuple[User, User, Organization, RegistryNamespace, Dataset],
) -> None:
    """Reject stale edits while preserving immutable dataset identity."""

    _, editor, organization, _, dataset = management_scope
    assign_perm("resource_registry.change_dataset", editor, dataset)
    service = DatasetLifecycleService(editor, organization)
    service.update_metadata(dataset.id, {"name": "Monthly sales"}, expected_revision=0)
    dataset.refresh_from_db()
    assert dataset.name == "Monthly sales"
    assert dataset.slug == "sales"
    assert dataset.revision == 1
    with pytest.raises(DatasetManagementError, match="changed"):
        service.update_metadata(dataset.id, {"name": "Stale"}, expected_revision=0)


def test_manual_archive_cannot_be_undone_by_legacy_ingestion(
    management_scope: tuple[User, User, Organization, RegistryNamespace, Dataset],
) -> None:
    """Keep manual lifecycle choices when a legacy registration is replayed."""

    manager, _, organization, _, dataset = management_scope
    DatasetLifecycleService(manager, organization).archive(
        dataset.id,
        expected_revision=0,
        reason="Retired",
    )
    DatasetRegistrationFacade()._activate_registered_dataset(dataset)
    dataset.refresh_from_db()
    assert dataset.status == "ARCHIVED"
    assert dataset.archive_reason == "MANUAL"


def test_schema_comparison_requires_manager_for_type_changes() -> None:
    """Treat added columns as compatible and removed or changed columns as breaking."""

    before = [{"name": "amount", "dtype": "INTEGER"}]
    compatible = compare_dataset_schemas(before, [*before, {"name": "region", "dtype": "VARCHAR"}])
    incompatible = compare_dataset_schemas(before, [{"name": "amount", "dtype": "VARCHAR"}])
    assert compatible["requires_manager"] is False
    assert incompatible["requires_manager"] is True
    assert incompatible["changed"] == ["amount"]
