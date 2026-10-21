from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import ClassVar

from django.conf import settings

from resource_registry.application.commands import RegisterDatasetCommand
from resource_registry.application.exceptions import ValidationError
from resource_registry.storage.base import StorageProvider
from resource_registry.storage.factory import DataRegistryStorageFactory


class DatasetStorageMode(StrEnum):
    LOCAL_REFERENCE = "LOCAL_REFERENCE"
    MANAGED_UPLOAD = "MANAGED_UPLOAD"


@dataclass(frozen=True)
class DatasetStoragePlan:
    mode: DatasetStorageMode
    should_upload: bool
    provider: StorageProvider | None = None
    provider_key: str | None = None
    destination_path: str | None = None


class DatasetStorageStrategy:
    """Resolves provider selection and deterministic artifact paths."""

    _SAFE_SEGMENT_PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"[^a-zA-Z0-9._-]+")
    _PROVIDER_KEY_MAP: ClassVar[dict[str, str]] = {
        "LOCAL": "local",
        "AZURE_BLOB": "azure_blob",
    }

    def __init__(
        self,
        *,
        storage_factory: DataRegistryStorageFactory | None = None,
        default_mode: str | None = None,
    ) -> None:
        self._storage_factory = storage_factory or DataRegistryStorageFactory()
        configured_mode = default_mode or self._read_default_mode()
        self._default_mode = self._parse_mode(configured_mode)

    def create_plan(
        self,
        *,
        command: RegisterDatasetCommand,
        version_number: int,
    ) -> DatasetStoragePlan:
        mode = self._parse_mode(command.storage_mode or self._default_mode.value)
        if mode == DatasetStorageMode.LOCAL_REFERENCE:
            return DatasetStoragePlan(mode=mode, should_upload=False)

        provider_key = self._resolve_provider_key(command.storage_provider)
        provider = self._storage_factory.create_provider(provider_key)
        destination_path = self.build_destination_path(
            tenant_id=command.tenant_id,
            dataset_slug=command.slug,
            version_number=version_number,
            source_path=command.path,
        )
        return DatasetStoragePlan(
            mode=mode,
            should_upload=True,
            provider=provider,
            provider_key=provider_key or getattr(provider, "PROVIDER_NAME", None),
            destination_path=destination_path,
        )

    def build_destination_path(
        self,
        *,
        tenant_id: str,
        dataset_slug: str,
        version_number: int,
        source_path: str,
    ) -> str:
        tenant_segment = self._safe_segment(tenant_id)
        slug_segment = self._safe_segment(dataset_slug)
        suffix = Path(source_path).suffix.lower() or ".bin"
        file_name = f"original{suffix}"
        destination = (
            PurePosixPath("datasets")
            / tenant_segment
            / slug_segment
            / f"v{version_number}"
            / file_name
        )
        return destination.as_posix()

    def _read_default_mode(self) -> str:
        value = getattr(settings, "RESOURCE_REGISTRY_STORAGE_MODE", None)
        if not value:
            return DatasetStorageMode.LOCAL_REFERENCE.value
        return str(value)

    def _parse_mode(self, raw_mode: str | DatasetStorageMode) -> DatasetStorageMode:
        value = str(raw_mode).strip().upper()
        try:
            return DatasetStorageMode(value)
        except ValueError as error:
            raise ValidationError(f"Unsupported storage_mode '{raw_mode}'.") from error

    def _resolve_provider_key(self, storage_provider: str | None) -> str | None:
        if storage_provider is None:
            return None
        normalized = storage_provider.strip().upper()
        return self._PROVIDER_KEY_MAP.get(normalized, normalized.lower())

    def _safe_segment(self, value: str) -> str:
        normalized = self._SAFE_SEGMENT_PATTERN.sub("-", str(value)).strip("-")
        normalized = normalized.strip(".")
        if not normalized:
            raise ValidationError("Unable to create safe artifact path segment.")
        return normalized
