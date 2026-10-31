from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import ClassVar

from django.db.models import Q
from django.utils.text import slugify

from resource_registry.application.commands import RegisterDatasetCommand
from resource_registry.application.exceptions import (
    DuplicateDatasetError,
    UnsupportedDatasetSourceError,
    ValidationError,
)
from resource_registry.models import ArtifactRole, Dataset, DatasetSourceType, RegistryNamespace


class RegisterDatasetCommandValidator:
    """Reusable validator for dataset registration commands."""

    _SUPPORTED_SUFFIXES: ClassVar[set[str]] = {
        ".csv",
        ".json",
        ".jsonl",
        ".ndjson",
        ".parquet",
        ".xlsx",
    }
    _SUPPORTED_STORAGE_PROVIDERS: ClassVar[set[str]] = {"LOCAL", "S3", "AZURE_BLOB", "GCS"}
    _SUPPORTED_STORAGE_MODES: ClassVar[set[str]] = {"LOCAL_REFERENCE", "MANAGED_UPLOAD"}
    _XLSX_MIME: ClassVar[str] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    def validate(
        self,
        command: RegisterDatasetCommand,
        *,
        allow_existing_slug: bool = True,
    ) -> None:
        if not command.tenant_id:
            raise ValidationError("tenant_id is required.")
        if not command.slug:
            raise ValidationError("slug is required.")
        normalized_slug = slugify(command.slug)
        if command.slug != normalized_slug:
            raise ValidationError(
                f"slug must be normalized (suggested: '{normalized_slug}').",
            )
        if not command.name:
            raise ValidationError("name is required.")
        if not command.path:
            raise ValidationError("path is required.")
        if command.preview_limit <= 0:
            raise ValidationError("preview_limit must be greater than 0.")
        if command.storage_mode.upper() not in self._SUPPORTED_STORAGE_MODES:
            raise ValidationError(f"Unsupported storage_mode '{command.storage_mode}'.")

        if command.source_type not in set(DatasetSourceType.values):
            raise UnsupportedDatasetSourceError(
                f"Unsupported source_type '{command.source_type}'.",
            )
        if command.artifact_role not in set(ArtifactRole.values):
            raise ValidationError(f"Unsupported artifact_role '{command.artifact_role}'.")
        if (
            command.storage_provider is not None
            and command.storage_provider.upper() not in self._SUPPORTED_STORAGE_PROVIDERS
        ):
            raise ValidationError(
                f"Unsupported storage_provider '{command.storage_provider}'.",
            )

        dataset_path = Path(command.path)
        if not dataset_path.exists() or not dataset_path.is_file():
            raise ValidationError(f"Dataset path does not exist or is not a file: {command.path}")
        if not self._is_supported_dataset_format(
            dataset_path=dataset_path,
            mime_type=command.mime_type,
        ):
            raise UnsupportedDatasetSourceError(
                "Only CSV, JSON, JSONL/NDJSON, Parquet, and XLSX dataset files are supported.",
            )

        if (
            not allow_existing_slug
            and Dataset.objects.filter(
                tenant_id=command.tenant_id,
                slug=command.slug,
            ).exists()
        ):
            raise DuplicateDatasetError(
                f"Dataset slug '{command.slug}' already exists for tenant '{command.tenant_id}'.",
            )
        self.require_explicit_update_if_enabled(command.tenant_id, command.slug)

    @staticmethod
    def require_explicit_update_if_enabled(tenant_id: str, slug: str) -> None:
        """Reject name-based replacement in destinations using dataset maintenance.

        Args:
            tenant_id: Existing Registry namespace identifier.
            slug: Legacy name-derived dataset identifier.

        Raises:
            ValidationError: If a matching dataset requires an explicit update proposal.
        """
        enabled = RegistryNamespace.objects.filter(
            Q(organization__datasetmanagementconfiguration__enabled=True)
            | Q(workspace__organization__datasetmanagementconfiguration__enabled=True)
            | Q(organization__datasetmanagementconfiguration__legacy_updates_blocked=True)
            | Q(
                workspace__organization__datasetmanagementconfiguration__legacy_updates_blocked=True
            ),
            pk=tenant_id,
        ).exists()
        if enabled and Dataset.objects.filter(tenant_id=tenant_id, slug=slug).exists():
            raise ValidationError(
                "An explicit dataset update is required. Create a dataset-change proposal with the target ID.",
                status=409,
                code="explicit_dataset_update_required",
            )

    def _is_supported_dataset_format(self, *, dataset_path: Path, mime_type: str | None) -> bool:
        suffix = dataset_path.suffix.lower()
        if suffix in self._SUPPORTED_SUFFIXES:
            return True
        provided_mime = str(mime_type or "").strip().lower()
        guessed_mime, _ = mimetypes.guess_type(str(dataset_path))
        guessed_mime_value = str(guessed_mime or "").strip().lower()
        return provided_mime == self._XLSX_MIME or guessed_mime_value == self._XLSX_MIME
