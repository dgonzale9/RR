"""Exercise JSONL and Parquet from staged upload through publication and authorized SQL."""

from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pyarrow as arrow  # type: ignore[import-untyped]  # PyArrow has no bundled stubs.
import pytest
from django.conf import settings
from pyarrow import parquet

from resource_registry.intake.services import StagedResourceIntakeService
from resource_registry.models import (
    Dataset,
    DatasetArtifact,
    DatasetRegistrationJob,
    DatasetRegistrationJobStatus,
    StagedResourceInspectionJob,
    StagedResourceIntake,
    StagedResourceIntakeStatus,
)
from resource_registry.read.exceptions import DatasetNotFoundError
from resource_registry.read_auth.context import ReadAuthorizationContext
from resource_registry.services.dataset_query_executor import DatasetQueryExecutor
from resource_registry.storage.base import StorageProvider
from resource_registry.storage.factory import DataRegistryStorageFactory
from resource_registry.storage.local import LocalStorageProvider
from resource_registry.tests.test_csv_registration_worker_execution import (
    _create_job,
    _environment,
    _runner,
)
from resource_registry.uploads.exceptions import UploadApplicationError
from resource_registry.uploads.local_provider import LocalLargeFileUploadProvider

pytestmark = pytest.mark.django_db(transaction=True)


class _AzureStorageDouble(LocalStorageProvider):
    """Exercise Azure provider identity and URI routing with local test bytes."""

    URI_SCHEME = "azure://"
    PROVIDER_NAME = "azure_blob"


def _build_source_content(extension: str) -> bytes:
    """Build equivalent source data for the supported formats."""
    if extension == "parquet":
        output = arrow.BufferOutputStream()
        parquet.write_table(
            arrow.table({"identifier": ["one"], "payload": [{"customer": {"id": "a"}}]}),
            output,
        )
        return bytes(output.getvalue().to_pybytes())
    return b'{"identifier":"one","payload":{"customer":{"id":"a"}}}\n'


@pytest.mark.parametrize("storage_provider", ["local", "azure_blob"])
@pytest.mark.parametrize("extension", ["jsonl", "ndjson", "parquet"])
def test_staged_dataset_format_can_be_published_and_queried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extension: str, storage_provider: str
) -> None:
    """Preserve identity and provenance through staging, registration, and tenant-scoped SQL."""
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(settings, "RESOURCE_REGISTRY_LOCAL_STORAGE_DIR", str(storage_root))
    environment = _environment()
    environment.user.is_superuser = True
    environment.user.save(update_fields=["is_superuser"])
    provider = LocalLargeFileUploadProvider()
    if storage_provider == "azure_blob":
        azure_storage = _AzureStorageDouble(storage_root)
        original_create_provider = DataRegistryStorageFactory.create_provider

        def create_test_provider(
            factory: DataRegistryStorageFactory,
            provider_key: str | None = None,
            *,
            local_base_directory: str | Path | None = None,
        ) -> StorageProvider:
            if provider_key == "azure_blob":
                return azure_storage
            return original_create_provider(
                factory, provider_key, local_base_directory=local_base_directory
            )

        monkeypatch.setattr(DataRegistryStorageFactory, "create_provider", create_test_provider)
        provider.provider_name = "azure_blob"
        provider._provider = azure_storage
    service = StagedResourceIntakeService(provider=provider)
    content = _build_source_content(extension)
    content_type = (
        "application/vnd.apache.parquet" if extension == "parquet" else "application/x-ndjson"
    )
    created = service.create_intake(
        user=environment.user,
        filename=f"observations.{extension}",
        content_type=content_type,
        size_bytes=len(content),
        checksum_algorithm="sha256",
        checksum=sha256(content).hexdigest(),
        idempotency_key=f"formats-{extension}",
    )
    intake = StagedResourceIntake.objects.get(pk=created.intake.intake_id)
    source = storage_root / intake.storage_object_key
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(content)
    service.complete_intake(user=environment.user, intake_id=intake.id, expected_etag=None)
    assert service.run_inspection_jobs(batch_size=1) == 1
    intake.refresh_from_db()
    assert intake.status == StagedResourceIntakeStatus.READY_FOR_PUBLICATION
    assert intake.original_filename == f"observations.{extension}"
    assert intake.resolved_file_format == ("PARQUET" if extension == "parquet" else "JSONL")
    for _ in range(2):
        service.publish_intake(
            user=environment.user,
            organization=environment.organization,
            intake_id=intake.id,
            visibility_scope="WORKSPACE",
            workspace_id=environment.workspace.id,
        )
    assert DatasetRegistrationJob.objects.count() == 1
    temporary = tmp_path / "temporary"
    assert _runner(temporary).run_once() == 1
    job = DatasetRegistrationJob.objects.get()
    assert job.status == DatasetRegistrationJobStatus.COMPLETED, (job.error_code, job.error_message)
    dataset = Dataset.objects.get(tenant_id=environment.namespace.id)
    assert DatasetArtifact.objects.filter(
        dataset_version__dataset=dataset, format="PARQUET"
    ).exists()
    assert not temporary.exists() or not tuple(temporary.iterdir())
    executor = DatasetQueryExecutor(temp_root_directory=tmp_path / "query", temp_min_free_bytes=0)
    result = executor.execute_query(
        context=ReadAuthorizationContext(tenant_id=str(environment.namespace.id), roles=("admin",)),
        dataset_id=str(dataset.id),
        sql="SELECT json_extract_string(payload, '$.customer.id') AS customer_id FROM dataset LIMIT 10",
    )
    assert result.get("columns") == ["customer_id"], result
    assert result["rows"] == [["a"]]
    with pytest.raises(DatasetNotFoundError):
        executor.execute_query(
            context=ReadAuthorizationContext(tenant_id=str(uuid4()), roles=("admin",)),
            dataset_id=str(dataset.id),
            sql="SELECT identifier FROM dataset LIMIT 10",
        )


def test_same_stem_json_uploads_create_distinct_datasets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep JSON, JSONL, and NDJSON publications separate despite matching file stems."""
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(settings, "RESOURCE_REGISTRY_LOCAL_STORAGE_DIR", str(storage_root))
    environment = _environment()
    environment.user.is_superuser = True
    environment.user.save(update_fields=["is_superuser"])
    service = StagedResourceIntakeService(provider=LocalLargeFileUploadProvider())
    sources = {
        "json": b'[{"identifier":"one"}]',
        "jsonl": b'{"identifier":"one"}\n',
        "ndjson": b'{"identifier":"one"}\n',
    }
    intake_ids = set()
    dataset_ids = set()

    for extension, content in sources.items():
        created = service.create_intake(
            user=environment.user,
            filename=f"testing_sales_10000.{extension}",
            content_type=("application/json" if extension == "json" else "application/x-ndjson"),
            size_bytes=len(content),
            checksum_algorithm="sha256",
            checksum=sha256(content).hexdigest(),
            idempotency_key=f"same-stem-{extension}",
        )
        intake = StagedResourceIntake.objects.get(pk=created.intake.intake_id)
        intake_ids.add(intake.id)
        source_path = storage_root / intake.storage_object_key
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_bytes(content)
        service.complete_intake(user=environment.user, intake_id=intake.id, expected_etag=None)
        assert service.run_inspection_jobs(batch_size=1) == 1
        service.publish_intake(
            user=environment.user,
            organization=environment.organization,
            intake_id=intake.id,
            visibility_scope="WORKSPACE",
            workspace_id=environment.workspace.id,
        )
        assert _runner(tmp_path / "temporary").run_once() == 1
        job = DatasetRegistrationJob.objects.get(
            registration_submission__upload_session__original_filename=(
                f"testing_sales_10000.{extension}"
            )
        )
        assert job.status == DatasetRegistrationJobStatus.COMPLETED, (
            job.error_code,
            job.error_message,
        )
        result_payload = job.result_payload_json
        assert isinstance(result_payload, dict)
        dataset_id = result_payload["dataset_id"]
        assert isinstance(dataset_id, str)
        dataset_ids.add(dataset_id)
        dataset = Dataset.objects.get(pk=dataset_id)
        assert dataset.slug == f"testing_sales_10000-{extension}"
        assert dataset.name == f"testing_sales_10000.{extension}"

    assert len(intake_ids) == len(dataset_ids) == 3
    assert Dataset.objects.filter(tenant_id=environment.namespace.id).count() == 3


@pytest.mark.parametrize(
    ("extension", "content", "error_code"),
    [
        ("jsonl", b'{"a":1}\n{"a":NaN}', "JSONL_NON_FINITE_NUMBER"),
        ("parquet", b"PAR1invalid", "PARQUET_CONTENT_INVALID"),
    ],
)
def test_staged_invalid_dataset_fails_without_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    extension: str,
    content: bytes,
    error_code: str,
) -> None:
    """Persist terminal errors before publication without creating dataset records."""
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(settings, "RESOURCE_REGISTRY_LOCAL_STORAGE_DIR", str(storage_root))
    environment = _environment()
    environment.user.is_superuser = True
    environment.user.save(update_fields=["is_superuser"])
    service = StagedResourceIntakeService(provider=LocalLargeFileUploadProvider())
    created = service.create_intake(
        user=environment.user,
        filename=f"invalid.{extension}",
        content_type=(
            "application/octet-stream" if extension == "parquet" else "application/x-ndjson"
        ),
        size_bytes=len(content),
        checksum_algorithm=None,
        checksum=None,
        idempotency_key="invalid-dataset",
    )
    intake = StagedResourceIntake.objects.get(pk=created.intake.intake_id)
    source = storage_root / intake.storage_object_key
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(content)
    service.complete_intake(user=environment.user, intake_id=intake.id, expected_etag=None)
    assert service.run_inspection_jobs(batch_size=1) == 0
    intake.refresh_from_db()
    assert intake.status == StagedResourceIntakeStatus.FAILED
    assert intake.failure_code == error_code
    assert StagedResourceInspectionJob.objects.get(intake=intake).error_code == error_code
    assert service.run_inspection_jobs(batch_size=1) == 0
    assert not Dataset.objects.exists()
    with pytest.raises(UploadApplicationError):
        service.publish_intake(
            user=environment.user,
            organization=environment.organization,
            intake_id=intake.id,
            visibility_scope="WORKSPACE",
            workspace_id=environment.workspace.id,
        )


@pytest.mark.parametrize(
    ("source_format", "content", "content_type", "expected_error"),
    [
        ("JSONL", b'{"a":1}\n[]', "application/x-ndjson", "jsonl_record_must_be_object"),
        ("PARQUET", b"PAR1invalid", "application/octet-stream", "parquet_content_invalid"),
    ],
)
def test_worker_revalidates_invalid_upload_and_cleans_temporary_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    source_format: str,
    content: bytes,
    content_type: str,
    expected_error: str,
) -> None:
    """Reject malformed direct uploads even when staged inspection was not used."""
    storage_root = tmp_path / "storage"
    temporary = tmp_path / "temporary"
    monkeypatch.setattr(settings, "RESOURCE_REGISTRY_LOCAL_STORAGE_DIR", str(storage_root))
    _, job, _ = _create_job(
        _environment(),
        storage_root=storage_root,
        content=content,
        source_format=source_format,
        content_type=content_type,
    )
    assert _runner(temporary).run_once() == 1
    job.refresh_from_db()
    assert job.status == DatasetRegistrationJobStatus.FAILED
    assert job.error_code == expected_error
    assert not Dataset.objects.exists()
    assert not temporary.exists() or not tuple(temporary.iterdir())
