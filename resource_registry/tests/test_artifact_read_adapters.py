from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from resource_registry.storage.exceptions import UnsafeStorageUriError
from resource_registry.storage.local import LocalStorageProvider
from resource_registry.storage.read_adapters import (
    LocalArtifactReadAdapter,
    StorageProviderArtifactReadAdapter,
)


def test_local_adapter_handles_local_uri_and_cleans_temp(tmp_path: Path) -> None:
    base_dir = tmp_path / "store"
    source_file = tmp_path / "sample.csv"
    source_file.write_text("id,name\n1,Ada\n", encoding="utf-8")
    provider = LocalStorageProvider(base_directory=base_dir)
    provider.put(str(source_file), "datasets/sample.csv")
    adapter = LocalArtifactReadAdapter(storage_provider=provider)

    temporary = adapter.materialize_to_temp("local://datasets/sample.csv")
    temp_path = Path(temporary.local_path)
    assert temp_path.exists()
    temporary.cleanup()
    assert not temp_path.exists()


def test_local_adapter_rejects_sas_uri() -> None:
    adapter = LocalArtifactReadAdapter(storage_provider=MagicMock())
    with pytest.raises(UnsafeStorageUriError):
        adapter.can_handle("https://x.blob.core.windows.net/c/f.csv?sig=secret")


def test_storage_provider_adapter_materializes_and_cleans_temp() -> None:
    provider = MagicMock()

    def _download(_uri, local_path):
        Path(local_path).write_text("id,name\n1,Ada\n", encoding="utf-8")
        return MagicMock()

    provider.download_to_file.side_effect = _download
    adapter = StorageProviderArtifactReadAdapter(storage_provider=provider, schemes=("azure://",))

    temporary = adapter.materialize_to_temp("azure://media/path/sample.csv")
    temp_path = Path(temporary.local_path)
    assert temp_path.exists()
    temporary.cleanup()
    assert not temp_path.exists()


def test_storage_provider_adapter_rejects_sas_uri() -> None:
    adapter = StorageProviderArtifactReadAdapter(storage_provider=MagicMock(), schemes=("azure://",))
    with pytest.raises(UnsafeStorageUriError):
        adapter.can_handle("https://x.blob.core.windows.net/c/f.csv?sig=secret")
