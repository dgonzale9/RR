from __future__ import annotations

from pathlib import Path

import duckdb
from openpyxl import Workbook

from resource_registry.services.canonical_dataset_builder import CanonicalDatasetBuilder

EXPECTED_TWO_ROWS = 2


def _query_row_count(path: str) -> int:
    with duckdb.connect(database=":memory:") as connection:
        return int(connection.execute("SELECT COUNT(*) FROM read_parquet(?)", [path]).fetchone()[0])


def test_csv_normalization_builds_canonical_parquet(tmp_path: Path) -> None:
    source = tmp_path / "dataset.csv"
    source.write_text("id,name\n1,Ada\n2,Ben\n", encoding="utf-8")
    builder = CanonicalDatasetBuilder()

    with builder.build(source_path=str(source), source_format="csv") as canonical:
        assert Path(canonical.canonical_path).exists()
        assert _query_row_count(canonical.canonical_path) == EXPECTED_TWO_ROWS
        assert canonical.metadata["conversion_applied"] is True
        assert canonical.source_format == "csv"


def test_parquet_normalization_avoids_unnecessary_conversion(tmp_path: Path) -> None:
    source = tmp_path / "dataset.parquet"
    with duckdb.connect(database=":memory:") as connection:
        connection.execute(
            "COPY (SELECT 1 AS id UNION ALL SELECT 2) TO ? (FORMAT PARQUET)",
            [str(source)],
        )
    builder = CanonicalDatasetBuilder()

    with builder.build(source_path=str(source), source_format="parquet") as canonical:
        assert Path(canonical.canonical_path).exists()
        assert _query_row_count(canonical.canonical_path) == EXPECTED_TWO_ROWS
        assert canonical.metadata["conversion_applied"] is False
        assert canonical.source_format == "parquet"


def test_excel_normalization_builds_canonical_parquet(tmp_path: Path) -> None:
    source = tmp_path / "dataset.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Sheet1"
    sheet.append(["id", "name"])
    sheet.append([1, "Ada"])
    sheet.append([2, "Ben"])
    workbook.save(source)
    workbook.close()
    builder = CanonicalDatasetBuilder()

    with builder.build(source_path=str(source), source_format="xlsx") as canonical:
        assert Path(canonical.canonical_path).exists()
        assert _query_row_count(canonical.canonical_path) == EXPECTED_TWO_ROWS
        assert canonical.metadata["conversion_applied"] is True
        assert canonical.metadata["excel_sheet_name"] == "Sheet1"
        assert canonical.source_format == "xlsx"
