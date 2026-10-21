"""Parse Resource Registry JSON datasets with bounded, repeatable passes."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from django.conf import settings


class JsonDatasetValidationError(ValueError):
    """Report a safe, stable JSON dataset validation failure."""


@dataclass(frozen=True)
class JsonDatasetSchema:
    """Represent the deterministic union schema from a JSON dataset scan."""

    columns: tuple[str, ...]
    json_columns: frozenset[str]


class JsonDatasetPolicy(Protocol):
    """Define JSON reading, classification, and serialization for Registry datasets."""

    def iter_rows(self, source_path: str | Path) -> Iterator[Mapping[str, object]]:
        """Yield validated JSON object rows."""

    def scan(self, source_path: str | Path) -> JsonDatasetSchema:
        """Return the deterministic JSON dataset schema."""

    def is_json_text_column(self, values: Sequence[str | None]) -> bool:
        """Return whether values form one logical JSON column."""

    def serialize(self, value: object) -> str:
        """Serialize one logical JSON value."""


def _reject_duplicate_keys(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    """Build an object while rejecting duplicate member names."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise JsonDatasetValidationError("JSON_DUPLICATE_KEY")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> object:
    """Reject non-standard JSON numeric constants."""
    _ = value
    raise JsonDatasetValidationError("JSON_NON_FINITE_NUMBER")


def iter_json_dataset_rows(source_path: str | Path) -> Iterator[Mapping[str, object]]:
    """Yield object rows from a non-empty root array without loading the document."""
    decoder = json.JSONDecoder(
        object_pairs_hook=_reject_duplicate_keys,
        parse_float=Decimal,
        parse_int=Decimal,
        parse_constant=_reject_non_finite,
    )
    maximum_record_bytes = max(
        int(getattr(settings, "RESOURCE_REGISTRY_JSON_MAX_RECORD_BYTES", 64 * 1024**2)), 1
    )
    try:
        with Path(source_path).open("r", encoding="utf-8-sig") as source_file:
            buffer = ""
            position = 0
            complete = False

            def refill() -> bool:
                nonlocal buffer, position, complete
                chunk = source_file.read(64 * 1024)
                if not chunk:
                    complete = True
                    return False
                buffer = buffer[position:] + chunk
                position = 0
                if len(buffer.encode("utf-8")) > maximum_record_bytes:
                    raise JsonDatasetValidationError("JSON_RECORD_TOO_LARGE")
                return True

            def next_character() -> str | None:
                nonlocal position
                while True:
                    while position < len(buffer) and buffer[position].isspace():
                        position += 1
                    if position < len(buffer):
                        character = buffer[position]
                        position += 1
                        return character
                    if not refill():
                        return None

            if next_character() != "[":
                raise JsonDatasetValidationError("JSON_ROOT_MUST_BE_NONEMPTY_ARRAY")
            row_count = 0
            while True:
                first_character = next_character()
                if first_character is None:
                    raise JsonDatasetValidationError("JSON_DOCUMENT_INVALID")
                if first_character == "]":
                    break
                position -= 1
                while True:
                    try:
                        value, position = decoder.raw_decode(buffer, position)
                        break
                    except json.JSONDecodeError as error:
                        if complete:
                            raise JsonDatasetValidationError("JSON_DOCUMENT_INVALID") from error
                        refill()
                if not isinstance(value, dict):
                    raise JsonDatasetValidationError("JSON_ARRAY_ELEMENTS_MUST_BE_OBJECTS")
                row_count += 1
                yield value
                delimiter = next_character()
                if delimiter == "]":
                    break
                if delimiter != ",":
                    raise JsonDatasetValidationError("JSON_DOCUMENT_INVALID")
            if next_character() is not None or row_count == 0:
                raise JsonDatasetValidationError("JSON_ROOT_MUST_BE_NONEMPTY_ARRAY")
    except JsonDatasetValidationError:
        raise
    except (OSError, UnicodeDecodeError) as error:
        raise JsonDatasetValidationError("JSON_DOCUMENT_INVALID") from error


def scan_json_dataset(source_path: str | Path) -> JsonDatasetSchema:
    """Scan a JSON dataset once to determine deterministic columns and JSON fields."""
    return _scan_json_rows(iter_json_dataset_rows(source_path))


def _scan_json_rows(rows: Iterable[Mapping[str, object]]) -> JsonDatasetSchema:
    """Collect the shared union schema without retaining records."""
    columns: list[str] = []
    json_columns: set[str] = set()
    for row in rows:
        for key, value in row.items():
            if key not in columns:
                columns.append(key)
            if isinstance(value, (dict, list)):
                json_columns.add(key)
    if not columns:
        raise JsonDatasetValidationError("JSON_DATASET_HAS_NO_COLUMNS")
    return JsonDatasetSchema(tuple(columns), frozenset(json_columns))


def iter_json_lines_dataset_rows(source_path: str | Path) -> Iterator[Mapping[str, object]]:
    """Yield bounded object records from a UTF-8 JSONL or NDJSON file.

    Args:
        source_path: File containing exactly one object per physical line.

    Yields:
        Validated object records, preserving the JSON dataset value semantics.

    Raises:
        JsonDatasetValidationError: If a record is invalid, oversized, or the file is empty.
        OSError: If the source cannot be read.
    """
    decoder = json.JSONDecoder(
        object_pairs_hook=_reject_duplicate_keys,
        parse_float=Decimal,
        parse_int=Decimal,
        parse_constant=_reject_non_finite,
    )
    maximum_record_bytes = max(
        int(getattr(settings, "RESOURCE_REGISTRY_JSON_MAX_RECORD_BYTES", 64 * 1024**2)), 1
    )
    record_count = 0
    try:
        with Path(source_path).open("rb") as source_file:
            while True:
                # Allow the first BOM and CRLF outside the record's byte budget.
                overhead = 5 if record_count == 0 else 2
                line = source_file.readline(maximum_record_bytes + overhead + 1)
                if not line:
                    break
                record = line.removesuffix(b"\n").removesuffix(b"\r")
                if record_count == 0:
                    record = record.removeprefix(b"\xef\xbb\xbf")
                if len(record) > maximum_record_bytes:
                    raise JsonDatasetValidationError("JSONL_RECORD_TOO_LARGE")
                if not record.strip():
                    raise JsonDatasetValidationError("JSONL_BLANK_LINE")
                value = decoder.decode(record.decode("utf-8"))
                if not isinstance(value, dict):
                    raise JsonDatasetValidationError("JSONL_RECORD_MUST_BE_OBJECT")
                record_count += 1
                yield value
        if record_count == 0:
            raise JsonDatasetValidationError("JSONL_DATASET_EMPTY")
    except JsonDatasetValidationError as error:
        raise JsonDatasetValidationError(str(error).replace("JSON_", "JSONL_", 1)) from error
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise JsonDatasetValidationError("JSONL_DOCUMENT_INVALID") from error


def is_json_text_column(values: Sequence[str | None]) -> bool:
    """Return whether a complete tabular column is a logical JSON column."""
    decoder = json.JSONDecoder(
        object_pairs_hook=_reject_duplicate_keys,
        parse_float=Decimal,
        parse_int=Decimal,
        parse_constant=_reject_non_finite,
    )
    has_container = False
    for value in values:
        if value is None or not value.strip() or value.strip() == "null":
            continue
        try:
            parsed = decoder.decode(value)
        except (JsonDatasetValidationError, json.JSONDecodeError):
            return False
        has_container = has_container or isinstance(parsed, (dict, list))
    return has_container


def serialize_json_value(value: object) -> str:
    """Serialize a parsed JSON value without converting decimals to strings."""
    if value is None:
        return "null"
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, list):
        return "[" + ",".join(serialize_json_value(item) for item in value) + "]"
    if isinstance(value, dict):
        return (
            "{"
            + ",".join(
                json.dumps(str(key), ensure_ascii=False) + ":" + serialize_json_value(item)
                for key, item in value.items()
            )
            + "}"
        )
    raise JsonDatasetValidationError("JSON_VALUE_INVALID")


class StrictJsonDatasetPolicy:
    """Apply strict JSON rules without knowing storage, jobs, or UI."""

    def iter_rows(self, source_path: str | Path) -> Iterator[Mapping[str, object]]:
        """Yield validated rows from a JSON dataset.

        Args:
            source_path: JSON dataset file to stream.

        Returns:
            Validated object rows.
        """
        return iter_json_dataset_rows(source_path)

    def scan(self, source_path: str | Path) -> JsonDatasetSchema:
        """Return the deterministic schema for a JSON dataset.

        Args:
            source_path: JSON dataset file to scan.

        Returns:
            Deterministic JSON dataset schema.
        """
        return scan_json_dataset(source_path)

    def is_json_text_column(self, values: Sequence[str | None]) -> bool:
        """Return whether values form a logical JSON column.

        Args:
            values: Complete tabular column values.

        Returns:
            Whether all non-empty values are compatible JSON.
        """
        return is_json_text_column(values)

    def serialize(self, value: object) -> str:
        """Serialize one logical JSON value.

        Args:
            value: Parsed JSON-compatible value.

        Returns:
            Valid JSON text.
        """
        return serialize_json_value(value)


class StrictJsonLinesDatasetPolicy(StrictJsonDatasetPolicy):
    """Apply JSON dataset semantics to bounded JSON Lines records."""

    def iter_rows(self, source_path: str | Path) -> Iterator[Mapping[str, object]]:
        """Read JSONL records without retaining the complete file.

        Args:
            source_path: JSONL or NDJSON file to read.

        Returns:
            Iterator of validated object records.

        Raises:
            JsonDatasetValidationError: During iteration if the content is invalid.
            OSError: During iteration if the file cannot be read.
        """
        return iter_json_lines_dataset_rows(source_path)

    def scan(self, source_path: str | Path) -> JsonDatasetSchema:
        """Build the same union schema as JSON from a JSON Lines source.

        Args:
            source_path: JSONL or NDJSON file to scan.

        Returns:
            Ordered columns and logical JSON column names.

        Raises:
            JsonDatasetValidationError: If content is invalid or contains no columns.
            OSError: If the file cannot be read.
        """
        try:
            return _scan_json_rows(self.iter_rows(source_path))
        except JsonDatasetValidationError as error:
            raise JsonDatasetValidationError(str(error).replace("JSON_", "JSONL_", 1)) from error
