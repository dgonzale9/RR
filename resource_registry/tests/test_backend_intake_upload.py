"""Exercise private backend intake transfer without external storage calls."""

from __future__ import annotations

from datetime import timedelta
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from azure.core.exceptions import AzureError
from django.conf import settings
from django.contrib.auth.models import Permission, User
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from resource_registry.intake.services import StagedResourceIntakeService
from resource_registry.models import StagedResourceInspectionJob, StagedResourceIntake
from resource_registry.uploads.azure_provider import AzureBlockBlobLargeFileUploadProvider
from resource_registry.uploads.backend_blocks import BACKEND_UPLOAD_CHUNK_SIZE
from resource_registry.uploads.exceptions import UploadApplicationError

pytestmark = pytest.mark.django_db


@pytest.fixture
def upload_environment(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Build real service/provider code around an isolated Azure SDK double.

    Args:
        monkeypatch: Test-scoped configuration and dependency overrides.

    Returns:
        Owner, service, fake SDK client, and uploaded block storage.
    """
    for name, value in (
        ("AZ_STORAGE_ACCOUNT_NAME", "testaccount"),
        ("AZ_STORAGE_ACCOUNT_KEY", "test-key"),
        ("AZ_STORAGE_MEDIA_CONTAINER", "media"),
    ):
        monkeypatch.setattr(settings, name, value, raising=False)
    user = User.objects.create_user(username="backend-uploader", is_staff=True)
    user.user_permissions.add(Permission.objects.get(codename="manage_stagedresourceintake"))
    blocks: dict[str, bytes] = {}
    blob = MagicMock()

    def stage_block(*, block_id: str, data: bytes, length: int) -> None:
        assert length == len(data)
        assert len(block_id.encode("utf-8")) <= 64
        blocks[block_id] = data

    def get_block_list(*, block_list_type: str) -> tuple[list[object], list[object]]:
        assert block_list_type == "all"
        return [], [SimpleNamespace(id=key, size=len(data)) for key, data in blocks.items()]

    blob.stage_block.side_effect = stage_block
    blob.get_block_list.side_effect = get_block_list
    blob.get_blob_properties.return_value = SimpleNamespace(
        size=8,
        etag="test-etag",
        version_id=None,
        content_settings=SimpleNamespace(content_type="text/csv", content_md5=None),
    )
    blob.download_blob.return_value.readall.return_value = b"name\nx\n\n"
    sdk = MagicMock()
    sdk.get_blob_client.return_value = blob
    provider = AzureBlockBlobLargeFileUploadProvider(blob_service_client=sdk)
    service = StagedResourceIntakeService(provider=provider)
    monkeypatch.setattr(
        "resource_registry.api.intakes.views.get_staged_resource_intake_service", lambda: service
    )
    monkeypatch.setattr(
        "resource_registry.admin.get_staged_resource_intake_service", lambda: service
    )
    sas = Mock(side_effect=AssertionError("Backend intake uploads must never issue a SAS."))
    monkeypatch.setattr("resource_registry.uploads.azure_provider.generate_blob_sas", sas)
    return SimpleNamespace(user=user, service=service, blob=blob, sdk=sdk, blocks=blocks, sas=sas)


def _create_intake(environment: SimpleNamespace, size: int = 8) -> StagedResourceIntake:
    created = environment.service.create_intake(
        user=environment.user,
        filename="data.csv",
        content_type="text/csv",
        size_bytes=size,
        checksum_algorithm=None,
        checksum=None,
        idempotency_key="backend-test",
        issue_direct_authorization=False,
    )
    assert created.authorization is None
    return StagedResourceIntake.objects.get(pk=created.intake.intake_id)


def test_api_upload_stays_on_application_and_completion_is_idempotent(
    upload_environment: SimpleNamespace,
) -> None:
    """Create, stage, retry, and complete through the real authenticated API.

    Args:
        upload_environment: Isolated service and storage fixture.
    """
    environment = upload_environment
    client = APIClient()
    client.force_authenticate(environment.user)
    response = client.post(
        reverse("intakes:collection"),
        {"filename": "data.csv", "content_type": "text/csv", "size_bytes": 8},
        format="json",
        HTTP_IDEMPOTENCY_KEY="api-test",
    )
    assert response.status_code == 201
    descriptor = response.json()["upload"]
    assert descriptor["method"] == "backend_blocks_v1"
    assert descriptor["url"].startswith("/api/v1/resource-registry/intakes/")
    assert "sig=" not in response.content.decode()
    block_url = descriptor["url"]
    for _ in range(2):
        assert (
            client.put(
                block_url, b"name\nx\n\n", content_type="application/octet-stream"
            ).status_code
            == 204
        )
    intake_id = response.json()["intake_id"]
    complete_url = reverse("intakes:complete", args=(intake_id,))
    for _ in range(2):
        completed = client.post(complete_url, {}, format="json")
        assert completed.status_code == 200
        assert completed.json()["status"] == "COMPLETED"
    assert StagedResourceInspectionJob.objects.filter(intake_id=intake_id).count() == 1
    assert len(environment.blocks) == 1
    environment.blob.commit_block_list.assert_called_once()
    environment.sas.assert_not_called()


def test_blocks_are_bounded_and_committed_in_file_order(
    upload_environment: SimpleNamespace,
) -> None:
    """Accept out-of-order blocks and derive a correctly sized ordered manifest.

    Args:
        upload_environment: Isolated service and storage fixture.
    """
    environment = upload_environment
    intake = _create_intake(environment, BACKEND_UPLOAD_CHUNK_SIZE + 3)
    for index, content in ((1, b"end"), (0, b"x" * BACKEND_UPLOAD_CHUNK_SIZE)):
        reader = Mock(side_effect=BytesIO(content).read)
        environment.service.upload_backend_block(
            user=environment.user, intake_id=intake.id, block_index=index, read_chunk=reader
        )
        reader.assert_called_once_with(len(content) + 1)
    environment.blob.get_blob_properties.return_value.size = intake.declared_size_bytes
    environment.service.complete_intake(
        user=environment.user, intake_id=intake.id, expected_etag=None, commit_backend_blocks=True
    )
    ordered = environment.blob.commit_block_list.call_args.args[0]
    assert [environment.blocks[key] for key in ordered] == [
        b"x" * BACKEND_UPLOAD_CHUNK_SIZE,
        b"end",
    ]
    environment.sdk.get_blob_client.assert_called_with(
        container="media", blob=intake.storage_object_key
    )


@pytest.mark.parametrize("content", [b"short", b"too-many-bytes"])
def test_wrong_block_length_never_reaches_azure(
    upload_environment: SimpleNamespace, content: bytes
) -> None:
    """Reject truncated and oversized blocks before storage writes.

    Args:
        upload_environment: Isolated service and storage fixture.
        content: Incorrectly sized request body.
    """
    environment = upload_environment
    intake = _create_intake(environment)
    with pytest.raises(UploadApplicationError) as error:
        environment.service.upload_backend_block(
            user=environment.user,
            intake_id=intake.id,
            block_index=0,
            read_chunk=BytesIO(content).read,
        )
    assert error.value.code == "UPLOAD_SIZE_MISMATCH"
    environment.blob.stage_block.assert_not_called()


@pytest.mark.parametrize(
    "condition", ["other_owner", "inactive", "expired", "cancelled", "bad_index"]
)
def test_denied_blocks_do_not_read_or_forward_file_bytes(
    upload_environment: SimpleNamespace, condition: str
) -> None:
    """Enforce ownership, active identity, lifecycle, and block bounds before reading.

    Args:
        upload_environment: Isolated service and storage fixture.
        condition: Admission failure to exercise.
    """
    environment = upload_environment
    intake = _create_intake(environment)
    user = environment.user
    index = 0
    if condition == "other_owner":
        user = User.objects.create_user(username="other-owner")
    elif condition == "inactive":
        user.is_active = False
    elif condition == "expired":
        intake.expires_at = timezone.now() - timedelta(seconds=1)
        intake.save()
    elif condition == "cancelled":
        environment.service.cancel_intake(user=user, intake_id=intake.id)
    else:
        index = 1
    reader = Mock()
    with pytest.raises(UploadApplicationError):
        environment.service.upload_backend_block(
            user=user, intake_id=intake.id, block_index=index, read_chunk=reader
        )
    reader.assert_not_called()
    environment.blob.stage_block.assert_not_called()


def test_missing_blocks_do_not_commit_or_queue_inspection(
    upload_environment: SimpleNamespace,
) -> None:
    """Keep an incomplete upload retryable without queuing an invalid file.

    Args:
        upload_environment: Isolated service and storage fixture.
    """
    environment = upload_environment
    intake = _create_intake(environment)
    with pytest.raises(UploadApplicationError) as error:
        environment.service.complete_intake(
            user=environment.user,
            intake_id=intake.id,
            expected_etag=None,
            commit_backend_blocks=True,
        )
    assert error.value.code == "INTAKE_UPLOAD_INCOMPLETE"
    intake.refresh_from_db()
    assert intake.status == "PENDING_UPLOAD"
    assert not StagedResourceInspectionJob.objects.filter(intake=intake).exists()
    environment.blob.commit_block_list.assert_not_called()


def test_provider_errors_are_sanitized_and_retryable(upload_environment: SimpleNamespace) -> None:
    """Return a stable failure without leaking the SDK exception's URL or credentials.

    Args:
        upload_environment: Isolated service and storage fixture.
    """
    environment = upload_environment
    intake = _create_intake(environment)
    environment.blob.stage_block.side_effect = AzureError("private-url?sig=secret")
    client = APIClient()
    client.force_authenticate(environment.user)
    response = client.put(
        reverse("intakes:block", args=(intake.id, 0)),
        b"name\nx\n\n",
        content_type="application/octet-stream",
    )
    assert response.status_code == 503
    assert response.json()["code"] == "UPLOAD_PROVIDER_TRANSFER_FAILED"
    assert b"secret" not in response.content


def test_admin_upload_requires_permission_and_csrf(upload_environment: SimpleNamespace) -> None:
    """Protect the Admin binary endpoint with its existing permission and CSRF checks.

    Args:
        upload_environment: Isolated service and storage fixture.
    """
    environment = upload_environment
    intake = _create_intake(environment)
    client = Client(enforce_csrf_checks=True)
    client.force_login(environment.user)
    url = reverse("admin:resource_registry_stagedresourceintake_upload_block", args=(intake.id, 0))
    assert (
        client.put(url, b"name\nx\n\n", content_type="application/octet-stream").status_code == 403
    )
    client.get(reverse("admin:resource_registry_stagedresourceintake_upload"))
    token = client.cookies["csrftoken"].value
    assert (
        client.put(
            url, b"name\nx\n\n", content_type="application/octet-stream", HTTP_X_CSRFTOKEN=token
        ).status_code
        == 200
    )
    environment.blob.stage_block.reset_mock()
    staff = User.objects.create_user(username="unprivileged-staff", is_staff=True)
    client.force_login(staff)
    assert (
        client.put(
            url, b"name\nx\n\n", content_type="application/octet-stream", HTTP_X_CSRFTOKEN=token
        ).status_code
        == 403
    )
    environment.blob.stage_block.assert_not_called()
