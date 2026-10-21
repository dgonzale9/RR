"""Authorize staged intake publication for scoped administrators."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.conf import settings
from django.contrib.auth.models import Group, Permission

from BotServer.util.user_group_management import UserGroupManagement
from Organization.models import Organization
from resource_registry.intake.services import StagedResourceIntakeService
from resource_registry.models import RegistrationSubmission, StagedResourceIntake
from resource_registry.tests.test_csv_registration_worker_execution import _environment
from resource_registry.uploads.exceptions import UploadApplicationError
from resource_registry.uploads.local_provider import LocalLargeFileUploadProvider
from resource_registry.uploads.services import LargeFileUploadAuthorizationService, UploadAdmission


def test_scoped_admin_reaches_mutating_workspace_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Apply destination capability checks to scoped administrators."""
    user = Mock()
    user.is_superuser = False
    organization = Mock(spec=Organization)
    admission = UploadAdmission(
        organization_id=1,
        workspace_id=2,
        namespace_id=uuid4(),
        actor_id=3,
    )
    authorization_service = Mock(spec=LargeFileUploadAuthorizationService)
    authorization_service.admit.return_value = admission
    monkeypatch.setattr(UserGroupManagement, "is_user_admin", Mock(return_value=False))
    monkeypatch.setattr(UserGroupManagement, "is_user_scoped_admin", Mock(return_value=True))
    service = StagedResourceIntakeService(
        provider=Mock(), authorization_service=authorization_service
    )

    result = service._admit_destination(
        user=user,
        organization=organization,
        visibility_scope="WORKSPACE",
        workspace_id=2,
    )

    assert result == admission
    authorization_service.admit.assert_called_once_with(
        user=user,
        organization=organization,
        workspace_id=2,
        require_mutation=True,
    )


@pytest.mark.django_db(transaction=True)
def test_scoped_admin_can_publish_owned_intake_to_authorized_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Publish a verified intake when the scoped administrator has both capabilities."""
    monkeypatch.setattr(settings, "RESOURCE_REGISTRY_LOCAL_STORAGE_DIR", str(tmp_path))
    environment = _environment()
    scoped_admin_group, _ = Group.objects.get_or_create(name=UserGroupManagement.GROUP_SCOPED_ADMIN)
    environment.user.groups.add(scoped_admin_group)
    environment.user.user_permissions.add(
        Permission.objects.get(codename="create_large_file_upload")
    )
    service = StagedResourceIntakeService(provider=LocalLargeFileUploadProvider())
    content = b"name\nrow\n"
    created = service.create_intake(
        user=environment.user,
        filename="observations.csv",
        content_type="text/csv",
        size_bytes=len(content),
        checksum_algorithm=None,
        checksum=None,
        idempotency_key="scoped-admin-publish",
    )
    intake = StagedResourceIntake.objects.get(pk=created.intake.intake_id)
    source = tmp_path / intake.storage_object_key
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(content)
    service.complete_intake(user=environment.user, intake_id=intake.id, expected_etag=None)
    assert service.run_inspection_jobs(batch_size=1) == 1

    publication = service.publish_intake(
        user=environment.user,
        organization=environment.organization,
        intake_id=intake.id,
        visibility_scope="WORKSPACE",
        workspace_id=environment.workspace.id,
    )

    assert publication.namespace_id == environment.namespace.id
    assert RegistrationSubmission.objects.filter(
        pk=publication.registration_id,
        submitted_by=environment.user,
        workspace=environment.workspace,
    ).exists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("group_name", "grant_upload_permission", "expected_code"),
    [
        (UserGroupManagement.GROUP_SCOPED_ADMIN, False, "UPLOAD_CAPABILITY_DENIED"),
        (UserGroupManagement.GROUP_POWER_USER, True, "AUTHORIZATION_DENIED"),
    ],
)
def test_intake_publication_keeps_role_and_upload_capability_checks(
    group_name: str,
    grant_upload_permission: bool,
    expected_code: str,
) -> None:
    """Reject publication before intake lookup when role or upload capability is absent."""
    environment = _environment()
    group, _ = Group.objects.get_or_create(name=group_name)
    environment.user.groups.add(group)
    if grant_upload_permission:
        environment.user.user_permissions.add(
            Permission.objects.get(codename="create_large_file_upload")
        )
    service = StagedResourceIntakeService(provider=LocalLargeFileUploadProvider())

    with pytest.raises(UploadApplicationError) as error:
        service.publish_intake(
            user=environment.user,
            organization=environment.organization,
            intake_id=uuid4(),
            visibility_scope="WORKSPACE",
            workspace_id=environment.workspace.id,
        )

    assert error.value.code == expected_code
