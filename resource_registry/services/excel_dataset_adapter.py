from __future__ import annotations

import csv
import re
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from tempfile import NamedTemporaryFile
from zipfile import BadZipFile

from django.conf import settings
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

DEFAULT_MAX_ROWS = 100_000
DEFAULT_MAX_COLUMNS = 1_000
DEFAULT_MAX_FILE_BYTES = 100 * 1024 * 1024
DEFAULT_HEADER_SCAN_ROWS = 30

_NON_ALPHANUMERIC_PATTERN = re.compile(r"[^A-Za-z0-9_]+")
_MULTI_UNDERSCORE_PATTERN = re.compile(r"_+")
_NUMERIC_VALUE_PATTERN = re.compile(r"^[+-]?\d+(?:\.\d+)?$")


class UnsupportedExcelWorkbookError(ValueError):
    """Raised when workbook encryption/protection prevents safe parsing."""


class WorksheetNotFoundError(ValueError):
    """Raised when selected worksheet does not exist in workbook."""


class EmptyExcelWorksheetError(ValueError):
    """Raised when selected worksheet has no tabular data."""


class ExcelRowLimitExceededError(ValueError):
    """Raised when worksheet row count exceeds configured limit."""


class ExcelColumnLimitExceededError(ValueError):
    """Raised when worksheet column count exceeds configured limit."""


class ExcelFileSizeLimitExceededError(ValueError):
    """Raised when workbook bytes exceed configured limit."""


@dataclass(frozen=True)
class NormalizedExcelArtifact:
    normalized_path: str
    sheet_name: str
    row_count: int
    column_count: int
    columns: tuple[str, ...]
    detected_header_row_index: int
    header_row_span: int
    skipped_rows: int
    original_column_names: tuple[str, ...]
    normalized_column_names: tuple[str, ...]
    fallback_name_reasons: dict[str, str]
    detection_warnings: tuple[str, ...]
    detection_confidence: float


class ExcelDatasetAdapter:
    """Normalize XLSX worksheet content into a temporary CSV artifact."""

    def __init__(
        self,
        *,
        max_rows: int | None = None,
        max_columns: int | None = None,
        max_file_bytes: int | None = None,
        header_scan_rows: int | None = None,
    ) -> None:
        configured_max_rows = (
            max_rows
            if max_rows is not None
            else int(getattr(settings, "RESOURCE_REGISTRY_EXCEL_MAX_ROWS", DEFAULT_MAX_ROWS))
        )
        configured_max_columns = (
            max_columns
            if max_columns is not None
            else int(getattr(settings, "RESOURCE_REGISTRY_EXCEL_MAX_COLUMNS", DEFAULT_MAX_COLUMNS))
        )
        configured_max_file_bytes = (
            max_file_bytes
            if max_file_bytes is not None
            else int(
                getattr(
                    settings,
                    "RESOURCE_REGISTRY_EXCEL_MAX_FILE_BYTES",
                    DEFAULT_MAX_FILE_BYTES,
                ),
            )
        )
        self._max_rows = max(int(configured_max_rows), 1)
        self._max_columns = max(int(configured_max_columns), 1)
        self._max_file_bytes = max(int(configured_max_file_bytes), 1)
        configured_header_scan_rows = (
            header_scan_rows
            if header_scan_rows is not None
            else int(
                getattr(
                    settings,
                    "RESOURCE_REGISTRY_EXCEL_HEADER_SCAN_ROWS",
                    DEFAULT_HEADER_SCAN_ROWS,
                ),
            )
        )
        self._header_scan_rows = max(int(configured_header_scan_rows), 1)

    @contextmanager
    def materialize_to_csv(
        self,
        path: str | Path,
        *,
        sheet_name: str | None = None,
    ) -> Iterator[NormalizedExcelArtifact]:
        artifact = self.normalize(path, sheet_name=sheet_name)
        try:
            yield artifact
        finally:
            normalized_path = Path(artifact.normalized_path)
            if normalized_path.exists():
                normalized_path.unlink()

    def normalize(
        self,
        path: str | Path,
        *,
        sheet_name: str | None = None,
    ) -> NormalizedExcelArtifact:
        workbook_path = Path(path)
        self._validate_file_size(workbook_path)
        worksheet_values, resolved_sheet_name = self._read_worksheet_values(
            workbook_path,
            sheet_name=sheet_name,
        )

        header_decision = self._select_header_rows(worksheet_values)
        original_headers = self._build_original_headers(
            worksheet_values=worksheet_values,
            header_index=header_decision.header_row_index,
            header_row_span=header_decision.header_row_span,
            column_count=header_decision.column_count,
        )
        headers, fallback_name_reasons = self._normalize_headers(original_headers)
        data_rows = self._extract_data_rows(
            worksheet_values=worksheet_values,
            header_index=header_decision.header_row_index + header_decision.header_row_span - 1,
            column_count=len(headers),
        )
        (
            headers,
            original_headers,
            data_rows,
            fallback_name_reasons,
            pruned_empty_column_count,
        ) = self._prune_fully_empty_columns(
            headers=headers,
            original_headers=original_headers,
            data_rows=data_rows,
            fallback_name_reasons=fallback_name_reasons,
        )
        if not data_rows:
            raise EmptyExcelWorksheetError(
                "The selected Excel worksheet does not contain tabular data.",
            )
        if not headers:
            raise EmptyExcelWorksheetError(
                "The selected Excel worksheet does not contain tabular columns.",
            )
        detection_warnings = list(header_decision.warnings)
        if pruned_empty_column_count > 0:
            detection_warnings.append(
                f"Dropped {pruned_empty_column_count} fully empty columns from detected schema.",
            )

        with NamedTemporaryFile(
            mode="w",
            suffix=".csv",
            prefix="resource_registry_excel_",
            newline="",
            encoding="utf-8",
            delete=False,
        ) as temporary_file:
            writer = csv.writer(temporary_file)
            writer.writerow(headers)
            for row in data_rows:
                writer.writerow(row)
            normalized_path = temporary_file.name

        return NormalizedExcelArtifact(
            normalized_path=normalized_path,
            sheet_name=resolved_sheet_name,
            row_count=len(data_rows),
            column_count=len(headers),
            columns=tuple(headers),
            detected_header_row_index=header_decision.header_row_index,
            header_row_span=header_decision.header_row_span,
            skipped_rows=header_decision.header_row_index,
            original_column_names=tuple(original_headers),
            normalized_column_names=tuple(headers),
            fallback_name_reasons=fallback_name_reasons,
            detection_warnings=tuple(detection_warnings),
            detection_confidence=header_decision.confidence,
        )

    def list_worksheets(self, path: str | Path) -> list[str]:
        """Return workbook sheet names preserving workbook order."""
        workbook_path = Path(path)
        self._validate_file_size(workbook_path)
        try:
            workbook = load_workbook(
                filename=str(workbook_path),
                read_only=True,
                data_only=True,
            )
        except (BadZipFile, InvalidFileException, KeyError, RuntimeError, ValueError) as error:
            raise UnsupportedExcelWorkbookError(
                "Encrypted or password-protected Excel workbooks are not supported.",
            ) from error

        try:
            if workbook_path.suffix.lower() != ".xlsx":
                raise UnsupportedExcelWorkbookError("The Excel workbook is not supported.")
            return [str(sheet_name) for sheet_name in workbook.sheetnames]
        finally:
            workbook.close()

    def _read_worksheet_values(
        self,
        workbook_path: Path,
        *,
        sheet_name: str | None,
    ) -> tuple[list[tuple[object | None, ...]], str]:
        try:
            workbook = load_workbook(
                filename=str(workbook_path),
                read_only=True,
                data_only=True,
            )
        except (BadZipFile, InvalidFileException, KeyError, RuntimeError, ValueError) as error:
            raise UnsupportedExcelWorkbookError(
                "Encrypted or password-protected Excel workbooks are not supported.",
            ) from error

        try:
            if workbook_path.suffix.lower() != ".xlsx":
                raise UnsupportedExcelWorkbookError("The Excel workbook is not supported.")
            if sheet_name is not None and sheet_name not in workbook.sheetnames:
                raise WorksheetNotFoundError("The requested Excel worksheet was not found.")
            worksheet = workbook[sheet_name] if sheet_name else workbook[workbook.sheetnames[0]]
            values = list(worksheet.iter_rows(max_row=self._max_rows + 1, values_only=True))
            if not values:
                raise EmptyExcelWorksheetError(
                    "The selected Excel worksheet does not contain tabular data.",
                )
            if len(values) > self._max_rows:
                raise ExcelRowLimitExceededError(
                    "The Excel worksheet exceeds the maximum supported row count.",
                )
            detected_columns = max(self._effective_column_count(row) for row in values)
            if detected_columns > self._max_columns:
                raise ExcelColumnLimitExceededError(
                    "The Excel worksheet exceeds the maximum supported column count.",
                )
            return values, worksheet.title
        finally:
            workbook.close()

    def _normalize_headers(self, header_row: list[str]) -> tuple[list[str], dict[str, str]]:
        limited_header_row = header_row[: self._max_columns]
        headers: list[str] = []
        fallback_name_reasons: dict[str, str] = {}
        seen: dict[str, int] = {}

        for index, value in enumerate(limited_header_row, start=1):
            raw = str(value).strip() if value is not None else ""
            base, reason = self._normalize_header_label(raw, index=index)
            count = seen.get(base, 0)
            seen[base] = count + 1
            unique_name = base if count == 0 else f"{base}_{count + 1}"
            headers.append(unique_name)
            if reason:
                fallback_name_reasons[unique_name] = reason
        return headers, fallback_name_reasons

    def _extract_data_rows(
        self,
        *,
        worksheet_values: list[tuple[object | None, ...]],
        header_index: int,
        column_count: int,
    ) -> list[list[object | None]]:
        rows: list[list[object | None]] = []
        for row in worksheet_values[header_index + 1 :]:
            limited = list(row[:column_count])
            if len(limited) < column_count:
                limited.extend([None] * (column_count - len(limited)))
            if all(cell in (None, "") for cell in limited):
                continue
            rows.append(limited)
        return rows

    def _effective_column_count(self, row: tuple[object | None, ...]) -> int:
        index = len(row)
        while index > 0 and row[index - 1] in (None, ""):
            index -= 1
        return index

    def _select_header_rows(
        self,
        worksheet_values: list[tuple[object | None, ...]],
    ) -> _HeaderSelectionResult:
        scan_limit = min(len(worksheet_values), self._header_scan_rows)
        best_index = 0
        best_score = float("-inf")
        best_column_count = 1
        candidate_scores: list[float] = []
        warnings: list[str] = []

        for header_index in range(scan_limit):
            score, column_count = self._score_header_candidate(
                worksheet_values=worksheet_values,
                header_index=header_index,
            )
            candidate_scores.append(score)
            if score > best_score:
                best_score = score
                best_index = header_index
                best_column_count = column_count

        header_row_span = self._resolve_header_span(
            worksheet_values=worksheet_values,
            header_index=best_index,
            column_count=best_column_count,
        )
        confidence = self._compute_detection_confidence(candidate_scores=candidate_scores)
        if best_index > 0:
            warnings.append(
                f"Detected header row at index {best_index}; skipped preceding worksheet rows.",
            )
        if header_row_span > 1:
            warnings.append("Detected grouped/multi-row header and flattened header labels.")

        return _HeaderSelectionResult(
            header_row_index=best_index,
            header_row_span=header_row_span,
            column_count=best_column_count,
            confidence=confidence,
            warnings=warnings,
        )

    def _score_header_candidate(
        self,
        *,
        worksheet_values: list[tuple[object | None, ...]],
        header_index: int,
    ) -> tuple[float, int]:
        candidate_row = worksheet_values[header_index]
        column_count = max(self._effective_column_count(candidate_row), 1)
        limited = list(candidate_row[:column_count])
        non_empty_values = [value for value in limited if str(value or "").strip()]
        non_empty_ratio = len(non_empty_values) / column_count
        string_ratio = self._string_like_ratio(non_empty_values)
        title_penalty = 1.0 if (len(non_empty_values) <= 2 and non_empty_ratio < 0.40) else 0.0

        normalized_labels: list[str] = []
        generic_count = 0
        for index, value in enumerate(limited, start=1):
            normalized_label, reason = self._normalize_header_label(str(value or ""), index=index)
            normalized_labels.append(normalized_label)
            if reason:
                generic_count += 1
        duplicate_count = self._duplicate_count(normalized_labels)
        duplicate_ratio = duplicate_count / column_count
        generic_ratio = generic_count / column_count
        semantic_ratio = self._semantic_label_ratio(non_empty_values)
        grouped_header_bonus = self._grouped_header_bonus(
            worksheet_values=worksheet_values,
            header_index=header_index,
            column_count=column_count,
            duplicate_ratio=duplicate_ratio,
            non_empty_ratio=non_empty_ratio,
        )
        data_following_ratio = self._data_following_ratio(
            worksheet_values=worksheet_values,
            header_index=header_index,
            column_count=column_count,
        )
        score = (
            (0.35 * non_empty_ratio)
            + (0.20 * string_ratio)
            + (0.20 * data_following_ratio)
            + (0.10 * semantic_ratio)
            + (0.12 * grouped_header_bonus)
            - (0.10 * duplicate_ratio)
            - (0.10 * generic_ratio)
            - (0.15 * title_penalty)
        )
        return score, column_count

    def _grouped_header_bonus(
        self,
        *,
        worksheet_values: list[tuple[object | None, ...]],
        header_index: int,
        column_count: int,
        duplicate_ratio: float,
        non_empty_ratio: float,
    ) -> float:
        if duplicate_ratio <= 0:
            return 0.0
        if header_index + 1 >= len(worksheet_values):
            return 0.0
        next_row = list(worksheet_values[header_index + 1][:column_count])
        next_non_empty_values = [value for value in next_row if str(value or "").strip()]
        if not next_non_empty_values:
            return 0.0
        next_non_empty_ratio = len(next_non_empty_values) / max(column_count, 1)
        next_string_ratio = self._string_like_ratio(next_non_empty_values)
        if next_non_empty_ratio >= non_empty_ratio * 0.70 and next_string_ratio >= 0.70:
            return 1.0
        return 0.0

    def _resolve_header_span(
        self,
        *,
        worksheet_values: list[tuple[object | None, ...]],
        header_index: int,
        column_count: int,
    ) -> int:
        if header_index + 1 >= len(worksheet_values):
            return 1
        if header_index + 2 >= len(worksheet_values):
            return 1
        top_row = list(worksheet_values[header_index][:column_count])
        second_row = list(worksheet_values[header_index + 1][:column_count])
        top_non_empty = self._non_empty_ratio(top_row, column_count=column_count)
        second_non_empty = self._non_empty_ratio(second_row, column_count=column_count)
        second_string_ratio = self._string_like_ratio(
            [value for value in second_row if str(value or "").strip()],
        )
        top_labels = [
            str(value or "").strip().lower() for value in top_row if str(value or "").strip()
        ]
        top_duplicate_ratio = self._duplicate_count(top_labels) / max(column_count, 1)
        if top_duplicate_ratio >= 0.25 and second_non_empty >= 0.65 and second_string_ratio >= 0.70:
            return 2
        if top_non_empty < 0.65 and second_non_empty >= 0.65 and second_string_ratio >= 0.70:
            return 2
        return 1

    def _build_original_headers(
        self,
        *,
        worksheet_values: list[tuple[object | None, ...]],
        header_index: int,
        header_row_span: int,
        column_count: int,
    ) -> list[str]:
        header_rows = [
            list(worksheet_values[row_index][:column_count])
            for row_index in range(
                header_index, min(header_index + header_row_span, len(worksheet_values))
            )
        ]
        if not header_rows:
            return [f"column_{index}" for index in range(1, column_count + 1)]
        if len(header_rows) == 1:
            return [str(value or "").strip() for value in header_rows[0]]

        flattened: list[str] = []
        carry = ""
        top_row = header_rows[0]
        bottom_row = header_rows[1]
        for top_value, bottom_value in zip(top_row, bottom_row, strict=False):
            top_label = str(top_value or "").strip()
            bottom_label = str(bottom_value or "").strip()
            if top_label:
                carry = top_label
            parts = [part for part in [carry, bottom_label] if part]
            flattened.append(" ".join(parts).strip())
        while len(flattened) < column_count:
            flattened.append("")
        return flattened

    def _normalize_header_label(self, raw: str, *, index: int) -> tuple[str, str | None]:
        stripped = str(raw or "").strip()
        if not stripped:
            return f"column_{index}", "empty_header_cell"
        normalized = stripped.replace("\n", " ").replace("\r", " ")
        normalized = _NON_ALPHANUMERIC_PATTERN.sub("_", normalized)
        normalized = _MULTI_UNDERSCORE_PATTERN.sub("_", normalized)
        normalized = normalized.strip("_").lower()
        if not normalized:
            return f"column_{index}", "header_not_identifier_safe"
        if _NUMERIC_VALUE_PATTERN.match(normalized):
            return f"column_{index}", "header_is_numeric_value"
        return normalized, None

    def _duplicate_count(self, values: Iterable[str]) -> int:
        counts: dict[str, int] = {}
        for value in values:
            normalized = value.strip().lower()
            counts[normalized] = counts.get(normalized, 0) + 1
        return sum(max(0, count - 1) for count in counts.values())

    def _string_like_ratio(self, values: list[object | None]) -> float:
        if not values:
            return 0.0
        string_like_count = 0
        for value in values:
            if isinstance(value, str):
                string_like_count += 1
                continue
            if isinstance(value, datetime | date | time):
                continue
            raw = str(value).strip() if value is not None else ""
            if raw and not _NUMERIC_VALUE_PATTERN.match(raw):
                string_like_count += 1
        return string_like_count / len(values)

    def _semantic_label_ratio(self, values: list[object | None]) -> float:
        if not values:
            return 0.0
        semantic_labels = 0
        for value in values:
            raw = str(value or "").strip()
            if not raw:
                continue
            if len(raw) >= 3 and any(character.isalpha() for character in raw):
                semantic_labels += 1
        return semantic_labels / len(values)

    def _data_following_ratio(
        self,
        *,
        worksheet_values: list[tuple[object | None, ...]],
        header_index: int,
        column_count: int,
    ) -> float:
        sampled_rows = worksheet_values[header_index + 1 : header_index + 6]
        if not sampled_rows:
            return 0.0
        sampled_scores: list[float] = []
        for row in sampled_rows:
            limited = list(row[:column_count])
            non_empty = [value for value in limited if str(value or "").strip()]
            if not non_empty:
                continue
            numeric_like = 0
            for value in non_empty:
                if isinstance(value, int | float):
                    numeric_like += 1
                    continue
                raw = str(value).strip()
                if _NUMERIC_VALUE_PATTERN.match(raw):
                    numeric_like += 1
            sampled_scores.append(numeric_like / len(non_empty))
        if not sampled_scores:
            return 0.0
        return sum(sampled_scores) / len(sampled_scores)

    def _non_empty_ratio(self, values: list[object | None], *, column_count: int) -> float:
        if column_count <= 0:
            return 0.0
        non_empty = sum(1 for value in values if str(value or "").strip())
        return non_empty / column_count

    def _compute_detection_confidence(self, *, candidate_scores: list[float]) -> float:
        if not candidate_scores:
            return 0.0
        highest = max(candidate_scores)
        sorted_scores = sorted(candidate_scores, reverse=True)
        runner_up = sorted_scores[1] if len(sorted_scores) > 1 else sorted_scores[0]
        margin = max(0.0, highest - runner_up)
        confidence = 0.60 + min(0.35, margin)
        return max(0.0, min(1.0, confidence))

    def _prune_fully_empty_columns(
        self,
        *,
        headers: list[str],
        original_headers: list[str],
        data_rows: list[list[object | None]],
        fallback_name_reasons: dict[str, str],
    ) -> tuple[list[str], list[str], list[list[object | None]], dict[str, str], int]:
        if not headers:
            return headers, original_headers, data_rows, fallback_name_reasons, 0

        keep_indices: list[int] = []
        for column_index, header_name in enumerate(headers):
            original_header_name = (
                str(original_headers[column_index]).strip()
                if column_index < len(original_headers)
                else ""
            )
            has_data_value = any(
                column_index < len(row) and str(row[column_index] or "").strip()
                for row in data_rows
            )
            if original_header_name:
                keep_indices.append(column_index)
                continue
            if has_data_value:
                keep_indices.append(column_index)
                continue
            # Empty header and empty data => completely empty column.
            if header_name not in fallback_name_reasons:
                continue

        if len(keep_indices) == len(headers):
            return headers, original_headers, data_rows, fallback_name_reasons, 0

        updated_headers = [headers[index] for index in keep_indices]
        updated_original_headers = [original_headers[index] for index in keep_indices]
        updated_rows = [
            [row[index] if index < len(row) else None for index in keep_indices]
            for row in data_rows
        ]
        updated_fallback_name_reasons = {
            key: value
            for key, value in fallback_name_reasons.items()
            if key in set(updated_headers)
        }
        pruned_count = len(headers) - len(updated_headers)
        return (
            updated_headers,
            updated_original_headers,
            updated_rows,
            updated_fallback_name_reasons,
            pruned_count,
        )

    def _validate_file_size(self, path: Path) -> None:
        if int(path.stat().st_size) > self._max_file_bytes:
            raise ExcelFileSizeLimitExceededError(
                "The Excel workbook exceeds the maximum supported file size.",
            )


@dataclass(frozen=True)
class _HeaderSelectionResult:
    header_row_index: int
    header_row_span: int
    column_count: int
    confidence: float
    warnings: list[str]
