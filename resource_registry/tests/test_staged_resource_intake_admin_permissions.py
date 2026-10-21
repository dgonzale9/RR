"""Authorization coverage for Django Admin staged-intake operations."""

from __future__ import annotations

from datetime import timedelta
from typing import cast
from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser, Group, Permission
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponseRedirect
from django.test import Client, RequestFactory
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone

from BotServer.util.user_group_management import UserGroupManagement
from Organization.models import Organization, Workspace
from resource_registry.admin import StagedResourceIntakeAdmin, StagedResourcePublicationInline
from resource_registry.intake.services import StagedResourceIntakeService
from resource_registry.models import (
    DatasetVisibilityScope,
    RegistryNamespace,
    StagedResourceIntake,
    StagedResourceIntakeStatus,
    StagedResourcePublication,
    StagedResourcePublicationStatus,
)
from resource_registry.uploads.exceptions import UploadApplicationError

pytestmark = pytest.mark.django_db


def test_intake_management_permission_is_created() -> None:
    """Create the global staged-intake operations permission through migrations."""
    permission = Permission.objects.get(
        content_type__app_label="resource_registry",
        content_type__model="stagedresourceintake",
        codename="manage_stagedresourceintake",
    )

    assert permission.name == "Can manage staged resource intakes"


def test_staff_user_with_permission_can_manage_staged_intakes() -> None:
    """Grant global Admin intake operations to a permitted staff user."""
    operator = get_user_model().objects.create_user(
        username="intake-operator",
        password="password",
        is_staff=True,
    )
    operator.user_permissions.add(Permission.objects.get(codename="manage_stagedresourceintake"))
    intake = _create_intake(
        submitted_by=operator,
        status=StagedResourceIntakeStatus.READY_FOR_PUBLICATION,
    )
    admin_instance = StagedResourceIntakeAdmin(StagedResourceIntake, admin.site)
    request = _request_for_user(operator)

    assert admin_instance.has_view_permission(request, intake) is True
    assert admin_instance.has_change_permission(request, intake) is True
    assert admin_instance.get_model_perms(request)["change"] is True
    assert admin_instance.publish_destinations_view(request, str(intake.pk)).status_code == 200
    assert admin_instance.remove_destinations_view(request, str(intake.pk)).status_code == 200
    service = StagedResourceIntakeService(provider=object())
    with pytest.raises(UploadApplicationError) as error:
        service.publish_intake_to_namespace(
            user=operator,
            intake_id=intake.id,
            namespace_id=uuid4(),
        )
    assert error.value.code == "REGISTRY_NAMESPACE_NOT_FOUND"
    organization = Organization.objects.create(
        name="Intake Operator Organization",
        slug="intake-operator-organization",
        last_modified_by=operator,
        resource_registry_enabled=True,
    )
    namespace = RegistryNamespace.objects.get(organization=organization)
    with pytest.raises(UploadApplicationError) as error:
        service.publish_intake_to_namespace(
            user=operator,
            intake_id=uuid4(),
            namespace_id=namespace.id,
        )
    assert error.value.code == "INTAKE_NOT_FOUND"


def test_superuser_can_manage_staged_intakes() -> None:
    """Retain staged-intake operations for superusers."""
    superuser = get_user_model().objects.create_superuser(
        username="intake-superuser",
        password="password",
    )
    intake = _create_intake(
        submitted_by=superuser,
        status=StagedResourceIntakeStatus.READY_FOR_PUBLICATION,
    )
    admin_instance = StagedResourceIntakeAdmin(StagedResourceIntake, admin.site)
    request = _request_for_user(superuser)

    assert admin_instance.has_view_permission(request, intake) is True
    assert admin_instance.has_change_permission(request, intake) is True
    assert admin_instance.publish_destinations_view(request, str(intake.pk)).status_code == 200
    assert admin_instance.remove_destinations_view(request, str(intake.pk)).status_code == 200


def test_admin_group_can_open_removal_without_global_intake_permission() -> None:
    """Expose the removal page to platform admins without granting unrelated Admin actions."""
    owner = get_user_model().objects.create_superuser(
        username="admin-group-intake-owner", password="password"
    )
    administrator = get_user_model().objects.create_user(
        username="admin-group-intake-operator", password="password", is_staff=True
    )
    admin_group, _ = Group.objects.get_or_create(name=UserGroupManagement.GROUP_ADMIN)
    administrator.groups.add(admin_group)
    intake = _create_intake(submitted_by=owner, status=StagedResourceIntakeStatus.PUBLISHED)
    admin_instance = StagedResourceIntakeAdmin(StagedResourceIntake, admin.site)
    request = _request_for_user(administrator)

    assert admin_instance.has_view_permission(request, intake) is True
    assert admin_instance.has_change_permission(request, intake) is False
    assert admin_instance.remove_destinations_view(request, str(intake.pk)).status_code == 200
    change_response = cast(TemplateResponse, admin_instance.change_view(request, str(intake.pk)))
    assert change_response.context_data["can_remove_destinations"] is True
    assert change_response.context_data["has_change_permission"] is False
    with pytest.raises(PermissionDenied):
        admin_instance.publish_destinations_view(request, str(intake.pk))


def test_scoped_admin_sees_only_host_destination_removal_without_global_intake_actions() -> None:
    """Expose scoped removal while retaining the global upload and publish boundary."""
    owner = get_user_model().objects.create_superuser(
        username="scoped-admin-intake-owner", password="password"
    )
    scoped_admin = get_user_model().objects.create_user(
        username="scoped-admin-intake-operator", password="password", is_staff=True
    )
    scoped_admin_group, _ = Group.objects.get_or_create(name=UserGroupManagement.GROUP_SCOPED_ADMIN)
    scoped_admin.groups.add(scoped_admin_group)
    host_organization = Organization.objects.create(
        name="Scoped Intake Host",
        slug="scoped-intake-host",
        last_modified_by=owner,
    )
    other_organization = Organization.objects.create(
        name="Scoped Intake Other",
        slug="scoped-intake-other",
        last_modified_by=owner,
    )
    host_namespace = RegistryNamespace.objects.create(id=uuid4(), organization=host_organization)
    other_namespace = RegistryNamespace.objects.create(id=uuid4(), organization=other_organization)
    intake = _create_intake(submitted_by=owner, status=StagedResourceIntakeStatus.PUBLISHED)
    host_publication = StagedResourcePublication.objects.create(
        intake=intake,
        registry_namespace=host_namespace,
        organization=host_organization,
        visibility_scope=DatasetVisibilityScope.ORGANIZATION,
        published_by=owner,
    )
    other_publication = StagedResourcePublication.objects.create(
        intake=intake,
        registry_namespace=other_namespace,
        organization=other_organization,
        visibility_scope=DatasetVisibilityScope.ORGANIZATION,
        published_by=owner,
    )
    admin_instance = StagedResourceIntakeAdmin(StagedResourceIntake, admin.site)
    request = _request_for_user(scoped_admin)
    setattr(request, "organization", host_organization)

    assert admin_instance.has_view_permission(request, intake) is True
    assert admin_instance.has_change_permission(request, intake) is False
    assert admin_instance.get_model_perms(request)["view"] is True
    assert list(admin_instance.get_queryset(request)) == [intake]
    change_response = cast(TemplateResponse, admin_instance.change_view(request, str(intake.pk)))
    assert change_response.context_data["can_remove_destinations"] is True
    assert change_response.context_data["has_change_permission"] is False
    removal_response = cast(
        TemplateResponse, admin_instance.remove_destinations_view(request, str(intake.pk))
    )
    assert removal_response.status_code == 200
    available_publications = {
        choice[0]
        for choice in removal_response.context_data["form"].fields["publication_ids"].choices
    }
    assert str(host_publication.id) in available_publications
    assert str(other_publication.id) not in available_publications
    with pytest.raises(PermissionDenied):
        admin_instance.publish_destinations_view(request, str(intake.pk))

    removal_request = RequestFactory().post(
        reverse(
            "admin:resource_registry_stagedresourceintake_remove_destinations", args=(intake.pk,)
        ),
        data={
            "publication_ids": [str(host_publication.id)],
            "reason": "Remove the host destination.",
            "confirmation": "on",
        },
    )
    removal_request.user = scoped_admin
    setattr(removal_request, "organization", host_organization)
    setattr(removal_request, "_messages", Mock())
    post_response = cast(
        HttpResponseRedirect,
        admin_instance.remove_destinations_view(removal_request, str(intake.pk)),
    )
    assert post_response.status_code == 302
    assert post_response.url == reverse("admin:resource_registry_stagedresourceintake_changelist")
    host_publication.refresh_from_db()
    assert host_publication.assignment_status == StagedResourcePublicationStatus.DETACHED
    assert not admin_instance.get_queryset(request).exists()

    setattr(request, "organization", None)
    assert not admin_instance.get_queryset(request).exists()
    with pytest.raises(PermissionDenied):
        admin_instance.remove_destinations_view(request, str(intake.pk))


def test_change_form_shows_actions_only_to_intake_operators(client: Client) -> None:
    """Show intake action buttons only when Admin change authority is present."""
    operator = get_user_model().objects.create_user(
        username="template-intake-operator",
        password="password",
        is_staff=True,
    )
    operator.user_permissions.add(Permission.objects.get(codename="manage_stagedresourceintake"))
    intake = _create_intake(
        submitted_by=operator,
        status=StagedResourceIntakeStatus.READY_FOR_PUBLICATION,
    )
    change_url = reverse("admin:resource_registry_stagedresourceintake_change", args=(intake.pk,))

    client.force_login(operator)
    operator_response = client.get(change_url)

    assert operator_response.status_code == 200
    assert b"Publish to destinations" in operator_response.content
    assert b"Remove destination assignments" in operator_response.content

    view_only_user = get_user_model().objects.create_user(
        username="view-only-intake-user",
        password="password",
        is_staff=True,
    )
    view_only_user.user_permissions.add(
        Permission.objects.get(codename="view_stagedresourceintake")
    )
    client.force_login(view_only_user)
    view_only_response = client.get(change_url)

    assert view_only_response.status_code == 200
    assert b"Publish to destinations" not in view_only_response.content
    assert b"Remove destination assignments" not in view_only_response.content


def test_cancelled_intake_hides_and_rejects_destination_publication(client: Client) -> None:
    """Prevent operators from opening or submitting publication for a cancelled intake."""
    operator = get_user_model().objects.create_user(
        username="cancelled-intake-operator",
        password="password",
        is_staff=True,
    )
    operator.user_permissions.add(Permission.objects.get(codename="manage_stagedresourceintake"))
    intake = _create_intake(
        submitted_by=operator,
        status=StagedResourceIntakeStatus.CANCELLED,
    )
    change_url = reverse("admin:resource_registry_stagedresourceintake_change", args=(intake.pk,))
    publish_url = reverse("admin:resource_registry_stagedresourceintake_publish", args=(intake.pk,))

    client.force_login(operator)
    change_response = client.get(change_url)
    publish_response = client.get(publish_url)
    publish_post_response = client.post(publish_url, {"namespace_ids": str(uuid4())})

    assert change_response.status_code == 200
    assert b"Publish to destinations" not in change_response.content
    assert publish_response.status_code == 409
    assert publish_response.content == b"INTAKE_NOT_READY_FOR_PUBLICATION"
    assert publish_post_response.status_code == 409
    assert publish_post_response.content == b"INTAKE_NOT_READY_FOR_PUBLICATION"


@pytest.mark.parametrize(
    ("job_status", "expected_label"),
    (
        ("submitted", "Processing pending"),
        ("queued", "Queued for processing"),
        ("running", "Processing"),
        ("completed", "Dataset available"),
        ("completed_with_skips", "Dataset available (some sheets skipped)"),
        ("failed", "Processing failed"),
        ("cancelled", "Processing cancelled"),
    ),
)
def test_processing_status_labels_describe_dataset_availability(
    job_status: str,
    expected_label: str,
) -> None:
    """Describe queued and terminal processing states without calling them published."""
    assert StagedResourcePublicationInline._processing_status_label(job_status) == expected_label


def test_staff_user_without_permission_cannot_manage_staged_intakes() -> None:
    """Deny hidden and direct staged-intake operations to unpermitted staff users."""
    staff_user = get_user_model().objects.create_user(
        username="unpermitted-intake-user",
        password="password",
        is_staff=True,
    )
    intake = _create_intake(submitted_by=staff_user)
    admin_instance = StagedResourceIntakeAdmin(StagedResourceIntake, admin.site)
    request = _request_for_user(staff_user)

    assert admin_instance.has_change_permission(request, intake) is False
    assert admin_instance.get_model_perms(request).get("change", False) is False
    with pytest.raises(PermissionDenied):
        admin_instance.publish_destinations_view(request, str(intake.pk))
    with pytest.raises(PermissionDenied):
        admin_instance.remove_destinations_view(request, str(intake.pk))


def test_destination_choices_allow_detached_destination_despite_other_active_publication() -> None:
    """Keep a detached destination selectable when another intake remains active there."""
    owner = get_user_model().objects.create_superuser(
        username="detached-destination-owner",
        password="password",
    )
    organization = Organization.objects.create(
        name="Destination Choices Organization",
        slug="destination-choices-organization",
        last_modified_by=owner,
        resource_registry_enabled=True,
    )
    workspace = Workspace.objects.create(
        organization=organization,
        name="Assets Library",
        slug="assets-library",
        last_modified_by=owner,
        resource_registry_enabled=True,
    )
    namespace = RegistryNamespace.objects.get(workspace=workspace)
    intake = _create_intake(submitted_by=owner)
    detached_publication = StagedResourcePublication.objects.create(
        intake=intake,
        registry_namespace=namespace,
        organization=organization,
        workspace=workspace,
        visibility_scope=DatasetVisibilityScope.WORKSPACE,
        published_by=owner,
        assignment_status=StagedResourcePublicationStatus.DETACHED,
    )
    other_intake = _create_intake(submitted_by=owner)
    StagedResourcePublication.objects.create(
        intake=other_intake,
        registry_namespace=namespace,
        organization=organization,
        workspace=workspace,
        visibility_scope=DatasetVisibilityScope.WORKSPACE,
        published_by=owner,
        assignment_status=StagedResourcePublicationStatus.ACTIVE,
    )

    choices = StagedResourceIntakeAdmin._destination_choices(intake)

    available_destinations = [
        destination for _, destinations in choices for destination in destinations
    ]

    assert (
        str(namespace.id),
        f"{organization.name} — Workspace: {workspace.name}",
    ) in available_destinations
    assert detached_publication.assignment_status == StagedResourcePublicationStatus.DETACHED


def _request_for_user(user: AbstractUser) -> HttpRequest:
    """Build one Django Admin request authenticated as the supplied user."""
    request = RequestFactory().get("/admin/resource_registry/stagedresourceintake/")
    request.user = user
    return request


def _create_intake(
    *,
    submitted_by: AbstractUser,
    status: StagedResourceIntakeStatus = StagedResourceIntakeStatus.PENDING_UPLOAD,
) -> StagedResourceIntake:
    """Create the minimal durable intake required by the Admin views."""
    now = timezone.now()
    return StagedResourceIntake.objects.create(
        submitted_by=submitted_by,
        original_filename="intake.xlsx",
        normalized_filename="intake.xlsx",
        resolved_file_format="XLSX",
        declared_content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        declared_size_bytes=1,
        storage_provider="local",
        storage_object_key=f"resource-registry/intakes/{uuid4()}/source.xlsx",
        idempotency_key=str(uuid4()),
        request_fingerprint="a" * 64,
        upload_authorization_expires_at=now + timedelta(minutes=5),
        expires_at=now + timedelta(days=1),
        status=status,
    )
