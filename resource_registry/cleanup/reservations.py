"""Reserve synchronous dataset use independently from individual storage transfers."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from threading import Event, Thread
from uuid import UUID

from django.db import close_old_connections, transaction
from django.utils import timezone

from resource_registry.management_domain.errors import DatasetManagementError
from resource_registry.models import Dataset, ResourceStorageLease

_logger = logging.getLogger(__name__)


@contextmanager
def maintain_lease(lease: ResourceStorageLease) -> Iterator[None]:
    """Renew a live operation's reservation and release it when its work finishes."""
    stopped = Event()

    def renew() -> None:
        while not stopped.wait(60):
            close_old_connections()
            try:
                ResourceStorageLease.objects.filter(pk=lease.pk).update(
                    expires_at=timezone.now() + timedelta(minutes=15)
                )
            except Exception:
                _logger.exception("Unable to renew resource use reservation.")
            finally:
                close_old_connections()

    thread = Thread(target=renew, name="resource-use-reservation", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join(timeout=1)
        ResourceStorageLease.objects.filter(pk=lease.pk).delete()


@contextmanager
def lease_dataset(identifier: str | UUID) -> Iterator[None]:
    """Fence a synchronous operation against purging its dataset or cached content."""
    try:
        dataset_id = UUID(str(identifier))
    except ValueError:
        # Non-Registry adapters cannot name a persisted UUID dataset.
        yield
        return
    with transaction.atomic():
        dataset = Dataset.objects.select_for_update().filter(pk=dataset_id).first()
        if dataset is None:
            raise DatasetManagementError("dataset_unavailable", "Dataset is unavailable.", 404)
        if dataset.purge_started_at is not None:
            raise DatasetManagementError("dataset_purging", "Dataset data is being deleted.", 410)
        lease = ResourceStorageLease.objects.create(
            dataset=dataset, expires_at=timezone.now() + timedelta(minutes=15)
        )
    with maintain_lease(lease):
        yield
