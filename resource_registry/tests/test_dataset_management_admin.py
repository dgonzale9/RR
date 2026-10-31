"""Verify scoped Admin admission and service-backed edits."""

import json
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from django.contrib import admin
from django.contrib.auth.models import User
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.backends.base import SessionBase
from django.template.response import TemplateResponse
from django.test import RequestFactory, TestCase
from django.urls import NoReverseMatch, reverse
from guardian.shortcuts import assign_perm
from pytest_django.fixtures import SettingsWrapper

from Organization.models import Organization
from resource_registry.dataset_admin import DatasetAdmin
from resource_registry.models import Dataset, DatasetChange, RegistryNamespace
from resource_registry.tests.background_processing_helpers import (  # noqa: F401
    DeferredExecutor,
    deferred_background_tasks,
)
from resource_registry.tests.test_dataset_management import (  # noqa: F401
    ManagementRequest,
    management_scope,
)
from resource_registry.tests.test_dataset_management_integrity import prepare_workbook


def make_admin_request(
    user: User, organization: Organization, path: str, *, data: dict[str, object] | None = None
) -> ManagementRequest:
    """Build a staff request with the tenant middleware and message state."""
    factory = RequestFactory()
    request = cast(
        ManagementRequest, factory.post(path, data) if data is not None else factory.get(path)
    )
    request.user = user
    request.organization = organization
    request.session = cast(SessionBase, {})
    request._messages = FallbackStorage(request)
    return request


def test_dataset_admin_exposes_scoped_reader_without_global_model_permission(
    management_scope: tuple[User, User, Organization, RegistryNamespace, Dataset],
) -> None:
    """Allow existing staff to read admitted datasets and forbid direct deletion."""
    _, editor, organization, _, dataset = management_scope
    model_admin = admin.site._registry[Dataset]
    request = cast(ManagementRequest, RequestFactory().get("/admin/resource_registry/dataset/"))
    request.user = editor
    request.organization = organization
    assert model_admin.has_view_permission(request, dataset)
    assert not model_admin.has_change_permission(request, dataset)
    assert not model_admin.has_delete_permission(request, dataset)
    assert list(model_admin.get_queryset(request)) == [dataset]


def test_dataset_admin_requires_staff_even_for_manager(
    management_scope: tuple[User, User, Organization, RegistryNamespace, Dataset],
) -> None:
    """Keep Admin admission separate from API business permission."""
    manager, _, organization, _, dataset = management_scope
    manager.is_staff = False
    request = cast(ManagementRequest, RequestFactory().get("/admin/resource_registry/dataset/"))
    request.user = manager
    request.organization = organization
    assert not admin.site._registry[Dataset].has_view_permission(request, dataset)


@pytest.mark.parametrize("screen", ["catalog", "detail", "upload", "changes", "review"])
def test_management_admin_screens_render(
    management_scope: tuple[User, User, Organization, RegistryNamespace, Dataset],
    screen: str,
    tmp_path: Path,
) -> None:
    """Render navigable Admin screens with scoped selectors and inherited styling."""
    manager, _, organization, _, dataset = management_scope
    model_admin = cast(DatasetAdmin, admin.site._registry[Dataset])
    request = make_admin_request(manager, organization, "/admin/resource_registry/dataset/")
    proposal = prepare_workbook(
        management_scope, tmp_path / "review.xlsx", ["Sales", "New"], {"name": "Workbook"}
    )
    views = {
        "catalog": lambda: model_admin.changelist_view(request),
        "detail": lambda: model_admin.change_view(request, str(dataset.id)),
        "upload": lambda: model_admin.upload_view(request),
        "changes": lambda: model_admin.changes_view(request),
        "review": lambda: model_admin.review_view(request, proposal.id),
    }
    response = views[screen]()
    assert response.status_code == 200
    rendered = cast(TemplateResponse, response).render()
    assert "<!DOCTYPE html>" in rendered.content.decode()
    assert "Manage collaborators" not in rendered.content.decode()
    assert "/dataset/collaborators/" not in rendered.content.decode()
    (tmp_path / f"dataset-admin-{screen}.html").write_bytes(rendered.content)


def test_dataset_admin_does_not_register_collaborator_management() -> None:
    """Remove the management route as well as its visible navigation."""
    with pytest.raises(NoReverseMatch):
        reverse("admin:resource_registry_dataset_collaborators")


def test_admin_reader_can_open_visible_organization_dataset(
    management_scope: tuple[User, User, Organization, RegistryNamespace, Dataset],
) -> None:
    """Keep organization dataset detail available to an admitted workspace reader."""
    _, editor, organization, _, _ = management_scope
    namespace, _ = RegistryNamespace.objects.get_or_create(organization=organization)
    dataset = Dataset.objects.create(
        tenant_id=namespace.id,
        organization=organization,
        name="Shared",
        slug="shared",
        visibility_scope="ORGANIZATION",
        source_type="FILE_UPLOAD",
    )
    request = make_admin_request(editor, organization, "/admin/resource_registry/dataset/")
    model_admin = cast(DatasetAdmin, admin.site._registry[Dataset])
    assert model_admin.change_view(request, str(dataset.id)).status_code == 200


def test_admin_rejects_unknown_sheet_target_in_manipulated_post(
    management_scope: tuple[User, User, Organization, RegistryNamespace, Dataset], tmp_path: Path
) -> None:
    """Reject a forged target instead of silently interpreting it as a new dataset."""
    manager, _, organization, _, _ = management_scope
    proposal = prepare_workbook(
        management_scope, tmp_path / "forged.xlsx", ["Sales"], {"name": "Book"}
    )
    original_specification = dict(proposal.specification)
    request = make_admin_request(
        manager,
        organization,
        "/review/",
        data={
            "operation": "revise",
            "expected_revision": proposal.revision,
            "selected_0": "on",
            "dataset_0": f"{uuid4()}|0",
        },
    )
    model_admin = cast(DatasetAdmin, admin.site._registry[Dataset])
    response = model_admin.review_view(request, proposal.id)
    proposal.refresh_from_db()
    assert response.status_code in {200, 404}
    assert proposal.specification == original_specification


def test_staff_collaborator_creates_dataset_through_admin(
    management_scope: tuple[User, User, Organization, RegistryNamespace, Dataset],
    settings: SettingsWrapper,
    deferred_background_tasks: DeferredExecutor,
) -> None:
    """Complete upload, inspection, proposal and publication without global intake permission."""
    _, editor, organization, namespace, _ = management_scope
    assign_perm("resource_registry.create_datasets", editor, namespace)
    assert not editor.has_perm("resource_registry.manage_stagedresourceintake")
    settings.RESOURCE_REGISTRY_UPLOAD_PROVIDER = "local"
    model_admin = cast(DatasetAdmin, admin.site._registry[Dataset])
    data = b"id,amount\n1,10\n"
    request = make_admin_request(
        editor,
        organization,
        "/create/",
        data={
            "namespace_id": str(namespace.id),
            "filename": "sales.csv",
            "size_bytes": len(data),
            "content_type": "text/csv",
            "idempotency_key": "admin-upload",
        },
    )
    created = model_admin.intake_create_view(request)
    assert created.status_code == 200
    intake_id = json.loads(created.content)["intake_id"]
    request = cast(
        ManagementRequest,
        RequestFactory().put("/blocks/0/", data, content_type="application/octet-stream"),
    )
    request.user = editor
    request.organization = organization
    assert model_admin.intake_block_view(request, intake_id, 0).status_code == 200
    request = make_admin_request(editor, organization, "/complete/", data={})
    with TestCase.captureOnCommitCallbacks(execute=True):
        assert model_admin.intake_action_view(request, intake_id, "complete").status_code == 200
    assert len(deferred_background_tasks.tasks) == 1
    deferred_background_tasks.run_next()
    request = make_admin_request(
        editor,
        organization,
        "/upload/",
        data={
            "namespace_id": str(namespace.id),
            "intake_id": intake_id,
            "name": "Created in Admin",
            "idempotency_key": "admin-proposal",
        },
    )
    with TestCase.captureOnCommitCallbacks(execute=True):
        response = model_admin.upload_view(request)
    assert response.status_code == 302
    change = DatasetChange.objects.get(submitted_by=editor)
    assert len(deferred_background_tasks.tasks) == 1
    deferred_background_tasks.run_next()
    change.refresh_from_db()
    request = make_admin_request(
        editor,
        organization,
        "/review/",
        data={"operation": "publish", "expected_revision": change.revision},
    )
    assert model_admin.review_view(request, change.id).status_code == 302
    dataset = Dataset.objects.get(name="Created in Admin")
    assert dataset.current_version_id is not None
    assert dataset.responsible_user == editor
    assert editor.has_perm("resource_registry.change_dataset", dataset)
