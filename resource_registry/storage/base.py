from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class StorageObjectMetadata:
    uri: str
    provider: str
    size_bytes: int | None = None
    content_type: str | None = None
    checksum: str | None = None
    etag: str | None = None
    last_modified: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class StorageProvider(Protocol):
    def exists(self, uri_or_path: str) -> bool:
        """Return true if the object exists."""

    def head(self, uri_or_path: str) -> StorageObjectMetadata:
        """Return object metadata."""

    def put(self, local_path: str, destination_path: str) -> StorageObjectMetadata:
        """Upload/copy from local path into provider destination path."""

    def get_uri(self, destination_path: str) -> str:
        """Return a canonical, sanitized URI for destination_path."""

    def delete(self, uri_or_path: str) -> None:
        """Delete object if present."""

    def delete_checked(self, uri_or_path: str, *, etag: str | None, checksum: str | None) -> None:
        """Delete the reviewed object only if its provider identity still matches."""

    def download_to_file(self, uri_or_path: str, local_path: str) -> StorageObjectMetadata:
        """Download/copy a provider object to local_path using streaming I/O."""


def to_dataset_artifact_fields(
    storage_metadata: StorageObjectMetadata,
    *,
    artifact_role: str,
    artifact_format: str,
) -> dict[str, Any]:
    """Map storage metadata into DatasetArtifact-compatible fields."""
    return {
        "artifact_role": artifact_role,
        "uri": storage_metadata.uri,
        "format": artifact_format,
        "size_bytes": storage_metadata.size_bytes or 0,
        "checksum": storage_metadata.checksum,
        "storage_provider": storage_metadata.provider,
        "metadata_json": storage_metadata.metadata,
    }
