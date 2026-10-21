from __future__ import annotations

from django.utils import timezone

from resource_registry.events.audit_service import AuditService
from resource_registry.events.event_types import EventType
from resource_registry.models import (
    Dataset,
    DatasetVersion,
    ExternalDatasetJob,
    ExternalDatasetJobStatus,
)


class InvalidJobTransitionError(ValueError):
    """Raised when a job state transition is invalid."""


class JobService:
    """Orchestrates ExternalDatasetJob lifecycle transitions."""

    def __init__(self, audit_service: AuditService | None = None) -> None:
        self._audit_service = audit_service or AuditService()

    def create_job(
        self,
        *,
        dataset: Dataset,
        job_type: str,
        provider: str | None = None,
        provider_job_ref: str | None = None,
        request_payload_json: dict | None = None,
    ) -> ExternalDatasetJob:
        job = ExternalDatasetJob.objects.create(
            dataset=dataset,
            job_type=job_type,
            status=ExternalDatasetJobStatus.QUEUED,
            provider=provider,
            provider_job_ref=provider_job_ref,
            request_payload_json=request_payload_json or {},
        )
        self._safe_audit(
            event_type=EventType.JOB_CREATED,
            job=job,
            metadata={"job_type": job_type},
        )
        return job

    def start_job(self, *, job: ExternalDatasetJob) -> ExternalDatasetJob:
        self._require_status(job=job, expected=ExternalDatasetJobStatus.QUEUED, action="start")
        job.status = ExternalDatasetJobStatus.RUNNING
        job.started_at = timezone.now()
        job.save(update_fields=["status", "started_at", "updated_at"])
        self._safe_audit(event_type=EventType.JOB_STARTED, job=job, metadata={})
        return job

    def complete_job(
        self,
        *,
        job: ExternalDatasetJob,
        output_dataset_version: DatasetVersion | None = None,
    ) -> ExternalDatasetJob:
        self._require_status(job=job, expected=ExternalDatasetJobStatus.RUNNING, action="complete")
        job.status = ExternalDatasetJobStatus.SUCCEEDED
        job.finished_at = timezone.now()
        job.output_dataset_version = output_dataset_version
        job.save(update_fields=["status", "finished_at", "output_dataset_version", "updated_at"])
        self._safe_audit(
            event_type=EventType.JOB_COMPLETED,
            job=job,
            metadata={
                "output_dataset_version_id": (
                    str(getattr(output_dataset_version, "id", None))
                    if output_dataset_version
                    else None
                ),
            },
        )
        return job

    def fail_job(
        self,
        *,
        job: ExternalDatasetJob,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> ExternalDatasetJob:
        self._require_status(job=job, expected=ExternalDatasetJobStatus.RUNNING, action="fail")
        job.status = ExternalDatasetJobStatus.FAILED
        job.error_code = error_code
        job.error_message = error_message
        job.finished_at = timezone.now()
        job.save(
            update_fields=["status", "error_code", "error_message", "finished_at", "updated_at"],
        )
        self._safe_audit(
            event_type=EventType.JOB_FAILED,
            job=job,
            metadata={
                "error_code": error_code,
            },
        )
        return job

    def retry_job(self, *, job: ExternalDatasetJob) -> ExternalDatasetJob:
        self._require_status(job=job, expected=ExternalDatasetJobStatus.FAILED, action="retry")
        job.status = ExternalDatasetJobStatus.QUEUED
        job.retry_count += 1
        job.started_at = None
        job.finished_at = None
        job.save(update_fields=["status", "retry_count", "started_at", "finished_at", "updated_at"])
        return job

    def _require_status(self, *, job: ExternalDatasetJob, expected: str, action: str) -> None:
        if job.status != expected:
            raise InvalidJobTransitionError(
                f"Cannot {action} job in status {job.status}. Expected {expected}.",
            )

    def _safe_audit(
        self,
        *,
        event_type: EventType,
        job: ExternalDatasetJob,
        metadata: dict[str, object],
    ) -> None:
        try:
            tenant_id = (
                str(job.dataset.tenant_id)
                if getattr(job.dataset, "tenant_id", None)
                else None
            )
            self._audit_service.record_job_transition(
                event_type=event_type,
                tenant_id=tenant_id,
                dataset_id=str(job.dataset.id),
                dataset_version_id=(
                    str(job.output_dataset_version.id)
                    if getattr(job, "output_dataset_version", None)
                    else None
                ),
                metadata=metadata,
            )
        except Exception:
            return
