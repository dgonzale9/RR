"""Keep intake publication state consistent with dataset maintenance transactions."""

from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from django.contrib import admin
from django.template.response import TemplateResponse
from django.utils import timezone
from guardian.shortcuts import assign_perm, remove_perm

from Organization.models import Workspace
from resource_registry.intake.services import StagedResourceIntakeService
from resource_registry.management_domain.changes import DatasetChangeService
from resource_registry.management_domain.errors import DatasetManagementError
from resource_registry.management_domain.worker import DatasetChangeWorker
from resource_registry.models import DatasetChange, OutboxEntry, StagedResourceIntake
from resource_registry.storage.factory import DataRegistryStorageFactory
from resource_registry.tests.test_dataset_changes import create_verified_intake
from resource_registry.tests.test_dataset_maintenance_flows import Scope, prepare_csv
from resource_registry.tests.test_dataset_management import management_scope  # noqa: F401
from resource_registry.tests.test_dataset_management_admin import make_admin_request
from resource_registry.tests.test_dataset_management_integrity import prepare_workbook
from resource_registry.uploads.exceptions import UploadApplicationError
from resource_registry.uploads.local_provider import LocalLargeFileUploadProvider


def test_publication_updates_intake_and_admin_without_duplicate_versions(
    management_scope: Scope, tmp_path: Path
) -> None:
    """Show Published only after committing and preserve evidence on repeated requests."""
    manager, editor, organization, _, dataset = management_scope
    change = prepare_csv(management_scope, tmp_path / "sales.csv", "id,amount\n1,10\n")
    assert change.intake_id is not None
    intake = StagedResourceIntake.objects.get(pk=change.intake_id)
    assert intake.status == "READY_FOR_PUBLICATION"
    assert intake.published_at is None

    service = DatasetChangeService(editor, organization)
    published = service.publish(change.id, expected_revision=change.revision)
    intake.refresh_from_db()
    assert intake.status == "PUBLISHED"
    assert intake.published_at == published.completed_at
    assert intake.expires_at is None
    assert not intake.cleanup_required
    event_count = OutboxEntry.objects.count()
    service.publish(change.id, expected_revision=change.revision)
    intake.refresh_from_db()
    assert intake.published_at == published.completed_at
    assert dataset.versions.count() == 1
    assert OutboxEntry.objects.count() == event_count

    assign_perm("resource_registry.manage_stagedresourceintake", manager)
    request = make_admin_request(
        manager,
        organization,
        "/admin/resource_registry/stagedresourceintake/?status__exact=PUBLISHED",
    )
    response = cast(
        TemplateResponse, admin.site._registry[StagedResourceIntake].changelist_view(request)
    )
    assert response.context_data is not None
    assert [row.pk for row in response.context_data["cl"].result_list] == [intake.id]
    assert "Published" in response.render().content.decode()


@pytest.mark.parametrize("authorized", [False, True])
def test_replayed_publication_repairs_old_intake_state_without_republishing(
    management_scope: Scope, tmp_path: Path, authorized: bool
) -> None:
    """Reconcile previous publications using their original completion timestamp."""
    _, editor, organization, _, dataset = management_scope
    change = prepare_csv(management_scope, tmp_path / "sales.csv", "id,amount\n1,10\n")
    assert change.intake_id is not None
    service = DatasetChangeService(editor, organization)
    published = service.publish(change.id, expected_revision=change.revision)
    StagedResourceIntake.objects.filter(pk=change.intake_id).update(
        status="READY_FOR_PUBLICATION", published_at=None
    )
    event_count = OutboxEntry.objects.count()
    if not authorized:
        remove_perm("resource_registry.change_dataset", editor, dataset)
        with pytest.raises(DatasetManagementError):
            service.publish(change.id, expected_revision=change.revision)
        intake = StagedResourceIntake.objects.get(pk=change.intake_id)
        assert intake.status == "READY_FOR_PUBLICATION"
        assert intake.published_at is None
        assert dataset.versions.count() == 1
        assert OutboxEntry.objects.count() == event_count
        return
    result = service.publish(change.id, expected_revision=change.revision)
    intake = StagedResourceIntake.objects.get(pk=change.intake_id)
    assert intake.status == "PUBLISHED"
    assert intake.published_at == published.completed_at
    assert result.revision == published.revision
    assert dataset.versions.count() == 1
    assert OutboxEntry.objects.count() == event_count


@pytest.mark.parametrize("failure", ["storage", "transaction"])
def test_failed_publication_keeps_intake_unpublished(
    management_scope: Scope, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Leave both intake and dataset unchanged after storage or transaction failures."""
    _, editor, organization, _, dataset = management_scope
    change = prepare_csv(management_scope, tmp_path / "sales.csv", "id,amount\n1,10\n")
    assert change.intake_id is not None
    event_count = OutboxEntry.objects.count()
    if failure == "storage":
        DataRegistryStorageFactory().create_provider("local")._resolve_target_path(
            change.prepared["items"][0]["uri"]
        ).unlink()
    else:
        original_save = StagedResourceIntake.save

        def fail_after_publication_save(
            instance: StagedResourceIntake, *, update_fields: list[str]
        ) -> None:
            original_save(instance, update_fields=update_fields)
            if instance.status == "PUBLISHED":
                raise RuntimeError("Simulated transaction failure")

        monkeypatch.setattr(StagedResourceIntake, "save", fail_after_publication_save)
    with pytest.raises((DatasetManagementError, RuntimeError)):
        DatasetChangeService(editor, organization).publish(
            change.id, expected_revision=change.revision
        )
    intake = StagedResourceIntake.objects.get(pk=change.intake_id)
    assert intake.status == "READY_FOR_PUBLICATION"
    assert intake.published_at is None
    assert not dataset.versions.exists()
    assert OutboxEntry.objects.count() == event_count
    change.refresh_from_db()
    assert change.status != "PUBLISHED"


def test_workbook_publication_updates_one_intake_for_all_sheets(
    management_scope: Scope, tmp_path: Path
) -> None:
    """Publish a selected workbook and its source intake in one transaction."""
    manager, _, organization, _, _ = management_scope
    change = prepare_workbook(
        management_scope,
        tmp_path / "book.xlsx",
        ["Sales", "Costs"],
        {"name": "Workbook", "sheets": [{"sheet_name": "Sales"}, {"sheet_name": "Costs"}]},
    )
    assert change.intake_id is not None
    published = DatasetChangeService(manager, organization).publish(
        change.id, expected_revision=change.revision
    )
    intake = StagedResourceIntake.objects.get(pk=change.intake_id)
    assert intake.status == "PUBLISHED"
    assert intake.published_at == published.completed_at
    assert len(published.result["dataset_ids"]) == 2


@pytest.mark.parametrize("copy_fails", [False, True])
def test_legacy_destination_publication_preserves_maintenance_intake(
    management_scope: Scope, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, copy_fails: bool
) -> None:
    """Reuse a maintenance-published source and retain its status when another destination fails."""
    manager, _, organization, namespace, _ = management_scope
    manager.is_superuser = True
    manager.save(update_fields=["is_superuser"])
    path = tmp_path / "sales.csv"
    path.write_text("id,amount\n1,10\n")
    intake = create_verified_intake(manager, path)
    service = DatasetChangeService(manager, organization)
    change = service.create(
        namespace_id=namespace.id,
        intake_id=intake.id,
        specification={"name": "Sales"},
        idempotency_key=str(uuid4()),
    )
    DatasetChangeWorker().run_once(change_id=change.id)
    change.refresh_from_db()
    published = service.publish(change.id, expected_revision=change.revision)
    # Reproduce the corrected status independently to cover legacy compatibility test-first.
    StagedResourceIntake.objects.filter(pk=intake.id).update(
        status="PUBLISHED", published_at=published.completed_at
    )
    workspace = Workspace.objects.create(
        organization=organization,
        name="Other destination",
        slug="other-destination",
        last_modified_by=manager,
        resource_registry_enabled=True,
    )
    provider = LocalLargeFileUploadProvider()
    legacy = StagedResourceIntakeService(provider=provider)

    def fail_copy(*, source_object_key: str, destination_object_key: str) -> None:
        raise UploadApplicationError("SIMULATED_COPY_FAILURE", 503)

    if copy_fails:
        monkeypatch.setattr(provider, "copy_object", fail_copy)
        with pytest.raises(UploadApplicationError) as error:
            legacy.publish_intake(
                user=manager,
                organization=organization,
                intake_id=intake.id,
                visibility_scope="WORKSPACE",
                workspace_id=workspace.id,
            )
        assert error.value.code == "SIMULATED_COPY_FAILURE"
    else:
        result = legacy.publish_intake(
            user=manager,
            organization=organization,
            intake_id=intake.id,
            visibility_scope="WORKSPACE",
            workspace_id=workspace.id,
        )
        assert result.registration_id is not None
    intake.refresh_from_db()
    assert intake.status == "PUBLISHED"
    assert not intake.cleanup_required
    assert DataRegistryStorageFactory().create_provider("local").head(intake.storage_object_key)


@pytest.mark.parametrize("status", ["READY", "NO_CHANGES", "FAILED", "CANCELLED"])
def test_reconciliation_requires_a_completed_publication(
    management_scope: Scope, tmp_path: Path, status: str
) -> None:
    """Avoid advertising private, failed or unchanged proposals as published intakes."""
    change = prepare_csv(management_scope, tmp_path / "sales.csv", "id,amount\n1,10\n")
    assert change.intake_id is not None
    DatasetChange.objects.filter(pk=change.pk).update(status=status, completed_at=timezone.now())
    assert not StagedResourceIntakeService.synchronize_dataset_publication(
        intake_id=change.intake_id
    )
    intake = StagedResourceIntake.objects.get(pk=change.intake_id)
    assert intake.status == "READY_FOR_PUBLICATION"
    assert intake.published_at is None


def test_cancelling_an_unpublished_intake_defers_cleanup_for_pending_use(
    management_scope: Scope, tmp_path: Path
) -> None:
    """Defer cleanup while a pending proposal still requires the original upload."""
    _, editor, _, _, _ = management_scope
    change = prepare_csv(management_scope, tmp_path / "sales.csv", "id,amount\n1,10\n")
    assert change.intake_id is not None
    intake = StagedResourceIntake.objects.get(pk=change.intake_id)
    snapshot = StagedResourceIntakeService(provider=LocalLargeFileUploadProvider()).cancel_intake(
        user=editor, intake_id=intake.id
    )
    assert snapshot.status == "CANCELLED"
    assert DataRegistryStorageFactory().create_provider("local").exists(intake.storage_object_key)
