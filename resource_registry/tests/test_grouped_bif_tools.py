"""Verify the four-tool transport without replacing operation authorization."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from resource_registry.assistant.campaign_rendering_intent import CampaignRenderingIntentPolicy
from resource_registry.bifs.grouped_publication import format_registry_tool, prepare_registry_tools
from resource_registry.bifs.grouped_tools import (
    GROUPED_REGISTRY_ACTIONS,
    GROUPED_REGISTRY_OPERATION_NAMES,
    FunctionToolDefinition,
    InvalidGroupedRegistryCallError,
    build_grouped_registry_tools,
    is_valid_grouped_registry_arguments,
    project_registry_tool_result,
    resolve_grouped_registry_call,
)
from resource_registry.bifs.manifest import RESOURCE_REGISTRY_BIF_MANIFEST


def test_groups_cover_every_operation_once_and_keep_analysis_in_inspect() -> None:
    """Cover all existing operations without assigning analysis to the output group."""
    names = [
        f"resource_registry_{action}"
        for actions in GROUPED_REGISTRY_ACTIONS.values()
        for action in actions
    ]
    assert len(names) == len(set(names)) == 31
    assert set(names) == set(RESOURCE_REGISTRY_BIF_MANIFEST)
    assert set(names) == GROUPED_REGISTRY_OPERATION_NAMES
    assert [len(actions) for actions in GROUPED_REGISTRY_ACTIONS.values()] == [9, 7, 9, 6]
    assert "analyze_dataset" in GROUPED_REGISTRY_ACTIONS["resource_registry_inspect"]


@pytest.mark.parametrize("function_name", sorted(GROUPED_REGISTRY_OPERATION_NAMES))
def test_each_grouped_action_resolves_to_one_unchanged_call(function_name: str) -> None:
    """Preserve nested operation payloads while resolving one registered action.

    Args:
        function_name: Original operation to resolve.
    """
    action = function_name.removeprefix("resource_registry_")
    group = next(name for name, actions in GROUPED_REGISTRY_ACTIONS.items() if action in actions)
    arguments = {"operation": "transformation", "payload": {"parameters": {"limit": 3}}}
    resolved = resolve_grouped_registry_call(group, {"action": action, "arguments": arguments})
    assert resolved.function_name == function_name
    assert resolved.arguments == arguments
    assert resolved.arguments is not arguments


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        "bad json",
        {"action": "get_dataset"},
        {"action": "start_transformation_run", "arguments": {}},
        {"action": "get_dataset", "arguments": []},
        {"action": "get_dataset", "arguments": {}, "tenant_id": "forged"},
    ],
)
def test_invalid_envelopes_cannot_resolve_an_operation(payload: object) -> None:
    """Reject malformed or cross-group actions before invocation.

    Args:
        payload: Invalid tool arguments.
    """
    with pytest.raises(InvalidGroupedRegistryCallError):
        resolve_grouped_registry_call("resource_registry_inspect", payload)


def test_partial_assignment_advertises_only_its_action() -> None:
    """Avoid granting analysis when only schema reading is assigned."""
    tools = build_grouped_registry_tools(
        [
            FunctionToolDefinition(
                name="resource_registry_get_dataset_schema",
                description="Read schema.",
                parameters={
                    "type": "object",
                    "properties": {"dataset_id": {"type": "string"}},
                    "required": ["dataset_id"],
                },
            ),
        ]
    )
    assert len(tools) == 1
    assert tools[0].name == "resource_registry_inspect"
    assert tools[0].parameters["properties"]["action"]["enum"] == ["get_dataset_schema"]
    assert "analyze_dataset" not in tools[0].description


def test_transformation_schema_advertises_real_input_binding_arrays() -> None:
    """Use the application contract instead of the legacy object parameter metadata."""
    tools = build_grouped_registry_tools(
        [
            FunctionToolDefinition(
                name="resource_registry_start_transformation_run",
                description="Start a run.",
                parameters={"type": "object", "properties": {"input_bindings": {"type": "object"}}},
            ),
        ]
    )
    bindings = tools[0].parameters["properties"]["arguments"]["properties"]["input_bindings"]
    assert bindings["type"] == "array"
    assert bindings["items"]["required"] == ["input_name", "dataset_id", "dataset_version_id"]


def test_non_registry_tools_are_preserved_and_empty_groups_are_omitted() -> None:
    """Keep unrelated tool contracts intact."""
    original = FunctionToolDefinition(name="other_tool", description="Other", parameters={})
    assert build_grouped_registry_tools([original]) == [original]
    assert build_grouped_registry_tools([]) == []


def test_recommendations_point_to_the_new_action_without_changing_data() -> None:
    """Rewrite transport hints without rewriting dataset content or status codes."""
    payload = {
        "success": False,
        "status": 409,
        "recommended_bif": "resource_registry_start_query_job",
        "suggested_bif": "resource_registry_analyze_dataset",
        "rows": [{"text": "resource_registry_analyze_dataset"}],
    }
    projected = json.loads(project_registry_tool_result(json.dumps(payload)))
    assert projected["recommended_bif"] == "resource_registry_query"
    assert projected["recommended_action"] == "start_query_job"
    assert projected["suggested_bif"] == "resource_registry_inspect"
    assert projected["suggested_action"] == "analyze_dataset"
    assert projected["rows"] == payload["rows"]
    assert projected["status"] == 409


def test_hint_names_are_replaced_as_complete_operation_names() -> None:
    """Avoid matching get_dataset inside get_dataset_schema."""
    projected = json.loads(
        project_registry_tool_result(
            json.dumps(
                {
                    "hint": (
                        "Use resource_registry_get_dataset_schema or resource_registry_analyze_dataset."
                    )
                }
            )
        )
    )
    assert projected["hint"] == (
        "Use resource_registry_inspect with action='get_dataset_schema' "
        "or resource_registry_inspect with action='analyze_dataset'."
    )


def test_provider_adapters_preserve_optional_and_nested_argument_contracts() -> None:
    """Use JSON Schema transport for Gemini and explicitly optional Responses arguments."""
    definition = FunctionToolDefinition(
        name="resource_registry_inspect",
        description="Inspect",
        parameters={"type": "object", "properties": {"arguments": {"type": "object"}}},
    )
    gemini = format_registry_tool(definition, "gemini")
    assert gemini["parametersJsonSchema"] == definition.parameters
    assert "parameters" not in gemini
    assert format_registry_tool(definition, "openai_responses")["strict"] is False


def test_campaign_filter_keeps_analysis_and_other_inspection_actions() -> None:
    """Omit exploration without hiding analysis or the whole inspect group."""
    tools = build_grouped_registry_tools(
        [
            FunctionToolDefinition(
                name=f"resource_registry_{action}",
                description=action,
                parameters={"type": "object", "properties": {}},
            )
            for action in ("explore_dataset", "analyze_dataset", "get_dataset_schema")
        ],
        excluded_actions=frozenset({"explore_dataset"}),
    )
    assert len(tools) == 1
    assert tools[0].parameters["properties"]["action"]["enum"] == [
        "get_dataset_schema",
        "analyze_dataset",
    ]


def test_action_argument_types_are_validated_without_filling_defaults() -> None:
    """Reject incompatible containers while preserving omitted and nullable optional fields."""
    definition = FunctionToolDefinition(
        name="resource_registry_list_datasets",
        description="List datasets.",
        parameters={
            "type": "object",
            "properties": {
                "tenant_id": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["tenant_id"],
        },
    )
    assert is_valid_grouped_registry_arguments(definition, {"tenant_id": "namespace"})
    assert is_valid_grouped_registry_arguments(
        definition, {"tenant_id": "namespace", "limit": None}
    )
    assert not is_valid_grouped_registry_arguments(definition, {"limit": {"invalid": True}})
    assert not is_valid_grouped_registry_arguments(definition, {"limit": True})
    assert not is_valid_grouped_registry_arguments(definition, {"sql": "SELECT 1"})


@pytest.mark.parametrize("value", ["country", ["country"], {"columns": ["country"]}])
def test_exploration_keeps_all_supported_column_and_term_forms(value: object) -> None:
    """Retain legacy string, array and wrapped-array exploration arguments.

    Args:
        value: One of the JSON representations accepted by the original handler.
    """
    definition = FunctionToolDefinition(
        name="resource_registry_explore_dataset",
        description="Explore",
        parameters={
            "type": "object",
            "required": ["columns"],
            "properties": {"columns": {"type": "object"}, "terms": {"type": "object"}},
        },
    )
    terms = {"terms": value["columns"]} if isinstance(value, dict) else value
    assert is_valid_grouped_registry_arguments(definition, {"columns": value, "terms": terms})


@pytest.mark.parametrize("provider", ["openai", "openai_responses", "gemini", "anthropic"])
def test_campaign_policy_filters_only_exploration_in_every_grouped_client(
    provider: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Apply campaign presentation policy consistently across provider adapters.

    Args:
        provider: Provider envelope under test.
        monkeypatch: Fixture isolating campaign intent detection.
    """
    monkeypatch.setattr(
        CampaignRenderingIntentPolicy,
        "should_exclude_exploration_tool",
        lambda self, **kwargs: True,
    )
    relationships = [
        SimpleNamespace(
            builtin_function=SimpleNamespace(
                name=f"resource_registry_{action}",
                description=action,
                _prefetched_objects_cache={"functionparameter_set": []},
            )
        )
        for action in ("explore_dataset", "analyze_dataset", "get_dataset_schema")
    ]
    remaining, tools = prepare_registry_tools(
        relationships, persona=None, provider=provider, statement="Create a campaign"
    )
    assert list(remaining) == []
    tool = tools[0].get("function", tools[0])
    schema = tool.get("parameters", tool.get("parametersJsonSchema", tool.get("input_schema")))
    assert schema["properties"]["action"]["enum"] == ["get_dataset_schema", "analyze_dataset"]


@pytest.mark.parametrize(
    "operation", ["list_dataset_versions", "get_dataset_schema", "plan_export"]
)
def test_partial_assignment_does_not_advertise_unassigned_charts(operation: str) -> None:
    """Keep chart promises out of tool descriptions when chart creation is unavailable.

    Args:
        operation: The only Registry operation assigned to the Persona.
    """
    tools = build_grouped_registry_tools(
        [
            FunctionToolDefinition(
                name=f"resource_registry_{operation}",
                description="Perform the assigned Registry operation.",
                parameters={"type": "object", "properties": {}},
            ),
        ],
    )
    assert len(tools) == 1
    assert tools[0].parameters["properties"]["action"]["enum"] == [operation]
    assert "chart" not in tools[0].description.lower()
