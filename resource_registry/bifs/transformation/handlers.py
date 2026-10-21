from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import cast
from uuid import uuid4

from resource_registry.application.contracts import (
    ApplicationError,
    InternalError,
    RunStatus,
    RuntimeUnavailableError,
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
from resource_registry.bifs.transformation.context import TransformationBifTrustedContextBuilder
from resource_registry.bifs.transformation.definitions import TRANSFORMATION_BIF_CONTRACT_VERSION
from resource_registry.bifs.transformation.errors import (
    TransformationBifErrorMapper,
    success_result,
)
from resource_registry.bifs.transformation.schemas import (
    GET_TRANSFORMATION_DIAGNOSTICS_SCHEMA,
    GET_TRANSFORMATION_RUN_SCHEMA,
    LIST_TRANSFORMATION_OUTPUTS_SCHEMA,
    LIST_TRANSFORMATION_RUNS_SCHEMA,
    LIST_TRANSFORMATION_STEPS_SCHEMA,
    START_TRANSFORMATION_RUN_SCHEMA,
    validate_bif_input,
)


@dataclass(frozen=True, kw_only=True)
class TransformationBifApplicationServices:
    """Contain only application-layer dependencies injected by the BIF host."""

    execution: TransformationExecutionApplicationService
    runs: TransformationRunReadService
    steps: TransformationStepReadService
    outputs: TransformationOutputReadService
    diagnostics: TransformationDiagnosticsReadService
    context_builder: TransformationBifTrustedContextBuilder
    error_mapper: TransformationBifErrorMapper


def resource_registry_start_transformation_run(
    request: object,
    context_manager: object,
    **payload: object,
) -> str:
    """Submit one transformation asynchronously through the application command service."""
    correlation_id = _optional_text(payload.get("correlation_id"))

    def invoke(services: TransformationBifApplicationServices) -> Mapping[str, object]:
        validate_bif_input(
            schema=START_TRANSFORMATION_RUN_SCHEMA,
            payload=payload,
            correlation_id=correlation_id,
        )
        active_correlation_id = correlation_id or _context_correlation_id(request=request)
        caller = services.context_builder.build_execution_context(
            request=request,
            context_manager=context_manager,
            function_name="resource_registry_start_transformation_run",
            requested_tenant_id=_optional_text(payload.get("tenant_id")),
            correlation_id=active_correlation_id,
        )
        bindings = tuple(
            TransformationInputBinding(
                input_name=str(item["input_name"]),
                dataset_id=str(item["dataset_id"]),
                dataset_version_id=str(item["dataset_version_id"]),
                artifact_id=(
                    str(item["artifact_id"]) if item.get("artifact_id") is not None else None
                ),
            )
            for item in cast(list[Mapping[str, object]], payload["input_bindings"])
        )
        application_request = TransformationExecutionRequest(
            tenant_id=caller.tenant_id,
            definition_id=str(payload["definition_id"]),
            definition_version_id=str(payload["definition_version_id"]),
            parameters=cast(Mapping[str, object], payload["parameters"]),
            input_bindings=bindings,
            caller_idempotency_key=str(payload["idempotency_key"]),
            correlation_id=active_correlation_id,
        )
        accepted = services.execution.execute(request=application_request, caller=caller)
        return _map_accepted(accepted)

    return _invoke_safely(request=request, correlation_id=correlation_id, invocation=invoke)


def resource_registry_get_transformation_run(
    request: object,
    context_manager: object,
    **payload: object,
) -> str:
    """Return one authorized public transformation run reference."""
    return _invoke_read(
        request=request,
        context_manager=context_manager,
        payload=payload,
        function_name="resource_registry_get_transformation_run",
        schema=GET_TRANSFORMATION_RUN_SCHEMA,
        operation=lambda services, context: {
            "run": _map_run(
                services.runs.get_run(context=context, run_id=str(payload["run_id"])),
            ),
        },
    )


def resource_registry_list_transformation_runs(
    request: object,
    context_manager: object,
    **payload: object,
) -> str:
    """Return the approved filtered and paginated transformation run list."""

    def operation(
        services: TransformationBifApplicationServices,
        context: TransformationReadContext,
    ) -> Mapping[str, object]:
        limit = cast(int, payload.get("limit", 50))
        offset = cast(int, payload.get("offset", 0))
        runs = services.runs.list_runs(
            context=context,
            filters=TransformationRunFilters(
                status=(
                    RunStatus(str(payload["status"])) if payload.get("status") is not None else None
                ),
                definition_id=_optional_text(payload.get("definition_id")),
                definition_version_id=_optional_text(payload.get("definition_version_id")),
                created_after=_parse_datetime(payload.get("created_after")),
                created_before=_parse_datetime(payload.get("created_before")),
                correlation_id=_optional_text(payload.get("correlation_id")),
            ),
            limit=limit,
            offset=offset,
        )
        return {
            "runs": [_map_run(run) for run in runs],
            "pagination": {"limit": limit, "offset": offset, "returned": len(runs)},
        }

    return _invoke_read(
        request=request,
        context_manager=context_manager,
        payload=payload,
        function_name="resource_registry_list_transformation_runs",
        schema=LIST_TRANSFORMATION_RUNS_SCHEMA,
        operation=operation,
    )


def resource_registry_list_transformation_steps(
    request: object,
    context_manager: object,
    **payload: object,
) -> str:
    """Return ordered public step-attempt summaries for one run."""
    return _invoke_read(
        request=request,
        context_manager=context_manager,
        payload=payload,
        function_name="resource_registry_list_transformation_steps",
        schema=LIST_TRANSFORMATION_STEPS_SCHEMA,
        operation=lambda services, context: {
            "steps": [
                _map_step(step)
                for step in services.steps.list_steps(
                    context=context,
                    run_id=str(payload["run_id"]),
                )
            ],
        },
    )


def resource_registry_list_transformation_outputs(
    request: object,
    context_manager: object,
    **payload: object,
) -> str:
    """Return ordered public output references without storage details."""
    return _invoke_read(
        request=request,
        context_manager=context_manager,
        payload=payload,
        function_name="resource_registry_list_transformation_outputs",
        schema=LIST_TRANSFORMATION_OUTPUTS_SCHEMA,
        operation=lambda services, context: {
            "outputs": [
                _map_output(output)
                for output in services.outputs.list_outputs(
                    context=context,
                    run_id=str(payload["run_id"]),
                )
            ],
        },
    )


def resource_registry_get_transformation_diagnostics(
    request: object,
    context_manager: object,
    **payload: object,
) -> str:
    """Return sanitized aggregate diagnostics for one authorized run."""
    return _invoke_read(
        request=request,
        context_manager=context_manager,
        payload=payload,
        function_name="resource_registry_get_transformation_diagnostics",
        schema=GET_TRANSFORMATION_DIAGNOSTICS_SCHEMA,
        operation=lambda services, context: {
            "diagnostics": _map_diagnostics(
                services.diagnostics.get_run_diagnostics(
                    context=context,
                    run_id=str(payload["run_id"]),
                ),
            ),
        },
    )


def _invoke_read(  # noqa: PLR0913
    *,
    request: object,
    context_manager: object,
    payload: Mapping[str, object],
    function_name: str,
    schema: Mapping[str, object],
    operation: Callable[
        [TransformationBifApplicationServices, TransformationReadContext],
        Mapping[str, object],
    ],
) -> str:
    correlation_id = _optional_text(payload.get("correlation_id"))

    def invoke(services: TransformationBifApplicationServices) -> Mapping[str, object]:
        validate_bif_input(schema=schema, payload=payload, correlation_id=correlation_id)
        context = services.context_builder.build_read_context(
            request=request,
            context_manager=context_manager,
            function_name=function_name,
            requested_tenant_id=_optional_text(payload.get("tenant_id")),
            correlation_id=correlation_id,
        )
        return {
            "bif_contract_version": TRANSFORMATION_BIF_CONTRACT_VERSION,
            **operation(services, context),
        }

    return _invoke_safely(request=request, correlation_id=correlation_id, invocation=invoke)


def _invoke_safely(
    *,
    request: object,
    correlation_id: str | None,
    invocation: Callable[[TransformationBifApplicationServices], Mapping[str, object]],
) -> str:
    services: TransformationBifApplicationServices | None = None
    try:
        services = _get_services(request=request, correlation_id=correlation_id)
        return success_result(data=invocation(services))
    except ApplicationError as error:
        mapper = services.error_mapper if services is not None else TransformationBifErrorMapper()
        return mapper.map_error(error=error)
    except Exception:
        mapper = services.error_mapper if services is not None else TransformationBifErrorMapper()
        return mapper.map_error(
            error=InternalError(
                code="transformation_bif_internal_error",
                public_message="The transformation built-in function could not be completed.",
                correlation_id=correlation_id,
            ),
        )


def _get_services(
    *,
    request: object,
    correlation_id: str | None,
) -> TransformationBifApplicationServices:
    services = getattr(request, "transformation_bif_services", None)
    if not isinstance(services, TransformationBifApplicationServices):
        raise RuntimeUnavailableError(
            code="transformation_bif_services_unavailable",
            public_message="Transformation built-in function services are unavailable.",
            retryable=True,
            correlation_id=correlation_id,
        )
    return services


def _map_accepted(value: TransformationExecutionAccepted) -> Mapping[str, object]:
    return {
        "bif_contract_version": TRANSFORMATION_BIF_CONTRACT_VERSION,
        "asynchronous": True,
        "message": "Transformation accepted; use read functions to inspect progress.",
        "run": _map_run(value.run),
        "execution_state": value.execution_state.value,
        "idempotent_replay": value.idempotent_replay,
        "correlation_id": value.correlation_id,
    }


def _map_run(value: TransformationRunReference) -> Mapping[str, object]:
    return {
        "contract_version": value.contract_version,
        "tenant_id": value.tenant_id,
        "run_id": value.run_id,
        "definition_id": value.definition_id,
        "definition_version_id": value.definition_version_id,
        "status": value.status.value,
    }


def _map_step(value: TransformationStepSummary) -> Mapping[str, object]:
    return {
        "contract_version": value.contract_version,
        "step_id": value.step_id,
        "attempt_number": value.attempt_number,
        "status": value.status.value,
        "duration_seconds": value.duration_seconds,
        "error_code": value.error_code,
    }


def _map_output(value: TransformationOutputReference) -> Mapping[str, object]:
    return {
        "contract_version": value.contract_version,
        "output_id": value.output_id,
        "logical_name": value.logical_name,
        "kind": value.kind.value,
        "status": value.status.value,
        "dataset_id": value.dataset_id,
        "dataset_version_id": value.dataset_version_id,
        "artifact_id": value.artifact_id,
    }


def _map_diagnostics(value: TransformationDiagnosticSummary) -> Mapping[str, object]:
    return {
        "contract_version": value.contract_version,
        "health_state": value.health_state.value,
        "aggregate_counts": dict(value.aggregate_counts),
        "warnings": list(value.warnings),
        "durations": dict(value.durations),
        "duration_seconds": value.duration_seconds,
        "progress": value.progress,
    }


def _context_correlation_id(*, request: object) -> str:
    value = getattr(request, "correlation_id", None)
    return value if isinstance(value, str) and value.strip() else str(uuid4())


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
