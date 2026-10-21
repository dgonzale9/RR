from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from resource_registry.application.commands import RegisterDatasetCommand
from resource_registry.application.exceptions import ValidationError
from resource_registry.application.storage_strategy import (
    DatasetStorageMode,
    DatasetStorageStrategy,
)


def _build_command(path: Path, *, storage_mode: str = "LOCAL_REFERENCE") -> RegisterDatasetCommand:
    return RegisterDatasetCommand(
        tenant_id="11111111-1111-1111-1111-111111111111",
        slug="sales-data",
        name="Sales Data",
        path=str(path),
        source_type="FILE_UPLOAD",
        storage_mode=storage_mode,
        storage_provider="AZURE_BLOB",
    )


def test_storage_strategy_local_reference_mode(tmp_path: Path) -> None:
    path = tmp_path / "dataset.csv"
    path.write_text("id\n1\n", encoding="utf-8")
    strategy = DatasetStorageStrategy(storage_factory=MagicMock())

    plan = strategy.create_plan(command=_build_command(path), version_number=1)

    assert plan.mode == DatasetStorageMode.LOCAL_REFERENCE
    assert plan.should_upload is False
    assert plan.provider is None
    assert plan.destination_path is None


def test_storage_strategy_managed_upload_mode(tmp_path: Path) -> None:
    path = tmp_path / "dataset.parquet"
    path.write_text("parquet-placeholder", encoding="utf-8")
    storage_factory = MagicMock()
    storage_factory.create_provider.return_value = MagicMock(PROVIDER_NAME="azure_blob")
    strategy = DatasetStorageStrategy(storage_factory=storage_factory)

    plan = strategy.create_plan(
        command=_build_command(path, storage_mode="MANAGED_UPLOAD"),
        version_number=3,
    )

    assert plan.mode == DatasetStorageMode.MANAGED_UPLOAD
    assert plan.should_upload is True
    assert plan.destination_path == (
        "datasets/11111111-1111-1111-1111-111111111111/sales-data/v3/original.parquet"
    )
    storage_factory.create_provider.assert_called_once_with("azure_blob")


def test_storage_strategy_generates_deterministic_safe_path() -> None:
    strategy = DatasetStorageStrategy(storage_factory=MagicMock())

    destination_path = strategy.build_destination_path(
        tenant_id="11111111-1111-1111-1111-111111111111",
        dataset_slug="Sales Data!!",
        version_number=4,
        source_path="/tmp/source.csv",
    )

    assert destination_path == (
        "datasets/11111111-1111-1111-1111-111111111111/Sales-Data/v4/original.csv"
    )
    assert ".." not in destination_path
    assert not destination_path.startswith("/")


def test_storage_strategy_rejects_unsafe_segments() -> None:
    strategy = DatasetStorageStrategy(storage_factory=MagicMock())
    with pytest.raises(ValidationError):
        strategy.build_destination_path(
            tenant_id="!!!",
            dataset_slug="...",
            version_number=1,
            source_path="/tmp/source.csv",
        )
