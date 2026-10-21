from __future__ import annotations

import hashlib
import mimetypes
import os
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import BlobServiceClient
from django.conf import settings

from resource_registry.storage.base import StorageObjectMetadata
from resource_registry.storage.exceptions import (
    StorageConfigurationError,
    StorageObjectNotFoundError,
    UnsafeStorageUriError,
)


class AzureBlobStorageProvider:
    """Azure Blob storage provider with path safety and URI sanitization."""

    PROVIDER_NAME = "azure_blob"

    def __init__(
        self,
        *,
        account_name: str | None = None,
        account_key: str | None = None,
        container_name: str | None = None,
        blob_service_client: BlobServiceClient | None = None,
    ) -> None:
        self._account_name = account_name or self._get_setting("AZ_STORAGE_ACCOUNT_NAME")
        self._account_key = account_key or self._get_setting("AZ_STORAGE_ACCOUNT_KEY")
        self._container_name = container_name or self._get_setting("AZ_STORAGE_MEDIA_CONTAINER")
        if not self._account_name or not self._account_key or not self._container_name:
            raise StorageConfigurationError(
                "Azure Blob storage configuration is incomplete. "
                "Expected account name, key, and container.",
            )

        if blob_service_client is None:
            # settings.py builds AZURE_CONNECTION_STRING once, honoring
            # AZURE_CUSTOM_DOMAIN for Azurite -- reuse it instead of guessing a
            # host, same pattern uploads/azure_provider.py already uses.
            connection_string = getattr(settings, "AZURE_CONNECTION_STRING", None)
            if not connection_string:
                raise StorageConfigurationError(
                    "AZURE_CONNECTION_STRING is not configured.",
                )
            self._blob_service_client = BlobServiceClient.from_connection_string(
                connection_string
            )
        else:
            self._blob_service_client = blob_service_client

    def exists(self, uri_or_path: str) -> bool:
        blob_path = self._normalize_blob_path(uri_or_path)
        blob_client = self._get_blob_client(blob_path)
        try:
            return bool(blob_client.exists())
        except ResourceNotFoundError:
            return False

    def head(self, uri_or_path: str) -> StorageObjectMetadata:
        blob_path = self._normalize_blob_path(uri_or_path)
        blob_client = self._get_blob_client(blob_path)
        try:
            properties = blob_client.get_blob_properties()
        except ResourceNotFoundError as error:
            raise StorageObjectNotFoundError(
                f"Azure blob object not found: {uri_or_path}",
            ) from error

        content_settings = getattr(properties, "content_settings", None)
        metadata = getattr(properties, "metadata", {}) or {}
        clean_metadata = self._sanitize_metadata(metadata)
        content_type = (
            getattr(content_settings, "content_type", None)
            or mimetypes.guess_type(blob_path)[0]
            or "application/octet-stream"
        )
        return StorageObjectMetadata(
            uri=self.get_uri(blob_path),
            provider=self.PROVIDER_NAME,
            size_bytes=getattr(properties, "size", None),
            content_type=content_type,
            checksum=getattr(content_settings, "content_md5", None),
            etag=getattr(properties, "etag", None),
            last_modified=getattr(properties, "last_modified", None),
            metadata=clean_metadata,
        )

    def put(self, local_path: str, destination_path: str) -> StorageObjectMetadata:
        source_path = Path(local_path)
        if not source_path.exists() or not source_path.is_file():
            raise StorageObjectNotFoundError(f"Source file not found: {local_path}")

        blob_path = self._normalize_blob_path(destination_path)
        blob_client = self._get_blob_client(blob_path)
        content_type = mimetypes.guess_type(source_path.name)[0] or "application/octet-stream"
        with source_path.open("rb") as stream:
            blob_client.upload_blob(stream, overwrite=True, content_type=content_type)
        return self.head(blob_path)

    def get_uri(self, destination_path: str) -> str:
        blob_path = self._normalize_blob_path(destination_path)
        return f"azure://{self._container_name}/{blob_path}"

    def delete(self, uri_or_path: str) -> None:
        blob_path = self._normalize_blob_path(uri_or_path)
        blob_client = self._get_blob_client(blob_path)
        try:
            blob_client.delete_blob()
        except ResourceNotFoundError:
            return

    def download_to_file(self, uri_or_path: str, local_path: str) -> StorageObjectMetadata:
        blob_path = self._normalize_blob_path(uri_or_path)
        blob_client = self._get_blob_client(blob_path)
        destination_path = Path(local_path)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            stream = blob_client.download_blob()
            with destination_path.open("wb") as destination_file:
                stream.readinto(destination_file)
        except ResourceNotFoundError as error:
            raise StorageObjectNotFoundError(
                f"Azure blob object not found: {uri_or_path}",
            ) from error
        return self.head(blob_path)

    def _get_blob_client(self, blob_path: str):
        container_client = self._blob_service_client.get_container_client(self._container_name)
        return container_client.get_blob_client(blob_path)

    def _normalize_blob_path(self, uri_or_path: str) -> str:
        raw_value = uri_or_path.strip()
        if not raw_value:
            raise UnsafeStorageUriError("Azure blob path cannot be empty.")
        if "sig=" in raw_value.lower():
            raise UnsafeStorageUriError(
                "SAS token-bearing URI is unsafe for persistent storage reference.",
            )

        if raw_value.startswith("azure://"):
            parsed = urlparse(raw_value)
            if parse_qs(parsed.query).get("sig"):
                raise UnsafeStorageUriError(
                    "SAS token-bearing URI is unsafe for persistent storage reference.",
                )
            container = parsed.netloc
            if container and container != self._container_name:
                raise UnsafeStorageUriError(
                    "Azure URI container does not match configured container.",
                )
            raw_value = parsed.path.lstrip("/")
        elif raw_value.startswith("http://") or raw_value.startswith("https://"):
            parsed = urlparse(raw_value)
            if parse_qs(parsed.query).get("sig"):
                raise UnsafeStorageUriError(
                    "SAS token-bearing URI is unsafe for persistent storage reference.",
                )
            raw_value = parsed.path
            marker = f"/{self._container_name}/"
            if marker in raw_value:
                raw_value = raw_value.split(marker, maxsplit=1)[1]
            else:
                raw_value = raw_value.lstrip("/")

        normalized = PurePosixPath(unquote(raw_value).replace("\\", "/")).as_posix().lstrip("/")
        if normalized in {"", "."} or normalized.startswith("../") or "/../" in normalized:
            raise UnsafeStorageUriError("Unsafe Azure blob path.")
        return normalized

    def _get_setting(self, key: str) -> str | None:
        value = getattr(settings, key, None)
        if value is None:
            value = os.environ.get(key)
        if isinstance(value, str):
            value = value.strip() or None
        return value

    def _sanitize_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        clean: dict[str, Any] = {}
        for key, value in metadata.items():
            lowered = key.lower()
            if lowered in {"sig", "token", "secret", "password", "key", "credential"}:
                continue
            clean[key] = value
        return clean

    def compute_source_checksum(self, local_path: str) -> str:
        digest = hashlib.sha256()
        with Path(local_path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(64 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
