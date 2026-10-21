from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import mkdtemp
from typing import Any

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
from resource_registry.services.schema_detection import CsvSchemaDetectionStrategy

_CANONICAL_PARQUET_NAME = "dataset.parquet"
_NORMALIZATION_VERSION = "v1"


@dataclass(frozen=True)
class CanonicalDatasetArtifact:
    canonical_path: str
    source_format: str
    metadata: dict[str, Any]


class DatasetAdapter(ABC):
    """Normalize a source dataset into canonical parquet."""

    source_format: str

    @abstractmethod
    def supports(self, source_format: str) -> bool:
        """Return whether adapter supports the source format."""

    @abstractmethod
    def build_canonical_parquet(
        self,
        *,
        source_path: str,
        working_directory: str,
        sheet_name: str | None = None,
    ) -> CanonicalDatasetArtifact:
        """Build canonical parquet artifact from source dataset path."""


class CsvDatasetAdapter(DatasetAdapter):
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

    def build_canonical_parquet(
        self,
        *,
        source_path: str,
        working_directory: str,
        sheet_name: str | None = None,
    ) -> CanonicalDatasetArtifact:
        _ = sheet_name
        canonical_path = str(Path(working_directory) / _CANONICAL_PARQUET_NAME)
        with self._duckdb_connection_provider.connect() as connection:
            connection.execute(
                "CREATE TABLE normalized_source AS SELECT * FROM read_csv_auto(?, header=true)",
                [source_path],
            )
            connection.execute(
                "COPY normalized_source TO ? (FORMAT PARQUET, COMPRESSION ZSTD)",
                [canonical_path],
            )
        detected_schema = CsvSchemaDetectionStrategy(
            duckdb_connection_provider=self._duckdb_connection_provider,
            json_dataset_policy=self._json_dataset_policy,
        ).detect_schema(source_path=source_path)
        json_columns = frozenset(detected_schema.metadata.get("logical_json_columns", []))
        return CanonicalDatasetArtifact(
            canonical_path=canonical_path,
            source_format=self.source_format,
            metadata={
                "normalization_version": _NORMALIZATION_VERSION,
                "conversion_applied": True,
                "logical_json_columns": sorted(json_columns),
            },
        )


class ParquetDatasetAdapter(DatasetAdapter):
    source_format = "parquet"

    def supports(self, source_format: str) -> bool:
        return source_format.strip().lower() == self.source_format

    def build_canonical_parquet(
        self,
        *,
        source_path: str,
        working_directory: str,
        sheet_name: str | None = None,
    ) -> CanonicalDatasetArtifact:
        _ = sheet_name
        canonical_path = str(Path(working_directory) / _CANONICAL_PARQUET_NAME)
        # Parquet inputs are already canonical: copy bytes, avoid conversion query.
        shutil.copy2(source_path, canonical_path)
        return CanonicalDatasetArtifact(
            canonical_path=canonical_path,
            source_format=self.source_format,
            metadata={
                "normalization_version": _NORMALIZATION_VERSION,
                "conversion_applied": False,
            },
        )


class JsonDatasetAdapter(DatasetAdapter):
    """Normalize an array-root JSON dataset into canonical parquet."""

    source_format = "json"

    def __init__(
        self,
        *,
        duckdb_connection_provider: DuckDbConnectionProvider | None = None,
        json_dataset_policy: JsonDatasetPolicy | None = None,
    ) -> None:
        """Initialize the adapter with a connection provider."""
        self._duckdb_connection_provider = (
            duckdb_connection_provider or InMemoryDuckDbConnectionProvider()
        )
        self._json_dataset_policy = json_dataset_policy or StrictJsonDatasetPolicy()

    def supports(self, source_format: str) -> bool:
        return source_format.strip().lower() == self.source_format

    def build_canonical_parquet(
        self, *, source_path: str, working_directory: str, sheet_name: str | None = None
    ) -> CanonicalDatasetArtifact:
        """Build parquet while preserving nested JSON values as valid JSON text."""
        _ = sheet_name
        dataset_schema = self._json_dataset_policy.scan(source_path)
        canonical_path = str(Path(working_directory) / _CANONICAL_PARQUET_NAME)
        # Parameterized VALUES keeps JSON text as VARCHAR and never invokes DuckDB read_json.
        column_definitions = ", ".join(
            '"' + name.replace('"', '""') + '" VARCHAR' for name in dataset_schema.columns
        )
        placeholders = ", ".join("?" for _ in dataset_schema.columns)
        with self._duckdb_connection_provider.connect() as connection:
            connection.execute(f"CREATE TABLE normalized_source ({column_definitions})")
            for row in self._json_dataset_policy.iter_rows(source_path):
                values = [
                    (
                        self._json_dataset_policy.serialize(row.get(column))
                        if column in dataset_schema.json_columns and column in row
                        else (
                            None
                            if column not in row or row.get(column) is None
                            else str(row.get(column))
                        )
                    )
                    for column in dataset_schema.columns
                ]
                connection.execute(f"INSERT INTO normalized_source VALUES ({placeholders})", values)
            connection.execute(
                "COPY normalized_source TO ? (FORMAT PARQUET, COMPRESSION ZSTD)", [canonical_path]
            )
        return CanonicalDatasetArtifact(
            canonical_path=canonical_path,
            source_format=self.source_format,
            metadata={
                "normalization_version": _NORMALIZATION_VERSION,
                "conversion_applied": True,
                "logical_json_columns": sorted(dataset_schema.json_columns),
            },
        )


class JsonLinesDatasetAdapter(JsonDatasetAdapter):
    """Normalize JSON Lines using the same canonical representation as JSON."""

    source_format = "jsonl"

    def __init__(
        self, *, duckdb_connection_provider: DuckDbConnectionProvider | None = None
    ) -> None:
        """Initialize the adapter with the JSON Lines record policy.

        Args:
            duckdb_connection_provider: Optional connection factory for canonicalization.
        """
        super().__init__(
            duckdb_connection_provider=duckdb_connection_provider,
            json_dataset_policy=StrictJsonLinesDatasetPolicy(),
        )


class ExcelCanonicalDatasetAdapter(DatasetAdapter):
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

    def build_canonical_parquet(
        self,
        *,
        source_path: str,
        working_directory: str,
        sheet_name: str | None = None,
    ) -> CanonicalDatasetArtifact:
        canonical_path = str(Path(working_directory) / _CANONICAL_PARQUET_NAME)
        with self._excel_adapter.materialize_to_csv(
            source_path, sheet_name=sheet_name
        ) as normalized:
            with self._duckdb_connection_provider.connect() as connection:
                connection.execute(
                    "CREATE TABLE normalized_source AS SELECT * FROM read_csv_auto(?, header=true)",
                    [normalized.normalized_path],
                )
                connection.execute(
                    "COPY normalized_source TO ? (FORMAT PARQUET, COMPRESSION ZSTD)",
                    [canonical_path],
                )
            return CanonicalDatasetArtifact(
                canonical_path=canonical_path,
                source_format=self.source_format,
                metadata={
                    "normalization_version": _NORMALIZATION_VERSION,
                    "conversion_applied": True,
                    "excel_sheet_name": normalized.sheet_name,
                    "detected_header_row_index": int(normalized.detected_header_row_index),
                    "header_row_span": int(normalized.header_row_span),
                    "skipped_rows": int(normalized.skipped_rows),
                    "original_column_names": list(normalized.original_column_names),
                    "normalized_column_names": list(normalized.normalized_column_names),
                    "fallback_name_reasons": dict(normalized.fallback_name_reasons),
                    "schema_detection_warnings": list(normalized.detection_warnings),
                    "schema_detection_confidence": float(normalized.detection_confidence),
                    "logical_json_columns": sorted(
                        CsvSchemaDetectionStrategy(
                            json_dataset_policy=self._json_dataset_policy,
                        )._detect_json_columns(
                            normalized.normalized_path,
                            list(normalized.normalized_column_names),
                        )
                    ),
                },
            )


class CanonicalDatasetBuilder:
    """Build canonical parquet artifacts from source datasets using adapter strategy."""

    def __init__(
        self,
        *,
        adapters: list[DatasetAdapter] | None = None,
        duckdb_connection_provider: DuckDbConnectionProvider | None = None,
    ) -> None:
        self._duckdb_connection_provider = (
            duckdb_connection_provider or InMemoryDuckDbConnectionProvider()
        )
        self._adapters = adapters or [
            CsvDatasetAdapter(
                duckdb_connection_provider=self._duckdb_connection_provider,
            ),
            ParquetDatasetAdapter(),
            JsonDatasetAdapter(duckdb_connection_provider=self._duckdb_connection_provider),
            JsonLinesDatasetAdapter(duckdb_connection_provider=self._duckdb_connection_provider),
            ExcelCanonicalDatasetAdapter(
                duckdb_connection_provider=self._duckdb_connection_provider,
            ),
        ]

    @contextmanager
    def build(
        self,
        *,
        source_path: str,
        source_format: str,
        sheet_name: str | None = None,
    ) -> Iterator[CanonicalDatasetArtifact]:
        working_directory = mkdtemp(prefix="resource_registry_canonical_")
        try:
            adapter = self._resolve_adapter(source_format)
            canonical = adapter.build_canonical_parquet(
                source_path=source_path,
                working_directory=working_directory,
                sheet_name=sheet_name,
            )
            yield canonical
        finally:
            shutil.rmtree(working_directory, ignore_errors=True)

    def _resolve_adapter(self, source_format: str) -> DatasetAdapter:
        source_format = normalize_dataset_format(source_format)
        for adapter in self._adapters:
            if adapter.supports(source_format):
                return adapter
        raise ValueError(f"Unsupported dataset source format for canonicalization: {source_format}")
