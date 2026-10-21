"""Resolve dataset registration jobs through trusted UploadSession provenance."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Final
from uuid import UUID

from django.conf import settings

from resource_registry.models import (
    DatasetRegistrationJob,
    DatasetRegistrationJobStatus,
    LargeFileUploadStatus,
    RegistrationJobType,
    RegistrationSubmissionStatus,
)
from resource_registry.services.json_dataset import (
    JsonDatasetValidationError,
    StrictJsonLinesDatasetPolicy,
    scan_json_dataset,
)
from resource_registry.services.parquet_dataset import (
    ParquetDatasetValidationError,
    validate_parquet_dataset,
)
from resource_registry.storage.base import StorageObjectMetadata
from resource_registry.storage.exceptions import (
    ArtifactMaterializationError,
    StorageConfigurationError,
    StorageObjectNotFoundError,
    StorageProviderUnsupportedError,
    UnsafeStorageUriError,
)
from resource_registry.storage.factory import DataRegistryStorageFactory
from resource_registry.storage.read_adapters import (
    StorageProviderArtifactReadAdapter,
    TemporaryArtifact,
)


class CsvUploadExecutionError(RuntimeError):
    """Report a stable CSV provenance or materialization failure."""

    def __init__(self, code: str, *, retryable: bool) -> None:
        super().__init__("Unable to prepare the dataset upload for execution.")
        self.code = code
        self.retryable = retryable


@dataclass(kw_only=True)
class MaterializedCsvUpload:
    """Carry trusted source metadata and one temporary CSV path."""

    upload_id: UUID
    tenant_id: UUID
    original_filename: str
    content_type: str
    storage_provider: str
    source_metadata: StorageObjectMetadata
    temporary_artifact: TemporaryArtifact

    @property
    def local_path(self) -> str:
        """Return the ephemeral server-controlled execution path."""
        return self.temporary_artifact.local_path

    def __enter__(self) -> MaterializedCsvUpload:
        """Enter the temporary materialization lifetime."""
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Remove the temporary CSV and surface sanitized cleanup failure."""
        _ = exception_type, exception, traceback
        try:
            self.temporary_artifact.cleanup()
        except OSError as error:
            raise CsvUploadExecutionError(
                "csv_temporary_cleanup_failed",
                retryable=False,
            ) from error


class CsvUploadMaterializationService:
    """Materialize the exact object referenced by a completed dataset upload.

    Retain the historical class name for compatibility with existing worker integrations.
    """

    def __init__(
        self,
        *,
        storage_factory: DataRegistryStorageFactory | None = None,
        temp_directory: str | Path | None = None,
    ) -> None:
        self._storage_factory = storage_factory or DataRegistryStorageFactory()
        self._temp_directory: Final[str | Path | None] = temp_directory

    def materialize(self, *, job: DatasetRegistrationJob) -> MaterializedCsvUpload:
        """Validate canonical provenance and securely download one CSV.

        Args:
            job: Claimed CSV registration job.

        Returns:
            Context-managed temporary CSV with canonical provider metadata.

        Raises:
            CsvUploadExecutionError: If provenance, integrity, download, or cleanup fails.
        """
        resolved_job = (
            DatasetRegistrationJob.objects.select_related(
                "registration_submission__upload_session",
            )
            .filter(pk=job.pk)
            .first()
        )
        if resolved_job is None:
            raise CsvUploadExecutionError("csv_job_not_found", retryable=False)
        submission = resolved_job.registration_submission
        upload = submission.upload_session
        self._validate_provenance(job=resolved_job, submission=submission, upload=upload)
        try:
            provider = self._storage_factory.create_provider(upload.storage_provider)
            source_uri = provider.get_uri(upload.storage_object_key)
            source_metadata = provider.head(source_uri)
        except StorageObjectNotFoundError as error:
            raise CsvUploadExecutionError("csv_upload_object_not_found", retryable=False) from error
        except (
            StorageConfigurationError,
            StorageProviderUnsupportedError,
            UnsafeStorageUriError,
        ) as error:
            raise CsvUploadExecutionError(
                "csv_storage_resolution_failed", retryable=False
            ) from error
        except Exception as error:
            raise CsvUploadExecutionError(
                "csv_storage_metadata_unavailable",
                retryable=True,
            ) from error
        self._validate_storage_metadata(upload=upload, metadata=source_metadata)
        adapter = StorageProviderArtifactReadAdapter(
            storage_provider=provider,
            schemes=(self._uri_scheme(source_uri),),
        )
        temporary_artifact: TemporaryArtifact | None = None
        try:
            temporary_artifact = adapter.materialize_to_temp(
                source_uri,
                temp_directory=self._temp_directory,
            )
            local_path = Path(temporary_artifact.local_path)
            if local_path.stat().st_size != upload.actual_size_bytes:
                raise CsvUploadExecutionError(
                    "csv_materialized_size_mismatch",
                    retryable=False,
                )
            self._validate_dataset_evidence(local_path, file_format=upload.resolved_file_format)
        except CsvUploadExecutionError:
            if temporary_artifact is not None:
                temporary_artifact.cleanup()
            raise
        except ArtifactMaterializationError as error:
            raise CsvUploadExecutionError("csv_download_failed", retryable=True) from error
        except OSError as error:
            if temporary_artifact is not None:
                temporary_artifact.cleanup()
            raise CsvUploadExecutionError(
                "csv_temporary_materialization_failed",
                retryable=True,
            ) from error
        trusted_metadata = StorageObjectMetadata(
            uri=source_metadata.uri,
            provider=source_metadata.provider,
            size_bytes=source_metadata.size_bytes,
            content_type=source_metadata.content_type,
            checksum=source_metadata.checksum,
            etag=source_metadata.etag,
            last_modified=source_metadata.last_modified,
            metadata={
                **dict(source_metadata.metadata),
                "upload_id": str(upload.id),
                "source_file_name": upload.normalized_filename,
            },
        )
        return MaterializedCsvUpload(
            upload_id=upload.id,
            tenant_id=upload.registry_namespace_id,
            original_filename=upload.normalized_filename,
            content_type=upload.detected_content_type or upload.declared_content_type,
            storage_provider=upload.storage_provider,
            source_metadata=trusted_metadata,
            temporary_artifact=temporary_artifact,
        )

    @staticmethod
    def _validate_provenance(*, job: object, submission: object, upload: object) -> None:
        if not isinstance(job, DatasetRegistrationJob):
            raise CsvUploadExecutionError("csv_job_not_found", retryable=False)
        if str(job.status) != DatasetRegistrationJobStatus.RUNNING:
            raise CsvUploadExecutionError("csv_job_not_claimed", retryable=False)
        if str(getattr(submission, "status", "")) != RegistrationSubmissionStatus.SUBMITTED:
            raise CsvUploadExecutionError("csv_registration_submission_invalid", retryable=False)
        if str(getattr(submission, "job_type", "")) != RegistrationJobType.DATASET_REGISTRATION:
            raise CsvUploadExecutionError("csv_registration_job_type_invalid", retryable=False)
        if str(getattr(upload, "status", "")) != LargeFileUploadStatus.COMPLETED:
            raise CsvUploadExecutionError("csv_upload_not_completed", retryable=False)
        if str(getattr(upload, "resolved_file_format", "")) not in {
            "CSV",
            "JSON",
            "JSONL",
            "PARQUET",
        }:
            raise CsvUploadExecutionError("csv_upload_format_invalid", retryable=False)
        provider_key = str(getattr(upload, "storage_provider", "")).strip().lower()
        if provider_key not in {
            DataRegistryStorageFactory.LOCAL_PROVIDER,
            DataRegistryStorageFactory.AZURE_BLOB_PROVIDER,
        }:
            raise CsvUploadExecutionError("csv_storage_resolution_failed", retryable=False)
        if not str(getattr(upload, "storage_object_key", "")).strip():
            raise CsvUploadExecutionError("csv_upload_object_key_invalid", retryable=False)
        actual_size = int(getattr(upload, "actual_size_bytes", 0) or 0)
        maximum_size = int(
            getattr(settings, "RESOURCE_REGISTRY_UPLOAD_MAX_FILE_SIZE_BYTES", 0) or 0
        )
        if actual_size <= 0 or (maximum_size > 0 and actual_size > maximum_size):
            raise CsvUploadExecutionError("csv_upload_size_invalid", retryable=False)
        if (
            getattr(submission, "organization_id", None) != getattr(upload, "organization_id", None)
            or getattr(submission, "workspace_id", None) != getattr(upload, "workspace_id", None)
            or getattr(submission, "registry_namespace_id", None)
            != getattr(upload, "registry_namespace_id", None)
            or getattr(job, "tenant_id", None) != getattr(upload, "registry_namespace_id", None)
        ):
            raise CsvUploadExecutionError("csv_upload_provenance_mismatch", retryable=False)

    @staticmethod
    def _validate_storage_metadata(*, upload: object, metadata: StorageObjectMetadata) -> None:
        if metadata.size_bytes != getattr(upload, "actual_size_bytes", None):
            raise CsvUploadExecutionError("csv_storage_size_mismatch", retryable=False)
        file_format = str(getattr(upload, "resolved_file_format", ""))
        content_type_setting = {
            "CSV": "RESOURCE_REGISTRY_UPLOAD_ALLOWED_CONTENT_TYPES_CSV",
            "JSON": "RESOURCE_REGISTRY_UPLOAD_ALLOWED_CONTENT_TYPES_JSON",
            "JSONL": "RESOURCE_REGISTRY_UPLOAD_ALLOWED_CONTENT_TYPES_JSONL",
            "PARQUET": "RESOURCE_REGISTRY_UPLOAD_ALLOWED_CONTENT_TYPES_PARQUET",
        }[file_format]
        allowed_content_types = {
            str(value).strip().lower() for value in getattr(settings, content_type_setting, ())
        }
        content_type = str(metadata.content_type or "").strip().lower()
        if content_type and allowed_content_types and content_type not in allowed_content_types:
            raise CsvUploadExecutionError("csv_storage_content_type_mismatch", retryable=False)
        expected_etag = getattr(upload, "storage_etag", None)
        if expected_etag and metadata.etag != expected_etag:
            raise CsvUploadExecutionError("csv_storage_etag_mismatch", retryable=False)

    @classmethod
    def _validate_dataset_evidence(cls, path: Path, *, file_format: str) -> None:
        """Validate the trusted upload format without falling back to CSV."""
        try:
            if file_format == "JSON":
                scan_json_dataset(path)
            elif file_format == "JSONL":
                StrictJsonLinesDatasetPolicy().scan(path)
            elif file_format == "PARQUET":
                validate_parquet_dataset(path)
            elif file_format == "CSV":
                cls._validate_csv_evidence(path)
            else:
                raise CsvUploadExecutionError("csv_upload_format_invalid", retryable=False)
        except (JsonDatasetValidationError, ParquetDatasetValidationError) as error:
            code = "json_content_invalid" if file_format == "JSON" else str(error).lower()
            raise CsvUploadExecutionError(code, retryable=False) from error

    @staticmethod
    def _validate_csv_evidence(path: Path) -> None:
        with path.open("rb") as csv_file:
            sample = csv_file.read(8192)
        if not sample.strip() or sample.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
            raise CsvUploadExecutionError("csv_content_invalid", retryable=False)
        has_unicode_bom = sample.startswith(
            (b"\xff\xfe", b"\xfe\xff", b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")
        )
        if not has_unicode_bom and sample.count(b"\x00") * 5 >= len(sample):
            raise CsvUploadExecutionError("csv_content_invalid", retryable=False)

    @staticmethod
    def _uri_scheme(uri: str) -> str:
        delimiter_index = uri.find("://")
        if delimiter_index <= 0:
            raise CsvUploadExecutionError("csv_storage_resolution_failed", retryable=False)
        return uri[: delimiter_index + 3]
