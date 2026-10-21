from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import duckdb
import pytest
from openpyxl import Workbook

from resource_registry.services.dataset_engine import DatasetEngine
from resource_registry.services.dataset_source_adapters import DatasetSourceAdapter

EXPECTED_ROW_COUNT = 3
PREVIEW_LIMIT = 2
SECOND_ROW_INDEX = 1
LARGE_PREVIEW_REQUEST = 1_000


@pytest.fixture
def dataset_engine() -> DatasetEngine:
    return DatasetEngine()


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
        connection.execute(
            "COPY sample_data TO ? (FORMAT PARQUET)",
            [str(dataset_path)],
        )
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


def test_get_row_count_supports_csv(dataset_engine: DatasetEngine, csv_dataset_path: Path) -> None:
    assert dataset_engine.get_row_count(csv_dataset_path) == EXPECTED_ROW_COUNT


def test_get_row_count_supports_parquet(
    dataset_engine: DatasetEngine,
    parquet_dataset_path: Path,
) -> None:
    assert dataset_engine.get_row_count(parquet_dataset_path) == EXPECTED_ROW_COUNT


def test_get_row_count_supports_xlsx(
    dataset_engine: DatasetEngine,
    xlsx_dataset_path: Path,
) -> None:
    assert dataset_engine.get_row_count(xlsx_dataset_path) == EXPECTED_ROW_COUNT


def test_get_schema_returns_column_metadata_for_csv(
    dataset_engine: DatasetEngine,
    csv_dataset_path: Path,
) -> None:
    schema = dataset_engine.get_schema(csv_dataset_path)

    assert [column["name"] for column in schema] == ["id", "name", "score"]
    assert all(column["type"] for column in schema)


def test_get_schema_returns_column_metadata_for_parquet(
    dataset_engine: DatasetEngine,
    parquet_dataset_path: Path,
) -> None:
    schema = dataset_engine.get_schema(parquet_dataset_path)

    assert [column["name"] for column in schema] == ["id", "name", "score"]
    assert all(column["type"] for column in schema)


def test_get_schema_returns_column_metadata_for_xlsx(
    dataset_engine: DatasetEngine,
    xlsx_dataset_path: Path,
) -> None:
    schema = dataset_engine.get_schema(xlsx_dataset_path)

    assert [column["name"] for column in schema] == ["id", "name", "score"]
    assert all(column["type"] for column in schema)


def test_preview_returns_limited_rows_for_csv(
    dataset_engine: DatasetEngine,
    csv_dataset_path: Path,
) -> None:
    preview_rows = dataset_engine.preview(csv_dataset_path, limit=PREVIEW_LIMIT)

    assert len(preview_rows) == PREVIEW_LIMIT
    assert preview_rows[0]["id"] == 1
    assert preview_rows[0]["name"] == "Ada"


def test_preview_returns_limited_rows_for_parquet(
    dataset_engine: DatasetEngine,
    parquet_dataset_path: Path,
) -> None:
    preview_rows = dataset_engine.preview(parquet_dataset_path, limit=PREVIEW_LIMIT)

    assert len(preview_rows) == PREVIEW_LIMIT
    assert preview_rows[SECOND_ROW_INDEX]["id"] == PREVIEW_LIMIT
    assert preview_rows[SECOND_ROW_INDEX]["name"] == "Ben"


def test_preview_returns_limited_rows_for_xlsx(
    dataset_engine: DatasetEngine,
    xlsx_dataset_path: Path,
) -> None:
    preview_rows = dataset_engine.preview(xlsx_dataset_path, limit=PREVIEW_LIMIT)

    assert len(preview_rows) == PREVIEW_LIMIT
    assert preview_rows[0]["id"] == 1
    assert preview_rows[0]["name"] == "Ada"


def test_preview_caps_large_limit_to_max_preview_rows(
    dataset_engine: DatasetEngine,
    parquet_dataset_path: Path,
) -> None:
    preview_rows = dataset_engine.preview(parquet_dataset_path, limit=LARGE_PREVIEW_REQUEST)

    assert len(preview_rows) == EXPECTED_ROW_COUNT


def test_xlsm_file_rejected(dataset_engine: DatasetEngine, tmp_path: Path) -> None:
    macro_file = tmp_path / "sample.xlsm"
    macro_file.write_text("macro", encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported dataset format"):
        dataset_engine.get_row_count(macro_file)


class StubCsvAdapter(DatasetSourceAdapter):
    def __init__(self) -> None:
        self.calls: list[str] = []

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() == ".csv"

    def materialize(self, path: Path):
        @contextmanager
        def _cm():
            self.calls.append(str(path))
            yield path

        return _cm()


def test_dataset_engine_composes_source_adapters(
    csv_dataset_path: Path,
) -> None:
    stub_adapter = StubCsvAdapter()
    engine = DatasetEngine(adapters=(stub_adapter,))

    assert engine.get_row_count(csv_dataset_path) == EXPECTED_ROW_COUNT
    assert stub_adapter.calls == [str(csv_dataset_path)]
