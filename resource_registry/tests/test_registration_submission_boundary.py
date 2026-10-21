"""Executable contracts for authorized completed-upload registration submission."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
from threading import Barrier
from uuid import UUID

import pytest
from django.contrib.auth.models import Permission, User
from django.db import close_old_connections
from django.utils import timezone
from guardian.shortcuts import remove_perm
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from Organization.models import Organization, OrganizationMembership, Workspace
from resource_registry.api.uploads.views import UploadSessionRegistrationView
from resource_registry.models import (
    Dataset,
    DatasetRegistrationJob,
    DatasetRegistrationJobStatus,
    LargeFileUploadSession,
    LargeFileUploadStatus,
    RegistrationSubmission,
    RegistryNamespace,
    WorkbookIngestionGroup,
    WorkbookIngestionJob,
    WorkbookIngestionJobStatus,
)
from resource_registry.registration_submissions.services import RegistrationSubmissionService
from resource_registry.services.workbook_ingestion_job_service import WorkbookIngestionJobService
from resource_registry.tests.workspace_permission_helpers import (
    add_workspace_user,
    remove_workspace_user,
)
from resource_registry.uploads.exceptions import UploadApplicationError

pytestmark = pytest.mark.django_db(transaction=True)

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@dataclass(frozen=True)
class _Environment:
    organization: Organization
    workspace: Workspace
    namespace: RegistryNamespace
    user: User


def _environment(*, grant_permission: bool = True) -> _Environment:
    user = User.objects.create_user(username="registration-user")
    organization = Organization.objects.create(
        name="Registration Org",
        slug="registration-org",
        last_modified_by=user,
        resource_registry_enabled=True,
    )
    workspace = Workspace.objects.create(
        organization=organization,
        name="Registration Workspace",
        slug="registration-workspace",
        last_modified_by=user,
        resource_registry_enabled=True,
    )
    add_workspace_user(workspace=workspace, user=user)
    namespace = workspace.resource_registry_namespace
    if grant_permission:
        permission = Permission.objects.get(codename="submit_upload_registration")
        user.user_permissions.add(permission)
    return _Environment(
        organization=organization,
        workspace=workspace,
        namespace=namespace,
        user=user,
    )


def _upload(
    environment: _Environment,
    *,
    file_format: str = "CSV",
    status_value: str = LargeFileUploadStatus.COMPLETED,
    registry_namespace: RegistryNamespace | None = None,
) -> LargeFileUploadSession:
    suffix = ".xlsx" if file_format == "XLSX" else ".csv"
    content_type = _XLSX_MIME if file_format == "XLSX" else "text/csv"
    now = timezone.now()
    return LargeFileUploadSession.objects.create(
        organization=environment.organization,
        workspace=environment.workspace,
        registry_namespace=registry_namespace or environment.namespace,
        submitted_by=environment.user,
        original_filename=f"source{suffix}",
        normalized_filename=f"source{suffix}",
        resolved_file_format=file_format,
        declared_content_type=content_type,
        detected_content_type=content_type,
        declared_size_bytes=128,
        actual_size_bytes=128,
        storage_provider="azure_blob",
        storage_object_key=(
            f"resource-registry/uploads/{environment.organization.id}/"
            f"{environment.namespace.id}/{file_format.lower()}/source{suffix}"
        ),
        checksum_verification_status="NOT_PROVIDED",
        status=status_value,
        idempotency_key=f"upload-{file_format}-{status_value}",
        request_fingerprint="a" * 64,
        upload_authorization_expires_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(hours=1),
        uploaded_at=now if status_value == LargeFileUploadStatus.COMPLETED else None,
        completed_at=now if status_value == LargeFileUploadStatus.COMPLETED else None,
    )


@pytest.mark.parametrize(
    ("file_format", "job_type"),
    (("XLSX", "WorkbookIngestionJob"), ("CSV", "DatasetRegistrationJob")),
)
def test_completed_upload_creates_exact_format_job_without_execution(
    file_format: str,
    job_type: str,
) -> None:
    environment = _environment()
    upload = _upload(environment, file_format=file_format)

    result = RegistrationSubmissionService().submit(
        user=environment.user,
        organization=environment.organization,
        upload_id=upload.id,
    )

    submission = RegistrationSubmission.objects.get(pk=result.registration_id)
    assert result.status == "SUBMITTED"
    assert result.job_type.value == job_type
    assert submission.upload_session_id == upload.id
    assert submission.registry_namespace_id == environment.namespace.id
    assert submission.visibility_scope == "WORKSPACE"
    job: WorkbookIngestionJob | DatasetRegistrationJob
    if file_format == "XLSX":
        job = WorkbookIngestionJob.objects.get(registration_submission=submission)
        assert job.status == WorkbookIngestionJobStatus.SUBMITTED
        assert WorkbookIngestionJobService().claim_queued_jobs(batch_size=1) == []
    else:
        job = DatasetRegistrationJob.objects.get(registration_submission=submission)
        assert job.status == DatasetRegistrationJobStatus.SUBMITTED
    assert job.request_payload_json == {"upload_id": str(upload.id)}
    assert Dataset.objects.count() == 0
    assert WorkbookIngestionGroup.objects.count() == 0


def test_duplicate_submission_returns_one_registration_and_one_job() -> None:
    environment = _environment()
    upload = _upload(environment)
    service = RegistrationSubmissionService()

    first = service.submit(
        user=environment.user,
        organization=environment.organization,
        upload_id=upload.id,
    )
    second = service.submit(
        user=environment.user,
        organization=environment.organization,
        upload_id=upload.id,
    )

    assert second.registration_id == first.registration_id
    assert second.idempotent_replay is True
    assert RegistrationSubmission.objects.count() == 1
    assert DatasetRegistrationJob.objects.count() == 1


def test_concurrent_submission_creates_exactly_one_registration_job() -> None:
    environment = _environment()
    upload = _upload(environment)
    barrier = Barrier(2)

    def submit_from_fresh_connection() -> UUID:
        close_old_connections()
        try:
            user = User.objects.get(pk=environment.user.pk)
            organization = Organization.objects.get(pk=environment.organization.pk)
            barrier.wait()
            return (
                RegistrationSubmissionService()
                .submit(
                    user=user,
                    organization=organization,
                    upload_id=upload.id,
                )
                .registration_id
            )
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        registration_ids = tuple(
            executor.map(lambda _index: submit_from_fresh_connection(), range(2))
        )

    assert len(set(registration_ids)) == 1
    assert RegistrationSubmission.objects.count() == 1
    assert DatasetRegistrationJob.objects.count() == 1


@pytest.mark.parametrize(
    ("upload_status", "expected_code"),
    (
        (LargeFileUploadStatus.PENDING_UPLOAD, "UPLOAD_NOT_COMPLETED"),
        (LargeFileUploadStatus.VALIDATING, "UPLOAD_NOT_COMPLETED"),
        (LargeFileUploadStatus.FAILED, "UPLOAD_NOT_COMPLETED"),
        (LargeFileUploadStatus.CANCELLED, "UPLOAD_CANCELLED"),
        (LargeFileUploadStatus.EXPIRED, "UPLOAD_EXPIRED"),
    ),
)
def test_only_completed_uploads_are_eligible(upload_status: str, expected_code: str) -> None:
    environment = _environment()
    upload = _upload(environment, status_value=upload_status)

    with pytest.raises(UploadApplicationError) as caught:
        RegistrationSubmissionService().submit(
            user=environment.user,
            organization=environment.organization,
            upload_id=upload.id,
        )
    assert caught.value.code == expected_code
    assert RegistrationSubmission.objects.count() == 0


def test_permission_and_current_workspace_access_are_revalidated() -> None:
    environment = _environment()
    upload = _upload(environment)
    environment.user.user_permissions.clear()
    current_user = User.objects.get(pk=environment.user.pk)
    with pytest.raises(UploadApplicationError) as permission_denied:
        RegistrationSubmissionService().submit(
            user=current_user,
            organization=environment.organization,
            upload_id=upload.id,
        )
    assert permission_denied.value.code == "AUTHORIZATION_DENIED"

    permission = Permission.objects.get(codename="submit_upload_registration")
    current_user.user_permissions.add(permission)
    OrganizationMembership.objects.filter(
        organization=environment.organization,
        user=current_user,
    ).delete()
    remove_perm(
        Organization.MANAGE_PERMISSION_CODENAME,
        current_user,
        environment.organization,
    )
    remove_perm(
        Organization.VIEW_PERMISSION_CODENAME,
        current_user,
        environment.organization,
    )
    remove_workspace_user(workspace=environment.workspace, user=current_user)
    current_user = User.objects.get(pk=current_user.pk)
    with pytest.raises(UploadApplicationError) as membership_denied:
        RegistrationSubmissionService().submit(
            user=current_user,
            organization=environment.organization,
            upload_id=upload.id,
        )
    assert membership_denied.value.code == "UPLOAD_NOT_FOUND"


def test_noncanonical_namespace_linkage_fails_closed() -> None:
    environment = _environment()
    other_workspace = Workspace.objects.create(
        organization=environment.organization,
        name="Other Workspace",
        slug="other-registration-workspace",
        last_modified_by=environment.user,
        resource_registry_enabled=True,
    )
    other_namespace = other_workspace.resource_registry_namespace
    upload = _upload(environment, registry_namespace=other_namespace)

    with pytest.raises(UploadApplicationError) as caught:
        RegistrationSubmissionService().submit(
            user=environment.user,
            organization=environment.organization,
            upload_id=upload.id,
        )
    assert caught.value.code == "UPLOAD_NOT_FOUND"


def test_unsupported_persisted_format_fails_closed() -> None:
    environment = _environment()
    upload = _upload(environment, file_format="XML")
    with pytest.raises(UploadApplicationError) as caught:
        RegistrationSubmissionService().submit(
            user=environment.user,
            organization=environment.organization,
            upload_id=upload.id,
        )
    assert caught.value.code == "UPLOAD_FORMAT_UNSUPPORTED"


def test_registration_api_accepts_only_upload_id_from_url() -> None:
    environment = _environment()
    upload = _upload(environment, file_format="XLSX")
    request = APIRequestFactory().post(
        f"/api/v1/resource-registry/uploads/{upload.id}/register/",
        {},
        format="json",
    )
    request.organization = environment.organization  # type: ignore[attr-defined]
    force_authenticate(request, user=environment.user)

    response = UploadSessionRegistrationView.as_view()(request, upload_id=upload.id)

    assert response.status_code == status.HTTP_202_ACCEPTED
    assert response.data["status"] == "SUBMITTED"
    assert response.data["job_type"] == "WorkbookIngestionJob"


@pytest.mark.parametrize(
    "forbidden_field",
    ("tenant_id", "namespace_id", "workspace_id", "object_key", "file_format", "filename"),
)
def test_registration_api_rejects_all_caller_metadata(forbidden_field: str) -> None:
    environment = _environment()
    upload = _upload(environment)
    request = APIRequestFactory().post(
        f"/api/v1/resource-registry/uploads/{upload.id}/register/",
        {forbidden_field: "caller-value"},
        format="json",
    )
    request.organization = environment.organization  # type: ignore[attr-defined]
    force_authenticate(request, user=environment.user)

    response = UploadSessionRegistrationView.as_view()(request, upload_id=upload.id)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert RegistrationSubmission.objects.count() == 0


def test_registration_audit_never_logs_upload_storage_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = _environment()
    upload = _upload(environment)
    logged_events: list[tuple[str, dict[str, object]]] = []

    def capture_log(message: str, *, extra: dict[str, object]) -> None:
        logged_events.append((message, extra))

    monkeypatch.setattr(
        "resource_registry.registration_submissions.services._logger.info",
        capture_log,
    )
    service = RegistrationSubmissionService()
    service.submit(
        user=environment.user,
        organization=environment.organization,
        upload_id=upload.id,
    )
    service.submit(
        user=environment.user,
        organization=environment.organization,
        upload_id=upload.id,
    )

    serialized_events = str(logged_events)
    assert "REGISTRATION_SUBMITTED" in serialized_events
    assert "REGISTRATION_SUBMISSION_REUSED" in serialized_events
    assert upload.storage_object_key not in serialized_events
    assert upload.normalized_filename not in serialized_events
