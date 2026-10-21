"""Runtime Adoption Phase 4 asynchronous submission boundary tests."""

from __future__ import annotations

import ast
import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock, patch

import pytest

from BotServer.builtin_functions import resource_registry_functions
from resource_registry.authorization.submission_authority import (
    SUBMISSION_AUTHORITY_METADATA_KEY,
    SubmissionAuthorityEnvelope,
    SubmissionAuthorityEnvelopeBuilder,
)
from resource_registry.bifs.read_only_namespace_admission import AdmittedBifNamespace
from resource_registry.models import (
    DatasetIntelligenceJob,
    DatasetIntelligenceJobStatus,
    DatasetQueryJob,
    DatasetQueryJobStatus,
)
from resource_registry.read_auth.exceptions import DataRegistryAuthorizationError
from resource_registry.services.async_job_idempotency import build_async_job_request_fingerprint
from resource_registry.services.dataset_intelligence_job_service import DatasetIntelligenceJobService
from resource_registry.services.dataset_query_job_service import DatasetQueryJobService

_ROOT = Path(__file__).resolve().parents[2]
_DATASET_BIF_MODULE = _ROOT / "BotServer/builtin_functions/resource_registry_functions.py"
_TRANSFORMATION_HANDLER_MODULE = _ROOT / "resource_registry/bifs/transformation/handlers.py"
_TRANSFORMATION_CONTEXT_MODULE = _ROOT / "resource_registry/bifs/transformation/context.py"
_NAMESPACE_ID = "11111111-1111-1111-1111-111111111111"
_SELECTOR_ID = "22222222-2222-2222-2222-222222222222"
_DATASET_ID = "33333333-3333-3333-3333-333333333333"
_VERSION_ID = "44444444-4444-4444-4444-444444444444"
_CORRELATION_ID = "phase-4-correlation"
_ORGANIZATION_ID = 10
_WORKSPACE_ID = 20
_ACTOR_ID = 30
_HTTP_FORBIDDEN = 403
_PHASE_4_DATASET_SUBMISSIONS = frozenset(
    {"resource_registry_start_query_job", "resource_registry_start_intelligence_job"}
)
_PHASE_4_SUBMISSIONS = _PHASE_4_DATASET_SUBMISSIONS | {"resource_registry_start_transformation_run"}


class _DenyingAdmissionService:
    """Deny provenance admission and record calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[object, object]] = []

    def admit_namespace_with_provenance(
        self,
        *,
        request: object,
        requested_namespace_id: object,
    ) -> AdmittedBifNamespace:
        """Reject before submission authorization or persistence."""
        self.calls.append((request, requested_namespace_id))
        raise DataRegistryAuthorizationError("The selected tenant_id is not authorized.")


def _admission() -> AdmittedBifNamespace:
    return AdmittedBifNamespace(
        namespace_id=_NAMESPACE_ID,
        organization_id=_ORGANIZATION_ID,
        workspace_id=_WORKSPACE_ID,
        actor_id=_ACTOR_ID,
    )


def _authority(*, actor_id: int = _ACTOR_ID) -> SubmissionAuthorityEnvelope:
    return SubmissionAuthorityEnvelopeBuilder().build(
        namespace_id=_NAMESPACE_ID,
        organization_id=_ORGANIZATION_ID,
        workspace_id=_WORKSPACE_ID,
        actor_id=actor_id,
        operation="resource_registry_start_query_job",
        resource_scope={"dataset_ids": [_DATASET_ID], "dataset_version_ids": [_VERSION_ID]},
        correlation_id=_CORRELATION_ID,
        submitted_at=datetime(2026, 7, 19, tzinfo=UTC),
    )


def _module_functions(path: Path) -> dict[str, ast.FunctionDef]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}


def _calls_name(function: ast.FunctionDef, name: str) -> bool:
    return any(
        isinstance(call, ast.Call)
        and (
            (isinstance(call.func, ast.Name) and call.func.id == name)
            or (isinstance(call.func, ast.Attribute) and call.func.attr == name)
        )
        for call in ast.walk(function)
    )


def test_exact_phase_4_submission_allowlist_and_exclusions() -> None:
    functions = _module_functions(_DATASET_BIF_MODULE)
    adopted = {
        name
        for name, function in functions.items()
        if _calls_name(function, "_admit_async_submission_namespace")
    }
    transformation_functions = _module_functions(_TRANSFORMATION_HANDLER_MODULE)
    transformation_context = _TRANSFORMATION_CONTEXT_MODULE.read_text(encoding="utf-8")

    if _calls_name(
        transformation_functions["resource_registry_start_transformation_run"],
        "build_execution_context",
    ):
        adopted.add("resource_registry_start_transformation_run")

    assert adopted == _PHASE_4_SUBMISSIONS
    assert "_admit_submission_namespace" in transformation_context
    assert "_resolve_tenant_id(" not in transformation_context
    assert "WorkbookIngestionJobService" not in _TRANSFORMATION_HANDLER_MODULE.read_text(
        encoding="utf-8"
    )


def test_workers_do_not_consume_request_time_admission() -> None:
    worker_paths = tuple((_ROOT / "resource_registry/services").glob("*worker*.py")) + tuple(
        (_ROOT / "resource_registry/management/commands").glob("run_*jobs.py")
    )

    for path in worker_paths:
        source = path.read_text(encoding="utf-8")
        assert "ReadOnlyBifNamespaceAdmissionService" not in source
        assert "admit_namespace_with_provenance" not in source


@pytest.mark.parametrize(
    ("function_name", "function_arguments"),
    (
        (
            "resource_registry_start_query_job",
            {"dataset_id": _DATASET_ID, "sql": "SELECT spend FROM dataset LIMIT 1"},
        ),
        (
            "resource_registry_start_intelligence_job",
            {
                "operation": "analysis",
                "payload": {"dataset_id": _DATASET_ID, "analysis_type": "summary"},
            },
        ),
    ),
)
def test_admission_denial_prevents_job_creation_and_every_downstream_call(
    monkeypatch: pytest.MonkeyPatch,
    function_name: str,
    function_arguments: dict[str, object],
) -> None:
    request = SimpleNamespace(user=SimpleNamespace(pk=30), session={})
    admission_service = _DenyingAdmissionService()

    def fail_if_reached(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("Submission dependency was reached after admission denial.")

    monkeypatch.setattr(
        resource_registry_functions,
        "_get_read_only_namespace_admission_service",
        lambda: admission_service,
    )
    for dependency_name in (
        "_assert_capability",
        "_build_authorization_context",
        "_get_read_facade",
        "_validate_sql_for_query_job",
        "_get_query_job_service",
        "_get_intelligence_job_service",
        "_get_export_plan_for_tenant",
        "_get_submission_authority_builder",
    ):
        monkeypatch.setattr(resource_registry_functions, dependency_name, fail_if_reached)

    response = json.loads(
        getattr(resource_registry_functions, function_name)(
            request,
            None,
            tenant_id=_SELECTOR_ID,
            **function_arguments,
        )
    )

    assert response["success"] is False
    assert response["status"] == _HTTP_FORBIDDEN
    assert response["code"] == "authorization_error"
    assert admission_service.calls == [(request, _SELECTOR_ID)]


@pytest.mark.parametrize(
    ("function_name", "function_arguments", "expected_operation"),
    (
        (
            "resource_registry_start_query_job",
            {
                "dataset_id": _DATASET_ID,
                "version_id": _VERSION_ID,
                "sql": "SELECT spend FROM dataset LIMIT 1",
            },
            "resource_registry_start_query_job",
        ),
        (
            "resource_registry_start_intelligence_job",
            {
                "operation": "analysis",
                "payload": {
                    "dataset_id": _DATASET_ID,
                    "version_id": _VERSION_ID,
                    "analysis_type": "summary",
                },
            },
            "resource_registry_start_intelligence_job:analysis",
        ),
    ),
)
def test_submission_forwards_canonical_namespace_and_immutable_authority(
    monkeypatch: pytest.MonkeyPatch,
    function_name: str,
    function_arguments: dict[str, object],
    expected_operation: str,
) -> None:
    request = SimpleNamespace(
        user=SimpleNamespace(pk=30),
        session={},
        correlation_id=_CORRELATION_ID,
    )
    job_service = Mock()
    job_service.create_job.return_value = SimpleNamespace(
        id=_DATASET_ID,
        status="QUEUED",
        operation="analysis",
    )
    facade = Mock()
    facade.get_dataset.return_value = {"id": _DATASET_ID}

    monkeypatch.setattr(
        resource_registry_functions,
        "_admit_async_submission_namespace",
        lambda *_args: _admission(),
    )
    monkeypatch.setattr(
        resource_registry_functions,
        "_assert_capability",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        resource_registry_functions,
        "_build_authorization_context",
        lambda _request, tenant_id: SimpleNamespace(tenant_id=tenant_id),
    )
    monkeypatch.setattr(resource_registry_functions, "_get_read_facade", lambda: facade)
    monkeypatch.setattr(
        resource_registry_functions,
        "_validate_sql_for_query_job",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(resource_registry_functions, "_get_query_job_service", lambda: job_service)
    monkeypatch.setattr(
        resource_registry_functions,
        "_get_intelligence_job_service",
        lambda: job_service,
    )

    response = json.loads(
        getattr(resource_registry_functions, function_name)(
            request,
            None,
            tenant_id=_SELECTOR_ID,
            **function_arguments,
        )
    )

    assert response["success"] is True
    create_kwargs = job_service.create_job.call_args.kwargs
    authority = create_kwargs["submission_authority"]
    assert create_kwargs["tenant_id"] == _NAMESPACE_ID
    assert isinstance(authority, SubmissionAuthorityEnvelope)
    assert authority.namespace_id == _NAMESPACE_ID
    assert authority.organization_id == _ORGANIZATION_ID
    assert authority.workspace_id == _WORKSPACE_ID
    assert authority.actor_id == _ACTOR_ID
    assert authority.operation == expected_operation
    assert dict(authority.resource_scope) == {
        "dataset_ids": (_DATASET_ID,),
        "dataset_version_ids": (_VERSION_ID,),
    }
    with pytest.raises(FrozenInstanceError):
        authority.actor_id = 99  # type: ignore[misc]


def test_authority_serialization_is_deterministic_and_secret_free() -> None:
    authority = _authority()

    assert authority.to_dict() == authority.to_dict()
    assert authority.to_dict()["schema_version"] == 1
    assert authority.to_dict()["actor"] == {"type": "user", "id": _ACTOR_ID}
    assert SUBMISSION_AUTHORITY_METADATA_KEY not in authority.to_dict()
    serialized = json.dumps(authority.to_dict(), sort_keys=True)
    assert "request" not in serialized
    assert "session" not in serialized
    assert "persona" not in serialized
    assert "token" not in serialized
    assert "secret" not in serialized


def test_direct_authority_construction_rejects_unsupported_schema_version() -> None:
    with pytest.raises(ValueError, match="schema_version is unsupported"):
        SubmissionAuthorityEnvelope(
            namespace_id=_NAMESPACE_ID,
            organization_id=_ORGANIZATION_ID,
            workspace_id=_WORKSPACE_ID,
            actor_id=_ACTOR_ID,
            operation="resource_registry_start_query_job",
            resource_scope=(("dataset_ids", (_DATASET_ID,)),),
            policy_revisions=(),
            correlation_id=_CORRELATION_ID,
            submitted_at=datetime(2026, 7, 19, tzinfo=UTC),
            schema_version=999,
        )


def test_direct_authority_construction_rejects_mutable_nested_scope() -> None:
    with pytest.raises(ValueError, match="resource_scope must be immutable"):
        SubmissionAuthorityEnvelope(
            namespace_id=_NAMESPACE_ID,
            organization_id=_ORGANIZATION_ID,
            workspace_id=_WORKSPACE_ID,
            actor_id=_ACTOR_ID,
            operation="resource_registry_start_query_job",
            resource_scope=[["dataset_ids", [_DATASET_ID]]],  # type: ignore[arg-type]
            policy_revisions=(),
            correlation_id=_CORRELATION_ID,
            submitted_at=datetime(2026, 7, 19, tzinfo=UTC),
        )


def test_workbook_transformation_scope_is_canonical_and_case_insensitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = SimpleNamespace(
        user=SimpleNamespace(pk=_ACTOR_ID),
        session={},
        correlation_id=_CORRELATION_ID,
    )
    workbook_group_id = "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA"
    job_service = Mock()
    job_service.create_job.return_value = SimpleNamespace(
        id=_DATASET_ID,
        status="QUEUED",
        operation="transformation",
    )
    read_facade = Mock()
    authorization_context = SimpleNamespace(tenant_id=_NAMESPACE_ID)

    monkeypatch.setattr(
        resource_registry_functions,
        "_admit_async_submission_namespace",
        lambda *_args: _admission(),
    )
    monkeypatch.setattr(
        resource_registry_functions,
        "_assert_capability",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        resource_registry_functions,
        "_build_authorization_context",
        lambda _request, _tenant_id: authorization_context,
    )
    monkeypatch.setattr(resource_registry_functions, "_get_read_facade", lambda: read_facade)
    monkeypatch.setattr(
        resource_registry_functions,
        "_get_intelligence_job_service",
        lambda: job_service,
    )

    response = json.loads(
        resource_registry_functions.resource_registry_start_intelligence_job(  # type: ignore[no-untyped-call]
            request,
            None,
            tenant_id=_SELECTOR_ID,
            operation="transformation",
            payload={
                "job_spec": {
                    "inputs": {
                        "scope": {"type": "WORKBOOK_GROUP", "id": workbook_group_id},
                    },
                },
            },
        )
    )

    assert response["success"] is True
    read_facade.get_workbook_context.assert_called_once_with(
        authorization_context,
        workbook_group_id,
    )
    authority = job_service.create_job.call_args.kwargs["submission_authority"]
    assert dict(authority.resource_scope) == {
        "workbook_group_ids": (workbook_group_id.lower(),),
    }


def test_authority_idempotency_scope_is_actor_and_namespace_bound() -> None:
    first = _authority(actor_id=_ACTOR_ID)
    second = _authority(actor_id=_ACTOR_ID + 1)

    assert first.idempotency_scope() != second.idempotency_scope()
    assert "submitted_at" not in first.idempotency_scope()
    assert "correlation_id" not in first.idempotency_scope()


def test_query_retry_status_update_preserves_submission_authority() -> None:
    persisted_authority = _authority().to_dict()
    save_calls: list[dict[str, object]] = []
    job = SimpleNamespace(
        status=DatasetQueryJobStatus.RUNNING,
        retry_count=0,
        cancellation_requested_at=None,
        metadata_json={SUBMISSION_AUTHORITY_METADATA_KEY: persisted_authority},
    )
    job.save = lambda **kwargs: save_calls.append(kwargs)

    DatasetQueryJobService().mark_failed(
        job=cast(DatasetQueryJob, job),
        error_code="retry",
        error_message="retry",
        retryable=True,
    )

    assert job.metadata_json[SUBMISSION_AUTHORITY_METADATA_KEY] == persisted_authority
    update_fields = save_calls[0]["update_fields"]
    assert isinstance(update_fields, list)
    assert "metadata_json" not in update_fields


def test_intelligence_retry_status_update_preserves_submission_authority() -> None:
    persisted_authority = _authority().to_dict()
    save_calls: list[dict[str, object]] = []
    job = SimpleNamespace(
        status=DatasetIntelligenceJobStatus.RUNNING,
        retry_count=0,
        cancellation_requested_at=None,
        metadata_json={SUBMISSION_AUTHORITY_METADATA_KEY: persisted_authority},
    )
    job.save = lambda **kwargs: save_calls.append(kwargs)

    DatasetIntelligenceJobService().mark_failed(
        job=cast(DatasetIntelligenceJob, job),
        error_code="retry",
        error_message="retry",
        retryable=True,
    )

    assert job.metadata_json[SUBMISSION_AUTHORITY_METADATA_KEY] == persisted_authority
    update_fields = save_calls[0]["update_fields"]
    assert isinstance(update_fields, list)
    assert "metadata_json" not in update_fields


def test_query_job_service_persists_authority_and_binds_idempotency_to_actor() -> None:
    first_authority = _authority(actor_id=_ACTOR_ID)
    second_authority = _authority(actor_id=_ACTOR_ID + 1)
    service = DatasetQueryJobService()
    first_fingerprint = service._build_request_fingerprint(
        tenant_id=_NAMESPACE_ID,
        dataset_id=_DATASET_ID,
        version_id=_VERSION_ID,
        sql_text="SELECT spend FROM dataset LIMIT 1",
        authority_scope=first_authority.idempotency_scope(),
    )
    second_fingerprint = service._build_request_fingerprint(
        tenant_id=_NAMESPACE_ID,
        dataset_id=_DATASET_ID,
        version_id=_VERSION_ID,
        sql_text="SELECT spend FROM dataset LIMIT 1",
        authority_scope=second_authority.idempotency_scope(),
    )

    assert first_fingerprint != second_fingerprint
    with (
        patch(
            "resource_registry.services.dataset_query_job_service.DatasetQueryJob.objects.create",
            return_value=SimpleNamespace(id=_DATASET_ID),
        ) as create_mock,
        patch.object(service, "_find_active_job_by_fingerprint", return_value=None),
        patch("resource_registry.services.dataset_query_job_service.transaction.atomic"),
    ):
        service.create_job(
            tenant_id=_NAMESPACE_ID,
            dataset_id=_DATASET_ID,
            version_id=_VERSION_ID,
            sql_text="SELECT spend FROM dataset LIMIT 1",
            submission_authority=first_authority,
        )

    persisted_authority = create_mock.call_args.kwargs["metadata_json"][
        SUBMISSION_AUTHORITY_METADATA_KEY
    ]
    assert persisted_authority == first_authority.to_dict()


def test_intelligence_job_service_persists_authority_and_binds_idempotency_to_actor() -> None:
    first_authority = _authority(actor_id=_ACTOR_ID)
    second_authority = _authority(actor_id=_ACTOR_ID + 1)
    service = DatasetIntelligenceJobService()
    first_fingerprint = service._build_request_fingerprint(
        tenant_id=_NAMESPACE_ID,
        operation="analysis",
        request_payload={"dataset_id": _DATASET_ID, "analysis_type": "summary"},
        job_spec={},
        dataset_id=_DATASET_ID,
        version_id=_VERSION_ID,
        export_plan_id=None,
        authority_scope=first_authority.idempotency_scope(),
    )
    second_fingerprint = service._build_request_fingerprint(
        tenant_id=_NAMESPACE_ID,
        operation="analysis",
        request_payload={"dataset_id": _DATASET_ID, "analysis_type": "summary"},
        job_spec={},
        dataset_id=_DATASET_ID,
        version_id=_VERSION_ID,
        export_plan_id=None,
        authority_scope=second_authority.idempotency_scope(),
    )

    assert first_fingerprint != second_fingerprint
    with (
        patch(
            "resource_registry.services.dataset_intelligence_job_service."
            "DatasetIntelligenceJob.objects.create",
            return_value=SimpleNamespace(id=_DATASET_ID),
        ) as create_mock,
        patch.object(service, "_find_active_job_by_fingerprint", return_value=None),
        patch("resource_registry.services.dataset_intelligence_job_service.transaction.atomic"),
    ):
        service.create_job(
            tenant_id=_NAMESPACE_ID,
            operation="analysis",
            request_payload={"dataset_id": _DATASET_ID, "analysis_type": "summary"},
            dataset_id=_DATASET_ID,
            version_id=_VERSION_ID,
            submission_authority=first_authority,
        )

    persisted_authority = create_mock.call_args.kwargs["metadata_json"][
        SUBMISSION_AUTHORITY_METADATA_KEY
    ]
    assert persisted_authority == first_authority.to_dict()


def test_legacy_job_fingerprints_remain_unchanged_without_submission_authority() -> None:
    query_fingerprint = DatasetQueryJobService()._build_request_fingerprint(
        tenant_id=_NAMESPACE_ID,
        dataset_id=_DATASET_ID,
        version_id=_VERSION_ID,
        sql_text="SELECT spend FROM dataset LIMIT 1",
    )
    intelligence_payload = {"dataset_id": _DATASET_ID, "analysis_type": "summary"}
    intelligence_fingerprint = DatasetIntelligenceJobService()._build_request_fingerprint(
        tenant_id=_NAMESPACE_ID,
        operation="analysis",
        request_payload=intelligence_payload,
        job_spec={},
        dataset_id=_DATASET_ID,
        version_id=_VERSION_ID,
        export_plan_id=None,
    )

    assert query_fingerprint == build_async_job_request_fingerprint(
        {
            "job_family": "dataset_query_job",
            "tenant_id": _NAMESPACE_ID,
            "dataset_id": _DATASET_ID,
            "version_id": _VERSION_ID,
            "sql_text": "SELECT spend FROM dataset LIMIT 1",
        }
    )
    assert intelligence_fingerprint == build_async_job_request_fingerprint(
        {
            "job_family": "dataset_intelligence_job",
            "tenant_id": _NAMESPACE_ID,
            "operation": "analysis",
            "dataset_id": _DATASET_ID,
            "version_id": _VERSION_ID,
            "export_plan_id": None,
            "request_payload": intelligence_payload,
            "job_spec": {},
        }
    )


def test_same_actor_can_safely_reuse_an_active_legacy_query_job() -> None:
    service = DatasetQueryJobService()
    legacy_job = SimpleNamespace(id=_DATASET_ID, created_by_id=_ACTOR_ID)
    with (
        patch.object(
            service,
            "_find_active_job_by_fingerprint",
            side_effect=(None, legacy_job),
        ),
        patch(
            "resource_registry.services.dataset_query_job_service.DatasetQueryJob.objects.create",
        ) as create_mock,
        patch("resource_registry.services.dataset_query_job_service.transaction.atomic"),
    ):
        result = service.create_job(
            tenant_id=_NAMESPACE_ID,
            dataset_id=_DATASET_ID,
            version_id=_VERSION_ID,
            sql_text="SELECT spend FROM dataset LIMIT 1",
            submission_authority=_authority(),
        )

    assert result is legacy_job
    create_mock.assert_not_called()


def test_different_actor_cannot_reuse_an_active_legacy_intelligence_job() -> None:
    service = DatasetIntelligenceJobService()
    legacy_job = SimpleNamespace(id=_DATASET_ID, created_by_id=_ACTOR_ID + 1)
    created_job = SimpleNamespace(id=_VERSION_ID)
    with (
        patch.object(
            service,
            "_find_active_job_by_fingerprint",
            side_effect=(None, legacy_job),
        ),
        patch(
            "resource_registry.services.dataset_intelligence_job_service."
            "DatasetIntelligenceJob.objects.create",
            return_value=created_job,
        ) as create_mock,
        patch("resource_registry.services.dataset_intelligence_job_service.transaction.atomic"),
    ):
        result = service.create_job(
            tenant_id=_NAMESPACE_ID,
            operation="analysis",
            request_payload={"dataset_id": _DATASET_ID, "analysis_type": "summary"},
            dataset_id=_DATASET_ID,
            version_id=_VERSION_ID,
            submission_authority=_authority(),
        )

    assert result is created_job
    create_mock.assert_called_once()


def test_untyped_metadata_cannot_forge_submission_authority() -> None:
    service = DatasetQueryJobService()
    with (
        patch(
            "resource_registry.services.dataset_query_job_service.DatasetQueryJob.objects.create",
            return_value=SimpleNamespace(id=_DATASET_ID),
        ) as create_mock,
        patch.object(service, "_find_active_job_by_fingerprint", return_value=None),
        patch("resource_registry.services.dataset_query_job_service.transaction.atomic"),
    ):
        service.create_job(
            tenant_id=_NAMESPACE_ID,
            dataset_id=_DATASET_ID,
            version_id=None,
            sql_text="SELECT spend FROM dataset LIMIT 1",
            metadata={SUBMISSION_AUTHORITY_METADATA_KEY: {"actor": {"id": 999}}},
        )

    assert SUBMISSION_AUTHORITY_METADATA_KEY not in create_mock.call_args.kwargs["metadata_json"]
