"""Persist format-specific jobs without executing registration work."""

from __future__ import annotations

import hashlib

from django.contrib.auth.models import User

from resource_registry.models import (
    DatasetRegistrationJob,
    DatasetRegistrationJobStatus,
    LargeFileUploadSession,
    RegistrationSubmission,
    WorkbookIngestionJob,
    WorkbookIngestionJobStatus,
)
from resource_registry.registration_submissions.contracts import (
    RegistrationJobKind,
    RegistrationJobReference,
)


class RegistrationSubmissionJobFactory:
    """Create exactly one non-executing job selected by the format resolver."""

    def create_job(
        self,
        *,
        job_kind: RegistrationJobKind,
        submission: RegistrationSubmission,
        upload: LargeFileUploadSession,
        actor: User,
    ) -> RegistrationJobReference:
        """Persist one linked job without parsing or executing the uploaded file.

        Args:
            job_kind: Trusted job family selected from the persisted upload format.
            submission: Persisted authorized registration request.
            upload: Locked completed upload that owns storage provenance.
            actor: Current authenticated submitting user.

        Returns:
            Reference to the newly persisted format-specific job.
        """
        if job_kind == RegistrationJobKind.WORKBOOK_INGESTION:
            return self._create_workbook_job(submission, upload, actor)
        return self._create_dataset_job(submission, upload, actor)

    @staticmethod
    def _create_workbook_job(
        submission: RegistrationSubmission,
        upload: LargeFileUploadSession,
        actor: User,
    ) -> RegistrationJobReference:
        payload = {"upload_id": str(upload.id)}
        fingerprint = hashlib.sha256(str(upload.id).encode("ascii")).hexdigest()
        job = WorkbookIngestionJob.objects.create(
            registration_submission=submission,
            tenant_id=upload.registry_namespace_id,
            status=WorkbookIngestionJobStatus.SUBMITTED,
            request_payload_json=payload,
            idempotency_key=f"registration:{submission.id}",
            request_fingerprint=fingerprint,
            created_by=actor,
            metadata_json={"registration_submission_id": str(submission.id)},
        )
        return RegistrationJobReference(
            job_id=job.id,
            job_type=RegistrationJobKind.WORKBOOK_INGESTION,
        )

    @staticmethod
    def _create_dataset_job(
        submission: RegistrationSubmission,
        upload: LargeFileUploadSession,
        actor: User,
    ) -> RegistrationJobReference:
        job = DatasetRegistrationJob.objects.create(
            registration_submission=submission,
            tenant_id=upload.registry_namespace_id,
            status=DatasetRegistrationJobStatus.SUBMITTED,
            request_payload_json={"upload_id": str(upload.id)},
            created_by=actor,
        )
        return RegistrationJobReference(
            job_id=job.id,
            job_type=RegistrationJobKind.DATASET_REGISTRATION,
        )

    @staticmethod
    def get_job_reference(
        *,
        submission: RegistrationSubmission,
        job_kind: RegistrationJobKind,
    ) -> RegistrationJobReference:
        """Resolve the exact persisted job linked to an idempotent submission.

        Args:
            submission: Existing registration submission.
            job_kind: Persisted trusted job family.

        Returns:
            Reference to the linked job.

        Raises:
            RuntimeError: If persistence linkage is unexpectedly incomplete.
        """
        if job_kind == RegistrationJobKind.WORKBOOK_INGESTION:
            job_id = (
                WorkbookIngestionJob.objects.filter(registration_submission=submission)
                .values_list("id", flat=True)
                .first()
            )
        else:
            job_id = (
                DatasetRegistrationJob.objects.filter(registration_submission=submission)
                .values_list("id", flat=True)
                .first()
            )
        if job_id is None:
            raise RuntimeError("Registration submission job linkage is incomplete.")
        return RegistrationJobReference(job_id=job_id, job_type=job_kind)
