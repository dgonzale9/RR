from __future__ import annotations

from enum import StrEnum

from resource_registry.models import ExternalDatasetJobStatus


class ProviderJobStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


def provider_status_to_model_status(status: ProviderJobStatus | str) -> str:
    value = ProviderJobStatus(str(status).upper())
    mapping = {
        ProviderJobStatus.PENDING: ExternalDatasetJobStatus.QUEUED,
        ProviderJobStatus.RUNNING: ExternalDatasetJobStatus.RUNNING,
        ProviderJobStatus.SUCCEEDED: ExternalDatasetJobStatus.SUCCEEDED,
        ProviderJobStatus.FAILED: ExternalDatasetJobStatus.FAILED,
        ProviderJobStatus.CANCELLED: ExternalDatasetJobStatus.CANCELLED,
    }
    return mapping[value]
