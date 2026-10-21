"""Executable contracts for the authenticated XLSX/CSV large-file upload boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import cast

import pytest
from azure.storage.blob import BlobServiceClient
from django.contrib.auth.models import Permission, User
from django.test import override_settings
from django.utils import timezone
from django.utils.functional import SimpleLazyObject
from guardian.shortcuts import remove_perm
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from Organization.models import Organization, Workspace
from resource_registry.api.uploads import views
from resource_registry.models import (
    Dataset,
    LargeFileUploadSession,
    LargeFileUploadStatus,
    RegistryNamespace,
    WorkbookIngestionJob,
)
from resource_registry.tests.workspace_permission_helpers import (
    add_workspace_user,
    remove_workspace_access,
)
from resource_registry.uploads.azure_provider import AzureBlockBlobLargeFileUploadProvider
from resource_registry.uploads.contracts import (
    DirectUploadAuthorization,
    SupportedUploadFormat,
    UploadedObjectMetadata,
)
from resource_registry.uploads.exceptions import UploadApplicationError
from resource_registry.uploads.format_policy import UploadFileFormatPolicy
from resource_registry.uploads.services import (
    CreatedUploadSession,
    LargeFileUploadAuthorizationService,
    LargeFileUploadService,
    UploadAdmission,
)

pytestmark = pytest.mark.django_db(transaction=True)

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_EXPECTED_REPLAY_AUTHORIZATION_CALLS = 2
_EXPECTED_FILE_SIZE_BYTES = 128
_EXPECTED_TWO_CLEANUP_CALLS = 2


@dataclass
class _FakeProvider:
    provider_name: str = "fake_blob"
    metadata: UploadedObjectMetadata | None = None
    authorization_calls: int = 0
    inspection_calls: int = 0
    deleted_keys: tuple[str, ...] = ()
    reported_object_key: str | None = None
    inspection_error: UploadApplicationError | None = None
    delete_error: UploadApplicationError | None = None
    authorization_error: UploadApplicationError | None = None

    def create_upload_authorization(
        self,
        *,
        object_key: str,
        content_type: str,
        expires_at: datetime,
        recommended_chunk_size_bytes: int,
        max_parallel_chunks: int,
    ) -> DirectUploadAuthorization:
        self.authorization_calls += 1
        if self.authorization_error is not None:
            raise self.authorization_error
        return DirectUploadAuthorization(
            upload_url=f"https://storage.invalid/blob?sig=ephemeral-{self.authorization_calls}",
            expires_at=expires_at,
            upload_method="block_blob",
            recommended_chunk_size_bytes=recommended_chunk_size_bytes,
            max_parallel_chunks=max_parallel_chunks,
            required_headers={"x-ms-blob-type": "BlockBlob", "Content-Type": content_type},
        )

    def inspect_uploaded_object(self, *, object_key: str) -> UploadedObjectMetadata:
        self.inspection_calls += 1
        if self.inspection_error is not None:
            raise self.inspection_error
        if self.metadata is None:
            raise UploadApplicationError("UPLOAD_PROVIDER_OBJECT_NOT_FOUND", 404)
        return UploadedObjectMetadata(
            object_key=self.reported_object_key or object_key,
            size_bytes=self.metadata.size_bytes,
            content_type=self.metadata.content_type,
            etag=self.metadata.etag,
            version_id=self.metadata.version_id,
            provider_checksum=self.metadata.provider_checksum,
            initial_bytes=self.metadata.initial_bytes,
        )

    def delete_object(self, *, object_key: str) -> None:
        if self.delete_error is not None:
            raise self.delete_error
        self.deleted_keys = (*self.deleted_keys, object_key)


class _FakeAzureDownload:
    def readall(self) -> bytes:
        return b"PK\x03\x04only"


class _FakeAzureBlobClient:
    def __init__(self) -> None:
        self.download_arguments: tuple[int, int] | None = None
        self.url = ""

    def get_blob_properties(self) -> object:
        return SimpleNamespace(
            size=128,
            etag="etag",
            version_id="version",
            content_settings=SimpleNamespace(content_type=_XLSX_MIME, content_md5=None),
        )

    def download_blob(self, *, offset: int, length: int) -> _FakeAzureDownload:
        self.download_arguments = (offset, length)
        return _FakeAzureDownload()

    def delete_blob(self) -> None:
        return None


class _FakeAzureServiceClient:
    def __init__(self) -> None:
        self.blob_client = _FakeAzureBlobClient()
        self.coordinates: tuple[str, str] | None = None

    def get_blob_client(self, *, container: str, blob: str) -> _FakeAzureBlobClient:
        self.coordinates = (container, blob)
        # Matches the real SDK's BlobClient.url -- the provider builds the
        # upload URL from this property instead of an account/container string.
        self.blob_client.url = f"https://account.blob.core.windows.net/{container}/{blob}"
        return self.blob_client


@dataclass(frozen=True)
class _Environment:
    organization: Organization
    workspace: Workspace
    namespace: RegistryNamespace
    user: User
    admission: UploadAdmission


def _environment(*, grant_capability: bool = True) -> _Environment:
    user = User.objects.create_user(username="upload-user")
    organization = Organization.objects.create(
        name="Upload Org",
        slug="upload-org",
        last_modified_by=user,
        resource_registry_enabled=True,
    )
    workspace = Workspace.objects.create(
        organization=organization,
        name="Upload Workspace",
        slug="upload-workspace",
        last_modified_by=user,
        resource_registry_enabled=True,
    )
    add_workspace_user(workspace=workspace, user=user)
    namespace = workspace.resource_registry_namespace
    if grant_capability:
        permission = Permission.objects.get(codename="create_large_file_upload")
        user.user_permissions.add(permission)
    return _Environment(
        organization=organization,
        workspace=workspace,
        namespace=namespace,
        user=user,
        admission=UploadAdmission(
            organization_id=organization.id,
            workspace_id=workspace.id,
            namespace_id=namespace.id,
            actor_id=user.id,
        ),
    )


def _create(
    environment: _Environment,
    provider: _FakeProvider,
    *,
    filename: str = "records.csv",
    content_type: str = "text/csv",
    size_bytes: int = 128,
    idempotency_key: str = "upload-key",
    checksum: str | None = None,
) -> CreatedUploadSession:
    return LargeFileUploadService(provider=provider).create_session(
        admission=environment.admission,
        user=environment.user,
        filename=filename,
        content_type=content_type,
        size_bytes=size_bytes,
        checksum_algorithm="sha256" if checksum else None,
        checksum=checksum,
        idempotency_key=idempotency_key,
    )


def test_upload_authorization_accepts_lazy_authenticated_user() -> None:
    """Admit a Django session user wrapped by authentication middleware."""
    environment = _environment()
    lazy_user = cast(User, SimpleLazyObject(lambda: environment.user))

    admission = LargeFileUploadAuthorizationService().admit(
        user=lazy_user,
        organization=environment.organization,
        workspace_id=environment.workspace.id,
        require_mutation=True,
    )

    assert admission == environment.admission


def test_upload_creation_accepts_lazy_authenticated_user() -> None:
    """Create an upload from a Django session user without a server error."""
    environment = _environment()
    lazy_user = cast(User, SimpleLazyObject(lambda: environment.user))

    created = LargeFileUploadService(provider=_FakeProvider()).create_session(
        admission=environment.admission,
        user=lazy_user,
        filename="records.csv",
        content_type="text/csv",
        size_bytes=128,
        checksum_algorithm=None,
        checksum=None,
        idempotency_key="lazy-user-upload",
    )

    assert created.session.status == LargeFileUploadStatus.PENDING_UPLOAD


@pytest.mark.parametrize(
    ("filename", "content_type", "expected"),
    (
        ("records.CSV", "text/csv", "CSV"),
        ("book.XLSX", _XLSX_MIME, "XLSX"),
        ("records.jsonl", "application/x-ndjson", "JSONL"),
        ("records.NDJSON", "application/json", "JSONL"),
        ("records.parquet", "application/vnd.apache.parquet", "PARQUET"),
        ("records.parquet", "application/octet-stream", "PARQUET"),
    ),
)
def test_format_policy_resolves_configured_dataset_formats(
    filename: str,
    content_type: str,
    expected: str,
) -> None:
    assert (
        UploadFileFormatPolicy()
        .resolve(
            filename=filename,
            declared_content_type=content_type,
        )
        .value
        == expected
    )


@pytest.mark.parametrize(
    ("filename", "content_type", "code"),
    (
        ("book.xlsx.exe", _XLSX_MIME, "UPLOAD_UNSUPPORTED_FILE_FORMAT"),
        ("book.xls", "application/vnd.ms-excel", "UPLOAD_UNSUPPORTED_FILE_FORMAT"),
        ("data.csv", _XLSX_MIME, "UPLOAD_EXTENSION_CONTENT_TYPE_MISMATCH"),
    ),
)
def test_format_policy_rejects_unsupported_or_mismatched_formats(
    filename: str,
    content_type: str,
    code: str,
) -> None:
    with pytest.raises(UploadApplicationError) as caught:
        UploadFileFormatPolicy().resolve(
            filename=filename,
            declared_content_type=content_type,
        )
    assert caught.value.code == code


@pytest.mark.parametrize(
    ("filename", "content_type"),
    (
        ("book.xlsm", "application/vnd.ms-excel.sheet.macroEnabled.12"),
        ("data.parquet.gz", "application/gzip"),
        ("README", "text/plain"),
        ("", "text/csv"),
    ),
)
def test_format_policy_fails_closed_for_all_other_extensions(
    filename: str,
    content_type: str,
) -> None:
    with pytest.raises(UploadApplicationError) as caught:
        UploadFileFormatPolicy().resolve(
            filename=filename,
            declared_content_type=content_type,
        )
    assert caught.value.code == "UPLOAD_UNSUPPORTED_FILE_FORMAT"


@override_settings(RESOURCE_REGISTRY_UPLOAD_ALLOWED_EXTENSIONS=(".xlsx",))
def test_format_policy_honors_configured_extension_allowlist() -> None:
    with pytest.raises(UploadApplicationError) as caught:
        UploadFileFormatPolicy().resolve(filename="records.csv", declared_content_type="text/csv")
    assert caught.value.code == "UPLOAD_UNSUPPORTED_FILE_FORMAT"


@override_settings(
    AZ_STORAGE_ACCOUNT_NAME="account",
    AZ_STORAGE_ACCOUNT_KEY="credential",
    AZ_STORAGE_MEDIA_CONTAINER="container",
    # Explicitly the real-Azure scenario -- local dev's own .env sets
    # AZURE_CUSTOM_DOMAIN for Azurite, which would otherwise leak in here.
    AZURE_CUSTOM_DOMAIN=None,
)
def test_azure_authorization_is_https_single_blob_create_write_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _FakeAzureServiceClient()
    sas_arguments: dict[str, object] = {}

    def capture_sas_arguments(**kwargs: object) -> str:
        sas_arguments.update(kwargs)
        return "sp=cw&spr=https&sig=redacted"

    monkeypatch.setattr(
        "resource_registry.uploads.azure_provider.generate_blob_sas",
        capture_sas_arguments,
    )
    provider = AzureBlockBlobLargeFileUploadProvider(
        blob_service_client=cast(BlobServiceClient, fake_client),
    )
    expires_at = timezone.now() + timedelta(minutes=30)
    authorization = provider.create_upload_authorization(
        object_key="resource-registry/uploads/1/namespace/upload/source.xlsx",
        content_type=_XLSX_MIME,
        expires_at=expires_at,
        recommended_chunk_size_bytes=16 * 1024**2,
        max_parallel_chunks=4,
    )
    assert authorization.upload_url.startswith("https://account.blob.core.windows.net/container/")
    assert "sp=cw" in authorization.upload_url
    assert "sp=r" not in authorization.upload_url
    assert "sp=d" not in authorization.upload_url
    assert authorization.required_headers["x-ms-blob-type"] == "BlockBlob"
    permission = sas_arguments["permission"]
    assert str(permission) == "cw"
    assert sas_arguments["blob_name"] == "resource-registry/uploads/1/namespace/upload/source.xlsx"
    assert sas_arguments["protocol"] == "https"


@override_settings(
    AZ_STORAGE_ACCOUNT_NAME="account",
    AZ_STORAGE_ACCOUNT_KEY="credential",
    AZ_STORAGE_MEDIA_CONTAINER="container",
)
def test_azure_inspection_reads_only_bounded_initial_evidence() -> None:
    fake_client = _FakeAzureServiceClient()
    provider = AzureBlockBlobLargeFileUploadProvider(
        blob_service_client=cast(BlobServiceClient, fake_client),
    )
    metadata = provider.inspect_uploaded_object(object_key="opaque/source.xlsx")
    assert metadata.size_bytes == _EXPECTED_FILE_SIZE_BYTES
    assert metadata.initial_bytes == b"PK\x03\x04only"
    assert fake_client.blob_client.download_arguments == (0, 4096)
    assert fake_client.coordinates == ("container", "opaque/source.xlsx")


@override_settings(
    AZ_STORAGE_ACCOUNT_NAME="account",
    AZ_STORAGE_ACCOUNT_KEY="credential",
    AZ_STORAGE_MEDIA_CONTAINER="container",
    RESOURCE_REGISTRY_UPLOAD_FORMAT_SNIFF_BYTES=10**9,
)
def test_azure_inspection_caps_misconfigured_sample_size() -> None:
    fake_client = _FakeAzureServiceClient()
    provider = AzureBlockBlobLargeFileUploadProvider(
        blob_service_client=cast(BlobServiceClient, fake_client),
    )
    provider.inspect_uploaded_object(object_key="opaque/source.xlsx")
    assert fake_client.blob_client.download_arguments == (0, 64 * 1024)


@override_settings(
    AZ_STORAGE_ACCOUNT_NAME="invalid account",
    AZ_STORAGE_ACCOUNT_KEY="credential",
    AZ_STORAGE_MEDIA_CONTAINER="container",
)
def test_azure_configuration_errors_are_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    def reject_configuration(*_args: object, **_kwargs: object) -> None:
        raise ValueError("provider detail must not escape")

    monkeypatch.setattr(
        "resource_registry.uploads.azure_provider.BlobServiceClient.from_connection_string",
        reject_configuration,
    )
    with pytest.raises(UploadApplicationError) as caught:
        AzureBlockBlobLargeFileUploadProvider()
    assert caught.value.code == "UPLOAD_PROVIDER_UNAVAILABLE"
    assert "provider detail" not in str(caught.value)


def test_canonical_authorization_requires_membership_mapping_and_capability() -> None:
    environment = _environment(grant_capability=False)
    authorization = LargeFileUploadAuthorizationService()
    with pytest.raises(UploadApplicationError) as denied:
        authorization.admit(
            user=environment.user,
            organization=environment.organization,
            workspace_id=environment.workspace.id,
            require_mutation=True,
        )
    assert denied.value.code == "UPLOAD_CAPABILITY_DENIED"
    environment.workspace.is_active = False
    environment.workspace.save(update_fields=["is_active"])  # type: ignore[no-untyped-call]
    with pytest.raises(UploadApplicationError) as hidden:
        authorization.admit(
            user=environment.user,
            organization=environment.organization,
            workspace_id=environment.workspace.id,
            require_mutation=False,
        )
    assert hidden.value.code == "UPLOAD_NOT_FOUND"


def test_existing_upload_access_is_hidden_after_workspace_access_is_lost() -> None:
    """Hide an existing upload after revoking membership and Guardian workspace access."""
    environment = _environment()
    created = _create(environment, _FakeProvider())
    authorization = LargeFileUploadAuthorizationService()
    initial_admission = authorization.admit_existing_upload(
        user=environment.user,
        organization=environment.organization,
        upload_id=created.session.upload_id,
        require_mutation=False,
    )
    assert initial_admission.workspace_id == environment.workspace.id

    # The fixture creator receives Guardian permissions independently of membership.
    # Retain organization viewing access so the denial isolates workspace revocation.
    remove_perm(
        Organization.MANAGE_PERMISSION_CODENAME,
        environment.user,
        environment.organization,
    )
    remove_workspace_access(workspace=environment.workspace, user=environment.user)
    for permission_codename in (
        Workspace.VIEW_PERMISSION_CODENAME,
        Workspace.MANAGE_PERMISSION_CODENAME,
    ):
        remove_perm(permission_codename, environment.user, environment.workspace)
    current_user = User.objects.get(pk=environment.user.pk)

    with pytest.raises(UploadApplicationError) as hidden:
        authorization.admit_existing_upload(
            user=current_user,
            organization=environment.organization,
            upload_id=created.session.upload_id,
            require_mutation=False,
        )

    assert hidden.value.code == "UPLOAD_NOT_FOUND"


def test_lost_upload_capability_denies_mutation_but_preserves_status_access() -> None:
    environment = _environment()
    created = _create(environment, _FakeProvider())
    environment.user.user_permissions.clear()
    current_user = User.objects.get(pk=environment.user.pk)
    authorization = LargeFileUploadAuthorizationService()

    status_admission = authorization.admit_existing_upload(
        user=current_user,
        organization=environment.organization,
        upload_id=created.session.upload_id,
        require_mutation=False,
    )
    assert status_admission.workspace_id == environment.workspace.id

    with pytest.raises(UploadApplicationError) as denied:
        authorization.admit_existing_upload(
            user=current_user,
            organization=environment.organization,
            upload_id=created.session.upload_id,
            require_mutation=True,
        )
    assert denied.value.code == "UPLOAD_CAPABILITY_DENIED"


def test_cross_organization_workspace_and_unmapped_workspace_fail_closed() -> None:
    environment = _environment()
    other_user = User.objects.create_user(username="other-upload-user")
    other_organization = Organization.objects.create(
        name="Other Upload Org",
        slug="other-upload-org",
        last_modified_by=other_user,
    )
    unmapped_workspace = Workspace.objects.create(
        organization=other_organization,
        name="Unmapped Workspace",
        slug="unmapped-workspace",
        last_modified_by=other_user,
    )
    add_workspace_user(workspace=unmapped_workspace, user=environment.user)

    with pytest.raises(UploadApplicationError) as cross_organization:
        LargeFileUploadAuthorizationService().admit(
            user=environment.user,
            organization=environment.organization,
            workspace_id=unmapped_workspace.id,
            require_mutation=False,
        )
    assert cross_organization.value.code == "UPLOAD_NOT_FOUND"

    with pytest.raises(UploadApplicationError) as unmapped:
        LargeFileUploadAuthorizationService().admit(
            user=environment.user,
            organization=other_organization,
            workspace_id=unmapped_workspace.id,
            require_mutation=False,
        )
    assert unmapped.value.code == "UPLOAD_NOT_FOUND"


def test_create_is_idempotent_opaque_and_never_persists_signed_url() -> None:
    environment = _environment()
    provider = _FakeProvider()
    first = _create(environment, provider, filename="../../records.CSV")
    second = _create(environment, provider, filename="records.CSV")

    assert first.session.file_format == SupportedUploadFormat.CSV
    assert first.session.filename == "records.CSV"
    assert second.session.upload_id == first.session.upload_id
    assert second.idempotent_replay is True
    session = LargeFileUploadSession.objects.get()
    assert str(environment.namespace.id) in session.storage_object_key
    assert session.storage_object_key.endswith("/source.csv")
    assert "records" not in session.storage_object_key
    assert "sig=" not in str(session.__dict__)
    assert provider.authorization_calls == _EXPECTED_REPLAY_AUTHORIZATION_CALLS


def test_idempotency_conflict_rejects_format_change() -> None:
    environment = _environment()
    provider = _FakeProvider()
    _create(environment, provider)
    with pytest.raises(UploadApplicationError) as conflict:
        _create(
            environment,
            provider,
            filename="book.xlsx",
            content_type=_XLSX_MIME,
        )
    assert conflict.value.code == "UPLOAD_IDEMPOTENCY_CONFLICT"
    assert LargeFileUploadSession.objects.count() == 1


def test_completed_idempotency_replay_never_reissues_upload_authorization() -> None:
    environment = _environment()
    provider = _FakeProvider(
        metadata=UploadedObjectMetadata(
            object_key="ignored",
            size_bytes=128,
            content_type="text/csv",
            initial_bytes=b"a,b\n1,2",
        )
    )
    first = _create(environment, provider)
    service = LargeFileUploadService(provider=provider)
    service.complete_session(upload_id=first.session.upload_id, admission=environment.admission)

    replay = _create(environment, provider)

    assert replay.idempotent_replay is True
    assert replay.session.status == LargeFileUploadStatus.COMPLETED
    assert replay.authorization is None
    assert provider.authorization_calls == 1


def test_expired_pending_idempotency_replay_never_reissues_authorization() -> None:
    environment = _environment()
    provider = _FakeProvider()
    created = _create(environment, provider)
    LargeFileUploadSession.objects.filter(pk=created.session.upload_id).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )

    with pytest.raises(UploadApplicationError) as caught:
        _create(environment, provider)

    assert caught.value.code == "UPLOAD_EXPIRED"
    assert provider.authorization_calls == 1


def test_pending_idempotency_replay_records_the_refreshed_authorization_expiry() -> None:
    environment = _environment()
    provider = _FakeProvider()
    created = _create(environment, provider)
    LargeFileUploadSession.objects.filter(pk=created.session.upload_id).update(
        upload_authorization_expires_at=timezone.now() - timedelta(seconds=1)
    )

    replay = _create(environment, provider)

    persisted = LargeFileUploadSession.objects.get(pk=created.session.upload_id)
    assert replay.authorization is not None
    assert persisted.upload_authorization_expires_at == replay.authorization.expires_at


@override_settings(RESOURCE_REGISTRY_UPLOAD_AUTHORIZATION_TTL_SECONDS=24 * 60 * 60)
def test_upload_authorization_ttl_is_capped_at_one_hour() -> None:
    before_create = timezone.now()
    created = _create(_environment(), _FakeProvider())
    assert created.authorization is not None
    assert created.authorization.expires_at <= before_create + timedelta(hours=1, seconds=1)


@override_settings(RESOURCE_REGISTRY_UPLOAD_MAX_PENDING_PER_USER=1)
def test_pending_actor_quota_is_bounded() -> None:
    environment = _environment()
    provider = _FakeProvider()
    _create(environment, provider, idempotency_key="one")
    with pytest.raises(UploadApplicationError) as quota:
        _create(environment, provider, idempotency_key="two")
    assert quota.value.code == "UPLOAD_USER_QUOTA_EXCEEDED"


@override_settings(
    RESOURCE_REGISTRY_UPLOAD_MAX_PENDING_PER_USER=10,
    RESOURCE_REGISTRY_UPLOAD_MAX_PENDING_PER_WORKSPACE=1,
)
def test_pending_workspace_quota_is_bounded() -> None:
    environment = _environment()
    provider = _FakeProvider()
    _create(environment, provider, idempotency_key="one")
    with pytest.raises(UploadApplicationError) as quota:
        _create(environment, provider, idempotency_key="two")
    assert quota.value.code == "UPLOAD_WORKSPACE_QUOTA_EXCEEDED"


@override_settings(
    RESOURCE_REGISTRY_UPLOAD_MAX_PENDING_PER_USER=10,
    RESOURCE_REGISTRY_UPLOAD_MAX_PENDING_PER_WORKSPACE=10,
    RESOURCE_REGISTRY_UPLOAD_MAX_PENDING_BYTES_PER_WORKSPACE=200,
)
def test_pending_workspace_byte_quota_is_bounded() -> None:
    environment = _environment()
    provider = _FakeProvider()
    _create(environment, provider, size_bytes=128, idempotency_key="one")
    with pytest.raises(UploadApplicationError) as quota:
        _create(environment, provider, size_bytes=128, idempotency_key="two")
    assert quota.value.code == "UPLOAD_BYTES_QUOTA_EXCEEDED"


@pytest.mark.parametrize(
    ("size_bytes", "expected_code"),
    ((0, "UPLOAD_EMPTY_FILE"), (129, "UPLOAD_FILE_TOO_LARGE")),
)
@override_settings(RESOURCE_REGISTRY_UPLOAD_MAX_FILE_SIZE_BYTES=128)
def test_create_rejects_empty_and_oversized_files(size_bytes: int, expected_code: str) -> None:
    with pytest.raises(UploadApplicationError) as caught:
        _create(_environment(), _FakeProvider(), size_bytes=size_bytes)
    assert caught.value.code == expected_code


@pytest.mark.parametrize(
    ("filename", "content_type", "initial_bytes"),
    (
        ("book.xlsx", _XLSX_MIME, b"PK\x03\x04abcd"),
        ("records.csv", "text/csv", b"a,b\n1,2"),
        ("records.csv", "text/csv", b"name;value\nalpha;1"),
        ("records.csv", "text/csv", b"\xff\xfen\x00a\x00m\x00e\x00"),
        ("records.csv", "text/csv", b"a,b\n1,\x00value"),
    ),
)
def test_complete_validates_bounded_format_evidence_without_creating_registry_entities(
    filename: str,
    content_type: str,
    initial_bytes: bytes,
) -> None:
    environment = _environment()
    provider = _FakeProvider()
    created = _create(
        environment,
        provider,
        filename=filename,
        content_type=content_type,
        checksum="a" * 64,
    )
    provider.metadata = UploadedObjectMetadata(
        object_key="ignored-by-fake",
        size_bytes=128,
        content_type=content_type,
        etag="etag-one",
        initial_bytes=initial_bytes,
    )

    completed = LargeFileUploadService(provider=provider).complete_session(
        upload_id=created.session.upload_id,
        admission=environment.admission,
        expected_etag="etag-one",
    )

    assert completed.status == LargeFileUploadStatus.COMPLETED
    assert completed.ready_for_registration is True
    assert completed.checksum_verification_status == "DEFERRED"
    assert provider.inspection_calls == 1
    assert Dataset.objects.count() == 0
    assert WorkbookIngestionJob.objects.count() == 0


def test_completion_failure_is_sanitized_and_terminal_for_that_attempt() -> None:
    environment = _environment()
    provider = _FakeProvider()
    created = _create(environment, provider)
    provider.metadata = UploadedObjectMetadata(
        object_key="ignored",
        size_bytes=127,
        content_type="text/csv",
        initial_bytes=b"a,b",
    )
    with pytest.raises(UploadApplicationError) as mismatch:
        LargeFileUploadService(provider=provider).complete_session(
            upload_id=created.session.upload_id,
            admission=environment.admission,
        )
    assert mismatch.value.code == "UPLOAD_SIZE_MISMATCH"
    session = LargeFileUploadSession.objects.get()
    assert session.status == LargeFileUploadStatus.FAILED
    assert session.failure_message_sanitized == "Upload validation failed."
    assert session.cleanup_required is False


def test_failed_completion_can_retry_without_premature_cleanup() -> None:
    environment = _environment()
    provider = _FakeProvider(
        metadata=UploadedObjectMetadata(
            object_key="ignored",
            size_bytes=127,
            content_type="text/csv",
            initial_bytes=b"a,b",
        )
    )
    created = _create(environment, provider)
    service = LargeFileUploadService(provider=provider)
    with pytest.raises(UploadApplicationError):
        service.complete_session(
            upload_id=created.session.upload_id,
            admission=environment.admission,
        )
    assert service.expire_sessions(batch_size=10) == 0
    assert provider.deleted_keys == ()

    provider.metadata = UploadedObjectMetadata(
        object_key="ignored",
        size_bytes=128,
        content_type="text/csv",
        initial_bytes=b"a,b\n1,2",
    )
    completed = service.complete_session(
        upload_id=created.session.upload_id,
        admission=environment.admission,
    )
    assert completed.status == LargeFileUploadStatus.COMPLETED


@pytest.mark.parametrize(
    ("filename", "content_type", "sample", "expected_code"),
    (
        ("book.xlsx", _XLSX_MIME, b"not-a-zip", "UPLOAD_FORMAT_EVIDENCE_MISMATCH"),
        ("book.xlsx", _XLSX_MIME, b"PK", "UPLOAD_FORMAT_EVIDENCE_MISMATCH"),
        ("records.csv", "text/csv", b"a\x00b", "UPLOAD_FORMAT_EVIDENCE_MISMATCH"),
        ("records.csv", "text/csv", b"", "UPLOAD_FORMAT_EVIDENCE_MISMATCH"),
    ),
)
def test_completion_rejects_obvious_format_evidence_mismatch(
    filename: str,
    content_type: str,
    sample: bytes,
    expected_code: str,
) -> None:
    environment = _environment()
    provider = _FakeProvider()
    created = _create(
        environment,
        provider,
        filename=filename,
        content_type=content_type,
    )
    provider.metadata = UploadedObjectMetadata(
        object_key="ignored",
        size_bytes=128,
        content_type=content_type,
        initial_bytes=sample,
    )

    with pytest.raises(UploadApplicationError) as caught:
        LargeFileUploadService(provider=provider).complete_session(
            upload_id=created.session.upload_id,
            admission=environment.admission,
        )
    assert caught.value.code == expected_code


@pytest.mark.parametrize(
    ("reported_object_key", "size_bytes", "etag", "expected_code"),
    (
        ("foreign/object.csv", 128, "expected", "UPLOAD_PROVIDER_PATH_MISMATCH"),
        (None, 128, "actual", "UPLOAD_ETAG_MISMATCH"),
        (None, 0, "expected", "UPLOAD_EMPTY_FILE"),
    ),
)
def test_completion_rejects_untrusted_or_mismatched_provider_evidence(
    reported_object_key: str | None,
    size_bytes: int,
    etag: str,
    expected_code: str,
) -> None:
    environment = _environment()
    provider = _FakeProvider()
    created = _create(environment, provider)
    provider.reported_object_key = reported_object_key
    provider.metadata = UploadedObjectMetadata(
        object_key="ignored",
        size_bytes=size_bytes,
        content_type="text/csv",
        etag=etag,
        initial_bytes=b"a,b\n1,2",
    )

    with pytest.raises(UploadApplicationError) as caught:
        LargeFileUploadService(provider=provider).complete_session(
            upload_id=created.session.upload_id,
            admission=environment.admission,
            expected_etag="expected",
        )
    assert caught.value.code == expected_code


def test_repeated_completion_is_idempotent_without_reinspection() -> None:
    environment = _environment()
    provider = _FakeProvider(
        metadata=UploadedObjectMetadata(
            object_key="ignored",
            size_bytes=128,
            content_type="text/csv",
            initial_bytes=b"name;value\nalpha;1",
        )
    )
    created = _create(environment, provider)
    service = LargeFileUploadService(provider=provider)
    first = service.complete_session(
        upload_id=created.session.upload_id,
        admission=environment.admission,
    )
    second = service.complete_session(
        upload_id=created.session.upload_id,
        admission=environment.admission,
    )
    assert first.status == second.status == LargeFileUploadStatus.COMPLETED
    assert provider.inspection_calls == 1


def test_cancel_is_idempotent_and_deletes_only_server_owned_object() -> None:
    environment = _environment()
    provider = _FakeProvider()
    created = _create(environment, provider)
    service = LargeFileUploadService(provider=provider)
    first = service.cancel_session(
        upload_id=created.session.upload_id,
        admission=environment.admission,
    )
    second = service.cancel_session(
        upload_id=created.session.upload_id,
        admission=environment.admission,
    )
    assert first.status == second.status == LargeFileUploadStatus.CANCELLED
    assert len(provider.deleted_keys) == 1
    assert provider.deleted_keys[0].endswith("/source.csv")


def test_completed_session_cannot_be_cancelled() -> None:
    environment = _environment()
    provider = _FakeProvider(
        metadata=UploadedObjectMetadata(
            object_key="ignored",
            size_bytes=128,
            content_type="text/csv",
            initial_bytes=b"a,b\n1,2",
        )
    )
    created = _create(environment, provider)
    service = LargeFileUploadService(provider=provider)
    service.complete_session(upload_id=created.session.upload_id, admission=environment.admission)

    with pytest.raises(UploadApplicationError) as caught:
        service.cancel_session(
            upload_id=created.session.upload_id,
            admission=environment.admission,
        )
    assert caught.value.code == "UPLOAD_INVALID_STATE"
    assert provider.deleted_keys == ()


def test_cancelled_session_cannot_complete_after_race_is_resolved() -> None:
    environment = _environment()
    provider = _FakeProvider(
        metadata=UploadedObjectMetadata(
            object_key="ignored",
            size_bytes=128,
            content_type="text/csv",
            initial_bytes=b"a,b\n1,2",
        )
    )
    created = _create(environment, provider)
    service = LargeFileUploadService(provider=provider)
    service.cancel_session(upload_id=created.session.upload_id, admission=environment.admission)

    with pytest.raises(UploadApplicationError) as caught:
        service.complete_session(
            upload_id=created.session.upload_id,
            admission=environment.admission,
        )
    assert caught.value.code == "UPLOAD_INVALID_STATE"
    assert provider.inspection_calls == 0


def test_foreign_cancel_returns_no_disclosure_and_never_deletes_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = _environment()
    provider = _FakeProvider()
    created = _create(environment, provider)
    foreign_user = User.objects.create_user(username="foreign-cancel-user")
    monkeypatch.setattr(
        views,
        "get_large_file_upload_service",
        lambda: LargeFileUploadService(provider=provider),
    )
    request = APIRequestFactory().post(
        f"/api/v1/resource-registry/uploads/{created.session.upload_id}/cancel/",
        {},
        format="json",
    )
    request.organization = environment.organization  # type: ignore[attr-defined]
    force_authenticate(request, user=foreign_user)

    response = views.UploadSessionCancelView.as_view()(
        request,
        upload_id=created.session.upload_id,
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.data["code"] == "UPLOAD_NOT_FOUND"
    assert provider.deleted_keys == ()


def test_expiration_ignores_completed_and_cleans_abandoned_sessions() -> None:
    environment = _environment()
    provider = _FakeProvider()
    abandoned = _create(environment, provider, idempotency_key="abandoned")
    future = _create(environment, provider, idempotency_key="future")
    LargeFileUploadSession.objects.filter(pk=abandoned.session.upload_id).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    count = LargeFileUploadService(provider=provider).expire_sessions(batch_size=10)
    assert count == 1
    assert LargeFileUploadSession.objects.get(pk=abandoned.session.upload_id).status == "EXPIRED"
    assert (
        LargeFileUploadSession.objects.get(pk=future.session.upload_id).status == "PENDING_UPLOAD"
    )


def test_completion_detected_expiry_remains_eligible_for_cleanup() -> None:
    environment = _environment()
    provider = _FakeProvider()
    created = _create(environment, provider)
    LargeFileUploadSession.objects.filter(pk=created.session.upload_id).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    service = LargeFileUploadService(provider=provider)

    with pytest.raises(UploadApplicationError) as caught:
        service.complete_session(
            upload_id=created.session.upload_id,
            admission=environment.admission,
        )
    assert caught.value.code == "UPLOAD_EXPIRED"
    assert LargeFileUploadSession.objects.get(pk=created.session.upload_id).cleanup_required is True

    assert service.expire_sessions(batch_size=1) == 0
    assert provider.deleted_keys == ()
    LargeFileUploadSession.objects.filter(pk=created.session.upload_id).update(
        upload_authorization_expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert service.expire_sessions(batch_size=1) == 0
    assert provider.deleted_keys != ()


def test_cleanup_failure_remains_retriable_and_never_reactivates_session() -> None:
    environment = _environment()
    provider = _FakeProvider(
        delete_error=UploadApplicationError("UPLOAD_PROVIDER_CLEANUP_FAILED", 503)
    )
    created = _create(environment, provider)
    service = LargeFileUploadService(provider=provider)
    cancelled = service.cancel_session(
        upload_id=created.session.upload_id,
        admission=environment.admission,
    )
    persisted = LargeFileUploadSession.objects.get(pk=created.session.upload_id)
    assert cancelled.status == LargeFileUploadStatus.CANCELLED
    assert persisted.cleanup_required is True

    provider.delete_error = None
    LargeFileUploadSession.objects.filter(pk=created.session.upload_id).update(
        upload_authorization_expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert service.expire_sessions(batch_size=1) == 0
    persisted.refresh_from_db()
    assert persisted.status == LargeFileUploadStatus.CANCELLED
    assert persisted.cleanup_required is False


def test_cancel_repeats_cleanup_after_outstanding_sas_expires() -> None:
    environment = _environment()
    provider = _FakeProvider()
    created = _create(environment, provider)
    service = LargeFileUploadService(provider=provider)

    service.cancel_session(upload_id=created.session.upload_id, admission=environment.admission)
    persisted = LargeFileUploadSession.objects.get(pk=created.session.upload_id)
    assert persisted.cleanup_required is True
    assert len(provider.deleted_keys) == 1

    assert service.expire_sessions(batch_size=1) == 0
    assert len(provider.deleted_keys) == 1
    LargeFileUploadSession.objects.filter(pk=created.session.upload_id).update(
        upload_authorization_expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert service.expire_sessions(batch_size=1) == 0
    persisted.refresh_from_db()
    assert len(provider.deleted_keys) == _EXPECTED_TWO_CLEANUP_CALLS
    assert persisted.status == LargeFileUploadStatus.CANCELLED
    assert persisted.cleanup_required is False


def test_create_api_returns_ephemeral_authorization_without_namespace_or_object_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = _environment()
    provider = _FakeProvider()
    service = LargeFileUploadService(provider=provider)
    monkeypatch.setattr(views, "get_large_file_upload_service", lambda: service)
    request = APIRequestFactory().post(
        "/api/v1/resource-registry/uploads/",
        {
            "workspace_id": environment.workspace.id,
            "filename": "records.csv",
            "content_type": "text/csv",
            "size_bytes": 128,
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY="api-key",
    )
    request.organization = environment.organization  # type: ignore[attr-defined]
    force_authenticate(request, user=environment.user)

    response = views.UploadSessionCollectionView.as_view()(request)

    assert response.status_code == status.HTTP_201_CREATED
    assert response.data["file_format"] == "CSV"
    assert response.data["upload"]["method"] == "block_blob"
    assert "namespace" not in response.data
    assert "object_key" not in response.data
    session = LargeFileUploadSession.objects.get()
    assert "sig=" not in str(session.__dict__)


@pytest.mark.parametrize("forbidden_field", ("namespace_id", "storage_object_key", "tenant_id"))
def test_create_api_rejects_caller_authority_and_storage_fields(
    monkeypatch: pytest.MonkeyPatch,
    forbidden_field: str,
) -> None:
    environment = _environment()
    monkeypatch.setattr(
        views,
        "get_large_file_upload_service",
        lambda: LargeFileUploadService(provider=_FakeProvider()),
    )
    payload: dict[str, object] = {
        "workspace_id": environment.workspace.id,
        "filename": "records.csv",
        "content_type": "text/csv",
        "size_bytes": 128,
        forbidden_field: "caller-controlled-value",
    }
    request = APIRequestFactory().post(
        "/api/v1/resource-registry/uploads/",
        payload,
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"forbidden-{forbidden_field}",
    )
    request.organization = environment.organization  # type: ignore[attr-defined]
    force_authenticate(request, user=environment.user)

    response = views.UploadSessionCollectionView.as_view()(request)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data["code"] == "UPLOAD_INVALID_REQUEST"
    assert LargeFileUploadSession.objects.count() == 0


def test_create_api_rejects_multipart_file_upload() -> None:
    environment = _environment()
    request = APIRequestFactory().post(
        "/api/v1/resource-registry/uploads/",
        {
            "workspace_id": environment.workspace.id,
            "filename": "records.csv",
            "content_type": "text/csv",
            "size_bytes": 128,
        },
        format="multipart",
        HTTP_IDEMPOTENCY_KEY="multipart-key",
    )
    request.organization = environment.organization  # type: ignore[attr-defined]
    force_authenticate(request, user=environment.user)

    response = views.UploadSessionCollectionView.as_view()(request)

    assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE


def test_completed_create_replay_returns_status_without_sas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = _environment()
    provider = _FakeProvider(
        metadata=UploadedObjectMetadata(
            object_key="ignored",
            size_bytes=128,
            content_type="text/csv",
            initial_bytes=b"a,b\n1,2",
        )
    )
    created = _create(environment, provider, idempotency_key="completed-api-key")
    service = LargeFileUploadService(provider=provider)
    service.complete_session(upload_id=created.session.upload_id, admission=environment.admission)
    monkeypatch.setattr(views, "get_large_file_upload_service", lambda: service)
    request = APIRequestFactory().post(
        "/api/v1/resource-registry/uploads/",
        {
            "workspace_id": environment.workspace.id,
            "filename": "records.csv",
            "content_type": "text/csv",
            "size_bytes": 128,
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY="completed-api-key",
    )
    request.organization = environment.organization  # type: ignore[attr-defined]
    force_authenticate(request, user=environment.user)

    response = views.UploadSessionCollectionView.as_view()(request)

    assert response.status_code == status.HTTP_200_OK
    assert response.data["status"] == LargeFileUploadStatus.COMPLETED
    assert "upload" not in response.data
    assert provider.authorization_calls == 1


def test_anonymous_create_api_is_rejected_before_service_execution() -> None:
    request = APIRequestFactory().post(
        "/api/v1/resource-registry/uploads/",
        {
            "workspace_id": 1,
            "filename": "records.csv",
            "content_type": "text/csv",
            "size_bytes": 128,
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY="api-key",
    )
    response = views.UploadSessionCollectionView.as_view()(request)
    assert response.status_code in {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN}


def test_status_api_returns_sanitized_metadata_only(monkeypatch: pytest.MonkeyPatch) -> None:
    environment = _environment()
    provider = _FakeProvider()
    service = LargeFileUploadService(provider=provider)
    created = _create(environment, provider)
    monkeypatch.setattr(views, "get_large_file_upload_service", lambda: service)
    request = APIRequestFactory().get(
        f"/api/v1/resource-registry/uploads/{created.session.upload_id}/"
    )
    request.organization = environment.organization  # type: ignore[attr-defined]
    force_authenticate(request, user=environment.user)

    response = views.UploadSessionDetailView.as_view()(
        request,
        upload_id=created.session.upload_id,
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.data["upload_id"] == created.session.upload_id
    assert "object_key" not in response.data
    assert "upload_url" not in response.data
    assert "namespace" not in response.data


def test_upload_audit_events_are_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    environment = _environment()
    provider = _FakeProvider()
    logged_events: list[tuple[str, dict[str, object]]] = []

    def capture_log(message: str, *, extra: dict[str, object]) -> None:
        logged_events.append((message, extra))

    monkeypatch.setattr("resource_registry.uploads.services._logger.info", capture_log)
    created = _create(environment, provider)
    with pytest.raises(UploadApplicationError):
        _create(
            environment,
            provider,
            filename="book.xlsx",
            content_type=_XLSX_MIME,
        )

    messages = " ".join(message for message, _extra in logged_events)
    serialized_events = str(logged_events)
    assert "Resource Registry upload lifecycle event." in messages
    assert "Resource Registry upload request rejected." in messages
    assert created.authorization is not None
    assert created.authorization.upload_url not in serialized_events
    assert "sig=" not in serialized_events
