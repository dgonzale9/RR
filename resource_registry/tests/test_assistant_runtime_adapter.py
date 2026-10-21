"""Test Resource Registry tool-result prompt-context compaction."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from resource_registry.assistant.exploration_routing import AssistantToolCall
from resource_registry.assistant.runtime_adapter import DataRegistryExplorationRoutingAdapter

PROMPT_ITEM_LIMIT = 5
SCHEMA_PROMPT_COLUMN_LIMIT = 64
RAW_ITEM_COUNT = 6
PROMPT_TEXT_LIMIT = 240


class _RecordingContextManager:
    """Capture tool-context writes made by the routing adapter."""

    def __init__(self) -> None:
        """Initialize the captured function payload list."""
        self.function_payloads: list[dict[str, Any]] = []

    def update_current_context_entry(self, request: object, *, function: dict[str, Any]) -> None:
        """Record one tool-call context payload.

        Args:
            request: Current assistant request.
            function: Tool-call context payload written by the adapter.
        """
        _ = request
        self.function_payloads.append(function)


class _ToolClient:
    """Return a configured tool result while exposing recorded context writes."""

    def __init__(self, raw_result: dict[str, Any]) -> None:
        """Initialize the client with one JSON-compatible tool result.

        Args:
            raw_result: Complete BIF result returned during the active routing flow.
        """
        self.context_mgr = _RecordingContextManager()
        self._raw_result = raw_result

    def callGenericFunction(  # noqa: N802
        self,
        request: object,
        function_name: str,
        function_args: dict[str, object],
    ) -> str:
        """Return the complete configured BIF payload.

        Args:
            request: Current assistant request.
            function_name: Canonical BIF name.
            function_args: Canonical BIF arguments.

        Returns:
            JSON-encoded complete BIF result.
        """
        _ = request, function_name, function_args
        return json.dumps(self._raw_result)


def _recorded_result(context_manager: _RecordingContextManager) -> dict[str, Any]:
    function_payload = context_manager.function_payloads[-1]
    raw_result = function_payload["tool_calls"][0]["result"]
    assert isinstance(raw_result, str)
    parsed_result = json.loads(raw_result)
    assert isinstance(parsed_result, dict)
    return parsed_result


def _record_result(
    *,
    tool_name: str,
    raw_result: Any,
) -> tuple[Any, dict[str, Any]]:
    context_manager = _RecordingContextManager()
    client = SimpleNamespace(context_mgr=context_manager)
    tool_call = AssistantToolCall(name=tool_name, arguments={"tenant_id": "tenant-1"})
    adapter = DataRegistryExplorationRoutingAdapter()

    adapter._record_tool_result(
        client=client,
        request=SimpleNamespace(),
        tool_call=tool_call,
        raw_result=raw_result,
    )

    return raw_result, _recorded_result(context_manager)


def test_exploration_context_result_is_bounded_without_mutating_raw_result() -> None:
    raw_result = {
        "success": True,
        "operation": "distinct_values",
        "column": "category",
        "values": [{"value": f"category-{index}"} for index in range(RAW_ITEM_COUNT)],
        "row_count": RAW_ITEM_COUNT,
        "limit": 20,
        "truncated": False,
    }

    returned_raw_result, stored_result = _record_result(
        tool_name="resource_registry_explore_dataset",
        raw_result=raw_result,
    )

    assert returned_raw_result is raw_result
    raw_values = raw_result["values"]
    assert isinstance(raw_values, list)
    assert len(raw_values) == RAW_ITEM_COUNT
    assert stored_result["values"] == [
        {"value": f"category-{index}"} for index in range(PROMPT_ITEM_LIMIT)
    ]
    assert stored_result["row_count"] == RAW_ITEM_COUNT
    assert stored_result["truncated"] is False


def test_exploration_execution_keeps_full_payload_for_current_turn_reasoning() -> None:
    raw_result = {
        "success": True,
        "operation": "distinct_values",
        "values": [{"value": f"category-{index}"} for index in range(RAW_ITEM_COUNT)],
    }
    client = _ToolClient(raw_result)
    tool_call = AssistantToolCall(
        name="resource_registry_explore_dataset",
        arguments={"tenant_id": "tenant-1"},
    )
    adapter = DataRegistryExplorationRoutingAdapter()

    active_payload = adapter._execute_tool_call(
        client=client,
        request=SimpleNamespace(),
        tool_call=tool_call,
    )
    stored_result = _recorded_result(client.context_mgr)

    assert len(active_payload["values"]) == RAW_ITEM_COUNT
    assert len(stored_result["values"]) == PROMPT_ITEM_LIMIT


def test_exploration_context_result_bounds_long_text_values() -> None:
    long_value = "x" * 300
    _, stored_result = _record_result(
        tool_name="resource_registry_explore_dataset",
        raw_result={
            "success": True,
            "operation": "sample_values",
            "rows": [{"cells": [{"column": "notes", "value": long_value}]}],
        },
    )

    stored_value = stored_result["rows"][0]["cells"][0]["value"]
    assert stored_value == long_value[:PROMPT_TEXT_LIMIT]


def test_schema_context_result_excludes_provider_metadata_and_bounds_columns() -> None:
    schema_column_count = SCHEMA_PROMPT_COLUMN_LIMIT + 1
    _, stored_result = _record_result(
        tool_name="resource_registry_get_dataset_schema",
        raw_result={
            "success": True,
            "data": {
                "schema": {
                    "column_count": schema_column_count,
                    "source_uri": "s3://private-bucket/source.parquet",
                    "schema_json": [
                        {"name": f"column_{index}", "type": "VARCHAR", "path": "/private"}
                        for index in range(schema_column_count)
                    ],
                },
            },
        },
    )

    schema = stored_result["data"]["schema"]
    assert schema["column_count"] == schema_column_count
    assert schema["schema_json"] == [
        {"name": f"column_{index}", "type": "VARCHAR"}
        for index in range(SCHEMA_PROMPT_COLUMN_LIMIT)
    ]
    assert "source_uri" not in json.dumps(stored_result)
    assert '"path"' not in json.dumps(stored_result)


def test_dataset_listing_context_result_uses_bounded_identity_fields() -> None:
    _, stored_result = _record_result(
        tool_name="resource_registry_list_datasets",
        raw_result={
            "success": True,
            "data": {
                "datasets": [
                    {
                        "id": f"dataset-{index}",
                        "name": f"Dataset {index}",
                        "storage_uri": "s3://private-bucket/data.parquet",
                    }
                    for index in range(RAW_ITEM_COUNT)
                ],
            },
        },
    )

    datasets = stored_result["data"]["datasets"]
    assert datasets == [
        {"id": f"dataset-{index}", "name": f"Dataset {index}"} for index in range(PROMPT_ITEM_LIMIT)
    ]
    assert "storage_uri" not in json.dumps(stored_result)


def test_non_resource_registry_tool_result_is_recorded_unchanged() -> None:
    raw_result = '{"success": true, "items": [1, 2, 3, 4, 5, 6]}'
    _, stored_result = _record_result(tool_name="unrelated_tool", raw_result=raw_result)

    assert stored_result == json.loads(raw_result)


def test_failed_resource_registry_result_preserves_error_envelope() -> None:
    _, stored_result = _record_result(
        tool_name="resource_registry_explore_dataset",
        raw_result={
            "success": False,
            "status": 400,
            "code": "empty_search_terms",
            "error": "search_values requires at least one search term.",
        },
    )

    assert stored_result == {
        "success": False,
        "status": 400,
        "code": "empty_search_terms",
        "error": "search_values requires at least one search term.",
    }


def test_adapter_uses_injected_prompt_result_compactor() -> None:
    class _StaticCompactor:
        def compact(self, *, tool_name: str, raw_result: Any) -> dict[str, object]:
            _ = tool_name, raw_result
            return {"success": True, "compacted": True}

    context_manager = _RecordingContextManager()
    adapter = DataRegistryExplorationRoutingAdapter(prompt_result_compactor=_StaticCompactor())

    adapter._record_tool_result(
        client=SimpleNamespace(context_mgr=context_manager),
        request=SimpleNamespace(),
        tool_call=AssistantToolCall(
            name="resource_registry_explore_dataset",
            arguments={"tenant_id": "tenant-1"},
        ),
        raw_result={"success": True, "values": [{"value": "uncompacted"}]},
    )

    assert _recorded_result(context_manager) == {"success": True, "compacted": True}
