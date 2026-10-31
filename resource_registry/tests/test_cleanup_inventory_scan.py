"""Verify bounded legacy discovery keeps ambiguous storage content intact."""

from pathlib import Path

import pytest
from pytest_django.fixtures import SettingsWrapper

from resource_registry.cleanup.scan import scan_legacy_storage
from resource_registry.models import ResourceCleanupRequest, ResourceStorageObject


@pytest.mark.django_db
def test_legacy_scan_persists_unknown_files_without_deleting_them(
    tmp_path: Path, settings: SettingsWrapper
) -> None:
    """Retain old unreferenced bytes until their provenance is established."""
    settings.RESOURCE_REGISTRY_STORAGE_PROVIDER = "local"
    settings.RESOURCE_REGISTRY_LOCAL_STORAGE_DIR = str(tmp_path)
    files = [tmp_path / f"unknown-{index}.parquet" for index in range(3)]
    for path in files:
        path.write_bytes(b"unknown provenance")
    scan_legacy_storage(batch_size=2)
    assert ResourceStorageObject.objects.count() == 2
    assert not ResourceStorageObject.objects.filter(ownership_verified=True).exists()
    assert (
        ResourceCleanupRequest.objects.filter(
            status="BLOCKED", error_code="ownership_unverified"
        ).count()
        == 2
    )
    scan_legacy_storage(batch_size=2)
    assert ResourceStorageObject.objects.count() == 3
    assert all(path.exists() for path in files)
