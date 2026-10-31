"""Exercise the public external-source contract without contacting a provider."""

from pathlib import Path
from uuid import uuid4

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone
from guardian.shortcuts import assign_perm

from resource_registry.api.management.serializers import CreateChangeSerializer
from resource_registry.management_domain.changes import DatasetChangeService
from resource_registry.management_domain.errors import DatasetManagementError
from resource_registry.management_domain.external_sources import DatasetExternalSourceService
from resource_registry.management_domain.lifecycle import DatasetLifecycleService
from resource_registry.management_domain.worker import DatasetChangeWorker
from resource_registry.models import Dataset, DatasetChange, DatasetVersion
from resource_registry.storage.factory import DataRegistryStorageFactory
from resource_registry.tests.test_dataset_changes import create_verified_intake
from resource_registry.tests.test_dataset_maintenance_flows import Scope
from resource_registry.tests.test_dataset_management import management_scope  # noqa: F401


def test_source_reference_is_an_explicit_creation_contract() -> None:
    """Accept a bounded source reference alongside a verified file selector."""
    serializer = CreateChangeSerializer(
        data={
            "namespace_id": str(uuid4()),
            "intake_id": str(uuid4()),
            "specification": {"name": "Sales"},
            "source_reference": {
                "provider": "sharepoint",
                "external_id": "drive:item",
                "version": "v1",
            },
            "expected_source_revision": 0,
        }
    )
    assert serializer.is_valid(), serializer.errors


@pytest.fixture
def source_scope(management_scope: Scope) -> Scope:
    """Admit an integration without granting management authority."""
    _, editor, _, namespace, _ = management_scope
    assign_perm("resource_registry.create_datasets", editor, namespace)
    assign_perm("resource_registry.synchronize_dataset_sources", editor, namespace)
    return management_scope


def prepare_source(
    scope: Scope,
    path: Path,
    content: str = "id,amount\n1,10\n",
    *,
    version: str = "v1",
    external_id: str = "drive:item",
    source_revision: int = 0,
    specification: dict[str, object] | None = None,
) -> DatasetChange:
    """Submit and prepare one verified file through the real application boundary."""
    _, editor, organization, namespace, _ = scope
    path.write_text(content)
    change = DatasetChangeService(editor, organization).create(
        namespace_id=namespace.id,
        intake_id=create_verified_intake(editor, path).id,
        idempotency_key=str(uuid4()),
        specification=specification or {"name": "Sales"},
        source_reference={"provider": "sharepoint", "external_id": external_id, "version": version},
        expected_source_revision=source_revision,
    )
    DatasetChangeWorker().run_once(batch_size=1, change_id=change.id)
    change.refresh_from_db()
    assert change.status in {"READY", "NEEDS_MANAGER", "NO_CHANGES"}, change.error_message
    return change


def publish_source(scope: Scope, change: DatasetChange) -> Dataset:
    """Publish a compatible proposal and return its persisted dataset."""
    _, editor, organization, _, _ = scope
    published = DatasetChangeService(editor, organization).publish(
        change.id, expected_revision=change.revision
    )
    return Dataset.objects.get(pk=published.result["dataset_ids"][0])


def test_source_publication_keeps_identity_and_returns_exact_results(
    source_scope: Scope, tmp_path: Path
) -> None:
    """Apply a full replacement to the same dataset and retain source version evidence."""
    first = prepare_source(source_scope, tmp_path / "sales.csv")
    dataset = publish_source(source_scope, first)
    first.refresh_from_db()
    source = first.external_source
    assert source is not None
    source.refresh_from_db()
    assert source.last_applied_version == "v1"
    assert first.result["items"][0]["dataset_version_id"] == str(dataset.current_version_id)
    second = prepare_source(
        source_scope,
        tmp_path / "renamed.csv",
        "id,amount\n1,10\n2,20\n",
        version="v2",
        source_revision=source.revision,
        specification={"dataset_id": str(dataset.id), "expected_revision": dataset.revision},
    )
    updated = publish_source(source_scope, second)
    assert updated.id == dataset.id
    assert updated.versions.count() == 2
    source.refresh_from_db()
    assert source.last_applied_version == "v2"


def test_source_rejects_manual_content_but_allows_metadata(
    source_scope: Scope, tmp_path: Path
) -> None:
    """Keep source-owned content immutable to manual writers, including managers."""
    manager, _, organization, namespace, _ = source_scope
    dataset = publish_source(source_scope, prepare_source(source_scope, tmp_path / "sales.csv"))
    path = tmp_path / "manual.csv"
    path.write_text("id,amount\n1,99\n")
    with pytest.raises(DatasetManagementError) as failure:
        DatasetChangeService(manager, organization).create(
            namespace_id=namespace.id,
            intake_id=create_verified_intake(manager, path).id,
            idempotency_key="manual",
            specification={"dataset_id": str(dataset.id), "expected_revision": dataset.revision},
        )
    assert failure.value.code == "source_managed_dataset"
    assert dataset.current_version_id is not None
    with pytest.raises(DatasetManagementError) as failure:
        DatasetLifecycleService(manager, organization).restore_version(
            dataset.id,
            version_id=dataset.current_version_id,
            expected_revision=dataset.revision,
            reason="manual",
        )
    assert failure.value.code == "source_managed_dataset"
    DatasetLifecycleService(manager, organization).update_metadata(
        dataset.id, {"name": "Renamed"}, expected_revision=dataset.revision
    )
    dataset.refresh_from_db()
    assert dataset.name == "Renamed"


def test_only_one_pending_proposal_per_source(source_scope: Scope, tmp_path: Path) -> None:
    """Reserve the external identity even before its first publication."""
    change = prepare_source(source_scope, tmp_path / "sales.csv")
    source = change.external_source
    assert source is not None
    with pytest.raises(DatasetManagementError) as failure:
        prepare_source(
            source_scope, tmp_path / "other.csv", version="v2", source_revision=source.revision
        )
    assert failure.value.code == "source_change_pending"


def test_no_changes_records_source_version_without_duplicate_dataset_versions(
    source_scope: Scope, tmp_path: Path
) -> None:
    """Persist a verified no-op with exact result and source provenance."""
    first = prepare_source(source_scope, tmp_path / "sales.csv")
    dataset = publish_source(source_scope, first)
    source = first.external_source
    assert source is not None
    source.refresh_from_db()
    repeated = prepare_source(
        source_scope,
        tmp_path / "same.csv",
        version="v2",
        source_revision=source.revision,
        specification={"dataset_id": str(dataset.id), "expected_revision": dataset.revision},
    )
    assert repeated.status == "NO_CHANGES"
    assert repeated.result["items"][0]["outcome"] == "unchanged"
    source.refresh_from_db()
    assert source.last_applied_version == "v2"
    assert dataset.versions.count() == 1


def test_historical_source_content_requires_manager_and_restores_atomically(
    source_scope: Scope, tmp_path: Path
) -> None:
    """Approve historical content through the source proposal, retaining version identity."""
    manager, editor, organization, _, _ = source_scope
    first = prepare_source(source_scope, tmp_path / "first.csv")
    dataset = publish_source(source_scope, first)
    initial_version = dataset.current_version_id
    source = first.external_source
    assert source is not None
    source.refresh_from_db()
    second = prepare_source(
        source_scope,
        tmp_path / "second.csv",
        "id,amount\n1,20\n",
        version="v2",
        source_revision=source.revision,
        specification={"dataset_id": str(dataset.id), "expected_revision": dataset.revision},
    )
    dataset = publish_source(source_scope, second)
    source.refresh_from_db()
    historical = prepare_source(
        source_scope,
        tmp_path / "history.csv",
        version="v3",
        source_revision=source.revision,
        specification={"dataset_id": str(dataset.id), "expected_revision": dataset.revision},
    )
    assert historical.status == "NEEDS_MANAGER"
    with pytest.raises(DatasetManagementError):
        DatasetChangeService(editor, organization).publish(
            historical.id, expected_revision=historical.revision
        )
    result = DatasetChangeService(manager, organization).publish(
        historical.id, expected_revision=historical.revision
    )
    dataset.refresh_from_db()
    assert dataset.current_version_id == initial_version
    assert dataset.versions.count() == 2
    assert result.result["items"][0]["outcome"] == "restored"


def test_deactivate_and_reappear_do_not_replay_old_publication(
    source_scope: Scope, tmp_path: Path
) -> None:
    """Retire only the source's assignments, then recover from newly verified content."""

    _, editor, organization, _, _ = source_scope
    first = prepare_source(source_scope, tmp_path / "sales.csv")
    dataset = publish_source(source_scope, first)
    source = first.external_source
    assert source is not None
    source.refresh_from_db()
    service = DatasetExternalSourceService(editor, organization)
    result = service.deactivate(
        source.id, expected_revision=source.revision, reason="Removed", idempotency_key="delete-1"
    )
    dataset.refresh_from_db()
    assert dataset.archive_reason == "SOURCE_REMOVED"
    assert result["items"][0]["outcome"] == "archived"
    DatasetChangeService(editor, organization).publish(first.id, expected_revision=first.revision)
    dataset.refresh_from_db()
    assert dataset.archive_reason == "SOURCE_REMOVED"
    source.refresh_from_db()
    recovered = prepare_source(
        source_scope,
        tmp_path / "returned.csv",
        version="v2",
        source_revision=source.revision,
        specification={"dataset_id": str(dataset.id), "expected_revision": dataset.revision},
    )
    assert recovered.status == "NO_CHANGES"
    dataset.refresh_from_db()
    assert dataset.archived_at is None
    assert dataset.versions.count() == 1


def test_source_identity_cannot_adopt_existing_manual_dataset(
    source_scope: Scope, tmp_path: Path
) -> None:
    """Require explicit new-source creation rather than silently claiming existing content."""
    _, editor, _, _, dataset = source_scope
    assign_perm("resource_registry.change_dataset", editor, dataset)
    with pytest.raises(DatasetManagementError) as failure:
        prepare_source(
            source_scope,
            tmp_path / "adopt.csv",
            specification={"dataset_id": str(dataset.id), "expected_revision": dataset.revision},
        )
    assert failure.value.code == "source_identity_conflict"


def test_deactivation_replay_is_immutable_after_reappearance(
    source_scope: Scope, tmp_path: Path
) -> None:
    """A lost-response retry never retires a subsequently recovered source again."""

    _, editor, organization, _, _ = source_scope
    first = prepare_source(source_scope, tmp_path / "first.csv")
    dataset = publish_source(source_scope, first)
    source = first.external_source
    assert source is not None
    source.refresh_from_db()
    original_revision = source.revision
    service = DatasetExternalSourceService(editor, organization)
    result = service.deactivate(
        source.id,
        expected_revision=original_revision,
        reason="Removed",
        idempotency_key="lost-response",
    )
    dataset.refresh_from_db()
    source.refresh_from_db()
    prepare_source(
        source_scope,
        tmp_path / "returned.csv",
        version="v2",
        source_revision=source.revision,
        specification={"dataset_id": str(dataset.id), "expected_revision": dataset.revision},
    )
    assert (
        service.deactivate(
            source.id,
            expected_revision=original_revision,
            reason="Removed",
            idempotency_key="lost-response",
        )
        == result
    )
    dataset.refresh_from_db()
    assert dataset.archived_at is None
    with pytest.raises(DatasetManagementError) as failure:
        service.deactivate(
            source.id,
            expected_revision=original_revision,
            reason="Different",
            idempotency_key="lost-response",
        )
    assert failure.value.code == "idempotency_conflict"


def test_manual_archive_is_not_recovered_by_source(source_scope: Scope, tmp_path: Path) -> None:
    """Honor a manager's manual archive even when the same source returns."""

    manager, editor, organization, _, _ = source_scope
    first = prepare_source(source_scope, tmp_path / "first.csv")
    dataset = publish_source(source_scope, first)
    DatasetLifecycleService(manager, organization).archive(
        dataset.id, expected_revision=dataset.revision, reason="Manual"
    )
    source = first.external_source
    assert source is not None
    source.refresh_from_db()
    DatasetExternalSourceService(editor, organization).deactivate(
        source.id, expected_revision=source.revision, reason="Gone", idempotency_key="gone"
    )
    source.refresh_from_db()
    dataset.refresh_from_db()
    with pytest.raises(DatasetManagementError):
        prepare_source(
            source_scope,
            tmp_path / "returned.csv",
            version="v2",
            source_revision=source.revision,
            specification={"dataset_id": str(dataset.id), "expected_revision": dataset.revision},
        )
    dataset.refresh_from_db()
    assert dataset.archive_reason == "MANUAL"


def test_missing_stored_artifact_prevents_noop_recovery(
    source_scope: Scope, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Do not claim a successful recovery when the retained version is physically absent."""

    _, editor, organization, namespace, _ = source_scope
    first = prepare_source(source_scope, tmp_path / "first.csv")
    dataset = publish_source(source_scope, first)
    source = first.external_source
    assert source is not None
    source.refresh_from_db()
    DatasetExternalSourceService(editor, organization).deactivate(
        source.id, expected_revision=source.revision, reason="Gone", idempotency_key="gone"
    )
    assert dataset.current_version is not None
    artifact = dataset.current_version.artifacts.get(metadata_json__is_canonical=True)
    provider_class = type(DataRegistryStorageFactory().create_provider("local"))
    original_exists = provider_class.exists
    monkeypatch.setattr(
        provider_class,
        "exists",
        lambda provider, uri: False if uri == artifact.uri else original_exists(provider, uri),
    )
    source.refresh_from_db()
    dataset.refresh_from_db()
    path = tmp_path / "returned.csv"
    path.write_text("id,amount\n1,10\n")
    change = DatasetChangeService(editor, organization).create(
        namespace_id=namespace.id,
        intake_id=create_verified_intake(editor, path).id,
        idempotency_key="returned",
        source_reference={"provider": "sharepoint", "external_id": "drive:item", "version": "v2"},
        expected_source_revision=source.revision,
        specification={"dataset_id": str(dataset.id), "expected_revision": dataset.revision},
    )
    DatasetChangeWorker().run_once(change_id=change.id)
    change.refresh_from_db()
    dataset.refresh_from_db()
    assert change.status == "FAILED"
    assert change.error_code == "version_unavailable"
    assert dataset.archive_reason == "SOURCE_REMOVED"


def test_legacy_version_writer_cannot_modify_source_dataset(
    source_scope: Scope, tmp_path: Path
) -> None:
    """Fence shared persistence before a legacy producer can create a new version."""

    dataset = publish_source(source_scope, prepare_source(source_scope, tmp_path / "first.csv"))
    with pytest.raises(DatasetManagementError) as failure:
        DatasetVersion.objects.create(
            dataset=dataset,
            version_number=2,
            source_uri="local://manual",
            content_hash="manual",
            status="READY",
        )
    assert failure.value.code == "source_managed_dataset"
    assert dataset.versions.count() == 1


def test_source_identity_is_immutable_after_reservation(
    source_scope: Scope, tmp_path: Path
) -> None:
    """Reject accidental reassignment of persisted source identity."""
    change = prepare_source(source_scope, tmp_path / "first.csv")
    source = change.external_source
    assert source is not None
    source.external_id = "another:file"
    with pytest.raises(ValidationError):
        source.save(update_fields=["external_id"])


def test_proposal_reference_is_immutable_after_creation(
    source_scope: Scope, tmp_path: Path
) -> None:
    """Keep approved file provenance fixed through every proposal lifecycle transition."""
    change = prepare_source(source_scope, tmp_path / "first.csv")
    change.source_reference = {**change.source_reference, "version": "forged"}
    with pytest.raises(ValidationError):
        change.save(update_fields=["source_reference"])


def test_identical_names_do_not_merge_distinct_external_files(
    source_scope: Scope, tmp_path: Path
) -> None:
    """Use provider identity rather than filename or display name for creation."""
    first = publish_source(source_scope, prepare_source(source_scope, tmp_path / "sales.csv"))
    second = publish_source(
        source_scope,
        prepare_source(source_scope, tmp_path / "sales.csv", external_id="drive:other-item"),
    )
    assert first.name == second.name
    assert first.id != second.id
    assert first.external_source_id != second.external_source_id


def test_source_cannot_recover_dataset_after_purge_started(
    source_scope: Scope, tmp_path: Path
) -> None:
    """Reject automatic recovery after the irreversible cleanup boundary."""

    _, editor, organization, _, _ = source_scope
    first = prepare_source(source_scope, tmp_path / "first.csv")
    dataset = publish_source(source_scope, first)
    source = first.external_source
    assert source is not None
    source.refresh_from_db()
    Dataset.objects.filter(pk=dataset.pk).update(
        status="ARCHIVED",
        archived_at=timezone.now(),
        archive_reason="SOURCE_REMOVED",
        purge_started_at=timezone.now(),
    )
    with pytest.raises(DatasetManagementError) as failure:
        prepare_source(
            source_scope,
            tmp_path / "returned.csv",
            version="v2",
            source_revision=source.revision,
            specification={"dataset_id": str(dataset.id), "expected_revision": dataset.revision},
        )
    assert failure.value.code == "dataset_purging"
    with pytest.raises(DatasetManagementError) as failure:
        DatasetExternalSourceService(editor, organization).deactivate(
            source.id, expected_revision=source.revision, reason="Gone", idempotency_key="purged"
        )
    assert failure.value.code == "dataset_purging"


def test_source_noop_revalidates_target_after_preparation(
    source_scope: Scope, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reject a no-op completion when a concurrent metadata edit changes the target revision."""
    manager, editor, organization, namespace, _ = source_scope
    first = prepare_source(source_scope, tmp_path / "first.csv")
    dataset = publish_source(source_scope, first)
    source = first.external_source
    assert source is not None
    source.refresh_from_db()
    path = tmp_path / "repeat.csv"
    path.write_text("id,amount\n1,10\n")
    change = DatasetChangeService(editor, organization).create(
        namespace_id=namespace.id,
        intake_id=create_verified_intake(editor, path).id,
        idempotency_key="noop-race",
        source_reference={"provider": "sharepoint", "external_id": "drive:item", "version": "v2"},
        expected_source_revision=source.revision,
        specification={"dataset_id": str(dataset.id), "expected_revision": dataset.revision},
    )
    original = DatasetChangeWorker._prepare_file

    def concurrent_edit(worker: DatasetChangeWorker, candidate: DatasetChange) -> dict[str, object]:
        prepared = original(worker, candidate)
        DatasetLifecycleService(manager, organization).update_metadata(
            dataset.id, {"description": "Concurrent"}, expected_revision=dataset.revision
        )
        return prepared

    monkeypatch.setattr(DatasetChangeWorker, "_prepare_file", concurrent_edit)
    DatasetChangeWorker().run_once(change_id=change.id)
    change.refresh_from_db()
    source.refresh_from_db()
    assert change.status == "FAILED"
    assert change.error_code == "revision_conflict"
    assert source.last_applied_version == "v1"
