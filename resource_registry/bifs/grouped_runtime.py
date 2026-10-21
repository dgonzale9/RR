"""Translate grouped Registry calls into the existing canonical BIF dispatcher."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from time import perf_counter
from typing import Protocol, cast

from BotServer.bif_execution_admission import (
    BuiltInFunctionExecutionAdmissionError,
    BuiltInFunctionExecutionAdmissionService,
    BuiltInFunctionExecutionChannel,
)
from BotServer.models import FunctionParameter, Persona
from resource_registry.assistant.dataset_selection_context import (
    RegistryDatasetSelectionRequired,
    resolve_registry_selection_tool_arguments,
)
from resource_registry.bifs.grouped_publication import build_registry_operation_definition
from resource_registry.bifs.grouped_tools import (
    GROUPED_REGISTRY_ACTIONS,
    InvalidGroupedRegistryCallError,
    ResolvedRegistryToolCall,
    is_valid_grouped_registry_arguments,
    project_registry_tool_result,
    resolve_grouped_registry_call,
)
from resource_registry.bifs.manifest import RESOURCE_REGISTRY_BIF_MANIFEST


class _RegistryDispatchClient(Protocol):
    persona: Persona | None
    appLogger: logging.Logger
    bif_execution_admission_service: BuiltInFunctionExecutionAdmissionService

    def callGenericFunction(
        self,
        request: object,
        function_name: str,
        function_args: object,
        *,
        execution_channel: BuiltInFunctionExecutionChannel,
    ) -> object: ...


class _RegistryRequest(Protocol):
    persona: Persona | None


def is_grouped_registry_tool(function_name: object) -> bool:
    """Identify the four Registry transport names without consulting assignments.

    Args:
        function_name: Untrusted model tool name.

    Returns:
        Whether the name is a registered grouped Registry tool.
    """
    return isinstance(function_name, str) and function_name in GROUPED_REGISTRY_ACTIONS


def is_registry_tool_name(function_name: object) -> bool:
    """Recognize pending grouped and legacy Registry calls for normal admission.

    Args:
        function_name: Untrusted model tool name.

    Returns:
        Whether the name belongs to either supported Registry transport contract.
    """
    return is_grouped_registry_tool(function_name) or (
        isinstance(function_name, str) and function_name in RESOURCE_REGISTRY_BIF_MANIFEST
    )


def _dispatch_admitted_operation(
    client: _RegistryDispatchClient,
    request: object,
    resolved: ResolvedRegistryToolCall,
    execution_channel: BuiltInFunctionExecutionChannel,
) -> object:
    admission = client.bif_execution_admission_service.admit(
        active_persona=client.persona,
        requested_function_name=resolved.function_name,
        execution_channel=execution_channel,
    )
    parameters = list(
        cast(
            Iterable[FunctionParameter],
            FunctionParameter.objects.filter(function=admission.function),
        )
    )
    cast(_RegistryRequest, request).persona = client.persona
    parameter_names = frozenset(parameter.name for parameter in parameters)
    arguments = resolve_registry_selection_tool_arguments(
        request=request,
        function_name=resolved.function_name,
        function_args=resolved.arguments,
        parameter_names=parameter_names,
    )
    if (
        set(arguments) - parameter_names
        or any(parameter.required and parameter.name not in arguments for parameter in parameters)
        or not is_valid_grouped_registry_arguments(
            build_registry_operation_definition(admission.function, parameters), arguments
        )
    ):
        return json.dumps(
            {
                "success": False,
                "status": 400,
                "code": "invalid_registry_tool_arguments",
                "error": "Arguments do not match the selected Registry action.",
            }
        )
    # Reuse the unchanged dispatcher, including its live admission and result recording.
    return client.callGenericFunction(
        request, resolved.function_name, arguments, execution_channel=execution_channel
    )


def execute_grouped_registry_call(
    client: object,
    request: object,
    function_name: str,
    function_args: object,
    *,
    execution_channel: BuiltInFunctionExecutionChannel,
) -> object:
    """Validate one Registry action and execute its original handler through normal admission.

    Args:
        client: Existing BotServer client supplying admission and canonical dispatch.
        request: Runtime request; attach the Persona for verified selector resolution.
        function_name: One of the four grouped Registry tool names.
        function_args: JSON string or parsed action/arguments envelope.
        execution_channel: Original trusted execution channel, forwarded unchanged.

    Returns:
        Original handler output with Registry recommendations translated, or a safe error.

    Raises:
        Exception: Propagate original dispatch and handler failures unchanged.
    """
    dispatch_client = cast(_RegistryDispatchClient, client)
    started_at = perf_counter()
    action: str | None = None
    outcome = "failed"
    try:
        try:
            resolved = resolve_grouped_registry_call(function_name, function_args)
            action = resolved.action
            result = _dispatch_admitted_operation(
                dispatch_client, request, resolved, execution_channel
            )
        except InvalidGroupedRegistryCallError as error:
            result = json.dumps(
                {
                    "success": False,
                    "status": 400,
                    "code": "invalid_registry_tool_call",
                    "error": str(error),
                }
            )
        except RegistryDatasetSelectionRequired as error:
            result = json.dumps(
                {"success": False, "status": 400, "code": error.code, "error": str(error)}
            )
        except BuiltInFunctionExecutionAdmissionError as error:
            result = json.dumps(
                {"success": False, "status": 403, "code": error.code.value, "error": str(error)}
            )
        outcome = "completed"
        if isinstance(result, str):
            try:
                payload = json.loads(result)
                if isinstance(payload, dict) and payload.get("success") is False:
                    outcome = "denied_or_failed"
            except ValueError:
                outcome = "non_json_result"
            return project_registry_tool_result(result)
        return result
    finally:
        dispatch_client.appLogger.info(
            "Grouped Registry tool completed.",
            extra={
                "event_code": "REGISTRY_GROUPED_TOOL_COMPLETED",
                "tool_name": (
                    function_name if is_grouped_registry_tool(function_name) else "<invalid>"
                ),
                "action": action,
                "outcome": outcome,
                "duration_ms": round((perf_counter() - started_at) * 1000, 3),
            },
        )
