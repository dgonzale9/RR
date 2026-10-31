"""Exercise storage failures, restart recovery and content redaction without external I/O."""

from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.utils import timezone
from pytest_django.fixtures import SettingsWrapper

from resource_registry.cleanup.reservations import lease_dataset
from resource_registry.cleanup.service import DatasetCleanupService
from resource_registry.cleanup.sweeper import discover_cleanup_candidates
from resource_registry.cleanup.worker import CleanupWorker
from resource_registry.management_domain.changes import DatasetChangeService
from resource_registry.models import (
    DatasetArtifact,
    DatasetChartArtifact,
    DatasetManagementConfiguration,
    DatasetProfile,
    DatasetSchema,
    ResourceCleanupRequest,
    StagedResourceIntake,
    StagedResourcePublication,
)
from resource_registry.tests.test_dataset_maintenance_flows import Scope, prepare_csv
from resource_registry.tests.test_dataset_management import management_scope  # noqa: F401
from resource_registry.tests.test_resource_cleanup import (
    create_archived_dataset_file,
    request_dataset_cleanup,
)


def test_delete_failures_stop_after_five_attempts_and_allow_manager_retry(
    management_scope: Scope, tmp_path: Path, settings: SettingsWrapper
) -> None:
    """Preserve failures as durable retryable results without treating them as absent files."""
    settings.RESOURCE_REGISTRY_LOCAL_STORAGE_DIR = str(tmp_path)
    dataset, path = create_archived_dataset_file(management_scope, tmp_path)
    request = request_dataset_cleanup(management_scope, dataset)
    with patch.object(
        CleanupWorker, "_delete_physical_object", side_effect=PermissionError("denied")
    ) as deletion:
        for _ in range(7):
            ResourceCleanupRequest.objects.filter(pk=request.pk).update(
                next_attempt_at=timezone.now()
            )
            CleanupWorker().run_once()
        assert deletion.call_count == 5
    request.refresh_from_db()
    assert request.status == "FAILED"
    assert path.exists()
    manager, _, organization, _, _ = management_scope
    DatasetCleanupService(manager, organization).retry(request.pk)
    CleanupWorker().run_once()
    request.refresh_from_db()
    assert request.status == "COMPLETED"
    assert not path.exists()


def test_restart_after_successful_delete_before_recording_result_is_idempotent(
    management_scope: Scope, tmp_path: Path, settings: SettingsWrapper
) -> None:
    """Recover an interrupted claim after storage deletion but before its database receipt."""
    settings.RESOURCE_REGISTRY_LOCAL_STORAGE_DIR = str(tmp_path)
    dataset, path = create_archived_dataset_file(management_scope, tmp_path)
    request = request_dataset_cleanup(management_scope, dataset)
    worker = CleanupWorker()
    token = worker._claim_request(request.id)
    assert token is not None
    path.unlink()
    ResourceCleanupRequest.objects.filter(pk=request.pk).update(
        lease_expires_at=timezone.now() - timedelta(minutes=1)
    )
    CleanupWorker().run_once()
    request.refresh_from_db()
    assert request.status == "COMPLETED"
    assert request.items.get().status == "DELETED"


def test_purge_redacts_schema_profiles_and_derivatives(
    management_scope: Scope, tmp_path: Path, settings: SettingsWrapper
) -> None:
    """Preserve identifiers while removing data-bearing Registry records."""
    settings.RESOURCE_REGISTRY_LOCAL_STORAGE_DIR = str(tmp_path)
    dataset, _ = create_archived_dataset_file(management_scope, tmp_path)
    schema = DatasetSchema.objects.create(
        dataset_version=dataset.current_version,
        column_count=1,
        schema_hash="fixture",
        schema_json=[{"name": "private"}],
    )
    profile = DatasetProfile.objects.create(
        dataset_version=dataset.current_version, metrics_json={"sample": "private"}
    )
    chart = DatasetChartArtifact.objects.create(
        dataset=dataset,
        tenant_id=dataset.tenant_id,
        title="Private chart",
        chart_type="bar",
        chart_spec_json={"data": [10]},
    )
    request_dataset_cleanup(management_scope, dataset)
    CleanupWorker().run_once()
    schema.refresh_from_db()
    profile.refresh_from_db()
    chart.refresh_from_db()
    assert schema.schema_json == []
    assert profile.metrics_json == {}
    assert chart.chart_spec_json == {}
    assert chart.title == "Deleted chart"


def test_storage_configuration_change_blocks_deletion(
    management_scope: Scope, tmp_path: Path, settings: SettingsWrapper
) -> None:
    """Do not reinterpret a reviewed URI inside a different storage root."""
    settings.RESOURCE_REGISTRY_LOCAL_STORAGE_DIR = str(tmp_path)
    dataset, path = create_archived_dataset_file(management_scope, tmp_path)
    request = request_dataset_cleanup(management_scope, dataset)
    other_root = tmp_path / "replacement-root"
    other_root.mkdir()
    replacement = other_root / path.name
    replacement.write_bytes(path.read_bytes())
    settings.RESOURCE_REGISTRY_LOCAL_STORAGE_DIR = str(other_root)
    CleanupWorker().run_once()
    assert request.items.get().block_reason == "storage_location_changed"
    assert path.exists() and replacement.exists()


def test_dataset_lease_protects_cached_synchronous_use(
    management_scope: Scope, tmp_path: Path, settings: SettingsWrapper
) -> None:
    """Keep a dataset recoverable while a query uses its already materialized copy."""
    settings.RESOURCE_REGISTRY_LOCAL_STORAGE_DIR = str(tmp_path)
    dataset, path = create_archived_dataset_file(management_scope, tmp_path)
    request_dataset_cleanup(management_scope, dataset)
    with lease_dataset(dataset.id):
        CleanupWorker().run_once()
        dataset.refresh_from_db()
        assert dataset.purge_started_at is None
        assert path.exists()


def test_published_original_is_retained_when_independent_copy_is_missing(
    management_scope: Scope, tmp_path: Path, settings: SettingsWrapper
) -> None:
    """Do not delete the last available bytes based only on an ACTIVE artifact row."""
    settings.RESOURCE_REGISTRY_LOCAL_STORAGE_DIR = str(tmp_path)
    _, editor, organization, _, dataset = management_scope
    change = prepare_csv(management_scope, tmp_path / "source.csv", "id,amount\n1,10\n")
    DatasetChangeService(editor, organization).publish(change.pk, expected_revision=change.revision)
    DatasetManagementConfiguration.objects.update(cleanup_mode="EXECUTE")
    old = timezone.now() - timedelta(days=31)
    StagedResourceIntake.objects.filter(pk=change.intake_id).update(
        created_at=old, published_at=old, source_last_used_at=old
    )
    StagedResourcePublication.objects.update(completed_at=old)
    for artifact in DatasetArtifact.objects.filter(dataset_version__dataset=dataset):
        (tmp_path / artifact.uri.removeprefix("local://")).unlink(missing_ok=True)
    discover_cleanup_candidates()
    CleanupWorker().run_once()
    intake = StagedResourceIntake.objects.get(pk=change.intake_id)
    assert intake.source_deleted_at is None
    assert (tmp_path / intake.storage_object_key).exists()


def test_global_shutdown_stops_cleanup_between_objects(
    management_scope: Scope, tmp_path: Path, settings: SettingsWrapper
) -> None:
    """Keep physical content intact when the cleanup kill switch is off."""
    settings.RESOURCE_REGISTRY_LOCAL_STORAGE_DIR = str(tmp_path)
    dataset, path = create_archived_dataset_file(management_scope, tmp_path)
    request = request_dataset_cleanup(management_scope, dataset)
    settings.RESOURCE_REGISTRY_CLEANUP_COORDINATOR_ENABLED = False
    CleanupWorker().run_once()
    dataset.refresh_from_db()
    assert dataset.purge_started_at is None
    assert path.exists()
    assert request.items.get().attempts == 0
