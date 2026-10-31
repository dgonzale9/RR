"""Record exact storage ownership and reconcile durable Registry provenance."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from uuid import UUID

from django.conf import settings
from django.db import models, transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone

from resource_registry.management_domain.errors import DatasetManagementError
from resource_registry.models import (
    Dataset,
    DatasetArtifact,
    DatasetChange,
    DatasetGeneratedArtifact,
    DatasetVersion,
    RegistryNamespace,
    ResourceStorageObject,
    ResourceStorageReference,
    StagedResourceIntake,
)
from resource_registry.storage.exceptions import DataRegistryStorageError
from resource_registry.storage.factory import DataRegistryStorageFactory


def object_identity(provider: str, uri: str) -> str:
    """Hash a provider and canonical location without exposing it in public responses."""
    return hashlib.sha256(f"{provider}:{uri}".encode()).hexdigest()


def location_fingerprint(provider: str, location: str | None = None) -> str:
    """Bind managed locations to the configured root or account without storing credentials."""
    if location is None:
        location = (
            str(Path(getattr(settings, "RESOURCE_REGISTRY_LOCAL_STORAGE_DIR", "")).resolve())
            if provider == "local"
            else (
                str(Path(getattr(settings, "RESOURCE_REGISTRY_DATASET_CACHE_DIR", "")).resolve())
                if provider == "dataset_cache"
                else str(getattr(settings, "AZ_STORAGE_ACCOUNT_NAME", ""))
            )
        )
    return hashlib.sha256(f"{provider}:{location}".encode()).hexdigest()


def is_managed_location(provider: str, uri: str) -> bool:
    """Accept only canonical locations inside the configured Registry storage root."""
    if provider not in {"local", "azure_blob", "dataset_cache"} or not uri.startswith(
        ("local://", "azure://", "dataset-cache://")
    ):
        return False

    try:
        storage = DataRegistryStorageFactory().create_provider(provider)
        if provider in {"local", "dataset_cache"}:
            storage._resolve_target_path(uri)  # type: ignore[attr-defined] # Provider validates roots.
        else:
            storage._normalize_blob_path(uri)  # type: ignore[attr-defined] # Provider validates roots.
    except (ValueError, OSError, DataRegistryStorageError):
        return False
    return True


@transaction.atomic
def register_object(
    *,
    provider: str,
    uri: str,
    owner_kind: str,
    owner_id: UUID,
    dataset: Dataset | None = None,
    revision: int = 0,
    size_bytes: int = 0,
    checksum: str = "",
    etag: str = "",
    verified: bool = False,
) -> ResourceStorageObject | None:
    """Attach an exact owner while preventing references to deleting object generations.

    Returns:
        Managed object, or None for an external location that must never be deleted.

    Raises:
        DatasetManagementError: If cleanup already fenced the object or dataset.
    """
    if not is_managed_location(provider, uri):
        return None
    if dataset is not None:
        locked = Dataset.objects.select_for_update().get(pk=dataset.pk)
        if locked.purge_started_at is not None:
            raise DatasetManagementError("dataset_purging", "Dataset data is being deleted.", 410)
    storage_object, _ = ResourceStorageObject.objects.get_or_create(
        identity=object_identity(provider, uri),
        defaults={
            "provider": provider,
            "uri": uri,
            "ownership_verified": verified,
            "size_bytes": size_bytes,
            "checksum": checksum,
            "etag": etag,
            "location_fingerprint": location_fingerprint(provider),
        },
    )
    storage_object = ResourceStorageObject.objects.select_for_update().get(pk=storage_object.pk)
    if storage_object.state in {"DELETING", "DELETED"}:
        raise DatasetManagementError(
            "object_unavailable", "Stored data is no longer available.", 410
        )
    if verified and not storage_object.ownership_verified:
        storage_object.ownership_verified = True
        storage_object.save(update_fields=["ownership_verified"])
    updated_fields = []
    for field, value in (("checksum", checksum), ("etag", etag), ("size_bytes", size_bytes)):
        if value and not getattr(storage_object, field):
            setattr(storage_object, field, value)
            updated_fields.append(field)
    if updated_fields:
        storage_object.save(update_fields=updated_fields)
    namespace_id = None
    if dataset is not None:
        namespace_id = (
            RegistryNamespace.objects.filter(pk=dataset.tenant_id)
            .values_list("id", flat=True)
            .first()
        )
    reference, created = ResourceStorageReference.objects.get_or_create(
        storage_object=storage_object,
        owner_kind=owner_kind,
        owner_id=owner_id,
        owner_revision=revision,
        defaults={"dataset": dataset, "namespace_id": namespace_id},
    )
    if not created and reference.released_at is not None:
        raise DatasetManagementError(
            "reference_released", "The content reference was retired.", 410
        )
    return storage_object


def register_dataset_objects(dataset: Dataset) -> None:
    """Reconcile registered files and proven source copies without reading their contents."""
    if dataset.purge_started_at is not None:
        return
    existing = set(
        ResourceStorageReference.objects.filter(dataset=dataset).values_list(
            "owner_kind", "owner_id"
        )
    )
    for artifact in DatasetArtifact.objects.filter(dataset_version__dataset=dataset).select_related(
        "dataset_version"
    ):
        if artifact.lifecycle_status == "PHYSICAL_DELETED":
            continue
        provider = artifact.storage_provider or ""
        register_cached_artifact(dataset, artifact, existing)
        if ("ARTIFACT", artifact.id) not in existing:
            register_object(
                provider=provider,
                uri=artifact.uri,
                owner_kind="ARTIFACT",
                owner_id=artifact.id,
                dataset=dataset,
                size_bytes=artifact.size_bytes,
                checksum=artifact.checksum or "",
                verified=bool(
                    (artifact.metadata_json or {}).get("managed_upload")
                    or (artifact.metadata_json or {}).get("is_canonical")
                ),
            )
        source_uri = str(
            (artifact.metadata_json or {}).get("source_uri") or artifact.dataset_version.source_uri
        )
        if (
            source_uri
            and source_uri != artifact.uri
            and ("SOURCE", artifact.dataset_version_id) not in existing
        ):
            register_object(
                provider=provider,
                uri=source_uri,
                owner_kind="SOURCE",
                owner_id=artifact.dataset_version_id,
                dataset=dataset,
                verified=False,
            )
    for generated in DatasetGeneratedArtifact.objects.filter(dataset=dataset):
        if ("GENERATED", generated.id) in existing:
            continue
        provider = "local" if generated.storage_uri.startswith("local://") else "azure_blob"
        register_object(
            provider=provider,
            uri=generated.storage_uri,
            owner_kind="GENERATED",
            owner_id=generated.id,
            dataset=dataset,
            size_bytes=generated.file_size_bytes,
            checksum=generated.checksum or "",
            verified=True,
        )
    assignments = dataset.staged_resource_publication_assignments.select_related(
        "publication__intake"
    )
    for assignment in assignments:
        if ("INTAKE_PROVENANCE", assignment.id) in existing:
            continue
        intake = assignment.publication.intake
        stored = register_intake_object(intake)
        if stored is not None:
            ResourceStorageReference.objects.get_or_create(
                storage_object=stored,
                owner_kind="INTAKE_PROVENANCE",
                owner_id=assignment.id,
                dataset=dataset,
                defaults={"namespace_id": dataset.tenant_id, "released_at": timezone.now()},
            )


def register_saved_content(content: models.Model, dataset: Dataset) -> None:
    """Attach newly persisted storage content inside its owning dataset transaction."""
    if isinstance(content, DatasetVersion) and content.source_uri.startswith(
        ("local://", "azure://")
    ):
        provider = "local" if content.source_uri.startswith("local://") else "azure_blob"
        register_object(
            provider=provider,
            uri=content.source_uri,
            owner_kind="SOURCE",
            owner_id=content.id,
            dataset=dataset,
            etag=content.source_etag or "",
            verified=False,
        )
    elif isinstance(content, DatasetArtifact) and content.lifecycle_status != "PHYSICAL_DELETED":
        register_object(
            provider=content.storage_provider or "",
            uri=content.uri,
            owner_kind="ARTIFACT",
            owner_id=content.id,
            dataset=dataset,
            size_bytes=content.size_bytes,
            checksum=content.checksum or "",
            verified=bool(
                (content.metadata_json or {}).get("managed_upload")
                or (content.metadata_json or {}).get("is_canonical")
            ),
        )

        source = content.source_artifact
        if source is not None:
            register_object(
                provider=source.storage_provider or "",
                uri=source.uri,
                owner_kind="SOURCE_ARTIFACT",
                owner_id=content.id,
                dataset=dataset,
                size_bytes=source.size_bytes,
                checksum=source.checksum or "",
                verified=bool(
                    (source.metadata_json or {}).get("managed_upload")
                    or (source.metadata_json or {}).get("is_canonical")
                ),
            )
    elif isinstance(content, DatasetGeneratedArtifact):
        provider = "local" if content.storage_uri.startswith("local://") else "azure_blob"
        register_object(
            provider=provider,
            uri=content.storage_uri,
            owner_kind="GENERATED",
            owner_id=content.id,
            dataset=dataset,
            size_bytes=content.file_size_bytes,
            checksum=content.checksum or "",
            verified=bool(
                (content.metadata_json or {}).get("managed_upload")
                or (content.metadata_json or {}).get("is_canonical")
            ),
        )


def register_cached_artifact(
    dataset: Dataset, artifact: DatasetArtifact, existing: set[tuple[str, UUID]]
) -> None:
    """Inventory exact cache files derived from a registered artifact's server-owned identity."""
    root = Path(str(getattr(settings, "RESOURCE_REGISTRY_DATASET_CACHE_DIR", "")))
    raw = "|".join(
        [
            str(dataset.tenant_id),
            str(dataset.id),
            str(artifact.dataset_version_id),
            str(artifact.id),
            artifact.checksum or "",
            artifact.uri,
            artifact.format,
        ]
    )
    key = hashlib.sha256(raw.encode()).hexdigest()
    suffix = Path(artifact.uri).suffix or (
        ".parquet" if artifact.format.lower() == "parquet" else ".bin"
    )
    for kind, filename, checksum in (
        ("CACHE_DATA", key + suffix, artifact.checksum or ""),
        ("CACHE_METADATA", key + ".json", ""),
    ):
        path = root / filename
        if not path.is_file() or path.is_symlink():
            continue
        if kind == "CACHE_METADATA":
            checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        current = ResourceStorageObject.objects.filter(
            identity=object_identity("dataset_cache", f"dataset-cache://{filename}"),
            state="AVAILABLE",
        ).first()
        if current is not None:
            ResourceStorageObject.objects.filter(pk=current.pk, state="AVAILABLE").update(
                checksum=checksum, size_bytes=path.stat().st_size
            )
        if (kind, artifact.id) in existing:
            continue
        register_object(
            provider="dataset_cache",
            uri=f"dataset-cache://{filename}",
            owner_kind=kind,
            owner_id=artifact.id,
            dataset=dataset,
            size_bytes=path.stat().st_size,
            checksum=checksum,
            verified=True,
        )


def reconcile_historical_inventory(*, batch_size: int = 100) -> int:
    """Reconstruct bounded, unequivocal artifact ownership without reading or deleting files."""
    references = ResourceStorageReference.objects.filter(
        owner_kind="ARTIFACT", owner_id=OuterRef("pk")
    )
    artifacts = (
        DatasetArtifact.objects.filter(dataset_version__dataset__purge_started_at__isnull=True)
        .exclude(lifecycle_status="PHYSICAL_DELETED")
        .annotate(indexed=Exists(references))
        .filter(indexed=False)
        .select_related("dataset_version__dataset")
    )
    count = 0
    for artifact in (
        artifacts.filter(uri__startswith="local://")
        .union(artifacts.filter(uri__startswith="azure://"))
        .order_by("created_at")[:batch_size]
    ):
        dataset = artifact.dataset_version.dataset
        register_saved_content(artifact.dataset_version, dataset)
        register_saved_content(artifact, dataset)
        count += 1
    return count


def register_intake_object(intake: StagedResourceIntake) -> ResourceStorageObject | None:
    """Track a verified private source independently from its publication history."""

    storage = DataRegistryStorageFactory().create_provider(intake.storage_provider)
    if intake.source_deleted_at is not None or intake.source_cleanup_started_at is not None:
        return ResourceStorageObject.objects.filter(
            identity=object_identity(
                intake.storage_provider, storage.get_uri(intake.storage_object_key)
            )
        ).first()
    stored = register_object(
        provider=intake.storage_provider,
        uri=storage.get_uri(intake.storage_object_key),
        owner_kind="INTAKE",
        owner_id=intake.id,
        verified=True,
        size_bytes=intake.actual_size_bytes or intake.declared_size_bytes,
        checksum=intake.verified_checksum or "",
        etag=intake.storage_etag or "",
    )
    if stored is not None and (intake.verified_checksum or intake.storage_etag):
        ResourceStorageObject.objects.filter(pk=stored.pk, state="WRITING").update(
            state="AVAILABLE"
        )
    return stored


def release_dataset_references(dataset: Dataset) -> None:
    """Release content ownership without deleting its provenance rows."""
    ResourceStorageReference.objects.filter(dataset=dataset, released_at__isnull=True).update(
        released_at=timezone.now()
    )


def dataset_inventory_fingerprint(dataset: Dataset) -> str:
    """Fingerprint the reviewed file and derivative identities without storing data samples."""
    inventory: dict[str, object] = {
        "objects": list(
            ResourceStorageObject.objects.filter(references__dataset=dataset)
            .distinct()
            .order_by("id")
            .values_list("id", "generation", "checksum", "etag", "size_bytes")
        ),
        "versions": list(dataset.versions.order_by("id").values_list("id", flat=True)),
        "publications": list(
            dataset.staged_resource_publication_assignments.order_by("id").values_list(
                "id", "publication_id", "detached_at"
            )
        ),
    }
    for relation in (
        "query_jobs",
        "query_runs",
        "intelligence_jobs",
        "analysis_artifacts",
        "chart_artifacts",
        "export_plans",
        "generated_artifacts",
    ):
        inventory[relation] = list(
            getattr(dataset, relation).order_by("id").values_list("id", flat=True)
        )
    return hashlib.sha256(json.dumps(inventory, sort_keys=True, default=str).encode()).hexdigest()


def get_change_dataset_ids(change: DatasetChange) -> set[str]:
    """Collect explicit dataset identities from a proposal's target and publication evidence."""
    identifiers = {str(change.specification.get("dataset_id") or "")}
    for entries in (
        change.specification.get("sheets", []),
        change.prepared.get("items", []),
        change.result.get("versions", []),
    ):
        identifiers.update(
            str(entry.get("dataset_id") or "") for entry in entries if isinstance(entry, Mapping)
        )
    return identifiers - {""}
