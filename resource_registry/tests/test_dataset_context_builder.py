from __future__ import annotations

from datetime import UTC
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
import pytest
from openpyxl import Workbook

from resource_registry.services.dataset_context_builder import DatasetContextBuilder
from resource_registry.services.dataset_engine import DatasetEngine

EXPECTED_TOP_LEVEL_KEYS = {"source", "summary", "schema", "preview", "safety"}
EXPECTED_SCHEMA_COLUMN_NAMES = ["id", "name", "score"]
LARGE_PREVIEW_LIMIT = 1_000
EXPECTED_ROW_COUNT = 3
EXPECTED_COLUMN_COUNT = 3
EXPECTED_PREVIEW_COUNT = 2
STUB_ROW_COUNT = 42
STUB_COLUMN_COUNT = 1
STUB_REQUESTED_PREVIEW_LIMIT = 99
DEFAULT_PREVIEW_LIMIT = 2


@pytest.fixture
def csv_dataset_path(tmp_path: Path) -> Path:
    dataset_path = tmp_path / "sample.csv"
    dataset_path.write_text("id,name,score\n1,Ada,95.5\n2,Ben,88.0\n3,Cam,72.5\n", encoding="utf-8")
    return dataset_path


@pytest.fixture
def parquet_dataset_path(tmp_path: Path) -> Path:
    dataset_path = tmp_path / "sample.parquet"
    with duckdb.connect(database=":memory:") as connection:
        connection.execute(
            """
            CREATE TABLE sample_data AS
            SELECT 1 AS id, 'Ada' AS name, 95.5 AS score
            UNION ALL SELECT 2, 'Ben', 88.0
            UNION ALL SELECT 3, 'Cam', 72.5
            """
        )
        connection.execute("COPY sample_data TO ? (FORMAT PARQUET)", [str(dataset_path)])
    return dataset_path


@pytest.fixture
def xlsx_dataset_path(tmp_path: Path) -> Path:
    dataset_path = tmp_path / "sample.xlsx"
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Sheet1"
    worksheet.append(["id", "name", "score"])
    worksheet.append([1, "Ada", 95.5])
    worksheet.append([2, "Ben", 88.0])
    worksheet.append([3, "Cam", 72.5])
    workbook.save(dataset_path)
    workbook.close()
    return dataset_path


def test_build_context_for_csv_contains_expected_contract(csv_dataset_path: Path) -> None:
    context_builder = DatasetContextBuilder()

    context = context_builder.build_context(csv_dataset_path, preview_limit=2)

    assert set(context.keys()) == EXPECTED_TOP_LEVEL_KEYS
    assert context["source"]["format"] == "csv"
    assert context["source"]["file_name"] == "sample.csv"
    assert context["source"]["file_size_bytes"] > 0
    assert context["source"]["schema_detection"]["strategy"]
    assert context["summary"]["row_count"] == EXPECTED_ROW_COUNT
    assert context["summary"]["column_count"] == EXPECTED_COLUMN_COUNT
    assert [column["name"] for column in context["schema"]] == EXPECTED_SCHEMA_COLUMN_NAMES
    assert len(context["preview"]) == EXPECTED_PREVIEW_COUNT
    assert context["safety"] == {
        "raw_content_included": False,
        "preview_limited": True,
        "preview_limit": 2,
    }
    assert "raw_content" not in context


def test_build_context_for_csv_supports_timestamp_columns(tmp_path: Path) -> None:
    dataset_path = tmp_path / "timestamps.csv"
    dataset_path.write_text(
        "id,last_modified\n1,2026-07-22T17:13:31Z\n",
        encoding="utf-8",
    )

    context = DatasetContextBuilder().build_context(dataset_path, preview_limit=1)

    assert context["summary"]["row_count"] == 1
    assert context["preview"][0]["id"] == 1
    last_modified = context["preview"][0]["last_modified"]
    assert last_modified.astimezone(UTC).isoformat() == "2026-07-22T17:13:31+00:00"


def test_build_context_for_parquet_contains_expected_contract(parquet_dataset_path: Path) -> None:
    context_builder = DatasetContextBuilder()

    context = context_builder.build_context(parquet_dataset_path, preview_limit=2)

    assert set(context.keys()) == EXPECTED_TOP_LEVEL_KEYS
    assert context["source"]["format"] == "parquet"
    assert context["source"]["file_name"] == "sample.parquet"
    assert context["source"]["file_size_bytes"] > 0
    assert context["source"]["schema_detection"]["strategy"]
    assert context["summary"]["row_count"] == EXPECTED_ROW_COUNT
    assert context["summary"]["column_count"] == EXPECTED_COLUMN_COUNT
    assert [column["name"] for column in context["schema"]] == EXPECTED_SCHEMA_COLUMN_NAMES
    assert len(context["preview"]) == EXPECTED_PREVIEW_COUNT
    assert context["safety"] == {
        "raw_content_included": False,
        "preview_limited": True,
        "preview_limit": 2,
    }
    assert "raw_dataset_content" not in context


def test_build_context_for_xlsx_contains_expected_contract(xlsx_dataset_path: Path) -> None:
    context_builder = DatasetContextBuilder()

    context = context_builder.build_context(
        xlsx_dataset_path,
        preview_limit=DEFAULT_PREVIEW_LIMIT,
    )

    assert set(context.keys()) == EXPECTED_TOP_LEVEL_KEYS
    assert context["source"]["format"] == "xlsx"
    assert context["source"]["file_name"] == "sample.xlsx"
    assert context["source"]["schema_detection"]["strategy"]
    assert context["summary"]["row_count"] == EXPECTED_ROW_COUNT
    assert context["summary"]["column_count"] == EXPECTED_COLUMN_COUNT
    assert [column["name"] for column in context["schema"]] == EXPECTED_SCHEMA_COLUMN_NAMES
    assert len(context["preview"]) == EXPECTED_PREVIEW_COUNT
    assert context["safety"]["preview_limit"] == DEFAULT_PREVIEW_LIMIT


def test_build_context_respects_dataset_engine_preview_max(csv_dataset_path: Path) -> None:
    context_builder = DatasetContextBuilder()

    context = context_builder.build_context(csv_dataset_path, preview_limit=LARGE_PREVIEW_LIMIT)

    assert context["safety"]["preview_limit"] == DatasetEngine.MAX_PREVIEW_ROWS
    assert len(context["preview"]) <= DatasetEngine.MAX_PREVIEW_ROWS


class StubDatasetEngine:
    """Test double to assert DatasetContextBuilder composition behavior."""

    MAX_PREVIEW_ROWS = 5

    def __init__(self) -> None:
        self.calls: list[tuple[str, str | Path, int | None]] = []

    def get_row_count(self, path: str | Path) -> int:
        self.calls.append(("get_row_count", path, None))
        return 42

    def get_schema(self, path: str | Path) -> list[dict[str, str]]:
        self.calls.append(("get_schema", path, None))
        return [{"name": "id", "type": "BIGINT"}]

    def get_schema_detection(self, path: str | Path):
        self.calls.append(("get_schema_detection", path, None))
        return SimpleNamespace(
            columns=[{"name": "id", "type": "BIGINT"}],
            metadata={"strategy": "stub"},
            warnings=(),
            confidence=1.0,
            strategy_name="StubSchemaDetectionStrategy",
        )

    def preview(self, path: str | Path, limit: int = 10) -> list[dict[str, Any]]:
        self.calls.append(("preview", path, limit))
        return [{"id": 1}]


def test_build_context_composes_engine_via_injection(tmp_path: Path) -> None:
    stub_dataset_path = tmp_path / "stub.csv"
    stub_dataset_path.write_text("id\n1\n", encoding="utf-8")
    stub_engine = StubDatasetEngine()
    context_builder = DatasetContextBuilder(dataset_engine=stub_engine)

    context = context_builder.build_context(
        stub_dataset_path,
        preview_limit=STUB_REQUESTED_PREVIEW_LIMIT,
    )

    assert context["summary"]["row_count"] == STUB_ROW_COUNT
    assert context["summary"]["column_count"] == STUB_COLUMN_COUNT
    assert context["schema"] == [{"name": "id", "type": "BIGINT"}]
    assert context["preview"] == [{"id": 1}]
    assert context["safety"]["preview_limit"] == stub_engine.MAX_PREVIEW_ROWS
    assert stub_engine.calls == [
        ("get_schema_detection", stub_dataset_path, None),
        ("get_row_count", stub_dataset_path, None),
        ("preview", stub_dataset_path, stub_engine.MAX_PREVIEW_ROWS),
    ]
