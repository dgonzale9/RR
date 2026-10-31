"""Verify cleanup contracts across REST, Django Admin and automatic startup."""

import os
from pathlib import Path
from typing import cast

import pytest
from django.contrib import admin
from django.template.response import TemplateResponse
from django.urls import resolve, reverse
from pytest_django.fixtures import SettingsWrapper
from rest_framework.test import APIRequestFactory, force_authenticate

from Organization.models import Organization
from resource_registry.cleanup.runtime import can_start_cleanup_coordinator
from resource_registry.cleanup.service import DatasetCleanupService
from resource_registry.models import Dataset
from resource_registry.tests.test_dataset_maintenance_flows import Scope
from resource_registry.tests.test_dataset_management import (  # noqa: F401
    ManagementRequest,
    management_scope,
)
from resource_registry.tests.test_dataset_management_admin import make_admin_request
from resource_registry.tests.test_resource_cleanup import (
    create_archived_dataset_file,
    request_dataset_cleanup,
)


@pytest.mark.parametrize("prefix", ["auth/api/v1", "api/v1"])
def test_deletion_api_requires_business_permission_not_staff(
    management_scope: Scope, prefix: str
) -> None:
    """Expose the same manager-authorized preview under session and token routes."""
    manager, reader, organization, _, dataset = management_scope
    manager.is_staff = False
    manager.save(update_fields=["is_staff"])
    path = f"/{prefix}/resource-registry/datasets/{dataset.pk}/deletion-preview/"
    match = resolve(path)
    for user, expected in ((manager, 200), (reader, 403)):
        request = cast(ManagementRequest, APIRequestFactory().get(path))
        request.organization = organization
        force_authenticate(request, user=user)
        assert match.func(request, **match.kwargs).status_code == expected


def test_admin_deletion_review_requires_confirmation(
    management_scope: Scope, tmp_path: Path, settings: SettingsWrapper
) -> None:
    """Render the reviewed inventory and reject an unchecked irreversible confirmation."""
    settings.RESOURCE_REGISTRY_LOCAL_STORAGE_DIR = str(tmp_path)
    manager, _, organization, _, _ = management_scope
    dataset, path = create_archived_dataset_file(management_scope, tmp_path)
    url = reverse("admin:resource_registry_dataset_deletion", args=[dataset.pk])
    model_admin = admin.site._registry[Dataset]
    response = model_admin.deletion_view(make_admin_request(manager, organization, url), dataset.pk)
    html = cast(TemplateResponse, response).render().content.decode()
    assert "Review deletion" in html
    assert "Request permanent deletion" in html
    assert "I understand" in html
    assert "Simulation" in html or "Execute" in html
    response = model_admin.deletion_view(
        make_admin_request(manager, organization, url, data={"reason": "Retired"}), dataset.pk
    )
    assert not dataset.cleanup_requests.exists()
    assert path.exists()


def test_cleanup_coordinator_does_not_start_from_management_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never spawn cleanup when Django is imported for migrations or tests."""

    monkeypatch.setattr("sys.argv", ["manage.py", "migrate"])
    assert not can_start_cleanup_coordinator()
    monkeypatch.setattr("sys.argv", ["pytest", "resource_registry/tests"])
    assert not can_start_cleanup_coordinator()


@pytest.mark.parametrize("prefix", ["auth/api/v1", "api/v1"])
def test_manager_can_schedule_replay_and_cancel_through_rest(
    management_scope: Scope, tmp_path: Path, settings: SettingsWrapper, prefix: str
) -> None:
    """Confirm scoped idempotent deletion without staff and cancel before execution."""
    manager, _, organization, _, _ = management_scope
    settings.RESOURCE_REGISTRY_LOCAL_STORAGE_DIR = str(tmp_path)
    dataset, path = create_archived_dataset_file(management_scope, tmp_path)
    manager.is_staff = False
    manager.save(update_fields=["is_staff"])
    preview = DatasetCleanupService(manager, organization).preview(dataset.pk)
    url = f"/{prefix}/resource-registry/datasets/{dataset.pk}/deletion-requests/"
    body = {
        "expected_revision": dataset.revision,
        "inventory_fingerprint": preview["inventory_fingerprint"],
        "reason": "Retired",
        "idempotency_key": "confirmed-request",
        "confirmed": True,
    }
    identifiers = []
    for _ in range(2):
        request = cast(ManagementRequest, APIRequestFactory().post(url, body, format="json"))
        request.organization = organization
        force_authenticate(request, user=manager)
        match = resolve(url)
        response = match.func(request, **match.kwargs)
        assert response.status_code == 202
        identifiers.append(response.data["id"])
    assert identifiers[0] == identifiers[1]
    url = f"/{prefix}/resource-registry/cleanup-requests/{identifiers[0]}/cancel/"
    request = cast(ManagementRequest, APIRequestFactory().post(url, {}, format="json"))
    request.organization = organization
    force_authenticate(request, user=manager)
    match = resolve(url)
    assert match.func(request, **match.kwargs).data["status"] == "CANCELLED"
    assert path.exists()


def test_cleanup_request_is_hidden_from_foreign_host(management_scope: Scope) -> None:
    """Apply the trusted host boundary before returning a deletion inventory."""
    manager, _, _, _, dataset = management_scope
    other = Organization.objects.create(name="Foreign", slug="foreign", last_modified_by=manager)
    url = f"/api/v1/resource-registry/datasets/{dataset.pk}/deletion-preview/"
    request = cast(ManagementRequest, APIRequestFactory().get(url))
    request.organization = other
    force_authenticate(request, user=manager)
    match = resolve(url)
    assert match.func(request, **match.kwargs).status_code in {403, 404}


def test_admin_cleanup_pages_show_english_progress(
    management_scope: Scope, tmp_path: Path, settings: SettingsWrapper
) -> None:
    """Render review, list and progress through the actual scoped Admin services."""
    settings.RESOURCE_REGISTRY_LOCAL_STORAGE_DIR = str(tmp_path)
    manager, _, organization, _, _ = management_scope
    dataset, _ = create_archived_dataset_file(management_scope, tmp_path)
    cleanup = request_dataset_cleanup(management_scope, dataset)
    model_admin = admin.site._registry[Dataset]
    pages = {
        "review": model_admin.deletion_view(
            make_admin_request(manager, organization, "/review/"), dataset.pk
        ),
        "progress": model_admin.cleanup_detail_view(
            make_admin_request(manager, organization, "/progress/"), cleanup.pk
        ),
        "candidates": model_admin.cleanup_list_view(
            make_admin_request(manager, organization, "/candidates/")
        ),
    }
    for name, response in pages.items():
        html = cast(TemplateResponse, response).render().content.decode()
        assert "Deletion" in html or "deletion" in html
        directory = os.environ.get("RESOURCE_CLEANUP_UI_ARTIFACT_DIR")
        if directory:
            output = Path(directory)
            output.mkdir(parents=True, exist_ok=True)
            (output / f"{name}.html").write_text(
                html.replace('"/static/', '"http://localhost:8000/static/')
            )


def test_admin_can_confirm_and_cancel_without_resubmitting_review_fields(
    management_scope: Scope, tmp_path: Path, settings: SettingsWrapper
) -> None:
    """Complete the manager's native Admin confirmation and cancellation workflow."""
    settings.RESOURCE_REGISTRY_LOCAL_STORAGE_DIR = str(tmp_path)
    manager, _, organization, _, _ = management_scope
    dataset, path = create_archived_dataset_file(management_scope, tmp_path)
    preview = DatasetCleanupService(manager, organization).preview(dataset.pk)
    model_admin = admin.site._registry[Dataset]
    url = reverse("admin:resource_registry_dataset_deletion", args=[dataset.pk])
    response = model_admin.deletion_view(
        make_admin_request(
            manager,
            organization,
            url,
            data={
                "expected_revision": dataset.revision,
                "inventory_fingerprint": preview["inventory_fingerprint"],
                "reason": "Retired",
                "idempotency_key": "admin-confirmed",
                "confirmed": "on",
            },
        ),
        dataset.pk,
    )
    assert response.status_code == 302
    cleanup = dataset.cleanup_requests.get()
    url = reverse("admin:resource_registry_dataset_cleanup_detail", args=[cleanup.pk])
    response = model_admin.cleanup_detail_view(
        make_admin_request(manager, organization, url, data={"operation": "cancel"}), cleanup.pk
    )
    assert response.status_code == 302
    cleanup.refresh_from_db()
    assert cleanup.status == "CANCELLED"
    assert path.exists()
