"""Verify host-bound management APIs without imposing Admin staff admission."""

from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from django.contrib.auth.models import User
from django.urls import Resolver404, resolve
from guardian.shortcuts import assign_perm
from rest_framework.test import APIRequestFactory, force_authenticate

from Organization.models import Organization
from resource_registry.api.catalog.views import DatasetCatalogView
from resource_registry.api.management.views import DatasetChangeCollectionView, DatasetDetailView
from resource_registry.models import Dataset, RegistryNamespace
from resource_registry.tests.test_dataset_changes import create_verified_intake
from resource_registry.tests.test_dataset_maintenance_flows import Scope
from resource_registry.tests.test_dataset_management import (  # noqa: F401
    ManagementRequest,
    management_scope,
)


@pytest.mark.parametrize("prefix", ["auth/api/v1", "api/v1"])
def test_dataset_principal_management_routes_are_removed(prefix: str) -> None:
    """Remove grant management from both session and token API route trees."""
    with pytest.raises(Resolver404):
        resolve(f"/{prefix}/resource-registry/namespaces/{uuid4()}/principals/")


def test_catalog_filters_before_counting_without_staff(
    management_scope: tuple[User, User, Organization, RegistryNamespace, Dataset],
) -> None:
    """Keep pagination counts within the admitted destination."""

    _, editor, organization, _, dataset = management_scope
    editor.is_staff = False
    editor.save(update_fields=["is_staff"])
    request = cast(ManagementRequest, APIRequestFactory().get("/datasets/?limit=1"))
    request.organization = organization
    force_authenticate(request, user=editor)
    response = DatasetCatalogView.as_view()(request)
    assert response.status_code == 200
    assert response.data["total"] == 1
    assert response.data["count"] == 1
    assert response.data["results"][0]["id"] == str(dataset.id)


def test_catalog_requires_host_organization(
    management_scope: tuple[User, User, Organization, RegistryNamespace, Dataset],
) -> None:
    """Reject requests that cannot prove their trusted organization."""

    manager, _, _, _, _ = management_scope
    request = APIRequestFactory().get("/datasets/")
    force_authenticate(request, user=manager)
    assert DatasetCatalogView.as_view()(request).status_code == 403


def test_api_rejects_immutable_metadata(
    management_scope: tuple[User, User, Organization, RegistryNamespace, Dataset],
) -> None:
    """Prevent clients from changing provenance through metadata patches."""

    manager, _, organization, _, dataset = management_scope
    request = APIRequestFactory().patch(
        "/datasets/", {"expected_revision": 0, "tenant_id": "foreign"}, format="json"
    )
    cast(ManagementRequest, request).organization = organization
    force_authenticate(request, user=manager)
    response = DatasetDetailView.as_view()(request, dataset_id=dataset.id)
    assert response.status_code == 400


def test_dataset_grant_never_admits_foreign_destination(
    management_scope: Scope, tmp_path: Path
) -> None:
    """Reject a forged foreign target despite a dataset object grant."""
    _, editor, organization, namespace, _ = management_scope
    foreign = Dataset.objects.create(
        tenant_id=uuid4(), name="Foreign", slug="foreign", source_type="FILE_UPLOAD"
    )
    assign_perm("resource_registry.change_dataset", editor, foreign)
    path = tmp_path / "owned.csv"
    path.write_text("id\n1\n")
    intake = create_verified_intake(editor, path)
    request = APIRequestFactory().post(
        "/dataset-changes/",
        {
            "namespace_id": str(namespace.id),
            "intake_id": str(intake.id),
            "specification": {"dataset_id": str(foreign.id), "expected_revision": 0},
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY="forged",
    )
    cast(ManagementRequest, request).organization = organization
    force_authenticate(request, user=editor)
    assert DatasetChangeCollectionView.as_view()(request).status_code == 404
    request = APIRequestFactory().get("/datasets/")
    cast(ManagementRequest, request).organization = organization
    force_authenticate(request, user=editor)
    assert DatasetDetailView.as_view()(request, dataset_id=foreign.id).status_code == 404


def test_organization_manager_can_create_without_staff(
    management_scope: Scope, tmp_path: Path
) -> None:
    """Honor organization ACLs for organization destinations without changing staff."""
    _, editor, organization, _, _ = management_scope
    assign_perm("organization.view_organization", editor, organization)
    assign_perm("organization.manage_organization", editor, organization)
    editor.is_staff = False
    editor.save(update_fields=["is_staff"])
    namespace, _ = RegistryNamespace.objects.get_or_create(organization=organization)
    path = tmp_path / "owned.csv"
    path.write_text("id\n1\n")
    request = APIRequestFactory().post(
        "/dataset-changes/",
        {
            "namespace_id": str(namespace.id),
            "intake_id": str(create_verified_intake(editor, path).id),
            "specification": {"name": "Organizational data"},
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY="organization",
    )
    cast(ManagementRequest, request).organization = organization
    force_authenticate(request, user=editor)
    assert DatasetChangeCollectionView.as_view()(request).status_code == 201
