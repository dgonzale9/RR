"""Project existing Registry operations into four model-facing tool contracts."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType
from typing import cast

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from resource_registry.bifs.transformation.registration import TRANSFORMATION_BIF_REGISTRY
from resource_registry.performance.instrumentation import (
    RegistryPerformanceOutcome,
    RegistryPerformanceStage,
    instrument_registry_function,
)

GROUPED_REGISTRY_ACTIONS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "resource_registry_inspect": (
            "list_datasets",
            "get_dataset",
            "get_dataset_context",
            "get_dataset_schema",
            "get_dataset_profile",
            "list_dataset_versions",
            "list_external_jobs",
            "explore_dataset",
            "analyze_dataset",
        ),
        "resource_registry_query": (
            "plan_query",
            "query_dataset",
            "create_filtered_dataset",
            "get_query_run",
            "start_query_job",
            "get_query_job_status",
            "get_query_job_result",
        ),
        "resource_registry_manage_outputs": (
            "create_chart",
            "plan_export",
            "generate_artifact",
            "get_analysis_artifact",
            "get_chart_artifact",
            "get_export_plan",
            "start_intelligence_job",
            "get_intelligence_job_status",
            "get_intelligence_job_result",
        ),
        "resource_registry_manage_transformations": (
            "start_transformation_run",
            "get_transformation_run",
            "list_transformation_runs",
            "list_transformation_steps",
            "list_transformation_outputs",
            "get_transformation_diagnostics",
        ),
    }
)
_OPERATION_TO_GROUP = {
    f"resource_registry_{action}": (group, action)
    for group, actions in GROUPED_REGISTRY_ACTIONS.items()
    for action in actions
}
GROUPED_REGISTRY_OPERATION_NAMES = frozenset(_OPERATION_TO_GROUP)
_GROUP_DESCRIPTIONS = {
    "resource_registry_inspect": "Discover, inspect, explore and analyze datasets.",
    "resource_registry_query": (
        "Plan and execute queries, inspect query jobs and create filtered datasets."
    ),
    "resource_registry_manage_outputs": "Manage dataset outputs using the assigned actions.",
    "resource_registry_manage_transformations": (
        "Submit transformations and inspect their runs, steps, outputs and diagnostics."
    ),
}

_VERSION_SELECTION_GUIDANCE = (
    "Dataset version selection: default to current_version_id or the READY version with "
    "is_current=true, even when a higher version_number exists. READY means readable, not current. "
    "Version history is newest-first; never select its first entry merely because it is newest. "
    "Use a historical version only when the user explicitly requests it. "
    "If the current version is unknown or absent from the page, inspect dataset metadata; "
    "if no current_version_id is assigned, resolve the schema without version_id. "
    "Pin the resolved version for the operation requested by the user. "
    "Offer only actions exposed in the currently available tools; never promise unavailable "
    "capabilities. For version-listing requests, report the versions and current selection "
    "without promising unrequested future work. "
    "When the user restores or changes the current version, refresh metadata before selecting it."
)


@dataclass(frozen=True, kw_only=True)
class FunctionToolDefinition:
    """Carry a provider-neutral function schema without granting execution authority."""

    name: str
    description: str
    parameters: dict[str, object]


@dataclass(frozen=True, kw_only=True)
class ResolvedRegistryToolCall:
    """Carry the canonical operation requiring the existing admission checks."""

    function_name: str
    arguments: dict[str, object]
    action: str


class InvalidGroupedRegistryCallError(ValueError):
    """Report a malformed grouped call without exposing caller input."""


def resolve_grouped_registry_call(tool_name: str, payload: object) -> ResolvedRegistryToolCall:
    """Resolve a registered tool/action pair without authorizing its execution.

    Args:
        tool_name: Model-facing Registry tool name.
        payload: Parsed arguments or a JSON object string.

    Returns:
        The original operation and a copy of its arguments.

    Raises:
        InvalidGroupedRegistryCallError: If the envelope or tool/action pair is invalid.
    """
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError):
            raise InvalidGroupedRegistryCallError(
                "Expected a JSON object with action and arguments."
            ) from None
    if not isinstance(payload, dict) or set(payload) != {"action", "arguments"}:
        raise InvalidGroupedRegistryCallError("Expected a JSON object with action and arguments.")
    action = payload["action"]
    arguments = payload["arguments"]
    if not isinstance(action, str) or action not in GROUPED_REGISTRY_ACTIONS.get(tool_name, ()):
        raise InvalidGroupedRegistryCallError("The action is not supported by this Registry tool.")
    if not isinstance(arguments, dict) or not all(isinstance(key, str) for key in arguments):
        raise InvalidGroupedRegistryCallError("arguments must be a JSON object.")
    return ResolvedRegistryToolCall(
        function_name=f"resource_registry_{action}",
        arguments=dict(arguments),
        action=action,
    )


def _get_operation_schema(tool: FunctionToolDefinition) -> dict[str, object]:
    transformation = TRANSFORMATION_BIF_REGISTRY.get(tool.name)
    schema = deepcopy(dict(transformation.input_schema) if transformation else tool.parameters)
    schema.pop("$schema", None)
    properties = cast(dict[str, dict[str, object]], schema.setdefault("properties", {}))
    if tool.name == "resource_registry_explore_dataset":
        for name in ("columns", "terms"):
            if name in properties:
                properties[name] = {
                    "anyOf": [
                        {"type": "string"},
                        {"type": "array", "items": {"type": "string"}},
                        {
                            "type": "object",
                            "properties": {name: {"type": "array", "items": {"type": "string"}}},
                            "required": [name],
                            "additionalProperties": False,
                        },
                    ],
                    "description": (
                        f"Supply a string, string array, or object wrapping that array under {name}."
                    ),
                }
    # The dispatcher fills these selectors from verified context before checking requirements.
    required = cast(list[str], schema.get("required", []))
    schema["required"] = [name for name in required if name not in {"tenant_id", "dataset_id"}]
    return schema


@instrument_registry_function(RegistryPerformanceStage.GROUPED_SCHEMA_BUILD)
def build_grouped_registry_tools(
    tools: Sequence[FunctionToolDefinition],
    *,
    excluded_actions: frozenset[str] = frozenset(),
) -> list[FunctionToolDefinition]:
    """Replace assigned Registry tools with nonempty groups of those exact operations.

    Args:
        tools: Tools already selected from current Persona assignments.
        excluded_actions: Actions omitted for this request's presentation policy.

    Returns:
        Unrelated definitions unchanged, followed by at most four Registry tools.
    """
    assigned = {tool.name: tool for tool in tools}
    result = [tool for tool in tools if tool.name not in _OPERATION_TO_GROUP]
    for group, actions in GROUPED_REGISTRY_ACTIONS.items():
        available = [
            action
            for action in actions
            if f"resource_registry_{action}" in assigned and action not in excluded_actions
        ]
        if not available:
            continue
        properties: dict[str, dict[str, object]] = {}
        descriptions = [_GROUP_DESCRIPTIONS[group]]
        if group != "resource_registry_manage_transformations":
            descriptions.append(_VERSION_SELECTION_GUIDANCE)
        descriptions.append("Choose exactly one action. Action arguments:")
        for action in available:
            tool = assigned[f"resource_registry_{action}"]
            schema = _get_operation_schema(tool)
            operation_properties = cast(dict[str, dict[str, object]], schema.get("properties", {}))
            for name, definition in operation_properties.items():
                properties.setdefault(name, definition)
            required = cast(list[str], schema.get("required", []))
            descriptions.append(
                f"{action}: {tool.description} "
                f"Arguments: {', '.join(operation_properties) or 'none'}. "
                f"Required: {', '.join(required) or 'none'}."
            )
        result.append(
            FunctionToolDefinition(
                name=group,
                description="\n".join(descriptions),
                parameters={
                    "type": "object",
                    "required": ["action", "arguments"],
                    "additionalProperties": False,
                    "properties": {
                        "action": {"type": "string", "enum": available},
                        "arguments": {
                            "type": "object",
                            "properties": properties,
                            "additionalProperties": False,
                            "description": (
                                "Supply only arguments for the selected action. "
                                "Omitted selectors use verified conversation context."
                            ),
                        },
                    },
                },
            )
        )
    return result


@instrument_registry_function(
    RegistryPerformanceStage.GROUPED_SCHEMA_VALIDATION,
    operation_parameter="definition",
    outcome_classifier=lambda result: (
        RegistryPerformanceOutcome.SUCCESS
        if result is True
        else RegistryPerformanceOutcome.REJECTED
    ),
)
def is_valid_grouped_registry_arguments(
    definition: FunctionToolDefinition, arguments: Mapping[str, object]
) -> bool:
    """Check action-specific argument shapes without changing defaults or values.

    Args:
        definition: Original operation definition from admitted parameter metadata.
        arguments: Effective arguments after verified selector resolution.

    Returns:
        Whether the transport matches the selected operation's input schema.
    """
    schema = _get_operation_schema(definition)
    schema["additionalProperties"] = False
    required = cast(list[str], schema.get("required", []))
    # Existing optional arguments accept None; retain it for the original handler.
    payload = {
        name: value for name, value in arguments.items() if value is not None or name in required
    }
    return bool(Draft202012Validator(schema).is_valid(payload))


@instrument_registry_function(RegistryPerformanceStage.GROUPED_RESULT_PROJECTION)
def project_registry_tool_result(result: str) -> str:
    """Translate operation hints for grouped callers without altering business data.

    Args:
        result: Unmodified JSON response from the original operation.

    Returns:
        The same response with model-facing recommendation metadata translated.
    """
    try:
        payload = json.loads(result)
    except (ValueError, TypeError):
        return result
    if not isinstance(payload, dict):
        return result
    changed = False
    for prefix in ("suggested", "recommended"):
        name = payload.get(f"{prefix}_bif")
        if isinstance(name, str) and name in _OPERATION_TO_GROUP:
            group, action = _OPERATION_TO_GROUP[name]
            payload[f"{prefix}_bif"] = group
            payload[f"{prefix}_action"] = action
            changed = True
    for key in ("hint", "error"):
        if isinstance(payload.get(key), str):
            original = payload[key]
            for name, (group, action) in _OPERATION_TO_GROUP.items():
                payload[key] = re.sub(
                    rf"\b{re.escape(name)}\b", f"{group} with action='{action}'", payload[key]
                )
            changed = changed or payload[key] != original
    return json.dumps(payload, ensure_ascii=False) if changed else result
