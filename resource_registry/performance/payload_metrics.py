"""Measure published Registry tool shape without retaining its contents."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

MAX_PAYLOAD_METRIC_VALUE = 2_147_483_647
_SCHEMA_KEYS = ("parameters", "input_schema", "parametersJsonSchema")


@dataclass(frozen=True)
class RegistryPayloadMeasurements:
    """Carry only numeric aggregates from the effective publication output."""

    tool_count: int
    action_count: int
    schema_property_count: int
    description_char_count: int
    payload_bytes: int


def bound_nonnegative_payload_value(value: int) -> int:
    """Clamp a published numeric aggregate to the metric contract.

    Args:
        value: Count or byte size calculated for one publication.

    Returns:
        A nonnegative integer within the public metric bound.
    """
    return min(max(0, value), MAX_PAYLOAD_METRIC_VALUE)


def _count_schema_properties_and_descriptions(value: object) -> tuple[int, int]:
    """Count concrete property entries and description characters recursively.

    Args:
        value: Provider-formatted Registry tool definition or nested schema node.

    Returns:
        Property entry count and Unicode description character count.
    """
    property_count = 0
    description_char_count = 0
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, Mapping):
            properties = current.get("properties")
            if isinstance(properties, Mapping):
                property_count += len(properties)
            description = current.get("description")
            if isinstance(description, str):
                description_char_count += len(description)
            pending.extend(current.values())
        elif isinstance(current, (list, tuple)):
            pending.extend(current)
    return property_count, description_char_count


def _count_published_actions(tool: Mapping[str, object]) -> int:
    """Count actions in the actual grouped schema's action enum.

    Args:
        tool: One provider-formatted Registry grouped tool.

    Returns:
        Number of published string actions in this tool.
    """
    function = tool.get("function", tool)
    if not isinstance(function, Mapping):
        return 0
    schema = next((function[key] for key in _SCHEMA_KEYS if key in function), None)
    if not isinstance(schema, Mapping):
        return 0
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        return 0
    action = properties.get("action")
    if not isinstance(action, Mapping):
        return 0
    enum = action.get("enum")
    if not isinstance(enum, (list, tuple)):
        return 0
    return sum(isinstance(value, str) for value in enum)


def measure_published_registry_tools(
    tools: Sequence[Mapping[str, object]],
) -> RegistryPayloadMeasurements:
    """Measure the effective provider-formatted Registry publication output.

    Args:
        tools: Grouped definitions returned by Registry publication, not arguments.

    Returns:
        Bounded numeric counts and deterministic serialized UTF-8 byte size.

    Raises:
        TypeError: If a published definition cannot be JSON serialized.
        ValueError: If a published definition contains a non-JSON numeric value.
    """
    serialized = json.dumps(
        tools, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    property_count = 0
    description_char_count = 0
    action_count = 0
    for tool in tools:
        tool_properties, tool_descriptions = _count_schema_properties_and_descriptions(tool)
        property_count += tool_properties
        description_char_count += tool_descriptions
        action_count += _count_published_actions(tool)
    return RegistryPayloadMeasurements(
        tool_count=bound_nonnegative_payload_value(len(tools)),
        action_count=bound_nonnegative_payload_value(action_count),
        schema_property_count=bound_nonnegative_payload_value(property_count),
        description_char_count=bound_nonnegative_payload_value(description_char_count),
        payload_bytes=bound_nonnegative_payload_value(len(serialized.encode("utf-8"))),
    )


def measure_registry_publication_result(result: object) -> RegistryPayloadMeasurements:
    """Extract the effective tool list from the existing publication result.

    Args:
        result: Unmodified return value of ``prepare_registry_tools``.

    Returns:
        Numeric measurements of its provider-formatted Registry tools.

    Raises:
        TypeError: If the result is not the expected publication shape.
    """
    if not isinstance(result, tuple) or len(result) != 2 or not isinstance(result[1], list):
        raise TypeError("Unexpected Registry publication result shape.")
    tools = result[1]
    if not all(isinstance(tool, Mapping) for tool in tools):
        raise TypeError("Unexpected Registry tool definition shape.")
    return measure_published_registry_tools(cast(list[Mapping[str, object]], tools))
