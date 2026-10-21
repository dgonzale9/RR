from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol

from resource_registry.services.canonical_dataset_builder import (
    JsonDatasetAdapter,
    JsonLinesDatasetAdapter,
)
from resource_registry.services.excel_dataset_adapter import ExcelDatasetAdapter


class DatasetSourceAdapter(Protocol):
    """Adapter contract for dataset source normalization/materialization."""

    def supports(self, path: Path) -> bool:
        """Return whether this adapter can handle the given source path."""

    @contextmanager
    def materialize(self, path: Path, *, sheet_name: str | None = None) -> Iterator[Path]:
        """Yield a queryable local path for the dataset source."""


class CsvDatasetAdapter:
    """Pass-through adapter for CSV sources."""

    _SUFFIX = ".csv"

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() == self._SUFFIX

    @contextmanager
    def materialize(self, path: Path, *, sheet_name: str | None = None) -> Iterator[Path]:
        _ = sheet_name
        yield path


class ParquetDatasetAdapter:
    """Pass-through adapter for Parquet sources."""

    _SUFFIX = ".parquet"

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() == self._SUFFIX

    @contextmanager
    def materialize(self, path: Path, *, sheet_name: str | None = None) -> Iterator[Path]:
        _ = sheet_name
        yield path


class JsonDatasetSourceAdapter:
    """Materialize validated JSON sources as temporary parquet for inspection."""

    _SUFFIX = ".json"

    def supports(self, path: Path) -> bool:
        """Return whether the source is a JSON dataset."""
        return path.suffix.lower() == self._SUFFIX

    @contextmanager
    def materialize(self, path: Path, *, sheet_name: str | None = None) -> Iterator[Path]:
        """Yield a canonical parquet view of one JSON source."""
        _ = sheet_name
        with TemporaryDirectory(prefix="resource_registry_json_inspection_") as directory:
            artifact = JsonDatasetAdapter().build_canonical_parquet(
                source_path=str(path), working_directory=directory
            )
            yield Path(artifact.canonical_path)


class JsonLinesDatasetSourceAdapter:
    """Materialize JSONL and NDJSON as temporary Parquet for inspection."""

    def supports(self, path: Path) -> bool:
        """Check whether this adapter accepts the source suffix.

        Args:
            path: Dataset path to inspect.

        Returns:
            Whether the source has a JSON Lines extension.
        """
        return path.suffix.lower() in {".jsonl", ".ndjson"}

    @contextmanager
    def materialize(self, path: Path, *, sheet_name: str | None = None) -> Iterator[Path]:
        """Create and clean up one canonical view of a JSON Lines source.

        Args:
            path: JSONL or NDJSON source to normalize.
            sheet_name: Unused for JSON Lines sources.

        Yields:
            Path to the temporary canonical Parquet file.

        Raises:
            ValueError: If the source contains invalid JSON Lines records.
            OSError: If the source or temporary file cannot be accessed.
        """
        _ = sheet_name
        with TemporaryDirectory(prefix="resource_registry_jsonl_inspection_") as directory:
            artifact = JsonLinesDatasetAdapter().build_canonical_parquet(
                source_path=str(path), working_directory=directory
            )
            yield Path(artifact.canonical_path)


class ExcelDatasetSourceAdapter:
    """Normalization adapter for XLSX sources."""

    _SUFFIX = ".xlsx"

    def __init__(self, *, excel_adapter: ExcelDatasetAdapter | None = None) -> None:
        self._excel_adapter = excel_adapter or ExcelDatasetAdapter()

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() == self._SUFFIX

    @contextmanager
    def materialize(self, path: Path, *, sheet_name: str | None = None) -> Iterator[Path]:
        with self._excel_adapter.materialize_to_csv(path, sheet_name=sheet_name) as normalized:
            yield Path(normalized.normalized_path)
