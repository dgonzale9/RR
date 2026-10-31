from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# Registration now reads the persisted organization rollout policy.
pytestmark = pytest.mark.django_db
from openpyxl import Workbook

from resource_registry.application.commands import RegisterDatasetCommand
from resource_registry.application.exceptions import (
    DuplicateDatasetError,
    UnsupportedDatasetSourceError,
    ValidationError,
)
from resource_registry.application.idempotency import (
    DatasetRegistrationIdempotencyService,
    LocalFileHashService,
)
from resource_registry.application.validators import RegisterDatasetCommandValidator

CHUNK_SIZE = 64 * 1024


@pytest.fixture
def csv_dataset_path(tmp_path: Path) -> Path:
    dataset_path = tmp_path / "sample.csv"
    dataset_path.write_text("id,name\n1,Ada\n", encoding="utf-8")
    return dataset_path


@pytest.fixture
def xlsx_dataset_path(tmp_path: Path) -> Path:
    dataset_path = tmp_path / "sample.xlsx"
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Sheet1"
    worksheet.append(["id", "name"])
    worksheet.append([1, "Ada"])
    workbook.save(dataset_path)
    workbook.close()
    return dataset_path


def _build_command(path: str) -> RegisterDatasetCommand:
    return RegisterDatasetCommand(
        tenant_id="11111111-1111-1111-1111-111111111111",
        slug="sales-data",
        name="Sales Data",
        path=path,
        source_type="FILE_UPLOAD",
        artifact_role="PRIMARY",
        storage_provider="LOCAL",
        preview_limit=10,
    )


def test_missing_tenant_id_rejected(csv_dataset_path: Path) -> None:
    command = _build_command(str(csv_dataset_path))
    command = RegisterDatasetCommand(**{**command.__dict__, "tenant_id": ""})
    validator = RegisterDatasetCommandValidator()

    with pytest.raises(ValidationError):
        validator.validate(command)


def test_missing_slug_rejected(csv_dataset_path: Path) -> None:
    command = _build_command(str(csv_dataset_path))
    command = RegisterDatasetCommand(**{**command.__dict__, "slug": ""})
    validator = RegisterDatasetCommandValidator()

    with pytest.raises(ValidationError):
        validator.validate(command)


def test_missing_name_rejected(csv_dataset_path: Path) -> None:
    command = _build_command(str(csv_dataset_path))
    command = RegisterDatasetCommand(**{**command.__dict__, "name": ""})
    validator = RegisterDatasetCommandValidator()

    with pytest.raises(ValidationError):
        validator.validate(command)


def test_unsupported_source_type_rejected(csv_dataset_path: Path) -> None:
    command = _build_command(str(csv_dataset_path))
    command = RegisterDatasetCommand(**{**command.__dict__, "source_type": "UNSUPPORTED"})
    validator = RegisterDatasetCommandValidator()

    with pytest.raises(UnsupportedDatasetSourceError):
        validator.validate(command)


def test_invalid_path_rejected_before_write() -> None:
    command = _build_command("/tmp/does-not-exist.csv")
    validator = RegisterDatasetCommandValidator()

    with pytest.raises(ValidationError):
        validator.validate(command)


def test_xlsx_path_is_supported(xlsx_dataset_path: Path) -> None:
    command = _build_command(str(xlsx_dataset_path))
    validator = RegisterDatasetCommandValidator()
    validator.validate(command)


def test_xlsx_mime_type_detection_is_supported(xlsx_dataset_path: Path) -> None:
    command = RegisterDatasetCommand(
        **{
            **_build_command(str(xlsx_dataset_path)).__dict__,
            "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        },
    )
    validator = RegisterDatasetCommandValidator()
    validator.validate(command)


def test_xls_is_rejected_as_unsupported_format(tmp_path: Path) -> None:
    source = tmp_path / "legacy.xls"
    source.write_text("legacy", encoding="utf-8")
    command = _build_command(str(source))
    validator = RegisterDatasetCommandValidator()

    with pytest.raises(UnsupportedDatasetSourceError) as error:
        validator.validate(command)
    assert (
        str(error.value)
        == "Only CSV, JSON, JSONL/NDJSON, Parquet, and XLSX dataset files are supported."
    )


def test_xlsm_is_rejected_as_unsupported_format(tmp_path: Path) -> None:
    source = tmp_path / "macro.xlsm"
    source.write_text("macro", encoding="utf-8")
    command = _build_command(str(source))
    validator = RegisterDatasetCommandValidator()

    with pytest.raises(UnsupportedDatasetSourceError) as error:
        validator.validate(command)
    assert (
        str(error.value)
        == "Only CSV, JSON, JSONL/NDJSON, Parquet, and XLSX dataset files are supported."
    )


def test_duplicate_slug_same_tenant_rejected_when_not_allowed(csv_dataset_path: Path) -> None:
    command = _build_command(str(csv_dataset_path))
    validator = RegisterDatasetCommandValidator()
    with patch(
        "resource_registry.application.validators.Dataset.objects.filter",
    ) as mocked_filter:
        mocked_filter.return_value.exists.return_value = True
        with pytest.raises(DuplicateDatasetError):
            validator.validate(command, allow_existing_slug=False)


def test_same_slug_different_tenant_allowed(csv_dataset_path: Path) -> None:
    validator = RegisterDatasetCommandValidator()
    command = _build_command(str(csv_dataset_path))
    with patch(
        "resource_registry.application.validators.Dataset.objects.filter",
    ) as mocked_filter:
        mocked_filter.return_value.exists.return_value = False
        validator.validate(command, allow_existing_slug=False)


def test_idempotency_same_slug_same_hash_returns_existing_version() -> None:
    service = DatasetRegistrationIdempotencyService()
    dataset = MagicMock()
    existing_version = MagicMock()
    existing_version.id = "version-1"
    dataset.versions.filter.return_value.order_by.return_value.first.return_value = existing_version
    existing_version.schemas.order_by.return_value.first.return_value = SimpleNamespace(
        id="schema-1",
    )
    existing_version.profiles.order_by.return_value.first.return_value = SimpleNamespace(
        id="profile-1",
    )
    existing_version.artifacts.order_by.return_value.first.return_value = SimpleNamespace(
        id="artifact-1",
    )
    with (
        patch.object(service._hash_service, "compute_sha256", return_value="hash-1"),
        patch("resource_registry.application.idempotency.Dataset.objects.filter") as mocked_filter,
    ):
        mocked_filter.return_value.first.return_value = dataset
        resolution = service.resolve(
            tenant_id="11111111-1111-1111-1111-111111111111",
            slug="sales-data",
            path="/tmp/data.csv",
        )

    assert resolution.existing_version == existing_version


def test_idempotency_same_slug_different_hash_creates_new_version() -> None:
    service = DatasetRegistrationIdempotencyService()
    dataset = MagicMock()
    dataset.versions.filter.return_value.order_by.return_value.first.return_value = None
    with (
        patch.object(service._hash_service, "compute_sha256", return_value="hash-2"),
        patch("resource_registry.application.idempotency.Dataset.objects.filter") as mocked_filter,
    ):
        mocked_filter.return_value.first.return_value = dataset
        resolution = service.resolve(
            tenant_id="11111111-1111-1111-1111-111111111111",
            slug="sales-data",
            path="/tmp/data.csv",
        )

    assert resolution.dataset == dataset
    assert resolution.existing_version is None
    assert resolution.content_hash == "hash-2"


def test_file_hash_is_streamed_in_chunks() -> None:
    hasher = LocalFileHashService()
    payload = b"a" * (CHUNK_SIZE + 10)
    stream = io.BytesIO(payload)
    read_calls: list[int] = []

    original_read = stream.read

    def tracked_read(size: int = -1) -> bytes:
        read_calls.append(size)
        return original_read(size)

    stream.read = tracked_read  # type: ignore[assignment]

    class StreamContext:
        def __enter__(self):
            return stream

        def __exit__(self, exc_type, exc, tb):
            return None

    with patch("pathlib.Path.open", return_value=StreamContext()):
        hasher.compute_sha256("/tmp/large-file.csv", chunk_size=CHUNK_SIZE)

    assert read_calls
    assert all(size == CHUNK_SIZE for size in read_calls)
