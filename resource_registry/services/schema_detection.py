from __future__ import annotations

import csv
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import pyarrow.types as pyarrow_types
from pyarrow import parquet as pyarrow_parquet

from resource_registry.services.dataset_formats import normalize_dataset_format
from resource_registry.services.duckdb_service import (
    DuckDbConnectionProvider,
    InMemoryDuckDbConnectionProvider,
)
from resource_registry.services.excel_dataset_adapter import ExcelDatasetAdapter
from resource_registry.services.json_dataset import (
    JsonDatasetPolicy,
    StrictJsonDatasetPolicy,
    StrictJsonLinesDatasetPolicy,
)


@dataclass(frozen=True)
class SchemaDetectionResult:
    """Represent normalized schema detection output across source formats."""

    columns: list[dict[str, str]]
    metadata: dict[str, Any]
    warnings: tuple[str, ...]
    confidence: float
    strategy_name: str


class SchemaDetectionStrategy(ABC):
    """Detect schema for a concrete dataset format."""

    source_format: str

    @abstractmethod
    def supports(self, source_format: str) -> bool:
        """Return whether the strategy supports the requested source format."""

    @abstractmethod
    def detect_schema(
        self,
        *,
        source_path: str,
        sheet_name: str | None = None,
    ) -> SchemaDetectionResult:
        """Detect schema metadata for the given dataset source."""


class CsvSchemaDetectionStrategy(SchemaDetectionStrategy):
    """Detect CSV schema through DuckDB type inference."""

    source_format = "csv"

    def __init__(
        self,
        *,
        duckdb_connection_provider: DuckDbConnectionProvider | None = None,
        json_dataset_policy: JsonDatasetPolicy | None = None,
    ) -> None:
        self._duckdb_connection_provider = (
            duckdb_connection_provider or InMemoryDuckDbConnectionProvider()
        )
        self._json_dataset_policy = json_dataset_policy or StrictJsonDatasetPolicy()

    def supports(self, source_format: str) -> bool:
        return source_format.strip().lower() == self.source_format

    def detect_schema(
        self,
        *,
        source_path: str,
        sheet_name: str | None = None,
    ) -> SchemaDetectionResult:
        _ = sheet_name
        with self._duckdb_connection_provider.connect() as connection:
            schema_rows = connection.execute(
                "DESCRIBE SELECT * FROM read_csv_auto(?, header=true)",
                [source_path],
            ).fetchall()
        columns = [
            {"name": str(column_name), "type": str(column_type)}
            for column_name, column_type, *_ in schema_rows
        ]
        json_columns = self._detect_json_columns(
            source_path, [column["name"] for column in columns]
        )
        for column in columns:
            if column["name"] in json_columns:
                column["type"] = "JSON"
        return SchemaDetectionResult(
            columns=columns,
            metadata={
                "source_format": self.source_format,
                "strategy": "duckdb_csv_describe",
                "logical_json_columns": sorted(json_columns),
            },
            warnings=(),
            confidence=1.0,
            strategy_name=self.__class__.__name__,
        )

    def _detect_json_columns(self, source_path: str, column_names: list[str]) -> frozenset[str]:
        """Validate every CSV cell before classifying columns as logical JSON."""
        values_by_column: dict[str, list[str | None]] = {name: [] for name in column_names}
        with open(source_path, encoding="utf-8-sig", newline="") as source_file:
            for row in csv.DictReader(source_file):
                for name in column_names:
                    values_by_column[name].append(row.get(name))
        return frozenset(
            name
            for name, values in values_by_column.items()
            if self._json_dataset_policy.is_json_text_column(values)
        )


class ParquetSchemaDetectionStrategy(SchemaDetectionStrategy):
    """Detect Parquet schema directly from Parquet metadata."""

    source_format = "parquet"

    def supports(self, source_format: str) -> bool:
        return source_format.strip().lower() == self.source_format

    def detect_schema(
        self,
        *,
        source_path: str,
        sheet_name: str | None = None,
    ) -> SchemaDetectionResult:
        _ = sheet_name
        parquet_file = pyarrow_parquet.ParquetFile(source_path)
        arrow_schema = parquet_file.schema_arrow
        columns = [
            {
                "name": str(field.name),
                "type": self._map_arrow_type_to_sql(field.type),
            }
            for field in arrow_schema
        ]
        return SchemaDetectionResult(
            columns=columns,
            metadata={"source_format": self.source_format, "strategy": "parquet_metadata"},
            warnings=(),
            confidence=1.0,
            strategy_name=self.__class__.__name__,
        )

    def _map_arrow_type_to_sql(self, arrow_type: Any) -> str:
        if pyarrow_types.is_boolean(arrow_type):
            return "BOOLEAN"
        if pyarrow_types.is_int8(arrow_type) or pyarrow_types.is_int16(arrow_type):
            return "SMALLINT"
        if pyarrow_types.is_int32(arrow_type):
            return "INTEGER"
        if pyarrow_types.is_int64(arrow_type):
            return "BIGINT"
        if pyarrow_types.is_uint8(arrow_type) or pyarrow_types.is_uint16(arrow_type):
            return "USMALLINT"
        if pyarrow_types.is_uint32(arrow_type):
            return "UINTEGER"
        if pyarrow_types.is_uint64(arrow_type):
            return "UBIGINT"
        if pyarrow_types.is_float16(arrow_type) or pyarrow_types.is_float32(arrow_type):
            return "FLOAT"
        if pyarrow_types.is_float64(arrow_type):
            return "DOUBLE"
        if pyarrow_types.is_decimal(arrow_type):
            precision = getattr(arrow_type, "precision", 38)
            scale = getattr(arrow_type, "scale", 0)
            return f"DECIMAL({int(precision)},{int(scale)})"
        if pyarrow_types.is_timestamp(arrow_type):
            return "TIMESTAMP"
        if pyarrow_types.is_date(arrow_type):
            return "DATE"
        if pyarrow_types.is_time(arrow_type):
            return "TIME"
        if pyarrow_types.is_binary(arrow_type) or pyarrow_types.is_large_binary(arrow_type):
            return "BLOB"
        if pyarrow_types.is_list(arrow_type) or pyarrow_types.is_struct(arrow_type):
            return "JSON"
        return "VARCHAR"


class JsonSchemaDetectionStrategy(SchemaDetectionStrategy):
    """Detect a JSON dataset schema using the shared strict JSON policy."""

    source_format = "json"

    def __init__(self, *, json_dataset_policy: JsonDatasetPolicy | None = None) -> None:
        """Initialize the strategy with a JSON policy.

        Args:
            json_dataset_policy: JSON reader and classifier implementation.
        """
        self._json_dataset_policy = json_dataset_policy or StrictJsonDatasetPolicy()

    def supports(self, source_format: str) -> bool:
        """Return whether the strategy supports JSON."""
        return source_format.strip().lower() == self.source_format

    def detect_schema(
        self, *, source_path: str, sheet_name: str | None = None
    ) -> SchemaDetectionResult:
        """Return the deterministic union schema for an array-root JSON dataset."""
        _ = sheet_name
        dataset = self._json_dataset_policy.scan(source_path)
        return SchemaDetectionResult(
            columns=[
                {"name": name, "type": "JSON" if name in dataset.json_columns else "VARCHAR"}
                for name in dataset.columns
            ],
            metadata={
                "source_format": self.source_format,
                "strategy": (
                    "json_lines_objects" if self.source_format == "jsonl" else "json_array_objects"
                ),
                "logical_json_columns": sorted(dataset.json_columns),
            },
            warnings=(),
            confidence=1.0,
            strategy_name=self.__class__.__name__,
        )


class JsonLinesSchemaDetectionStrategy(JsonSchemaDetectionStrategy):
    """Detect JSON Lines columns with the shared JSON value semantics."""

    source_format = "jsonl"

    def __init__(self) -> None:
        """Initialize schema detection with the JSON Lines reader."""
        super().__init__(json_dataset_policy=StrictJsonLinesDatasetPolicy())


class ExcelSchemaDetectionStrategy(SchemaDetectionStrategy):
    """Detect Excel schema after adapter-driven CSV normalization."""

    source_format = "xlsx"

    def __init__(
        self,
        *,
        excel_adapter: ExcelDatasetAdapter | None = None,
        duckdb_connection_provider: DuckDbConnectionProvider | None = None,
        json_dataset_policy: JsonDatasetPolicy | None = None,
    ) -> None:
        self._excel_adapter = excel_adapter or ExcelDatasetAdapter()
        self._duckdb_connection_provider = (
            duckdb_connection_provider or InMemoryDuckDbConnectionProvider()
        )
        self._json_dataset_policy = json_dataset_policy or StrictJsonDatasetPolicy()

    def supports(self, source_format: str) -> bool:
        return source_format.strip().lower() == self.source_format

    def detect_schema(
        self,
        *,
        source_path: str,
        sheet_name: str | None = None,
    ) -> SchemaDetectionResult:
        with self._excel_adapter.materialize_to_csv(
            source_path, sheet_name=sheet_name
        ) as normalized:
            with self._duckdb_connection_provider.connect() as connection:
                schema_rows = connection.execute(
                    "DESCRIBE SELECT * FROM read_csv_auto(?, header=true)",
                    [normalized.normalized_path],
                ).fetchall()
            columns = [
                {"name": str(column_name), "type": str(column_type)}
                for column_name, column_type, *_ in schema_rows
            ]
            json_columns = CsvSchemaDetectionStrategy(
                json_dataset_policy=self._json_dataset_policy,
            )._detect_json_columns(
                normalized.normalized_path,
                [column["name"] for column in columns],
            )
            for column in columns:
                if column["name"] in json_columns:
                    column["type"] = "JSON"
            warnings = tuple(str(item) for item in normalized.detection_warnings)
            return SchemaDetectionResult(
                columns=columns,
                metadata={
                    "source_format": self.source_format,
                    "strategy": "excel_adapter_normalization",
                    "sheet_name": normalized.sheet_name,
                    "detected_header_row_index": int(normalized.detected_header_row_index),
                    "header_row_span": int(normalized.header_row_span),
                    "skipped_rows": int(normalized.skipped_rows),
                    "original_column_names": list(normalized.original_column_names),
                    "normalized_column_names": list(normalized.normalized_column_names),
                    "fallback_name_reasons": dict(normalized.fallback_name_reasons),
                    "logical_json_columns": sorted(json_columns),
                },
                warnings=warnings,
                confidence=float(normalized.detection_confidence),
                strategy_name=self.__class__.__name__,
            )


class SchemaDetectionStrategyResolver:
    """Resolve schema-detection strategy by source format."""

    def __init__(
        self,
        *,
        strategies: list[SchemaDetectionStrategy] | None = None,
        duckdb_connection_provider: DuckDbConnectionProvider | None = None,
        excel_adapter: ExcelDatasetAdapter | None = None,
    ) -> None:
        shared_provider = duckdb_connection_provider or InMemoryDuckDbConnectionProvider()
        shared_excel_adapter = excel_adapter or ExcelDatasetAdapter()
        self._strategies = strategies or [
            CsvSchemaDetectionStrategy(duckdb_connection_provider=shared_provider),
            ParquetSchemaDetectionStrategy(),
            JsonSchemaDetectionStrategy(),
            JsonLinesSchemaDetectionStrategy(),
            ExcelSchemaDetectionStrategy(
                excel_adapter=shared_excel_adapter,
                duckdb_connection_provider=shared_provider,
            ),
        ]

    def resolve(self, source_format: str) -> SchemaDetectionStrategy:
        """Resolve the first matching schema-detection strategy."""
        source_format = normalize_dataset_format(source_format)
        for strategy in self._strategies:
            if strategy.supports(source_format):
                return strategy
        raise ValueError(f"Unsupported dataset source format for schema detection: {source_format}")

    def detect(
        self,
        *,
        source_path: str,
        source_format: str,
        sheet_name: str | None = None,
    ) -> SchemaDetectionResult:
        """Detect schema via resolved strategy."""
        strategy = self.resolve(source_format)
        return strategy.detect_schema(source_path=source_path, sheet_name=sheet_name)
