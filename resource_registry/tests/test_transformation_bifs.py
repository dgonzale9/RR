from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest

from resource_registry.application.contracts import (
    AuthorizationError,
    ExecutionState,
    HealthState,
    OutputKind,
    OutputStatus,
    RunStatus,
    StepStatus,
    TransformationDiagnosticSummary,
    TransformationExecutionAccepted,
    TransformationExecutionRequest,
    TransformationInputBinding,
    TransformationOutputReference,
    TransformationRunReference,
    TransformationStepSummary,
)
from resource_registry.application.read import (
    TransformationDiagnosticsReadService,
    TransformationOutputReadService,
    TransformationReadContext,
    TransformationRunFilters,
    TransformationRunReadService,
    TransformationStepReadService,
)
from resource_registry.application.services import TransformationExecutionApplicationService
from resource_registry.application.services.results import TransformationExecutionCallerContext
from resource_registry.bifs.operation_handlers import resolve_registry_operation_handler
from resource_registry.bifs.read_only_namespace_admission import AdmittedBifNamespace
from resource_registry.bifs.transformation import (
    TRANSFORMATION_BIF_DEFINITIONS,
    TransformationBifApplicationServices,
    resource_registry_get_transformation_diagnostics,
    resource_registry_get_transformation_run,
    resource_registry_list_transformation_outputs,
    resource_registry_list_transformation_runs,
    resource_registry_list_transformation_steps,
    resource_registry_start_transformation_run,
)
from resource_registry.bifs.transformation.context import (
    TransformationBifTrustedContextBuilder,
    TransformationReadNamespaceAdmission,
)
from resource_registry.bifs.transformation.errors import TransformationBifErrorMapper
from resource_registry.bifs.transformation.registration import build_transformation_bif_registry
from resource_registry.read_auth.exceptions import DataRegistryAuthorizationError

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEFINITION_ID = "22222222-2222-2222-2222-222222222222"
DEFINITION_VERSION_ID = "33333333-3333-3333-3333-333333333333"
RUN_ID = "44444444-4444-4444-4444-444444444444"
DATASET_ID = "55555555-5555-5555-5555-555555555555"
DATASET_VERSION_ID = "66666666-6666-6666-6666-666666666666"
ARTIFACT_ID = "77777777-7777-7777-7777-777777777777"
OUTPUT_ID = "88888888-8888-8888-8888-888888888888"
CORRELATION_ID = "corr-123"
HTTP_BAD_REQUEST = 400
HTTP_FORBIDDEN = 403
HTTP_SERVICE_UNAVAILABLE = 503
PAGE_LIMIT = 20
PAGE_OFFSET = 40


@dataclass(frozen=True)
class _FakeUser:
    username: str = "alice"
    pk: int = 101

    def get_all_permissions(self) -> set[str]:
        return {
            "resource_registry.execute_transformation",
            "resource_registry.use_transformation_provider",
            "resource_registry.view_transformationrun",
            "resource_registry.view_transformation_diagnostics",
        }


class _FakeRequest:
    def __init__(self) -> None:
        self.user = _FakeUser()
        self.tenant_id = TENANT_ID
        self.correlation_id = CORRELATION_ID
        self.session = {
            "resource_registry_roles": ["admin"],
            "resource_registry_enabled_capabilities": [
                "resource_registry.transformation.run.start",
                "resource_registry.transformation.run.read",
                "resource_registry.transformation.step.read",
                "resource_registry.transformation.output.read",
                "resource_registry.transformation.diagnostics.read",
            ],
        }
        self.transformation_bif_services: TransformationBifApplicationServices | None = None


class _FakeContextManager:
    def get_all_registry_entries(self, request: object) -> dict[str, object]:
        _ = request
        return {"resource_registry_tenant_id": {"value": TENANT_ID}}


class _FakeNamespaceAdmissionService:
    def __init__(self) -> None:
        self.calls: list[tuple[object, object]] = []

    def admit_namespace(
        self,
        *,
        request: object,
        requested_namespace_id: object,
    ) -> str:
        self.calls.append((request, requested_namespace_id))
        if requested_namespace_id not in {None, TENANT_ID}:
            raise DataRegistryAuthorizationError("The selected tenant_id is not authorized.")
        return TENANT_ID

    def admit_namespace_with_provenance(
        self,
        *,
        request: object,
        requested_namespace_id: object,
    ) -> AdmittedBifNamespace:
        self.calls.append((request, requested_namespace_id))
        if requested_namespace_id not in {None, TENANT_ID}:
            raise DataRegistryAuthorizationError("The selected tenant_id is not authorized.")
        return AdmittedBifNamespace(
            namespace_id=TENANT_ID,
            organization_id=1,
            workspace_id=1,
            actor_id=101,
        )


class _DenyingNamespaceAdmissionService:
    def admit_namespace(
        self,
        *,
        request: object,
        requested_namespace_id: object,
    ) -> str:
        _ = request, requested_namespace_id
        raise DataRegistryAuthorizationError("The selected tenant_id is not authorized.")

    def admit_namespace_with_provenance(
        self,
        *,
        request: object,
        requested_namespace_id: object,
    ) -> AdmittedBifNamespace:
        _ = request, requested_namespace_id
        raise DataRegistryAuthorizationError("The selected tenant_id is not authorized.")


class _FakeExecutionService:
    def __init__(self) -> None:
        self.calls: list[tuple[object, object]] = []

    def execute(
        self,
        *,
        request: object,
        caller: object,
    ) -> TransformationExecutionAccepted:
        self.calls.append((request, caller))
        return TransformationExecutionAccepted(
            run=_run_reference(status=RunStatus.QUEUED),
            execution_state=ExecutionState.ACCEPTED,
            idempotent_replay=False,
            correlation_id=CORRELATION_ID,
        )


class _FakeRunReadService:
    def __init__(self) -> None:
        self.run_calls: list[tuple[TransformationReadContext, str]] = []
        self.list_calls: list[
            tuple[TransformationReadContext, TransformationRunFilters, int, int]
        ] = []

    def get_run(
        self,
        *,
        context: TransformationReadContext,
        run_id: str,
    ) -> TransformationRunReference:
        self.run_calls.append((context, run_id))
        return _run_reference(status=RunStatus.RUNNING)

    def list_runs(
        self,
        *,
        context: TransformationReadContext,
        filters: TransformationRunFilters | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[TransformationRunReference, ...]:
        active_filters = filters or TransformationRunFilters()
        self.list_calls.append((context, active_filters, limit, offset))
        return (_run_reference(status=RunStatus.SUCCEEDED),)


class _FakeStepReadService:
    def __init__(self) -> None:
        self.calls: list[tuple[TransformationReadContext, str]] = []

    def list_steps(
        self,
        *,
        context: TransformationReadContext,
        run_id: str,
    ) -> tuple[TransformationStepSummary, ...]:
        self.calls.append((context, run_id))
        return (
            TransformationStepSummary(
                step_id="load_input",
                attempt_number=1,
                status=StepStatus.SUCCEEDED,
                duration_seconds=1.5,
                error_code=None,
            ),
        )


class _FakeOutputReadService:
    def __init__(self) -> None:
        self.calls: list[tuple[TransformationReadContext, str]] = []

    def list_outputs(
        self,
        *,
        context: TransformationReadContext,
        run_id: str,
    ) -> tuple[TransformationOutputReference, ...]:
        self.calls.append((context, run_id))
        return (
            TransformationOutputReference(
                output_id=OUTPUT_ID,
                logical_name="result",
                kind=OutputKind.DATASET_VERSION,
                status=OutputStatus.REGISTERED,
                dataset_id=DATASET_ID,
                dataset_version_id=DATASET_VERSION_ID,
                artifact_id=ARTIFACT_ID,
            ),
        )


class _FakeDiagnosticsReadService:
    def __init__(self) -> None:
        self.calls: list[tuple[TransformationReadContext, str]] = []

    def get_run_diagnostics(
        self,
        *,
        context: TransformationReadContext,
        run_id: str,
    ) -> TransformationDiagnosticSummary:
        self.calls.append((context, run_id))
        return TransformationDiagnosticSummary(
            health_state=HealthState.READY,
            aggregate_counts={"steps": 1},
            warnings=("execution_retried",),
            durations={"execution": 1.0},
            duration_seconds=1.0,
            progress=1.0,
        )


def test_transformation_bif_registry_contains_only_approved_names() -> None:
    registry = build_transformation_bif_registry()

    assert tuple(registry) == (
        "resource_registry_get_transformation_diagnostics",
        "resource_registry_get_transformation_run",
        "resource_registry_list_transformation_outputs",
        "resource_registry_list_transformation_runs",
        "resource_registry_list_transformation_steps",
        "resource_registry_start_transformation_run",
    )
    assert {definition.name for definition in TRANSFORMATION_BIF_DEFINITIONS} == set(registry)


def test_transformation_bif_registry_rejects_duplicates() -> None:
    duplicate = (TRANSFORMATION_BIF_DEFINITIONS[0], TRANSFORMATION_BIF_DEFINITIONS[0])

    with pytest.raises(ValueError, match="Duplicate transformation BIF name"):
        build_transformation_bif_registry(duplicate)


def test_start_transformation_run_delegates_once_and_maps_accepted_result() -> None:
    request, execution = _configured_request()

    response = _load_json(
        resource_registry_start_transformation_run(
            request,
            _FakeContextManager(),
            definition_id=DEFINITION_ID,
            definition_version_id=DEFINITION_VERSION_ID,
            parameters={"threshold": 10},
            input_bindings=[
                {
                    "input_name": "source",
                    "dataset_id": DATASET_ID,
                    "dataset_version_id": DATASET_VERSION_ID,
                    "artifact_id": ARTIFACT_ID,
                },
            ],
            idempotency_key="agent-call-1",
            correlation_id=CORRELATION_ID,
        ),
    )

    assert response["success"] is True
    data = cast(dict[str, object], response["data"])
    run = cast(dict[str, object], data["run"])
    assert data["asynchronous"] is True
    assert data["execution_state"] == "ACCEPTED"
    assert run["run_id"] == RUN_ID
    assert len(execution.calls) == 1
    application_request = cast(TransformationExecutionRequest, execution.calls[0][0])
    caller = cast(TransformationExecutionCallerContext, execution.calls[0][1])
    assert application_request.tenant_id == TENANT_ID
    assert application_request.definition_version_id == DEFINITION_VERSION_ID
    assert application_request.caller_idempotency_key == "agent-call-1"
    assert application_request.input_bindings == (
        TransformationInputBinding(
            input_name="source",
            dataset_id=DATASET_ID,
            dataset_version_id=DATASET_VERSION_ID,
            artifact_id=ARTIFACT_ID,
        ),
    )
    assert caller.tenant_id == TENANT_ID
    assert caller.actor == "alice"


def test_start_transformation_run_rejects_unknown_fields_before_delegation() -> None:
    request, execution = _configured_request()

    response = _load_json(
        resource_registry_start_transformation_run(
            request,
            _FakeContextManager(),
            definition_id=DEFINITION_ID,
            definition_version_id=DEFINITION_VERSION_ID,
            parameters={},
            input_bindings=[],
            idempotency_key="agent-call-1",
            unsupported=True,
        ),
    )

    assert response["success"] is False
    assert response["status"] == HTTP_BAD_REQUEST
    assert response["code"] == "invalid_transformation_bif_request"
    assert execution.calls == []


def test_start_transformation_run_rejects_tenant_spoofing() -> None:
    request, execution = _configured_request()

    response = _load_json(
        resource_registry_start_transformation_run(
            request,
            _FakeContextManager(),
            tenant_id="99999999-9999-9999-9999-999999999999",
            definition_id=DEFINITION_ID,
            definition_version_id=DEFINITION_VERSION_ID,
            parameters={},
            input_bindings=[],
            idempotency_key="agent-call-1",
            correlation_id=CORRELATION_ID,
        ),
    )

    assert response["success"] is False
    assert response["status"] == HTTP_FORBIDDEN
    assert response["code"] == "transformation_bif_tenant_not_authorized"
    assert execution.calls == []


def test_start_transformation_run_denies_before_application_submission() -> None:
    request, execution = _configured_request(
        namespace_admission_service=_DenyingNamespaceAdmissionService(),
    )

    response = _load_json(
        resource_registry_start_transformation_run(
            request,
            _FakeContextManager(),
            tenant_id=TENANT_ID,
            definition_id=DEFINITION_ID,
            definition_version_id=DEFINITION_VERSION_ID,
            parameters={},
            input_bindings=[],
            idempotency_key="agent-call-1",
            correlation_id=CORRELATION_ID,
        ),
    )

    assert response["success"] is False
    assert response["status"] == HTTP_FORBIDDEN
    assert response["code"] == "transformation_bif_tenant_not_authorized"
    assert execution.calls == []


def test_start_transformation_run_fails_closed_without_services() -> None:
    request = _FakeRequest()

    response = _load_json(
        resource_registry_start_transformation_run(
            request,
            _FakeContextManager(),
            definition_id=DEFINITION_ID,
            definition_version_id=DEFINITION_VERSION_ID,
            parameters={},
            input_bindings=[],
            idempotency_key="agent-call-1",
            correlation_id=CORRELATION_ID,
        ),
    )

    assert response["success"] is False
    assert response["status"] == HTTP_SERVICE_UNAVAILABLE
    assert response["code"] == "transformation_bif_services_unavailable"


def test_list_transformation_runs_preserves_filters_and_pagination() -> None:
    request, _ = _configured_request()
    services = request.transformation_bif_services
    assert services is not None

    response = _load_json(
        resource_registry_list_transformation_runs(
            request,
            _FakeContextManager(),
            status="SUCCEEDED",
            definition_id=DEFINITION_ID,
            definition_version_id=DEFINITION_VERSION_ID,
            created_after="2026-01-01T00:00:00Z",
            created_before="2026-01-02T00:00:00Z",
            correlation_id=CORRELATION_ID,
            limit=PAGE_LIMIT,
            offset=PAGE_OFFSET,
        ),
    )

    run_service = cast(_FakeRunReadService, services.runs)
    _, filters, limit, offset = run_service.list_calls[0]
    data = cast(dict[str, object], response["data"])
    runs = cast(list[dict[str, object]], data["runs"])
    assert response["success"] is True
    assert runs[0]["status"] == "SUCCEEDED"
    assert data["pagination"] == {
        "limit": PAGE_LIMIT,
        "offset": PAGE_OFFSET,
        "returned": 1,
    }
    assert filters.status == RunStatus.SUCCEEDED
    assert filters.definition_id == DEFINITION_ID
    assert filters.definition_version_id == DEFINITION_VERSION_ID
    assert filters.correlation_id == CORRELATION_ID
    assert limit == PAGE_LIMIT
    assert offset == PAGE_OFFSET


def test_read_bifs_delegate_and_return_sanitized_public_dtos() -> None:
    request, _ = _configured_request()

    run_response = _load_json(
        resource_registry_get_transformation_run(request, _FakeContextManager(), run_id=RUN_ID),
    )
    step_response = _load_json(
        resource_registry_list_transformation_steps(request, _FakeContextManager(), run_id=RUN_ID),
    )
    output_response = _load_json(
        resource_registry_list_transformation_outputs(request, _FakeContextManager(), run_id=RUN_ID),
    )
    diagnostics_response = _load_json(
        resource_registry_get_transformation_diagnostics(request, _FakeContextManager(), run_id=RUN_ID),
    )

    run_data = cast(dict[str, object], run_response["data"])
    step_data = cast(dict[str, object], step_response["data"])
    output_data = cast(dict[str, object], output_response["data"])
    diagnostics_data = cast(dict[str, object], diagnostics_response["data"])
    run = cast(dict[str, object], run_data["run"])
    steps = cast(list[dict[str, object]], step_data["steps"])
    outputs = cast(list[dict[str, object]], output_data["outputs"])
    diagnostics = cast(dict[str, object], diagnostics_data["diagnostics"])

    assert run["status"] == "RUNNING"
    assert steps[0] == {
        "attempt_number": 1,
        "contract_version": "transformation-application/1.0",
        "duration_seconds": 1.5,
        "error_code": None,
        "status": "SUCCEEDED",
        "step_id": "load_input",
    }
    assert "storage_path" not in json.dumps(output_response)
    assert outputs[0]["artifact_id"] == ARTIFACT_ID
    assert diagnostics["warnings"] == ["execution_retried"]


def test_transformation_read_context_uses_canonical_namespace_admission() -> None:
    request = _FakeRequest()
    admission_service = _FakeNamespaceAdmissionService()
    context = TransformationBifTrustedContextBuilder(
        namespace_admission_service=admission_service,
    ).build_read_context(
        request=request,
        context_manager=_FakeContextManager(),
        function_name="resource_registry_get_transformation_run",
        requested_tenant_id=TENANT_ID,
        correlation_id=CORRELATION_ID,
    )

    assert context.tenant_id == TENANT_ID
    assert admission_service.calls == [(request, TENANT_ID)]


@pytest.mark.parametrize(
    ("read_bif", "payload"),
    (
        (resource_registry_get_transformation_run, {"run_id": RUN_ID}),
        (resource_registry_list_transformation_runs, {}),
        (resource_registry_list_transformation_steps, {"run_id": RUN_ID}),
        (resource_registry_list_transformation_outputs, {"run_id": RUN_ID}),
        (resource_registry_get_transformation_diagnostics, {"run_id": RUN_ID}),
    ),
)
def test_every_transformation_read_bif_denies_before_application_read(
    read_bif: Callable[..., str],
    payload: dict[str, object],
) -> None:
    request, _ = _configured_request(
        namespace_admission_service=_DenyingNamespaceAdmissionService(),
    )
    response = _load_json(
        read_bif(
            request,
            _FakeContextManager(),
            tenant_id=TENANT_ID,
            **payload,
        )
    )
    services = request.transformation_bif_services
    assert services is not None

    assert response["success"] is False
    assert response["status"] == HTTP_FORBIDDEN
    assert response["code"] == "transformation_bif_tenant_not_authorized"
    assert cast(_FakeRunReadService, services.runs).run_calls == []
    assert cast(_FakeRunReadService, services.runs).list_calls == []
    assert cast(_FakeStepReadService, services.steps).calls == []
    assert cast(_FakeOutputReadService, services.outputs).calls == []
    assert cast(_FakeDiagnosticsReadService, services.diagnostics).calls == []


def test_application_errors_map_to_stable_bif_failure_envelope() -> None:
    request, _ = _configured_request()
    services = request.transformation_bif_services
    assert services is not None

    class _DenyingRunReadService(_FakeRunReadService):
        def get_run(
            self,
            *,
            context: TransformationReadContext,
            run_id: str,
        ) -> TransformationRunReference:
            _ = context, run_id
            raise AuthorizationError(
                code="transformation_read_not_authorized",
                public_message="The transformation resource is not available.",
                correlation_id=CORRELATION_ID,
                metadata={"reason": "not_visible"},
            )

    request.transformation_bif_services = TransformationBifApplicationServices(
        execution=services.execution,
        runs=cast(TransformationRunReadService, _DenyingRunReadService()),
        steps=services.steps,
        outputs=services.outputs,
        diagnostics=services.diagnostics,
        context_builder=services.context_builder,
        error_mapper=services.error_mapper,
    )

    response = _load_json(
        resource_registry_get_transformation_run(request, _FakeContextManager(), run_id=RUN_ID),
    )

    assert response == {
        "category": "AUTHORIZATION",
        "code": "transformation_read_not_authorized",
        "correlation_id": CORRELATION_ID,
        "error": "The transformation resource is not available.",
        "metadata": {"reason": "not_visible"},
        "retryable": False,
        "status": 403,
        "success": False,
    }


def test_transformation_bifs_remain_resolvable_inside_resource_registry() -> None:
    """Preserve canonical transformation handlers behind the public grouped tool."""
    assert (
        resolve_registry_operation_handler("resource_registry_start_transformation_run")
        is resource_registry_start_transformation_run
    )
    assert (
        resolve_registry_operation_handler("resource_registry_get_transformation_run")
        is resource_registry_get_transformation_run
    )


def test_transformation_bif_modules_do_not_import_forbidden_layers() -> None:
    forbidden = (
        "resource_registry.models",
        "resource_registry.api.",
        "resource_registry.transformations.execution",
        "resource_registry.transformations.providers",
        "resource_registry.transformations.runs",
        "resource_registry.storage",
        "ArtifactLifecycleService",
        "DatasetRegistryService",
        "LineageService",
        "rest_framework",
    )
    root = Path(__file__).parents[1] / "bifs" / "transformation"

    for source_path in root.glob("*.py"):
        source = source_path.read_text(encoding="utf-8")
        for forbidden_import in forbidden:
            assert forbidden_import not in source, (
                f"{source_path.name} imports forbidden dependency {forbidden_import}"
            )


def _configured_request(
    *,
    namespace_admission_service: TransformationReadNamespaceAdmission | None = None,
) -> tuple[_FakeRequest, _FakeExecutionService]:
    execution = _FakeExecutionService()
    request = _FakeRequest()
    request.transformation_bif_services = TransformationBifApplicationServices(
        execution=cast(TransformationExecutionApplicationService, execution),
        runs=cast(TransformationRunReadService, _FakeRunReadService()),
        steps=cast(TransformationStepReadService, _FakeStepReadService()),
        outputs=cast(TransformationOutputReadService, _FakeOutputReadService()),
        diagnostics=cast(TransformationDiagnosticsReadService, _FakeDiagnosticsReadService()),
        context_builder=TransformationBifTrustedContextBuilder(
            namespace_admission_service=(
                namespace_admission_service or _FakeNamespaceAdmissionService()
            ),
        ),
        error_mapper=TransformationBifErrorMapper(),
    )
    return request, execution


def _run_reference(*, status: RunStatus) -> TransformationRunReference:
    return TransformationRunReference(
        tenant_id=TENANT_ID,
        run_id=RUN_ID,
        definition_id=DEFINITION_ID,
        definition_version_id=DEFINITION_VERSION_ID,
        status=status,
    )


def _load_json(value: str) -> dict[str, object]:
    parsed = json.loads(value)
    assert isinstance(parsed, dict)
    return cast(dict[str, object], parsed)
