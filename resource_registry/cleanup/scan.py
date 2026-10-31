"""Inventory old files without inferring ownership from their presence in storage."""

from __future__ import annotations

import hashlib
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from resource_registry.cleanup.inventory import location_fingerprint, object_identity
from resource_registry.models import (
    ResourceCleanupItem,
    ResourceCleanupRequest,
    ResourceInventoryCursor,
    ResourceStorageObject,
)
from resource_registry.storage.azure_blob import AzureBlobStorageProvider
from resource_registry.storage.base import StorageObjectMetadata
from resource_registry.storage.factory import DataRegistryStorageFactory


def scan_legacy_storage(*, batch_size: int = 100) -> int:
    """Discover at most a bounded page, retaining all ambiguous bytes for explicit review."""
    provider_name = str(getattr(settings, "RESOURCE_REGISTRY_STORAGE_PROVIDER", "local"))
    scope = location_fingerprint(provider_name)
    cursor, _ = ResourceInventoryCursor.objects.get_or_create(identity=scope)
    maximum = max(1, min(batch_size, 100))
    if provider_name == "local":
        root = Path(str(getattr(settings, "RESOURCE_REGISTRY_LOCAL_STORAGE_DIR", "")))
        paths = sorted(
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file() and not path.is_symlink()
        )
        selected = [name for name in paths if name > cursor.continuation][:maximum]
        objects = [
            StorageObjectMetadata(
                uri=f"local://{name}", provider="local", size_bytes=(root / name).stat().st_size
            )
            for name in selected
        ]
        continuation = selected[-1] if selected and selected[-1] != paths[-1] else ""
    elif provider_name == "azure_blob":
        provider = DataRegistryStorageFactory().create_provider("azure_blob")
        if not isinstance(provider, AzureBlobStorageProvider):
            return 0
        objects, continuation = provider.list_inventory_page(
            continuation=cursor.continuation, limit=maximum
        )
    else:
        return 0
    for metadata in objects:
        stored, created = ResourceStorageObject.objects.get_or_create(
            identity=object_identity(provider_name, metadata.uri),
            defaults={
                "provider": provider_name,
                "uri": metadata.uri,
                "location_fingerprint": scope,
                "size_bytes": metadata.size_bytes or 0,
                "etag": metadata.etag or "",
                "ownership_verified": False,
            },
        )
        if not created:
            continue
        request, _ = ResourceCleanupRequest.objects.get_or_create(
            idempotency_key=f"inventory:{stored.id}",
            defaults={
                "kind": "INVENTORY",
                "reason": "Historical storage ownership requires review",
                "status": "BLOCKED",
                "error_code": "ownership_unverified",
                "inventory_fingerprint": hashlib.sha256(str(stored.id).encode()).hexdigest(),
                "execute_after": timezone.now(),
            },
        )
        ResourceCleanupItem.objects.get_or_create(
            request=request,
            storage_object=stored,
            defaults={"status": "BLOCKED", "block_reason": "ownership_unverified"},
        )
    cursor.continuation = continuation
    cursor.save(update_fields=["continuation", "updated_at"])
    return len(objects)
