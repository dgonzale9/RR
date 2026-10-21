"""Resolve supported upload formats independently from upload workflow state."""

from __future__ import annotations

from pathlib import PurePath

from django.conf import settings

from resource_registry.uploads.contracts import SupportedUploadFormat
from resource_registry.uploads.exceptions import UploadApplicationError


class UploadFileFormatPolicy:
    """Resolve dataset formats from safe filenames and compatible declared MIME types."""

    def resolve(self, *, filename: str, declared_content_type: str) -> SupportedUploadFormat:
        """Resolve one configured upload format or fail closed.

        Args:
            filename: Safe filename whose extension selects the format.
            declared_content_type: MIME type reported by the client or storage provider.

        Returns:
            Trusted upload format, with NDJSON normalized to JSONL.

        Raises:
            UploadApplicationError: If the extension is disabled or the MIME type is incompatible.
        """
        suffix = PurePath(filename).suffix.lower()
        content_type = declared_content_type.strip().lower()
        formats = {
            ".xlsx": SupportedUploadFormat.XLSX,
            ".csv": SupportedUploadFormat.CSV,
            ".json": SupportedUploadFormat.JSON,
            ".jsonl": SupportedUploadFormat.JSONL,
            ".ndjson": SupportedUploadFormat.JSONL,
            ".parquet": SupportedUploadFormat.PARQUET,
        }
        allowed_extensions = frozenset(
            getattr(settings, "RESOURCE_REGISTRY_UPLOAD_ALLOWED_EXTENSIONS", ())
        )
        file_format = formats.get(suffix)
        if suffix not in allowed_extensions or file_format is None:
            raise UploadApplicationError("UPLOAD_UNSUPPORTED_FILE_FORMAT", 400)
        allowed_content_types = frozenset(
            getattr(
                settings, f"RESOURCE_REGISTRY_UPLOAD_ALLOWED_CONTENT_TYPES_{file_format.value}", ()
            )
        )
        if content_type not in allowed_content_types:
            raise UploadApplicationError("UPLOAD_EXTENSION_CONTENT_TYPE_MISMATCH", 400)
        return file_format

    @staticmethod
    def extension_for(file_format: SupportedUploadFormat) -> str:
        """Return a trusted storage suffix for one resolved format.

        Args:
            file_format: Validated upload format.

        Returns:
            Canonical suffix to use for server-selected storage objects.
        """
        return {
            SupportedUploadFormat.XLSX: ".xlsx",
            SupportedUploadFormat.CSV: ".csv",
            SupportedUploadFormat.JSON: ".json",
            SupportedUploadFormat.JSONL: ".jsonl",
            SupportedUploadFormat.PARQUET: ".parquet",
        }[file_format]
