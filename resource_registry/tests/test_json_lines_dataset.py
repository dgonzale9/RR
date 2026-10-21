"""Verify JSON Lines ingestion against the established JSON dataset contract."""

from pathlib import Path

import duckdb
import pytest
from django.test import override_settings

from resource_registry.services.canonical_dataset_builder import CanonicalDatasetBuilder
from resource_registry.services.dataset_context_builder import DatasetContextBuilder
from resource_registry.services.dataset_engine import DatasetEngine
from resource_registry.services.json_dataset import (
    JsonDatasetValidationError,
    StrictJsonLinesDatasetPolicy,
)
from resource_registry.services.schema_detection import SchemaDetectionStrategyResolver


@pytest.mark.parametrize("extension", ["jsonl", "ndjson", "NDJSON"])
@pytest.mark.parametrize("separator", ["\n", "\r\n"])
@pytest.mark.parametrize("final_newline", [False, True])
def test_json_lines_matches_json_dataset(
    tmp_path: Path, extension: str, separator: str, final_newline: bool
) -> None:
    """Preserve union columns, missing values, decimals, and nested JSON across formats."""
    records = [
        '{"identifier":"á","number":123.4567890123456789,"payload":{"items":[1,true]}}',
        '{"identifier":"b","payload":null,"late":["two"]}',
        '{"identifier":"c","payload":"scalar","flag":false}',
    ]
    json_source = tmp_path / "reference.json"
    json_source.write_text("[" + ",".join(records) + "]", encoding="utf-8")
    source = tmp_path / f"records.{extension}"
    source.write_bytes(
        ("\ufeff" + separator.join(records) + (separator if final_newline else "")).encode("utf-8")
    )
    engine = DatasetEngine()
    assert engine.get_schema(source) == engine.get_schema(json_source)
    assert engine.preview(source) == engine.preview(json_source)
    assert engine.get_row_count(source) == len(records)
    assert DatasetContextBuilder().build_context(source)["source"]["format"] == "jsonl"
    detected = SchemaDetectionStrategyResolver().detect(
        source_path=str(source), source_format=extension
    )
    assert detected.metadata["strategy"] == "json_lines_objects"
    with CanonicalDatasetBuilder().build(
        source_path=str(source), source_format=extension
    ) as artifact:
        assert artifact.source_format == "jsonl"
        assert artifact.metadata["logical_json_columns"] == ["late", "payload"]
        with duckdb.connect(":memory:") as connection:
            result = connection.execute(
                "SELECT json_extract(payload, '$.items[1]') FROM read_parquet(?) "
                "WHERE identifier = ?",
                [artifact.canonical_path, "á"],
            ).fetchall()
    assert result == [("true",)]


@pytest.mark.parametrize(
    ("content", "code"),
    [
        (b"", "JSONL_DATASET_EMPTY"),
        (b"{}", "JSONL_DATASET_HAS_NO_COLUMNS"),
        (b"\n", "JSONL_BLANK_LINE"),
        (b'{"a":1}\n\n{"a":2}', "JSONL_BLANK_LINE"),
        (b'{"a":1}\n\n', "JSONL_BLANK_LINE"),
        (b'[{"a":1}]', "JSONL_RECORD_MUST_BE_OBJECT"),
        (b"null", "JSONL_RECORD_MUST_BE_OBJECT"),
        (b"123", "JSONL_RECORD_MUST_BE_OBJECT"),
        (b'{"a":1,"a":2}', "JSONL_DUPLICATE_KEY"),
        (b'{"a":{"b":1,"b":2}}', "JSONL_DUPLICATE_KEY"),
        (b'{"a":NaN}', "JSONL_NON_FINITE_NUMBER"),
        (b'{"a":Infinity}', "JSONL_NON_FINITE_NUMBER"),
        (b'{"a":-Infinity}', "JSONL_NON_FINITE_NUMBER"),
        (b'{"a":1} {"a":2}', "JSONL_DOCUMENT_INVALID"),
        (b'{"a":\n1}', "JSONL_DOCUMENT_INVALID"),
        (b'{"a":"\xff"}', "JSONL_DOCUMENT_INVALID"),
        (b'{"a":1}\n\xef\xbb\xbf{"a":2}', "JSONL_DOCUMENT_INVALID"),
    ],
)
def test_json_lines_rejects_invalid_content(tmp_path: Path, content: bytes, code: str) -> None:
    """Report stable content errors without exposing the input record."""
    source = tmp_path / "invalid.jsonl"
    source.write_bytes(content)
    with pytest.raises(JsonDatasetValidationError, match=f"^{code}$"):
        StrictJsonLinesDatasetPolicy().scan(source)


@override_settings(RESOURCE_REGISTRY_JSON_MAX_RECORD_BYTES=10)
@pytest.mark.parametrize("record", ['{"á":"b"}', '{"a":"bc"}'])
def test_json_lines_record_limit_counts_utf8_bytes(tmp_path: Path, record: str) -> None:
    """Allow exact byte limits with BOM and CRLF, then reject an oversized record."""
    source = tmp_path / "bounded.jsonl"
    source.write_bytes(b"\xef\xbb\xbf" + record.encode("utf-8") + b"\r\n")
    assert StrictJsonLinesDatasetPolicy().scan(source).columns
    source.write_bytes((record + " ").encode("utf-8") + b"\n")
    with pytest.raises(JsonDatasetValidationError, match="JSONL_RECORD_TOO_LARGE"):
        StrictJsonLinesDatasetPolicy().scan(source)


@override_settings(RESOURCE_REGISTRY_JSON_MAX_RECORD_BYTES=16)
def test_json_lines_late_oversized_record_fails_and_cleans_up(tmp_path: Path) -> None:
    """Reject an oversized record after valid rows without leaving canonical output."""
    source = tmp_path / "bounded.jsonl"
    source.write_bytes(b'{"a":1}\n{"a":"' + b"x" * 4096 + b'"}')
    with pytest.raises(JsonDatasetValidationError, match="JSONL_RECORD_TOO_LARGE"):
        with CanonicalDatasetBuilder().build(source_path=str(source), source_format="jsonl"):
            pytest.fail("Invalid input must not produce a canonical artifact")
