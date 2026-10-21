from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from resource_registry.providers.status import ProviderJobStatus


@dataclass(frozen=True)
class ProviderJobSubmissionResult:
    provider_job_id: str
    provider_name: str
    submitted_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderJobStatusResult:
    provider_job_id: str
    provider_name: str
    status: ProviderJobStatus
    progress: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderJobResult:
    provider_job_id: str
    provider_name: str
    artifact_uri: str
    artifact_metadata: dict[str, Any]
    completed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
