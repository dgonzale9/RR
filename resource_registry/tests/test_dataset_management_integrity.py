"""Exercise complete workbook, recovery, migration and consumer invariants."""

from importlib import import_module
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.apps import apps
from django.db import connection
from django.test.utils import CaptureQueriesContext
from openpyxl import Workbook  # type: ignore[import-untyped]

from resource_registry.management_domain.changes import DatasetChangeService
from resource_registry.management_domain.errors import DatasetManagementError
from resource_registry.management_domain.lifecycle import DatasetLifecycleService
from resource_registry.management_domain.worker import DatasetChangeWorker
from resource_registry.models import (
    Dataset,
    DatasetArtifact,
    DatasetChange,
    DatasetVersion,
    OutboxEntry,
)
from resource_registry.read.artifact_read_service import DatasetArtifactReadService
from resource_registry.read.dataset_context_read_service import DatasetContextReadService
from resource_registry.read.exceptions import DatasetVersionNotFoundError
from resource_registry.read.version_read_service import DatasetVersionReadService
from resource_registry.read_auth.context import ReadAuthorizationContext
from resource_registry.services.dataset_intelligence_persistence_service import (
    DatasetIntelligencePersistenceService,
)
from resource_registry.services.dataset_query_executor import DatasetQueryExecutor
from resource_registry.tests.test_dataset_changes import create_verified_intake
from resource_registry.tests.test_dataset_maintenance_flows import Scope, prepare_csv
from resource_registry.tests.test_dataset_management import management_scope  # noqa: F401


def prepare_workbook(
    scope: Scope, path: Path, names: list[str], specification: dict[str, object]
) -> DatasetChange:
    """Prepare a complete workbook through the real local storage worker."""
    manager, _, organization, namespace, _ = scope
    workbook = Workbook()
    workbook.remove(workbook.active)
    for name in names:
        sheet = workbook.create_sheet(name)
        sheet.append(["id", "amount"])
        sheet.append([1, 10])
    workbook.save(path)
    workbook.close()
    change = DatasetChangeService(manager, organization).create(
        namespace_id=namespace.id,
        intake_id=create_verified_intake(manager, path).id,
        idempotency_key=str(uuid4()),
        specification=specification,
    )
    DatasetChangeWorker().run_once(batch_size=1)
    change.refresh_from_db()
    return change


def test_workbook_rename_new_sheet_and_missing_sheet_archive_are_atomic(
    management_scope: Scope, tmp_path: Path
) -> None:
    """Keep sheet identities across renames and roll back the whole publication on failure."""
    manager, _, organization, _, _ = management_scope
    service = DatasetChangeService(manager, organization)
    initial = prepare_workbook(
        management_scope,
        tmp_path / "first.xlsx",
        ["Sales", "Costs"],
        {
            "name": "Book",
            "sheets": [{"sheet_name": "Sales"}, {"sheet_name": "Costs"}],
        },
    )
    published = service.publish(initial.id, expected_revision=initial.revision)
    workbook = published.workbook
    assert workbook is not None
    sales = Dataset.objects.get(workbook=workbook, workbook_sheet="Sales")
    costs = Dataset.objects.get(workbook=workbook, workbook_sheet="Costs")
    first_version = sales.current_version_id
    replacement = prepare_workbook(
        management_scope,
        tmp_path / "renamed.xlsx",
        ["Revenue", "New"],
        {
            "workbook_id": str(workbook.id),
            "workbook_revision": workbook.revision,
            "sheets": [
                {
                    "sheet_name": "Revenue",
                    "dataset_id": str(sales.id),
                    "expected_revision": sales.revision,
                },
                {"sheet_name": "New"},
            ],
            "archive": [{"dataset_id": str(costs.id), "expected_revision": costs.revision}],
        },
    )
    before_events = OutboxEntry.objects.count()
    with patch(
        "resource_registry.management_domain.changes.DatasetRegistryService.attach_artifact",
        side_effect=RuntimeError("storage failed"),
    ):
        with pytest.raises(DatasetManagementError, match="Publication failed"):
            service.publish(replacement.id, expected_revision=replacement.revision)
    sales.refresh_from_db()
    costs.refresh_from_db()
    assert sales.workbook_sheet == "Sales"
    assert costs.archived_at is None
    assert Dataset.objects.filter(workbook=workbook).count() == 2
    assert OutboxEntry.objects.count() == before_events
    replacement.refresh_from_db()
    assert replacement.status == "FAILED"
    service.retry(replacement.id, expected_revision=replacement.revision)
    DatasetChangeWorker().run_once(batch_size=1)
    replacement.refresh_from_db()
    service.publish(replacement.id, expected_revision=replacement.revision)
    sales.refresh_from_db()
    costs.refresh_from_db()
    assert sales.workbook_sheet == "Revenue"
    assert sales.current_version_id == first_version
    assert costs.archive_reason == "MANUAL"
    assert Dataset.objects.filter(workbook=workbook).count() == 3


def test_export_plan_and_context_follow_restoration_without_rewriting_plan(
    management_scope: Scope, tmp_path: Path
) -> None:
    """Freeze export plans while chat context follows the restored current version."""
    manager, editor, organization, namespace, dataset = management_scope
    service = DatasetChangeService(editor, organization)
    first = prepare_csv(management_scope, tmp_path / "first.csv", "id,amount\n1,10\n")
    service.publish(first.id, expected_revision=first.revision)
    dataset.refresh_from_db()
    original = dataset.current_version_id
    plan = DatasetIntelligencePersistenceService().persist_export_plan(
        tenant_id=str(namespace.id),
        dataset_id=str(dataset.id),
        version_id=None,
        result={"artifact_type": "xlsx", "export_plan": {}},
        created_by=editor,
    )
    second = prepare_csv(management_scope, tmp_path / "second.csv", "id,amount\n1,20\n2,30\n")
    service.publish(second.id, expected_revision=second.revision)
    dataset.refresh_from_db()
    assert original is not None
    DatasetLifecycleService(manager, organization).restore_version(
        dataset.id, version_id=original, expected_revision=dataset.revision, reason="Restore"
    )
    context = DatasetContextReadService().get_dataset_context(
        tenant_id=str(namespace.id), dataset_id=str(dataset.id)
    )
    assert context.latest_version is not None
    assert context.schema is not None
    assert context.profile is not None
    assert context.artifact is not None
    assert context.latest_version.id == str(original)
    assert context.schema.dataset_version_id == str(original)
    assert context.profile.dataset_version_id == str(original)
    assert context.artifact.dataset_version_id == str(original)
    query = DatasetQueryExecutor(temp_min_free_bytes=0).execute_query(
        context=ReadAuthorizationContext(tenant_id=str(namespace.id), roles=("reader",)),
        dataset_id=str(dataset.id),
        sql="SELECT amount FROM dataset LIMIT 10",
    )
    assert query["columns"] == ["amount"]
    assert query["rows"] == [[10]]
    assert query["row_count"] == 1
    plan.refresh_from_db()
    assert plan.version_id == original


def test_migration_selects_usable_history_and_never_grants_edit(management_scope: Scope) -> None:
    """Backfill the last usable READY version without reopening incomplete data."""
    manager, editor, _, namespace, dataset = management_scope
    ready = DatasetVersion.objects.create(
        dataset=dataset,
        version_number=1,
        status="READY",
        source_uri="local://ready",
        content_hash="a",
    )
    DatasetArtifact.objects.create(
        dataset_version=ready,
        artifact_role="PRIMARY",
        uri="local://ready",
        format="CSV",
        storage_provider="local",
        size_bytes=1,
    )
    DatasetVersion.objects.create(
        dataset=dataset,
        version_number=2,
        status="READY",
        source_uri="local://missing",
        content_hash="b",
    )
    incomplete = Dataset.objects.create(
        tenant_id=namespace.id,
        organization=dataset.organization,
        workspace=dataset.workspace,
        slug="incomplete",
        name="Incomplete",
        source_type="FILE_UPLOAD",
        status="ACTIVE",
    )
    DatasetVersion.objects.create(
        dataset=incomplete,
        version_number=1,
        status="READY",
        source_uri="local://missing",
        content_hash="c",
    )
    migration = import_module("resource_registry.migrations.0005_backfill_dataset_current_version")
    with connection.schema_editor(atomic=False) as schema_editor:
        migration.initialize_dataset_management(apps, schema_editor)
    dataset.refresh_from_db()
    assert dataset.current_version_id == ready.id
    assert dataset.responsible_user_id == manager.id
    assert not editor.has_perm("resource_registry.change_dataset", dataset)
    with pytest.raises(DatasetVersionNotFoundError):
        DatasetVersionReadService().get_latest_version(
            tenant_id=str(namespace.id), dataset_id=str(incomplete.id)
        )


def test_version_history_queries_remain_bounded(management_scope: Scope, tmp_path: Path) -> None:
    """Bound ORM work when more published versions and artifacts are listed."""
    _, editor, organization, namespace, dataset = management_scope
    service = DatasetChangeService(editor, organization)
    first = prepare_csv(management_scope, tmp_path / "one.csv", "id\n1\n")
    service.publish(first.id, expected_revision=first.revision)
    reader = DatasetVersionReadService()
    with CaptureQueriesContext(connection) as small:
        reader.list_versions(tenant_id=str(namespace.id), dataset_id=str(dataset.id))
    for index in range(2, 6):
        change = prepare_csv(management_scope, tmp_path / f"{index}.csv", f"id\n{index}\n")
        service.publish(change.id, expected_revision=change.revision)
    with CaptureQueriesContext(connection) as large:
        versions = reader.list_versions(tenant_id=str(namespace.id), dataset_id=str(dataset.id))
    assert len(versions) == 5
    assert len(large) <= len(small) + 1
    artifacts = DatasetArtifactReadService().list_artifacts(
        tenant_id=str(namespace.id), dataset_id=str(dataset.id)
    )
    dataset.refresh_from_db()
    assert all(item.dataset_version_id == str(dataset.current_version_id) for item in artifacts)


def test_current_artifacts_are_not_duplicated_by_usability_fallback(
    management_scope: Scope, tmp_path: Path
) -> None:
    """Return source and canonical artifacts exactly once for the selected version."""
    _, editor, organization, namespace, dataset = management_scope
    change = prepare_csv(management_scope, tmp_path / "one.csv", "id\n1\n")
    DatasetChangeService(editor, organization).publish(change.id, expected_revision=change.revision)
    dataset.refresh_from_db()
    assert dataset.current_version is not None
    DatasetArtifact.objects.create(
        dataset_version=dataset.current_version,
        artifact_role="PRIMARY",
        uri="local://source.csv",
        format="CSV",
        size_bytes=5,
        storage_provider="local",
    )
    artifacts = DatasetArtifactReadService().list_artifacts(
        tenant_id=str(namespace.id), dataset_id=str(dataset.id)
    )
    assert len(artifacts) == 2
    assert len({artifact.id for artifact in artifacts}) == 2
    next_change = prepare_csv(management_scope, tmp_path / "two.csv", "id\n2\n")
    DatasetChangeService(editor, organization).publish(
        next_change.id, expected_revision=next_change.revision
    )
    dataset.refresh_from_db()
    latest_id = dataset.current_version_id
    dataset.current_version = None
    dataset.save(update_fields=["current_version"])
    legacy_artifacts = DatasetArtifactReadService().list_artifacts(
        tenant_id=str(namespace.id), dataset_id=str(dataset.id)
    )
    assert len(legacy_artifacts) == 1
    assert legacy_artifacts[0].dataset_version_id == str(latest_id)
