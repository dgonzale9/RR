"""Verify Dataset Registry owner-local outbox persistence boundaries."""

from __future__ import annotations

from threading import Barrier, Thread
from uuid import uuid4

import pytest
from django.conf import settings
from django.contrib.auth.models import User
from django.db import IntegrityError, close_old_connections, transaction

from Organization.models import Organization, Workspace
from resource_registry.models import (
    Dataset,
    DatasetStatus,
    DatasetVersionStatus,
    DatasetVisibilityScope,
    OutboxEntry,
    RegistryNamespace,
)
from resource_registry.read.exceptions import WorkspaceRegistryNamespaceNotFoundError
from resource_registry.services.registry.dataset_registry_service import DatasetRegistryService

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def _enable_outbox(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enable owner-local outbox behavior for its focused integration tests."""
    monkeypatch.setattr(settings, "RESOURCE_REGISTRY_OUTBOX_ENABLED", True)


def _create_workspace_dataset(*, create_namespace: bool = True) -> Dataset:
    """Create a Dataset with one verified Workspace Namespace."""
    owner = User.objects.create_superuser(username=f"outbox-{uuid4()}", password="password")
    organization = Organization.objects.create(
        name="Outbox Organization", slug=f"outbox-{uuid4()}", last_modified_by=owner
    )
    workspace = Workspace.objects.create(
        organization=organization,
        name="Outbox Workspace",
        slug=f"outbox-{uuid4()}",
        last_modified_by=owner,
    )
    namespace_id = uuid4()
    if create_namespace:
        RegistryNamespace.objects.create(id=namespace_id, workspace=workspace)
    return Dataset.objects.create(
        tenant_id=namespace_id,
        organization=organization,
        workspace=workspace,
        visibility_scope=DatasetVisibilityScope.WORKSPACE,
        slug=f"outbox-{uuid4()}",
        name="Outbox Dataset",
        source_type="FILE_UPLOAD",
        status=DatasetStatus.ACTIVE,
    )


def _context() -> dict[str, object]:
    """Build safe Dataset bundle context without preview rows."""
    return {
        "source": {"path": "memory://dataset.csv"},
        "summary": {"row_count": 1, "column_count": 1},
        "schema": [{"name": "id", "type": "INTEGER"}],
        "safety": {"preview_limit": 0, "preview_limited": True},
    }


def test_ready_bundle_commits_one_pending_outbox_entry() -> None:
    """Commit a READY Dataset bundle and its immutable pending outbox event together."""
    dataset = _create_workspace_dataset()
    result = DatasetRegistryService().create_version_bundle_from_context(
        dataset=dataset, version_number=1, content_hash="a" * 64, context=_context()
    )
    entry = OutboxEntry.objects.get(dataset=dataset)
    assert entry.sequence == 1
    assert entry.checksum
    assert entry.payload["dataset_version_id"] == str(result.dataset_version.id)


def test_bundle_rollback_removes_outbox_and_owner_records() -> None:
    """Roll back Dataset bundle and outbox state as one owner transaction."""
    dataset = _create_workspace_dataset()
    with pytest.raises(RuntimeError), transaction.atomic():
        DatasetRegistryService().create_version_bundle_from_context(
            dataset=dataset, version_number=1, content_hash="b" * 64, context=_context()
        )
        raise RuntimeError("force rollback")
    assert not OutboxEntry.objects.filter(dataset=dataset).exists()
    assert not dataset.versions.exists()


def test_pending_bundle_does_not_create_outbox_entry() -> None:
    """Keep PENDING versions outside the published-event lifecycle."""
    dataset = _create_workspace_dataset()
    DatasetRegistryService().create_version_bundle_from_context(
        dataset=dataset,
        version_number=1,
        content_hash="c" * 64,
        context=_context(),
        status=DatasetVersionStatus.PENDING,
    )
    assert not OutboxEntry.objects.filter(dataset=dataset).exists()


def test_ready_bundle_does_not_create_outbox_entry_when_feature_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Avoid accumulating local outbox entries before eventing receives approval."""
    dataset = _create_workspace_dataset()
    monkeypatch.setattr(settings, "RESOURCE_REGISTRY_OUTBOX_ENABLED", False)

    DatasetRegistryService().create_version_bundle_from_context(
        dataset=dataset, version_number=1, content_hash="f" * 64, context=_context()
    )

    assert not OutboxEntry.objects.filter(dataset=dataset).exists()


def test_outbox_rejects_duplicate_event_identity_and_stream_sequence() -> None:
    """Reject duplicate immutable event identity and Dataset stream sequence evidence."""
    dataset = _create_workspace_dataset()
    DatasetRegistryService().create_version_bundle_from_context(
        dataset=dataset, version_number=1, content_hash="e" * 64, context=_context()
    )
    entry = OutboxEntry.objects.get(dataset=dataset)
    values = {
        "dataset": entry.dataset,
        "namespace_id": entry.namespace_id,
        "sequence": entry.sequence,
        "event_type": entry.event_type,
        "event_version": entry.event_version,
        "source": entry.source,
        "subject": entry.subject,
        "payload": entry.payload,
        "canonical_event": entry.canonical_event,
        "checksum": entry.checksum,
    }
    with pytest.raises(IntegrityError), transaction.atomic():
        OutboxEntry.objects.create(event_id=entry.event_id, **values)
    with pytest.raises(IntegrityError), transaction.atomic():
        OutboxEntry.objects.create(event_id=f"evt-{uuid4()}", **values)


def test_missing_workspace_namespace_fails_closed() -> None:
    """Reject READY publication when the Dataset owner has no Namespace mapping."""
    dataset = _create_workspace_dataset(create_namespace=False)
    with pytest.raises(WorkspaceRegistryNamespaceNotFoundError):
        DatasetRegistryService().create_version_bundle_from_context(
            dataset=dataset, version_number=1, content_hash="d" * 64, context=_context()
        )
    assert not OutboxEntry.objects.filter(dataset=dataset).exists()


def test_concurrent_ready_bundles_allocate_distinct_monotonic_sequences() -> None:
    """Serialize concurrent Dataset outbox sequence allocation through the owner lock."""
    dataset = _create_workspace_dataset()
    start_barrier = Barrier(2)
    failures: list[BaseException] = []

    def create_bundle(version_number: int) -> None:
        """Create one version from an independent Django database connection."""
        close_old_connections()
        try:
            start_barrier.wait(timeout=10)
            DatasetRegistryService().create_version_bundle_from_context(
                dataset=Dataset.objects.get(pk=dataset.pk),
                version_number=version_number,
                content_hash=f"{version_number:x}" * 64,
                context=_context(),
            )
        except BaseException as error:
            failures.append(error)
        finally:
            close_old_connections()

    workers = [Thread(target=create_bundle, args=(version_number,)) for version_number in (1, 2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=20)

    assert all(not worker.is_alive() for worker in workers)
    assert not failures
    assert list(
        OutboxEntry.objects.filter(dataset=dataset)
        .order_by("sequence")
        .values_list("sequence", flat=True)
    ) == [1, 2]
