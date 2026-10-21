from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any, Protocol

from resource_registry.services.dataset_engine import DatasetEngine
from resource_registry.services.dataset_formats import normalize_dataset_format
from resource_registry.services.schema_detection import SchemaDetectionResult


class DatasetInspectionEngine(Protocol):
    """Contract required by DatasetContextBuilder for dataset inspection."""

    MAX_PREVIEW_ROWS: int

    def get_row_count(self, path: str | Path, *, sheet_name: str | None = None) -> int:
        """Return the number of rows in the dataset."""

    def get_schema(
        self, path: str | Path, *, sheet_name: str | None = None
    ) -> list[dict[str, str]]:
        """Return schema information for the dataset."""

    def get_schema_detection(
        self,
        path: str | Path,
        *,
        sheet_name: str | None = None,
    ) -> SchemaDetectionResult:
        """Return schema and detection metadata."""

    def preview(
        self,
        path: str | Path,
        limit: int = 10,
        *,
        sheet_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return a bounded preview of dataset rows."""


class DatasetContextBuilder:
    """Build a normalized, safe dataset context from DatasetEngine outputs."""

    _CSV_FORMAT = "csv"
    _PARQUET_FORMAT = "parquet"
    _XLSX_FORMAT = "xlsx"
    _JSON_FORMAT = "json"

    def __init__(self, dataset_engine: DatasetInspectionEngine | None = None) -> None:
        self._dataset_engine = dataset_engine or DatasetEngine()

    def build_context(
        self,
        path: str | Path,
        preview_limit: int = 10,
        *,
        sheet_name: str | None = None,
        mime_type: str | None = None,
    ) -> dict[str, Any]:
        """Build a deterministic dataset context contract."""
        file_path = Path(path)
        effective_preview_limit = self._get_effective_preview_limit(preview_limit)

        schema_detection = self._get_schema_detection(file_path, sheet_name=sheet_name)
        schema = list(schema_detection.columns)
        row_count = self._get_row_count(file_path, sheet_name=sheet_name)
        preview_rows = self._get_preview_rows(
            file_path,
            effective_preview_limit=effective_preview_limit,
            sheet_name=sheet_name,
        )
        detected_mime = self._detect_mime_type(file_path, explicit_mime_type=mime_type)

        return {
            "source": {
                "path": str(file_path),
                "format": self._detect_file_format(file_path),
                "mime_type": detected_mime,
                "sheet_name": sheet_name,
                "file_name": file_path.name,
                "file_size_bytes": file_path.stat().st_size,
                "schema_detection": {
                    "strategy": schema_detection.strategy_name,
                    "confidence": schema_detection.confidence,
                    "warnings": list(schema_detection.warnings),
                    **dict(schema_detection.metadata),
                },
            },
            "summary": {
                "row_count": row_count,
                "column_count": len(schema),
            },
            "schema": schema,
            "preview": preview_rows,
            "safety": {
                "raw_content_included": False,
                "preview_limited": True,
                "preview_limit": effective_preview_limit,
            },
        }

    def _detect_file_format(self, file_path: Path) -> str:
        suffix = file_path.suffix.lower()
        if suffix == ".csv":
            return self._CSV_FORMAT
        if suffix == ".parquet":
            return self._PARQUET_FORMAT
        if suffix == ".xlsx":
            return self._XLSX_FORMAT
        if suffix == ".json":
            return self._JSON_FORMAT
        return normalize_dataset_format(suffix)

    def _get_effective_preview_limit(self, preview_limit: int) -> int:
        if preview_limit <= 0:
            return 0
        return min(preview_limit, self._dataset_engine.MAX_PREVIEW_ROWS)

    def _detect_mime_type(self, file_path: Path, *, explicit_mime_type: str | None) -> str | None:
        candidate = str(explicit_mime_type or "").strip()
        if candidate:
            return candidate
        guessed_mime, _ = mimetypes.guess_type(str(file_path))
        return guessed_mime

    def _get_schema_detection(
        self,
        file_path: Path,
        *,
        sheet_name: str | None,
    ) -> SchemaDetectionResult:
        if sheet_name is None:
            try:
                return self._dataset_engine.get_schema_detection(file_path, sheet_name=sheet_name)
            except TypeError:
                return self._dataset_engine.get_schema_detection(file_path)
        return self._dataset_engine.get_schema_detection(file_path, sheet_name=sheet_name)

    def _get_row_count(self, file_path: Path, *, sheet_name: str | None) -> int:
        if sheet_name is None:
            try:
                return self._dataset_engine.get_row_count(file_path, sheet_name=sheet_name)
            except TypeError:
                return self._dataset_engine.get_row_count(file_path)
        return self._dataset_engine.get_row_count(file_path, sheet_name=sheet_name)

    def _get_preview_rows(
        self,
        file_path: Path,
        *,
        effective_preview_limit: int,
        sheet_name: str | None,
    ) -> list[dict[str, Any]]:
        if sheet_name is None:
            try:
                return self._dataset_engine.preview(
                    file_path,
                    limit=effective_preview_limit,
                    sheet_name=sheet_name,
                )
            except TypeError:
                return self._dataset_engine.preview(file_path, limit=effective_preview_limit)
        return self._dataset_engine.preview(
            file_path,
            limit=effective_preview_limit,
            sheet_name=sheet_name,
        )
