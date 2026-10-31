"""Evaluate content dependencies rather than relying on catalog usage summaries."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from django.db.models import Q
from django.utils import timezone

from resource_registry.cleanup.inventory import get_change_dataset_ids
from resource_registry.models import (
    Dataset,
    DatasetArtifact,
    DatasetChange,
    DatasetGeneratedArtifact,
    DatasetRegistrationJob,
    ResourceStorageLease,
    ResourceStorageObject,
    ResourceStorageReference,
    StagedResourceInspectionJob,
    StagedResourceIntake,
    StagedResourcePublication,
    TransformationDefinitionVersion,
    TransformationRun,
    TransformationWorkspaceLease,
    WorkbookIngestionJob,
)

PENDING_CHANGES = ("QUEUED", "PROCESSING", "READY", "NEEDS_MANAGER")
ACTIVE_JOBS = ("QUEUED", "RUNNING", "PENDING", "PROCESSING", "OUTPUT_PENDING")


def has_durable_object_owner(provider: str, uri: str) -> bool:
    """Protect historical records not yet reconciled into the inventory."""
    key = uri.split("://", 1)[-1]
    if provider == "azure_blob":
        key = key.partition("/")[2]
    if (
        TransformationWorkspaceLease.objects.filter(
            storage_provider=provider, workspace_key=key, run__status__in=ACTIVE_JOBS
        )
        .exclude(state="RELEASED")
        .exists()
    ):
        return True
    if StagedResourceIntake.objects.filter(
        storage_provider=provider,
        storage_object_key=key,
        source_cleanup_started_at__isnull=True,
        source_deleted_at__isnull=True,
    ).exists():
        return True
    if StagedResourcePublication.objects.filter(
        destination_object_key=key,
        intake__storage_provider=provider,
        cleanup_required=False,
        completed_at__isnull=True,
    ).exists():
        return True
    return (
        DatasetArtifact.objects.filter(
            Q(uri=uri) | Q(metadata_json__source_uri=uri) | Q(dataset_version__source_uri=uri),
            storage_provider=provider,
            dataset_version__dataset__purge_started_at__isnull=True,
        )
        .exclude(lifecycle_status="PHYSICAL_DELETED")
        .exists()
        or DatasetGeneratedArtifact.objects.filter(
            storage_uri=uri, dataset__purge_started_at__isnull=True
        ).exists()
    )


def has_pending_intake_use(intake_id: UUID) -> bool:
    """Protect intake sources while any destination still needs preparation or publication."""

    return (
        DatasetChange.objects.filter(intake_id=intake_id, status__in=PENDING_CHANGES).exists()
        or StagedResourceInspectionJob.objects.filter(
            intake_id=intake_id, status__in=["QUEUED", "RUNNING"]
        ).exists()
    )


def get_dataset_blocker(dataset: Dataset) -> str:
    """Return a non-sensitive reason preventing the start of irreversible cleanup."""
    if dataset.status != "ARCHIVED" or dataset.archived_at is None:
        return "dataset_not_archived"
    if ResourceStorageLease.objects.filter(
        Q(dataset=dataset) | Q(storage_object__references__dataset=dataset),
        expires_at__gt=timezone.now(),
    ).exists():
        return "work_in_progress"
    for relation in ("query_jobs", "intelligence_jobs", "external_jobs"):
        if getattr(dataset, relation).filter(status__in=ACTIVE_JOBS).exists():
            return "work_in_progress"
    for change in DatasetChange.objects.filter(
        namespace_id=dataset.tenant_id, status__in=PENDING_CHANGES
    ).iterator():
        if str(dataset.id) in get_change_dataset_ids(change) or (
            dataset.workbook_id and change.workbook_id == dataset.workbook_id
        ):
            return "pending_update"
    # Materialized lineage is historical evidence. Exact source files remain protected
    # by content references; published definitions and active runs retain their inputs.
    version_identifiers = tuple(
        str(identifier) for identifier in dataset.versions.values_list("id", flat=True)
    )
    for run in TransformationRun.objects.filter(status__in=ACTIVE_JOBS).iterator():
        if _contains_identifier(run.execution_plan_snapshot_json, str(dataset.id)) or any(
            _contains_identifier(run.execution_plan_snapshot_json, identifier)
            for identifier in version_identifiers
        ):
            return "transformation_in_progress"
    for definition in TransformationDefinitionVersion.objects.filter(
        definition__archived_at__isnull=True, status="PUBLISHED"
    ).iterator():
        if _contains_identifier(definition.specification_json, str(dataset.id)) or any(
            _contains_identifier(definition.specification_json, identifier)
            for identifier in version_identifiers
        ):
            return "registered_transformation"
    for model in (DatasetRegistrationJob, WorkbookIngestionJob):
        for job in model.objects.filter(
            tenant_id=dataset.tenant_id, status__in=(*ACTIVE_JOBS, "SUBMITTED")
        ).iterator():
            if _contains_identifier(job.request_payload_json, str(dataset.id)) or (
                dataset.workbook_group_id
                and getattr(job, "workbook_group_id", None) == dataset.workbook_group_id
            ):
                return "ingestion_in_progress"
    return ""


def _contains_identifier(value: object, identifier: str) -> bool:
    if isinstance(value, dict):
        return any(_contains_identifier(item, identifier) for item in value.values())
    if isinstance(value, list):
        return any(_contains_identifier(item, identifier) for item in value)
    return value == identifier


def get_object_blocker(storage_object: ResourceStorageObject) -> str:
    """Protect ambiguous, shared and currently leased physical objects globally."""
    if not storage_object.ownership_verified:
        return "ownership_unverified"
    if storage_object.leases.filter(expires_at__gt=timezone.now()).exists():
        return "object_in_use"
    if storage_object.state == "WRITING" and storage_object.created_at > timezone.now() - timedelta(
        days=7
    ):
        return "write_in_progress"
    if has_durable_object_owner(storage_object.provider, storage_object.uri):
        return "shared_dataset"
    references = ResourceStorageReference.objects.filter(
        storage_object=storage_object, released_at__isnull=True
    )
    if references.filter(dataset__isnull=False).exists():
        return "shared_dataset"
    for reference in references.filter(dataset__isnull=True):
        if reference.owner_kind == "CHANGE":
            change = DatasetChange.objects.filter(pk=reference.owner_id).first()
            if change is None or (
                change.revision == reference.owner_revision and change.status in PENDING_CHANGES
            ):
                return "pending_update"
        elif reference.owner_kind == "INTAKE":
            intake = StagedResourceIntake.objects.filter(pk=reference.owner_id).first()
            if intake is not None and intake.source_deleted_at is None:
                return "retained_source"
        elif reference.owner_kind == "PUBLICATION":
            publication = StagedResourcePublication.objects.filter(pk=reference.owner_id).first()
            if publication is None or (
                storage_object.uri.endswith("/" + (publication.destination_object_key or ""))
                and not publication.cleanup_required
                and publication.completed_at is None
            ):
                return "publication_in_progress"
        else:
            return "ownership_unverified"
    return ""
