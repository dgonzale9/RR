from __future__ import annotations

from pathlib import Path

from django.conf import settings

from resource_registry.storage.azure_blob import AzureBlobStorageProvider
from resource_registry.storage.base import StorageProvider
from resource_registry.storage.dataset_cache import DatasetCacheStorageProvider
from resource_registry.storage.exceptions import (
    StorageConfigurationError,
    StorageProviderUnsupportedError,
)
from resource_registry.storage.local import LocalStorageProvider


class DataRegistryStorageFactory:
    """Construct storage providers from a short provider key."""

    LOCAL_PROVIDER = "local"
    AZURE_BLOB_PROVIDER = "azure_blob"

    def create_provider(
        self,
        provider_key: str | None = None,
        *,
        local_base_directory: str | Path | None = None,
    ) -> StorageProvider:
        selected_provider = (provider_key or self._default_provider()).strip().lower()
        if selected_provider == "dataset_cache":
            return DatasetCacheStorageProvider(
                base_directory=str(getattr(settings, "RESOURCE_REGISTRY_DATASET_CACHE_DIR", ""))
            )
        if selected_provider == self.LOCAL_PROVIDER:
            base_directory = local_base_directory or self._default_local_directory()
            return LocalStorageProvider(base_directory=base_directory)
        if selected_provider == self.AZURE_BLOB_PROVIDER:
            return AzureBlobStorageProvider()
        raise StorageProviderUnsupportedError(
            f"Unsupported storage provider: {selected_provider}",
        )

    def _default_provider(self) -> str:
        value = getattr(settings, "RESOURCE_REGISTRY_STORAGE_PROVIDER", None)
        if not value:
            return self.LOCAL_PROVIDER
        return str(value)

    def _default_local_directory(self) -> Path:
        value = getattr(settings, "RESOURCE_REGISTRY_LOCAL_STORAGE_DIR", None)
        if value:
            return Path(value)
        if hasattr(settings, "BASE_DIR"):
            return Path(settings.BASE_DIR) / "resource_registry_storage"
        raise StorageConfigurationError("Unable to determine local storage directory.")
