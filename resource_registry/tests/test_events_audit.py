from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from resource_registry.application.commands import RegisterDatasetCommand
from resource_registry.application.dataset_registration import DatasetRegistrationFacade
from resource_registry.application.idempotency import IdempotencyResolution
from resource_registry.application.storage_strategy import DatasetStorageMode, DatasetStoragePlan
from resource_registry.events.audit_service import AuditService
from resource_registry.events.event_models import DomainEvent
from resource_registry.events.event_publisher import InMemoryEventPublisher
from resource_registry.events.event_types import EventType
from resource_registry.security.policies import ArtifactMaterializationPolicy
from resource_registry.services.registry.dataset_registry_service import (
    VersionContextPersistenceResult,
)
from resource_registry.services.registry.job_service import JobService
from resource_registry.storage.base import StorageObjectMetadata


@pytest.fixture(autouse=True)
def allow_local_materialization_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    def _resolve_policy(_self, *, tenant_id: str, dataset_id: str | None = None):
        _ = tenant_id, dataset_id
        return ArtifactMaterializationPolicy(allow_local_files=True)

    def _resolve_with_provenance(_self, *, tenant_id: str, dataset_id: str | None = None):
        _ = tenant_id, dataset_id
        return SimpleNamespace(
            policy=ArtifactMaterializationPolicy(allow_local_files=True),
            provenance=SimpleNamespace(
                policy_key="artifact_materialization_default",
                source="SYSTEM_DEFAULT",
                revision=None,
                rules_hash="hash",
                resolution_path=["tenant_default", "system_default"],
            ),
        )

    monkeypatch.setattr(
        "resource_registry.application.dataset_registration."
        "ArtifactMaterializationPolicyResolver.resolve_policy",
        _resolve_policy,
    )
    monkeypatch.setattr(
        "resource_registry.application.dataset_registration."
        "ArtifactMaterializationPolicyResolver.resolve_with_provenance",
        _resolve_with_provenance,
    )


def test_domain_event_creation() -> None:
    event = DomainEvent(
        event_type=EventType.DATASET_REGISTERED,
        tenant_id="tenant-a",
        dataset_id="dataset-1",
        metadata={"slug": "sales-data"},
    )
    assert event.event_id
    assert event.occurred_at
    assert event.event_type == EventType.DATASET_REGISTERED


def test_event_publisher_publish_retrieve_and_list() -> None:
    publisher = InMemoryEventPublisher()
    first = DomainEvent(event_type=EventType.JOB_CREATED, tenant_id="tenant-a")
    second = DomainEvent(event_type=EventType.JOB_FAILED, tenant_id="tenant-b")

    publisher.publish(first)
    publisher.publish(second)

    assert publisher.retrieve(first.event_id) == first
    assert publisher.list_events(event_type=EventType.JOB_FAILED) == [second]
    assert publisher.list_events(tenant_id="tenant-a") == [first]


def test_event_publisher_is_append_only() -> None:
    publisher = InMemoryEventPublisher()
    event = DomainEvent(event_type=EventType.JOB_CREATED, tenant_id="tenant-a")
    publisher.record(event)

    listed_events = publisher.list_events()
    listed_events.clear()

    assert len(publisher.list_events()) == 1


def test_audit_service_sanitizes_preview_and_raw_content() -> None:
    publisher = InMemoryEventPublisher()
    audit_service = AuditService(publisher=publisher)

    audit_service.record_dataset_registered(
        tenant_id="tenant-a",
        dataset_id="dataset-1",
        dataset_version_id="version-1",
        metadata={
            "slug": "sales-data",
            "preview": [{"id": 1}],
            "nested": {"raw_content": "secret", "column_count": 3},
            "password": "never",
        },
    )

    event = publisher.list_events()[0]
    assert "preview" not in event.metadata
    assert "password" not in event.metadata
    assert event.metadata["nested"] == {"column_count": 3}


def test_registration_emits_dataset_registered_and_version_created_events() -> None:
    publisher = InMemoryEventPublisher()
    audit_service = AuditService(publisher=publisher)
    registry_service = MagicMock()
    dataset = SimpleNamespace(
        id="dataset-1",
        versions=SimpleNamespace(
            order_by=lambda *_args, **_kwargs: SimpleNamespace(first=lambda: None),
        ),
    )
    persistence_result = VersionContextPersistenceResult(
        dataset_version=SimpleNamespace(id="version-1"),
        dataset_schema=SimpleNamespace(id="schema-1"),
        dataset_profile=SimpleNamespace(id="profile-1"),
    )
    registry_service.create_dataset.return_value = dataset
    registry_service.create_version_bundle_from_context.return_value = persistence_result
    registry_service.attach_artifact.return_value = SimpleNamespace(id="artifact-1")

    context_builder = MagicMock()
    context_builder.build_context.return_value = {
        "source": {
            "path": "/tmp/sample.csv",
            "format": "csv",
            "file_name": "sample.csv",
            "file_size_bytes": 10,
        },
        "summary": {"row_count": 3, "column_count": 2},
        "schema": [{"name": "id", "type": "BIGINT"}],
        "preview": [{"id": 1}],
        "safety": {"preview_limit": 10, "preview_limited": True, "raw_content_included": False},
    }
    idempotency_service = MagicMock()
    idempotency_service.resolve.return_value = IdempotencyResolution(
        dataset=None,
        existing_version=None,
        existing_schema=None,
        existing_profile=None,
        existing_artifact=None,
        content_hash="hash-1",
    )
    idempotency_service.get_next_version_number.return_value = 1
    validator = MagicMock()

    facade = DatasetRegistrationFacade(
        context_builder=context_builder,
        registry_service=registry_service,
        validator=validator,
        idempotency_service=idempotency_service,
        audit_service=audit_service,
    )
    command = RegisterDatasetCommand(
        tenant_id="tenant-a",
        slug="sales-data",
        name="Sales Data",
        path="/tmp/sample.csv",
        source_type="FILE_UPLOAD",
    )

    facade.register(command)

    event_types = [event.event_type for event in publisher.list_events()]
    assert EventType.ARTIFACT_MATERIALIZATION_POLICY_RESOLVED in event_types
    assert EventType.DATASET_REGISTERED in event_types
    assert EventType.DATASET_VERSION_CREATED in event_types
    assert EventType.DATASET_ARTIFACT_REGISTERED in event_types


def test_duplicate_registration_emits_dedup_event() -> None:
    publisher = InMemoryEventPublisher()
    audit_service = AuditService(publisher=publisher)
    registry_service = MagicMock()
    existing_dataset = SimpleNamespace(id="dataset-1")
    existing_version = SimpleNamespace(id="version-1")
    existing_schema = SimpleNamespace(id="schema-1")
    existing_profile = SimpleNamespace(id="profile-1")
    existing_artifact = SimpleNamespace(id="artifact-1")
    context_builder = MagicMock()
    context_builder.build_context.return_value = {
        "source": {
            "path": "/tmp/sample.csv",
            "format": "csv",
            "file_name": "sample.csv",
            "file_size_bytes": 10,
        },
        "summary": {"row_count": 3, "column_count": 2},
        "schema": [{"name": "id", "type": "BIGINT"}],
        "preview": [{"id": 1}],
        "safety": {"preview_limit": 10, "preview_limited": True, "raw_content_included": False},
    }
    idempotency_service = MagicMock()
    idempotency_service.resolve.return_value = IdempotencyResolution(
        dataset=existing_dataset,
        existing_version=existing_version,
        existing_schema=existing_schema,
        existing_profile=existing_profile,
        existing_artifact=existing_artifact,
        content_hash="hash-1",
    )
    validator = MagicMock()
    facade = DatasetRegistrationFacade(
        context_builder=context_builder,
        registry_service=registry_service,
        validator=validator,
        idempotency_service=idempotency_service,
        audit_service=audit_service,
    )
    command = RegisterDatasetCommand(
        tenant_id="tenant-a",
        slug="sales-data",
        name="Sales Data",
        path="/tmp/sample.csv",
        source_type="FILE_UPLOAD",
    )

    facade.register(command)

    event_types = [event.event_type for event in publisher.list_events()]
    assert EventType.DATASET_DEDUPLICATED in event_types
    assert EventType.DATASET_ARTIFACT_UPLOADED not in event_types


def test_managed_upload_registration_emits_artifact_uploaded_event() -> None:
    publisher = InMemoryEventPublisher()
    audit_service = AuditService(publisher=publisher)
    registry_service = MagicMock()
    dataset = SimpleNamespace(
        id="dataset-1",
        versions=SimpleNamespace(
            order_by=lambda *_args, **_kwargs: SimpleNamespace(first=lambda: None),
        ),
    )
    persistence_result = VersionContextPersistenceResult(
        dataset_version=SimpleNamespace(id="version-1"),
        dataset_schema=SimpleNamespace(id="schema-1"),
        dataset_profile=SimpleNamespace(id="profile-1"),
    )
    registry_service.create_dataset.return_value = dataset
    registry_service.create_version_bundle_from_context.return_value = persistence_result
    artifact_lifecycle_service = MagicMock()
    artifact_lifecycle_service.register_uploaded_artifact.return_value = SimpleNamespace(
        id="artifact-1",
        storage_provider="azure_blob",
        artifact_role="PRIMARY",
    )
    context_builder = MagicMock()
    context_builder.build_context.return_value = {
        "source": {
            "path": "/tmp/sample.csv",
            "format": "csv",
            "file_name": "sample.csv",
            "file_size_bytes": 10,
        },
        "summary": {"row_count": 3, "column_count": 2},
        "schema": [{"name": "id", "type": "BIGINT"}],
        "preview": [{"id": 1}],
        "safety": {"preview_limit": 10, "preview_limited": True, "raw_content_included": False},
    }
    idempotency_service = MagicMock()
    idempotency_service.resolve.return_value = IdempotencyResolution(
        dataset=None,
        existing_version=None,
        existing_schema=None,
        existing_profile=None,
        existing_artifact=None,
        content_hash="hash-1",
    )
    idempotency_service.get_next_version_number.return_value = 1
    validator = MagicMock()
    provider = MagicMock()
    provider.put.return_value = StorageObjectMetadata(
        uri="azure://media/datasets/tenant/sales-data/v1/original.csv",
        provider="azure_blob",
        size_bytes=10,
    )
    storage_strategy = MagicMock()
    storage_strategy.create_plan.return_value = DatasetStoragePlan(
        mode=DatasetStorageMode.MANAGED_UPLOAD,
        should_upload=True,
        provider=provider,
        provider_key="azure_blob",
        destination_path="datasets/tenant/sales-data/v1/original.csv",
    )
    facade = DatasetRegistrationFacade(
        context_builder=context_builder,
        registry_service=registry_service,
        validator=validator,
        idempotency_service=idempotency_service,
        audit_service=audit_service,
        storage_strategy=storage_strategy,
        artifact_lifecycle_service=artifact_lifecycle_service,
    )
    command = RegisterDatasetCommand(
        tenant_id="tenant-a",
        slug="sales-data",
        name="Sales Data",
        path="/tmp/sample.csv",
        source_type="FILE_UPLOAD",
        storage_mode="MANAGED_UPLOAD",
    )

    facade.register(command)

    event_types = [event.event_type for event in publisher.list_events()]
    assert EventType.DATASET_ARTIFACT_UPLOADED in event_types


def test_job_lifecycle_emits_job_events() -> None:
    publisher = InMemoryEventPublisher()
    audit_service = AuditService(publisher=publisher)
    service = JobService(audit_service=audit_service)
    dataset = SimpleNamespace(id="dataset-1", tenant_id="tenant-a")
    created_job = SimpleNamespace(
        dataset=dataset,
        status="QUEUED",
        save=MagicMock(),
    )
    with patch(
            "resource_registry.services.registry.job_service.ExternalDatasetJob.objects.create",
            return_value=created_job,
    ):
        service.create_job(dataset=dataset, job_type="INGEST")

    created_job.status = "QUEUED"
    service.start_job(job=created_job)
    created_job.status = "RUNNING"
    service.complete_job(job=created_job, output_dataset_version=SimpleNamespace(id="version-1"))
    created_job.status = "RUNNING"
    service.fail_job(job=created_job, error_code="ERR")

    event_types = [event.event_type for event in publisher.list_events()]
    assert EventType.JOB_CREATED in event_types
    assert EventType.JOB_STARTED in event_types
    assert EventType.JOB_COMPLETED in event_types
    assert EventType.JOB_FAILED in event_types


def test_registration_does_not_fail_when_audit_publish_fails() -> None:
    registry_service = MagicMock()
    dataset = SimpleNamespace(
        id="dataset-1",
        versions=SimpleNamespace(
            order_by=lambda *_args, **_kwargs: SimpleNamespace(first=lambda: None),
        ),
    )
    registry_service.create_dataset.return_value = dataset
    registry_service.create_version_bundle_from_context.return_value = (
        VersionContextPersistenceResult(
            dataset_version=SimpleNamespace(id="version-1"),
            dataset_schema=SimpleNamespace(id="schema-1"),
            dataset_profile=SimpleNamespace(id="profile-1"),
        )
    )
    registry_service.attach_artifact.return_value = SimpleNamespace(id="artifact-1")
    context_builder = MagicMock()
    context_builder.build_context.return_value = {
        "source": {
            "path": "/tmp/sample.csv",
            "format": "csv",
            "file_name": "sample.csv",
            "file_size_bytes": 10,
        },
        "summary": {"row_count": 3, "column_count": 2},
        "schema": [{"name": "id", "type": "BIGINT"}],
        "preview": [{"id": 1}],
        "safety": {"preview_limit": 10, "preview_limited": True, "raw_content_included": False},
    }
    idempotency_service = MagicMock()
    idempotency_service.resolve.return_value = IdempotencyResolution(
        dataset=None,
        existing_version=None,
        existing_schema=None,
        existing_profile=None,
        existing_artifact=None,
        content_hash="hash-1",
    )
    idempotency_service.get_next_version_number.return_value = 1
    validator = MagicMock()
    failing_audit_service = MagicMock()
    failing_audit_service.record_dataset_registered.side_effect = RuntimeError("audit failed")
    failing_audit_service.record_dataset_version_created.side_effect = RuntimeError("audit failed")
    failing_audit_service.record_dataset_schema_created.side_effect = RuntimeError("audit failed")
    failing_audit_service.record_dataset_profile_created.side_effect = RuntimeError("audit failed")

    facade = DatasetRegistrationFacade(
        context_builder=context_builder,
        registry_service=registry_service,
        validator=validator,
        idempotency_service=idempotency_service,
        audit_service=failing_audit_service,
    )
    result = facade.register(
        RegisterDatasetCommand(
            tenant_id="tenant-a",
            slug="sales-data",
            name="Sales Data",
            path="/tmp/sample.csv",
            source_type="FILE_UPLOAD",
        ),
    )

    assert result.dataset_id == "dataset-1"


def test_audit_service_records_guardrail_and_integrity_events() -> None:
    publisher = InMemoryEventPublisher()
    audit_service = AuditService(publisher=publisher)

    audit_service.record_artifact_guardrail_rejected(
        tenant_id="tenant-a",
        dataset_id="dataset-1",
        metadata={"reason": "format", "raw_content": "x"},
    )
    audit_service.record_artifact_integrity_verified(
        tenant_id="tenant-a",
        dataset_id="dataset-1",
        metadata={"algorithm": "sha256", "preview": [{"id": 1}]},
    )
    audit_service.record_artifact_integrity_failed(
        tenant_id="tenant-a",
        dataset_id="dataset-1",
        metadata={"error_type": "ArtifactIntegrityError"},
    )

    events = publisher.list_events()
    event_types = [event.event_type for event in events]
    assert EventType.ARTIFACT_GUARDRAIL_REJECTED in event_types
    assert EventType.ARTIFACT_INTEGRITY_VERIFIED in event_types
    assert EventType.ARTIFACT_INTEGRITY_FAILED in event_types
    assert "raw_content" not in events[0].metadata
    assert "preview" not in events[1].metadata


def test_audit_service_emits_artifact_lifecycle_events() -> None:
    publisher = InMemoryEventPublisher()
    audit_service = AuditService(publisher=publisher)

    audit_service.record_dataset_artifact_deprecated(
        tenant_id="tenant-a",
        dataset_id="dataset-1",
        dataset_version_id="version-1",
        metadata={"artifact_id": "artifact-1"},
    )
    audit_service.record_dataset_artifact_restored(
        tenant_id="tenant-a",
        dataset_id="dataset-1",
        dataset_version_id="version-1",
        metadata={"artifact_id": "artifact-1"},
    )
    audit_service.record_dataset_artifact_reference_deleted(
        tenant_id="tenant-a",
        dataset_id="dataset-1",
        dataset_version_id="version-1",
        metadata={"artifact_id": "artifact-1"},
    )
    audit_service.record_dataset_artifact_physical_deleted(
        tenant_id="tenant-a",
        dataset_id="dataset-1",
        dataset_version_id="version-1",
        metadata={"artifact_id": "artifact-1"},
    )
    audit_service.record_dataset_artifact_orphan_cleaned(
        tenant_id="tenant-a",
        dataset_id="dataset-1",
        dataset_version_id="version-1",
        metadata={"artifact_id": "artifact-1"},
    )

    event_types = [event.event_type for event in publisher.list_events()]
    assert EventType.DATASET_ARTIFACT_DEPRECATED in event_types
    assert EventType.DATASET_ARTIFACT_RESTORED in event_types
    assert EventType.DATASET_ARTIFACT_REFERENCE_DELETED in event_types
    assert EventType.DATASET_ARTIFACT_PHYSICAL_DELETED in event_types
    assert EventType.DATASET_ARTIFACT_ORPHAN_CLEANED in event_types


def test_artifact_orphan_cleaned_event_metadata_is_sanitized() -> None:
    publisher = InMemoryEventPublisher()
    audit_service = AuditService(publisher=publisher)

    audit_service.record_dataset_artifact_orphan_cleaned(
        tenant_id="tenant-a",
        dataset_id="dataset-1",
        dataset_version_id="version-1",
        metadata={
            "artifact_id": "artifact-1",
            "preview": [{"id": 1}],
            "credentials": {"password": "x"},
            "raw_content": "forbidden",
        },
    )

    event = publisher.list_events()[0]
    assert "preview" not in event.metadata
    assert "credentials" not in event.metadata
    assert "raw_content" not in event.metadata


def test_audit_service_emits_policy_lifecycle_events() -> None:
    publisher = InMemoryEventPublisher()
    audit_service = AuditService(publisher=publisher)

    audit_service.record_policy_created(
        tenant_id="tenant-a",
        dataset_id=None,
        metadata={"policy_key": "retention_default"},
    )
    audit_service.record_policy_revision_created(
        tenant_id="tenant-a",
        dataset_id=None,
        metadata={"policy_key": "retention_default", "revision": 2},
    )
    audit_service.record_policy_activated(
        tenant_id="tenant-a",
        dataset_id=None,
        metadata={"policy_key": "retention_default", "revision": 2},
    )
    audit_service.record_policy_deactivated(
        tenant_id="tenant-a",
        dataset_id=None,
        metadata={"policy_key": "retention_default", "revision": 1},
    )
    audit_service.record_policy_assigned(
        tenant_id="tenant-a",
        dataset_id="dataset-1",
        metadata={"policy_key": "retention_default"},
    )
    audit_service.record_policy_removed(
        tenant_id="tenant-a",
        dataset_id="dataset-1",
        metadata={"policy_key": "retention_default"},
    )
    audit_service.record_policy_resolved(
        tenant_id="tenant-a",
        dataset_id="dataset-1",
        metadata={"policy_key": "retention_default", "source": "system_default"},
    )

    event_types = [event.event_type for event in publisher.list_events()]
    assert EventType.POLICY_CREATED in event_types
    assert EventType.POLICY_REVISION_CREATED in event_types
    assert EventType.POLICY_ACTIVATED in event_types
    assert EventType.POLICY_DEACTIVATED in event_types
    assert EventType.POLICY_ASSIGNED in event_types
    assert EventType.POLICY_REMOVED in event_types
    assert EventType.POLICY_RESOLVED in event_types


def test_audit_service_emits_external_job_events() -> None:
    publisher = InMemoryEventPublisher()
    audit_service = AuditService(publisher=publisher)

    audit_service.record_external_job_submitted(
        tenant_id="tenant-a",
        dataset_id="dataset-1",
        metadata={"job_id": "job-1"},
    )
    audit_service.record_external_job_running(
        tenant_id="tenant-a",
        dataset_id="dataset-1",
        metadata={"job_id": "job-1", "progress": 50},
    )
    audit_service.record_external_job_synced(
        tenant_id="tenant-a",
        dataset_id="dataset-1",
        metadata={"job_id": "job-1", "status": "RUNNING"},
    )
    audit_service.record_external_job_completed(
        tenant_id="tenant-a",
        dataset_id="dataset-1",
        metadata={"job_id": "job-1"},
    )
    audit_service.record_external_job_failed(
        tenant_id="tenant-a",
        dataset_id="dataset-1",
        metadata={"job_id": "job-1"},
    )
    audit_service.record_external_job_cancelled(
        tenant_id="tenant-a",
        dataset_id="dataset-1",
        metadata={"job_id": "job-1"},
    )

    event_types = [event.event_type for event in publisher.list_events()]
    assert EventType.EXTERNAL_JOB_SUBMITTED in event_types
    assert EventType.EXTERNAL_JOB_RUNNING in event_types
    assert EventType.EXTERNAL_JOB_SYNCED in event_types
    assert EventType.EXTERNAL_JOB_COMPLETED in event_types
    assert EventType.EXTERNAL_JOB_FAILED in event_types
    assert EventType.EXTERNAL_JOB_CANCELLED in event_types


def test_audit_service_emits_external_job_ingestion_events() -> None:
    publisher = InMemoryEventPublisher()
    audit_service = AuditService(publisher=publisher)

    audit_service.record_external_job_result_ingested(
        tenant_id="tenant-a",
        dataset_id="dataset-1",
        metadata={"external_job_id": "job-1"},
    )
    audit_service.record_external_job_result_deduplicated(
        tenant_id="tenant-a",
        dataset_id="dataset-1",
        metadata={"external_job_id": "job-1"},
    )
    audit_service.record_external_job_result_ingestion_failed(
        tenant_id="tenant-a",
        dataset_id="dataset-1",
        metadata={"external_job_id": "job-1", "error_type": "ValidationError"},
    )

    event_types = [event.event_type for event in publisher.list_events()]
    assert EventType.EXTERNAL_JOB_RESULT_INGESTED in event_types
    assert EventType.EXTERNAL_JOB_RESULT_DEDUPLICATED in event_types
    assert EventType.EXTERNAL_JOB_RESULT_INGESTION_FAILED in event_types


def test_audit_service_emits_materialization_policy_events() -> None:
    publisher = InMemoryEventPublisher()
    audit_service = AuditService(publisher=publisher)

    audit_service.record_artifact_materialization_policy_resolved(
        tenant_id="tenant-a",
        dataset_id="dataset-1",
        metadata={"allow_local_files": False},
    )
    audit_service.record_artifact_materialization_policy_rejected(
        tenant_id="tenant-a",
        dataset_id="dataset-1",
        metadata={"error_type": "ArtifactGuardrailViolation"},
    )

    event_types = [event.event_type for event in publisher.list_events()]
    assert EventType.ARTIFACT_MATERIALIZATION_POLICY_RESOLVED in event_types
    assert EventType.ARTIFACT_MATERIALIZATION_POLICY_REJECTED in event_types


def test_audit_service_emits_policy_provenance_and_enforcement_events() -> None:
    publisher = InMemoryEventPublisher()
    audit_service = AuditService(publisher=publisher)

    audit_service.record_policy_provenance_resolved(
        tenant_id="tenant-a",
        dataset_id="dataset-1",
        metadata={"policy_key": "artifact_materialization_default", "source": "SYSTEM_DEFAULT"},
    )
    audit_service.record_policy_enforcement_recorded(
        tenant_id="tenant-a",
        dataset_id="dataset-1",
        metadata={"enforcement_result": "ALLOW", "enforcement_reason": "guardrails_passed"},
    )

    event_types = [event.event_type for event in publisher.list_events()]
    assert EventType.POLICY_PROVENANCE_RESOLVED in event_types
    assert EventType.POLICY_ENFORCEMENT_RECORDED in event_types
