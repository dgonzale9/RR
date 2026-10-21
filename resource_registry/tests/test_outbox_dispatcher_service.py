"""Database-backed behavioral tests for Dataset Registry outbox dispatching."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from django.conf import settings
from django.contrib.auth.models import User
from django.utils import timezone

from Organization.models import Organization, Workspace
from resource_registry.models import (
    Dataset,
    DatasetStatus,
    DatasetVisibilityScope,
    EventPublication,
    OutboxEntry,
    OutboxEntryStatus,
    RegistryNamespace,
)
from resource_registry.services.outbox_dispatcher_service import OutboxDispatcherService
from resource_registry.services.registry.dataset_registry_service import DatasetRegistryService

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def _enable_outbox_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enable dispatch for behavioral tests that exercise external-effect boundaries."""
    monkeypatch.setattr(settings, "RESOURCE_REGISTRY_OUTBOX_ENABLED", True)


@dataclass
class _RecordingTransport:
    """Capture transport publication requests without calling an external broker."""

    publications: list[tuple[str, str, str]] = field(default_factory=list)

    def publish(self, *, event_id: str, canonical_event: str, partition_key: str) -> None:
        """Record one publication request.

        Args:
            event_id: Stable identity sent to the broker.
            canonical_event: Immutable serialized event body.
            partition_key: Stream-routing key sent to the broker.
        """
        self.publications.append((event_id, canonical_event, partition_key))


class _FailingTransport:
    """Raise a transient provider failure without calling an external broker."""

    def publish(self, *, event_id: str, canonical_event: str, partition_key: str) -> None:
        """Raise the configured transient failure.

        Args:
            event_id: Stable identity that would have been sent to the broker.
            canonical_event: Immutable serialized event body that would have been sent.
            partition_key: Stream-routing key that would have been sent.

        Raises:
            RuntimeError: Always, to model a transient transport failure.
        """
        _ = event_id, canonical_event, partition_key
        raise RuntimeError("broker unavailable")


@dataclass
class _RecordingArchive:
    """Capture immutable archive writes without calling Azure Blob Storage."""

    archive_uri: str = "azure://event-archive/resource-registry/outbox/event.json"
    archived_events: list[tuple[str, str, str, UUID, UUID, int]] = field(default_factory=list)

    def archive(
        self,
        *,
        event_id: str,
        canonical_event: str,
        checksum: str,
        namespace_id: UUID,
        dataset_id: UUID,
        sequence: int,
    ) -> str:
        """Record one immutable archive write.

        Args:
            event_id: Immutable global event identifier.
            canonical_event: Canonical serialized event body.
            checksum: Canonical event checksum.
            namespace_id: Owner Namespace identifier.
            dataset_id: Dataset stream identifier.
            sequence: Dataset stream sequence.

        Returns:
            Stable archive URI for the captured event.
        """
        self.archived_events.append(
            (event_id, canonical_event, checksum, namespace_id, dataset_id, sequence)
        )
        return self.archive_uri


class _FailingArchive:
    """Raise an archive failure without calling Azure Blob Storage."""

    def archive(
        self,
        *,
        event_id: str,
        canonical_event: str,
        checksum: str,
        namespace_id: UUID,
        dataset_id: UUID,
        sequence: int,
    ) -> str:
        """Raise the configured transient archive failure.

        Args:
            event_id: Immutable global event identifier.
            canonical_event: Canonical serialized event body.
            checksum: Canonical event checksum.
            namespace_id: Owner Namespace identifier.
            dataset_id: Dataset stream identifier.
            sequence: Dataset stream sequence.

        Raises:
            RuntimeError: Always, to model an unavailable archive.
        """
        _ = event_id, canonical_event, checksum, namespace_id, dataset_id, sequence
        raise RuntimeError("archive unavailable")


def _create_ready_outbox_entry() -> OutboxEntry:
    """Create a committed Dataset-owned READY event through the owner service."""
    owner = User.objects.create_superuser(username=f"dispatcher-{uuid4()}", password="password")
    organization = Organization.objects.create(
        name="Dispatcher Organization",
        slug=f"dispatcher-{uuid4()}",
        last_modified_by=owner,
    )
    workspace = Workspace.objects.create(
        organization=organization,
        name="Dispatcher Workspace",
        slug=f"dispatcher-{uuid4()}",
        last_modified_by=owner,
    )
    namespace = RegistryNamespace.objects.create(id=uuid4(), workspace=workspace)
    dataset = Dataset.objects.create(
        tenant_id=namespace.id,
        organization=organization,
        workspace=workspace,
        visibility_scope=DatasetVisibilityScope.WORKSPACE,
        slug=f"dispatcher-{uuid4()}",
        name="Dispatcher Dataset",
        source_type="FILE_UPLOAD",
        status=DatasetStatus.ACTIVE,
    )
    DatasetRegistryService().create_version_bundle_from_context(
        dataset=dataset,
        version_number=1,
        content_hash="a" * 64,
        context={
            "source": {"path": "memory://dispatcher.csv"},
            "summary": {"row_count": 1, "column_count": 1},
            "schema": [{"name": "id", "type": "INTEGER"}],
            "safety": {"preview_limit": 0, "preview_limited": True},
        },
    )
    return OutboxEntry.objects.get(dataset=dataset)


def test_dispatcher_publishes_committed_entry_and_records_fenced_evidence() -> None:
    """Publish one committed entry and persist only immutable acknowledgement evidence."""
    entry = _create_ready_outbox_entry()
    transport = _RecordingTransport()
    archive = _RecordingArchive()

    dispatched = OutboxDispatcherService(transport=transport, archive=archive).dispatch_next(
        worker_id="worker-a"
    )

    entry.refresh_from_db()
    publication = EventPublication.objects.get(outbox_entry=entry)
    assert dispatched is True
    assert transport.publications == [
        (entry.event_id, entry.canonical_event, f"{entry.namespace_id}:{entry.dataset_id}")
    ]
    assert entry.status == OutboxEntryStatus.PUBLISHED
    assert entry.published_at is not None
    assert entry.attempt_count == 1
    assert entry.claim_owner is None
    assert entry.claim_token is None
    assert entry.lease_expires_at is None
    assert publication.claim_token is not None
    assert publication.broker_position is None
    assert publication.archive_uri == archive.archive_uri
    assert publication.archive_checksum == entry.checksum
    assert publication.archived_at is not None
    assert archive.archived_events == [
        (
            entry.event_id,
            entry.canonical_event,
            entry.checksum,
            entry.namespace_id,
            entry.dataset_id,
            entry.sequence,
        )
    ]


def test_dispatcher_defers_transport_failure_without_mutating_the_event() -> None:
    """Retain the original event and schedule a retry after transient publication failure."""
    entry = _create_ready_outbox_entry()
    original_event = entry.canonical_event

    with pytest.raises(RuntimeError, match="broker unavailable"):
        OutboxDispatcherService(
            transport=_FailingTransport(), archive=_RecordingArchive()
        ).dispatch_next(worker_id="worker-a")

    entry.refresh_from_db()
    assert entry.status == OutboxEntryStatus.PENDING
    assert entry.canonical_event == original_event
    assert entry.attempt_count == 1
    assert entry.claim_owner is None
    assert entry.claim_token is None
    assert entry.lease_expires_at is not None
    assert entry.lease_expires_at > timezone.now()
    assert not EventPublication.objects.filter(outbox_entry=entry).exists()


def test_dispatcher_recovers_an_expired_claim() -> None:
    """Recover an expired lease and publish the still-unacknowledged immutable entry."""
    entry = _create_ready_outbox_entry()
    entry.status = OutboxEntryStatus.CLAIMED
    entry.claim_owner = "interrupted-worker"
    entry.claim_token = uuid4()
    entry.lease_expires_at = timezone.now() - timedelta(seconds=1)
    entry.save(update_fields=["status", "claim_owner", "claim_token", "lease_expires_at"])
    transport = _RecordingTransport()

    dispatched = OutboxDispatcherService(
        transport=transport, archive=_RecordingArchive()
    ).dispatch_next(worker_id="recovery-worker")

    entry.refresh_from_db()
    assert dispatched is True
    assert len(transport.publications) == 1
    assert entry.status == OutboxEntryStatus.PUBLISHED
    assert entry.attempt_count == 1
    assert EventPublication.objects.filter(outbox_entry=entry).count() == 1


def test_dispatcher_rejects_stale_acknowledgement_token() -> None:
    """Prevent an obsolete lease holder from recording publication evidence."""
    entry = _create_ready_outbox_entry()
    dispatcher = OutboxDispatcherService(
        transport=_RecordingTransport(), archive=_RecordingArchive()
    )
    claim = dispatcher._claim_next(worker_id="worker-a", lease_seconds=60)
    assert claim is not None
    entry_id, claim_token = claim
    replacement_token = uuid4()
    OutboxEntry.objects.filter(pk=entry_id).update(claim_token=replacement_token)

    acknowledged = dispatcher._acknowledge(
        entry_id=entry_id,
        claim_token=claim_token,
        archive_uri="azure://event-archive/resource-registry/outbox/event.json",
        archive_checksum=entry.checksum,
    )

    entry.refresh_from_db()
    assert acknowledged is False
    assert entry.status == OutboxEntryStatus.CLAIMED
    assert entry.claim_token == replacement_token
    assert not EventPublication.objects.filter(outbox_entry=entry).exists()


def test_dispatcher_skips_entries_deferred_for_retry() -> None:
    """Leave an entry alone until its retry backoff period has elapsed."""
    entry = _create_ready_outbox_entry()
    entry.lease_expires_at = timezone.now() + timedelta(minutes=1)
    entry.save(update_fields=["lease_expires_at"])
    transport = _RecordingTransport()

    dispatched = OutboxDispatcherService(
        transport=transport, archive=_RecordingArchive()
    ).dispatch_next(worker_id="worker-a")

    assert dispatched is False
    assert transport.publications == []


def test_dispatcher_does_not_claim_or_publish_when_dispatch_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep committed outbox entries pending until dispatch receives explicit approval."""
    entry = _create_ready_outbox_entry()
    transport = _RecordingTransport()
    monkeypatch.setattr(settings, "RESOURCE_REGISTRY_OUTBOX_ENABLED", False)

    dispatched = OutboxDispatcherService(
        transport=transport, archive=_RecordingArchive()
    ).dispatch_next(worker_id="worker-a")

    entry.refresh_from_db()
    assert dispatched is False
    assert transport.publications == []
    assert entry.status == OutboxEntryStatus.PENDING
    assert entry.attempt_count == 0


def test_dispatcher_defers_after_broker_success_when_archive_is_unavailable() -> None:
    """Retain retry ownership when archiving fails after broker acceptance is uncertain."""
    entry = _create_ready_outbox_entry()
    transport = _RecordingTransport()

    with pytest.raises(RuntimeError, match="archive unavailable"):
        OutboxDispatcherService(transport=transport, archive=_FailingArchive()).dispatch_next(
            worker_id="worker-a"
        )

    entry.refresh_from_db()
    assert len(transport.publications) == 1
    assert entry.status == OutboxEntryStatus.PENDING
    assert entry.claim_token is None
    assert entry.lease_expires_at is not None
    assert not EventPublication.objects.filter(outbox_entry=entry).exists()
