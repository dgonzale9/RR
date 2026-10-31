from __future__ import annotations

import hashlib
import json
import logging
import shutil
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

from resource_registry.cleanup.cache_storage import reserve_cache_files
from resource_registry.read.dtos import DatasetArtifactSummary
from resource_registry.storage.read_adapters import ArtifactReadAdapter

_logger = logging.getLogger("app-logger")
_LOCK_INDEX_GUARD = Lock()
_LOCK_INDEX: dict[str, Lock] = {}


class DatasetCacheMaterializationError(RuntimeError):
    """Raised when a cache entry cannot be safely materialized."""


class DatasetCacheStorageExceededError(RuntimeError):
    """Raised when cache storage limits are exceeded."""


@dataclass(frozen=True)
class MaterializedDatasetCacheResult:
    local_path: str
    cache_key: str
    cache_hit: bool


@dataclass(frozen=True)
class _CacheEntry:
    cache_key: str
    data_path: Path
    metadata_path: Path
    file_size_bytes: int
    created_at: float
    last_accessed_at: float


class MaterializedDatasetCache:
    """Controlled local cache for dataset artifact materialization."""

    def __init__(
        self,
        *,
        cache_directory: str | Path,
        ttl_seconds: int,
        max_bytes: int,
    ) -> None:
        self._cache_directory = Path(cache_directory).resolve()
        self._cache_directory.mkdir(parents=True, exist_ok=True)
        self._ttl_seconds = max(int(ttl_seconds), 0)
        self._max_bytes = max(int(max_bytes), 1)

    def get_or_materialize(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        version_id: str,
        artifact: DatasetArtifactSummary,
        adapter: ArtifactReadAdapter,
    ) -> MaterializedDatasetCacheResult:
        cache_key = self.build_cache_key(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            version_id=version_id,
            artifact=artifact,
        )
        data_path = self._cache_directory / f"{cache_key}{self._suffix_for_artifact(artifact)}"
        metadata_path = self._cache_directory / f"{cache_key}.json"
        self._evict_expired_entries()

        cached = self._load_valid_entry(data_path=data_path, metadata_path=metadata_path)
        if cached is not None:
            self._touch_entry(cached)
            self._log_event(
                "cache_hit",
                cache_key=cache_key,
                file_size_bytes=cached.file_size_bytes,
            )
            return MaterializedDatasetCacheResult(
                local_path=str(cached.data_path),
                cache_key=cache_key,
                cache_hit=True,
            )

        lock = self._lock_for_key(cache_key)
        with lock:
            cached = self._load_valid_entry(data_path=data_path, metadata_path=metadata_path)
            if cached is not None:
                self._touch_entry(cached)
                self._log_event(
                    "cache_hit",
                    cache_key=cache_key,
                    file_size_bytes=cached.file_size_bytes,
                )
                return MaterializedDatasetCacheResult(
                    local_path=str(cached.data_path),
                    cache_key=cache_key,
                    cache_hit=True,
                )

            staging_directory = Path(
                tempfile.mkdtemp(
                    prefix=f"dataset-cache-{cache_key[:12]}-",
                    dir=self._cache_directory,
                ),
            )
            temporary_artifact = None
            try:
                temporary_artifact = adapter.materialize_to_temp(
                    artifact.uri,
                    temp_directory=staging_directory,
                )
                source_path = Path(temporary_artifact.local_path).resolve()
                source_size = source_path.stat().st_size
                self._ensure_capacity(required_bytes=source_size)
                with reserve_cache_files(
                    dataset_id=dataset_id,
                    artifact_id=str(artifact.id),
                    data_path=data_path,
                    metadata_path=metadata_path,
                ):
                    shutil.move(str(source_path), str(data_path))
                    self._assert_inside_cache(data_path)
                    now = time.time()
                    self._write_metadata(
                        metadata_path=metadata_path,
                        payload={
                            "cache_key": cache_key,
                            "tenant_id": tenant_id,
                            "dataset_id": dataset_id,
                            "version_id": version_id,
                            "artifact_id": str(artifact.id),
                            "file_size_bytes": source_size,
                            "checksum": artifact.checksum,
                            "created_at": now,
                            "last_accessed_at": now,
                        },
                    )
                self._enforce_max_size()
                self._log_event("cache_miss", cache_key=cache_key, file_size_bytes=source_size)
                return MaterializedDatasetCacheResult(
                    local_path=str(data_path),
                    cache_key=cache_key,
                    cache_hit=False,
                )
            except DatasetCacheStorageExceededError:
                raise
            except Exception as error:
                self._safe_remove(data_path)
                self._safe_remove(metadata_path)
                raise DatasetCacheMaterializationError(
                    "Unable to prepare dataset for query execution.",
                ) from error
            finally:
                if temporary_artifact is not None:
                    try:
                        temporary_artifact.cleanup()
                    except Exception:
                        pass
                shutil.rmtree(staging_directory, ignore_errors=True)

    def build_cache_key(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        version_id: str,
        artifact: DatasetArtifactSummary,
    ) -> str:
        raw = "|".join(
            [
                tenant_id.strip(),
                dataset_id.strip(),
                version_id.strip(),
                str(artifact.id),
                str(artifact.checksum or ""),
                str(artifact.uri or ""),
                str(artifact.format or ""),
            ],
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _suffix_for_artifact(self, artifact: DatasetArtifactSummary) -> str:
        suffix = Path(str(artifact.uri or "")).suffix.lower()
        if suffix:
            return suffix
        normalized_format = str(artifact.format or "").strip().lower()
        if normalized_format == "parquet":
            return ".parquet"
        return ".bin"

    def _lock_for_key(self, cache_key: str) -> Lock:
        with _LOCK_INDEX_GUARD:
            lock = _LOCK_INDEX.get(cache_key)
            if lock is None:
                lock = Lock()
                _LOCK_INDEX[cache_key] = lock
        return lock

    def _load_valid_entry(self, *, data_path: Path, metadata_path: Path) -> _CacheEntry | None:
        if not data_path.exists() or not data_path.is_file():
            self._safe_remove(metadata_path)
            return None
        if not metadata_path.exists() or not metadata_path.is_file():
            self._safe_remove(data_path)
            return None
        self._assert_inside_cache(data_path)
        self._assert_inside_cache(metadata_path)

        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except Exception:
            self._safe_remove(data_path)
            self._safe_remove(metadata_path)
            return None

        created_at = self._to_float(metadata.get("created_at"))
        last_accessed_at = self._to_float(metadata.get("last_accessed_at"))
        file_size_bytes = int(data_path.stat().st_size)
        if created_at is None or last_accessed_at is None:
            self._safe_remove(data_path)
            self._safe_remove(metadata_path)
            return None
        if self._is_expired(last_accessed_at):
            self._evict_entry(data_path=data_path, metadata_path=metadata_path, reason="ttl")
            return None
        return _CacheEntry(
            cache_key=str(metadata.get("cache_key") or ""),
            data_path=data_path,
            metadata_path=metadata_path,
            file_size_bytes=file_size_bytes,
            created_at=created_at,
            last_accessed_at=last_accessed_at,
        )

    def _touch_entry(self, entry: _CacheEntry) -> None:
        try:
            metadata = json.loads(entry.metadata_path.read_text(encoding="utf-8"))
        except Exception:
            metadata = {}
        metadata["last_accessed_at"] = time.time()
        self._write_metadata(metadata_path=entry.metadata_path, payload=metadata)

    def _evict_expired_entries(self) -> None:
        for entry in self._list_entries():
            if self._is_expired(entry.last_accessed_at):
                self._evict_entry(
                    data_path=entry.data_path,
                    metadata_path=entry.metadata_path,
                    reason="ttl",
                )

    def _enforce_max_size(self) -> None:
        entries = sorted(self._list_entries(), key=lambda entry: entry.last_accessed_at)
        total_bytes = sum(entry.file_size_bytes for entry in entries)
        while total_bytes > self._max_bytes and entries:
            oldest = entries.pop(0)
            total_bytes -= oldest.file_size_bytes
            self._evict_entry(
                data_path=oldest.data_path,
                metadata_path=oldest.metadata_path,
                reason="lru",
            )

    def _ensure_capacity(self, *, required_bytes: int) -> None:
        entries = sorted(self._list_entries(), key=lambda entry: entry.last_accessed_at)
        current_size = sum(entry.file_size_bytes for entry in entries)
        while current_size + required_bytes > self._max_bytes and entries:
            oldest = entries.pop(0)
            current_size -= oldest.file_size_bytes
            self._evict_entry(
                data_path=oldest.data_path,
                metadata_path=oldest.metadata_path,
                reason="lru",
            )
        if current_size + required_bytes > self._max_bytes:
            raise DatasetCacheStorageExceededError(
                "Dataset cache storage limit exceeded.",
            )

    def _list_entries(self) -> list[_CacheEntry]:
        entries: list[_CacheEntry] = []
        for metadata_path in self._cache_directory.glob("*.json"):
            data_candidate = self._cache_directory / f"{metadata_path.stem}.parquet"
            if not data_candidate.exists():
                matches = list(self._cache_directory.glob(f"{metadata_path.stem}.*"))
                data_candidate = next(
                    (
                        candidate
                        for candidate in matches
                        if candidate.is_file() and candidate.suffix != ".json"
                    ),
                    data_candidate,
                )
            entry = self._load_valid_entry(data_path=data_candidate, metadata_path=metadata_path)
            if entry is not None:
                entries.append(entry)
        return entries

    def _evict_entry(self, *, data_path: Path, metadata_path: Path, reason: str) -> None:
        file_size = data_path.stat().st_size if data_path.exists() else 0
        self._safe_remove(data_path)
        self._safe_remove(metadata_path)
        self._log_event(
            "cache_eviction",
            cache_key=metadata_path.stem,
            file_size_bytes=file_size,
            reason=reason,
        )

    def _assert_inside_cache(self, path: Path) -> None:
        resolved = path.resolve()
        if not resolved.is_relative_to(self._cache_directory):
            raise DatasetCacheMaterializationError("Unable to prepare dataset for query execution.")

    def _is_expired(self, last_accessed_at: float) -> bool:
        if self._ttl_seconds <= 0:
            return False
        return (time.time() - last_accessed_at) > self._ttl_seconds

    def _write_metadata(self, *, metadata_path: Path, payload: dict[str, Any]) -> None:
        sanitized_payload = {
            key: value
            for key, value in payload.items()
            if key
            not in {
                "uri",
                "storage_uri",
                "signed_url",
                "connection_string",
                "token",
                "secret",
            }
        }
        self._assert_inside_cache(metadata_path)
        metadata_path.write_text(json.dumps(sanitized_payload, sort_keys=True), encoding="utf-8")

    def _safe_remove(self, path: Path) -> None:
        try:
            if path.exists() and path.is_file():
                self._assert_inside_cache(path)
                path.unlink()
        except Exception:
            pass

    def _to_float(self, value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _log_event(self, event: str, **details: Any) -> None:
        _logger.info(
            "Dataset cache event.",
            extra={
                "event": event,
                "timestamp": datetime.now(tz=UTC).isoformat(),
                **details,
            },
        )
