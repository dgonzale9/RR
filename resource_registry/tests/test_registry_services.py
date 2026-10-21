from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from resource_registry.models import (
    DatasetVersionStatus,
    ExternalDatasetJobStatus,
    ProfileType,
)
from resource_registry.services.registry.dataset_registry_service import (
    DatasetRegistryService,
    VersionContextPersistenceResult,
)
from resource_registry.services.registry.governance_service import (
    GovernanceBoundaryError,
    GovernanceService,
)
from resource_registry.services.registry.job_service import InvalidJobTransitionError, JobService
from resource_registry.services.registry.lineage_service import InvalidLineageError, LineageService

TENANT_A = uuid.uuid4()
TENANT_B = uuid.uuid4()
VERSION_NUMBER = 1
PREVIEW_LIMIT = 10
RETRY_INCREMENT = 1


@pytest.fixture
def dataset_registry_service() -> DatasetRegistryService:
    return DatasetRegistryService()


@pytest.fixture
def lineage_service() -> LineageService:
    return LineageService()


@pytest.fixture
def governance_service() -> GovernanceService:
    return GovernanceService()


@pytest.fixture
def job_service() -> JobService:
    return JobService()


def test_create_dataset_calls_model_create(
    dataset_registry_service: DatasetRegistryService,
) -> None:
    with patch(
        "resource_registry.services.registry.dataset_registry_service.Dataset.objects.create",
        return_value=SimpleNamespace(),
    ) as mocked_create:
        dataset_registry_service.create_dataset(
            tenant_id=str(TENANT_A),
            slug="sales-data",
            name="Sales Data",
            source_type="FILE_UPLOAD",
        )
    mocked_create.assert_called_once()


def test_create_dataset_version_calls_model_create(
    dataset_registry_service: DatasetRegistryService,
) -> None:
    dataset = SimpleNamespace()
    with patch(
        "resource_registry.services.registry.dataset_registry_service.DatasetVersion.objects.create",
        return_value=SimpleNamespace(),
    ) as mocked_create:
        dataset_registry_service.create_dataset_version(
            dataset=dataset,
            version_number=VERSION_NUMBER,
            source_uri="/tmp/sales.csv",
            content_hash="hash",
        )
    mocked_create.assert_called_once()


def test_create_version_bundle_from_context_persists_metadata_not_preview(
    dataset_registry_service: DatasetRegistryService,
) -> None:
    dataset = SimpleNamespace()
    dataset_version = SimpleNamespace()
    context = {
        "source": {
            "path": "/tmp/sales.csv",
            "format": "csv",
            "file_name": "sales.csv",
            "file_size_bytes": 1024,
        },
        "summary": {
            "row_count": 1000,
            "column_count": 3,
        },
        "schema": [
            {"name": "id", "type": "BIGINT"},
            {"name": "name", "type": "VARCHAR"},
            {"name": "amount", "type": "DOUBLE"},
        ],
        "preview": [
            {"id": 1, "name": "A", "amount": 10.0},
        ],
        "safety": {
            "raw_content_included": False,
            "preview_limited": True,
            "preview_limit": PREVIEW_LIMIT,
        },
    }
    with (
        patch.object(
            dataset_registry_service,
            "create_dataset_version",
            return_value=dataset_version,
        ),
        patch.object(dataset_registry_service, "attach_schema") as mocked_attach_schema,
        patch.object(dataset_registry_service, "attach_profile") as mocked_attach_profile,
        patch.object(dataset_registry_service, "_persist_ready_version_outbox_entry"),
    ):
        result = dataset_registry_service.create_version_bundle_from_context.__wrapped__(
            dataset_registry_service,
            dataset=dataset,
            version_number=VERSION_NUMBER,
            content_hash="hash",
            context=context,
            status=DatasetVersionStatus.READY,
            profile_type=ProfileType.BASIC,
        )

    assert result.dataset_version == dataset_version
    mocked_attach_schema.assert_called_once()
    mocked_attach_profile.assert_called_once()
    profile_metrics = mocked_attach_profile.call_args.kwargs["metrics_json"]
    assert "preview" not in profile_metrics
    assert profile_metrics["row_count"] == context["summary"]["row_count"]


def test_create_version_from_context_returns_dataset_version(
    dataset_registry_service: DatasetRegistryService,
) -> None:
    dataset = SimpleNamespace()
    dataset_version = SimpleNamespace()
    persistence_result = VersionContextPersistenceResult(
        dataset_version=dataset_version,
        dataset_schema=SimpleNamespace(),
        dataset_profile=SimpleNamespace(),
    )
    with patch.object(
        dataset_registry_service,
        "create_version_bundle_from_context",
        return_value=persistence_result,
    ) as mocked_bundle:
        result = dataset_registry_service.create_version_from_context.__wrapped__(
            dataset_registry_service,
            dataset=dataset,
            version_number=VERSION_NUMBER,
            content_hash="hash",
            context={},
        )

    assert result == dataset_version
    mocked_bundle.assert_called_once()


def test_attach_artifact_calls_model_create(
    dataset_registry_service: DatasetRegistryService,
) -> None:
    dataset_version = SimpleNamespace()
    with patch(
        "resource_registry.services.registry.dataset_registry_service.DatasetArtifact.objects.create",
        return_value=SimpleNamespace(),
    ) as mocked_create:
        dataset_registry_service.attach_artifact(
            dataset_version=dataset_version,
            artifact_role="PRIMARY",
            uri="s3://bucket/data.parquet",
            format="PARQUET",
            size_bytes=2048,
        )
    mocked_create.assert_called_once()


def test_attach_schema_calls_model_create(dataset_registry_service: DatasetRegistryService) -> None:
    dataset_version = SimpleNamespace()
    with patch(
        "resource_registry.services.registry.dataset_registry_service.DatasetSchema.objects.create",
        return_value=SimpleNamespace(),
    ) as mocked_create:
        dataset_registry_service.attach_schema(
            dataset_version=dataset_version,
            schema_hash="schema-hash",
            schema_json=[{"name": "id", "type": "BIGINT"}],
            column_count=1,
        )
    mocked_create.assert_called_once()


def test_attach_profile_calls_model_create(
    dataset_registry_service: DatasetRegistryService,
) -> None:
    dataset_version = SimpleNamespace()
    with patch(
        "resource_registry.services.registry.dataset_registry_service.DatasetProfile.objects.create",
        return_value=SimpleNamespace(),
    ) as mocked_create:
        dataset_registry_service.attach_profile(
            dataset_version=dataset_version,
            profile_type=ProfileType.BASIC,
            profile_hash="profile-hash",
            metrics_json={"row_count": 10},
        )
    mocked_create.assert_called_once()


def test_record_internal_lineage(lineage_service: LineageService) -> None:
    downstream = SimpleNamespace(id=1, dataset=SimpleNamespace(tenant_id=TENANT_A))
    upstream = SimpleNamespace(id=2, dataset=SimpleNamespace(tenant_id=TENANT_A))
    with patch(
        "resource_registry.services.registry.lineage_service.DatasetLineage.objects.create",
        return_value=SimpleNamespace(),
    ) as mocked_create:
        lineage_service.record_lineage(
            downstream_dataset_version=downstream,
            upstream_dataset_version=upstream,
            lineage_type="DERIVED_FROM",
        )
    mocked_create.assert_called_once()


def test_record_external_lineage(lineage_service: LineageService) -> None:
    downstream = SimpleNamespace(dataset=SimpleNamespace(tenant_id=TENANT_A))
    with patch(
        "resource_registry.services.registry.lineage_service.DatasetLineage.objects.create",
        return_value=SimpleNamespace(),
    ) as mocked_create:
        lineage_service.record_lineage(
            downstream_dataset_version=downstream,
            upstream_external_uri="gs://bucket/source.csv",
            lineage_type="INGESTED_FROM",
        )
    mocked_create.assert_called_once()


def test_record_lineage_rejects_invalid_input(lineage_service: LineageService) -> None:
    downstream = SimpleNamespace(dataset=SimpleNamespace(tenant_id=TENANT_A))
    with pytest.raises(InvalidLineageError):
        lineage_service.record_lineage(
            downstream_dataset_version=downstream,
            lineage_type="DERIVED_FROM",
        )


def test_assign_policy(governance_service: GovernanceService) -> None:
    dataset = SimpleNamespace(tenant_id=TENANT_A)
    policy = SimpleNamespace(tenant_id=TENANT_A)
    assignment = SimpleNamespace()
    with patch(
        "resource_registry.services.registry.governance_service.DatasetPolicyAssignment.objects.update_or_create",
        return_value=(assignment, True),
    ) as mocked_update_or_create:
        result = governance_service.assign_policy(dataset=dataset, governance_policy=policy)
    assert result == assignment
    mocked_update_or_create.assert_called_once()


def test_remove_policy(governance_service: GovernanceService) -> None:
    dataset = SimpleNamespace(tenant_id=TENANT_A)
    policy = SimpleNamespace(tenant_id=TENANT_A)
    mocked_queryset = MagicMock()
    mocked_queryset.delete.return_value = (1, {})
    with patch(
        "resource_registry.services.registry.governance_service.DatasetPolicyAssignment.objects.filter",
        return_value=mocked_queryset,
    ) as mocked_filter:
        removed = governance_service.remove_policy(dataset=dataset, governance_policy=policy)
    assert removed is True
    mocked_filter.assert_called_once()


def test_get_active_policies_without_override_returns_union(
    governance_service: GovernanceService,
) -> None:
    dataset = SimpleNamespace(tenant_id=TENANT_A)
    assigned_qs = MagicMock()
    default_qs = MagicMock()
    union_qs = MagicMock()
    assigned_qs.__or__.return_value = union_qs
    union_qs.distinct.return_value = "combined"

    with (
        patch(
            "resource_registry.services.registry.governance_service.GovernancePolicy.objects.filter",
            side_effect=[assigned_qs, default_qs],
        ),
        patch(
            "resource_registry.services.registry.governance_service.DatasetPolicyAssignment.objects.filter",
        ) as mocked_assignment_filter,
    ):
        mocked_assignment_filter.return_value.exists.return_value = False
        result = governance_service.get_active_policies(dataset=dataset)

    assert result == "combined"


def test_assign_policy_rejects_cross_tenant(governance_service: GovernanceService) -> None:
    dataset = SimpleNamespace(tenant_id=TENANT_A)
    policy = SimpleNamespace(tenant_id=TENANT_B)
    with pytest.raises(GovernanceBoundaryError):
        governance_service.assign_policy(dataset=dataset, governance_policy=policy)


def test_create_job(job_service: JobService) -> None:
    dataset = SimpleNamespace()
    with patch(
        "resource_registry.services.registry.job_service.ExternalDatasetJob.objects.create",
        return_value=SimpleNamespace(),
    ) as mocked_create:
        job_service.create_job(dataset=dataset, job_type="INGEST")
    mocked_create.assert_called_once()


def test_job_transition_pending_to_running(job_service: JobService) -> None:
    mocked_now = SimpleNamespace()
    job = SimpleNamespace(status=ExternalDatasetJobStatus.QUEUED, save=MagicMock())
    with patch(
        "resource_registry.services.registry.job_service.timezone.now", return_value=mocked_now
    ):
        job_service.start_job(job=job)
    assert job.status == ExternalDatasetJobStatus.RUNNING
    assert job.started_at == mocked_now
    job.save.assert_called_once()


def test_job_transition_running_to_completed(job_service: JobService) -> None:
    mocked_now = SimpleNamespace()
    job = SimpleNamespace(status=ExternalDatasetJobStatus.RUNNING, save=MagicMock())
    output_dataset_version = SimpleNamespace()
    with patch(
        "resource_registry.services.registry.job_service.timezone.now", return_value=mocked_now
    ):
        job_service.complete_job(job=job, output_dataset_version=output_dataset_version)
    assert job.status == ExternalDatasetJobStatus.SUCCEEDED
    assert job.finished_at == mocked_now
    assert job.output_dataset_version == output_dataset_version


def test_job_transition_running_to_failed(job_service: JobService) -> None:
    mocked_now = SimpleNamespace()
    job = SimpleNamespace(status=ExternalDatasetJobStatus.RUNNING, save=MagicMock())
    with patch(
        "resource_registry.services.registry.job_service.timezone.now", return_value=mocked_now
    ):
        job_service.fail_job(job=job, error_code="TIMEOUT", error_message="Timed out")
    assert job.status == ExternalDatasetJobStatus.FAILED
    assert job.finished_at == mocked_now
    assert job.error_code == "TIMEOUT"


def test_job_transition_failed_to_retry(job_service: JobService) -> None:
    job = SimpleNamespace(
        status=ExternalDatasetJobStatus.FAILED,
        retry_count=0,
        started_at=SimpleNamespace(),
        finished_at=SimpleNamespace(),
        save=MagicMock(),
    )
    job_service.retry_job(job=job)
    assert job.status == ExternalDatasetJobStatus.QUEUED
    assert job.retry_count == RETRY_INCREMENT
    assert job.started_at is None
    assert job.finished_at is None


def test_job_invalid_transition_rejected(job_service: JobService) -> None:
    job = SimpleNamespace(status=ExternalDatasetJobStatus.SUCCEEDED, save=MagicMock())
    with pytest.raises(InvalidJobTransitionError):
        job_service.start_job(job=job)
