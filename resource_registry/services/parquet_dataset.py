"""Validate uploaded Parquet data before publication or registration."""

from pathlib import Path

from pyarrow import ArrowException  # type: ignore[import-untyped]  # PyArrow has no bundled stubs.
from pyarrow import parquet as pyarrow_parquet


class ParquetDatasetValidationError(ValueError):
    """Report a safe terminal Parquet content validation error."""


def validate_parquet_dataset(source_path: str | Path) -> None:
    """Read the schema and every data batch of an unencrypted Parquet file.

    Args:
        source_path: Local file to validate without retaining the complete dataset.

    Raises:
        ParquetDatasetValidationError: If the file is corrupt, encrypted, or has no columns.
        OSError: If the local file cannot be opened or read.
    """
    try:
        with Path(source_path).open("rb") as source_file:
            if source_file.read(4) != b"PAR1":
                raise ParquetDatasetValidationError("PARQUET_CONTENT_INVALID")
            with pyarrow_parquet.ParquetFile(source_file) as parquet_file:
                if not parquet_file.schema_arrow.names:
                    raise ParquetDatasetValidationError("PARQUET_DATASET_HAS_NO_COLUMNS")
                for batch in parquet_file.iter_batches(batch_size=1024, use_threads=False):
                    batch.validate(full=True)
    except (ArrowException, ValueError) as error:
        if isinstance(error, ParquetDatasetValidationError):
            raise
        raise ParquetDatasetValidationError("PARQUET_CONTENT_INVALID") from error
    except OSError as error:
        # Arrow also reports corrupt pages and encrypted columns as OSError,
        # without an OS errno. Real filesystem errors keep their retry behavior.
        if error.errno is not None:
            raise
        raise ParquetDatasetValidationError("PARQUET_CONTENT_INVALID") from error
