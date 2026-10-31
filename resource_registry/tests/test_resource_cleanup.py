"""Exercise irreversible cleanup through authorized, durable application services."""

import hashlib
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from pytest_django.fixtures import SettingsWrapper

from resource_registry.cleanup.inventory import register_dataset_objects
from resource_registry.cleanup.service import DatasetCleanupService
from resource_registry.cleanup.storage import lease_object
from resource_registry.cleanup.sweeper import discover_cleanup_candidates
from resource_registry.cleanup.worker import CleanupWorker
from resource_registry.management_domain.changes import DatasetChangeService
from resource_registry.management_domain.errors import DatasetManagementError
from resource_registry.models import (
    Dataset,
    DatasetArtifact,
    DatasetManagementConfiguration,
    DatasetVersion,
    DatasetWorkbook,
    ResourceCleanupRequest,
    ResourceStorageObject,
    ResourceStorageReference,
    StagedResourceIntake,
    StagedResourcePublication,
)
from resource_registry.tests.test_dataset_maintenance_flows import Scope, prepare_csv
from resource_registry.tests.test_dataset_management import management_scope  # noqa: F401


def create_archived_dataset_file(scope: Scope, directory: Path) -> tuple[Dataset, Path]:
    """Create a managed, archived dataset without contacting external storage."""
    _, _, organization, _, dataset = scope
    DatasetManagementConfiguration.objects.filter(organization=organization).update(
        cleanup_mode="EXECUTE"
    )
    path = directory / f"{uuid4()}.parquet"
    path.write_bytes(b"private dataset content")
    version = DatasetVersion.objects.create(
        dataset=dataset,
        version_number=1,
        content_hash=str(uuid4()),
        source_uri=f"local://{path.name}",
        status="READY",
    )
    DatasetArtifact.objects.create(
        dataset_version=version,
        artifact_role="PRIMARY",
        uri=f"local://{path.name}",
        format="parquet",
        storage_provider="local",
        size_bytes=path.stat().st_size,
        checksum=hashlib.sha256(path.read_bytes()).hexdigest(),
        metadata_json={"is_canonical": True},
    )
    dataset.current_version = version
    dataset.status = "ARCHIVED"
    dataset.archived_at = timezone.now() - timedelta(days=31)
    dataset.archive_reason = "MANUAL"
    dataset.save()
    register_dataset_objects(dataset)
    return dataset, path


def request_dataset_cleanup(scope: Scope, dataset: Dataset) -> ResourceCleanupRequest:
    """Confirm exactly the inventory reviewed by the dataset manager."""
    manager, _, organization, _, _ = scope
    service = DatasetCleanupService(manager, organization)
    preview = service.preview(dataset.pk)
    return service.request_deletion(
        dataset.pk,
        expected_revision=dataset.revision,
        inventory_fingerprint=str(preview["inventory_fingerprint"]),
        reason="No longer needed",
        idempotency_key=str(uuid4()),
    )


def test_archive_does_not_schedule_deletion_and_explicit_request_waits_thirty_days(
    management_scope: Scope, tmp_path: Path, settings: SettingsWrapper
) -> None:
    """Keep archiving reversible and enforce the minimum recovery deadline."""
    settings.RESOURCE_REGISTRY_LOCAL_STORAGE_DIR = str(tmp_path)
    dataset, path = create_archived_dataset_file(management_scope, tmp_path)
    dataset.archived_at = timezone.now()
    dataset.save(update_fields=["archived_at"])
    assert not ResourceCleanupRequest.objects.exists()
    request = request_dataset_cleanup(management_scope, dataset)
    assert request.execute_after >= dataset.archived_at + timedelta(days=30)
    CleanupWorker().run_once()
    assert path.exists()
    request.refresh_from_db()
    assert request.status == "SCHEDULED"


def test_manager_purges_exclusive_files_and_preserves_version_identity(
    management_scope: Scope, tmp_path: Path, settings: SettingsWrapper
) -> None:
    """Delete physical data while retaining immutable identity and operation evidence."""
    settings.RESOURCE_REGISTRY_LOCAL_STORAGE_DIR = str(tmp_path)
    dataset, path = create_archived_dataset_file(management_scope, tmp_path)
    request = request_dataset_cleanup(management_scope, dataset)
    CleanupWorker().run_once()
    request.refresh_from_db()
    dataset.refresh_from_db()
    assert request.status == "COMPLETED"
    assert not path.exists()
    assert dataset.purged_at is not None
    assert dataset.versions.count() == 1
    assert dataset.name == f"Deleted dataset {dataset.pk}"
    CleanupWorker().run_once()
    assert ResourceCleanupRequest.objects.count() == 1


def test_reader_cannot_request_deletion(management_scope: Scope, tmp_path: Path) -> None:
    """Reject a scoped reader without granting global intake operator permissions."""
    _, editor, organization, _, dataset = management_scope
    with pytest.raises(DatasetManagementError):
        DatasetCleanupService(editor, organization).preview(dataset.pk)


def test_active_dataset_cannot_be_purged(management_scope: Scope) -> None:
    """Protect all published history while a dataset remains active."""
    manager, _, organization, _, dataset = management_scope
    service = DatasetCleanupService(manager, organization)
    preview = service.preview(dataset.pk)
    with pytest.raises(DatasetManagementError, match="Archive"):
        service.request_deletion(
            dataset.pk,
            expected_revision=dataset.revision,
            inventory_fingerprint=str(preview["inventory_fingerprint"]),
            reason="Retire",
            idempotency_key="active",
        )


def test_shared_file_survives_until_last_dataset_is_purged(
    management_scope: Scope, tmp_path: Path, settings: SettingsWrapper
) -> None:
    """Complete exclusive deletion while retaining another dataset's physical content."""
    settings.RESOURCE_REGISTRY_LOCAL_STORAGE_DIR = str(tmp_path)
    dataset, path = create_archived_dataset_file(management_scope, tmp_path)
    shared_dataset = Dataset.objects.create(
        tenant_id=dataset.tenant_id,
        organization=dataset.organization,
        workspace=dataset.workspace,
        slug="shared",
        name="Shared",
        source_type="FILE_UPLOAD",
        status="ACTIVE",
    )
    version = DatasetVersion.objects.create(
        dataset=shared_dataset,
        version_number=1,
        content_hash="shared",
        source_uri=f"local://{path.name}",
        status="READY",
    )
    DatasetArtifact.objects.create(
        dataset_version=version,
        artifact_role="PRIMARY",
        uri=f"local://{path.name}",
        format="PARQUET",
        storage_provider="local",
        size_bytes=1,
    )
    register_dataset_objects(shared_dataset)
    request = request_dataset_cleanup(management_scope, dataset)
    CleanupWorker().run_once()
    request.refresh_from_db()
    assert path.exists()
    assert request.status in {"PARTIAL", "BLOCKED"}
    assert request.items.get().block_reason == "shared_dataset"
    shared_dataset.status = "ARCHIVED"
    shared_dataset.archived_at = timezone.now() - timedelta(days=31)
    shared_dataset.save()
    other_request = request_dataset_cleanup(management_scope, shared_dataset)
    CleanupWorker().run_once()
    ResourceCleanupRequest.objects.filter(pk=request.pk).update(next_attempt_at=timezone.now())
    CleanupWorker().run_once()
    request.refresh_from_db()
    other_request.refresh_from_db()
    assert request.status == other_request.status == "COMPLETED"
    assert not path.exists()


def test_changed_inventory_requires_review(
    management_scope: Scope, tmp_path: Path, settings: SettingsWrapper
) -> None:
    """Do not silently broaden a manager's approved set of files or derivatives."""
    settings.RESOURCE_REGISTRY_LOCAL_STORAGE_DIR = str(tmp_path)
    dataset, path = create_archived_dataset_file(management_scope, tmp_path)
    request = request_dataset_cleanup(management_scope, dataset)
    DatasetVersion.objects.create(
        dataset=dataset,
        version_number=2,
        content_hash="unexpected",
        source_uri="local://new",
        status="READY",
    )
    CleanupWorker().run_once()
    request.refresh_from_db()
    assert request.status == "NEEDS_REVIEW"
    assert path.exists()


def test_simulation_never_deletes_or_retires_content(
    management_scope: Scope, tmp_path: Path, settings: SettingsWrapper
) -> None:
    """Keep rollout simulation side-effect free for data and publications."""
    settings.RESOURCE_REGISTRY_LOCAL_STORAGE_DIR = str(tmp_path)
    dataset, path = create_archived_dataset_file(management_scope, tmp_path)
    request = request_dataset_cleanup(management_scope, dataset)
    DatasetManagementConfiguration.objects.update(cleanup_mode="SIMULATE")
    CleanupWorker().run_once()
    dataset.refresh_from_db()
    assert dataset.purge_started_at is None
    assert path.exists()


def test_cancel_preserves_recovery_before_worker_claim(
    management_scope: Scope, tmp_path: Path, settings: SettingsWrapper
) -> None:
    """Allow cancellation only while the request is still reversible."""
    settings.RESOURCE_REGISTRY_LOCAL_STORAGE_DIR = str(tmp_path)
    dataset, path = create_archived_dataset_file(management_scope, tmp_path)
    request = request_dataset_cleanup(management_scope, dataset)
    manager, _, organization, _, _ = management_scope
    DatasetCleanupService(manager, organization).cancel(request.id)
    CleanupWorker().run_once()
    request.refresh_from_db()
    assert request.status == "CANCELLED"
    assert path.exists()


def test_read_lease_prevents_physical_deletion(
    management_scope: Scope, tmp_path: Path, settings: SettingsWrapper
) -> None:
    """Protect a synchronous read that began before the worker fenced the dataset."""
    settings.RESOURCE_REGISTRY_LOCAL_STORAGE_DIR = str(tmp_path)
    dataset, path = create_archived_dataset_file(management_scope, tmp_path)
    request = request_dataset_cleanup(management_scope, dataset)
    with lease_object("local", f"local://{path.name}"):
        CleanupWorker().run_once()
        assert path.exists()
    ResourceCleanupRequest.objects.filter(pk=request.pk).update(next_attempt_at=timezone.now())
    CleanupWorker().run_once()
    assert not path.exists()


def test_changed_file_is_not_deleted(
    management_scope: Scope, tmp_path: Path, settings: SettingsWrapper
) -> None:
    """Reject storage content replaced outside the reviewed immutable generation."""
    settings.RESOURCE_REGISTRY_LOCAL_STORAGE_DIR = str(tmp_path)
    dataset, path = create_archived_dataset_file(management_scope, tmp_path)
    request = request_dataset_cleanup(management_scope, dataset)
    path.write_bytes(b"different data")
    CleanupWorker().run_once()
    assert path.exists()
    assert request.items.get().block_reason == "object_changed"


def test_automatic_cleanup_cancels_unused_candidate_after_seven_days(
    management_scope: Scope, tmp_path: Path, settings: SettingsWrapper
) -> None:
    """Remove discarded private preparation files without a dataset deletion request."""

    settings.RESOURCE_REGISTRY_LOCAL_STORAGE_DIR = str(tmp_path)
    _, editor, organization, _, _ = management_scope
    change = prepare_csv(management_scope, tmp_path / "source.csv", "id,amount\n1,10\n")
    DatasetChangeService(editor, organization).cancel(change.pk, expected_revision=change.revision)
    DatasetManagementConfiguration.objects.update(cleanup_mode="EXECUTE")
    ResourceStorageObject.objects.update(created_at=timezone.now() - timedelta(days=8))

    ResourceStorageReference.objects.update(created_at=timezone.now() - timedelta(days=8))
    type(change).objects.filter(pk=change.pk).update(
        updated_at=timezone.now() - timedelta(days=8),
        completed_at=timezone.now() - timedelta(days=8),
    )
    discover_cleanup_candidates()
    CleanupWorker().run_once()
    assert ResourceCleanupRequest.objects.filter(kind="CANDIDATE", status="COMPLETED").exists()


def test_purging_dataset_rejects_new_versions_and_artifacts(
    management_scope: Scope, tmp_path: Path, settings: SettingsWrapper
) -> None:
    """Reject legacy producers after irreversible cleanup has fenced the dataset."""
    settings.RESOURCE_REGISTRY_LOCAL_STORAGE_DIR = str(tmp_path)
    dataset, _ = create_archived_dataset_file(management_scope, tmp_path)
    request_dataset_cleanup(management_scope, dataset)
    CleanupWorker().run_once()
    with pytest.raises(DatasetManagementError, match="deleted"):
        DatasetVersion.objects.create(
            dataset=dataset,
            version_number=2,
            content_hash="late",
            source_uri="local://late",
            status="READY",
        )
    with pytest.raises(DatasetManagementError, match="deleted"):
        DatasetArtifact.objects.create(
            dataset_version=dataset.current_version,
            artifact_role="PRIMARY",
            uri="local://late",
            format="PARQUET",
            storage_provider="local",
            size_bytes=1,
        )


def test_live_read_prevents_redaction_as_well_as_file_deletion(
    management_scope: Scope, tmp_path: Path, settings: SettingsWrapper
) -> None:
    """Keep the recoverable dataset intact until a synchronous reader has finished."""
    settings.RESOURCE_REGISTRY_LOCAL_STORAGE_DIR = str(tmp_path)
    dataset, path = create_archived_dataset_file(management_scope, tmp_path)
    request_dataset_cleanup(management_scope, dataset)
    with lease_object("local", f"local://{path.name}"):
        CleanupWorker().run_once()
        dataset.refresh_from_db()
        assert dataset.purge_started_at is None


def test_sibling_sheets_can_be_purged_without_mapping_collision(
    management_scope: Scope, tmp_path: Path, settings: SettingsWrapper
) -> None:
    """Keep distinct neutral sheet identities after deleting two sheets of one book."""

    settings.RESOURCE_REGISTRY_LOCAL_STORAGE_DIR = str(tmp_path)
    dataset, _ = create_archived_dataset_file(management_scope, tmp_path)
    workbook = DatasetWorkbook.objects.create(
        namespace_id=dataset.tenant_id, name="Private workbook"
    )
    dataset.workbook = workbook
    dataset.workbook_sheet = "Sales"
    dataset.save()
    sibling = Dataset.objects.create(
        tenant_id=dataset.tenant_id,
        organization=dataset.organization,
        workspace=dataset.workspace,
        slug="sibling",
        name="Sibling",
        source_type="FILE_UPLOAD",
        status="ARCHIVED",
        archived_at=timezone.now() - timedelta(days=31),
        workbook=workbook,
        workbook_sheet="Other",
    )
    request_dataset_cleanup(management_scope, dataset)
    request_dataset_cleanup(management_scope, sibling)
    CleanupWorker().run_once()
    assert Dataset.objects.filter(workbook=workbook, purged_at__isnull=False).count() == 2


def test_repeated_review_has_bounded_queries(
    management_scope: Scope,
    tmp_path: Path,
    settings: SettingsWrapper,
    django_assert_max_num_queries: object,
) -> None:
    """Avoid per-artifact database work when reviewing an indexed dataset."""

    settings.RESOURCE_REGISTRY_LOCAL_STORAGE_DIR = str(tmp_path)
    dataset, _ = create_archived_dataset_file(management_scope, tmp_path)
    manager, _, organization, _, _ = management_scope
    service = DatasetCleanupService(manager, organization)
    service.preview(dataset.pk)
    with CaptureQueriesContext(connection) as small:
        service.preview(dataset.pk)
    for index in range(12):
        DatasetArtifact.objects.create(
            dataset_version=dataset.current_version,
            artifact_role="EXPORT",
            uri=f"local://extra-{index}",
            format="PARQUET",
            storage_provider="local",
            size_bytes=1,
        )
    service.preview(dataset.pk)
    with CaptureQueriesContext(connection) as large:
        service.preview(dataset.pk)
    assert len(large) <= len(small) + 2


def test_published_intake_source_expires_without_losing_publication(
    management_scope: Scope, tmp_path: Path, settings: SettingsWrapper
) -> None:
    """Remove the retained original while preserving published data and exact replay."""

    settings.RESOURCE_REGISTRY_LOCAL_STORAGE_DIR = str(tmp_path)
    _, editor, organization, namespace, dataset = management_scope
    change = prepare_csv(management_scope, tmp_path / "source.csv", "id,amount\n1,10\n")
    service = DatasetChangeService(editor, organization)
    published = service.publish(change.pk, expected_revision=change.revision)
    DatasetManagementConfiguration.objects.update(cleanup_mode="EXECUTE")
    old = timezone.now() - timedelta(days=31)
    StagedResourceIntake.objects.filter(pk=change.intake_id).update(
        created_at=old, published_at=old, source_last_used_at=old
    )
    StagedResourcePublication.objects.update(completed_at=old)
    discover_cleanup_candidates()
    CleanupWorker().run_once()
    intake = StagedResourceIntake.objects.get(pk=change.intake_id)
    assert intake.status == "PUBLISHED"
    assert intake.source_deleted_at is not None
    assert not (tmp_path / intake.storage_object_key).exists()
    assert dataset.versions.get().artifacts.filter(lifecycle_status="ACTIVE").exists()
    assert service.publish(change.pk, expected_revision=change.revision).result == published.result
    with pytest.raises(DatasetManagementError, match="Upload"):
        DatasetChangeService(editor, organization).create(
            namespace_id=namespace.pk,
            intake_id=intake.pk,
            idempotency_key="new-after-source-deletion",
            specification={"dataset_id": str(dataset.pk), "expected_revision": 1},
        )


def test_inactive_requester_blocks_execution(
    management_scope: Scope, tmp_path: Path, settings: SettingsWrapper
) -> None:
    """Revalidate account admission at execution instead of trusting saved authorization."""
    settings.RESOURCE_REGISTRY_LOCAL_STORAGE_DIR = str(tmp_path)
    dataset, path = create_archived_dataset_file(management_scope, tmp_path)
    request = request_dataset_cleanup(management_scope, dataset)
    manager = management_scope[0]
    manager.is_active = False
    manager.save(update_fields=["is_active"])
    CleanupWorker().run_once()
    request.refresh_from_db()
    assert request.status == "BLOCKED"
    assert request.error_code == "authority_revoked"
    assert path.exists()
