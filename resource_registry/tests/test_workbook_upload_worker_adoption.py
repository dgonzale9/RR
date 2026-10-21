"""Integration contracts for Workbook worker adoption of direct-upload provenance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from types import TracebackType
from typing import cast
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from django.conf import settings
from django.contrib.auth.models import Permission, User
from django.utils import timezone
from openpyxl import Workbook  # type: ignore[import-untyped]  # Third-party stubs unavailable.

from Organization.models import Organization, Workspace
from resource_registry.models import (
    Dataset,
    DatasetArtifact,
    DatasetVersion,
    LargeFileUploadSession,
    LargeFileUploadStatus,
    RegistryNamespace,
    WorkbookIngestionGroup,
    WorkbookIngestionJob,
    WorkbookIngestionJobStatus,
)
from resource_registry.registration_submissions.services import RegistrationSubmissionService
from resource_registry.services.registry.artifact_lifecycle_service import ArtifactLifecycleService
from resource_registry.services.workbook_ingestion_job_execution_service import (
    WorkbookIngestionJobExecutionService,
)
from resource_registry.services.workbook_ingestion_job_service import WorkbookIngestionJobService
from resource_registry.services.workbook_ingestion_job_worker_runner import (
    WorkbookIngestionJobWorkerRunner,
)
from resource_registry.services.workbook_upload_materialization_service import (
    MaterializedWorkbookUpload,
    WorkbookUploadExecutionError,
    WorkbookUploadMaterializationService,
)
from resource_registry.storage.base import StorageObjectMetadata, StorageProvider
from resource_registry.storage.factory import DataRegistryStorageFactory
from resource_registry.storage.read_adapters import TemporaryArtifact
from resource_registry.tests.workspace_permission_helpers import add_workspace_user

pytestmark = pytest.mark.django_db(transaction=True)

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_EXPECTED_DATASET_COUNT = 2


@dataclass(frozen=True)
class _Environment:
    organization: Organization
    workspace: Workspace
    namespace: RegistryNamespace
    user: User


def _environment() -> _Environment:
    user = User.objects.create_user(username="workbook-worker-user")
    organization = Organization.objects.create(
        name="Workbook Worker Org",
        slug="workbook-worker-org",
        last_modified_by=user,
    )
    workspace = Workspace.objects.create(
        organization=organization,
        name="Workbook Worker Workspace",
        slug="workbook-worker-workspace",
        last_modified_by=user,
    )
    add_workspace_user(workspace=workspace, user=user)
    namespace = RegistryNamespace.objects.create(
        id=UUID("44444444-4444-4444-8444-444444444444"),
        workspace=workspace,
    )
    organization.resource_registry_enabled = True
    organization.save(update_fields=["resource_registry_enabled"])
    workspace.resource_registry_enabled = True
    workspace.save(update_fields=["resource_registry_enabled"])
    user.user_permissions.add(Permission.objects.get(codename="submit_upload_registration"))
    return _Environment(organization, workspace, namespace, user)


def _write_workbook(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    first_sheet = workbook.active
    first_sheet.title = "Sales"
    first_sheet.append(["region", "amount"])
    first_sheet.append(["North", 10])
    second_sheet = workbook.create_sheet("Costs")
    second_sheet.append(["category", "amount"])
    second_sheet.append(["Hosting", 4])
    workbook.save(path)


def _create_submitted_job(
    environment: _Environment,
    *,
    storage_root: Path,
    write_valid_workbook: bool = True,
    storage_provider: str = "local",
) -> tuple[LargeFileUploadSession, WorkbookIngestionJob, Path]:
    object_key = f"uploads/{environment.namespace.id}/source.xlsx"
    source_path = storage_root / object_key
    source_path.parent.mkdir(parents=True, exist_ok=True)
    if write_valid_workbook:
        _write_workbook(source_path)
    else:
        source_path.write_bytes(b"not-a-valid-workbook")
    now = timezone.now()
    upload = LargeFileUploadSession.objects.create(
        organization=environment.organization,
        workspace=environment.workspace,
        registry_namespace=environment.namespace,
        submitted_by=environment.user,
        original_filename="financial model.xlsx",
        normalized_filename="financial model.xlsx",
        resolved_file_format="XLSX",
        declared_content_type=_XLSX_MIME,
        detected_content_type=_XLSX_MIME,
        declared_size_bytes=source_path.stat().st_size,
        actual_size_bytes=source_path.stat().st_size,
        storage_provider=storage_provider,
        storage_object_key=object_key,
        checksum_verification_status="NOT_PROVIDED",
        status=LargeFileUploadStatus.COMPLETED,
        idempotency_key=f"worker-{storage_provider}-{write_valid_workbook}",
        request_fingerprint="b" * 64,
        upload_authorization_expires_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(hours=1),
        uploaded_at=now,
        completed_at=now,
    )
    RegistrationSubmissionService().submit(
        user=environment.user,
        organization=environment.organization,
        upload_id=upload.id,
    )
    return upload, WorkbookIngestionJob.objects.get(), source_path


def _runner(*, temp_directory: Path) -> WorkbookIngestionJobWorkerRunner:
    job_service = WorkbookIngestionJobService()
    execution_service = WorkbookIngestionJobExecutionService(
        job_service=job_service,
        upload_materialization_service=WorkbookUploadMaterializationService(
            temp_directory=temp_directory,
        ),
    )
    return WorkbookIngestionJobWorkerRunner(
        job_service=job_service,
        execution_service=execution_service,
        batch_size=1,
        stale_running_seconds=0,
    )


def test_worker_executes_submitted_upload_and_persists_no_temp_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage_root = tmp_path / "storage"
    temp_directory = tmp_path / "temporary"
    monkeypatch.setattr(
        settings,
        "RESOURCE_REGISTRY_LOCAL_STORAGE_DIR",
        str(storage_root),
        raising=False,
    )
    environment = _environment()
    upload, job, _source_path = _create_submitted_job(
        environment,
        storage_root=storage_root,
    )
    job.request_payload_json = {
        "path": "/caller/controlled/evil.xlsx",
        "tenant_id": "caller-tenant",
    }
    job.save(update_fields=["request_payload_json"])

    assert _runner(temp_directory=temp_directory).run_once() == 1

    job.refresh_from_db()
    assert job.status == WorkbookIngestionJobStatus.COMPLETED, (
        job.error_code,
        job.error_message,
        job.result_payload_json,
    )
    assert Dataset.objects.count() == _EXPECTED_DATASET_COUNT
    assert DatasetVersion.objects.count() == _EXPECTED_DATASET_COUNT
    assert WorkbookIngestionGroup.objects.count() == 1
    assert DatasetArtifact.objects.count() >= _EXPECTED_DATASET_COUNT
    assert all(version.status == "READY" for version in DatasetVersion.objects.all())
    assert all(
        version.source_uri.startswith("local://")
        and "artifact-read-" not in version.source_uri
        and "/caller/controlled" not in version.source_uri
        for version in DatasetVersion.objects.all()
    )
    assert all(
        artifact.uri.startswith("local://") and "artifact-read-" not in artifact.uri
        for artifact in DatasetArtifact.objects.all()
    )
    assert all(
        "artifact-read-" not in str(artifact.metadata_json)
        and "/caller/controlled" not in str(artifact.metadata_json)
        for artifact in DatasetArtifact.objects.all()
    )
    group = WorkbookIngestionGroup.objects.get()
    assert job.workbook_group_id == group.id
    assert group.source_uri.startswith("local://")
    assert group.source_file_name == upload.normalized_filename
    assert not temp_directory.exists() or tuple(temp_directory.iterdir()) == ()


def test_corrupt_workbook_fails_and_removes_temporary_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage_root = tmp_path / "storage"
    temp_directory = tmp_path / "temporary"
    monkeypatch.setattr(
        settings,
        "RESOURCE_REGISTRY_LOCAL_STORAGE_DIR",
        str(storage_root),
        raising=False,
    )
    environment = _environment()
    _upload, job, _source_path = _create_submitted_job(
        environment,
        storage_root=storage_root,
        write_valid_workbook=False,
    )

    assert _runner(temp_directory=temp_directory).run_once() == 1

    job.refresh_from_db()
    assert job.status == WorkbookIngestionJobStatus.FAILED, (
        job.error_code,
        job.error_message,
    )
    assert job.error_code in {"unsupported_excel_workbook", "workbook_has_no_valid_sheets"}
    assert Dataset.objects.count() == 0
    assert not temp_directory.exists() or tuple(temp_directory.iterdir()) == ()


def test_missing_trusted_object_fails_without_using_job_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(
        settings,
        "RESOURCE_REGISTRY_LOCAL_STORAGE_DIR",
        str(storage_root),
        raising=False,
    )
    environment = _environment()
    _upload, job, source_path = _create_submitted_job(
        environment,
        storage_root=storage_root,
    )
    source_path.unlink()
    job.request_payload_json = {"path": str(tmp_path / "attacker.xlsx")}
    job.save(update_fields=["request_payload_json"])

    assert _runner(temp_directory=tmp_path / "temporary").run_once() == 1

    job.refresh_from_db()
    assert job.status == WorkbookIngestionJobStatus.FAILED
    assert job.error_code == "workbook_upload_object_not_found"
    assert Dataset.objects.count() == 0


class _FailingDownloadProvider:
    PROVIDER_NAME = "fake"

    def __init__(self, *, size_bytes: int) -> None:
        self._size_bytes = size_bytes

    def exists(self, uri_or_path: str) -> bool:
        return True

    def head(self, uri_or_path: str) -> StorageObjectMetadata:
        return StorageObjectMetadata(
            uri="fake://trusted/source.xlsx",
            provider="fake",
            size_bytes=self._size_bytes,
            content_type=_XLSX_MIME,
        )

    def put(self, local_path: str, destination_path: str) -> StorageObjectMetadata:
        raise AssertionError("Worker materialization must not upload.")

    def get_uri(self, destination_path: str) -> str:
        return "fake://trusted/source.xlsx"

    def delete(self, uri_or_path: str) -> None:
        return None

    def download_to_file(self, uri_or_path: str, local_path: str) -> StorageObjectMetadata:
        raise TimeoutError("provider timeout must be sanitized")


class _FakeStorageFactory:
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


def test_provider_timeout_schedules_bounded_retry(tmp_path: Path) -> None:
    storage_root = tmp_path / "storage"
    environment = _environment()
    upload, job, _source_path = _create_submitted_job(
        environment,
        storage_root=storage_root,
        storage_provider="fake",
    )
    job_service = WorkbookIngestionJobService(max_retries=1)
    materializer = WorkbookUploadMaterializationService(
        storage_factory=cast(
            DataRegistryStorageFactory,
            _FakeStorageFactory(
                cast(
                    StorageProvider,
                    _FailingDownloadProvider(size_bytes=int(upload.actual_size_bytes or 0)),
                )
            ),
        ),
        temp_directory=tmp_path / "temporary",
    )
    runner = WorkbookIngestionJobWorkerRunner(
        job_service=job_service,
        execution_service=WorkbookIngestionJobExecutionService(
            job_service=job_service,
            upload_materialization_service=materializer,
        ),
        batch_size=1,
        stale_running_seconds=0,
    )

    assert runner.run_once() == 1

    job.refresh_from_db()
    assert job.status == WorkbookIngestionJobStatus.QUEUED
    assert job.retry_count == 1
    assert job.error_code == "workbook_download_failed"
    assert "provider timeout" not in str(job.error_message)

    assert runner.run_once() == 1

    job.refresh_from_db()
    assert job.status == WorkbookIngestionJobStatus.FAILED
    assert job.retry_count == 1
    assert job.error_code == "workbook_download_failed"


def test_registration_pipeline_failure_cleans_materialized_workbook(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage_root = tmp_path / "storage"
    temp_directory = tmp_path / "temporary"
    monkeypatch.setattr(
        settings,
        "RESOURCE_REGISTRY_LOCAL_STORAGE_DIR",
        str(storage_root),
        raising=False,
    )
    environment = _environment()
    _upload, job, _source_path = _create_submitted_job(
        environment,
        storage_root=storage_root,
    )
    facade = MagicMock()
    facade.register_workbook.side_effect = RuntimeError("database detail must remain private")
    job_service = WorkbookIngestionJobService(max_retries=1)
    runner = WorkbookIngestionJobWorkerRunner(
        job_service=job_service,
        execution_service=WorkbookIngestionJobExecutionService(
            job_service=job_service,
            registration_facade=facade,
            upload_materialization_service=WorkbookUploadMaterializationService(
                temp_directory=temp_directory,
            ),
        ),
        batch_size=1,
        stale_running_seconds=0,
    )

    assert runner.run_once() == 1

    job.refresh_from_db()
    assert job.status == WorkbookIngestionJobStatus.FAILED
    assert job.error_code == "workbook_ingestion_error"
    assert "database detail" not in str(job.error_message)
    assert not temp_directory.exists() or tuple(temp_directory.iterdir()) == ()


def test_artifact_failure_rolls_back_incomplete_dataset_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage_root = tmp_path / "storage"
    temp_directory = tmp_path / "temporary"
    monkeypatch.setattr(
        settings,
        "RESOURCE_REGISTRY_LOCAL_STORAGE_DIR",
        str(storage_root),
        raising=False,
    )
    environment = _environment()
    _upload, job, _source_path = _create_submitted_job(
        environment,
        storage_root=storage_root,
    )

    def reject_artifact_registration(*args: object, **kwargs: object) -> object:
        _ = args, kwargs
        raise RuntimeError("artifact persistence unavailable")

    monkeypatch.setattr(
        ArtifactLifecycleService,
        "register_uploaded_artifact",
        reject_artifact_registration,
    )

    assert _runner(temp_directory=temp_directory).run_once() == 1

    job.refresh_from_db()
    assert job.status == WorkbookIngestionJobStatus.FAILED
    assert job.error_code == "workbook_ingestion_error"
    assert Dataset.objects.count() == 0
    assert DatasetVersion.objects.count() == 0
    assert DatasetArtifact.objects.count() == 0
    assert not temp_directory.exists() or tuple(temp_directory.iterdir()) == ()


def test_provenance_mismatch_fails_before_storage_download(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(
        settings,
        "RESOURCE_REGISTRY_LOCAL_STORAGE_DIR",
        str(storage_root),
        raising=False,
    )
    environment = _environment()
    _upload, job, _source_path = _create_submitted_job(
        environment,
        storage_root=storage_root,
    )
    job.tenant_id = UUID("66666666-6666-4666-8666-666666666666")
    job.save(update_fields=["tenant_id"])

    assert _runner(temp_directory=tmp_path / "temporary").run_once() == 0

    job.refresh_from_db()
    assert job.status == WorkbookIngestionJobStatus.FAILED
    assert job.error_code == "workbook_registration_provenance_invalid"
    assert Dataset.objects.count() == 0

    job.status = WorkbookIngestionJobStatus.QUEUED
    job.error_code = None
    job.error_message = None
    job.save(update_fields=["status", "error_code", "error_message"])
    WorkbookIngestionJobExecutionService(
        upload_materialization_service=WorkbookUploadMaterializationService(
            temp_directory=tmp_path / "temporary",
        ),
    ).execute_job(tenant_id=str(job.tenant_id), ingestion_job_id=str(job.id))

    job.refresh_from_db()
    assert job.status == WorkbookIngestionJobStatus.FAILED
    assert job.error_code == "workbook_upload_provenance_mismatch"
    assert Dataset.objects.count() == 0


def test_invalid_submitted_job_is_failed_before_claim() -> None:
    environment = _environment()
    job = WorkbookIngestionJob.objects.create(
        tenant_id=environment.namespace.id,
        status=WorkbookIngestionJobStatus.SUBMITTED,
        request_payload_json={"path": "/untrusted/path.xlsx"},
        created_by=environment.user,
    )
    service = WorkbookIngestionJobService()

    assert service.activate_submitted_jobs(batch_size=1) == 0
    assert service.claim_queued_jobs(batch_size=1) == []
    job.refresh_from_db()
    assert job.status == WorkbookIngestionJobStatus.FAILED
    assert job.error_code == "workbook_registration_provenance_invalid"


def test_csv_provenance_is_not_promoted_to_workbook_queue(
    tmp_path: Path,
) -> None:
    environment = _environment()
    upload, job, _source_path = _create_submitted_job(
        environment,
        storage_root=tmp_path / "storage",
    )
    submission = job.registration_submission
    assert submission is not None
    upload.resolved_file_format = "CSV"
    upload.save(update_fields=["resolved_file_format"])
    submission.job_type = "DATASET_REGISTRATION"
    submission.save(update_fields=["job_type"])
    service = WorkbookIngestionJobService()

    assert service.activate_submitted_jobs(batch_size=1) == 0
    assert service.claim_queued_jobs(batch_size=1) == []
    job.refresh_from_db()
    assert job.status == WorkbookIngestionJobStatus.FAILED
    assert job.error_code == "workbook_registration_provenance_invalid"


def test_non_completed_upload_is_not_promoted_to_workbook_queue(tmp_path: Path) -> None:
    environment = _environment()
    upload, job, _source_path = _create_submitted_job(
        environment,
        storage_root=tmp_path / "storage",
    )
    upload.status = LargeFileUploadStatus.FAILED
    upload.save(update_fields=["status"])
    service = WorkbookIngestionJobService()

    assert service.activate_submitted_jobs(batch_size=1) == 0
    assert service.claim_queued_jobs(batch_size=1) == []
    job.refresh_from_db()
    assert job.status == WorkbookIngestionJobStatus.FAILED
    assert job.error_code == "workbook_registration_provenance_invalid"


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("size", "workbook_storage_size_mismatch"),
        ("etag", "workbook_storage_etag_mismatch"),
        ("provider", "workbook_storage_resolution_failed"),
    ],
)
def test_storage_integrity_mismatch_fails_before_dataset_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    expected_code: str,
) -> None:
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(
        settings,
        "RESOURCE_REGISTRY_LOCAL_STORAGE_DIR",
        str(storage_root),
        raising=False,
    )
    environment = _environment()
    upload, job, _source_path = _create_submitted_job(
        environment,
        storage_root=storage_root,
    )
    if mutation == "size":
        upload.actual_size_bytes = int(upload.actual_size_bytes or 0) + 1
        upload.save(update_fields=["actual_size_bytes"])
    elif mutation == "etag":
        upload.storage_etag = "unexpected-etag"
        upload.save(update_fields=["storage_etag"])
    else:
        upload.storage_provider = "unsupported-provider"
        upload.save(update_fields=["storage_provider"])

    assert _runner(temp_directory=tmp_path / "temporary").run_once() == 1

    job.refresh_from_db()
    assert job.status == WorkbookIngestionJobStatus.FAILED
    assert job.error_code == expected_code
    assert Dataset.objects.count() == 0


def test_duplicate_worker_claim_executes_job_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(
        settings,
        "RESOURCE_REGISTRY_LOCAL_STORAGE_DIR",
        str(storage_root),
        raising=False,
    )
    environment = _environment()
    _upload, job, _source_path = _create_submitted_job(
        environment,
        storage_root=storage_root,
    )
    service = WorkbookIngestionJobService()
    assert service.activate_submitted_jobs(batch_size=1) == 1
    first_claim = service.claim_queued_jobs(batch_size=1)
    second_claim = service.claim_queued_jobs(batch_size=1)

    assert [claimed.id for claimed in first_claim] == [job.id]
    assert second_claim == []


def test_cleanup_failure_is_stable_and_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    temporary_path = tmp_path / "temporary.xlsx"
    temporary_path.write_bytes(b"content")
    materialized = MaterializedWorkbookUpload(
        upload_id=UUID("55555555-5555-4555-8555-555555555555"),
        tenant_id=UUID("44444444-4444-4444-8444-444444444444"),
        original_filename="source.xlsx",
        content_type=_XLSX_MIME,
        storage_provider="local",
        source_metadata=StorageObjectMetadata(
            uri="local://trusted/source.xlsx",
            provider="local",
        ),
        temporary_artifact=TemporaryArtifact(
            local_path=str(temporary_path),
            source_uri="local://trusted/source.xlsx",
        ),
    )

    def reject_unlink(_path: Path) -> None:
        raise OSError("cleanup detail")

    monkeypatch.setattr(Path, "unlink", reject_unlink)
    with pytest.raises(WorkbookUploadExecutionError) as caught:
        materialized.__exit__(
            cast(type[BaseException] | None, None), None, cast(TracebackType | None, None)
        )
    assert caught.value.code == "workbook_temporary_cleanup_failed"
    assert caught.value.retryable is False
    assert "cleanup detail" not in str(caught.value)
