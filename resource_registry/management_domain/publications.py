"""Bind dataset maintenance to durable intake destination and version provenance."""

from collections.abc import Sequence
from uuid import UUID

from django.contrib.auth.models import User
from django.db.models import Max

from resource_registry.management_domain.errors import DatasetManagementError
from resource_registry.models import (
    DatasetActivity,
    DatasetArtifact,
    DatasetChange,
    DatasetVersion,
    StagedResourceIntake,
    StagedResourcePublication,
    StagedResourcePublicationDataset,
)
from resource_registry.services.destination_assignment_removal_service import (
    DestinationAssignmentRemovalService,
)


def reserve_dataset_publication(
    change: DatasetChange, actor: User, *, reconciliation: bool = False
) -> StagedResourcePublication | None:
    """Lock or create the destination publication before locking its datasets.

    Args:
        change: Authorized proposal locked by the caller's publication transaction.
        actor: Verified publisher, including the historical actor during reconciliation.
        reconciliation: Whether an old completed proposal is being repaired.

    Returns:
        The locked publication, or None for proposals without a staged intake.

    Raises:
        DatasetManagementError: If the source, destination or previous attempt is unavailable.
    """
    if change.intake_id is None:
        return None
    intake = StagedResourceIntake.objects.select_for_update().get(pk=change.intake_id)
    if intake.status not in {"READY_FOR_PUBLICATION", "PUBLISHED"}:
        raise DatasetManagementError(
            "source_unavailable", "The intake is not available for publication."
        )
    attempts = StagedResourcePublication.objects.filter(
        intake=intake, registry_namespace_id=change.namespace_id
    )
    if reconciliation and attempts.filter(assignment_status="DETACHED").exists():
        raise DatasetManagementError(
            "publication_evidence_missing",
            "A previous destination was removed; review its publication history.",
        )
    publication = (
        attempts.select_for_update()
        .filter(assignment_status__in=["ACTIVE", "DETACH_REQUESTED"])
        .first()
    )
    if publication is not None:
        if publication.assignment_status != "ACTIVE":
            raise DatasetManagementError("publication_detaching", "Destination removal is pending.")
        job = DestinationAssignmentRemovalService._get_publication_job(publication=publication)
        if job is not None and DestinationAssignmentRemovalService._is_job_in_progress(job=job):
            raise DatasetManagementError(
                "publication_in_progress", "The previous publication is still processing."
            )
        if publication.completed_at is None:
            publication.published_by = actor
            publication.save(update_fields=["published_by"])
        return publication
    previous_attempt = attempts.aggregate(value=Max("publication_attempt"))["value"] or 0
    if reconciliation and previous_attempt:
        raise DatasetManagementError(
            "publication_evidence_missing",
            "A previous destination was removed; review its publication history.",
        )
    namespace = change.namespace
    workspace = namespace.workspace
    organization_id = workspace.organization_id if workspace else namespace.organization_id
    if organization_id is None:
        raise DatasetManagementError("not_found", "Publication destination not found.", 404)
    return StagedResourcePublication.objects.create(
        intake=intake,
        registry_namespace=namespace,
        organization_id=organization_id,
        workspace_id=namespace.workspace_id,
        visibility_scope="WORKSPACE" if namespace.workspace_id else "ORGANIZATION",
        publication_attempt=previous_attempt + 1,
        published_by=actor,
    )


def record_dataset_publication(
    change: DatasetChange,
    publication: StagedResourcePublication | None,
    versions: Sequence[dict[str, str]],
) -> None:
    """Persist exact version assignments in the caller's publication transaction.

    Args:
        change: Authorized completed proposal with its immutable publication timestamp.
        publication: Locked active destination publication, or None for an upload session.
        versions: Server-produced dataset and version identifiers.

    Raises:
        DatasetManagementError: If any version does not belong to the admitted destination.
    """
    if publication is None:
        return
    resolved = list(
        DatasetVersion.objects.select_related("dataset").filter(
            pk__in=[entry["dataset_version_id"] for entry in versions], status="READY"
        )
    )
    expected = {(entry["dataset_id"], entry["dataset_version_id"]) for entry in versions}
    actual = {(str(version.dataset_id), str(version.id)) for version in resolved}
    if actual != expected or any(
        not DestinationAssignmentRemovalService._matches_publication_destination(
            publication=publication, dataset=version.dataset
        )
        for version in resolved
    ):
        raise DatasetManagementError(
            "publication_evidence_missing", "Published versions do not match their destination."
        )
    StagedResourcePublicationDataset.objects.bulk_create(
        [
            StagedResourcePublicationDataset(
                publication=publication, dataset_id=version.dataset_id, dataset_version=version
            )
            for version in resolved
        ],
        ignore_conflicts=True,
    )
    if publication.completed_at is None:
        publication.completed_at = change.completed_at
        publication.save(update_fields=["completed_at"])
    change.publication = publication
    change.result = {**change.result, "versions": list(versions)}


def reconcile_dataset_publication(change: DatasetChange) -> None:
    """Reconstruct a completed proposal's missing publication from unambiguous history.

    Args:
        change: Authorized PUBLISHED proposal locked by the caller's transaction.

    Raises:
        DatasetManagementError: If exact versions or their historical publisher cannot be proven.
    """
    if change.intake_id is None or change.publication_id is not None:
        return
    expected_datasets = set(change.result.get("dataset_ids", []))
    artifacts = DatasetArtifact.objects.filter(
        metadata_json__change_id=str(change.id), dataset_version__status="READY"
    ).values_list("dataset_version__dataset_id", "dataset_version_id")
    identifiers = {(str(dataset_id), str(version_id)) for dataset_id, version_id in artifacts}
    if (
        not expected_datasets
        or len(identifiers) != len(expected_datasets)
        or {dataset_id for dataset_id, _ in identifiers} != expected_datasets
    ):
        raise DatasetManagementError(
            "publication_evidence_missing", "Exact historical versions require publication review."
        )
    activities = list(
        DatasetActivity.objects.filter(
            action="VERSION_PUBLISHED",
            version_id__in=[UUID(version_id) for _, version_id in identifiers],
            created_at__gte=change.created_at,
            created_at__lte=change.completed_at,
        ).values_list("version_id", "actor_id")
    )
    actors = {actor_id for _, actor_id in activities}
    if (
        len(actors) != 1
        or None in actors
        or {str(version_id) for version_id, _ in activities}
        != {version_id for _, version_id in identifiers}
    ):
        raise DatasetManagementError(
            "publication_evidence_missing", "The original publisher requires publication review."
        )
    actor = User.objects.get(pk=actors.pop())
    publication = reserve_dataset_publication(change, actor, reconciliation=True)
    record_dataset_publication(
        change,
        publication,
        [
            {"dataset_id": dataset_id, "dataset_version_id": version_id}
            for dataset_id, version_id in sorted(identifiers)
        ],
    )
    change.save(update_fields=["publication", "result"])
