from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from uuid import uuid4

from resource_registry.providers.exceptions import (
    InvalidProviderJobStateError,
    ProviderJobNotFoundError,
)
from resource_registry.providers.models import (
    ProviderJobResult,
    ProviderJobStatusResult,
    ProviderJobSubmissionResult,
)
from resource_registry.providers.status import ProviderJobStatus
from resource_registry.storage.base import StorageObjectMetadata, StorageProvider


@dataclass
class _SimulatedProviderJobState:
    provider_job_id: str
    tenant_id: str
    dataset_id: str
    source_relative_path: str
    target_relative_path: str
    submitted_at: datetime
    status: ProviderJobStatus = ProviderJobStatus.PENDING
    progress: int = 0
    result: ProviderJobResult | None = None


class SimulatedAzureDatasetProvider:
    """Simulated external dataset provider using storage abstraction only."""

    provider_name = "simulated_azure"

    def __init__(
        self,
        *,
        storage_provider: StorageProvider,
        source_prefix: str,
        target_prefix: str,
    ) -> None:
        self._storage_provider = storage_provider
        self._source_prefix = Path(source_prefix)
        self._target_prefix = target_prefix.strip("/")
        self._jobs: dict[str, _SimulatedProviderJobState] = {}

    def submit_job(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        request: dict,
    ) -> ProviderJobSubmissionResult:
        source_relative_path = str(request.get("source_path", "")).strip().lstrip("/")
        if not source_relative_path:
            raise ValueError("source_path is required in provider request.")
        target_name = str(request.get("target_name") or Path(source_relative_path).name)
        target_relative_path = self._build_target_path(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            target_name=target_name,
        )
        provider_job_id = f"sim-az-{uuid4()}"
        submitted_at = datetime.now(UTC)
        self._jobs[provider_job_id] = _SimulatedProviderJobState(
            provider_job_id=provider_job_id,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            source_relative_path=source_relative_path,
            target_relative_path=target_relative_path,
            submitted_at=submitted_at,
        )
        return ProviderJobSubmissionResult(
            provider_job_id=provider_job_id,
            provider_name=self.provider_name,
            submitted_at=submitted_at,
            metadata={
                "source_path": source_relative_path,
                "target_path": target_relative_path,
            },
        )

    def get_job_status(self, *, provider_job_id: str) -> ProviderJobStatusResult:
        job_state = self._get_job_state(provider_job_id=provider_job_id)
        return ProviderJobStatusResult(
            provider_job_id=job_state.provider_job_id,
            provider_name=self.provider_name,
            status=job_state.status,
            progress=job_state.progress,
            metadata={"target_path": job_state.target_relative_path},
        )

    def advance_job(self, *, provider_job_id: str) -> ProviderJobStatusResult:
        job_state = self._get_job_state(provider_job_id=provider_job_id)
        if job_state.status == ProviderJobStatus.PENDING:
            job_state.status = ProviderJobStatus.RUNNING
            job_state.progress = 50
            return self.get_job_status(provider_job_id=provider_job_id)
        if job_state.status == ProviderJobStatus.RUNNING:
            source_local_path = self._build_source_local_path(
                source_relative_path=job_state.source_relative_path,
            )
            metadata = self._storage_provider.put(
                source_local_path,
                job_state.target_relative_path,
            )
            job_state.status = ProviderJobStatus.SUCCEEDED
            job_state.progress = 100
            job_state.result = self._build_result(job_state=job_state, metadata=metadata)
            return self.get_job_status(provider_job_id=provider_job_id)
        raise InvalidProviderJobStateError(
            f"Cannot advance job {provider_job_id} in state {job_state.status}.",
        )

    def get_job_result(self, *, provider_job_id: str) -> ProviderJobResult:
        job_state = self._get_job_state(provider_job_id=provider_job_id)
        if job_state.status != ProviderJobStatus.SUCCEEDED or job_state.result is None:
            raise InvalidProviderJobStateError(
                f"Job {provider_job_id} is not completed.",
            )
        return job_state.result

    def cancel_job(self, *, provider_job_id: str) -> ProviderJobStatusResult:
        job_state = self._get_job_state(provider_job_id=provider_job_id)
        if job_state.status in {ProviderJobStatus.SUCCEEDED, ProviderJobStatus.FAILED}:
            raise InvalidProviderJobStateError(
                f"Cannot cancel terminal job {provider_job_id}.",
            )
        job_state.status = ProviderJobStatus.CANCELLED
        job_state.progress = 0
        return self.get_job_status(provider_job_id=provider_job_id)

    def _get_job_state(self, *, provider_job_id: str) -> _SimulatedProviderJobState:
        try:
            return self._jobs[provider_job_id]
        except KeyError as error:
            raise ProviderJobNotFoundError(
                f"Provider job '{provider_job_id}' not found.",
            ) from error

    def _build_target_path(self, *, tenant_id: str, dataset_id: str, target_name: str) -> str:
        safe_name = PurePosixPath(str(target_name).replace("\\", "/")).name
        return (
            PurePosixPath(self._target_prefix)
            / str(tenant_id)
            / str(dataset_id)
            / safe_name
        ).as_posix()

    def _build_source_local_path(self, *, source_relative_path: str) -> str:
        return str(self._source_prefix / source_relative_path)

    def _build_result(
        self,
        *,
        job_state: _SimulatedProviderJobState,
        metadata: StorageObjectMetadata,
    ) -> ProviderJobResult:
        return ProviderJobResult(
            provider_job_id=job_state.provider_job_id,
            provider_name=self.provider_name,
            artifact_uri=metadata.uri,
            artifact_metadata={
                "provider": metadata.provider,
                "size_bytes": metadata.size_bytes,
                "checksum": metadata.checksum,
                "etag": metadata.etag,
                "content_type": metadata.content_type,
            },
            completed_at=datetime.now(UTC),
        )
