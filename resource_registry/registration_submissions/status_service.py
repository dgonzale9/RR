"""Scoped persistence reads for asynchronous registration status."""

from __future__ import annotations

from typing import cast
from uuid import UUID

from resource_registry.models import (
    DatasetRegistrationJob,
    RegistrationSubmission,
    WorkbookIngestionJob,
)
from resource_registry.registration_submissions.contracts import (
    RegistrationJobKind,
    RegistrationJobStatusSnapshot,
)


class RegistrationStatusNotFoundError(LookupError):
    """Hide missing and foreign registration identifiers behind one failure."""


class RegistrationSubmissionStatusReadService:
    """Resolve one canonically scoped submission and its linked job."""

    def get_status(
        self,
        *,
        organization_id: int,
        workspace_id: int | None,
        namespace_id: UUID,
        registration_id: UUID,
    ) -> RegistrationJobStatusSnapshot:
        """Return a persistence-neutral status snapshot for one authorized scope.

        Args:
            organization_id: Canonical Organization identifier.
            workspace_id: Canonical Workspace identifier, if the namespace is Workspace-owned.
            namespace_id: Canonical Registry Namespace identifier.
            registration_id: Opaque registration submission identifier.

        Returns:
            Immutable linked-job status and sanitized durable result payload.

        Raises:
            RegistrationStatusNotFoundError: If the submission or linked job is unavailable.
        """
        submission = (
            RegistrationSubmission.objects.filter(
                id=registration_id,
                organization_id=organization_id,
                workspace_id=workspace_id,
                registry_namespace_id=namespace_id,
            )
            .select_related("dataset_registration_job", "workbook_ingestion_job")
            .first()
        )
        if submission is None:
            raise RegistrationStatusNotFoundError
        try:
            job_type = RegistrationJobKind(submission.job_type)
        except ValueError as error:
            raise RegistrationStatusNotFoundError from error
        job = self._get_linked_job(submission=submission, job_type=job_type)
        return RegistrationJobStatusSnapshot(
            registration_id=submission.id,
            job_type=job_type,
            status=str(job.status),
            result_payload=dict(job.result_payload_json or {}),
            error_code=str(job.error_code) if job.error_code else None,
        )

    @staticmethod
    def _get_linked_job(
        *,
        submission: RegistrationSubmission,
        job_type: RegistrationJobKind,
    ) -> DatasetRegistrationJob | WorkbookIngestionJob:
        job: DatasetRegistrationJob | WorkbookIngestionJob | None
        if job_type == RegistrationJobKind.DATASET_REGISTRATION:
            job = cast(
                DatasetRegistrationJob | None,
                getattr(submission, "dataset_registration_job", None),
            )
        else:
            job = cast(
                WorkbookIngestionJob | None,
                getattr(submission, "workbook_ingestion_job", None),
            )
        if job is None:
            raise RegistrationStatusNotFoundError
        return job
