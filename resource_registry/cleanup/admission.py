"""Fence new persisted dependencies against irreversible dataset cleanup."""

from collections.abc import Mapping
from uuid import UUID

from django.db.models import Q

from resource_registry.management_domain.errors import DatasetManagementError
from resource_registry.models import Dataset, DatasetVersion


def lock_referenced_datasets(payload: object) -> None:
    """Lock referenced datasets in the caller's transaction before admitting a dependency.

    Args:
        payload: Server-validated execution or definition snapshot containing exact identities.

    Raises:
        DatasetManagementError: If a referenced dataset has started permanent deletion.
    """
    identifiers = collect_uuid_values(payload)
    if not identifiers:
        return
    version_owners = DatasetVersion.objects.filter(pk__in=identifiers).values("dataset_id")
    datasets = (
        Dataset.objects.select_for_update()
        .filter(Q(pk__in=identifiers) | Q(pk__in=version_owners))
        .order_by("pk")
    )
    if any(dataset.purge_started_at is not None for dataset in datasets):
        raise DatasetManagementError(
            "dataset_purging", "Referenced dataset data is being deleted.", 410
        )


def collect_uuid_values(payload: object) -> set[UUID]:
    """Collect exact UUID values without accepting client storage coordinates.

    Args:
        payload: A JSON-compatible snapshot or scalar.

    Returns:
        All UUID values in the snapshot, ignoring ordinary text.
    """
    if isinstance(payload, Mapping):
        return set().union(*(collect_uuid_values(value) for value in payload.values()))
    if isinstance(payload, (list, tuple)):
        return set().union(*(collect_uuid_values(value) for value in payload))
    if isinstance(payload, (str, UUID)):
        try:
            return {UUID(str(payload))}
        except ValueError:
            pass
    return set()
