"""Expose the four Registry transport entry points using the active dispatch client."""

from __future__ import annotations

from BotServer.bif_execution_admission import BuiltInFunctionExecutionChannel
from resource_registry.bifs import grouped_runtime


def resource_registry_inspect(
    client: object,
    request: object,
    function_args: object,
    *,
    execution_channel: BuiltInFunctionExecutionChannel = BuiltInFunctionExecutionChannel.LLM_TOOL,
) -> object:
    """Dispatch one inspection action, including dataset analysis.

    Args:
        client: Active client retaining its Persona, context and admission service.
        request: Original request with its authenticated execution context.
        function_args: Parsed or JSON-encoded action/arguments envelope.
        execution_channel: Trusted channel supplied by the caller.

    Returns:
        The grouped dispatch result, including existing safe error envelopes.

    Raises:
        Exception: Propagate original operation failures unchanged.
    """
    return grouped_runtime.execute_grouped_registry_call(
        client,
        request,
        "resource_registry_inspect",
        function_args,
        execution_channel=execution_channel,
    )


def resource_registry_query(
    client: object,
    request: object,
    function_args: object,
    *,
    execution_channel: BuiltInFunctionExecutionChannel = BuiltInFunctionExecutionChannel.LLM_TOOL,
) -> object:
    """Dispatch one synchronous or asynchronous query action.

    Args:
        client: Active client retaining its Persona, context and admission service.
        request: Original request with its authenticated execution context.
        function_args: Parsed or JSON-encoded action/arguments envelope.
        execution_channel: Trusted channel supplied by the caller.

    Returns:
        The grouped dispatch result, including existing safe error envelopes.

    Raises:
        Exception: Propagate original operation failures unchanged.
    """
    return grouped_runtime.execute_grouped_registry_call(
        client,
        request,
        "resource_registry_query",
        function_args,
        execution_channel=execution_channel,
    )


def resource_registry_manage_outputs(
    client: object,
    request: object,
    function_args: object,
    *,
    execution_channel: BuiltInFunctionExecutionChannel = BuiltInFunctionExecutionChannel.LLM_TOOL,
) -> object:
    """Dispatch one artifact or intelligence job action.

    Args:
        client: Active client retaining its Persona, context and admission service.
        request: Original request with its authenticated execution context.
        function_args: Parsed or JSON-encoded action/arguments envelope.
        execution_channel: Trusted channel supplied by the caller.

    Returns:
        The grouped dispatch result, including existing safe error envelopes.

    Raises:
        Exception: Propagate original operation failures unchanged.
    """
    return grouped_runtime.execute_grouped_registry_call(
        client,
        request,
        "resource_registry_manage_outputs",
        function_args,
        execution_channel=execution_channel,
    )


def resource_registry_manage_transformations(
    client: object,
    request: object,
    function_args: object,
    *,
    execution_channel: BuiltInFunctionExecutionChannel = BuiltInFunctionExecutionChannel.LLM_TOOL,
) -> object:
    """Dispatch one transformation run, step, output or diagnostics action.

    Args:
        client: Active client retaining its Persona, context and admission service.
        request: Original request with its authenticated execution context.
        function_args: Parsed or JSON-encoded action/arguments envelope.
        execution_channel: Trusted channel supplied by the caller.

    Returns:
        The grouped dispatch result, including existing safe error envelopes.

    Raises:
        Exception: Propagate original operation failures unchanged.
    """
    return grouped_runtime.execute_grouped_registry_call(
        client,
        request,
        "resource_registry_manage_transformations",
        function_args,
        execution_channel=execution_channel,
    )
