from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from resource_registry.events.audit_service import AuditService
from resource_registry.events.event_publisher import InMemoryEventPublisher
from resource_registry.models import ExternalDatasetJobStatus
from resource_registry.providers.exceptions import ProviderNotRegisteredError
from resource_registry.providers.registry import ProviderRegistry
from resource_registry.providers.services import ExternalDatasetProviderService
from resource_registry.providers.simulated_azure_provider import SimulatedAzureDatasetProvider
from resource_registry.providers.status import ProviderJobStatus
from resource_registry.storage.local import LocalStorageProvider


def test_provider_registry_register_lookup_and_list() -> None:
    registry = ProviderRegistry()
    provider = MagicMock(provider_name="simulated_azure")
    registry.register_provider(key="simulated_azure", provider=provider)

    assert registry.get_provider(key="simulated_azure") is provider
    assert registry.list_providers() == ["simulated_azure"]


def test_provider_registry_unknown_provider() -> None:
    registry = ProviderRegistry()
    with pytest.raises(ProviderNotRegisteredError):
        registry.get_provider(key="missing")


def test_simulated_provider_submit_status_advance_result_and_cancel(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    source_file = source_dir / "sample.csv"
    source_file.write_text("id,name\n1,Ada\n", encoding="utf-8")
    storage_provider = LocalStorageProvider(base_directory=tmp_path / "storage")
    provider = SimulatedAzureDatasetProvider(
        storage_provider=storage_provider,
        source_prefix=str(source_dir),
        target_prefix="target",
    )

    submission = provider.submit_job(
        tenant_id="tenant-a",
        dataset_id="dataset-1",
        request={"source_path": "sample.csv"},
    )
    pending = provider.get_job_status(provider_job_id=submission.provider_job_id)
    assert pending.status == ProviderJobStatus.PENDING

    running = provider.advance_job(provider_job_id=submission.provider_job_id)
    assert running.status == ProviderJobStatus.RUNNING

    succeeded = provider.advance_job(provider_job_id=submission.provider_job_id)
    assert succeeded.status == ProviderJobStatus.SUCCEEDED
    result = provider.get_job_result(provider_job_id=submission.provider_job_id)
    assert result.artifact_uri.startswith("local://")

    cancel_submission = provider.submit_job(
        tenant_id="tenant-a",
        dataset_id="dataset-2",
        request={"source_path": "sample.csv"},
    )
    cancelled = provider.cancel_job(provider_job_id=cancel_submission.provider_job_id)
    assert cancelled.status == ProviderJobStatus.CANCELLED


def test_external_provider_service_submit_sync_fetch_cancel(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    source_file = source_dir / "sample.csv"
    source_file.write_text("id,name\n1,Ada\n", encoding="utf-8")
    storage_provider = LocalStorageProvider(base_directory=tmp_path / "storage")
    provider = SimulatedAzureDatasetProvider(
        storage_provider=storage_provider,
        source_prefix=str(source_dir),
        target_prefix="target",
    )
    registry = ProviderRegistry()
    registry.register_provider(key="simulated_azure", provider=provider)
    publisher = InMemoryEventPublisher()
    audit_service = AuditService(publisher=publisher)
    service = ExternalDatasetProviderService(
        provider_registry=registry,
        audit_service=audit_service,
    )
    dataset = SimpleNamespace(id="dataset-1", tenant_id="tenant-a")
    job = SimpleNamespace(
        id="job-1",
        dataset=dataset,
        provider="simulated_azure",
        provider_job_ref=None,
        status=ExternalDatasetJobStatus.QUEUED,
        started_at=None,
        finished_at=None,
        save=MagicMock(),
    )
    with patch(
        "resource_registry.providers.services.ExternalDatasetJob.objects.create",
        return_value=job,
    ):
        created_job = service.submit_external_job(
            dataset=dataset,
            provider_key="simulated_azure",
            request_payload={"source_path": "sample.csv"},
        )
    assert created_job.provider_job_ref is not None
    job.save.assert_called()

    provider.advance_job(provider_job_id=job.provider_job_ref)
    status_running = service.sync_job_status(job=job)
    assert status_running.status == ProviderJobStatus.RUNNING
    assert job.status == ExternalDatasetJobStatus.RUNNING

    provider.advance_job(provider_job_id=job.provider_job_ref)
    status_succeeded = service.sync_job_status(job=job)
    assert status_succeeded.status == ProviderJobStatus.SUCCEEDED
    assert job.status == ExternalDatasetJobStatus.SUCCEEDED

    result = service.fetch_job_result(job=job)
    assert result.provider_job_id == job.provider_job_ref
    assert result.artifact_uri.startswith("local://")

    job_to_cancel = SimpleNamespace(
        id="job-2",
        dataset=dataset,
        provider="simulated_azure",
        provider_job_ref=provider.submit_job(
            tenant_id="tenant-a",
            dataset_id="dataset-1",
            request={"source_path": "sample.csv"},
        ).provider_job_id,
        status=ExternalDatasetJobStatus.QUEUED,
        started_at=None,
        finished_at=None,
        save=MagicMock(),
    )
    cancelled = service.cancel_job(job=job_to_cancel)
    assert cancelled.status == ProviderJobStatus.CANCELLED
    assert job_to_cancel.status == ExternalDatasetJobStatus.CANCELLED

    event_types = [event.event_type for event in publisher.list_events()]
    assert any(str(event_type) == "EXTERNAL_JOB_SUBMITTED" for event_type in event_types)
    assert any(str(event_type) == "EXTERNAL_JOB_RUNNING" for event_type in event_types)
    assert any(str(event_type) == "EXTERNAL_JOB_COMPLETED" for event_type in event_types)
    assert any(str(event_type) == "EXTERNAL_JOB_CANCELLED" for event_type in event_types)
