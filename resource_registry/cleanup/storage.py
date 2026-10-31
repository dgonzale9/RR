"""Fence storage reads, writes and deletions using durable object reservations."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from resource_registry.cleanup.inventory import location_fingerprint, object_identity
from resource_registry.cleanup.protection import get_object_blocker
from resource_registry.cleanup.reservations import maintain_lease
from resource_registry.management_domain.errors import DatasetManagementError
from resource_registry.models import (
    ResourceStorageLease,
    ResourceStorageObject,
    StagedResourceIntake,
)

_deletion_object: ContextVar[UUID | None] = ContextVar("registry_deletion_object", default=None)


@contextmanager
def deletion_authority(identifier: UUID) -> Iterator[None]:
    """Permit only a worker's already fenced exact object to cross the deletion boundary."""
    token = _deletion_object.set(identifier)
    try:
        yield
    finally:
        _deletion_object.reset(token)


@contextmanager
def guarded_deletion(provider: str, uri: str) -> Iterator[None]:
    """Fence an exact legacy temporary object or an authorized worker deletion through I/O."""

    with transaction.atomic():
        storage_object = (
            ResourceStorageObject.objects.select_for_update()
            .filter(identity=object_identity(provider, uri))
            .first()
        )
        if storage_object is None:
            raise DatasetManagementError(
                "ownership_unverified", "Uninventoried storage requires review before deletion."
            )
        authorized_worker = (
            _deletion_object.get() == storage_object.id and storage_object.state == "DELETING"
        )
        if not authorized_worker and (
            get_object_blocker(storage_object)
            or storage_object.references.exists()
            or storage_object.state == "DELETING"
        ):
            raise DatasetManagementError(
                "object_protected", "Stored data requires reviewed cleanup."
            )
        if not authorized_worker:
            storage_object.state = "DELETING"
            storage_object.save(update_fields=["state"])
    yield
    if not authorized_worker:
        ResourceStorageObject.objects.filter(pk=storage_object.pk).update(
            state="DELETED", deleted_at=timezone.now()
        )


@contextmanager
def lease_object(
    provider: str, uri: str, *, writing: bool = False, location: str | None = None
) -> Iterator[ResourceStorageObject]:
    """Fence a bounded storage operation; existing reservations survive process interruption.

    Args:
        provider: Server-selected provider identity.
        uri: Canonical server-selected location.
        writing: Whether this call creates a new immutable object generation.
        location: Adapter's actual root or account, without secrets.

    Yields:
        Object held against cleanup for this operation.

    Raises:
        DatasetManagementError: If deletion started or writing would replace referenced data.
    """
    with transaction.atomic():
        storage_object, created = ResourceStorageObject.objects.get_or_create(
            identity=object_identity(provider, uri),
            defaults={
                "provider": provider,
                "uri": uri,
                "state": "WRITING" if writing else "AVAILABLE",
                "ownership_verified": writing,
                "location_fingerprint": location_fingerprint(provider, location),
            },
        )
        storage_object = ResourceStorageObject.objects.select_for_update().get(pk=storage_object.pk)
        if _deletion_object.get() != storage_object.id and storage_object.state in {
            "DELETING",
            "DELETED",
        }:
            raise DatasetManagementError("object_unavailable", "Stored data is being deleted.", 410)
        if (
            writing
            and not created
            and (
                storage_object.references.filter(released_at__isnull=True).exists()
                or storage_object.leases.filter(expires_at__gt=timezone.now())
                .exclude(purpose="AUTHORIZATION")
                .exists()
                or storage_object.cleanup_items.exclude(
                    request__status__in=["CANCELLED", "COMPLETED"]
                ).exists()
            )
        ):
            raise DatasetManagementError(
                "immutable_object",
                "Write a new object generation instead of replacing published data.",
            )
        if writing:
            storage_object.state = "WRITING"
            storage_object.generation = uuid4()
            storage_object.save(update_fields=["state", "generation"])
        lease = ResourceStorageLease.objects.create(
            storage_object=storage_object, expires_at=timezone.now() + timedelta(minutes=15)
        )
    with maintain_lease(lease):
        yield storage_object


def complete_object_write(
    storage_object: ResourceStorageObject,
    *,
    size_bytes: int,
    checksum: str | None,
    etag: str | None,
) -> None:
    """Persist verified object identity after an upload has completed."""
    ResourceStorageObject.objects.filter(pk=storage_object.pk).update(
        state="AVAILABLE",
        ownership_verified=True,
        size_bytes=size_bytes,
        checksum=checksum or "",
        etag=etag or "",
    )


@transaction.atomic
def reserve_upload_source(intake: StagedResourceIntake) -> None:
    """Record a private server-owned generation before authorizing any client upload."""
    if intake.status != "PENDING_UPLOAD" or intake.storage_provider not in {"local", "azure_blob"}:
        return
    uri = (
        f"local://{intake.storage_object_key}"
        if intake.storage_provider == "local"
        else f"azure://{getattr(settings, 'AZ_STORAGE_MEDIA_CONTAINER', '')}/{intake.storage_object_key}"
    )
    stored, _ = ResourceStorageObject.objects.get_or_create(
        identity=object_identity(intake.storage_provider, uri),
        defaults={
            "provider": intake.storage_provider,
            "uri": uri,
            "state": "WRITING",
            "ownership_verified": True,
            "location_fingerprint": location_fingerprint(intake.storage_provider),
        },
    )
    stored = ResourceStorageObject.objects.select_for_update().get(pk=stored.pk)
    if stored.state in {"DELETING", "DELETED"}:
        raise DatasetManagementError("source_deleted", "Source file deleted. Upload it again.", 410)
    ResourceStorageLease.objects.update_or_create(
        pk=uuid5(NAMESPACE_URL, f"intake-upload:{intake.pk}"),
        defaults={
            "storage_object": stored,
            "purpose": "AUTHORIZATION",
            "expires_at": intake.upload_authorization_expires_at,
        },
    )
