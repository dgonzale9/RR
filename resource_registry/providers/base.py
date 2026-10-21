from __future__ import annotations

from typing import Protocol

from resource_registry.providers.models import (
    ProviderJobResult,
    ProviderJobStatusResult,
    ProviderJobSubmissionResult,
)


class ExternalDatasetProvider(Protocol):
    provider_name: str

    def submit_job(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        request: dict,
    ) -> ProviderJobSubmissionResult:
        """Submit a provider job and return submission metadata."""

    def get_job_status(self, *, provider_job_id: str) -> ProviderJobStatusResult:
        """Return current provider job status."""

    def get_job_result(self, *, provider_job_id: str) -> ProviderJobResult:
        """Return provider artifact result metadata for a completed job."""

    def cancel_job(self, *, provider_job_id: str) -> ProviderJobStatusResult:
        """Cancel provider job and return updated status."""
