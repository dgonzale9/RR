"""Detach staged-resource destination assignments without deleting durable data."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

from Organization.models import Organization
from resource_registry.intake.permissions import (
    can_remove_global_destination_assignments,
    can_remove_scoped_destination_assignments,
    get_removable_staged_resource_publications,
)
from resource_registry.models import (
    ArtifactLifecycleStatus,
    Dataset,
    DatasetArtifact,
    DatasetRegistrationJob,
    DatasetRegistrationJobStatus,
    DatasetStatus,
    DatasetVersion,
    StagedResourcePublication,
    StagedResourcePublicationDataset,
    StagedResourcePublicationStatus,
    WorkbookIngestionJob,
    WorkbookIngestionJobStatus,
)
from resource_registry.services.dataset_registration_job_service import (
    DatasetRegistrationJobService,
)
from resource_registry.services.workbook_ingestion_job_service import WorkbookIngestionJobService
from resource_registry.uploads.exceptions import UploadApplicationError

_logger = logging.getLogger("app-logger")


@dataclass(frozen=True)
class DestinationAssignmentRemovalResult:
    """Describe the stable outcome of a destination-assignment removal request."""

    publication_id: UUID
    assignment_status: str
    is_pending: bool


class DestinationAssignmentRemovalService:
    """Detach one immutable publication destination while preserving durable evidence."""

    def __init__(
        self,
        *,
        workbook_job_service: WorkbookIngestionJobService | None = None,
        dataset_registration_job_service: DatasetRegistrationJobService | None = None,
    ) -> None:
        """Initialize destination detachment dependencies.

        Args:
            workbook_job_service: Service used to cancel active workbook ingestion jobs.
            dataset_registration_job_service: Service used to cancel active CSV registration jobs.
        """
        self._workbook_job_service = workbook_job_service or WorkbookIngestionJobService()
        self._dataset_registration_job_service = (
            dataset_registration_job_service or DatasetRegistrationJobService()
        )

    def request_detachment(
        self,
        *,
        user: User,
        publication_id: UUID,
        reason: str,
        organization: Organization | None = None,
    ) -> DestinationAssignmentRemovalResult:
        """Request logical removal of one destination publication.

        Args:
            user: Global operator or scoped administrator initiating the removal.
            publication_id: Immutable publication identifier.
            reason: Human-readable, non-secret removal reason.
            organization: Trusted host Organization for scoped administrator removal.

        Returns:
            The resulting detached or pending lifecycle state.

        Raises:
            UploadApplicationError: If authorization, publication state, or evidence is invalid.
        """
        if not (
            can_remove_global_destination_assignments(user)
            or can_remove_scoped_destination_assignments(user, organization)
        ):
            raise UploadApplicationError("AUTHORIZATION_DENIED", 403)
        normalized_reason = str(reason or "").strip()
        if not normalized_reason:
            raise UploadApplicationError("DETACHMENT_REASON_REQUIRED", 400)

        with transaction.atomic():
            publication = self._get_locked_publication(publication_id=publication_id)
            if (
                not get_removable_staged_resource_publications(user, organization)
                .filter(pk=publication_id)
                .exists()
            ):
                raise UploadApplicationError("AUTHORIZATION_DENIED", 403)
            if publication.assignment_status == StagedResourcePublicationStatus.DETACHED:
                return self._result(publication=publication)
            if publication.assignment_status == StagedResourcePublicationStatus.ACTIVE:
                publication.assignment_status = StagedResourcePublicationStatus.DETACH_REQUESTED
                publication.detachment_requested_at = timezone.now()
                publication.detached_by = user
                publication.detachment_reason = normalized_reason
                publication.save(
                    update_fields=[
                        "assignment_status",
                        "detachment_requested_at",
                        "detached_by",
                        "detachment_reason",
                    ]
                )
            job = self._get_publication_job(publication=publication)
            if job is not None and self._is_job_in_progress(job=job):
                self._request_job_cancellation(job=job)

        return self.finalize_detachment(publication_id=publication_id)

    def record_job_outputs(
        self,
        *,
        publication_id: UUID | None,
        result_payload: dict[str, Any],
    ) -> None:
        """Persist exact publication output links from a successful registration result.

        Args:
            publication_id: Optional staged publication associated with the job.
            result_payload: Normalized CSV or workbook registration result payload.

        Raises:
            UploadApplicationError: If result output ownership cannot be proven.
        """
        if publication_id is None:
            return
        with transaction.atomic():
            publication = self._get_locked_publication(publication_id=publication_id)
            output_identifiers = self._validated_output_identifiers(result_payload=result_payload)
            for dataset_id, dataset_version_id in output_identifiers:
                version = (
                    DatasetVersion.objects.select_related("dataset")
                    .select_for_update()
                    .filter(id=dataset_version_id, dataset_id=dataset_id)
                    .first()
                )
                if version is None or not self._matches_publication_destination(
                    publication=publication,
                    dataset=version.dataset,
                ):
                    raise UploadApplicationError("PUBLICATION_OUTPUT_PROVENANCE_INVALID", 409)
                StagedResourcePublicationDataset.objects.get_or_create(
                    publication=publication,
                    dataset=version.dataset,
                    dataset_version=version,
                )
            if publication.assignment_status == StagedResourcePublicationStatus.DETACHED:
                StagedResourcePublicationDataset.objects.filter(
                    publication=publication,
                    detached_at__isnull=True,
                ).update(detached_at=publication.detached_at or timezone.now())

    def finalize_detachment(
        self,
        *,
        publication_id: UUID,
    ) -> DestinationAssignmentRemovalResult:
        """Finalize a requested detachment after its worker is no longer running.

        Args:
            publication_id: Immutable publication identifier.

        Returns:
            The detached or still-pending lifecycle state.

        Raises:
            UploadApplicationError: If completed output ownership cannot be proven.
        """
        with transaction.atomic():
            publication = self._get_locked_publication(publication_id=publication_id)
            if publication.assignment_status == StagedResourcePublicationStatus.DETACHED:
                return self._result(publication=publication)
            if publication.assignment_status != StagedResourcePublicationStatus.DETACH_REQUESTED:
                raise UploadApplicationError("DETACHMENT_NOT_REQUESTED", 409)
            job = self._get_publication_job(publication=publication)
            if job is not None and self._is_job_in_progress(job=job):
                return self._result(publication=publication)
            if job is not None and self._is_job_successful(job=job):
                completed_output_identifiers = self._validated_output_identifiers(
                    result_payload=job.result_payload_json
                )
                assignment_identifier_rows = publication.dataset_assignments.values_list(
                    "dataset_id",
                    "dataset_version_id",
                )
                recorded_output_identifiers = {
                    (str(dataset_id), str(dataset_version_id))
                    for dataset_id, dataset_version_id in assignment_identifier_rows
                }
                if set(completed_output_identifiers) != recorded_output_identifiers:
                    raise UploadApplicationError("PUBLICATION_OUTPUT_RECONCILIATION_REQUIRED", 409)

            assignment_rows = list(
                publication.dataset_assignments.select_related("dataset").select_for_update()
            )
            affected_dataset_ids = sorted(
                {assignment.dataset_id for assignment in assignment_rows},
                key=str,
            )
            locked_dataset_ids = list(
                Dataset.objects.select_for_update()
                .filter(id__in=affected_dataset_ids)
                .order_by("id")
                .values_list("id", flat=True)
            )
            detached_at = timezone.now()
            StagedResourcePublicationDataset.objects.filter(
                id__in=[assignment.id for assignment in assignment_rows],
                detached_at__isnull=True,
            ).update(detached_at=detached_at)
            for dataset_id in locked_dataset_ids:
                if StagedResourcePublicationDataset.objects.filter(
                    dataset_id=dataset_id,
                    detached_at__isnull=True,
                    publication__assignment_status__in=[
                        StagedResourcePublicationStatus.ACTIVE,
                        StagedResourcePublicationStatus.DETACH_REQUESTED,
                    ],
                ).exists():
                    continue
                self._archive_dataset(dataset_id=dataset_id, archived_at=detached_at)
            publication.assignment_status = StagedResourcePublicationStatus.DETACHED
            publication.detached_at = detached_at
            publication.save(update_fields=["assignment_status", "detached_at"])
            _logger.info(
                "Resource Registry destination assignment detached.",
                extra={
                    "event_code": "STAGED_WORKBOOK_DESTINATION_DETACHED",
                    "publication_id": str(publication.id),
                    "registry_namespace_id": str(publication.registry_namespace_id),
                    "detached_by_id": publication.detached_by_id,
                },
            )
            return self._result(publication=publication)

    def reconcile_requested_detachments(self, *, batch_size: int = 100) -> int:
        """Retry finalization of destination removals whose jobs reached a terminal state.

        Args:
            batch_size: Maximum requested removals to inspect.

        Returns:
            Number of removals finalized during this run.
        """
        publication_ids = list(
            StagedResourcePublication.objects.filter(
                assignment_status=StagedResourcePublicationStatus.DETACH_REQUESTED
            )
            .order_by("detachment_requested_at", "id")
            .values_list("id", flat=True)[: max(int(batch_size), 1)]
        )
        finalized = 0
        for publication_id in publication_ids:
            try:
                result = self.finalize_detachment(publication_id=publication_id)
            except UploadApplicationError:
                continue
            if not result.is_pending:
                finalized += 1
        return finalized

    def finalize_detachment_for_ingestion_job(self, *, ingestion_job_id: UUID) -> int:
        """Finalize the pending staged-publication removal linked to one terminal job.

        Args:
            ingestion_job_id: Immutable workbook ingestion job identifier.

        Returns:
            Number of detached publications finalized for the job.
        """
        publication_ids = StagedResourcePublication.objects.filter(
            registration_submission__workbook_ingestion_job_id=ingestion_job_id,
            assignment_status=StagedResourcePublicationStatus.DETACH_REQUESTED,
        ).values_list("id", flat=True)
        finalized = 0
        for publication_id in publication_ids:
            result = self.finalize_detachment(publication_id=publication_id)
            if not result.is_pending:
                finalized += 1
        return finalized

    def finalize_detachment_for_dataset_registration_job(
        self,
        *,
        registration_job_id: UUID,
    ) -> int:
        """Finalize pending removals linked to one terminal CSV registration job.

        Args:
            registration_job_id: Immutable CSV registration job identifier.

        Returns:
            Number of detached publications finalized for the job.
        """
        publication_ids = StagedResourcePublication.objects.filter(
            registration_submission__dataset_registration_job_id=registration_job_id,
            assignment_status=StagedResourcePublicationStatus.DETACH_REQUESTED,
        ).values_list("id", flat=True)
        finalized = 0
        for publication_id in publication_ids:
            result = self.finalize_detachment(publication_id=publication_id)
            if not result.is_pending:
                finalized += 1
        return finalized

    @staticmethod
    def _get_locked_publication(*, publication_id: UUID) -> StagedResourcePublication:
        publication = (
            StagedResourcePublication.objects.select_for_update(of=("self",))
            .select_related(
                "registration_submission__dataset_registration_job",
                "registration_submission__workbook_ingestion_job",
            )
            .filter(pk=publication_id)
            .first()
        )
        if publication is None:
            raise UploadApplicationError("STAGED_PUBLICATION_NOT_FOUND", 404)
        return publication

    @staticmethod
    def _get_publication_job(
        *, publication: StagedResourcePublication
    ) -> WorkbookIngestionJob | DatasetRegistrationJob | None:
        submission = publication.registration_submission
        if submission is None:
            return None
        try:
            return submission.workbook_ingestion_job
        except WorkbookIngestionJob.DoesNotExist:
            try:
                return submission.dataset_registration_job
            except DatasetRegistrationJob.DoesNotExist:
                return None

    def _request_job_cancellation(
        self,
        *,
        job: WorkbookIngestionJob | DatasetRegistrationJob,
    ) -> None:
        """Request cancellation through the job family that owns the publication.

        Args:
            job: Active workbook ingestion or CSV registration job to cancel.
        """
        if isinstance(job, DatasetRegistrationJob):
            self._dataset_registration_job_service.request_cancellation(
                tenant_id=str(job.tenant_id),
                registration_job_id=str(job.id),
            )
            return
        self._workbook_job_service.request_cancellation(
            tenant_id=str(job.tenant_id),
            ingestion_job_id=str(job.id),
        )

    @staticmethod
    def _is_job_in_progress(*, job: WorkbookIngestionJob | DatasetRegistrationJob) -> bool:
        """Return whether a publication job must complete before detachment finalizes.

        Args:
            job: Workbook ingestion or CSV registration job associated with a publication.

        Returns:
            True while the job can still create Dataset outputs.
        """
        if isinstance(job, DatasetRegistrationJob):
            return job.status in {
                DatasetRegistrationJobStatus.SUBMITTED,
                DatasetRegistrationJobStatus.QUEUED,
                DatasetRegistrationJobStatus.RUNNING,
            }
        return job.status in {
            WorkbookIngestionJobStatus.SUBMITTED,
            WorkbookIngestionJobStatus.QUEUED,
            WorkbookIngestionJobStatus.RUNNING,
        }

    @staticmethod
    def _is_job_successful(*, job: WorkbookIngestionJob | DatasetRegistrationJob) -> bool:
        """Return whether a job has output evidence that detachment must reconcile.

        Args:
            job: Workbook ingestion or CSV registration job associated with a publication.

        Returns:
            True when the job completed with outputs that require exact assignment validation.
        """
        if isinstance(job, DatasetRegistrationJob):
            return job.status == DatasetRegistrationJobStatus.COMPLETED
        return job.status in {
            WorkbookIngestionJobStatus.COMPLETED,
            WorkbookIngestionJobStatus.COMPLETED_WITH_SKIPS,
        }

    @staticmethod
    def _validated_output_identifiers(*, result_payload: object) -> tuple[tuple[str, str], ...]:
        """Validate and extract exact Dataset-version identifiers from a registration result.

        Args:
            result_payload: Persisted normalized CSV or workbook result payload.

        Returns:
            Unique Dataset and DatasetVersion identifier pairs.

        Raises:
            UploadApplicationError: If the payload cannot prove every produced output.
        """
        if not isinstance(result_payload, dict):
            raise UploadApplicationError("PUBLICATION_OUTPUT_RECONCILIATION_REQUIRED", 409)
        if "dataset_id" in result_payload or "dataset_version_id" in result_payload:
            dataset_id = str(result_payload.get("dataset_id") or "").strip()
            version_id = str(result_payload.get("dataset_version_id") or "").strip()
            if not dataset_id or not version_id:
                raise UploadApplicationError("PUBLICATION_OUTPUT_RECONCILIATION_REQUIRED", 409)
            return ((dataset_id, version_id),)
        identifiers: list[tuple[str, str]] = []
        for result_key in ("created", "deduplicated"):
            items = result_payload.get(result_key)
            if not isinstance(items, list):
                raise UploadApplicationError("PUBLICATION_OUTPUT_RECONCILIATION_REQUIRED", 409)
            for item in items:
                if not isinstance(item, dict):
                    raise UploadApplicationError("PUBLICATION_OUTPUT_RECONCILIATION_REQUIRED", 409)
                dataset_id = str(item.get("dataset_id") or "").strip()
                version_id = str(item.get("dataset_version_id") or "").strip()
                if not dataset_id or not version_id:
                    raise UploadApplicationError("PUBLICATION_OUTPUT_RECONCILIATION_REQUIRED", 409)
                identifiers.append((dataset_id, version_id))
        return tuple(dict.fromkeys(identifiers))

    @staticmethod
    def _matches_publication_destination(
        *,
        publication: StagedResourcePublication,
        dataset: Dataset,
    ) -> bool:
        return bool(
            dataset.tenant_id == publication.registry_namespace_id
            and dataset.organization_id == publication.organization_id
            and dataset.workspace_id == publication.workspace_id
            and dataset.visibility_scope == publication.visibility_scope
        )

    @staticmethod
    def _archive_dataset(*, dataset_id: UUID, archived_at: datetime) -> None:
        Dataset.objects.filter(id=dataset_id, archived_at__isnull=True).update(
            status=DatasetStatus.ARCHIVED,
            archived_at=archived_at,
        )
        DatasetArtifact.objects.filter(
            dataset_version__dataset_id=dataset_id,
            lifecycle_status=ArtifactLifecycleStatus.ACTIVE,
        ).update(lifecycle_status=ArtifactLifecycleStatus.DEPRECATED)

    @staticmethod
    def _result(
        *,
        publication: StagedResourcePublication,
    ) -> DestinationAssignmentRemovalResult:
        return DestinationAssignmentRemovalResult(
            publication_id=publication.id,
            assignment_status=str(publication.assignment_status),
            is_pending=(
                publication.assignment_status == StagedResourcePublicationStatus.DETACH_REQUESTED
            ),
        )
