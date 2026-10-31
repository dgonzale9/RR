"""Protect historical versions and concurrent dependency registration."""

import shutil
from pathlib import Path

import pytest
from django.db import transaction
from django.utils import timezone
from pytest_django.fixtures import SettingsWrapper

from resource_registry.cleanup.admission import lock_referenced_datasets
from resource_registry.cleanup.worker import CleanupWorker
from resource_registry.management_domain.errors import DatasetManagementError
from resource_registry.models import (
    Dataset,
    DatasetLineage,
    DatasetVersion,
    ResourceStorageObject,
    WorkbookIngestionGroup,
)
from resource_registry.read.dtos import DatasetArtifactSummary
from resource_registry.services.materialized_dataset_cache import MaterializedDatasetCache
from resource_registry.services.workbook_retention_service import WorkbookRetentionService
from resource_registry.tests.test_dataset_maintenance_flows import Scope
from resource_registry.tests.test_dataset_management import management_scope  # noqa: F401
from resource_registry.tests.test_materialized_dataset_cache import _AdapterSpy
from resource_registry.tests.test_resource_cleanup import (
    create_archived_dataset_file,
    request_dataset_cleanup,
)


def test_legacy_retention_preserves_historical_versions(
    management_scope: Scope, tmp_path: Path, settings: SettingsWrapper
) -> None:
    """Keep every usable version until the manager explicitly purges its dataset."""
    settings.RESOURCE_REGISTRY_LOCAL_STORAGE_DIR = str(tmp_path)
    dataset, path = create_archived_dataset_file(management_scope, tmp_path)
    group = WorkbookIngestionGroup.objects.create(
        tenant_id=dataset.tenant_id, source_hash="fixture"
    )
    dataset.workbook_group = group
    dataset.status = "ACTIVE"
    dataset.archived_at = None
    dataset.save()
    DatasetVersion.objects.create(
        dataset=dataset, version_number=2, content_hash="new", status="READY"
    )
    dataset.current_version = dataset.versions.get(version_number=2)
    dataset.save()
    result = WorkbookRetentionService().apply_workbook_retention(
        tenant_id=str(dataset.tenant_id), workbook_group_id=str(group.id), dry_run=False
    )
    assert result.artifacts_selected == 0
    assert dataset.versions.get(version_number=1).artifacts.get().lifecycle_status == "ACTIVE"
    assert path.exists()


def test_new_reference_rejects_dataset_after_purge_starts(management_scope: Scope) -> None:
    """Prevent an exact version reference from being admitted after its purge fence."""

    _, _, _, _, dataset = management_scope
    version = DatasetVersion.objects.create(
        dataset=dataset, version_number=1, content_hash="fixture"
    )
    Dataset.objects.filter(pk=dataset.pk).update(purge_started_at=timezone.now())
    with transaction.atomic(), pytest.raises(DatasetManagementError):
        lock_referenced_datasets({"source": {"version_id": str(version.id)}})


def test_query_cache_is_inventoried_before_its_files_are_written(
    management_scope: Scope,
    tmp_path: Path,
    settings: SettingsWrapper,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inventory both cached bytes and metadata before persisting them."""
    settings.RESOURCE_REGISTRY_LOCAL_STORAGE_DIR = str(tmp_path)
    settings.RESOURCE_REGISTRY_DATASET_CACHE_DIR = str(tmp_path / "cache")
    dataset, path = create_archived_dataset_file(management_scope, tmp_path)
    artifact = dataset.current_version.artifacts.get()
    summary = DatasetArtifactSummary(
        id=str(artifact.id),
        dataset_version_id=str(dataset.current_version_id),
        tenant_id=str(dataset.tenant_id),
        artifact_role="PRIMARY",
        uri=artifact.uri,
        format="parquet",
        size_bytes=artifact.size_bytes,
        checksum=artifact.checksum,
        storage_provider="local",
        lifecycle_status="ACTIVE",
        created_at=artifact.created_at.isoformat(),
        metadata_json={},
    )
    move = shutil.move

    def assert_inventory_before_move(source: str, destination: str) -> str:
        assert (
            ResourceStorageObject.objects.filter(provider="dataset_cache", state="WRITING").count()
            == 2
        )
        return move(source, destination)

    monkeypatch.setattr(shutil, "move", assert_inventory_before_move)
    cache = MaterializedDatasetCache(
        cache_directory=tmp_path / "cache", ttl_seconds=3600, max_bytes=100000
    )
    cache.get_or_materialize(
        tenant_id=str(dataset.tenant_id),
        dataset_id=str(dataset.id),
        version_id=str(dataset.current_version_id),
        artifact=summary,
        adapter=_AdapterSpy(source_path=path),
    )
    assert (
        ResourceStorageObject.objects.filter(provider="dataset_cache", state="AVAILABLE").count()
        == 2
    )


def test_materialized_lineage_is_audit_and_does_not_block_source_purge(
    management_scope: Scope, tmp_path: Path, settings: SettingsWrapper
) -> None:
    """Keep lineage evidence without treating an independent materialized result as a reader."""
    settings.RESOURCE_REGISTRY_LOCAL_STORAGE_DIR = str(tmp_path)
    dataset, path = create_archived_dataset_file(management_scope, tmp_path)
    downstream = Dataset.objects.create(
        tenant_id=dataset.tenant_id,
        organization=dataset.organization,
        workspace=dataset.workspace,
        slug="materialized",
        name="Materialized",
        status="ACTIVE",
        source_type="FILE_UPLOAD",
    )
    version = DatasetVersion.objects.create(
        dataset=downstream, version_number=1, content_hash="materialized"
    )
    evidence = DatasetLineage.objects.create(
        upstream_dataset_version=dataset.current_version,
        downstream_dataset_version=version,
        lineage_type="DERIVED_FROM",
    )
    request = request_dataset_cleanup(management_scope, dataset)
    CleanupWorker().run_once()
    request.refresh_from_db()
    assert request.status == "COMPLETED"
    assert not path.exists()
    assert DatasetLineage.objects.filter(pk=evidence.pk).exists()
