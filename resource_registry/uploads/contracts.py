"""Immutable contracts for large-file upload application services."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol


class SupportedUploadFormat(StrEnum):
    """Identify formats accepted by the initial upload boundary."""

    XLSX = "XLSX"
    CSV = "CSV"
    JSON = "JSON"
    JSONL = "JSONL"
    PARQUET = "PARQUET"


@dataclass(frozen=True, kw_only=True, slots=True)
class DirectUploadAuthorization:
    """Return one short-lived, single-object direct-upload authorization."""

    upload_url: str
    expires_at: datetime
    upload_method: str
    recommended_chunk_size_bytes: int
    max_parallel_chunks: int
    required_headers: Mapping[str, str]

    def __post_init__(self) -> None:
        """Freeze required headers so callers cannot widen the contract."""
        object.__setattr__(self, "required_headers", MappingProxyType(dict(self.required_headers)))


@dataclass(frozen=True, kw_only=True)
class UploadedObjectMetadata:
    """Describe bounded provider metadata without exposing storage credentials."""

    object_key: str
    size_bytes: int
    content_type: str
    etag: str | None = None
    version_id: str | None = None
    provider_checksum: str | None = None
    initial_bytes: bytes = b""


class LargeFileUploadProvider(Protocol):
    """Define provider-neutral direct-upload operations."""

    provider_name: str

    def create_upload_authorization(
        self,
        *,
        object_key: str,
        content_type: str,
        expires_at: datetime,
        recommended_chunk_size_bytes: int,
        max_parallel_chunks: int,
    ) -> DirectUploadAuthorization:
        """Issue write/create authority for exactly one object."""

    def inspect_uploaded_object(self, *, object_key: str) -> UploadedObjectMetadata:
        """Inspect provider metadata and a bounded initial byte sample."""

    def delete_object(self, *, object_key: str) -> None:
        """Delete exactly one server-selected object if it exists."""

    def copy_object(
        self, *, source_object_key: str, destination_object_key: str
    ) -> UploadedObjectMetadata:
        """Copy one server-selected object without exposing storage credentials."""
