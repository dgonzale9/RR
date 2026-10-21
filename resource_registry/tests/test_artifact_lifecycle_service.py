from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from resource_registry.events.audit_service import AuditService
from resource_registry.events.event_publisher import InMemoryEventPublisher
from resource_registry.events.event_types import EventType
from resource_registry.models import ArtifactLifecycleStatus
from resource_registry.services.registry.artifact_lifecycle_service import ArtifactLifecycleService


def _artifact_stub() -> SimpleNamespace:
    return SimpleNamespace(
        id="artifact-1",
        uri="local://datasets/tenant/sales/v1/original.csv",
        storage_provider="local",
        artifact_role="PRIMARY",
        metadata_json={},
        lifecycle_status=ArtifactLifecycleStatus.ACTIVE,
        dataset_version=SimpleNamespace(
            id="version-1",
            dataset=SimpleNamespace(id="dataset-1", tenant_id="tenant-a"),
        ),
        save=MagicMock(),
        delete=MagicMock(),
    )


def test_register_uploaded_artifact_creates_active_artifact() -> None:
    service = ArtifactLifecycleService()
    dataset_version = SimpleNamespace(
        id="version-1",
        dataset=SimpleNamespace(id="dataset-1", tenant_id="tenant-a"),
    )
    created_artifact = _artifact_stub()
    with patch(
        "resource_registry.services.registry.artifact_lifecycle_service.DatasetArtifact.objects.create",
        return_value=created_artifact,
    ) as mocked_create:
        result = service.register_uploaded_artifact(
            dataset_version=dataset_version,
            artifact_role="PRIMARY",
            uri="local://datasets/tenant/sales/v1/original.csv",
            format="CSV",
            size_bytes=100,
            storage_provider="local",
        )

    assert result == created_artifact
    assert mocked_create.call_args.kwargs["lifecycle_status"] == ArtifactLifecycleStatus.ACTIVE


def test_deprecate_artifact() -> None:
    artifact = _artifact_stub()
    service = ArtifactLifecycleService()

    service.deprecate_artifact(artifact=artifact, reason="superseded")

    assert artifact.lifecycle_status == ArtifactLifecycleStatus.DEPRECATED
    assert artifact.metadata_json["deprecation_reason"] == "superseded"
    artifact.save.assert_called_once()


def test_restore_artifact() -> None:
    artifact = _artifact_stub()
    artifact.lifecycle_status = ArtifactLifecycleStatus.DEPRECATED
    service = ArtifactLifecycleService()

    service.restore_artifact(artifact=artifact)

    assert artifact.lifecycle_status == ArtifactLifecycleStatus.ACTIVE
    artifact.save.assert_called_once()


def test_delete_artifact_reference_does_not_call_storage_provider() -> None:
    artifact = _artifact_stub()
    storage_provider = MagicMock()
    service = ArtifactLifecycleService()

    service.delete_artifact_reference(artifact=artifact)

    assert artifact.lifecycle_status == ArtifactLifecycleStatus.DELETED_REFERENCE
    storage_provider.delete.assert_not_called()
    artifact.save.assert_called_once()


def test_delete_physical_artifact_calls_provider_and_updates_status() -> None:
    artifact = _artifact_stub()
    storage_provider = MagicMock()
    service = ArtifactLifecycleService()

    service.delete_physical_artifact(artifact=artifact, storage_provider=storage_provider)

    storage_provider.delete.assert_called_once_with(artifact.uri)
    assert artifact.lifecycle_status == ArtifactLifecycleStatus.PHYSICAL_DELETED
    artifact.save.assert_called_once()
    artifact.delete.assert_not_called()


def test_cleanup_orphan_upload_calls_provider_delete_and_emits_event() -> None:
    publisher = InMemoryEventPublisher()
    audit_service = AuditService(publisher=publisher)
    service = ArtifactLifecycleService(audit_service=audit_service)
    storage_provider = MagicMock()

    service.cleanup_orphan_upload(
        storage_provider=storage_provider,
        uri="local://datasets/tenant/sales/v1/original.csv",
        tenant_id="tenant-a",
        dataset_id="dataset-1",
        dataset_version_id="version-1",
        metadata={"reason": "db_persistence_failed"},
    )

    storage_provider.delete.assert_called_once_with(
        "local://datasets/tenant/sales/v1/original.csv",
    )
    event_types = [event.event_type for event in publisher.list_events()]
    assert EventType.DATASET_ARTIFACT_ORPHAN_CLEANED in event_types


def test_get_artifacts_for_version() -> None:
    service = ArtifactLifecycleService()
    dataset_version = SimpleNamespace(id="version-1")
    queryset = MagicMock()
    filtered = MagicMock()
    filtered.order_by.return_value = "ordered"
    queryset.exclude.return_value = filtered
    with patch(
        "resource_registry.services.registry.artifact_lifecycle_service.DatasetArtifact.objects.filter",
        return_value=queryset,
    ) as mocked_filter:
        artifacts = service.get_artifacts_for_version(
            dataset_version=dataset_version,
            include_deleted_reference=False,
        )

    assert artifacts == "ordered"
    mocked_filter.assert_called_once_with(dataset_version=dataset_version)
    queryset.exclude.assert_called_once()
