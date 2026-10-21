"""Composition boundary for large-file upload REST adapters."""

from __future__ import annotations

from django.conf import settings

from resource_registry.uploads.azure_provider import AzureBlockBlobLargeFileUploadProvider
from resource_registry.uploads.exceptions import UploadApplicationError
from resource_registry.uploads.services import LargeFileUploadService


def get_large_file_upload_service() -> LargeFileUploadService:
    """Build the production upload service with the configured Azure adapter."""
    if str(settings.RESOURCE_REGISTRY_UPLOAD_PROVIDER).strip().lower() != "azure_blob":
        raise UploadApplicationError("UPLOAD_PROVIDER_UNAVAILABLE", 503)
    return LargeFileUploadService(provider=AzureBlockBlobLargeFileUploadProvider())
