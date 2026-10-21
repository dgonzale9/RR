"""Resolve and materialize workbook jobs from trusted UploadSession provenance."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Final
from uuid import UUID

from django.conf import settings

from resource_registry.models import (
    LargeFileUploadStatus,
    RegistrationJobType,
    RegistrationSubmissionStatus,
    WorkbookIngestionJob,
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


class WorkbookUploadExecutionError(RuntimeError):
    """Report a stable workbook provenance or materialization failure."""

    def __init__(self, code: str, *, retryable: bool) -> None:
        super().__init__("Unable to prepare the workbook upload for execution.")
        self.code = code
        self.retryable = retryable


@dataclass(kw_only=True)
class MaterializedWorkbookUpload:
    """Carry trusted source metadata and one temporary workbook path."""

    upload_id: UUID
    tenant_id: UUID
    original_filename: str
    content_type: str
    storage_provider: str
    source_metadata: StorageObjectMetadata
    temporary_artifact: TemporaryArtifact

    @property
    def local_path(self) -> str:
        """Return the ephemeral server-controlled path used only during execution."""
        return self.temporary_artifact.local_path

    def __enter__(self) -> MaterializedWorkbookUpload:
        """Enter the temporary materialization lifetime."""
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Always remove the temporary workbook and surface cleanup failure."""
        _ = exception_type, exception, traceback
        try:
            self.temporary_artifact.cleanup()
        except OSError as error:
            raise WorkbookUploadExecutionError(
                "workbook_temporary_cleanup_failed",
                retryable=False,
            ) from error


class WorkbookUploadMaterializationService:
    """Materialize only the exact object referenced by a linked completed upload."""

    def __init__(
        self,
        *,
        storage_factory: DataRegistryStorageFactory | None = None,
        temp_directory: str | Path | None = None,
    ) -> None:
        self._storage_factory = storage_factory or DataRegistryStorageFactory()
        self._temp_directory: Final[str | Path | None] = temp_directory

    def materialize(self, *, job: WorkbookIngestionJob) -> MaterializedWorkbookUpload:
        """Resolve trusted provenance, verify provider metadata, and download securely.

        Args:
            job: Claimed Workbook ingestion job.

        Returns:
            Context-managed temporary workbook plus canonical storage metadata.

        Raises:
            WorkbookUploadExecutionError: If provenance, storage, integrity, or cleanup fails.
        """
        resolved_job = (
            WorkbookIngestionJob.objects.select_related(
                "registration_submission__upload_session",
            )
            .filter(pk=job.pk)
            .first()
        )
        if resolved_job is None:
            raise WorkbookUploadExecutionError("workbook_job_not_found", retryable=False)
        submission = resolved_job.registration_submission
        if submission is None:
            raise WorkbookUploadExecutionError(
                "workbook_registration_submission_missing",
                retryable=False,
            )
        upload = submission.upload_session
        self._validate_provenance(job=resolved_job, submission=submission, upload=upload)
        try:
            provider = self._storage_factory.create_provider(upload.storage_provider)
            source_uri = provider.get_uri(upload.storage_object_key)
            source_metadata = provider.head(source_uri)
        except StorageObjectNotFoundError as error:
            raise WorkbookUploadExecutionError(
                "workbook_upload_object_not_found",
                retryable=False,
            ) from error
        except (
            StorageConfigurationError,
            StorageProviderUnsupportedError,
            UnsafeStorageUriError,
        ) as error:
            raise WorkbookUploadExecutionError(
                "workbook_storage_resolution_failed",
                retryable=False,
            ) from error
        except Exception as error:
            raise WorkbookUploadExecutionError(
                "workbook_storage_metadata_unavailable",
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
            if Path(temporary_artifact.local_path).stat().st_size != upload.actual_size_bytes:
                raise WorkbookUploadExecutionError(
                    "workbook_materialized_size_mismatch",
                    retryable=False,
                )
        except WorkbookUploadExecutionError:
            if temporary_artifact is not None:
                temporary_artifact.cleanup()
            raise
        except ArtifactMaterializationError as error:
            raise WorkbookUploadExecutionError(
                "workbook_download_failed",
                retryable=True,
            ) from error
        except OSError as error:
            if temporary_artifact is not None:
                temporary_artifact.cleanup()
            raise WorkbookUploadExecutionError(
                "workbook_temporary_materialization_failed",
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
        return MaterializedWorkbookUpload(
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
        if not isinstance(job, WorkbookIngestionJob):
            raise WorkbookUploadExecutionError("workbook_job_not_found", retryable=False)
        if str(getattr(submission, "status", "")) != RegistrationSubmissionStatus.SUBMITTED:
            raise WorkbookUploadExecutionError(
                "workbook_registration_submission_invalid",
                retryable=False,
            )
        if str(getattr(submission, "job_type", "")) != RegistrationJobType.WORKBOOK_INGESTION:
            raise WorkbookUploadExecutionError(
                "workbook_registration_job_type_invalid",
                retryable=False,
            )
        if str(getattr(upload, "status", "")) != LargeFileUploadStatus.COMPLETED:
            raise WorkbookUploadExecutionError("workbook_upload_not_completed", retryable=False)
        if str(getattr(upload, "resolved_file_format", "")) != "XLSX":
            raise WorkbookUploadExecutionError("workbook_upload_format_invalid", retryable=False)
        actual_size = int(getattr(upload, "actual_size_bytes", 0) or 0)
        maximum_size = int(getattr(settings, "RESOURCE_REGISTRY_UPLOAD_MAX_FILE_SIZE_BYTES", 0) or 0)
        if actual_size <= 0 or (maximum_size > 0 and actual_size > maximum_size):
            raise WorkbookUploadExecutionError("workbook_upload_size_invalid", retryable=False)
        if (
            getattr(submission, "organization_id", None) != getattr(upload, "organization_id", None)
            or getattr(submission, "workspace_id", None) != getattr(upload, "workspace_id", None)
            or getattr(submission, "registry_namespace_id", None)
            != getattr(upload, "registry_namespace_id", None)
            or getattr(job, "tenant_id", None) != getattr(upload, "registry_namespace_id", None)
        ):
            raise WorkbookUploadExecutionError(
                "workbook_upload_provenance_mismatch",
                retryable=False,
            )

    @staticmethod
    def _validate_storage_metadata(*, upload: object, metadata: StorageObjectMetadata) -> None:
        actual_size = getattr(upload, "actual_size_bytes", None)
        if metadata.size_bytes != actual_size:
            raise WorkbookUploadExecutionError(
                "workbook_storage_size_mismatch",
                retryable=False,
            )
        expected_etag = getattr(upload, "storage_etag", None)
        if expected_etag and metadata.etag != expected_etag:
            raise WorkbookUploadExecutionError(
                "workbook_storage_etag_mismatch",
                retryable=False,
            )

    @staticmethod
    def _uri_scheme(uri: str) -> str:
        delimiter_index = uri.find("://")
        if delimiter_index <= 0:
            raise WorkbookUploadExecutionError(
                "workbook_storage_resolution_failed",
                retryable=False,
            )
        return uri[: delimiter_index + 3]
