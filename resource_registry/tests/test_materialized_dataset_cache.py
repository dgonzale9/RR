from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

from resource_registry.read.dtos import DatasetArtifactSummary
from resource_registry.services.materialized_dataset_cache import (
    DatasetCacheMaterializationError,
    MaterializedDatasetCache,
)
from resource_registry.storage.read_adapters import TemporaryArtifact

EXPECTED_TWO_CALLS = 2
EXPECTED_TWO_RESULTS = 2


class _AdapterSpy:
    def __init__(
        self,
        *,
        source_path: Path,
        fail: bool = False,
        sleep_seconds: float = 0.0,
    ) -> None:
        self.source_path = source_path
        self.fail = fail
        self.sleep_seconds = sleep_seconds
        self.calls = 0

    def can_handle(self, uri: str) -> bool:
        return uri == str(self.source_path)

    def materialize_to_temp(
        self,
        uri: str,
        *,
        temp_directory: str | Path | None = None,
    ) -> TemporaryArtifact:
        _ = uri
        self.calls += 1
        if self.fail:
            raise RuntimeError("materialize failure")
        if self.sleep_seconds > 0:
            time.sleep(self.sleep_seconds)
        target_directory = (
            Path(temp_directory) if temp_directory is not None else self.source_path.parent
        )
        target_directory.mkdir(parents=True, exist_ok=True)
        temp_path = target_directory / f"adapter-{self.calls}.parquet"
        temp_path.write_bytes(self.source_path.read_bytes())
        return TemporaryArtifact(local_path=str(temp_path), source_uri=str(self.source_path))


def _artifact(
    path: Path,
    *,
    artifact_id: str = "artifact-1",
    checksum: str | None = "checksum-1",
) -> DatasetArtifactSummary:
    return DatasetArtifactSummary(
        id=artifact_id,
        dataset_version_id="version-1",
        tenant_id="tenant-1",
        artifact_role="SNAPSHOT",
        uri=str(path),
        format="PARQUET",
        size_bytes=path.stat().st_size,
        checksum=checksum,
        storage_provider="local",
        lifecycle_status="ACTIVE",
        created_at=datetime.utcnow().isoformat(),
        metadata_json={"is_canonical": True},
    )


@pytest.fixture
def sample_file(tmp_path: Path) -> Path:
    file_path = tmp_path / "dataset.parquet"
    file_path.write_bytes(b"sample-parquet-content")
    return file_path


def test_first_materialization_creates_cache_entry(sample_file: Path, tmp_path: Path) -> None:
    cache = MaterializedDatasetCache(
        cache_directory=tmp_path / "cache",
        ttl_seconds=600,
        max_bytes=1024 * 1024,
    )
    adapter = _AdapterSpy(source_path=sample_file)
    result = cache.get_or_materialize(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        version_id="version-1",
        artifact=_artifact(sample_file),
        adapter=adapter,
    )

    assert result.cache_hit is False
    assert Path(result.local_path).exists()
    assert adapter.calls == 1


def test_second_materialization_hits_cache(sample_file: Path, tmp_path: Path) -> None:
    cache = MaterializedDatasetCache(
        cache_directory=tmp_path / "cache-hit",
        ttl_seconds=600,
        max_bytes=1024 * 1024,
    )
    adapter = _AdapterSpy(source_path=sample_file)
    first = cache.get_or_materialize(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        version_id="version-1",
        artifact=_artifact(sample_file),
        adapter=adapter,
    )
    second = cache.get_or_materialize(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        version_id="version-1",
        artifact=_artifact(sample_file),
        adapter=adapter,
    )

    assert first.cache_hit is False
    assert second.cache_hit is True
    assert first.local_path == second.local_path
    assert adapter.calls == 1


def test_cache_key_changes_with_version_and_artifact(sample_file: Path, tmp_path: Path) -> None:
    cache = MaterializedDatasetCache(
        cache_directory=tmp_path / "cache-key",
        ttl_seconds=600,
        max_bytes=1024 * 1024,
    )
    first_key = cache.build_cache_key(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        version_id="version-1",
        artifact=_artifact(sample_file, artifact_id="artifact-1", checksum="checksum-1"),
    )
    second_key = cache.build_cache_key(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        version_id="version-2",
        artifact=_artifact(sample_file, artifact_id="artifact-2", checksum="checksum-2"),
    )

    assert first_key != second_key


def test_expired_entry_is_refreshed(sample_file: Path, tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache-expired"
    cache = MaterializedDatasetCache(
        cache_directory=cache_dir,
        ttl_seconds=1,
        max_bytes=1024 * 1024,
    )
    adapter = _AdapterSpy(source_path=sample_file)
    first = cache.get_or_materialize(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        version_id="version-1",
        artifact=_artifact(sample_file),
        adapter=adapter,
    )
    metadata_path = cache_dir / f"{first.cache_key}.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["last_accessed_at"] = time.time() - 10
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    second = cache.get_or_materialize(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        version_id="version-1",
        artifact=_artifact(sample_file),
        adapter=adapter,
    )

    assert second.cache_hit is False
    assert adapter.calls == EXPECTED_TWO_CALLS


def test_lru_eviction_happens_when_max_size_exceeded(tmp_path: Path) -> None:
    file_one = tmp_path / "one.parquet"
    file_two = tmp_path / "two.parquet"
    file_one.write_bytes(b"a" * 80)
    file_two.write_bytes(b"b" * 80)
    cache_dir = tmp_path / "cache-lru"
    cache = MaterializedDatasetCache(cache_directory=cache_dir, ttl_seconds=600, max_bytes=120)
    adapter_one = _AdapterSpy(source_path=file_one)
    adapter_two = _AdapterSpy(source_path=file_two)

    first = cache.get_or_materialize(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        version_id="version-1",
        artifact=_artifact(file_one, artifact_id="a1", checksum="c1"),
        adapter=adapter_one,
    )
    _ = cache.get_or_materialize(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        version_id="version-2",
        artifact=_artifact(file_two, artifact_id="a2", checksum="c2"),
        adapter=adapter_two,
    )

    assert not Path(first.local_path).exists()


def test_per_key_lock_prevents_duplicate_materialization(sample_file: Path, tmp_path: Path) -> None:
    cache = MaterializedDatasetCache(
        cache_directory=tmp_path / "cache-lock",
        ttl_seconds=600,
        max_bytes=1024 * 1024,
    )
    adapter = _AdapterSpy(source_path=sample_file, sleep_seconds=0.1)
    results: list[str] = []

    def _worker() -> None:
        value = cache.get_or_materialize(
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            version_id="version-1",
            artifact=_artifact(sample_file),
            adapter=adapter,
        )
        results.append(value.local_path)

    thread_one = threading.Thread(target=_worker)
    thread_two = threading.Thread(target=_worker)
    thread_one.start()
    thread_two.start()
    thread_one.join(timeout=1.0)
    thread_two.join(timeout=1.0)

    assert len(results) == EXPECTED_TWO_RESULTS
    assert results[0] == results[1]
    assert adapter.calls == 1


def test_lock_released_after_failed_materialization(sample_file: Path, tmp_path: Path) -> None:
    cache = MaterializedDatasetCache(
        cache_directory=tmp_path / "cache-lock-release",
        ttl_seconds=600,
        max_bytes=1024 * 1024,
    )
    failing_adapter = _AdapterSpy(source_path=sample_file, fail=True)
    with pytest.raises(DatasetCacheMaterializationError):
        cache.get_or_materialize(
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            version_id="version-1",
            artifact=_artifact(sample_file),
            adapter=failing_adapter,
        )

    successful_adapter = _AdapterSpy(source_path=sample_file)
    result = cache.get_or_materialize(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        version_id="version-1",
        artifact=_artifact(sample_file),
        adapter=successful_adapter,
    )
    assert Path(result.local_path).exists()


def test_cache_paths_stay_inside_configured_directory(sample_file: Path, tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache-safe-path"
    cache = MaterializedDatasetCache(
        cache_directory=cache_dir,
        ttl_seconds=600,
        max_bytes=1024 * 1024,
    )
    adapter = _AdapterSpy(source_path=sample_file)
    result = cache.get_or_materialize(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        version_id="version-1",
        artifact=_artifact(sample_file),
        adapter=adapter,
    )
    assert Path(result.local_path).resolve().is_relative_to(cache_dir.resolve())


def test_cache_metadata_does_not_store_signed_urls_or_credentials(tmp_path: Path) -> None:
    source_file = tmp_path / "source.parquet"
    source_file.write_bytes(b"content")
    artifact = _artifact(source_file)
    artifact = DatasetArtifactSummary(
        **{
            **artifact.__dict__,
            "uri": "https://blob/path/file.parquet?sig=abc&token=xyz",
        },
    )
    cache_dir = tmp_path / "cache-metadata"
    cache = MaterializedDatasetCache(
        cache_directory=cache_dir,
        ttl_seconds=600,
        max_bytes=1024 * 1024,
    )
    adapter = _AdapterSpy(source_path=source_file)
    result = cache.get_or_materialize(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        version_id="version-1",
        artifact=artifact,
        adapter=adapter,
    )
    metadata_raw = (cache_dir / f"{result.cache_key}.json").read_text(encoding="utf-8").lower()
    assert "sig=" not in metadata_raw
    assert "token" not in metadata_raw
    assert "secret" not in metadata_raw
    assert "connection_string" not in metadata_raw
