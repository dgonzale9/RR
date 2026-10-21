from __future__ import annotations

from resource_registry.dataset_visibility import build_dataset_visibility_filter
from resource_registry.models import ExternalDatasetJob
from resource_registry.read.dtos import ExternalDatasetJobSummary, to_iso
from resource_registry.read.exceptions import DataRegistryReadError


class ExternalDatasetJobReadService:
    """Tenant-scoped read service for external dataset jobs."""

    MAX_LIMIT = 100

    def list_jobs(  # noqa: PLR0913
        self,
        *,
        tenant_id: str,
        dataset_id: str | None = None,
        status: str | None = None,
        provider: str | None = None,
        job_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ExternalDatasetJobSummary]:
        queryset = ExternalDatasetJob.objects.filter(
            build_dataset_visibility_filter(tenant_id=tenant_id, field_prefix="dataset__"),
        )
        if dataset_id:
            queryset = queryset.filter(dataset_id=dataset_id)
        if status:
            queryset = queryset.filter(status=status)
        if provider:
            queryset = queryset.filter(provider=provider)
        if job_type:
            queryset = queryset.filter(job_type=job_type)
        ordered = queryset.order_by("-requested_at", "-id")
        jobs = self._paginate(list(ordered), limit=limit, offset=offset)
        return [self._to_summary(job) for job in jobs]

    def get_job(self, *, tenant_id: str, job_id: str) -> ExternalDatasetJobSummary:
        job = ExternalDatasetJob.objects.filter(
            build_dataset_visibility_filter(tenant_id=tenant_id, field_prefix="dataset__"),
            id=job_id,
        ).first()
        if job is None:
            raise DataRegistryReadError("External dataset job not found for tenant.")
        return self._to_summary(job)

    def _paginate(
        self,
        items: list[ExternalDatasetJob],
        *,
        limit: int,
        offset: int,
    ) -> list[ExternalDatasetJob]:
        safe_limit = min(max(limit, 1), self.MAX_LIMIT)
        safe_offset = max(offset, 0)
        return items[safe_offset : safe_offset + safe_limit]

    def _to_summary(self, job: ExternalDatasetJob) -> ExternalDatasetJobSummary:
        return ExternalDatasetJobSummary(
            id=str(job.id),
            dataset_id=str(job.dataset_id),
            tenant_id=str(job.dataset.tenant_id),
            job_type=job.job_type,
            status=job.status,
            provider=job.provider,
            provider_job_ref=job.provider_job_ref,
            retry_count=int(job.retry_count),
            error_code=job.error_code,
            requested_at=str(to_iso(job.requested_at)),
            started_at=to_iso(job.started_at),
            finished_at=to_iso(job.finished_at),
            updated_at=str(to_iso(job.updated_at)),
            output_dataset_version_id=(
                str(job.output_dataset_version_id) if job.output_dataset_version_id else None
            ),
        )
