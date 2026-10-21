"""Compose private intake services with the production upload provider."""

from __future__ import annotations

from django.conf import settings

from resource_registry.intake.services import StagedResourceIntakeService
from resource_registry.uploads.azure_provider import AzureBlockBlobLargeFileUploadProvider
from resource_registry.uploads.exceptions import UploadApplicationError
from resource_registry.uploads.local_provider import LocalLargeFileUploadProvider


def get_staged_resource_intake_service() -> StagedResourceIntakeService:
    """Build the private intake service for the configured direct upload provider."""
    provider_key = str(settings.RESOURCE_REGISTRY_UPLOAD_PROVIDER).strip().lower()
    if provider_key == "azure_blob":
        return StagedResourceIntakeService(provider=AzureBlockBlobLargeFileUploadProvider())
    if provider_key == "local":
        return StagedResourceIntakeService(provider=LocalLargeFileUploadProvider())
    raise UploadApplicationError("UPLOAD_PROVIDER_UNAVAILABLE", 503)
