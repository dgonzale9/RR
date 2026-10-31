"""Local development adapter for direct workbook intake storage."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from resource_registry.storage.exceptions import (
    DataRegistryStorageError,
    StorageObjectNotFoundError,
)
from resource_registry.storage.factory import DataRegistryStorageFactory
from resource_registry.uploads.contracts import DirectUploadAuthorization, UploadedObjectMetadata
from resource_registry.uploads.exceptions import UploadApplicationError


class LocalLargeFileUploadProvider:
    """Provide non-production local object inspection and controlled copy operations."""

    provider_name = "local"

    def __init__(self) -> None:
        """Initialize the configured local Resource Registry storage provider."""
        self._provider = DataRegistryStorageFactory().create_provider("local")

    def create_upload_authorization(
        self,
        *,
        object_key: str,
        content_type: str,
        expires_at: datetime,
        recommended_chunk_size_bytes: int,
        max_parallel_chunks: int,
    ) -> DirectUploadAuthorization:
        """Return a local development descriptor without exposing write credentials."""
        return DirectUploadAuthorization(
            upload_url=self._provider.get_uri(object_key),
            expires_at=expires_at,
            upload_method="local-development",
            recommended_chunk_size_bytes=recommended_chunk_size_bytes,
            max_parallel_chunks=max_parallel_chunks,
            required_headers={"Content-Type": content_type},
        )

    def inspect_uploaded_object(self, *, object_key: str) -> UploadedObjectMetadata:
        """Inspect one local server-selected object."""
        try:
            metadata = self._provider.head(object_key)
            sample_path = Path()
            with NamedTemporaryFile() as handle:
                sample_path = Path(handle.name)
                self._provider.download_to_file(object_key, str(sample_path))
                with sample_path.open("rb") as source_file:
                    initial_bytes = source_file.read(64 * 1024)
        except Exception as error:
            raise UploadApplicationError("UPLOAD_PROVIDER_OBJECT_NOT_FOUND", 404) from error
        return UploadedObjectMetadata(
            object_key=object_key,
            size_bytes=int(metadata.size_bytes or 0),
            content_type=str(metadata.content_type or "application/octet-stream"),
            etag=metadata.etag,
            provider_checksum=metadata.checksum,
            initial_bytes=initial_bytes,
        )

    def delete_object(self, *, object_key: str) -> None:
        """Delete one local server-selected object."""
        try:
            self._provider.delete(object_key)
        except Exception as error:
            raise UploadApplicationError("UPLOAD_PROVIDER_CLEANUP_FAILED", 503) from error

    def stage_upload_block(self, *, object_key: str, block_id: str, content: bytes) -> None:
        """Persist one bounded block at a server-selected local staging key.

        Args:
            object_key: Validated, server-owned upload destination.
            block_id: Server-derived identity of this block.
            content: Size-checked bytes from the intake upload service.
        """
        with NamedTemporaryFile() as temporary:
            temporary.write(content)
            temporary.flush()
            try:
                self._provider.put(temporary.name, self._block_key(object_key, block_id))
            except (OSError, DataRegistryStorageError) as error:
                raise UploadApplicationError("UPLOAD_PROVIDER_WRITE_FAILED", 503) from error

    def commit_upload_blocks(
        self, *, object_key: str, block_sizes: Mapping[str, int], content_type: str
    ) -> None:
        """Verify and assemble an ordered block manifest before exposing the local file.

        Args:
            object_key: Server-selected final key.
            block_sizes: Ordered expected block IDs and exact byte counts.
            content_type: Validated declared MIME type, retained by intake metadata.

        Raises:
            UploadApplicationError: If any block is absent or has an unexpected size.
        """
        with NamedTemporaryFile() as assembled, NamedTemporaryFile() as fragment:
            for block_id, expected_size in block_sizes.items():
                try:
                    self._provider.download_to_file(
                        self._block_key(object_key, block_id), fragment.name
                    )
                    content = Path(fragment.name).read_bytes()
                except (OSError, StorageObjectNotFoundError) as error:
                    raise UploadApplicationError("INTAKE_UPLOAD_INCOMPLETE", 409) from error
                if len(content) != expected_size:
                    raise UploadApplicationError("INTAKE_UPLOAD_INCOMPLETE", 409)
                assembled.write(content)
            assembled.flush()
            self._provider.put(assembled.name, object_key)
        for block_id in block_sizes:
            self._provider.delete(self._block_key(object_key, block_id))

    @staticmethod
    def _block_key(object_key: str, block_id: str) -> str:
        return f"{object_key}.blocks/{hashlib.sha256(block_id.encode()).hexdigest()}"

    def copy_object(
        self,
        *,
        source_object_key: str,
        destination_object_key: str,
    ) -> UploadedObjectMetadata:
        """Copy one local object through a private temporary file."""
        try:
            with NamedTemporaryFile() as handle:
                self._provider.download_to_file(source_object_key, handle.name)
                metadata = self._provider.put(handle.name, destination_object_key)
        except Exception as error:
            raise UploadApplicationError("UPLOAD_PROVIDER_COPY_FAILED", 503) from error
        return UploadedObjectMetadata(
            object_key=destination_object_key,
            size_bytes=int(metadata.size_bytes or 0),
            content_type=str(metadata.content_type or "application/octet-stream"),
            etag=metadata.etag,
            provider_checksum=metadata.checksum,
        )
