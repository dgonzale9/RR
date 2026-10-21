from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from resource_registry.services.dataset_source_adapters import (
    CsvDatasetAdapter,
    DatasetSourceAdapter,
    ExcelDatasetSourceAdapter,
    JsonDatasetSourceAdapter,
    JsonLinesDatasetSourceAdapter,
    ParquetDatasetAdapter,
)
from resource_registry.services.duckdb_service import (
    DuckDbConnectionProvider,
    InMemoryDuckDbConnectionProvider,
)
from resource_registry.services.excel_dataset_adapter import ExcelDatasetAdapter
from resource_registry.services.schema_detection import (
    SchemaDetectionResult,
    SchemaDetectionStrategyResolver,
)


class DatasetEngine:
    """Inspect external datasets through DuckDB without full materialization."""

    MAX_PREVIEW_ROWS = 100
    _CSV_SUFFIX = ".csv"
    _PARQUET_SUFFIX = ".parquet"
    _XLSX_SUFFIX = ".xlsx"
    _JSON_SUFFIX = ".json"

    def __init__(
        self,
        *,
        adapters: Sequence[DatasetSourceAdapter] | None = None,
        excel_adapter: ExcelDatasetAdapter | None = None,
        duckdb_connection_provider: DuckDbConnectionProvider | None = None,
        schema_detection_resolver: SchemaDetectionStrategyResolver | None = None,
    ) -> None:
        self._adapters = tuple(
            adapters
            or (
                CsvDatasetAdapter(),
                ParquetDatasetAdapter(),
                ExcelDatasetSourceAdapter(excel_adapter=excel_adapter),
                JsonDatasetSourceAdapter(),
                JsonLinesDatasetSourceAdapter(),
            ),
        )
        self._duckdb_connection_provider = (
            duckdb_connection_provider or InMemoryDuckDbConnectionProvider()
        )
        self._schema_detection_resolver = (
            schema_detection_resolver
            or SchemaDetectionStrategyResolver(
                duckdb_connection_provider=self._duckdb_connection_provider,
                excel_adapter=excel_adapter,
            )
        )

    def get_row_count(self, path: str | Path, *, sheet_name: str | None = None) -> int:
        """Return the number of rows in the dataset file."""
        file_path = self._validate_dataset_path(path)
        with self._resolve_query_source(file_path, sheet_name=sheet_name) as query_path:
            from_clause = self._build_from_clause(query_path)
            with self._duckdb_connection_provider.connect() as connection:
                row_count = connection.execute(
                    f"SELECT COUNT(*) {from_clause}",
                    [str(query_path)],
                ).fetchone()
        return int(row_count[0]) if row_count else 0

    def get_schema(
        self, path: str | Path, *, sheet_name: str | None = None
    ) -> list[dict[str, str]]:
        """Return a list of columns with their inferred data types."""
        detection = self.get_schema_detection(path, sheet_name=sheet_name)
        return list(detection.columns)

    def get_schema_detection(
        self,
        path: str | Path,
        *,
        sheet_name: str | None = None,
    ) -> SchemaDetectionResult:
        """Return full schema detection output, including confidence metadata."""
        file_path = self._validate_dataset_path(path)
        source_format = file_path.suffix.lower().removeprefix(".")
        return self._schema_detection_resolver.detect(
            source_path=str(file_path),
            source_format=source_format,
            sheet_name=sheet_name,
        )

    def preview(
        self,
        path: str | Path,
        limit: int = 10,
        *,
        sheet_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return up to ``limit`` rows for quick dataset inspection."""
        if limit <= 0:
            return []
        effective_limit = min(limit, self.MAX_PREVIEW_ROWS)

        file_path = self._validate_dataset_path(path)
        with self._resolve_query_source(file_path, sheet_name=sheet_name) as query_path:
            from_clause = self._build_from_clause(query_path)
            with self._duckdb_connection_provider.connect() as connection:
                cursor = connection.execute(
                    f"SELECT * {from_clause} LIMIT ?",
                    [str(query_path), effective_limit],
                )
                rows = cursor.fetchall()
                column_names = [column[0] for column in cursor.description]
        return [dict(zip(column_names, row, strict=False)) for row in rows]

    def _validate_dataset_path(self, path: str | Path) -> Path:
        file_path = Path(path)
        if not file_path.exists():
            msg = f"Dataset file does not exist: {file_path}"
            raise FileNotFoundError(msg)
        if not file_path.is_file():
            msg = f"Dataset path is not a file: {file_path}"
            raise ValueError(msg)
        if file_path.suffix.lower() not in {
            self._CSV_SUFFIX,
            self._PARQUET_SUFFIX,
            self._XLSX_SUFFIX,
            self._JSON_SUFFIX,
            ".jsonl",
            ".ndjson",
        }:
            msg = f"Unsupported dataset format: {file_path.suffix}"
            raise ValueError(msg)
        return file_path

    def _build_from_clause(self, file_path: Path) -> str:
        suffix = file_path.suffix.lower()
        if suffix == self._CSV_SUFFIX:
            return "FROM read_csv_auto(?, header=true)"
        if suffix == self._PARQUET_SUFFIX:
            return "FROM read_parquet(?)"
        msg = f"Unsupported dataset format: {suffix}"
        raise ValueError(msg)

    def _resolve_adapter(self, file_path: Path) -> DatasetSourceAdapter:
        for adapter in self._adapters:
            if adapter.supports(file_path):
                return adapter
        msg = f"Unsupported dataset format: {file_path.suffix}"
        raise ValueError(msg)

    def _resolve_query_source(self, file_path: Path, *, sheet_name: str | None = None):
        adapter = self._resolve_adapter(file_path)
        if sheet_name is None:
            return adapter.materialize(file_path)
        return adapter.materialize(file_path, sheet_name=sheet_name)
