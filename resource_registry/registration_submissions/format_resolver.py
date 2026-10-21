"""Resolve trusted completed-upload formats into registration job families."""

from __future__ import annotations

from typing import ClassVar

from resource_registry.registration_submissions.contracts import RegistrationJobKind
from resource_registry.uploads.contracts import SupportedUploadFormat
from resource_registry.uploads.exceptions import UploadApplicationError


class RegistrationSubmissionFormatResolver:
    """Map persisted upload formats to one provider-independent job family."""

    _ROUTES: ClassVar[dict[SupportedUploadFormat, RegistrationJobKind]] = {
        SupportedUploadFormat.XLSX: RegistrationJobKind.WORKBOOK_INGESTION,
        SupportedUploadFormat.CSV: RegistrationJobKind.DATASET_REGISTRATION,
        SupportedUploadFormat.JSON: RegistrationJobKind.DATASET_REGISTRATION,
        SupportedUploadFormat.JSONL: RegistrationJobKind.DATASET_REGISTRATION,
        SupportedUploadFormat.PARQUET: RegistrationJobKind.DATASET_REGISTRATION,
    }

    def resolve(self, *, resolved_file_format: str) -> RegistrationJobKind:
        """Resolve one persisted upload format or fail closed.

        Args:
            resolved_file_format: Server-persisted UploadSession format value.

        Returns:
            Registration job family selected for the trusted format.

        Raises:
            UploadApplicationError: If the persisted format is unsupported.
        """
        try:
            upload_format = SupportedUploadFormat(resolved_file_format)
            return self._ROUTES[upload_format]
        except (KeyError, ValueError) as error:
            raise UploadApplicationError("UPLOAD_FORMAT_UNSUPPORTED", 409) from error
