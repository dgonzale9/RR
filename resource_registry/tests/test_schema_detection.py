from __future__ import annotations

from pathlib import Path

import duckdb
from openpyxl import Workbook

from resource_registry.services.schema_detection import (
    ParquetSchemaDetectionStrategy,
    SchemaDetectionStrategyResolver,
)


def test_parquet_strategy_reads_schema_from_metadata(tmp_path: Path) -> None:
    source_path = tmp_path / "sample.parquet"
    with duckdb.connect(database=":memory:") as connection:
        connection.execute(
            """
            COPY (
                SELECT 1::BIGINT AS id, 'Ada'::VARCHAR AS name, 95.5::DOUBLE AS score
            ) TO ? (FORMAT PARQUET)
            """,
            [str(source_path)],
        )
    strategy = ParquetSchemaDetectionStrategy()

    result = strategy.detect_schema(source_path=str(source_path))

    assert result.strategy_name == "ParquetSchemaDetectionStrategy"
    assert result.metadata["strategy"] == "parquet_metadata"
    assert [column["name"] for column in result.columns] == ["id", "name", "score"]
    assert [column["type"] for column in result.columns] == ["BIGINT", "VARCHAR", "DOUBLE"]


def test_resolver_returns_excel_detection_with_header_metadata(tmp_path: Path) -> None:
    source_path = tmp_path / "sheet.xlsx"
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(["Campaign Segment Summary"])
    worksheet.append(["customer_id", "country", "total_spend"])
    worksheet.append([1, "Costa Rica", 100.0])
    workbook.save(source_path)
    workbook.close()

    resolver = SchemaDetectionStrategyResolver()
    result = resolver.detect(
        source_path=str(source_path),
        source_format="xlsx",
    )

    assert result.strategy_name == "ExcelSchemaDetectionStrategy"
    assert int(result.metadata["detected_header_row_index"]) == 1
    assert [column["name"] for column in result.columns] == [
        "customer_id",
        "country",
        "total_spend",
    ]


def test_csv_json_column_requires_full_column_validation(tmp_path: Path) -> None:
    source_path = tmp_path / "records.csv"
    source_path.write_text(
        'payload,label\n"{""customer"": {""id"": 1}}",first\n"[1, 2]",second\n',
        encoding="utf-8",
    )
    result = SchemaDetectionStrategyResolver().detect(
        source_path=str(source_path), source_format="csv"
    )
    assert result.columns[0] == {"name": "payload", "type": "JSON"}
