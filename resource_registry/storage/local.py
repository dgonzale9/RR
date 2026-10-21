from __future__ import annotations

import hashlib
import mimetypes
import shutil
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlparse

from resource_registry.storage.base import StorageObjectMetadata
from resource_registry.storage.exceptions import StorageObjectNotFoundError, UnsafeStorageUriError


class LocalStorageProvider:
    """Filesystem-backed storage provider with local:// URI scheme."""

    URI_SCHEME = "local://"
    PROVIDER_NAME = "local"

    def __init__(self, base_directory: str | Path) -> None:
        self._base_directory = Path(base_directory).resolve()
        self._base_directory.mkdir(parents=True, exist_ok=True)

    def exists(self, uri_or_path: str) -> bool:
        target_path = self._resolve_target_path(uri_or_path)
        return target_path.exists() and target_path.is_file()

    def head(self, uri_or_path: str) -> StorageObjectMetadata:
        target_path = self._resolve_target_path(uri_or_path)
        if not target_path.exists() or not target_path.is_file():
            raise StorageObjectNotFoundError(f"Local storage object not found: {uri_or_path}")

        stat_result = target_path.stat()
        content_type, _ = mimetypes.guess_type(target_path.name)
        content_type = {
            ".jsonl": "application/x-ndjson",
            ".ndjson": "application/x-ndjson",
            ".parquet": "application/vnd.apache.parquet",
        }.get(target_path.suffix.lower(), content_type)
        relative_path = target_path.relative_to(self._base_directory).as_posix()
        return StorageObjectMetadata(
            uri=self.get_uri(relative_path),
            provider=self.PROVIDER_NAME,
            size_bytes=stat_result.st_size,
            content_type=content_type or "application/octet-stream",
            checksum=self._compute_sha256(target_path),
            etag=None,
            last_modified=None,
            metadata={"relative_path": relative_path},
        )

    def put(self, local_path: str, destination_path: str) -> StorageObjectMetadata:
        source_path = Path(local_path)
        if not source_path.exists() or not source_path.is_file():
            raise StorageObjectNotFoundError(f"Source file not found: {local_path}")

        destination_full_path = self._resolve_target_path(destination_path)
        destination_full_path.parent.mkdir(parents=True, exist_ok=True)

        with (
            source_path.open("rb") as source_file,
            destination_full_path.open("wb") as destination_file,
        ):
            shutil.copyfileobj(source_file, destination_file)

        return self.head(destination_full_path.relative_to(self._base_directory).as_posix())

    def get_uri(self, destination_path: str) -> str:
        normalized_path = self._normalize_relative_path(destination_path)
        return f"{self.URI_SCHEME}{normalized_path}"

    def delete(self, uri_or_path: str) -> None:
        target_path = self._resolve_target_path(uri_or_path)
        if target_path.exists() and target_path.is_file():
            target_path.unlink()

    def download_to_file(self, uri_or_path: str, local_path: str) -> StorageObjectMetadata:
        source_path = self._resolve_target_path(uri_or_path)
        if not source_path.exists() or not source_path.is_file():
            raise StorageObjectNotFoundError(f"Local storage object not found: {uri_or_path}")

        destination_path = Path(local_path)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        with (
            source_path.open("rb") as source_file,
            destination_path.open("wb") as destination_file,
        ):
            shutil.copyfileobj(source_file, destination_file)
        return self.head(uri_or_path)

    def _resolve_target_path(self, uri_or_path: str) -> Path:
        relative_path = self._normalize_relative_path(uri_or_path)
        resolved_path = (self._base_directory / relative_path).resolve()
        if not str(resolved_path).startswith(str(self._base_directory)):
            raise UnsafeStorageUriError("Path traversal detected for local storage provider.")
        return resolved_path

    def _normalize_relative_path(self, uri_or_path: str) -> str:
        raw_value = uri_or_path.strip()
        if not raw_value:
            raise UnsafeStorageUriError("Storage path cannot be empty.")

        if raw_value.startswith(self.URI_SCHEME):
            raw_value = raw_value[len(self.URI_SCHEME) :]
        elif "://" in raw_value:
            parsed_url = urlparse(raw_value)
            if parsed_url.scheme != "local":
                raise UnsafeStorageUriError("Unsupported URI scheme for local storage provider.")
            raw_value = unquote(parsed_url.path.lstrip("/"))

        normalized = PurePosixPath(raw_value.replace("\\", "/")).as_posix().lstrip("/")
        if normalized in {"", "."} or normalized.startswith("../") or "/../" in normalized:
            raise UnsafeStorageUriError("Unsafe local storage path.")
        return normalized

    def _compute_sha256(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(64 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
