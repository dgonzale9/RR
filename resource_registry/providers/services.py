from __future__ import annotations

from typing import Any

from django.utils import timezone

from resource_registry.events.audit_service import AuditService
from resource_registry.models import Dataset, ExternalDatasetJob
from resource_registry.providers.models import (
    ProviderJobResult,
    ProviderJobStatusResult,
)
from resource_registry.providers.registry import ProviderRegistry
from resource_registry.providers.status import ProviderJobStatus, provider_status_to_model_status


class ExternalDatasetProviderService:
    """Application service orchestrating external job provider interactions."""

    def __init__(
        self,
        *,
        provider_registry: ProviderRegistry,
        audit_service: AuditService | None = None,
    ) -> None:
        self._provider_registry = provider_registry
        self._audit_service = audit_service or AuditService()

    def submit_external_job(
        self,
        *,
        dataset: Dataset,
        provider_key: str,
        request_payload: dict[str, Any],
    ) -> ExternalDatasetJob:
        provider = self._provider_registry.get_provider(key=provider_key)
        created_job = ExternalDatasetJob.objects.create(
            dataset=dataset,
            job_type="INGEST",
            status=provider_status_to_model_status(ProviderJobStatus.PENDING),
            provider=provider_key,
            request_payload_json=request_payload,
        )
        submission_result = provider.submit_job(
            tenant_id=str(dataset.tenant_id),
            dataset_id=str(dataset.id),
            request=request_payload,
        )
        created_job.provider_job_ref = submission_result.provider_job_id
        created_job.save(update_fields=["provider_job_ref", "updated_at"])
        self._safe_audit(
            lambda: self._audit_service.record_external_job_submitted(
                tenant_id=str(dataset.tenant_id),
                dataset_id=str(dataset.id),
                metadata={
                    "job_id": str(created_job.id),
                    "provider": provider_key,
                    "provider_job_id": submission_result.provider_job_id,
                },
            ),
        )
        return created_job

    def sync_job_status(self, *, job: ExternalDatasetJob) -> ProviderJobStatusResult:
        provider = self._provider_registry.get_provider(key=str(job.provider))
        status_result = provider.get_job_status(provider_job_id=str(job.provider_job_ref))
        job.status = provider_status_to_model_status(status_result.status)
        if status_result.status == ProviderJobStatus.RUNNING:
            if job.started_at is None:
                job.started_at = timezone.now()
            self._safe_audit(
                lambda: self._audit_service.record_external_job_running(
                    tenant_id=str(job.dataset.tenant_id),
                    dataset_id=str(job.dataset.id),
                    metadata={
                        "job_id": str(job.id),
                        "provider_job_id": status_result.provider_job_id,
                        "progress": status_result.progress,
                    },
                ),
            )
        if status_result.status == ProviderJobStatus.SUCCEEDED:
            job.finished_at = timezone.now()
            self._safe_audit(
                lambda: self._audit_service.record_external_job_completed(
                    tenant_id=str(job.dataset.tenant_id),
                    dataset_id=str(job.dataset.id),
                    metadata={
                        "job_id": str(job.id),
                        "provider_job_id": status_result.provider_job_id,
                    },
                ),
            )
        if status_result.status == ProviderJobStatus.FAILED:
            job.finished_at = timezone.now()
            self._safe_audit(
                lambda: self._audit_service.record_external_job_failed(
                    tenant_id=str(job.dataset.tenant_id),
                    dataset_id=str(job.dataset.id),
                    metadata={
                        "job_id": str(job.id),
                        "provider_job_id": status_result.provider_job_id,
                    },
                ),
            )
        if status_result.status == ProviderJobStatus.CANCELLED:
            job.finished_at = timezone.now()
            self._safe_audit(
                lambda: self._audit_service.record_external_job_cancelled(
                    tenant_id=str(job.dataset.tenant_id),
                    dataset_id=str(job.dataset.id),
                    metadata={
                        "job_id": str(job.id),
                        "provider_job_id": status_result.provider_job_id,
                    },
                ),
            )
        job.save(update_fields=["status", "started_at", "finished_at", "updated_at"])
        self._safe_audit(
            lambda: self._audit_service.record_external_job_synced(
                tenant_id=str(job.dataset.tenant_id),
                dataset_id=str(job.dataset.id),
                metadata={
                    "job_id": str(job.id),
                    "provider_job_id": status_result.provider_job_id,
                    "status": status_result.status,
                    "progress": status_result.progress,
                },
            ),
        )
        return status_result

    def fetch_job_result(self, *, job: ExternalDatasetJob) -> ProviderJobResult:
        provider = self._provider_registry.get_provider(key=str(job.provider))
        return provider.get_job_result(provider_job_id=str(job.provider_job_ref))

    def cancel_job(self, *, job: ExternalDatasetJob) -> ProviderJobStatusResult:
        provider = self._provider_registry.get_provider(key=str(job.provider))
        status_result = provider.cancel_job(provider_job_id=str(job.provider_job_ref))
        job.status = provider_status_to_model_status(status_result.status)
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "finished_at", "updated_at"])
        self._safe_audit(
            lambda: self._audit_service.record_external_job_cancelled(
                tenant_id=str(job.dataset.tenant_id),
                dataset_id=str(job.dataset.id),
                metadata={
                    "job_id": str(job.id),
                    "provider_job_id": status_result.provider_job_id,
                },
            ),
        )
        return status_result

    def _safe_audit(self, audit_call) -> None:
        try:
            audit_call()
        except Exception:
            return
