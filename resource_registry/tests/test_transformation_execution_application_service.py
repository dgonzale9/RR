from __future__ import annotations

import ast
import uuid
from concurrent.futures import CancelledError as FutureCancelledError
from pathlib import Path

import pytest

from resource_registry.application.contracts import (
    ApplicationError,
    AuthorizationError,
    CancelledError,
    ConflictError,
    ExecutionState,
    InternalError,
    NotFoundError,
    OperationFailedError,
    RunStatus,
    RuntimeUnavailableError,
    TransformationExecutionRequest,
    TransformationInputBinding,
    ValidationError,
)
from resource_registry.application.contracts import (
    TimeoutError as ApplicationTimeoutError,
)
from resource_registry.application.services import (
    EXECUTE_TRANSFORMATION_PERMISSION,
    USE_TRANSFORMATION_PROVIDER_PERMISSION,
    TransformationApplicationErrorTranslator,
    TransformationExecutionApplicationService,
    TransformationExecutionCallerContext,
    TransformationExecutionEntryAuthorizationService,
    TransformationExecutionRequestValidator,
    TransformationRuntimeInvocationResult,
)
from resource_registry.application.transformation_runtime import RuntimeBootstrapException
from resource_registry.read.exceptions import TransformationDefinitionNotFoundError
from resource_registry.transformations.operational.exceptions import (
    TransformationPermissionDeniedError,
)
from resource_registry.transformations.runs.exceptions import (
    InvalidTransformationRunCreationRequestError,
    TransformationExecutionPlanChecksumMismatchError,
    TransformationRunIdempotencyConflictError,
)

_SERVICE_ROOT = Path("resource_registry/application/services")
_FORBIDDEN_IMPORT_PREFIXES = (
    "rest_framework",
    "resource_registry.api",
    "resource_registry.serializers",
    "resource_registry.models",
    "resource_registry.storage",
    "resource_registry.services.transformation_run_worker",
    "resource_registry.services.transformation_output_finalization_worker",
    "resource_registry.transformations.execution.service",
    "resource_registry.transformations.providers",
    "resource_registry.transformations.output_finalization",
    "resource_registry.services.registry.lineage_service",
    "BotServer.built_in_functions",
)


class _Governance:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available

    def is_available(self, *, tenant_id: str) -> bool:
        _ = tenant_id
        return self.available


class _Runtime:
    def __init__(
        self,
        *,
        result: TransformationRuntimeInvocationResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.calls: list[
            tuple[TransformationExecutionRequest, TransformationExecutionCallerContext]
        ] = []

    def submit_execution(
        self,
        *,
        request: TransformationExecutionRequest,
        caller: TransformationExecutionCallerContext,
    ) -> TransformationRuntimeInvocationResult:
        self.calls.append((request, caller))
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


def _request() -> TransformationExecutionRequest:
    return TransformationExecutionRequest(
        tenant_id=str(uuid.UUID(int=1)),
        definition_id=str(uuid.UUID(int=2)),
        definition_version_id=str(uuid.UUID(int=3)),
        parameters={"threshold": 0.5},
        input_bindings=(
            TransformationInputBinding(
                input_name="source",
                dataset_id=str(uuid.UUID(int=4)),
                dataset_version_id=str(uuid.UUID(int=5)),
                artifact_id=str(uuid.UUID(int=6)),
            ),
        ),
        caller_idempotency_key="caller-key-1",
        correlation_id="correlation-1",
    )


def _caller(
    *,
    permissions: frozenset[str] | None = None,
    tenant_id: str | None = None,
) -> TransformationExecutionCallerContext:
    request = _request()
    return TransformationExecutionCallerContext(
        tenant_id=tenant_id or request.tenant_id,
        actor="service:application-tests",
        principal_kind="SERVICE",
        permissions=(
            permissions
            if permissions is not None
            else frozenset(
                {
                    EXECUTE_TRANSFORMATION_PERMISSION,
                    USE_TRANSFORMATION_PROVIDER_PERMISSION,
                },
            )
        ),
        allowed_definition_ids=frozenset({request.definition_id}),
        allowed_dataset_ids=frozenset(
            binding.dataset_id for binding in request.input_bindings
        ),
    )


def _result(
    request: TransformationExecutionRequest,
    *,
    status: str = RunStatus.QUEUED.value,
    was_created: bool = True,
) -> TransformationRuntimeInvocationResult:
    return TransformationRuntimeInvocationResult(
        tenant_id=request.tenant_id,
        run_id=str(uuid.UUID(int=7)),
        definition_id=request.definition_id,
        definition_version_id=request.definition_version_id,
        status=status,
        was_created=was_created,
    )


def _service(
    *,
    runtime: _Runtime,
    governance_available: bool = True,
) -> TransformationExecutionApplicationService:
    return TransformationExecutionApplicationService(
        validator=TransformationExecutionRequestValidator(),
        authorization=TransformationExecutionEntryAuthorizationService(
            governance=_Governance(available=governance_available),
        ),
        runtime=runtime,
        error_translator=TransformationApplicationErrorTranslator(),
    )


def test_service_validates_authorizes_delegates_and_maps_accepted_result() -> None:
    request = _request()
    caller = _caller()
    runtime = _Runtime(result=_result(request))

    accepted = _service(runtime=runtime).execute(request=request, caller=caller)

    assert accepted.run.run_id == str(uuid.UUID(int=7))
    assert accepted.run.status == RunStatus.QUEUED
    assert accepted.execution_state == ExecutionState.ACCEPTED
    assert accepted.idempotent_replay is False
    assert accepted.correlation_id == request.correlation_id
    assert runtime.calls == [(request, caller)]
    assert runtime.calls[0][0].caller_idempotency_key == request.caller_idempotency_key


def test_service_preserves_runtime_idempotent_resolution_and_maps_active_state() -> None:
    request = _request()
    runtime = _Runtime(
        result=_result(request, status=RunStatus.RUNNING.value, was_created=False),
    )

    accepted = _service(runtime=runtime).execute(request=request, caller=_caller())

    assert accepted.idempotent_replay is True
    assert accepted.execution_state == ExecutionState.IN_PROGRESS
    assert accepted.run.status == RunStatus.RUNNING


def test_malformed_request_is_rejected_before_authorization_or_runtime_invocation() -> None:
    request = _request()
    object.__setattr__(request, "tenant_id", "not-a-uuid")
    runtime = _Runtime(result=_result(_request()))

    with pytest.raises(ValidationError) as captured:
        _service(runtime=runtime).execute(request=request, caller=_caller())

    assert captured.value.code == "invalid_transformation_identifier"
    assert runtime.calls == []


def test_mutable_nested_parameters_are_rejected_before_runtime_invocation() -> None:
    request = _request()
    object.__setattr__(request, "parameters", {"mutable": []})
    runtime = _Runtime(result=_result(_request()))

    with pytest.raises(ValidationError) as captured:
        _service(runtime=runtime).execute(request=request, caller=_caller())

    assert captured.value.code == "invalid_transformation_parameters"
    assert runtime.calls == []


@pytest.mark.parametrize(
    "caller",
    (
        _caller(tenant_id=str(uuid.UUID(int=99))),
        _caller(permissions=frozenset({EXECUTE_TRANSFORMATION_PERMISSION})),
        TransformationExecutionCallerContext(
            tenant_id=_request().tenant_id,
            actor="service:application-tests",
            principal_kind="SERVICE",
            permissions=frozenset(
                {
                    EXECUTE_TRANSFORMATION_PERMISSION,
                    USE_TRANSFORMATION_PROVIDER_PERMISSION,
                },
            ),
            allowed_definition_ids=frozenset(),
            allowed_dataset_ids=None,
        ),
    ),
)
def test_entry_authorization_fails_closed_before_runtime_invocation(
    caller: TransformationExecutionCallerContext,
) -> None:
    request = _request()
    runtime = _Runtime(result=_result(request))

    with pytest.raises(AuthorizationError):
        _service(runtime=runtime).execute(request=request, caller=caller)

    assert runtime.calls == []


def test_governance_unavailability_fails_closed_before_runtime_invocation() -> None:
    request = _request()
    runtime = _Runtime(result=_result(request))

    with pytest.raises(AuthorizationError) as captured:
        _service(runtime=runtime, governance_available=False).execute(
            request=request,
            caller=_caller(),
        )

    assert captured.value.code == "transformation_governance_unavailable"
    assert captured.value.retryable is True
    assert runtime.calls == []


@pytest.mark.parametrize(
    ("runtime_error", "application_error_type"),
    (
        (TransformationPermissionDeniedError("private"), AuthorizationError),
        (InvalidTransformationRunCreationRequestError("private"), ValidationError),
        (TransformationRunIdempotencyConflictError("private"), ConflictError),
        (TransformationDefinitionNotFoundError("private"), NotFoundError),
        (TransformationExecutionPlanChecksumMismatchError("private"), OperationFailedError),
        (RuntimeBootstrapException("private"), RuntimeUnavailableError),
        (TimeoutError("private"), ApplicationTimeoutError),
        (FutureCancelledError("private"), CancelledError),
        (RuntimeError("private provider response"), InternalError),
    ),
)
def test_runtime_errors_translate_without_internal_details(
    runtime_error: Exception,
    application_error_type: type[ApplicationError],
) -> None:
    request = _request()

    with pytest.raises(application_error_type) as captured:
        _service(runtime=_Runtime(error=runtime_error)).execute(
            request=request,
            caller=_caller(),
        )

    assert captured.value.correlation_id == request.correlation_id
    assert "private" not in captured.value.public_message


def test_runtime_result_identity_or_status_mismatch_fails_closed() -> None:
    request = _request()
    mismatched = _result(request)
    object.__setattr__(mismatched, "tenant_id", str(uuid.UUID(int=99)))

    with pytest.raises(InternalError) as identity_error:
        _service(runtime=_Runtime(result=mismatched)).execute(
            request=request,
            caller=_caller(),
        )
    assert identity_error.value.code == "transformation_runtime_result_mismatch"

    invalid_status = _result(request, status="PRIVATE_RUNTIME_STATE")
    with pytest.raises(InternalError) as status_error:
        _service(runtime=_Runtime(result=invalid_status)).execute(
            request=request,
            caller=_caller(),
        )
    assert status_error.value.code == "transformation_runtime_result_invalid"


def test_application_service_modules_exclude_forbidden_dependencies() -> None:
    for source_path in _SERVICE_ROOT.glob("*.py"):
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
