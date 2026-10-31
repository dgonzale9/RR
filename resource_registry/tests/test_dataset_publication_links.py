"""Integrate maintenance publications with intake destination and version evidence."""

from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.contrib import admin
from django.contrib.auth.models import User
from django.db import close_old_connections, connection, connections
from django.test.utils import CaptureQueriesContext
from guardian.shortcuts import remove_perm

from resource_registry.admin import StagedResourceIntakeAdmin, StagedResourcePublicationInline
from resource_registry.intake.services import StagedResourceIntakeService
from resource_registry.management_domain.changes import DatasetChangeService
from resource_registry.management_domain.errors import DatasetManagementError
from resource_registry.management_domain.worker import DatasetChangeWorker
from resource_registry.models import (
    Dataset,
    DatasetArtifact,
    DatasetChange,
    DatasetVersion,
    RegistryNamespace,
    StagedResourceIntake,
    StagedResourcePublication,
    StagedResourcePublicationDataset,
    WorkbookIngestionJobStatus,
)
from resource_registry.services.destination_assignment_removal_service import (
    DestinationAssignmentRemovalService,
)
from resource_registry.tests.background_processing_helpers import (
    deferred_background_tasks,  # noqa: F401
)
from resource_registry.tests.test_dataset_changes import create_verified_intake
from resource_registry.tests.test_dataset_maintenance_flows import Scope, prepare_csv
from resource_registry.tests.test_dataset_management import management_scope  # noqa: F401
from resource_registry.tests.test_dataset_management_admin import make_admin_request
from resource_registry.tests.test_dataset_management_integrity import prepare_workbook
from resource_registry.uploads.exceptions import UploadApplicationError
from resource_registry.uploads.local_provider import LocalLargeFileUploadProvider


def test_publication_records_destination_actor_and_exact_version(
    management_scope: Scope, tmp_path: Path
) -> None:
    """Record real publication evidence without creating a legacy registration job."""
    manager, editor, organization, namespace, dataset = management_scope
    change = prepare_csv(management_scope, tmp_path / "sales.csv", "id,amount\n1,10\n")
    assert not StagedResourcePublication.objects.exists()
    service = DatasetChangeService(manager, organization)
    published = service.publish(change.id, expected_revision=change.revision)
    assert StagedResourcePublication.objects.count() == 1
    publication = StagedResourcePublication.objects.get(intake_id=change.intake_id)
    dataset.refresh_from_db()
    assert publication.registry_namespace_id == namespace.id
    assert publication.organization_id == organization.id
    assert publication.workspace_id == namespace.workspace_id
    assert publication.published_by_id == manager.id != editor.id
    assert publication.completed_at == published.completed_at
    assert publication.registration_submission_id is None
    assert publication.upload_session_id is None
    assert published.publication_id == publication.id
    assert list(publication.dataset_assignments.values_list("dataset_version_id", flat=True)) == [
        dataset.current_version_id
    ]
    assert published.result["versions"] == [
        {"dataset_id": str(dataset.id), "dataset_version_id": str(dataset.current_version_id)}
    ]
    service.publish(change.id, expected_revision=change.revision)
    assert StagedResourcePublication.objects.count() == 1
    assert publication.dataset_assignments.count() == 1
    assert dataset.versions.count() == 1


def test_shared_dataset_publications_detach_independently_and_replay_stays_detached(
    management_scope: Scope, tmp_path: Path
) -> None:
    """Preserve another active publication and never reactivate a detached replay."""
    manager, editor, organization, _, dataset = management_scope
    manager.is_superuser = True
    manager.save(update_fields=["is_superuser"])
    service = DatasetChangeService(editor, organization)
    first = prepare_csv(management_scope, tmp_path / "first.csv", "id,amount\n1,10\n")
    service.publish(first.id, expected_revision=first.revision)
    first_version = dataset.versions.get().id
    second = prepare_csv(management_scope, tmp_path / "renamed.csv", "id,amount\n1,20\n")
    service.publish(second.id, expected_revision=second.revision)
    assert StagedResourcePublication.objects.count() == 2
    first_publication = StagedResourcePublication.objects.get(intake_id=first.intake_id)
    second_publication = StagedResourcePublication.objects.get(intake_id=second.intake_id)
    assert first_publication.dataset_assignments.get().dataset_version_id == first_version
    removal = DestinationAssignmentRemovalService()
    removal.request_detachment(user=manager, publication_id=first_publication.id, reason="Retired")
    dataset.refresh_from_db()
    assert dataset.status == "ACTIVE"
    service.publish(first.id, expected_revision=first.revision)
    first_publication.refresh_from_db()
    assert first_publication.assignment_status == "DETACHED"
    assert first_publication.dataset_assignments.get().detached_at is not None
    removal.request_detachment(user=manager, publication_id=second_publication.id, reason="Retired")
    dataset.refresh_from_db()
    assert dataset.status == "ARCHIVED"
    assert dataset.versions.count() == 2


def test_one_intake_destination_reuses_publication_and_blocks_legacy_resubmission(
    management_scope: Scope, tmp_path: Path
) -> None:
    """Associate multiple explicit proposals with one destination without another old job."""
    manager, _, organization, namespace, _ = management_scope
    manager.is_superuser = True
    manager.save(update_fields=["is_superuser"])
    path = tmp_path / "source.csv"
    path.write_text("id,amount\n1,10\n")
    intake = create_verified_intake(manager, path)
    service = DatasetChangeService(manager, organization)
    for name in ("First dataset", "Second dataset"):
        change = service.create(
            namespace_id=namespace.id,
            intake_id=intake.id,
            idempotency_key=str(uuid4()),
            specification={"name": name},
        )
        DatasetChangeWorker().run_once(change_id=change.id)
        change.refresh_from_db()
        service.publish(change.id, expected_revision=change.revision)
    assert intake.publications.count() == 1
    publication = intake.publications.get()
    assert publication.dataset_assignments.count() == 2
    assert publication.dataset_changes.count() == 2
    with pytest.raises(UploadApplicationError) as error:
        StagedResourceIntakeService(provider=LocalLargeFileUploadProvider()).publish_intake(
            user=manager,
            organization=organization,
            intake_id=intake.id,
            visibility_scope="WORKSPACE",
            workspace_id=namespace.workspace_id,
        )
    assert error.value.code == "INTAKE_ALREADY_PUBLISHED"
    publication.refresh_from_db()
    assert publication.registration_submission_id is None
    assert not publication.cleanup_required


@pytest.mark.parametrize("fail_publication", [False, True])
def test_workbook_links_selected_versions_atomically(
    management_scope: Scope, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_publication: bool
) -> None:
    """Commit all selected version assignments or roll them back with their proposal."""
    manager, _, organization, _, _ = management_scope
    change = prepare_workbook(
        management_scope,
        tmp_path / "book.xlsx",
        ["Sales", "Costs"],
        {"name": "Book", "sheets": [{"sheet_name": "Sales"}, {"sheet_name": "Costs"}]},
    )
    if fail_publication:

        def fail_intake_save(instance: StagedResourceIntake, *, update_fields: list[str]) -> None:
            if instance.status == "PUBLISHED":
                raise RuntimeError("Simulated failure after recording publication")

        monkeypatch.setattr(StagedResourceIntake, "save", fail_intake_save)
        with pytest.raises(DatasetManagementError):
            DatasetChangeService(manager, organization).publish(
                change.id, expected_revision=change.revision
            )
        assert not StagedResourcePublication.objects.exists()
        assert not StagedResourcePublicationDataset.objects.exists()
        return
    result = DatasetChangeService(manager, organization).publish(
        change.id, expected_revision=change.revision
    )
    assert StagedResourcePublication.objects.count() == 1
    publication = StagedResourcePublication.objects.get()
    assert publication.dataset_assignments.count() == 2
    assert {
        str(value) for value in publication.dataset_assignments.values_list("dataset_id", flat=True)
    } == set(result.result["dataset_ids"])


@pytest.mark.parametrize("has_evidence", [False, True])
def test_historical_reconciliation_uses_original_versions_and_actor(
    management_scope: Scope, tmp_path: Path, has_evidence: bool
) -> None:
    """Backfill old proposals from exact provenance instead of the dataset's current version."""
    manager, editor, organization, _, dataset = management_scope
    service = DatasetChangeService(manager, organization)
    first = prepare_csv(management_scope, tmp_path / "first.csv", "id,amount\n1,10\n")
    published = service.publish(first.id, expected_revision=first.revision)
    original_version = dataset.versions.get().id
    assert StagedResourcePublication.objects.count() == 1
    publication = StagedResourcePublication.objects.get()
    # Reproduce the persisted shape created before publication integration.
    DatasetChange.objects.filter(pk=first.id).update(
        publication=None, result={"dataset_ids": [str(dataset.id)], "workbook_id": None}
    )
    publication.dataset_assignments.all().delete()
    publication.delete()
    if not has_evidence:
        DatasetArtifact.objects.filter(metadata_json__change_id=str(first.id)).update(
            metadata_json={}
        )
    second = prepare_csv(management_scope, tmp_path / "second.csv", "id,amount\n1,20\n")
    service.publish(second.id, expected_revision=second.revision)
    # The collaborator replay must retain the original manager as publication actor.
    replay = DatasetChangeService(editor, organization)
    if not has_evidence:
        with pytest.raises(DatasetManagementError) as error:
            replay.publish(first.id, expected_revision=first.revision)
        assert error.value.code == "publication_evidence_missing"
        assert StagedResourcePublication.objects.count() == 1
        return
    result = replay.publish(first.id, expected_revision=first.revision)
    assert result.publication_id is not None
    restored = StagedResourcePublication.objects.get(pk=result.publication_id)
    assert restored.published_by_id == manager.id
    assert restored.completed_at == published.completed_at
    assert restored.dataset_assignments.get().dataset_version_id == original_version
    dataset.refresh_from_db()
    assert dataset.current_version_id != original_version
    assert dataset.versions.count() == 2


def test_revoked_permission_does_not_create_publication(
    management_scope: Scope, tmp_path: Path
) -> None:
    """Revalidate editor authority before persisting any destination assignment."""
    _, editor, organization, _, dataset = management_scope
    change = prepare_csv(management_scope, tmp_path / "sales.csv", "id,amount\n1,10\n")
    remove_perm("resource_registry.change_dataset", editor, dataset)
    with pytest.raises(DatasetManagementError):
        DatasetChangeService(editor, organization).publish(
            change.id, expected_revision=change.revision
        )
    assert not StagedResourcePublication.objects.exists()


def test_admin_shows_maintenance_evidence_without_per_publication_queries(
    management_scope: Scope, tmp_path: Path
) -> None:
    """Render completed maintenance evidence and exact versions with bounded query counts."""
    manager, _, organization, _, _ = management_scope
    manager.is_superuser = True
    manager.save(update_fields=["is_superuser"])
    request = make_admin_request(
        manager, organization, "/admin/resource_registry/stagedresourceintake/"
    )
    inline = StagedResourcePublicationInline(StagedResourceIntake, admin.site)
    counts = []
    for index in range(3):
        change = prepare_csv(
            management_scope, tmp_path / f"sales-{index}.csv", f"id,amount\n1,{index}\n"
        )
        DatasetChangeService(manager, organization).publish(
            change.id, expected_revision=change.revision
        )
        assert StagedResourcePublication.objects.count() == index + 1
        with CaptureQueriesContext(connection) as queries:
            for publication in inline.get_queryset(request):
                assert inline.processing_status(publication) == "Dataset maintenance: Published"
                assert "/dataset/changes/" in inline.maintenance_updates(publication)
                assert "version" in inline.published_versions(publication)
        counts.append(len(queries))
    assert counts[0] == counts[-1]
    assert change.intake_id is not None
    intake = StagedResourceIntake.objects.get(pk=change.intake_id)
    destinations = StagedResourceIntakeAdmin._destination_choices(intake)
    assert all(
        str(change.namespace_id) != identifier
        for _, choices in destinations
        for identifier, _ in choices
    )


@pytest.mark.parametrize("remove_pending", [False, True])
def test_maintenance_reuses_completed_legacy_evidence_and_respects_pending_removal(
    management_scope: Scope, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, remove_pending: bool
) -> None:
    """Reconcile legacy plus maintenance versions while rejecting pending destination removal."""
    manager, _, organization, namespace, dataset = management_scope
    manager.is_superuser = True
    manager.save(update_fields=["is_superuser"])
    path = tmp_path / "legacy.csv"
    path.write_text("id,amount\n1,10\n")
    intake = create_verified_intake(manager, path)
    service = DatasetChangeService(manager, organization)
    first = service.create(
        namespace_id=namespace.id,
        intake_id=intake.id,
        idempotency_key=str(uuid4()),
        specification={"dataset_id": str(dataset.id), "expected_revision": dataset.revision},
    )
    DatasetChangeWorker().run_once(change_id=first.id)
    first.refresh_from_db()
    published = service.publish(first.id, expected_revision=first.revision)
    assert published.publication_id is not None
    publication = StagedResourcePublication.objects.get(pk=published.publication_id)
    legacy_outputs = published.result["versions"]
    DatasetChange.objects.filter(pk=first.id).update(publication=None)
    job = SimpleNamespace(
        status=WorkbookIngestionJobStatus.COMPLETED,
        result_payload_json={"created": legacy_outputs, "deduplicated": []},
    )
    monkeypatch.setattr(
        DestinationAssignmentRemovalService,
        "_get_publication_job",
        staticmethod(lambda *, publication: job),
    )
    if remove_pending:
        publication.assignment_status = "DETACH_REQUESTED"
        publication.save(update_fields=["assignment_status"])
    second = service.create(
        namespace_id=namespace.id,
        intake_id=intake.id,
        idempotency_key=str(uuid4()),
        specification={"name": "Additional output"},
    )
    DatasetChangeWorker().run_once(change_id=second.id)
    second.refresh_from_db()
    if remove_pending:
        with pytest.raises(DatasetManagementError) as error:
            service.publish(second.id, expected_revision=second.revision)
        assert error.value.code == "publication_detaching"
        assert publication.dataset_assignments.count() == 1
        return
    service.publish(second.id, expected_revision=second.revision)
    assert publication.dataset_assignments.count() == 2
    result = DestinationAssignmentRemovalService().request_detachment(
        user=manager, publication_id=publication.id, reason="Retire combined publication"
    )
    assert result.assignment_status == "DETACHED"


def test_one_intake_has_independent_workspace_and_organization_publications(
    management_scope: Scope, tmp_path: Path
) -> None:
    """Preserve destination scope when reusing one source across authorized namespaces."""
    manager, _, organization, workspace_namespace, _ = management_scope
    manager.is_superuser = True
    manager.save(update_fields=["is_superuser"])
    organization_namespace, _ = RegistryNamespace.objects.get_or_create(organization=organization)
    path = tmp_path / "source.csv"
    path.write_text("id,amount\n1,10\n")
    intake = create_verified_intake(manager, path)
    for namespace in [workspace_namespace, organization_namespace]:
        service = DatasetChangeService(manager, organization)
        change = service.create(
            namespace_id=namespace.id,
            intake_id=intake.id,
            idempotency_key=str(uuid4()),
            specification={"name": "Scoped dataset"},
        )
        DatasetChangeWorker().run_once(change_id=change.id)
        change.refresh_from_db()
        service.publish(change.id, expected_revision=change.revision)
        publication = intake.publications.get(registry_namespace=namespace)
        assert publication.organization_id == organization.id
        assert publication.workspace_id == namespace.workspace_id
        assert publication.visibility_scope == (
            "WORKSPACE" if namespace.workspace_id else "ORGANIZATION"
        )
    assert intake.publications.count() == 2


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("deferred_background_tasks")
def test_concurrent_proposals_share_one_intake_destination_publication(
    management_scope: Scope, tmp_path: Path
) -> None:
    """Serialize destination allocation across distinct proposals using the same source."""
    manager, _, organization, namespace, _ = management_scope
    path = tmp_path / "source.csv"
    path.write_text("id,amount\n1,10\n")
    intake = create_verified_intake(manager, path)
    proposals = []
    for index in range(2):
        change = DatasetChangeService(manager, organization).create(
            namespace_id=namespace.id,
            intake_id=intake.id,
            idempotency_key=str(uuid4()),
            specification={"name": f"Dataset {index}"},
        )
        DatasetChangeWorker().run_once(change_id=change.id)
        change.refresh_from_db()
        proposals.append(change)

    def publish_proposal(change: DatasetChange) -> str:
        close_old_connections()
        try:
            actor = User.objects.get(pk=manager.pk)
            return (
                DatasetChangeService(actor, organization)
                .publish(change.id, expected_revision=change.revision)
                .status
            )
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert list(executor.map(publish_proposal, proposals)) == ["PUBLISHED", "PUBLISHED"]
    assert intake.publications.count() == 1
    assert intake.publications.get().dataset_assignments.count() == 2


def test_historical_replay_cannot_join_an_active_attempt_after_detachment(
    management_scope: Scope, tmp_path: Path
) -> None:
    """Reject ambiguous historical reconciliation after a destination was republished."""
    manager, _, organization, namespace, _ = management_scope
    manager.is_superuser = True
    manager.save(update_fields=["is_superuser"])
    path = tmp_path / "source.csv"
    path.write_text("id,amount\n1,10\n")
    intake = create_verified_intake(manager, path)
    service = DatasetChangeService(manager, organization)
    first = service.create(
        namespace_id=namespace.id,
        intake_id=intake.id,
        idempotency_key=str(uuid4()),
        specification={"name": "Original dataset"},
    )
    DatasetChangeWorker().run_once(change_id=first.id)
    first.refresh_from_db()
    original = service.publish(first.id, expected_revision=first.revision)
    assert original.publication_id is not None
    DatasetChange.objects.filter(pk=first.id).update(
        publication=None,
        result={"dataset_ids": original.result["dataset_ids"], "workbook_id": None},
    )
    DestinationAssignmentRemovalService().request_detachment(
        user=manager, publication_id=original.publication_id, reason="Retire original attempt"
    )
    second = service.create(
        namespace_id=namespace.id,
        intake_id=intake.id,
        idempotency_key=str(uuid4()),
        specification={"name": "Replacement dataset"},
    )
    DatasetChangeWorker().run_once(change_id=second.id)
    second.refresh_from_db()
    replacement = service.publish(second.id, expected_revision=second.revision)
    assert replacement.publication_id is not None
    assert (
        StagedResourcePublication.objects.get(pk=replacement.publication_id).publication_attempt
        == 2
    )

    def snapshot() -> dict[str, Sequence[Mapping[str, object]]]:
        return {
            "publications": list(StagedResourcePublication.objects.order_by("id").values()),
            "assignments": list(StagedResourcePublicationDataset.objects.order_by("id").values()),
            "proposals": list(DatasetChange.objects.order_by("id").values()),
            "datasets": list(Dataset.objects.order_by("id").values()),
            "versions": list(DatasetVersion.objects.order_by("id").values()),
        }

    before = snapshot()
    with pytest.raises(DatasetManagementError) as error:
        service.publish(first.id, expected_revision=first.revision)
    assert error.value.code == "publication_evidence_missing"
    assert snapshot() == before
