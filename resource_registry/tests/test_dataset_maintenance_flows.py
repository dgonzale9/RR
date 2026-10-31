"""Exercise publication compatibility, workbook identity, rollback and lifecycle behavior."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import close_old_connections, connection, connections
from django.test.utils import CaptureQueriesContext
from guardian.shortcuts import assign_perm
from openpyxl import Workbook  # type: ignore[import-untyped]  # Parser has no installed stubs.

from Organization.models import Organization
from resource_registry.application.commands import RegisterDatasetCommand
from resource_registry.application.exceptions import ValidationError
from resource_registry.application.validators import RegisterDatasetCommandValidator
from resource_registry.management_domain.catalog import DatasetCatalogService
from resource_registry.management_domain.changes import DatasetChangeService
from resource_registry.management_domain.errors import DatasetManagementError
from resource_registry.management_domain.lifecycle import DatasetLifecycleService
from resource_registry.management_domain.worker import DatasetChangeWorker
from resource_registry.models import (
    Dataset,
    DatasetChange,
    DatasetManagementConfiguration,
    RegistryNamespace,
)
from resource_registry.services.dataset_query_job_service import DatasetQueryJobService
from resource_registry.storage.factory import DataRegistryStorageFactory
from resource_registry.tests.background_processing_helpers import (
    deferred_background_tasks,  # noqa: F401
)
from resource_registry.tests.test_dataset_changes import create_verified_intake
from resource_registry.tests.test_dataset_management import management_scope  # noqa: F401

Scope = tuple[User, User, Organization, RegistryNamespace, Dataset]


def prepare_csv(scope: Scope, path: Path, content: str) -> DatasetChange:
    """Prepare an editor-owned replacement for the current dataset revision."""
    _, editor, organization, namespace, dataset = scope
    dataset.refresh_from_db()
    assign_perm("resource_registry.change_dataset", editor, dataset)
    path.write_text(content, encoding="utf-8")
    intake = create_verified_intake(editor, path)
    change = DatasetChangeService(editor, organization).create(
        namespace_id=namespace.id,
        intake_id=intake.id,
        idempotency_key=str(uuid4()),
        specification={"dataset_id": str(dataset.id), "expected_revision": dataset.revision},
    )
    DatasetChangeWorker().run_once(batch_size=1)
    change.refresh_from_db()
    return change


def test_breaking_update_requires_manager_and_restoration_keeps_history(
    management_scope: Scope, tmp_path: Path
) -> None:
    """Publish compatible changes, gate removed columns and restore the original pointer."""
    manager, editor, organization, _, dataset = management_scope
    first = prepare_csv(management_scope, tmp_path / "first.csv", "id,amount\n1,10\n")
    DatasetChangeService(editor, organization).publish(first.id, expected_revision=first.revision)
    dataset.refresh_from_db()
    original = dataset.current_version_id
    assert original is not None
    breaking = prepare_csv(management_scope, tmp_path / "second.csv", "id\n1\n")
    assert breaking.status == "NEEDS_MANAGER"
    with pytest.raises(DatasetManagementError, match="manager"):
        DatasetChangeService(editor, organization).publish(
            breaking.id, expected_revision=breaking.revision
        )
    DatasetChangeService(manager, organization).publish(
        breaking.id, expected_revision=breaking.revision
    )
    dataset.refresh_from_db()
    DatasetLifecycleService(manager, organization).restore_version(
        dataset.id, version_id=original, expected_revision=dataset.revision, reason="Rollback"
    )
    dataset.refresh_from_db()
    assert dataset.current_version_id == original
    assert dataset.versions.count() == 2
    assert len(DatasetCatalogService(editor, organization).get_detail(dataset.id)["schema"]) == 2


def test_identical_and_historical_content_do_not_duplicate_versions(
    management_scope: Scope, tmp_path: Path
) -> None:
    """Detect canonical content despite changed filenames and historical reuploads."""
    _, editor, organization, _, dataset = management_scope
    first = prepare_csv(management_scope, tmp_path / "first.csv", "id,amount\n1,10\n")
    service = DatasetChangeService(editor, organization)
    service.publish(first.id, expected_revision=first.revision)
    repeated = prepare_csv(management_scope, tmp_path / "renamed.csv", "id,amount\n1,10\n")
    assert repeated.status == "NO_CHANGES"
    second = prepare_csv(management_scope, tmp_path / "second.csv", "id,amount\n1,20\n")
    service.publish(second.id, expected_revision=second.revision)
    historical = prepare_csv(management_scope, tmp_path / "historical.csv", "id,amount\n1,10\n")
    assert historical.prepared["items"][0]["historical_version_id"]
    assert dataset.versions.count() == 2


def test_concurrent_metadata_change_blocks_stale_publication(
    management_scope: Scope, tmp_path: Path
) -> None:
    """Prevent prepared data from silently overwriting a more recent dataset revision."""
    _, editor, organization, _, dataset = management_scope
    change = prepare_csv(management_scope, tmp_path / "first.csv", "id,amount\n1,10\n")
    DatasetLifecycleService(editor, organization).update_metadata(
        dataset.id, {"description": "New"}, expected_revision=0
    )
    with pytest.raises(DatasetManagementError, match="changed"):
        DatasetChangeService(editor, organization).publish(
            change.id, expected_revision=change.revision
        )
    assert not dataset.versions.exists()


def test_workbook_invalid_sheet_blocks_all_selected_sheets(
    management_scope: Scope, tmp_path: Path
) -> None:
    """Keep candidates private until the invalid worksheet is explicitly excluded."""
    manager, _, organization, namespace, _ = management_scope
    workbook = Workbook()
    workbook.active.title = "Sales"
    workbook.active.append(["id", "amount"])
    workbook.active.append([1, 10])
    workbook.create_sheet("Empty")
    path = tmp_path / "book.xlsx"
    workbook.save(path)
    workbook.close()
    intake = create_verified_intake(manager, path)
    service = DatasetChangeService(manager, organization)
    change = service.create(
        namespace_id=namespace.id,
        intake_id=intake.id,
        idempotency_key="book",
        specification={
            "name": "Book",
            "sheets": [{"sheet_name": "Sales"}, {"sheet_name": "Empty"}],
        },
    )
    DatasetChangeWorker().run_once(batch_size=1)
    change.refresh_from_db()
    assert change.status == "FAILED"
    assert not Dataset.objects.filter(workbook__isnull=False).exists()
    assert change.prepared["available_sheets"] == ["Sales", "Empty"]
    service.revise(
        change.id,
        {
            "name": "Book",
            "sheets": [{"sheet_name": "Sales"}, {"sheet_name": "Empty", "selected": False}],
        },
        expected_revision=change.revision,
    )
    DatasetChangeWorker().run_once(batch_size=1)
    change.refresh_from_db()
    published = service.publish(change.id, expected_revision=change.revision)
    assert published.status == "PUBLISHED"
    assert Dataset.objects.filter(workbook_id=published.workbook_id).count() == 1


def test_pilot_disabled_preserves_reads_and_blocks_mutations(management_scope: Scope) -> None:
    """Keep pointer-aware reads working after mutation rollback."""
    manager, _, organization, _, dataset = management_scope
    DatasetManagementConfiguration.objects.filter(organization=organization).update(enabled=False)
    assert DatasetCatalogService(manager, organization).get_detail(dataset.id)["id"] == str(
        dataset.id
    )
    with pytest.raises(DatasetManagementError, match="not enabled"):
        DatasetLifecycleService(manager, organization).archive(
            dataset.id, expected_revision=0, reason="Disabled"
        )


def test_query_job_pins_current_version_at_submission(
    management_scope: Scope, tmp_path: Path
) -> None:
    """Keep queued work on its selected version after a new publication."""
    _, editor, organization, namespace, dataset = management_scope
    first = prepare_csv(management_scope, tmp_path / "one.csv", "id,amount\n1,10\n")
    service = DatasetChangeService(editor, organization)
    service.publish(first.id, expected_revision=first.revision)
    dataset.refresh_from_db()
    original = dataset.current_version_id
    job = DatasetQueryJobService().create_job(
        tenant_id=str(namespace.id),
        dataset_id=str(dataset.id),
        version_id=None,
        sql_text="SELECT * FROM dataset",
        created_by=editor,
    )
    second = prepare_csv(management_scope, tmp_path / "two.csv", "id,amount\n1,20\n")
    service.publish(second.id, expected_revision=second.revision)
    job.refresh_from_db()
    assert job.version_id == original


def test_catalog_queries_do_not_grow_per_dataset(management_scope: Scope) -> None:
    """Compare collection query counts with realistic responsible-user relationships."""
    manager, _, organization, namespace, dataset = management_scope
    dataset.responsible_user = manager
    dataset.save(update_fields=["responsible_user"])
    with CaptureQueriesContext(connection) as small:
        DatasetCatalogService(manager, organization).list_datasets({})
    for index in range(15):
        Dataset.objects.create(
            tenant_id=namespace.id,
            organization=organization,
            workspace=dataset.workspace,
            slug=f"other-{index}",
            name=f"Other {index}",
            source_type="FILE_UPLOAD",
            responsible_user=manager,
        )
    with CaptureQueriesContext(connection) as large:
        result = DatasetCatalogService(manager, organization).list_datasets({})
    assert result["count"] == 16
    assert len(large) <= len(small) + 1


def test_legacy_registration_requires_explicit_target_when_pilot_enabled(
    management_scope: Scope, tmp_path: Path
) -> None:
    """Reject legacy filename-based updates while preserving legacy creation."""
    manager, _, _, namespace, dataset = management_scope
    path = tmp_path / "sales.csv"
    path.write_text("id\n1\n")
    command = RegisterDatasetCommand(
        tenant_id=str(namespace.id),
        slug=dataset.slug,
        name="Sales",
        path=str(path),
        source_type="FILE_UPLOAD",
        created_by=manager,
    )
    with pytest.raises(ValidationError, match="explicit"):
        RegisterDatasetCommandValidator().validate(command)


def test_disabling_pilot_keeps_legacy_updates_blocked(
    management_scope: Scope, tmp_path: Path
) -> None:
    """Prevent rollback from reopening implicit name-based replacement."""
    manager, _, organization, namespace, dataset = management_scope
    call_command("configure_dataset_management", organization_id=organization.id, mode="enable")
    call_command("configure_dataset_management", organization_id=organization.id, mode="disable")
    path = tmp_path / "same.csv"
    path.write_text("id\n1\n")
    command = RegisterDatasetCommand(
        tenant_id=str(namespace.id),
        slug=dataset.slug,
        name="Sales",
        path=str(path),
        source_type="FILE_UPLOAD",
        created_by=manager,
    )
    with pytest.raises(ValidationError, match="explicit"):
        RegisterDatasetCommandValidator().validate(command)


def test_missing_candidate_storage_preserves_current_version(
    management_scope: Scope, tmp_path: Path
) -> None:
    """Reject unavailable candidate artifacts before entering publication locks."""
    _, editor, organization, _, dataset = management_scope
    change = prepare_csv(management_scope, tmp_path / "candidate.csv", "id\n1\n")
    item = change.prepared["items"][0]
    DataRegistryStorageFactory().create_provider(item["provider"])._resolve_target_path(
        item["uri"]
    ).unlink()
    with pytest.raises(DatasetManagementError, match="unavailable"):
        DatasetChangeService(editor, organization).publish(
            change.id, expected_revision=change.revision
        )
    assert not dataset.versions.exists()


def test_recovery_rejects_physically_missing_current_artifact(
    management_scope: Scope, tmp_path: Path
) -> None:
    """Keep a manual archive closed when its serving artifact was removed."""
    manager, editor, organization, _, dataset = management_scope
    change = prepare_csv(management_scope, tmp_path / "first.csv", "id\n1\n")
    DatasetChangeService(editor, organization).publish(change.id, expected_revision=change.revision)
    dataset.refresh_from_db()
    service = DatasetLifecycleService(manager, organization)
    service.archive(dataset.id, expected_revision=dataset.revision, reason="Retired")
    dataset.refresh_from_db()
    item = change.prepared["items"][0]
    DataRegistryStorageFactory().create_provider(item["provider"])._resolve_target_path(
        item["uri"]
    ).unlink()
    with pytest.raises(DatasetManagementError, match="unavailable"):
        service.unarchive(dataset.id, expected_revision=dataset.revision, reason="Recover")
    dataset.refresh_from_db()
    assert dataset.status == "ARCHIVED"


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("deferred_background_tasks")
def test_concurrent_publish_requests_create_one_version(
    management_scope: Scope, tmp_path: Path
) -> None:
    """Serialize concurrent replays of the same prepared proposal."""
    _, editor, organization, _, dataset = management_scope
    change = prepare_csv(management_scope, tmp_path / "concurrent.csv", "id\n1\n")

    def publish_copy(index: int) -> str:
        close_old_connections()
        try:
            actor = User.objects.get(pk=editor.pk)
            host = Organization.objects.get(pk=organization.pk)
            return str(
                DatasetChangeService(actor, host)
                .publish(change.id, expected_revision=change.revision)
                .id
            )
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        identifiers = list(executor.map(publish_copy, range(2)))
    assert identifiers == [str(change.id), str(change.id)]
    assert dataset.versions.count() == 1
