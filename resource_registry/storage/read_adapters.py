from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from resource_registry.storage.base import StorageProvider
from resource_registry.storage.exceptions import (
    ArtifactMaterializationError,
    UnsafeStorageUriError,
)


@dataclass
class TemporaryArtifact:
    local_path: str
    source_uri: str
    _owns_file: bool = True

    def cleanup(self) -> None:
        if not self._owns_file:
            return
        path = Path(self.local_path)
        if path.exists() and path.is_file():
            path.unlink()

    def __enter__(self) -> TemporaryArtifact:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.cleanup()


class ArtifactReadAdapter(Protocol):
    def can_handle(self, uri: str) -> bool:
        """Return whether this adapter can materialize a given URI."""

    def materialize_to_temp(
        self,
        uri: str,
        *,
        temp_directory: str | Path | None = None,
    ) -> TemporaryArtifact:
        """Materialize source URI to a local temporary artifact path."""


class LocalArtifactReadAdapter:
    """Materialize local URIs/paths to safe temporary files."""

    def __init__(self, *, storage_provider: StorageProvider | None = None) -> None:
        self._storage_provider = storage_provider

    def can_handle(self, uri: str) -> bool:
        if "sig=" in uri.lower():
            raise UnsafeStorageUriError("SAS-bearing URI is unsafe for artifact reads.")
        if uri.startswith("local://"):
            return True
        path = Path(uri)
        return path.exists() and path.is_file()

    def materialize_to_temp(
        self,
        uri: str,
        *,
        temp_directory: str | Path | None = None,
    ) -> TemporaryArtifact:
        if "sig=" in uri.lower():
            raise UnsafeStorageUriError("SAS-bearing URI is unsafe for artifact reads.")
        if temp_directory is None:
            temp_path = _create_temp_path(uri)
        else:
            temp_path = _create_temp_path(uri, temp_directory=temp_directory)
        try:
            if uri.startswith("local://"):
                if self._storage_provider is None:
                    raise ArtifactMaterializationError(
                        "Local storage provider is required for local:// URI materialization.",
                    )
                self._storage_provider.download_to_file(uri, temp_path)
                return TemporaryArtifact(local_path=temp_path, source_uri=uri)

            source_path = Path(uri)
            if not source_path.exists() or not source_path.is_file():
                raise ArtifactMaterializationError(f"Local artifact does not exist: {uri}")
            with source_path.open("rb") as source_file, Path(temp_path).open("wb") as target_file:
                shutil.copyfileobj(source_file, target_file)
            return TemporaryArtifact(local_path=temp_path, source_uri=uri)
        except Exception as error:
            _safe_remove(temp_path)
            if isinstance(error, UnsafeStorageUriError | ArtifactMaterializationError):
                raise
            raise ArtifactMaterializationError(
                f"Failed to materialize local artifact: {uri}",
            ) from error


class StorageProviderArtifactReadAdapter:
    """Materialize provider-backed URI via StorageProvider.download_to_file."""

    def __init__(self, *, storage_provider: StorageProvider, schemes: tuple[str, ...]) -> None:
        self._storage_provider = storage_provider
        self._schemes = schemes

    def can_handle(self, uri: str) -> bool:
        if "sig=" in uri.lower():
            raise UnsafeStorageUriError("SAS-bearing URI is unsafe for artifact reads.")
        return uri.startswith(self._schemes)

    def materialize_to_temp(
        self,
        uri: str,
        *,
        temp_directory: str | Path | None = None,
    ) -> TemporaryArtifact:
        if "sig=" in uri.lower():
            raise UnsafeStorageUriError("SAS-bearing URI is unsafe for artifact reads.")
        if temp_directory is None:
            temp_path = _create_temp_path(uri)
        else:
            temp_path = _create_temp_path(uri, temp_directory=temp_directory)
        try:
            self._storage_provider.download_to_file(uri, temp_path)
            return TemporaryArtifact(local_path=temp_path, source_uri=uri)
        except Exception as error:
            _safe_remove(temp_path)
            if isinstance(error, UnsafeStorageUriError):
                raise
            raise ArtifactMaterializationError(
                f"Failed to materialize storage artifact: {uri}",
            ) from error


def _create_temp_path(uri: str, *, temp_directory: str | Path | None = None) -> str:
    suffix = Path(uri).suffix or ".bin"
    target_directory: str | None = None
    if temp_directory is not None:
        temp_directory_path = Path(temp_directory)
        temp_directory_path.mkdir(parents=True, exist_ok=True)
        target_directory = str(temp_directory_path)
    handle = tempfile.NamedTemporaryFile(
        prefix="artifact-read-",
        suffix=suffix,
        delete=False,
        dir=target_directory,
    )
    handle.close()
    return handle.name


def _safe_remove(path: str) -> None:
    target = Path(path)
    if target.exists() and target.is_file():
        target.unlink()
