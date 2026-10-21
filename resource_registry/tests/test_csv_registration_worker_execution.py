"""Integration contracts for authorized upload-backed CSV execution."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from django.conf import settings
from django.contrib.auth.models import Permission, User
from django.db import close_old_connections
from django.utils import timezone

from Organization.models import Organization, Workspace
from resource_registry.application.commands import RegisterDatasetCommand
from resource_registry.application.dataset_registration import (
    DatasetRegistrationFacade,
    RegisterDatasetResult,
)
from resource_registry.models import (
    Dataset,
    DatasetArtifact,
    DatasetRegistrationJob,
    DatasetRegistrationJobStatus,
    DatasetVersion,
    LargeFileUploadSession,
    LargeFileUploadStatus,
    RegistryNamespace,
    StagedResourceIntake,
    StagedResourceIntakeStatus,
    StagedResourcePublication,
)
from resource_registry.registration_submissions.services import RegistrationSubmissionService
from resource_registry.services.csv_upload_materialization_service import (
    CsvUploadMaterializationService,
    MaterializedCsvUpload,
)
from resource_registry.services.dataset_registration_job_execution_service import (
    DatasetRegistrationJobExecutionService,
)
from resource_registry.services.dataset_registration_job_service import (
    DatasetRegistrationJobService,
)
from resource_registry.services.dataset_registration_job_worker_runner import (
    DatasetRegistrationJobWorkerRunner,
)
from resource_registry.services.registry.artifact_lifecycle_service import ArtifactLifecycleService
from resource_registry.services.registry.dataset_registry_service import DatasetRegistryService
from resource_registry.storage.base import StorageObjectMetadata, StorageProvider
from resource_registry.storage.factory import DataRegistryStorageFactory
from resource_registry.tests.workspace_permission_helpers import add_workspace_user

pytestmark = pytest.mark.django_db(transaction=True)
_EXPECTED_ARTIFACT_COUNT = 2


@dataclass(frozen=True)
class _Environment:
    organization: Organization
    workspace: Workspace
    namespace: RegistryNamespace
    user: User


def _environment() -> _Environment:
    identifier = uuid4().hex
    user = User.objects.create_user(username=f"csv-worker-user-{identifier}")
    organization = Organization.objects.create(
        name=f"CSV Worker Org {identifier}",
        slug=f"csv-worker-org-{identifier}",
        last_modified_by=user,
        resource_registry_enabled=True,
    )
    workspace = Workspace.objects.create(
        organization=organization,
        name=f"CSV Worker Workspace {identifier}",
        slug=f"csv-worker-workspace-{identifier}",
        last_modified_by=user,
        resource_registry_enabled=True,
    )
    add_workspace_user(workspace=workspace, user=user)
    namespace = workspace.resource_registry_namespace
    user.user_permissions.add(Permission.objects.get(codename="submit_upload_registration"))
    return _Environment(organization, workspace, namespace, user)


def _create_job(
    environment: _Environment,
    *,
    storage_root: Path,
    content: bytes = b"alpha,metric,observed_at\nA,10,2026-01-01\nB,20,2026-01-02\n",
    visibility_scope: str = "WORKSPACE",
    idempotency_key: str = "csv-worker-upload",
    source_format: str = "CSV",
    content_type: str = "text/csv",
) -> tuple[LargeFileUploadSession, DatasetRegistrationJob, Path]:
    """Create one trusted registration job with a source format selected by the caller."""
    source_suffix = source_format.lower()
    source_name = (
        f"source.{source_suffix}"
        if idempotency_key == "csv-worker-upload"
        else f"source-{idempotency_key}.{source_suffix}"
    )
    object_key = f"uploads/{environment.namespace.id}/{source_name}"
    source_path = storage_root / object_key
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(content)
    now = timezone.now()
    upload = LargeFileUploadSession.objects.create(
        organization=environment.organization,
        workspace=environment.workspace,
        registry_namespace=environment.namespace,
        submitted_by=environment.user,
        original_filename=f"generic observations.{source_suffix}",
        normalized_filename=f"generic observations.{source_suffix}",
        resolved_file_format=source_format,
        declared_content_type=content_type,
        detected_content_type=content_type,
        declared_size_bytes=len(content),
        actual_size_bytes=len(content),
        storage_provider="local",
        storage_object_key=object_key,
        checksum_verification_status="NOT_PROVIDED",
        status=LargeFileUploadStatus.COMPLETED,
        idempotency_key=idempotency_key,
        request_fingerprint="c" * 64,
        upload_authorization_expires_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(hours=1),
        uploaded_at=now,
        completed_at=now,
    )
    RegistrationSubmissionService().submit(
        user=environment.user,
        organization=environment.organization,
        upload_id=upload.id,
        visibility_scope=visibility_scope,
    )
    return (
        upload,
        DatasetRegistrationJob.objects.get(registration_submission__upload_session=upload),
        source_path,
    )


def _create_staged_publication(
    *,
    environment: _Environment,
    upload: LargeFileUploadSession,
) -> StagedResourcePublication:
    """Create the completed publication linkage required for CSV output assignment tests."""
    now = timezone.now()
    intake = StagedResourceIntake.objects.create(
        submitted_by=environment.user,
        original_filename=upload.original_filename,
        normalized_filename=upload.normalized_filename,
        resolved_file_format=upload.resolved_file_format,
        declared_content_type=upload.declared_content_type,
        declared_size_bytes=upload.declared_size_bytes,
        storage_provider=upload.storage_provider,
        storage_object_key=f"intakes/{upload.id}/source.csv",
        status=StagedResourceIntakeStatus.PUBLISHED,
        idempotency_key=f"intake-{upload.id}",
        request_fingerprint="i" * 64,
        upload_authorization_expires_at=now,
    )
    return StagedResourcePublication.objects.create(
        intake=intake,
        registry_namespace=environment.namespace,
        organization=environment.organization,
        workspace=environment.workspace,
        visibility_scope="WORKSPACE",
        published_by=environment.user,
        upload_session=upload,
        registration_submission=upload.registration_submission,
    )


def _runner(temp_directory: Path) -> DatasetRegistrationJobWorkerRunner:
    job_service = DatasetRegistrationJobService()
    return DatasetRegistrationJobWorkerRunner(
        job_service=job_service,
        execution_service=DatasetRegistrationJobExecutionService(
            job_service=job_service,
            materialization_service=CsvUploadMaterializationService(
                temp_directory=temp_directory,
            ),
        ),
        batch_size=1,
        stale_running_seconds=0,
    )


def test_csv_worker_registers_generic_dataset_without_persisting_temp_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage_root = tmp_path / "storage"
    temp_directory = tmp_path / "temporary"
    monkeypatch.setattr(
        settings, "RESOURCE_REGISTRY_LOCAL_STORAGE_DIR", str(storage_root), raising=False
    )
    environment = _environment()
    upload, job, _source_path = _create_job(environment, storage_root=storage_root)
    job.request_payload_json = {
        "path": "/attacker/file.csv",
        "blob_url": "https://foreign.invalid/object.csv?sig=secret",
        "object_key": "foreign/object.csv",
        "provider": "foreign",
        "container": "foreign",
        "tenant_id": "attacker",
        "format": "XLSX",
    }
    job.save(update_fields=["request_payload_json"])

    assert _runner(temp_directory).run_once() == 1

    job.refresh_from_db()
    assert job.status == DatasetRegistrationJobStatus.COMPLETED, (
        job.error_code,
        job.error_message,
    )
    assert Dataset.objects.filter(tenant_id=environment.namespace.id).count() == 1
    dataset = Dataset.objects.get(tenant_id=environment.namespace.id)
    assert dataset.organization_id == environment.organization.id
    assert dataset.workspace_id == environment.workspace.id
    assert dataset.visibility_scope == "WORKSPACE"
    version = DatasetVersion.objects.get(dataset=dataset)
    assert version.status == "READY"
    assert version.source_uri.startswith("local://")
    assert "artifact-read-" not in version.source_uri
    artifacts = list(DatasetArtifact.objects.all())
    assert len(artifacts) >= _EXPECTED_ARTIFACT_COUNT
    assert any(artifact.uri.endswith("source.csv") for artifact in artifacts)
    assert any(artifact.format == "PARQUET" for artifact in artifacts)
    assert all("artifact-read-" not in str(artifact.metadata_json) for artifact in artifacts)
    assert all("sig=secret" not in str(artifact.metadata_json) for artifact in artifacts)
    assert isinstance(job.result_payload_json, dict)
    assert job.result_payload_json["dataset_id"] == str(dataset.id)
    assert upload.storage_object_key in version.source_uri
    assert not temp_directory.exists() or tuple(temp_directory.iterdir()) == ()


def test_json_worker_registers_a_canonical_dataset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Register a JSON upload through the trusted worker and persist canonical Parquet."""
    storage_root = tmp_path / "storage"
    temp_directory = tmp_path / "temporary"
    monkeypatch.setattr(
        settings, "RESOURCE_REGISTRY_LOCAL_STORAGE_DIR", str(storage_root), raising=False
    )
    environment = _environment()
    _upload, job, _source_path = _create_job(
        environment,
        storage_root=storage_root,
        content=b'[{"identifier":"one","payload":{"customer":{"id":"a"}}}]',
        source_format="JSON",
        content_type="application/json",
        idempotency_key="json-worker-upload",
    )

    assert _runner(temp_directory).run_once() == 1

    job.refresh_from_db()
    assert job.status == DatasetRegistrationJobStatus.COMPLETED, (
        job.error_code,
        job.error_message,
    )
    assert DatasetVersion.objects.get().status == "READY"
    assert DatasetArtifact.objects.filter(format="PARQUET").exists()
    assert not temp_directory.exists() or tuple(temp_directory.iterdir()) == ()


def test_csv_worker_persists_organization_global_dataset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Persist an admitted Organization-global Dataset without a Workspace owner."""
    storage_root = tmp_path / "storage"
    temp_directory = tmp_path / "temporary"
    monkeypatch.setattr(
        settings, "RESOURCE_REGISTRY_LOCAL_STORAGE_DIR", str(storage_root), raising=False
    )
    environment = _environment()
    _upload, _job, _source_path = _create_job(
        environment,
        storage_root=storage_root,
        visibility_scope="ORGANIZATION",
    )

    assert _runner(temp_directory).run_once() == 1

    dataset = Dataset.objects.get()
    assert dataset.organization_id == environment.organization.id
    assert dataset.workspace_id is None
    assert dataset.visibility_scope == "ORGANIZATION"


def test_csv_worker_versions_datasets_and_records_publication_assignments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Create, version, and deduplicate a CSV Dataset published through staged intakes."""
    storage_root = tmp_path / "storage"
    temp_directory = tmp_path / "temporary"
    monkeypatch.setattr(
        settings, "RESOURCE_REGISTRY_LOCAL_STORAGE_DIR", str(storage_root), raising=False
    )
    environment = _environment()
    first_upload, _first_job, _source_path = _create_job(
        environment,
        storage_root=storage_root,
        content=b"name,value\nalpha,1\n",
        idempotency_key="csv-version-001",
    )
    first_publication = _create_staged_publication(
        environment=environment,
        upload=first_upload,
    )

    assert _runner(temp_directory).run_once() == 1

    dataset = Dataset.objects.get()
    first_version = DatasetVersion.objects.get(dataset=dataset)
    assert dataset.slug == "generic-observations-csv"
    assert dataset.name == "generic observations.csv"
    assert first_version.version_number == 1
    assert first_publication.dataset_assignments.get().dataset_version_id == first_version.id

    second_upload, _second_job, _source_path = _create_job(
        environment,
        storage_root=storage_root,
        content=b"name,value\nalpha,2\n",
        idempotency_key="csv-version-002",
    )
    second_publication = _create_staged_publication(
        environment=environment,
        upload=second_upload,
    )

    assert _runner(temp_directory).run_once() == 1

    versions = list(DatasetVersion.objects.filter(dataset=dataset).order_by("version_number"))
    assert [version.version_number for version in versions] == [1, 2]
    assert second_publication.dataset_assignments.get().dataset_version_id == versions[1].id

    third_upload, third_job, _source_path = _create_job(
        environment,
        storage_root=storage_root,
        content=b"name,value\nalpha,2\n",
        idempotency_key="csv-version-003",
    )
    third_publication = _create_staged_publication(
        environment=environment,
        upload=third_upload,
    )

    assert _runner(temp_directory).run_once() == 1

    third_job.refresh_from_db()
    assert DatasetVersion.objects.filter(dataset=dataset).count() == 2
    assert third_job.result_payload_json["dataset_version_id"] == str(versions[1].id)
    assert third_publication.dataset_assignments.get().dataset_version_id == versions[1].id


def test_csv_worker_reuses_matching_legacy_dataset_slug(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep versioning an existing upload dataset whose slug omitted the extension."""
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(settings, "RESOURCE_REGISTRY_LOCAL_STORAGE_DIR", str(storage_root))
    environment = _environment()
    _create_job(
        environment,
        storage_root=storage_root,
        content=b"name,value\nalpha,1\n",
        idempotency_key="legacy-csv-001",
    )
    assert _runner(tmp_path / "temporary").run_once() == 1
    dataset = Dataset.objects.get()
    dataset.slug = "generic-observations"
    dataset.save(update_fields=["slug"])

    _create_job(
        environment,
        storage_root=storage_root,
        content=b"name,value\nalpha,2\n",
        idempotency_key="legacy-csv-002",
    )
    assert _runner(tmp_path / "temporary").run_once() == 1

    assert Dataset.objects.count() == 1
    assert list(
        DatasetVersion.objects.filter(dataset=dataset)
        .order_by("version_number")
        .values_list("version_number", flat=True)
    ) == [1, 2]


@pytest.mark.parametrize(
    ("mutation", "expected_code", "expected_claims"),
    [
        ("xlsx", "csv_registration_provenance_invalid", 0),
        ("not_completed", "csv_registration_provenance_invalid", 0),
        ("organization", "csv_registration_provenance_invalid", 0),
        ("workspace", "csv_registration_provenance_invalid", 0),
        ("namespace", "csv_registration_provenance_invalid", 0),
        ("tenant", "csv_registration_provenance_invalid", 0),
        ("size", "csv_storage_size_mismatch", 1),
        ("etag", "csv_storage_etag_mismatch", 1),
        ("provider", "csv_registration_provenance_invalid", 0),
    ],
)
def test_csv_worker_fails_closed_for_invalid_provenance_or_integrity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    expected_code: str,
    expected_claims: int,
) -> None:
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(
        settings, "RESOURCE_REGISTRY_LOCAL_STORAGE_DIR", str(storage_root), raising=False
    )
    environment = _environment()
    upload, job, _source_path = _create_job(environment, storage_root=storage_root)
    if mutation == "xlsx":
        upload.resolved_file_format = "XLSX"
        upload.save(update_fields=["resolved_file_format"])
    elif mutation == "not_completed":
        upload.status = LargeFileUploadStatus.FAILED
        upload.save(update_fields=["status"])
    elif mutation == "organization":
        foreign_organization = Organization.objects.create(
            name="Foreign CSV Worker Org",
            slug="foreign-csv-worker-org",
            last_modified_by=environment.user,
        )
        upload.organization = foreign_organization
        upload.save(update_fields=["organization"])
    elif mutation == "workspace":
        foreign_workspace = Workspace.objects.create(
            organization=environment.organization,
            name="Foreign CSV Worker Workspace",
            slug="foreign-csv-worker-workspace",
            last_modified_by=environment.user,
        )
        upload.workspace = foreign_workspace
        upload.save(update_fields=["workspace"])
    elif mutation == "namespace":
        foreign_workspace = Workspace.objects.create(
            organization=environment.organization,
            name="Namespace CSV Worker Workspace",
            slug="namespace-csv-worker-workspace",
            last_modified_by=environment.user,
        )
        foreign_namespace = RegistryNamespace.objects.create(
            id=UUID("99999999-9999-4999-8999-999999999999"),
            workspace=foreign_workspace,
        )
        upload.registry_namespace = foreign_namespace
        upload.save(update_fields=["registry_namespace"])
    elif mutation == "tenant":
        job.tenant_id = UUID("88888888-8888-4888-8888-888888888888")
        job.save(update_fields=["tenant_id"])
    elif mutation == "size":
        upload.actual_size_bytes = int(upload.actual_size_bytes or 0) + 1
        upload.save(update_fields=["actual_size_bytes"])
    elif mutation == "etag":
        upload.storage_etag = "wrong-etag"
        upload.save(update_fields=["storage_etag"])
    else:
        upload.storage_provider = "unsupported"
        upload.save(update_fields=["storage_provider"])

    assert _runner(tmp_path / "temporary").run_once() == expected_claims

    job.refresh_from_db()
    assert job.status == DatasetRegistrationJobStatus.FAILED
    assert job.error_code == expected_code
    assert Dataset.objects.count() == 0


@pytest.mark.parametrize(
    "content",
    [b"", b"PK\x03\x04not-a-csv", b"\x00\x00\x00\x01\x00\x00", b"header\n\xff"],
)
def test_invalid_csv_content_never_creates_dataset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    content: bytes,
) -> None:
    if not content:
        content = b"\n"
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(
        settings, "RESOURCE_REGISTRY_LOCAL_STORAGE_DIR", str(storage_root), raising=False
    )
    environment = _environment()
    _upload, job, _source_path = _create_job(
        environment,
        storage_root=storage_root,
        content=content,
    )

    temp_directory = tmp_path / "temporary"
    _runner(temp_directory).run_once()

    job.refresh_from_db()
    assert job.status == DatasetRegistrationJobStatus.FAILED
    assert Dataset.objects.count() == 0
    assert not temp_directory.exists() or tuple(temp_directory.iterdir()) == ()


def test_header_only_csv_preserves_existing_zero_row_registration_behavior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(
        settings, "RESOURCE_REGISTRY_LOCAL_STORAGE_DIR", str(storage_root), raising=False
    )
    environment = _environment()
    _upload, job, _source_path = _create_job(
        environment,
        storage_root=storage_root,
        content=b"generic_key,generic_value\n",
    )

    assert _runner(tmp_path / "temporary").run_once() == 1

    job.refresh_from_db()
    assert job.status == DatasetRegistrationJobStatus.COMPLETED
    assert isinstance(job.result_payload_json, dict)
    assert job.result_payload_json["row_count"] == 0


def test_csv_artifact_failure_rolls_back_ready_dataset_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(
        settings, "RESOURCE_REGISTRY_LOCAL_STORAGE_DIR", str(storage_root), raising=False
    )
    environment = _environment()
    _upload, job, _source_path = _create_job(environment, storage_root=storage_root)

    def reject_artifact(*args: object, **kwargs: object) -> object:
        _ = args, kwargs
        raise RuntimeError("artifact persistence unavailable")

    monkeypatch.setattr(
        ArtifactLifecycleService,
        "register_uploaded_artifact",
        reject_artifact,
    )

    assert _runner(tmp_path / "temporary").run_once() == 1

    job.refresh_from_db()
    assert job.status == DatasetRegistrationJobStatus.FAILED
    assert job.error_code == "csv_registration_error"
    assert Dataset.objects.count() == 0
    assert DatasetVersion.objects.count() == 0
    assert DatasetArtifact.objects.count() == 0


def test_failed_canonical_artifact_persistence_removes_uploaded_parquet(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(
        settings, "RESOURCE_REGISTRY_LOCAL_STORAGE_DIR", str(storage_root), raising=False
    )
    environment = _environment()
    _upload, job, source_path = _create_job(environment, storage_root=storage_root)
    original_attach = DatasetRegistryService.attach_artifact

    def reject_canonical_artifact(
        service: DatasetRegistryService,
        **kwargs: Any,
    ) -> DatasetArtifact:
        if kwargs.get("artifact_role") == "SNAPSHOT":
            raise RuntimeError("canonical artifact persistence unavailable")
        return original_attach(service, **kwargs)

    monkeypatch.setattr(DatasetRegistryService, "attach_artifact", reject_canonical_artifact)

    assert _runner(tmp_path / "temporary").run_once() == 1

    job.refresh_from_db()
    assert job.status == DatasetRegistrationJobStatus.FAILED
    assert source_path.exists()
    assert list(storage_root.rglob("dataset.parquet")) == []
    assert Dataset.objects.count() == 0
    assert DatasetVersion.objects.count() == 0
    assert DatasetArtifact.objects.count() == 0


def test_missing_object_fails_without_payload_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(
        settings, "RESOURCE_REGISTRY_LOCAL_STORAGE_DIR", str(storage_root), raising=False
    )
    environment = _environment()
    _upload, job, source_path = _create_job(environment, storage_root=storage_root)
    source_path.unlink()
    job.request_payload_json = {"path": str(tmp_path / "attacker.csv")}
    job.save(update_fields=["request_payload_json"])

    assert _runner(tmp_path / "temporary").run_once() == 1

    job.refresh_from_db()
    assert job.status == DatasetRegistrationJobStatus.FAILED
    assert job.error_code == "csv_upload_object_not_found"
    assert Dataset.objects.count() == 0


class _TimeoutProvider:
    PROVIDER_NAME = "fake"

    def __init__(self, size_bytes: int) -> None:
        self._size_bytes = size_bytes

    def exists(self, uri_or_path: str) -> bool:
        return True

    def head(self, uri_or_path: str) -> StorageObjectMetadata:
        return StorageObjectMetadata(
            uri="fake://trusted/source.csv",
            provider="fake",
            size_bytes=self._size_bytes,
            content_type="text/csv",
        )

    def put(self, local_path: str, destination_path: str) -> StorageObjectMetadata:
        raise AssertionError("CSV materialization must not upload.")

    def get_uri(self, destination_path: str) -> str:
        return "fake://trusted/source.csv"

    def delete(self, uri_or_path: str) -> None:
        return None

    def download_to_file(self, uri_or_path: str, local_path: str) -> StorageObjectMetadata:
        raise TimeoutError("credential-like provider detail")


class _StorageFactory:
    def __init__(self, provider: StorageProvider) -> None:
        self._provider = provider

    def create_provider(
        self,
        provider_key: str | None = None,
        *,
        local_base_directory: str | Path | None = None,
    ) -> StorageProvider:
        _ = provider_key, local_base_directory
        return self._provider


def test_csv_provider_timeout_retries_only_before_registration(tmp_path: Path) -> None:
    environment = _environment()
    upload, job, _source_path = _create_job(environment, storage_root=tmp_path / "storage")
    job_service = DatasetRegistrationJobService(max_retries=1)
    assert job_service.activate_submitted_jobs(batch_size=1) == 1
    materializer = CsvUploadMaterializationService(
        storage_factory=cast(
            DataRegistryStorageFactory,
            _StorageFactory(
                cast(StorageProvider, _TimeoutProvider(int(upload.actual_size_bytes or 0)))
            ),
        ),
        temp_directory=tmp_path / "temporary",
    )
    runner = DatasetRegistrationJobWorkerRunner(
        job_service=job_service,
        execution_service=DatasetRegistrationJobExecutionService(
            job_service=job_service,
            materialization_service=materializer,
        ),
        batch_size=1,
        stale_running_seconds=0,
    )

    assert runner.run_once() == 1
    job.refresh_from_db()
    assert job.status == DatasetRegistrationJobStatus.QUEUED
    assert job.retry_count == 1
    assert job.error_code == "csv_download_failed"
    assert "credential-like" not in str(job.error_message)

    assert runner.run_once() == 1
    job.refresh_from_db()
    assert job.status == DatasetRegistrationJobStatus.FAILED
    assert job.retry_count == 1


def test_csv_duplicate_claim_and_pre_execution_cancellation(
    tmp_path: Path,
) -> None:
    environment = _environment()
    _upload, job, _source_path = _create_job(environment, storage_root=tmp_path / "storage")
    service = DatasetRegistrationJobService()
    assert service.activate_submitted_jobs(batch_size=1) == 1
    first_claim = service.claim_queued_jobs(batch_size=1)
    second_claim = service.claim_queued_jobs(batch_size=1)
    assert [claimed.id for claimed in first_claim] == [job.id]
    assert second_claim == []

    job.status = DatasetRegistrationJobStatus.QUEUED
    job.save(update_fields=["status"])
    service.request_cancellation(
        tenant_id=str(job.tenant_id),
        registration_job_id=str(job.id),
    )
    assert service.claim_queued_jobs(batch_size=1) == []
    job.refresh_from_db()
    assert job.status == DatasetRegistrationJobStatus.CANCELLED


def test_concurrent_csv_workers_claim_job_once(tmp_path: Path) -> None:
    environment = _environment()
    _upload, job, _source_path = _create_job(environment, storage_root=tmp_path / "storage")
    service = DatasetRegistrationJobService()
    assert service.activate_submitted_jobs(batch_size=1) == 1

    def claim_one_job(worker_index: int) -> list[UUID]:
        _ = worker_index
        close_old_connections()
        try:
            return [claimed.id for claimed in service.claim_queued_jobs(batch_size=1)]
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        claim_results = list(executor.map(claim_one_job, range(2)))

    assert sorted(len(result) for result in claim_results) == [0, 1]
    assert {identifier for result in claim_results for identifier in result} == {job.id}


def test_cancellation_after_materialization_prevents_registration_and_cleans_temp_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage_root = tmp_path / "storage"
    temp_directory = tmp_path / "temporary"
    monkeypatch.setattr(
        settings, "RESOURCE_REGISTRY_LOCAL_STORAGE_DIR", str(storage_root), raising=False
    )
    environment = _environment()
    _upload, _job, _source_path = _create_job(environment, storage_root=storage_root)
    job_service = DatasetRegistrationJobService()
    assert job_service.activate_submitted_jobs(batch_size=1) == 1
    claimed_job = job_service.claim_queued_jobs(batch_size=1)[0]
    materialization_service = CsvUploadMaterializationService(temp_directory=temp_directory)
    original_materialize = materialization_service.materialize

    def materialize_and_request_cancellation(
        *, job: DatasetRegistrationJob
    ) -> MaterializedCsvUpload:
        materialized_upload = original_materialize(job=job)
        job_service.request_cancellation(
            tenant_id=str(job.tenant_id),
            registration_job_id=str(job.id),
        )
        return materialized_upload

    monkeypatch.setattr(
        materialization_service,
        "materialize",
        materialize_and_request_cancellation,
    )

    executed_job = DatasetRegistrationJobExecutionService(
        job_service=job_service,
        materialization_service=materialization_service,
    ).execute_job(
        tenant_id=str(claimed_job.tenant_id),
        registration_job_id=str(claimed_job.id),
    )

    assert executed_job.status == DatasetRegistrationJobStatus.CANCELLED
    assert Dataset.objects.count() == 0
    assert not temp_directory.exists() or tuple(temp_directory.iterdir()) == ()


def test_csv_output_link_failure_rolls_back_registered_dataset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rollback CSV registration when its staged-publication output link cannot persist."""
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(
        settings, "RESOURCE_REGISTRY_LOCAL_STORAGE_DIR", str(storage_root), raising=False
    )
    environment = _environment()
    _upload, _job, _source_path = _create_job(environment, storage_root=storage_root)
    job_service = DatasetRegistrationJobService()
    assert job_service.activate_submitted_jobs(batch_size=1) == 1
    claimed_job = job_service.claim_queued_jobs(batch_size=1)[0]
    removal_service = Mock()
    removal_service.record_job_outputs.side_effect = RuntimeError("output link failed")

    executed_job = DatasetRegistrationJobExecutionService(
        job_service=job_service,
        materialization_service=CsvUploadMaterializationService(
            temp_directory=tmp_path / "temporary"
        ),
        destination_assignment_removal_service=removal_service,
    ).execute_job(
        tenant_id=str(claimed_job.tenant_id),
        registration_job_id=str(claimed_job.id),
    )

    assert executed_job.status == DatasetRegistrationJobStatus.FAILED
    assert executed_job.error_code == "csv_registration_error"
    assert Dataset.objects.filter(tenant_id=environment.namespace.id).exists() is False
    assert (
        DatasetVersion.objects.filter(dataset__tenant_id=environment.namespace.id).exists() is False
    )


def test_successful_registration_wins_concurrent_cancellation_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(
        settings, "RESOURCE_REGISTRY_LOCAL_STORAGE_DIR", str(storage_root), raising=False
    )
    environment = _environment()
    _upload, job, _source_path = _create_job(environment, storage_root=storage_root)
    job_service = DatasetRegistrationJobService()
    registration_facade = DatasetRegistrationFacade()
    original_register = registration_facade.register

    def register_and_request_cancellation(
        command: RegisterDatasetCommand,
        **kwargs: Any,
    ) -> RegisterDatasetResult:
        result = original_register(command, **kwargs)
        job_service.request_cancellation(
            tenant_id=str(job.tenant_id),
            registration_job_id=str(job.id),
        )
        return result

    monkeypatch.setattr(registration_facade, "register", register_and_request_cancellation)
    destination_assignment_removal_service = Mock()
    runner = DatasetRegistrationJobWorkerRunner(
        job_service=job_service,
        execution_service=DatasetRegistrationJobExecutionService(
            job_service=job_service,
            registration_facade=registration_facade,
            materialization_service=CsvUploadMaterializationService(
                temp_directory=tmp_path / "temporary"
            ),
        ),
        batch_size=1,
        stale_running_seconds=0,
        destination_assignment_removal_service=destination_assignment_removal_service,
    )

    assert runner.run_once() == 1

    job.refresh_from_db()
    assert job.status == DatasetRegistrationJobStatus.COMPLETED
    assert job.cancellation_requested_at is not None
    assert DatasetVersion.objects.get().status == "READY"
    detachment_finalizer = (
        destination_assignment_removal_service.finalize_detachment_for_dataset_registration_job
    )
    detachment_finalizer.assert_called_once_with(registration_job_id=job.id)


@pytest.mark.parametrize(
    "status",
    [
        DatasetRegistrationJobStatus.SUBMITTED,
        DatasetRegistrationJobStatus.QUEUED,
        DatasetRegistrationJobStatus.CANCELLED,
        DatasetRegistrationJobStatus.COMPLETED,
        DatasetRegistrationJobStatus.FAILED,
    ],
)
def test_execution_service_rejects_jobs_that_were_not_claimed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
) -> None:
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(
        settings, "RESOURCE_REGISTRY_LOCAL_STORAGE_DIR", str(storage_root), raising=False
    )
    environment = _environment()
    _upload, job, _source_path = _create_job(environment, storage_root=storage_root)
    job.status = status
    job.save(update_fields=["status"])

    executed_job = DatasetRegistrationJobExecutionService(
        materialization_service=CsvUploadMaterializationService(
            temp_directory=tmp_path / "temporary"
        ),
    ).execute_job(
        tenant_id=str(job.tenant_id),
        registration_job_id=str(job.id),
    )

    if status in {
        DatasetRegistrationJobStatus.CANCELLED,
        DatasetRegistrationJobStatus.COMPLETED,
        DatasetRegistrationJobStatus.FAILED,
    }:
        assert executed_job.status == status
    else:
        assert executed_job.status == DatasetRegistrationJobStatus.FAILED
        assert executed_job.error_code == "csv_job_not_claimed"
    assert Dataset.objects.count() == 0
    assert DatasetVersion.objects.count() == 0


def test_csv_job_failure_details_are_sanitized_and_bounded(tmp_path: Path) -> None:
    environment = _environment()
    _upload, job, _source_path = _create_job(environment, storage_root=tmp_path / "storage")
    job.status = DatasetRegistrationJobStatus.RUNNING
    job.save(update_fields=["status"])

    failed_job = DatasetRegistrationJobService().mark_failed(
        job=job,
        error_code="x" * 150,
        error_message="provider secret " + ("detail" * 100),
    )

    assert failed_job.status == DatasetRegistrationJobStatus.FAILED
    assert failed_job.error_code == "x" * 100
    assert failed_job.error_message == "[REDACTED]"
