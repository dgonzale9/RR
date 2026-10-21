from __future__ import annotations

import ast
import uuid
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock, patch

import pytest
from django.db import DatabaseError
from django.utils import timezone

from resource_registry.application.contracts import (
    AuthorizationError,
    HealthState,
    InternalError,
    NotFoundError,
    OutputKind,
    OutputStatus,
    RunStatus,
    StepStatus,
    TransformationOutputReference,
    TransformationRunReference,
    TransformationStepSummary,
    ValidationError,
)
from resource_registry.application.read import (
    DEFAULT_TRANSFORMATION_READ_LIMIT,
    MAXIMUM_TRANSFORMATION_READ_LIMIT,
    VIEW_TRANSFORMATION_DIAGNOSTICS_PERMISSION,
    VIEW_TRANSFORMATION_RUN_PERMISSION,
    TransformationDiagnosticsReadService,
    TransformationOutputReadService,
    TransformationReadAuthorizationService,
    TransformationReadContext,
    TransformationRunFilters,
    TransformationRunReadService,
    TransformationStepReadService,
)
from resource_registry.application.read.mapping import map_output_reference
from resource_registry.application.read.transformation_run_read_service import _normalize_limit
from resource_registry.models import (
    TransformationRun,
    TransformationRunOutput,
    TransformationRunOutputPolicyKind,
    TransformationRunOutputStatus,
    TransformationRunStatus,
    TransformationRunStepAttempt,
    TransformationRunStepAttemptStatus,
)
from resource_registry.tests.test_transformation_run_creation_service import (
    _build_request,
    _create_definition_and_version,
)
from resource_registry.transformations.operational.contracts import (
    TransformationOperationalHealthStatus,
)
from resource_registry.transformations.operational.health import TransformationOperationalHealthService
from resource_registry.transformations.runs import TransformationRunCreationService

pytestmark = pytest.mark.django_db(transaction=True)

_READ_ROOT = Path("resource_registry/application/read")
_EXECUTION_RETRY_COUNT = 2
_RUN_DURATION_SECONDS = 1.5
_STEP_DURATION_SECONDS = 0.25
_FORBIDDEN_IMPORT_PREFIXES = (
    "rest_framework",
    "resource_registry.api",
    "resource_registry.serializers",
    "resource_registry.storage",
    "resource_registry.services.transformation_run_worker",
    "resource_registry.services.transformation_output_finalization_worker",
    "resource_registry.transformations.execution",
    "resource_registry.transformations.providers",
    "resource_registry.transformations.output_finalization",
    "BotServer.built_in_functions",
)


class _Governance:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available

    def is_available(self, *, tenant_id: str) -> bool:
        _ = tenant_id
        return self.available


def _context(
    *,
    tenant_id: uuid.UUID,
    permissions: frozenset[str] | None = None,
    allowed_definition_ids: frozenset[str] | None = None,
    allowed_dataset_ids: frozenset[str] | None = None,
) -> TransformationReadContext:
    return TransformationReadContext(
        tenant_id=str(tenant_id),
        actor="service:application-read-tests",
        permissions=permissions or frozenset({VIEW_TRANSFORMATION_RUN_PERMISSION}),
        correlation_id="read-correlation",
        allowed_definition_ids=allowed_definition_ids,
        allowed_dataset_ids=allowed_dataset_ids,
    )


def _authorization(*, governance_available: bool = True) -> TransformationReadAuthorizationService:
    return TransformationReadAuthorizationService(
        governance=_Governance(available=governance_available),
    )


def _create_run(
    *,
    tenant_id: uuid.UUID,
    parameter_value: float,
    correlation_id: str,
    definition_id: uuid.UUID | None = None,
) -> TransformationRun:
    if definition_id is None:
        definition, version = _create_definition_and_version(tenant_id=tenant_id)
    else:
        existing = TransformationRun.objects.filter(definition_id=definition_id).first()
        assert existing is not None
        definition = existing.definition
        version = existing.definition_version
    request = _build_request(
        definition=definition,
        version=version,
        parameter_value=parameter_value,
        correlation_id=correlation_id,
    )
    result = TransformationRunCreationService().create_run(request=request)
    return TransformationRun.objects.get(id=result.run_id)


def _create_step(
    *,
    run: TransformationRun,
    attempt_number: int,
    step_index: int,
    status: str,
    error_code: str | None = None,
) -> TransformationRunStepAttempt:
    return TransformationRunStepAttempt.objects.create(
        run=run,
        run_attempt_number=attempt_number,
        run_attempt_token=uuid.uuid4(),
        step_index=step_index,
        step_id=f"step-{step_index}",
        provider_key="private-provider",
        provider_version="1",
        operation_key="private-operation",
        operation_version="1",
        status=status,
        plan_checksum="a" * 64,
        capability_checksum="b" * 64,
        attempt_idempotency_token="c" * 64,
        error_code=error_code,
        execution_ms=250,
    )


def test_run_reads_enforce_tenant_permission_governance_and_definition_scope() -> None:
    tenant_id = uuid.uuid4()
    run = _create_run(
        tenant_id=tenant_id,
        parameter_value=0.1,
        correlation_id="authorized",
    )
    service = TransformationRunReadService(authorization=_authorization())

    result = service.get_run(context=_context(tenant_id=tenant_id), run_id=str(run.id))

    assert isinstance(result, TransformationRunReference)
    assert result.run_id == str(run.id)
    assert result.status == RunStatus.QUEUED
    with pytest.raises(NotFoundError):
        service.get_run(context=_context(tenant_id=uuid.uuid4()), run_id=str(run.id))
    with pytest.raises(AuthorizationError):
        service.get_run(
            context=_context(tenant_id=tenant_id, permissions=frozenset({"unrelated"})),
            run_id=str(run.id),
        )
    with pytest.raises(AuthorizationError):
        TransformationRunReadService(
            authorization=_authorization(governance_available=False),
        ).get_run(context=_context(tenant_id=tenant_id), run_id=str(run.id))
    with pytest.raises(AuthorizationError):
        service.get_run(
            context=_context(
                tenant_id=tenant_id,
                allowed_definition_ids=frozenset({str(uuid.uuid4())}),
            ),
            run_id=str(run.id),
        )


def test_run_list_filters_orders_and_paginates_deterministically() -> None:
    tenant_id = uuid.uuid4()
    first = _create_run(
        tenant_id=tenant_id,
        parameter_value=0.1,
        correlation_id="first",
    )
    second = _create_run(
        tenant_id=tenant_id,
        parameter_value=0.2,
        correlation_id="second",
        definition_id=first.definition_id,
    )
    third = _create_run(
        tenant_id=tenant_id,
        parameter_value=0.3,
        correlation_id="third",
    )
    now = timezone.now()
    TransformationRun.objects.filter(id=first.id).update(
        created_at=now - timedelta(minutes=2),
        status=TransformationRunStatus.FAILED,
        completed_at=now - timedelta(minutes=1),
        error_code="run_failed",
    )
    TransformationRun.objects.filter(id=second.id).update(created_at=now - timedelta(minutes=1))
    TransformationRun.objects.filter(id=third.id).update(created_at=now)
    service = TransformationRunReadService(authorization=_authorization())
    context = _context(tenant_id=tenant_id)

    page = service.list_runs(context=context, limit=2, offset=-10)
    filtered = service.list_runs(
        context=context,
        filters=TransformationRunFilters(
            definition_id=str(first.definition_id),
            definition_version_id=str(first.definition_version_id),
            status=RunStatus.QUEUED,
            created_after=now - timedelta(minutes=90),
            created_before=now,
            correlation_id="second",
        ),
    )

    assert tuple(item.run_id for item in page) == (str(third.id), str(second.id))
    assert tuple(item.run_id for item in filtered) == (str(second.id),)
    TransformationRun.objects.filter(id__in=(second.id, third.id)).update(created_at=now)
    tied_page = service.list_runs(context=context, limit=2)
    assert tuple(item.run_id for item in tied_page) == tuple(
        sorted((str(second.id), str(third.id)), reverse=True)
    )
    assert _normalize_limit(0) == DEFAULT_TRANSFORMATION_READ_LIMIT
    assert _normalize_limit(101) == MAXIMUM_TRANSFORMATION_READ_LIMIT
    with pytest.raises(ValidationError):
        service.list_runs(
            context=context,
            filters=TransformationRunFilters(
                created_after=now,
                created_before=now - timedelta(seconds=1),
            ),
        )


def test_run_list_applies_definition_scope_before_pagination() -> None:
    tenant_id = uuid.uuid4()
    allowed = _create_run(
        tenant_id=tenant_id,
        parameter_value=0.1,
        correlation_id="allowed",
    )
    _create_run(
        tenant_id=tenant_id,
        parameter_value=0.2,
        correlation_id="hidden",
    )
    service = TransformationRunReadService(authorization=_authorization())

    results = service.list_runs(
        context=_context(
            tenant_id=tenant_id,
            allowed_definition_ids=frozenset({str(allowed.definition_id)}),
        ),
    )

    assert tuple(item.run_id for item in results) == (str(allowed.id),)


def test_step_reads_return_ordered_summaries_without_internal_fields() -> None:
    run = _create_run(
        tenant_id=uuid.uuid4(),
        parameter_value=0.1,
        correlation_id="steps",
    )
    _create_step(
        run=run,
        attempt_number=2,
        step_index=0,
        status=TransformationRunStepAttemptStatus.RUNNING,
    )
    _create_step(
        run=run,
        attempt_number=1,
        step_index=1,
        status=TransformationRunStepAttemptStatus.FAILED,
        error_code="provider_failed",
    )
    service = TransformationStepReadService(authorization=_authorization())

    results = service.list_steps(
        context=_context(tenant_id=run.tenant_id),
        run_id=str(run.id),
    )

    assert all(isinstance(item, TransformationStepSummary) for item in results)
    assert tuple(item.attempt_number for item in results) == (1, 2)
    assert results[0].status == StepStatus.FAILED
    assert results[0].duration_seconds == _STEP_DURATION_SECONDS
    assert not {
        "provider_key",
        "operation_key",
        "checkpoint",
        "execution_request",
    }.intersection(results[0].__dataclass_fields__)


def test_output_reads_expose_only_public_identifiers_and_enforce_dataset_scope() -> None:
    run = _create_run(
        tenant_id=uuid.uuid4(),
        parameter_value=0.1,
        correlation_id="outputs",
    )
    service = TransformationOutputReadService(authorization=_authorization())
    context = _context(tenant_id=run.tenant_id)

    results = service.list_outputs(context=context, run_id=str(run.id))
    selected = service.get_output(context=context, output_id=results[0].output_id)

    assert all(isinstance(item, TransformationOutputReference) for item in results)
    assert selected.kind == OutputKind.STANDALONE
    assert selected.status == OutputStatus.PENDING
    assert not {
        "canonical_uri",
        "destination_object_key",
        "storage_provider",
        "workspace",
    }.intersection(selected.__dataclass_fields__)

    dataset_id = uuid.uuid4()
    dataset_version_id = uuid.uuid4()
    artifact_id = uuid.uuid4()
    internal_output = TransformationRunOutput(
        id=uuid.uuid4(),
        logical_name="dataset-result",
        output_policy_kind=TransformationRunOutputPolicyKind.NEW_DATASET_VERSION,
        status=TransformationRunOutputStatus.REGISTERED,
        dataset_id=dataset_id,
        dataset_version_id=dataset_version_id,
        dataset_artifact_id=artifact_id,
    )
    mapped = map_output_reference(internal_output)
    assert mapped.kind == OutputKind.DATASET_VERSION
    assert mapped.dataset_id == str(dataset_id)
    assert mapped.dataset_version_id == str(dataset_version_id)
    assert mapped.artifact_id == str(artifact_id)
    with pytest.raises(AuthorizationError):
        _authorization().authorize_output_dataset(
            context=_context(
                tenant_id=run.tenant_id,
                allowed_dataset_ids=frozenset(),
            ),
            dataset_id=str(dataset_id),
        )


def test_diagnostics_return_safe_aggregates_retries_duration_progress_and_health() -> None:
    run = _create_run(
        tenant_id=uuid.uuid4(),
        parameter_value=0.1,
        correlation_id="diagnostics",
    )
    TransformationRun.objects.filter(id=run.id).update(
        latest_attempt_number=_EXECUTION_RETRY_COUNT,
        retry_count=_EXECUTION_RETRY_COUNT,
        finalization_attempt_number=1,
        finalization_retry_count=1,
        execution_ms=1500,
        error_code="run_failed",
    )
    run.refresh_from_db()
    _create_step(
        run=run,
        attempt_number=_EXECUTION_RETRY_COUNT,
        step_index=0,
        status=TransformationRunStepAttemptStatus.FAILED,
        error_code="step_failed",
    )
    TransformationRunOutput.objects.filter(run=run).update(
        status=TransformationRunOutputStatus.FAILED,
        error_code="output_failed",
    )
    health = MagicMock()
    health.get_health.return_value = SimpleNamespace(
        status=TransformationOperationalHealthStatus.DEGRADED,
    )
    service = TransformationDiagnosticsReadService(
        authorization=_authorization(),
        health=cast(TransformationOperationalHealthService, health),
    )

    result = service.get_run_diagnostics(
        context=_context(
            tenant_id=run.tenant_id,
            permissions=frozenset({VIEW_TRANSFORMATION_DIAGNOSTICS_PERMISSION}),
        ),
        run_id=str(run.id),
    )

    assert result.health_state == HealthState.DEGRADED
    assert result.aggregate_counts["retry_count"] == _EXECUTION_RETRY_COUNT
    assert result.aggregate_counts["finalization_retry_count"] == 1
    assert result.duration_seconds == _RUN_DURATION_SECONDS
    assert result.progress == 0.0
    assert set(result.warnings) == {
        "execution_retried",
        "finalization_retried",
        "output_failed",
        "run_failed",
        "runtime_health_degraded",
        "step_failed",
    }
    assert not {
        "execution_plan",
        "checkpoint",
        "payload",
        "provider",
    }.intersection(result.__dataclass_fields__)


def test_database_and_mapping_failures_become_stable_application_errors() -> None:
    run = _create_run(
        tenant_id=uuid.uuid4(),
        parameter_value=0.1,
        correlation_id="errors",
    )
    service = TransformationRunReadService(authorization=_authorization())
    context = _context(tenant_id=run.tenant_id)
    with patch(
        "resource_registry.application.read.transformation_run_read_service."
        "TransformationRun.objects.filter",
        side_effect=DatabaseError("private database detail"),
    ):
        with pytest.raises(InternalError) as captured:
            service.list_runs(context=context)

    assert captured.value.code == "transformation_read_failed"
    assert "database" not in captured.value.public_message.lower()


def test_read_service_dependencies_exclude_forbidden_runtime_and_interface_layers() -> None:
    for source_path in _READ_ROOT.glob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imported_modules = _imported_modules(tree)
        assert not any(
            module.startswith(_FORBIDDEN_IMPORT_PREFIXES) for module in imported_modules
        ), source_path


def _imported_modules(tree: ast.AST) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    return imported
