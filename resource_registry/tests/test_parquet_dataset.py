"""Validate Parquet file contents rather than trusting extensions or metadata alone."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pyarrow as arrow  # type: ignore[import-untyped]  # PyArrow has no bundled stubs.
import pytest
from pyarrow import parquet

from resource_registry.services.canonical_dataset_builder import CanonicalDatasetBuilder
from resource_registry.services.parquet_dataset import (
    ParquetDatasetValidationError,
    validate_parquet_dataset,
)


def test_parquet_preserves_native_types_and_bytes(tmp_path: Path) -> None:
    """Validate data batches and preserve the source representation without conversion."""
    source = tmp_path / "data.parquet"
    table = arrow.table(
        {
            "integer": [1, None],
            "decimal": [Decimal("123.45"), None],
            "date": [date(2026, 1, 1), None],
            "nested": [{"items": [1, 2]}, None],
        }
    )
    parquet.write_table(table, source, row_group_size=1)
    validate_parquet_dataset(source)
    with CanonicalDatasetBuilder().build(
        source_path=str(source), source_format="parquet"
    ) as artifact:
        assert artifact.metadata["conversion_applied"] is False
        assert Path(artifact.canonical_path).read_bytes() == source.read_bytes()
        assert parquet.read_table(artifact.canonical_path).equals(table)


@pytest.mark.parametrize("content", [b"", b"name\nvalue\n", b"PAREencrypted", b"PAR1truncated"])
def test_parquet_rejects_invalid_file(tmp_path: Path, content: bytes) -> None:
    """Reject non-Parquet, encrypted-footer, and truncated files as terminal content errors."""
    source = tmp_path / "invalid.parquet"
    source.write_bytes(content)
    with pytest.raises(ParquetDatasetValidationError, match="PARQUET_CONTENT_INVALID"):
        validate_parquet_dataset(source)


def test_parquet_detects_corrupt_data_with_readable_footer(tmp_path: Path) -> None:
    """Decode every batch so a readable footer cannot hide corrupt data pages."""
    source = tmp_path / "corrupt.parquet"
    parquet.write_table(arrow.table({"value": [1, 2, 3]}), source, use_dictionary=False)
    with parquet.ParquetFile(source) as parquet_file:
        offset = parquet_file.metadata.row_group(0).column(0).data_page_offset
    with source.open("r+b") as source_file:
        source_file.seek(offset)
        source_file.write(b"\xff" * 8)
    with pytest.raises(ParquetDatasetValidationError, match="PARQUET_CONTENT_INVALID"):
        validate_parquet_dataset(source)
