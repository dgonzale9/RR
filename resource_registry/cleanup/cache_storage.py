"""Track exact query-cache generations before their bytes are written."""

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid4

from django.db import transaction

from resource_registry.cleanup.inventory import location_fingerprint, register_object
from resource_registry.cleanup.reservations import lease_dataset
from resource_registry.management_domain.errors import DatasetManagementError
from resource_registry.models import Dataset, ResourceStorageObject


@contextmanager
def reserve_cache_files(
    *, dataset_id: str, artifact_id: str, data_path: Path, metadata_path: Path
) -> Iterator[None]:
    """Record cache ownership before I/O and fence it for the complete write.

    Args:
        dataset_id: Resolved dataset identity.
        artifact_id: Resolved artifact identity.
        data_path: Exact server-derived cache data path.
        metadata_path: Exact server-derived cache metadata path.

    Yields:
        A protected interval in which the cache may write both files.

    Raises:
        DatasetManagementError: If the dataset or a cache object is being purged.
    """
    try:
        dataset_uuid, artifact_uuid = UUID(dataset_id), UUID(artifact_id)
    except ValueError:
        # Non-Registry adapters have no persistent dataset identity.
        yield
        return
    with lease_dataset(dataset_uuid):
        objects: list[tuple[ResourceStorageObject, Path]] = []
        with transaction.atomic():
            dataset = Dataset.objects.select_for_update().get(pk=dataset_uuid)
            for kind, path in (("CACHE_DATA", data_path), ("CACHE_METADATA", metadata_path)):
                stored = register_object(
                    provider="dataset_cache",
                    uri=f"dataset-cache://{path.name}",
                    owner_kind=kind,
                    owner_id=artifact_uuid,
                    dataset=dataset,
                    verified=True,
                )
                if stored is None:
                    raise DatasetManagementError(
                        "cache_unavailable", "Cache ownership could not be established."
                    )
                stored = ResourceStorageObject.objects.select_for_update().get(pk=stored.pk)
                if stored.state in {"DELETING", "DELETED"}:
                    raise DatasetManagementError(
                        "cache_unavailable", "Cached data is being deleted.", 410
                    )
                stored.state = "WRITING"
                stored.generation = uuid4()
                stored.location_fingerprint = location_fingerprint(
                    "dataset_cache", str(path.parent.resolve())
                )
                stored.save(update_fields=["state", "generation", "location_fingerprint"])
                objects.append((stored, path))
        yield
        for stored, path in objects:
            if path.is_file():
                with path.open("rb") as stream:
                    checksum = hashlib.file_digest(stream, "sha256").hexdigest()
                ResourceStorageObject.objects.filter(
                    pk=stored.pk, generation=stored.generation
                ).update(state="AVAILABLE", size_bytes=path.stat().st_size, checksum=checksum)
