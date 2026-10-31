from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from resource_registry.application.artifact_context import ArtifactContextBuilder
from resource_registry.application.commands import IngestExternalJobResultCommand
from resource_registry.application.exceptions import ValidationError
from resource_registry.application.external_job_ingestion import ExternalJobResultIngestionFacade
from resource_registry.events.audit_service import AuditService
from resource_registry.events.event_publisher import InMemoryEventPublisher
from resource_registry.events.event_types import EventType
from resource_registry.models import ExternalDatasetJobStatus
from resource_registry.providers.models import ProviderJobResult
from resource_registry.security.exceptions import (
    ArtifactGuardrailViolation,
    ArtifactIntegrityError,
    ArtifactTooLargeError,
    UnsupportedArtifactFormatError,
)
from resource_registry.security.policies import ArtifactMaterializationPolicy
from resource_registry.services.registry.dataset_registry_service import (
    VersionContextPersistenceResult,
)
from resource_registry.storage.exceptions import UnsupportedArtifactReadError
from resource_registry.storage.local import LocalStorageProvider
from resource_registry.storage.read_adapters import LocalArtifactReadAdapter

SELECT_RELATED_PATH = (
    "resource_registry.application.external_job_ingestion."
    "ExternalDatasetJob.objects.select_related"
)
ARTIFACT_FILTER_PATH = (
    "resource_registry.application.external_job_ingestion." "DatasetArtifact.objects.filter"
)
SCHEMA_FILTER_PATH = (
    "resource_registry.application.external_job_ingestion." "DatasetSchema.objects.filter"
)
PROFILE_FILTER_PATH = (
    "resource_registry.application.external_job_ingestion." "DatasetProfile.objects.filter"
)
LINEAGE_FILTER_PATH = (
    "resource_registry.application.external_job_ingestion." "DatasetLineage.objects.filter"
)


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
        "resource_registry.application.external_job_ingestion."
        "ArtifactMaterializationPolicyResolver.resolve_policy",
        _resolve_policy,
    )
    monkeypatch.setattr(
        "resource_registry.application.external_job_ingestion."
        "ArtifactMaterializationPolicyResolver.resolve_with_provenance",
        _resolve_with_provenance,
    )


def _make_dataset(*, tenant_id: str = "11111111-1111-1111-1111-111111111111") -> SimpleNamespace:
    versions_manager = MagicMock()
    latest_query = MagicMock()
    latest_query.first.return_value = None
    versions_manager.order_by.return_value = latest_query
    hash_query = MagicMock()
    hash_query.order_by.return_value.first.return_value = None
    versions_manager.filter.return_value = hash_query
    return SimpleNamespace(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        versions=versions_manager,
        archive_reason="",
        revision=0,
        current_version=None,
        save=MagicMock(),
    )


def _make_job(dataset, *, status: str = ExternalDatasetJobStatus.SUCCEEDED):
    return SimpleNamespace(
        id=uuid.uuid4(),
        dataset=dataset,
        dataset_id=dataset.id,
        status=status,
        provider="simulated_azure",
        provider_job_ref="provider-job-1",
        output_dataset_version=None,
        output_dataset_version_id=None,
        save=MagicMock(),
    )


def _make_command(job, dataset, *, tenant_id: str | None = None):
    return IngestExternalJobResultCommand(
        tenant_id=tenant_id or str(dataset.tenant_id),
        dataset_id=str(dataset.id),
        external_dataset_job_id=str(job.id),
        provider_name="simulated_azure",
        preview_limit=5,
    )


def _make_provider_result(
    uri: str,
    *,
    checksum: str | None = None,
    size_bytes: int = 123,
    provider: str = "simulated_azure",
) -> ProviderJobResult:
    resolved_checksum = checksum
    local_path = Path(uri)
    if resolved_checksum is None and local_path.exists() and local_path.is_file():
        resolved_checksum = hashlib.sha256(local_path.read_bytes()).hexdigest()
    artifact_metadata: dict[str, object] = {"size_bytes": size_bytes, "etag": "etag-1"}
    if resolved_checksum is not None:
        artifact_metadata["checksum"] = resolved_checksum
    return ProviderJobResult(
        provider_job_id="provider-job-1",
        provider_name=provider,
        artifact_uri=uri,
        artifact_metadata=artifact_metadata,
    )


def _patch_existing_lookup_results():
    return (
        patch(ARTIFACT_FILTER_PATH),
        patch(SCHEMA_FILTER_PATH),
        patch(PROFILE_FILTER_PATH),
        patch(LINEAGE_FILTER_PATH),
    )


def test_successful_ingestion_from_succeeded_external_job(tmp_path: Path) -> None:
    artifact_path = tmp_path / "provider_result.csv"
    artifact_path.write_text("id,name\n1,Ada\n", encoding="utf-8")
    dataset = _make_dataset()
    job = _make_job(dataset)
    provider_service = MagicMock()
    provider_service.fetch_job_result.return_value = _make_provider_result(str(artifact_path))
    registry_service = MagicMock()
    persistence = VersionContextPersistenceResult(
        dataset_version=SimpleNamespace(id="ver-1"),
        dataset_schema=SimpleNamespace(id="schema-1"),
        dataset_profile=SimpleNamespace(id="profile-1"),
    )
    registry_service.create_version_bundle_from_context.return_value = persistence
    registry_service.attach_artifact.return_value = SimpleNamespace(id="artifact-1")
    lineage_service = MagicMock()
    lineage_service.record_lineage.return_value = SimpleNamespace(id="lineage-1")
    facade = ExternalJobResultIngestionFacade(
        external_provider_service=provider_service,
        artifact_context_builder=ArtifactContextBuilder(
            materialization_policy=ArtifactMaterializationPolicy(allow_local_files=True),
        ),
        registry_service=registry_service,
        lineage_service=lineage_service,
    )
    with patch(SELECT_RELATED_PATH) as mocked_select_related:
        mocked_select_related.return_value.get.return_value = job
        result = facade.ingest(_make_command(job, dataset))

    assert result.dataset_version_id == "ver-1"
    assert result.artifact_id == "artifact-1"
    assert result.schema_id == "schema-1"
    assert result.profile_id == "profile-1"
    assert result.lineage_id == "lineage-1"
    assert result.was_idempotent is False
    assert registry_service.create_version_bundle_from_context.called
    artifact_metadata = registry_service.attach_artifact.call_args.kwargs["metadata_json"]
    assert "preview" not in artifact_metadata
    assert job.output_dataset_version == persistence.dataset_version


def test_unsafe_uri_fails_closed() -> None:
    dataset = _make_dataset()
    job = _make_job(dataset)
    provider_service = MagicMock()
    provider_service.fetch_job_result.return_value = _make_provider_result(
        "https://example.blob.core.windows.net/c/data.csv?sig=secret",
    )
    facade = ExternalJobResultIngestionFacade(external_provider_service=provider_service)
    with patch(SELECT_RELATED_PATH) as mocked_select_related:
        mocked_select_related.return_value.get.return_value = job
        with pytest.raises(ValidationError):
            facade.ingest(_make_command(job, dataset))


def test_oversized_artifact_fails_closed() -> None:
    dataset = _make_dataset()
    job = _make_job(dataset)
    provider_service = MagicMock()
    provider_service.fetch_job_result.return_value = _make_provider_result(
        "local://datasets/file.csv",
        size_bytes=10_000_000_000,
    )
    artifact_context_builder = ArtifactContextBuilder(
        materialization_policy=ArtifactMaterializationPolicy(max_size_bytes=10),
    )
    facade = ExternalJobResultIngestionFacade(
        external_provider_service=provider_service,
        artifact_context_builder=artifact_context_builder,
    )
    with patch(SELECT_RELATED_PATH) as mocked_select_related:
        mocked_select_related.return_value.get.return_value = job
        with pytest.raises(ArtifactTooLargeError):
            facade.ingest(_make_command(job, dataset))


@pytest.mark.parametrize(
    "status",
    [
        ExternalDatasetJobStatus.QUEUED,
        ExternalDatasetJobStatus.RUNNING,
        ExternalDatasetJobStatus.FAILED,
        ExternalDatasetJobStatus.CANCELLED,
    ],
)
def test_ingestion_rejected_for_non_succeeded_status(status: str) -> None:
    dataset = _make_dataset()
    job = _make_job(dataset, status=status)
    facade = ExternalJobResultIngestionFacade(external_provider_service=MagicMock())
    with patch(SELECT_RELATED_PATH) as mocked_select_related:
        mocked_select_related.return_value.get.return_value = job
        with pytest.raises(ValidationError):
            facade.ingest(_make_command(job, dataset))


def test_tenant_mismatch_rejected() -> None:
    dataset = _make_dataset(tenant_id="11111111-1111-1111-1111-111111111111")
    job = _make_job(dataset)
    facade = ExternalJobResultIngestionFacade(external_provider_service=MagicMock())
    with patch(SELECT_RELATED_PATH) as mocked_select_related:
        mocked_select_related.return_value.get.return_value = job
        with pytest.raises(ValidationError):
            facade.ingest(
                _make_command(job, dataset, tenant_id="22222222-2222-2222-2222-222222222222"),
            )


def test_idempotent_when_output_dataset_version_already_present() -> None:
    dataset = _make_dataset()
    output_version = SimpleNamespace(id="ver-existing", source_uri="/tmp/existing.csv")
    job = _make_job(dataset)
    job.output_dataset_version = output_version
    job.output_dataset_version_id = "ver-existing"
    provider_service = MagicMock()
    facade = ExternalJobResultIngestionFacade(external_provider_service=provider_service)
    with (
        patch(SELECT_RELATED_PATH) as mocked_select_related,
        patch(ARTIFACT_FILTER_PATH) as artifact_qs,
        patch(SCHEMA_FILTER_PATH) as schema_qs,
        patch(PROFILE_FILTER_PATH) as profile_qs,
        patch(LINEAGE_FILTER_PATH) as lineage_qs,
    ):
        mocked_select_related.return_value.get.return_value = job
        artifact_qs.return_value.order_by.return_value.first.return_value = SimpleNamespace(
            id="artifact-1",
        )
        schema_qs.return_value.order_by.return_value.first.return_value = None
        profile_qs.return_value.order_by.return_value.first.return_value = None
        lineage_qs.return_value.order_by.return_value.first.return_value = None
        result = facade.ingest(_make_command(job, dataset))

    assert result.was_idempotent is True
    provider_service.fetch_job_result.assert_not_called()


def test_minimal_ingestion_when_context_cannot_be_built() -> None:
    dataset = _make_dataset()
    job = _make_job(dataset)
    provider_service = MagicMock()
    provider_service.fetch_job_result.return_value = _make_provider_result(
        "local://remote/file.parquet"
    )
    registry_service = MagicMock()
    registry_service.create_dataset_version.return_value = SimpleNamespace(id="ver-min")
    registry_service.attach_artifact.return_value = SimpleNamespace(id="artifact-min")
    lineage_service = MagicMock()
    lineage_service.record_lineage.return_value = SimpleNamespace(id="lineage-min")
    artifact_context_builder = MagicMock()
    artifact_context_builder.build_context_with_integrity.side_effect = (
        UnsupportedArtifactReadError("unsupported")
    )
    facade = ExternalJobResultIngestionFacade(
        external_provider_service=provider_service,
        artifact_context_builder=artifact_context_builder,
        registry_service=registry_service,
        lineage_service=lineage_service,
    )
    with patch(SELECT_RELATED_PATH) as mocked_select_related:
        mocked_select_related.return_value.get.return_value = job
        result = facade.ingest(_make_command(job, dataset))

    assert result.schema_id is None
    assert result.profile_id is None
    assert registry_service.create_dataset_version.called
    assert result.row_count == 0
    assert result.column_count == 0


def test_unsupported_safe_unreadable_artifact_uses_minimal_fallback() -> None:
    dataset = _make_dataset()
    job = _make_job(dataset)
    provider_service = MagicMock()
    provider_service.fetch_job_result.return_value = _make_provider_result(
        "azure://datasets/provider/file.parquet",
        checksum=None,
    )
    registry_service = MagicMock()
    registry_service.create_dataset_version.return_value = SimpleNamespace(id="ver-min")
    registry_service.attach_artifact.return_value = SimpleNamespace(id="artifact-min")
    lineage_service = MagicMock()
    lineage_service.record_lineage.return_value = SimpleNamespace(id="lineage-min")
    facade = ExternalJobResultIngestionFacade(
        external_provider_service=provider_service,
        artifact_context_builder=ArtifactContextBuilder(
            materialization_policy=ArtifactMaterializationPolicy(allow_local_files=True),
        ),
        registry_service=registry_service,
        lineage_service=lineage_service,
    )
    with patch(SELECT_RELATED_PATH) as mocked_select_related:
        mocked_select_related.return_value.get.return_value = job
        result = facade.ingest(_make_command(job, dataset))

    assert result.schema_id is None
    assert result.profile_id is None
    assert registry_service.create_dataset_version.called


def test_local_managed_uri_builds_schema_profile_via_adapter(tmp_path: Path) -> None:
    source_file = tmp_path / "source.csv"
    source_file.write_text("id,name\n1,Ada\n", encoding="utf-8")
    storage_provider = LocalStorageProvider(base_directory=tmp_path / "store")
    storage_provider.put(str(source_file), "datasets/provider/result.csv")
    dataset = _make_dataset()
    job = _make_job(dataset)
    provider_service = MagicMock()
    provider_service.fetch_job_result.return_value = _make_provider_result(
        "local://datasets/provider/result.csv",
    )
    registry_service = MagicMock()
    persistence = VersionContextPersistenceResult(
        dataset_version=SimpleNamespace(id="ver-1"),
        dataset_schema=SimpleNamespace(id="schema-1"),
        dataset_profile=SimpleNamespace(id="profile-1"),
    )
    registry_service.create_version_bundle_from_context.return_value = persistence
    registry_service.attach_artifact.return_value = SimpleNamespace(id="artifact-1")
    lineage_service = MagicMock()
    lineage_service.record_lineage.return_value = SimpleNamespace(id="lineage-1")
    artifact_context_builder = ArtifactContextBuilder(
        adapters=[LocalArtifactReadAdapter(storage_provider=storage_provider)],
    )
    facade = ExternalJobResultIngestionFacade(
        external_provider_service=provider_service,
        artifact_context_builder=artifact_context_builder,
        registry_service=registry_service,
        lineage_service=lineage_service,
    )
    with patch(SELECT_RELATED_PATH) as mocked_select_related:
        mocked_select_related.return_value.get.return_value = job
        result = facade.ingest(_make_command(job, dataset))

    assert result.schema_id == "schema-1"
    assert result.profile_id == "profile-1"
    assert registry_service.create_version_bundle_from_context.called


def test_content_hash_idempotency_avoids_duplicate_creation(tmp_path: Path) -> None:
    artifact_path = tmp_path / "provider_result.csv"
    artifact_path.write_text("id,name\n1,Ada\n", encoding="utf-8")
    dataset = _make_dataset()
    existing_version = SimpleNamespace(id="ver-existing", source_uri=str(artifact_path))
    hash_query = MagicMock()
    hash_query.order_by.return_value.first.return_value = existing_version
    dataset.versions.filter.return_value = hash_query
    job = _make_job(dataset)
    provider_service = MagicMock()
    provider_service.fetch_job_result.return_value = _make_provider_result(str(artifact_path))
    registry_service = MagicMock()
    facade = ExternalJobResultIngestionFacade(
        external_provider_service=provider_service,
        registry_service=registry_service,
    )
    with (
        patch(SELECT_RELATED_PATH) as mocked_select_related,
        patch(ARTIFACT_FILTER_PATH) as artifact_qs,
        patch(SCHEMA_FILTER_PATH) as schema_qs,
        patch(PROFILE_FILTER_PATH) as profile_qs,
        patch(LINEAGE_FILTER_PATH) as lineage_qs,
    ):
        mocked_select_related.return_value.get.return_value = job
        artifact_qs.return_value.order_by.return_value.first.return_value = SimpleNamespace(
            id="artifact-1",
        )
        schema_qs.return_value.order_by.return_value.first.return_value = None
        profile_qs.return_value.order_by.return_value.first.return_value = None
        lineage_qs.return_value.order_by.return_value.first.return_value = None
        result = facade.ingest(_make_command(job, dataset))

    assert result.was_idempotent is True
    registry_service.create_version_bundle_from_context.assert_not_called()
    registry_service.create_dataset_version.assert_not_called()


def test_repeat_ingestion_does_not_duplicate_versions_or_artifacts(tmp_path: Path) -> None:
    artifact_path = tmp_path / "provider_result.csv"
    artifact_path.write_text("id,name\n1,Ada\n", encoding="utf-8")
    dataset = _make_dataset()
    job = _make_job(dataset)
    provider_service = MagicMock()
    provider_service.fetch_job_result.return_value = _make_provider_result(str(artifact_path))
    registry_service = MagicMock()
    persistence = VersionContextPersistenceResult(
        dataset_version=SimpleNamespace(id="ver-1", source_uri=str(artifact_path)),
        dataset_schema=SimpleNamespace(id="schema-1"),
        dataset_profile=SimpleNamespace(id="profile-1"),
    )
    registry_service.create_version_bundle_from_context.return_value = persistence
    registry_service.attach_artifact.return_value = SimpleNamespace(id="artifact-1")
    lineage_service = MagicMock()
    lineage_service.record_lineage.return_value = SimpleNamespace(id="lineage-1")
    facade = ExternalJobResultIngestionFacade(
        external_provider_service=provider_service,
        artifact_context_builder=ArtifactContextBuilder(
            materialization_policy=ArtifactMaterializationPolicy(allow_local_files=True),
        ),
        registry_service=registry_service,
        lineage_service=lineage_service,
    )
    with (
        patch(SELECT_RELATED_PATH) as mocked_select_related,
        patch(ARTIFACT_FILTER_PATH) as artifact_qs,
        patch(SCHEMA_FILTER_PATH) as schema_qs,
        patch(PROFILE_FILTER_PATH) as profile_qs,
        patch(LINEAGE_FILTER_PATH) as lineage_qs,
    ):
        mocked_select_related.return_value.get.return_value = job
        artifact_qs.return_value.order_by.return_value.first.return_value = SimpleNamespace(
            id="artifact-1",
        )
        schema_qs.return_value.order_by.return_value.first.return_value = SimpleNamespace(
            id="schema-1",
        )
        profile_qs.return_value.order_by.return_value.first.return_value = SimpleNamespace(
            id="profile-1",
            metrics_json={"row_count": 1, "column_count": 2},
        )
        lineage_qs.return_value.order_by.return_value.first.return_value = SimpleNamespace(
            id="lineage-1",
        )

        first = facade.ingest(_make_command(job, dataset))
        second = facade.ingest(_make_command(job, dataset))

    assert first.was_idempotent is False
    assert second.was_idempotent is True
    registry_service.create_version_bundle_from_context.assert_called_once()
    registry_service.attach_artifact.assert_called_once()


def test_ingestion_emits_guardrail_rejected_event() -> None:
    dataset = _make_dataset()
    job = _make_job(dataset)
    provider_service = MagicMock()
    provider_service.fetch_job_result.return_value = _make_provider_result(
        "local://datasets/file.txt",
    )
    publisher = InMemoryEventPublisher()
    audit_service = AuditService(publisher=publisher)
    facade = ExternalJobResultIngestionFacade(
        external_provider_service=provider_service,
        audit_service=audit_service,
    )
    with patch(SELECT_RELATED_PATH) as mocked_select_related:
        mocked_select_related.return_value.get.return_value = job
        with pytest.raises(UnsupportedArtifactFormatError):
            facade.ingest(_make_command(job, dataset))

    event_types = [event.event_type for event in publisher.list_events()]
    assert EventType.ARTIFACT_GUARDRAIL_REJECTED in event_types
    assert EventType.ARTIFACT_MATERIALIZATION_POLICY_REJECTED in event_types


def test_ingestion_emits_integrity_verified_and_failed_events(tmp_path: Path) -> None:
    source_file = tmp_path / "source.csv"
    source_file.write_text("id,name\n1,Ada\n", encoding="utf-8")
    storage_provider = LocalStorageProvider(base_directory=tmp_path / "store")
    storage_provider.put(str(source_file), "datasets/provider/result.csv")
    expected_checksum = hashlib.sha256(source_file.read_bytes()).hexdigest()

    dataset = _make_dataset()
    job = _make_job(dataset)
    provider_service = MagicMock()
    provider_service.fetch_job_result.return_value = _make_provider_result(
        "local://datasets/provider/result.csv",
        checksum=expected_checksum,
    )
    registry_service = MagicMock()
    persistence = VersionContextPersistenceResult(
        dataset_version=SimpleNamespace(id="ver-1"),
        dataset_schema=SimpleNamespace(id="schema-1"),
        dataset_profile=SimpleNamespace(id="profile-1"),
    )
    registry_service.create_version_bundle_from_context.return_value = persistence
    registry_service.attach_artifact.return_value = SimpleNamespace(id="artifact-1")
    lineage_service = MagicMock()
    lineage_service.record_lineage.return_value = SimpleNamespace(id="lineage-1")
    artifact_context_builder = ArtifactContextBuilder(
        adapters=[LocalArtifactReadAdapter(storage_provider=storage_provider)],
    )
    publisher = InMemoryEventPublisher()
    audit_service = AuditService(publisher=publisher)
    facade = ExternalJobResultIngestionFacade(
        external_provider_service=provider_service,
        artifact_context_builder=artifact_context_builder,
        registry_service=registry_service,
        lineage_service=lineage_service,
        audit_service=audit_service,
    )
    with patch(SELECT_RELATED_PATH) as mocked_select_related:
        mocked_select_related.return_value.get.return_value = job
        _ = facade.ingest(_make_command(job, dataset))
    emitted_types = [event.event_type for event in publisher.list_events()]
    assert EventType.ARTIFACT_INTEGRITY_VERIFIED in emitted_types
    assert EventType.ARTIFACT_MATERIALIZATION_POLICY_RESOLVED in emitted_types

    mismatch_job = _make_job(dataset)
    mismatch_provider_service = MagicMock()
    mismatch_provider_service.fetch_job_result.return_value = _make_provider_result(
        "local://datasets/provider/result.csv",
        checksum="a" * 64,
    )
    mismatch_facade = ExternalJobResultIngestionFacade(
        external_provider_service=mismatch_provider_service,
        artifact_context_builder=artifact_context_builder,
        audit_service=audit_service,
    )
    with patch(SELECT_RELATED_PATH) as mismatch_select_related:
        mismatch_select_related.return_value.get.return_value = mismatch_job
        with pytest.raises(ArtifactIntegrityError):
            mismatch_facade.ingest(_make_command(mismatch_job, dataset))
    assert EventType.ARTIFACT_INTEGRITY_FAILED in [
        event.event_type for event in publisher.list_events()
    ]


def test_ingestion_checksum_required_policy_rejects_missing_checksum() -> None:
    dataset = _make_dataset()
    job = _make_job(dataset)
    provider_service = MagicMock()
    provider_service.fetch_job_result.return_value = _make_provider_result(
        "local://datasets/provider/result.csv",
        checksum=None,
    )
    policy_resolver = MagicMock()
    policy_resolver.resolve_policy.return_value = ArtifactMaterializationPolicy(
        allow_local_files=True,
        require_checksum=True,
    )
    facade = ExternalJobResultIngestionFacade(
        external_provider_service=provider_service,
        materialization_policy_resolver=policy_resolver,
    )
    with patch(SELECT_RELATED_PATH) as mocked_select_related:
        mocked_select_related.return_value.get.return_value = job
        with pytest.raises(ArtifactGuardrailViolation):
            facade.ingest(_make_command(job, dataset))
