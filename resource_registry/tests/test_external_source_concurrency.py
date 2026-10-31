"""Prove source serialization, publication rollback, and shared-assignment protection."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.db import close_old_connections, connections

from Organization.models import Organization
from resource_registry.management_domain import changes
from resource_registry.management_domain.changes import DatasetChangeService
from resource_registry.management_domain.errors import DatasetManagementError
from resource_registry.management_domain.external_sources import DatasetExternalSourceService
from resource_registry.models import (
    Dataset,
    DatasetChange,
    StagedResourcePublication,
    StagedResourcePublicationDataset,
)
from resource_registry.tests.background_processing_helpers import (
    deferred_background_tasks,  # noqa: F401
)
from resource_registry.tests.test_dataset_changes import create_verified_intake
from resource_registry.tests.test_dataset_maintenance_flows import Scope
from resource_registry.tests.test_dataset_management import management_scope  # noqa: F401
from resource_registry.tests.test_external_source_contract import (  # noqa: F401
    prepare_source,
    publish_source,
    source_scope,
)


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("deferred_background_tasks")
def test_concurrent_source_publications_commit_once(source_scope: Scope, tmp_path: Path) -> None:
    """Serialize two publication retries through the same source lock."""
    _, editor, organization, _, _ = source_scope
    change = prepare_source(source_scope, tmp_path / "first.csv")

    def publish_copy(index: int) -> dict[str, object]:
        close_old_connections()
        try:
            actor = User.objects.get(pk=editor.pk)
            host = Organization.objects.get(pk=organization.pk)
            result = DatasetChangeService(actor, host).publish(
                change.id, expected_revision=change.revision
            )
            return dict(result.result)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(publish_copy, range(2)))
    assert results[0] == results[1]
    dataset = Dataset.objects.get(external_source_id=change.external_source_id)
    assert dataset.versions.count() == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("deferred_background_tasks")
def test_source_removal_and_publication_serialize_without_resurrection(
    source_scope: Scope, tmp_path: Path
) -> None:
    """Either publish wins and removal must reload, or removal cancels publication."""
    _, editor, organization, _, _ = source_scope
    first = prepare_source(source_scope, tmp_path / "first.csv")
    dataset = publish_source(source_scope, first)
    source = first.external_source
    assert source is not None
    source.refresh_from_db()
    candidate = prepare_source(
        source_scope,
        tmp_path / "next.csv",
        "id,amount\n1,20\n",
        version="v2",
        source_revision=source.revision,
        specification={"dataset_id": str(dataset.id), "expected_revision": dataset.revision},
    )
    source.refresh_from_db()
    expected_source_revision = source.revision

    def compete(operation: str) -> str:
        close_old_connections()
        try:
            actor = User.objects.get(pk=editor.pk)
            host = Organization.objects.get(pk=organization.pk)
            if operation == "publish":
                DatasetChangeService(actor, host).publish(
                    candidate.id, expected_revision=candidate.revision
                )
            else:
                DatasetExternalSourceService(actor, host).deactivate(
                    source.id,
                    expected_revision=expected_source_revision,
                    reason="Removed",
                    idempotency_key="race",
                )
            return operation
        except DatasetManagementError as error:
            return error.code
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(compete, ["publish", "remove"]))
    assert sum(outcome in {"publish", "remove"} for outcome in outcomes) == 1
    source.refresh_from_db()
    if source.status != "INACTIVE":
        DatasetExternalSourceService(editor, organization).deactivate(
            source.id,
            expected_revision=source.revision,
            reason="Removed",
            idempotency_key="race-reloaded",
        )
    dataset.refresh_from_db()
    candidate.refresh_from_db()
    assert dataset.archive_reason == "SOURCE_REMOVED"
    if candidate.status == "PUBLISHED":
        DatasetChangeService(editor, organization).publish(
            candidate.id, expected_revision=candidate.revision
        )
    else:
        with pytest.raises(DatasetManagementError):
            DatasetChangeService(editor, organization).publish(
                candidate.id, expected_revision=candidate.revision
            )
    dataset.refresh_from_db()
    assert dataset.archive_reason == "SOURCE_REMOVED"


def test_independent_assignment_keeps_dataset_visible(source_scope: Scope, tmp_path: Path) -> None:
    """Detach source evidence without removing an independent active publication."""
    _, editor, organization, namespace, _ = source_scope
    first = prepare_source(source_scope, tmp_path / "first.csv")
    dataset = publish_source(source_scope, first)
    first.refresh_from_db()
    source = first.external_source
    assert source is not None
    source.refresh_from_db()
    other_file = tmp_path / "independent.csv"
    other_file.write_text("id,amount\n1,10\n")
    independent = StagedResourcePublication.objects.create(
        intake=create_verified_intake(editor, other_file),
        registry_namespace=namespace,
        organization=organization,
        workspace=namespace.workspace,
        visibility_scope="WORKSPACE",
        publication_attempt=2,
        published_by=editor,
    )
    assert dataset.current_version is not None
    assignment = StagedResourcePublicationDataset.objects.create(
        publication=independent, dataset=dataset, dataset_version=dataset.current_version
    )
    result = DatasetExternalSourceService(editor, organization).deactivate(
        source.id, expected_revision=source.revision, reason="Removed", idempotency_key="shared"
    )
    dataset.refresh_from_db()
    assignment.refresh_from_db()
    assert dataset.status == "ACTIVE"
    assert assignment.detached_at is None
    assert result["items"][0]["outcome"] == "retained"


def test_failed_source_publication_rolls_back_identity_versions_and_applied_reference(
    source_scope: Scope, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep source applied state and all outputs unchanged if publication bookkeeping fails."""
    _, editor, organization, _, _ = source_scope
    first = prepare_source(source_scope, tmp_path / "first.csv")
    dataset = publish_source(source_scope, first)
    source = first.external_source
    assert source is not None
    source.refresh_from_db()
    candidate = prepare_source(
        source_scope,
        tmp_path / "next.csv",
        "id,amount\n1,20\n",
        version="v2",
        source_revision=source.revision,
        specification={"dataset_id": str(dataset.id), "expected_revision": dataset.revision},
    )

    def fail_publication(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated persistence failure")

    monkeypatch.setattr(changes, "record_dataset_publication", fail_publication)
    with pytest.raises(DatasetManagementError):
        DatasetChangeService(editor, organization).publish(
            candidate.id, expected_revision=candidate.revision
        )
    source.refresh_from_db()
    dataset.refresh_from_db()
    candidate.refresh_from_db()
    assert source.last_applied_version == "v1"
    assert dataset.versions.count() == 1
    assert candidate.status == "FAILED"


def test_removed_source_fences_inflight_worker_result(source_scope: Scope, tmp_path: Path) -> None:
    """Ignore an old worker's completion after source removal cancels its proposal."""
    _, editor, organization, _, _ = source_scope
    change = prepare_source(source_scope, tmp_path / "first.csv")
    source = change.external_source
    assert source is not None
    DatasetChange.objects.filter(pk=change.pk).update(status="PROCESSING")
    DatasetExternalSourceService(editor, organization).deactivate(
        source.id, expected_revision=source.revision, reason="Removed", idempotency_key="worker"
    )
    DatasetChangeService(editor, organization).complete_external_preparation(
        change.id, expected_revision=change.revision, prepared=change.prepared, status="NO_CHANGES"
    )
    change.refresh_from_db()
    assert change.status == "CANCELLED"
    assert not Dataset.objects.filter(external_source=source).exists()
