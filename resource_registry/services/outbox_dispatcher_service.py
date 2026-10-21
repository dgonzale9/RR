"""Dispatch Dataset Registry outbox events after their owner transaction commits."""

from __future__ import annotations

import json
import random
from datetime import timedelta
from typing import Protocol
from uuid import UUID, uuid4

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from resource_registry.models import EventPublication, OutboxEntry, OutboxEntryStatus


class EventTransport(Protocol):
    """Publish one canonical immutable event through an external transport."""

    def publish(self, *, event_id: str, canonical_event: str, partition_key: str) -> None:
        """Publish one event using its stable identity and stream key."""


class EventArchive(Protocol):
    """Preserve immutable canonical event bytes for retention and later replay."""

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
        """Archive one immutable event and return its stable location."""


class OutboxDispatcherService:
    """Claim, publish, and acknowledge Dataset Registry outbox entries."""

    def __init__(self, transport: EventTransport, archive: EventArchive) -> None:
        """Initialize the dispatcher with its provider-neutral transport."""
        self._transport = transport
        self._archive = archive

    def dispatch_next(self, *, worker_id: str, lease_seconds: int = 60) -> bool:
        """Claim and publish one due outbox entry without touching Dataset authority."""
        if not bool(getattr(settings, "RESOURCE_REGISTRY_OUTBOX_ENABLED", False)):
            return False
        claim = self._claim_next(worker_id=worker_id, lease_seconds=lease_seconds)
        if claim is None:
            return False
        entry_id, claim_token = claim
        entry = OutboxEntry.objects.get(pk=entry_id)
        self._validate_claimed_entry(entry)
        try:
            self._transport.publish(
                event_id=entry.event_id,
                canonical_event=entry.canonical_event,
                partition_key=f"{entry.namespace_id}:{entry.dataset_id}",
            )
            archive_uri = self._archive.archive(
                event_id=entry.event_id,
                canonical_event=entry.canonical_event,
                checksum=entry.checksum,
                namespace_id=entry.namespace_id,
                dataset_id=entry.dataset_id,
                sequence=entry.sequence,
            )
        except Exception:
            self._defer_claim(entry=entry, claim_token=claim_token)
            raise
        return self._acknowledge(
            entry_id=entry_id,
            claim_token=claim_token,
            archive_uri=archive_uri,
            archive_checksum=entry.checksum,
        )

    @staticmethod
    def _claim_next(*, worker_id: str, lease_seconds: int) -> tuple[UUID, UUID] | None:
        """Claim one pending or expired entry under a short database lease."""
        now = timezone.now()
        with transaction.atomic():
            entry = (
                OutboxEntry.objects.filter(
                    status__in=[OutboxEntryStatus.PENDING, OutboxEntryStatus.CLAIMED],
                    published_at__isnull=True,
                )
                .filter(Q(lease_expires_at__isnull=True) | Q(lease_expires_at__lte=now))
                .order_by("namespace_id", "dataset_id", "sequence", "created_at")
                .select_for_update(skip_locked=True)
                .first()
            )
            if entry is None:
                return None
            token = uuid4()
            entry.status = OutboxEntryStatus.CLAIMED
            entry.claim_owner = worker_id
            entry.claim_token = token
            entry.lease_expires_at = now + timedelta(seconds=max(lease_seconds, 1))
            entry.attempt_count += 1
            entry.save(
                update_fields=[
                    "status",
                    "claim_owner",
                    "claim_token",
                    "lease_expires_at",
                    "attempt_count",
                ]
            )
            return entry.id, token

    @staticmethod
    def _defer_claim(*, entry: OutboxEntry, claim_token: UUID) -> None:
        """Defer a transient transport failure with bounded exponential backoff and jitter."""
        delay_seconds = min(300, 2 ** min(entry.attempt_count, 8)) + random.uniform(0, 1)
        OutboxEntry.objects.filter(pk=entry.pk, claim_token=claim_token).update(
            status=OutboxEntryStatus.PENDING,
            claim_owner=None,
            claim_token=None,
            lease_expires_at=timezone.now() + timedelta(seconds=delay_seconds),
        )

    @staticmethod
    def _acknowledge(
        *,
        entry_id: UUID,
        claim_token: UUID,
        archive_uri: str,
        archive_checksum: str,
    ) -> bool:
        """Persist publication evidence only while the matching lease fence is active."""
        with transaction.atomic():
            entry = (
                OutboxEntry.objects.select_for_update()
                .filter(pk=entry_id, claim_token=claim_token)
                .first()
            )
            if (
                entry is None
                or entry.lease_expires_at is None
                or entry.lease_expires_at <= timezone.now()
            ):
                return False
            EventPublication.objects.create(
                outbox_entry=entry,
                claim_token=claim_token,
                archive_uri=archive_uri,
                archive_checksum=archive_checksum,
                archived_at=timezone.now(),
            )
            entry.status = OutboxEntryStatus.PUBLISHED
            entry.published_at = timezone.now()
            entry.claim_owner = None
            entry.claim_token = None
            entry.lease_expires_at = None
            entry.save(
                update_fields=[
                    "status",
                    "published_at",
                    "claim_owner",
                    "claim_token",
                    "lease_expires_at",
                ]
            )
            return True

    @staticmethod
    def _validate_claimed_entry(entry: OutboxEntry) -> None:
        """Reject malformed stored event identity before external publication."""
        try:
            event = json.loads(entry.canonical_event)
        except json.JSONDecodeError as error:
            raise ValueError("Outbox event is not valid canonical JSON.") from error
        if (
            event.get("event_id") != entry.event_id
            or event.get("event_type") != entry.event_type
            or event.get("event_version") != entry.event_version
            or event.get("checksum") != entry.checksum
            or entry.source != "dataset.registry.application"
        ):
            raise ValueError("Outbox event identity, checksum, or source is invalid.")
