"""Runtime Adoption Phase 5 worker execution-authority tests."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from resource_registry.authorization.submission_authority import (
    SUBMISSION_AUTHORITY_METADATA_KEY,
    SubmissionAuthorityEnvelope,
    SubmissionAuthorityEnvelopeBuilder,
)
from resource_registry.authorization.worker_execution_authority import (
    WorkerExecutionAuthorityError,
    WorkerExecutionAuthorityService,
    WorkerExecutionContext,
)
from resource_registry.models import (
    DatasetIntelligenceJob,
    DatasetIntelligenceJobOperation,
    DatasetIntelligenceJobStatus,
    DatasetQueryJob,
    DatasetQueryJobStatus,
)
from resource_registry.read_auth.permissions import ReadAuthorizationService
from resource_registry.services.dataset_intelligence_job_service import DatasetIntelligenceJobService
from resource_registry.services.dataset_intelligence_job_worker_runner import (
    DatasetIntelligenceJobWorkerRunner,
)
from resource_registry.services.dataset_query_job_service import DatasetQueryJobService
from resource_registry.services.dataset_query_job_worker_runner import DatasetQueryJobWorkerRunner

_NAMESPACE_ID = "11111111-1111-1111-1111-111111111111"
_DATASET_ID = "22222222-2222-2222-2222-222222222222"
_VERSION_ID = "33333333-3333-3333-3333-333333333333"
_WORKBOOK_GROUP_ID = "44444444-4444-4444-4444-444444444444"
_JOB_ID = "66666666-6666-6666-6666-666666666666"
_ORGANIZATION_ID = 10
_WORKSPACE_ID = 20
_ACTOR_ID = 30
_ROOT = Path(__file__).resolve().parents[2]


def _authority(
    *,
    operation: str = "resource_registry_start_query_job",
    resource_scope: dict[str, list[str]] | None = None,
) -> SubmissionAuthorityEnvelope:
    return SubmissionAuthorityEnvelopeBuilder().build(
        namespace_id=_NAMESPACE_ID,
        organization_id=_ORGANIZATION_ID,
        workspace_id=_WORKSPACE_ID,
        actor_id=_ACTOR_ID,
        operation=operation,
        resource_scope=(
            resource_scope
            if resource_scope is not None
            else {"dataset_ids": [_DATASET_ID], "dataset_version_ids": [_VERSION_ID]}
        ),
        correlation_id="phase-5-correlation",
        submitted_at=datetime(2026, 7, 19, tzinfo=UTC),
    )


def _query_job(
    *,
    authority: SubmissionAuthorityEnvelope | None = None,
    tenant_id: str = _NAMESPACE_ID,
    dataset_id: str = _DATASET_ID,
    version_id: str | None = _VERSION_ID,
    actor_id: int | None = _ACTOR_ID,
) -> DatasetQueryJob:
    active_authority = authority or _authority()
    fingerprint = DatasetQueryJobService()._build_request_fingerprint(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        version_id=version_id,
        sql_text="SELECT spend FROM dataset",
        authority_scope=active_authority.idempotency_scope(),
    )
    return cast(
        DatasetQueryJob,
        SimpleNamespace(
            id=_JOB_ID,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            version_id=version_id,
            sql_text="SELECT spend FROM dataset",
            created_by_id=actor_id,
            request_fingerprint=fingerprint,
            metadata_json={SUBMISSION_AUTHORITY_METADATA_KEY: active_authority.to_dict()},
            status=DatasetQueryJobStatus.RUNNING,
            error_code=None,
            execution_ms=0,
        ),
    )


def _intelligence_job(
    *,
    operation: str,
    scope_type: str = "dataset",
    scope_id: str = _DATASET_ID,
) -> DatasetIntelligenceJob:
    resource_scope: dict[str, list[str]] = {}
    dataset_id: str | None = None
    normalized_scope_type = scope_type.lower()
    if normalized_scope_type == "dataset":
        dataset_id = scope_id
        resource_scope["dataset_ids"] = [scope_id]
    elif normalized_scope_type == "workbook_group":
        resource_scope["workbook_group_ids"] = [scope_id]
    authority = _authority(
        operation=f"resource_registry_start_intelligence_job:{operation}",
        resource_scope=resource_scope,
    )
    job_spec = (
        {"inputs": {"scope": {"type": scope_type, "id": scope_id}}}
        if operation == DatasetIntelligenceJobOperation.TRANSFORMATION
        else {}
    )
    fingerprint = DatasetIntelligenceJobService()._build_request_fingerprint(
        tenant_id=_NAMESPACE_ID,
        operation=operation,
        request_payload={},
        job_spec=job_spec,
        dataset_id=dataset_id,
        version_id=None,
        export_plan_id=None,
        authority_scope=authority.idempotency_scope(),
    )
    return cast(
        DatasetIntelligenceJob,
        SimpleNamespace(
            id=_JOB_ID,
            tenant_id=_NAMESPACE_ID,
            operation=operation,
            dataset_id=dataset_id,
            version_id=None,
            export_plan_id=None,
            request_payload_json={},
            job_spec_json=job_spec,
            created_by_id=_ACTOR_ID,
            request_fingerprint=fingerprint,
            metadata_json={SUBMISSION_AUTHORITY_METADATA_KEY: authority.to_dict()},
        ),
    )


@pytest.fixture(autouse=True)
def trusted_namespace_provenance() -> Iterator[MagicMock]:
    with patch(
        "resource_registry.authorization.worker_execution_authority." "RegistryNamespace.objects.filter"
    ) as filter_mock:
        filter_mock.return_value.values_list.return_value.first.return_value = (
            _WORKSPACE_ID,
            _ORGANIZATION_ID,
        )
        yield filter_mock


def test_valid_query_authority_builds_bounded_non_admin_context() -> None:
    execution = WorkerExecutionAuthorityService().build_query_context(job=_query_job())
    context = execution.to_read_authorization_context()

    assert execution.original_actor_id == _ACTOR_ID
    assert context.tenant_id == _NAMESPACE_ID
    assert context.actor == f"user:{_ACTOR_ID}"
    assert context.roles == ("reader",)
    assert context.allowed_dataset_ids == frozenset({_DATASET_ID})
    assert "admin" not in context.roles


def test_worker_execution_context_rejects_forged_source_and_mutable_scope() -> None:
    execution = WorkerExecutionAuthorityService().build_query_context(job=_query_job())

    with pytest.raises(ValueError, match="authority source"):
        replace(execution, authority_source="caller_metadata")
    with pytest.raises(ValueError, match="deeply immutable"):
        WorkerExecutionContext(
            namespace_id=execution.namespace_id,
            organization_id=execution.organization_id,
            workspace_id=execution.workspace_id,
            original_actor_id=execution.original_actor_id,
            actor_type=execution.actor_type,
            operation=execution.operation,
            resource_scope=[["dataset_ids", [_DATASET_ID]]],  # type: ignore[arg-type]
            policy_revisions=(),
            job_id=execution.job_id,
            correlation_id=execution.correlation_id,
            authority_schema_version=execution.authority_schema_version,
        )


def test_exact_phase_5_worker_allowlist_and_dependency_boundaries() -> None:
    query_worker = _ROOT / "resource_registry/services/dataset_query_job_worker_runner.py"
    intelligence_worker = _ROOT / "resource_registry/services/dataset_intelligence_job_worker_runner.py"
    transformation_attempt = (
        _ROOT / "resource_registry/transformations/execution/attempt_persistence.py"
    )
    transformation_finalization = (
        _ROOT / "resource_registry/services/transformation_output_finalization_job_service.py"
    )
    adopted_sources = (
        query_worker.read_text(encoding="utf-8"),
        intelligence_worker.read_text(encoding="utf-8"),
        transformation_attempt.read_text(encoding="utf-8"),
        transformation_finalization.read_text(encoding="utf-8"),
    )

    assert "WorkerExecutionAuthorityService" in adopted_sources[0]
    assert "WorkerExecutionAuthorityService" in adopted_sources[1]
    assert "validate_transformation_authorization_snapshot" in adopted_sources[2]
    assert "validate_transformation_authorization_snapshot" in adopted_sources[3]
    for source in adopted_sources:
        assert "ReadOnlyBifNamespaceAdmissionService" not in source
        assert "context_manager" not in source
        assert "Persona" not in source
    assert 'roles=("admin",)' not in adopted_sources[0]
    assert 'roles=("admin",)' not in adopted_sources[1]

    for excluded_path in (
        _ROOT / "resource_registry/services/workbook_ingestion_job_worker_runner.py",
        _ROOT / "resource_registry/providers/services.py",
    ):
        excluded_source = excluded_path.read_text(encoding="utf-8")
        assert "WorkerExecutionAuthorityService" not in excluded_source
        assert "validate_transformation_authorization_snapshot" not in excluded_source


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    (
        ("missing", "worker_authority_missing"),
        ("schema", "worker_authority_unsupported_schema"),
        ("namespace", "worker_authority_namespace_mismatch"),
        ("actor", "worker_authority_actor_mismatch"),
        ("operation", "worker_authority_operation_mismatch"),
        ("scope", "worker_authority_scope_mismatch"),
        ("fingerprint", "worker_authority_fingerprint_mismatch"),
    ),
)
def test_query_authority_mismatches_fail_closed(mutation: str, expected_code: str) -> None:
    job = _query_job()
    if mutation == "missing":
        job.metadata_json = {}
    elif mutation == "schema":
        assert isinstance(job.metadata_json, dict)
        authority_metadata = job.metadata_json[SUBMISSION_AUTHORITY_METADATA_KEY]
        assert isinstance(authority_metadata, dict)
        authority_metadata["schema_version"] = 999
    elif mutation == "namespace":
        job.tenant_id = uuid4()
    elif mutation == "actor":
        job.created_by_id = _ACTOR_ID + 1
    elif mutation == "operation":
        assert isinstance(job.metadata_json, dict)
        authority_metadata = job.metadata_json[SUBMISSION_AUTHORITY_METADATA_KEY]
        assert isinstance(authority_metadata, dict)
        authority_metadata["operation"] = "other"
    elif mutation == "scope":
        job.dataset_id = uuid4()
    elif mutation == "fingerprint":
        job.request_fingerprint = "0" * 64

    with pytest.raises(WorkerExecutionAuthorityError) as captured:
        WorkerExecutionAuthorityService().build_query_context(job=job)

    assert captured.value.code == expected_code


@pytest.mark.parametrize("mutation", ("extra", "malformed_scope"))
def test_authority_deserialization_rejects_extra_or_malformed_fields(mutation: str) -> None:
    job = _query_job()
    assert isinstance(job.metadata_json, dict)
    authority_metadata = job.metadata_json[SUBMISSION_AUTHORITY_METADATA_KEY]
    assert isinstance(authority_metadata, dict)
    if mutation == "extra":
        authority_metadata["caller_authority"] = True
    else:
        authority_metadata["resource_scope"] = {"dataset_ids": {"mutable": True}}

    with pytest.raises(WorkerExecutionAuthorityError) as captured:
        WorkerExecutionAuthorityService().build_query_context(job=job)

    assert captured.value.code == "worker_authority_malformed"


def test_workspace_and_organization_provenance_are_verified(
    trusted_namespace_provenance: MagicMock,
) -> None:
    trusted_namespace_provenance.return_value.values_list.return_value.first.return_value = (
        _WORKSPACE_ID + 1,
        _ORGANIZATION_ID,
    )
    with pytest.raises(WorkerExecutionAuthorityError) as workspace_error:
        WorkerExecutionAuthorityService().build_query_context(job=_query_job())
    assert workspace_error.value.code == "worker_authority_workspace_mismatch"

    trusted_namespace_provenance.return_value.values_list.return_value.first.return_value = (
        _WORKSPACE_ID,
        _ORGANIZATION_ID + 1,
    )
    with pytest.raises(WorkerExecutionAuthorityError) as organization_error:
        WorkerExecutionAuthorityService().build_query_context(job=_query_job())
    assert organization_error.value.code == "worker_authority_organization_mismatch"


def test_workbook_group_scope_is_case_insensitive_and_bounded() -> None:
    job = _intelligence_job(
        operation=DatasetIntelligenceJobOperation.TRANSFORMATION,
        scope_type="WORKBOOK_GROUP",
        scope_id=_WORKBOOK_GROUP_ID,
    )

    execution = WorkerExecutionAuthorityService().build_intelligence_context(job=job)

    assert execution.resource_ids("workbook_group_ids") == frozenset({_WORKBOOK_GROUP_ID})
    assert execution.resource_ids("dataset_ids") == frozenset()


def test_query_result_empty_scope_never_grants_dataset_access() -> None:
    job = _intelligence_job(
        operation=DatasetIntelligenceJobOperation.TRANSFORMATION,
        scope_type="query_result",
        scope_id="",
    )

    context = (
        WorkerExecutionAuthorityService()
        .build_intelligence_context(job=job)
        .to_read_authorization_context()
    )

    assert context.allowed_dataset_ids == frozenset()
    assert context.roles == ("reader",)
    assert (
        ReadAuthorizationService().filter_allowed_dataset_ids(
            context,
            [_DATASET_ID],
        )
        == []
    )


def test_analysis_missing_required_dataset_fails_before_execution() -> None:
    job = _intelligence_job(operation=DatasetIntelligenceJobOperation.ANALYSIS)
    job.dataset_id = None
    assert isinstance(job.metadata_json, dict)
    authority = SubmissionAuthorityEnvelope.from_dict(
        job.metadata_json[SUBMISSION_AUTHORITY_METADATA_KEY]
    )
    job.request_fingerprint = DatasetIntelligenceJobService()._build_request_fingerprint(
        tenant_id=_NAMESPACE_ID,
        operation=DatasetIntelligenceJobOperation.ANALYSIS,
        request_payload={},
        job_spec={},
        dataset_id=None,
        version_id=None,
        export_plan_id=None,
        authority_scope=authority.idempotency_scope(),
    )
    job.status = DatasetIntelligenceJobStatus.RUNNING
    job.error_code = None
    job.execution_ms = 0
    job_service = MagicMock()
    job_service.claim_queued_jobs.return_value = [job]
    job_service.mark_failed.return_value = job
    execution_service = MagicMock()
    runner = DatasetIntelligenceJobWorkerRunner(
        job_service=job_service,
        execution_service=execution_service,
        authority_service=WorkerExecutionAuthorityService(),
        stale_running_seconds=0,
    )

    assert runner.run_once() == 1
    execution_service.execute_job.assert_not_called()
    failure = job_service.mark_failed.call_args.kwargs
    assert failure["error_code"] == "worker_authority_scope_mismatch"
    assert failure["retryable"] is False


@pytest.mark.parametrize("job_kind", ("query", "intelligence"))
def test_valid_persisted_authority_reaches_execution_service(job_kind: str) -> None:
    job_service = MagicMock()
    execution_service = MagicMock()
    job: DatasetQueryJob | DatasetIntelligenceJob
    runner: DatasetQueryJobWorkerRunner | DatasetIntelligenceJobWorkerRunner
    if job_kind == "query":
        job = _query_job()
        job_service.claim_queued_jobs.return_value = [job]
        execution_service.execute_job.return_value = job
        runner = DatasetQueryJobWorkerRunner(
            query_job_service=job_service,
            execution_service=execution_service,
            authority_service=WorkerExecutionAuthorityService(),
            stale_running_seconds=0,
        )
    else:
        job = _intelligence_job(operation=DatasetIntelligenceJobOperation.ANALYSIS)
        job.status = DatasetIntelligenceJobStatus.RUNNING
        job.error_code = None
        job.execution_ms = 0
        job_service.claim_queued_jobs.return_value = [job]
        execution_service.execute_job.return_value = job
        runner = DatasetIntelligenceJobWorkerRunner(
            job_service=job_service,
            execution_service=execution_service,
            authority_service=WorkerExecutionAuthorityService(),
            stale_running_seconds=0,
        )

    assert runner.run_once() == 1
    execution_service.execute_job.assert_called_once()
    context = execution_service.execute_job.call_args.kwargs["context"]
    assert context.tenant_id == _NAMESPACE_ID
    assert context.roles == ("reader",)


def test_missing_authority_is_terminal_before_execution() -> None:
    job = _query_job()
    job.metadata_json = {}
    job_service = MagicMock()
    job_service.claim_queued_jobs.return_value = [job]
    job_service.mark_failed.return_value = job
    execution_service = MagicMock()
    runner = DatasetQueryJobWorkerRunner(
        query_job_service=job_service,
        execution_service=execution_service,
        authority_service=WorkerExecutionAuthorityService(),
        stale_running_seconds=0,
    )

    assert runner.run_once() == 1
    execution_service.execute_job.assert_not_called()
    failure = job_service.mark_failed.call_args.kwargs
    assert failure["error_code"] == "worker_authority_missing"
    assert failure["retryable"] is False
