"""Exercise real candidate preparation and atomic publication with local storage."""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

import pytest
from django.contrib.auth.models import User
from django.utils import timezone
from guardian.shortcuts import assign_perm, remove_perm

from Organization.models import Organization
from resource_registry.management_domain.changes import DatasetChangeService
from resource_registry.management_domain.errors import DatasetManagementError
from resource_registry.management_domain.worker import DatasetChangeWorker
from resource_registry.models import (
    Dataset,
    DatasetChange,
    DatasetVersion,
    OutboxEntry,
    RegistryNamespace,
    StagedResourceIntake,
)
from resource_registry.storage.factory import DataRegistryStorageFactory
from resource_registry.tests.test_dataset_management import management_scope  # noqa: F401


@pytest.mark.parametrize("legacy_queued", [False, True])
def test_csv_sheet_mapping_cannot_bypass_creation_permission(
    management_scope: tuple[User, User, Organization, RegistryNamespace, Dataset],
    tmp_path: Path,
    legacy_queued: bool,
) -> None:
    """Reject mismatched CSV targets in new requests and previously queued proposals."""
    _, editor, organization, namespace, dataset = management_scope
    assign_perm("resource_registry.change_dataset", editor, dataset)
    assert not editor.has_perm("resource_registry.create_datasets", namespace)
    path = tmp_path / "replacement.csv"
    path.write_text("id,amount\n1,20\n")
    intake = create_verified_intake(editor, path)
    specification = {
        "sheets": [
            {
                "sheet_name": "Data",
                "dataset_id": str(dataset.id),
                "expected_revision": dataset.revision,
            }
        ]
    }
    service = DatasetChangeService(editor, organization)
    original_count = Dataset.objects.count()
    original_events = OutboxEntry.objects.count()
    if legacy_queued:
        change = DatasetChange.objects.create(
            namespace=namespace,
            submitted_by=editor,
            intake=intake,
            specification=specification,
            idempotency_key="legacy-mismatch",
            request_fingerprint="legacy",
        )
        DatasetChangeWorker().run_once(batch_size=1)
        change.refresh_from_db()
        assert change.status == "FAILED"
        assert change.error_code == "invalid_source_mapping"
        with pytest.raises(DatasetManagementError):
            service.publish(change.id, expected_revision=change.revision)
    else:
        with pytest.raises(DatasetManagementError) as failure:
            service.create(
                namespace_id=namespace.id,
                intake_id=intake.id,
                idempotency_key="mismatch",
                specification=specification,
            )
        assert failure.value.code == "invalid_source_mapping"
    assert Dataset.objects.count() == original_count
    assert not dataset.versions.exists()
    assert OutboxEntry.objects.count() == original_events


def test_publication_rejects_prepared_operations_different_from_authorized_targets(
    management_scope: tuple[User, User, Organization, RegistryNamespace, Dataset], tmp_path: Path
) -> None:
    """Revalidate effective publication operations even for already prepared candidates."""
    _, editor, organization, namespace, dataset = management_scope
    assign_perm("resource_registry.change_dataset", editor, dataset)
    path = tmp_path / "replacement.csv"
    path.write_text("id,amount\n1,20\n")
    service = DatasetChangeService(editor, organization)
    change = service.create(
        namespace_id=namespace.id,
        intake_id=create_verified_intake(editor, path).id,
        idempotency_key="prepared-mismatch",
        specification={"dataset_id": str(dataset.id), "expected_revision": dataset.revision},
    )
    DatasetChangeWorker().run_once(batch_size=1)
    change.refresh_from_db()
    # Represent evidence persisted by a worker using the former interpretation.
    change.prepared["items"][0]["dataset_id"] = None
    change.save(update_fields=["prepared"])
    original_count = Dataset.objects.count()
    original_events = OutboxEntry.objects.count()
    with pytest.raises(DatasetManagementError) as failure:
        service.publish(change.id, expected_revision=change.revision)
    assert failure.value.code == "prepared_target_mismatch"
    assert Dataset.objects.count() == original_count
    assert not dataset.versions.exists()
    assert OutboxEntry.objects.count() == original_events


def create_verified_intake(user: User, path: Path) -> StagedResourceIntake:
    """Store a fixture file with server-verified intake evidence."""
    provider = DataRegistryStorageFactory().create_provider("local")
    key = f"management-tests/{uuid4()}/{path.name}"
    metadata = provider.put(str(path), key)
    return StagedResourceIntake.objects.create(
        submitted_by=user,
        normalized_filename=path.name,
        original_filename=path.name,
        resolved_file_format=path.suffix[1:].upper(),
        declared_content_type="text/csv",
        declared_size_bytes=path.stat().st_size,
        actual_size_bytes=path.stat().st_size,
        storage_provider="local",
        storage_object_key=key,
        storage_etag=metadata.etag,
        verified_checksum=hashlib.sha256(path.read_bytes()).hexdigest(),
        status="READY_FOR_PUBLICATION",
        upload_authorization_expires_at=timezone.now(),
        idempotency_key=str(uuid4()),
        request_fingerprint="fixture",
    )


def test_candidate_is_private_until_published_and_preserves_target_identity(
    management_scope: tuple[User, User, Organization, RegistryNamespace, Dataset],
    tmp_path: Path,
) -> None:
    """Prepare without exposing versions, then publish one version on the selected dataset."""

    _, editor, organization, namespace, dataset = management_scope
    assign_perm("resource_registry.change_dataset", editor, dataset)
    path = tmp_path / "renamed.csv"
    path.write_text("id,amount\n1,10\n2,20\n", encoding="utf-8")
    intake = create_verified_intake(editor, path)
    service = DatasetChangeService(editor, organization)
    change = service.create(
        namespace_id=namespace.id,
        intake_id=intake.id,
        idempotency_key="update-1",
        specification={"dataset_id": str(dataset.id), "expected_revision": 0},
    )
    assert DatasetChangeWorker().run_once(batch_size=1) == 1
    change.refresh_from_db()
    assert change.status == "READY"
    assert not DatasetVersion.objects.filter(dataset=dataset).exists()
    published = service.publish(change.id, expected_revision=change.revision)
    dataset.refresh_from_db()
    assert published.status == "PUBLISHED"
    assert dataset.current_version is not None
    assert dataset.slug == "sales"
    assert dataset.versions.count() == 1
    assert service.publish(change.id, expected_revision=change.revision).id == published.id
    assert dataset.versions.count() == 1


def test_revoked_editor_cannot_publish_prepared_candidate(
    management_scope: tuple[User, User, Organization, RegistryNamespace, Dataset],
    tmp_path: Path,
) -> None:
    """Recheck current authority rather than trusting preparation-time permission."""

    _, editor, organization, namespace, dataset = management_scope
    assign_perm("resource_registry.change_dataset", editor, dataset)
    path = tmp_path / "sales.csv"
    path.write_text("id,amount\n1,10\n", encoding="utf-8")
    intake = create_verified_intake(editor, path)
    service = DatasetChangeService(editor, organization)
    change = service.create(
        namespace_id=namespace.id,
        intake_id=intake.id,
        idempotency_key="revoke",
        specification={"dataset_id": str(dataset.id), "expected_revision": 0},
    )
    DatasetChangeWorker().run_once(batch_size=1)
    change.refresh_from_db()
    remove_perm("resource_registry.change_dataset", editor, dataset)
    with pytest.raises(DatasetManagementError):
        DatasetChangeService(editor, organization).publish(
            change.id, expected_revision=change.revision
        )
    assert not dataset.versions.exists()
