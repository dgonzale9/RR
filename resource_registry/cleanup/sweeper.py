"""Discover policy cleanup candidates without deleting or changing published content."""

from __future__ import annotations

from datetime import datetime, timedelta

from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from resource_registry.cleanup.inventory import register_intake_object
from resource_registry.cleanup.protection import PENDING_CHANGES, has_pending_intake_use
from resource_registry.models import (
    DatasetChange,
    DatasetManagementConfiguration,
    RegistryNamespace,
    ResourceCleanupItem,
    ResourceCleanupRequest,
    ResourceStorageObject,
    ResourceStorageReference,
    StagedResourceIntake,
    StagedResourcePublication,
)


def _namespace_configuration(namespace: RegistryNamespace) -> DatasetManagementConfiguration | None:
    organization_id = namespace.organization_id or (
        namespace.workspace.organization_id if namespace.workspace else None
    )
    if organization_id is None:
        return None
    return DatasetManagementConfiguration.objects.filter(organization_id=organization_id).first()


def _candidate_deadline(
    reference: ResourceStorageReference,
) -> tuple[datetime | None, RegistryNamespace | None]:
    if reference.owner_kind == "PUBLICATION":
        publication = (
            StagedResourcePublication.objects.select_related("registry_namespace__workspace")
            .filter(pk=reference.owner_id)
            .first()
        )
        if publication is None:
            return None, None
        is_current = reference.storage_object.uri.endswith(
            "/" + (publication.destination_object_key or "")
        )
        if is_current and not publication.cleanup_required and publication.completed_at is None:
            return None, publication.registry_namespace
        configuration = _namespace_configuration(publication.registry_namespace)
        days = max(7, configuration.discarded_candidate_retention_days if configuration else 7)
        return (
            max(reference.created_at, publication.completed_at or publication.created_at)
            + timedelta(days=days),
            publication.registry_namespace,
        )
    change = (
        DatasetChange.objects.select_related("namespace__workspace", "namespace__organization")
        .filter(pk=reference.owner_id)
        .first()
    )
    if change is None:
        return None, None
    if reference.owner_revision == change.revision and change.status in PENDING_CHANGES:
        return None, change.namespace
    configuration = _namespace_configuration(change.namespace)
    days = max(7, configuration.discarded_candidate_retention_days if configuration else 7)
    retired_at = max(reference.created_at, change.completed_at or change.updated_at)
    return retired_at + timedelta(days=days), change.namespace


def discover_cleanup_candidates(*, batch_size: int = 100) -> int:
    """Persist bounded simulation-visible candidates; execution remains separately gated."""
    created_count = 0
    now = timezone.now()
    candidates = ResourceStorageObject.objects.filter(
        deleted_at__isnull=True, created_at__lte=now - timedelta(days=7)
    ).exclude(references__owner_kind__in=["ARTIFACT", "SOURCE", "GENERATED", "INTAKE"])
    pending_changes = DatasetChange.objects.filter(
        pk=OuterRef("owner_id"), revision=OuterRef("owner_revision"), status__in=PENDING_CHANGES
    )
    pending_references = (
        ResourceStorageReference.objects.filter(
            storage_object_id=OuterRef("pk"), owner_kind="CHANGE", released_at__isnull=True
        )
        .annotate(pending=Exists(pending_changes))
        .filter(pending=True)
    )
    candidates = candidates.annotate(pending=Exists(pending_references)).filter(pending=False)
    for stored in candidates.exclude(
        cleanup_items__request__kind__in=["CANDIDATE", "ORPHAN"]
    ).order_by("created_at")[:batch_size]:
        references = list(stored.references.filter(released_at__isnull=True))
        if references and any(
            reference.owner_kind not in {"CHANGE", "PUBLICATION"} for reference in references
        ):
            continue
        namespace = None
        deadline = stored.created_at + timedelta(days=7)
        eligible = stored.ownership_verified
        for reference in references:
            candidate_deadline, reference_namespace = _candidate_deadline(reference)
            if candidate_deadline is None:
                eligible = False
                break
            deadline = max(deadline, candidate_deadline)
            namespace = reference_namespace
        if not eligible or deadline > now:
            continue
        request, created = ResourceCleanupRequest.objects.get_or_create(
            idempotency_key=f"candidate:{stored.id}",
            defaults={
                "kind": "CANDIDATE" if references else "ORPHAN",
                "namespace": namespace,
                "reason": "Expired unused preparation data",
                "inventory_fingerprint": stored.identity,
                "execute_after": deadline,
            },
        )
        ResourceCleanupItem.objects.get_or_create(request=request, storage_object=stored)
        created_count += int(created)
    intakes = (
        StagedResourceIntake.objects.filter(
            source_deleted_at__isnull=True,
            status__in=[
                "PENDING_UPLOAD",
                "COMPLETED",
                "PUBLISHED",
                "CANCELLED",
                "EXPIRED",
                "FAILED",
                "READY_FOR_PUBLICATION",
            ],
        )
        .exclude(cleanup_requests__isnull=False)
        .filter(
            Q(status__in=["CANCELLED", "EXPIRED", "FAILED"])
            | Q(expires_at__lte=now)
            | Q(status="PUBLISHED", published_at__lte=now - timedelta(days=30))
        )
        .exclude(datasetchange__status__in=PENDING_CHANGES)
    )
    for intake in intakes.order_by("created_at")[:batch_size]:
        if has_pending_intake_use(intake.id) or intake.status in {"ANALYZING", "PUBLISHING"}:
            continue
        publications = list(
            intake.publications.select_related(
                "registry_namespace__workspace", "registry_namespace__organization"
            )
        )
        namespace = publications[0].registry_namespace if publications else None
        days = 30
        for publication in publications:
            configuration = _namespace_configuration(publication.registry_namespace)
            days = max(days, configuration.intake_source_retention_days if configuration else 30)
        if intake.status == "PUBLISHED":
            latest_use = max(
                value
                for value in (
                    intake.created_at,
                    intake.published_at,
                    intake.source_last_used_at,
                    *(publication.completed_at for publication in publications),
                )
                if value is not None
            )
            deadline = latest_use + timedelta(days=days)
        elif intake.status in {"CANCELLED", "EXPIRED", "FAILED"} or (
            intake.expires_at and intake.expires_at <= now
        ):
            deadline = intake.cancelled_at or intake.expires_at or intake.updated_at
        else:
            continue
        if deadline > now:
            continue
        intake_object = register_intake_object(intake)
        if intake_object is None:
            continue
        request, created = ResourceCleanupRequest.objects.get_or_create(
            idempotency_key=f"intake:{intake.id}",
            defaults={
                "kind": "INTAKE",
                "intake": intake,
                "namespace": namespace,
                "reason": "Source retention period elapsed",
                "inventory_fingerprint": intake_object.identity,
                "execute_after": deadline,
            },
        )
        ResourceCleanupItem.objects.get_or_create(request=request, storage_object=intake_object)
        created_count += int(created)
    return created_count


def prepare_automatic_request(request: ResourceCleanupRequest) -> str:
    """Revalidate automatic retention and release only the expired source ownership."""
    now = timezone.now()
    if request.intake_id:
        intake = StagedResourceIntake.objects.select_for_update().get(pk=request.intake_id)
        if has_pending_intake_use(intake.id) or intake.status in {"ANALYZING", "PUBLISHING"}:
            return "source_in_use"
        days = 30
        for publication in intake.publications.select_related("registry_namespace__workspace"):
            configuration = _namespace_configuration(publication.registry_namespace)
            if (
                configuration is None
                or not configuration.enabled
                or configuration.cleanup_mode != "EXECUTE"
            ):
                return "shared_scope_disabled"
            days = max(days, configuration.intake_source_retention_days)
            if (
                publication.registration_submission_id
                and not publication.dataset_assignments.exists()
            ):
                return "published_copy_unavailable"
            if (
                publication.dataset_assignments.filter(dataset__purge_started_at__isnull=True)
                .exclude(dataset_version__artifacts__lifecycle_status="ACTIVE")
                .exists()
            ):
                return "published_copy_unavailable"
        if (
            intake.status == "PUBLISHED"
            and max(
                value
                for value in (intake.created_at, intake.published_at, intake.source_last_used_at)
                if value is not None
            )
            + timedelta(days=days)
            > now
        ):
            return "source_retention_period"
        ResourceStorageReference.objects.filter(owner_kind="INTAKE", owner_id=intake.pk).update(
            released_at=now
        )
        intake.source_cleanup_started_at = intake.source_cleanup_started_at or now
        if intake.status in {"PENDING_UPLOAD", "COMPLETED", "READY_FOR_PUBLICATION"}:
            intake.status = "EXPIRED"
        intake.save(update_fields=["source_cleanup_started_at", "status"])
        ResourceStorageObject.objects.filter(
            cleanup_items__request=request, state="WRITING"
        ).exclude(leases__expires_at__gt=now).update(state="AVAILABLE")
        return ""
    for reference in ResourceStorageReference.objects.filter(
        storage_object__cleanup_items__request=request, released_at__isnull=True
    ):
        if reference.owner_kind not in {"CHANGE", "PUBLICATION"}:
            return "retained_reference"
        deadline, namespace = _candidate_deadline(reference)
        if deadline is None or deadline > now:
            return "pending_update"
        if namespace is not None:
            configuration = _namespace_configuration(namespace)
            if (
                configuration is None
                or not configuration.enabled
                or configuration.cleanup_mode != "EXECUTE"
            ):
                return "shared_scope_disabled"
        reference.released_at = now
        reference.save(update_fields=["released_at"])
    return ""
