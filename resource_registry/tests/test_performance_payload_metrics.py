"""Verify opt-in, sanitized metrics for published Registry grouped tools."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import FrozenInstanceError
from threading import Barrier
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

import pytest

from BotServer.models import BuiltInFunctionPersonaRelationship
from resource_registry.assistant.campaign_rendering_intent import CampaignRenderingIntentPolicy
from resource_registry.bifs import grouped_publication
from resource_registry.bifs.grouped_publication import (
    RegistryToolProvider,
    prepare_registry_tools,
)
from resource_registry.bifs.grouped_tools import (
    GROUPED_REGISTRY_ACTIONS,
    FunctionToolDefinition,
    is_valid_grouped_registry_arguments,
)
from resource_registry.performance import instrumentation, payload_metrics
from resource_registry.performance.instrumentation import (
    RegistryPerformanceMetric,
    RegistryPerformanceOutcome,
    RegistryPerformanceStage,
    get_registry_performance_configuration,
    reset_registry_performance_configuration_for_tests,
)


def build_registry_relationship(
    action: str, description: str = "Private description é."
) -> BuiltInFunctionPersonaRelationship:
    """Build a loaded Persona assignment without database access.

    Args:
        action: Canonical Registry action suffix.
        description: Sensitive text that must not enter emitted metrics.

    Returns:
        A relationship-shaped test fixture with prefetched parameters.
    """
    return cast(
        BuiltInFunctionPersonaRelationship,
        SimpleNamespace(
            builtin_function=SimpleNamespace(
                name=f"resource_registry_{action}",
                description=description,
                _prefetched_objects_cache={"functionparameter_set": []},
            )
        ),
    )


@pytest.fixture(autouse=True)
def reset_performance_configuration() -> Iterator[None]:
    """Isolate process-cached performance flags between tests."""
    reset_registry_performance_configuration_for_tests()
    yield
    reset_registry_performance_configuration_for_tests()


def test_payload_metrics_are_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Require explicit payload opt-in in addition to E1.1 timing."""
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_ENABLED", "true")
    monkeypatch.delenv("RESOURCE_REGISTRY_PERFORMANCE_PAYLOAD_ENABLED", raising=False)
    assert get_registry_performance_configuration().payload_enabled is False


def test_payload_configuration_is_immutable_and_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the E1.3 flag process-stable in the existing frozen configuration."""
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_PAYLOAD_ENABLED", "true")
    original = get_registry_performance_configuration()
    assert original.payload_enabled is True
    with pytest.raises(FrozenInstanceError):
        original.payload_enabled = False
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_PAYLOAD_ENABLED", "false")
    assert get_registry_performance_configuration() is original
    reset_registry_performance_configuration_for_tests()
    assert get_registry_performance_configuration().payload_enabled is False


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [("true", True), ("yes", True), ("1", True), ("on", True), ("garbage", False)],
)
def test_payload_configuration_parses_only_known_true_values(
    monkeypatch: pytest.MonkeyPatch, raw_value: str, expected: bool
) -> None:
    """Avoid activating payload traversal on an invalid opt-in value."""
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_PAYLOAD_ENABLED", raw_value)
    assert get_registry_performance_configuration().payload_enabled is expected


@pytest.mark.parametrize(
    ("performance_enabled", "sample_rate", "sink"),
    [("false", "1", "log"), ("true", "0.0", "log"), ("true", "1.0", "noop")],
)
def test_inactive_paths_skip_payload_serialization_and_traversal(
    monkeypatch: pytest.MonkeyPatch,
    performance_enabled: str,
    sample_rate: str,
    sink: str,
) -> None:
    """Leave payload collection untouched when timing cannot emit an event."""
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_ENABLED", performance_enabled)
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_PAYLOAD_ENABLED", "true")
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_SAMPLE_RATE", sample_rate)
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_SINK", sink)
    collector = Mock(side_effect=AssertionError("Payload collection must not run"))
    monkeypatch.setattr(payload_metrics, "measure_published_registry_tools", collector)

    remaining, tools = prepare_registry_tools(
        [build_registry_relationship("get_dataset_schema")],
        persona=None,
        provider="openai",
        statement="Inspect datasets",
    )

    assert list(remaining) == []
    assert len(tools) == 1
    collector.assert_not_called()


def test_payload_flag_disabled_keeps_e11_event_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """Leave existing stage fields unchanged when E1.3 is not enabled."""
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_ENABLED", "true")
    monkeypatch.delenv("RESOURCE_REGISTRY_PERFORMANCE_PAYLOAD_ENABLED", raising=False)
    collector = Mock(side_effect=AssertionError("Payload collection must not run"))
    monkeypatch.setattr(payload_metrics, "measure_published_registry_tools", collector)
    recorded: list[RegistryPerformanceMetric] = []
    monkeypatch.setattr(instrumentation, "emit_registry_performance_metric", recorded.append)

    prepare_registry_tools(
        [build_registry_relationship("get_dataset_schema")],
        persona=None,
        provider="openai",
        statement="Inspect datasets",
    )

    collector.assert_not_called()
    assert "tool_count" not in recorded[-1].to_safe_payload()
    assert "payload_bytes" not in recorded[-1].to_safe_payload()


def test_publication_metric_counts_effective_single_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Measure only the grouped tool and action returned for one assignment."""
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_ENABLED", "true")
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_PAYLOAD_ENABLED", "true")
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_SAMPLE_RATE", "1.0")
    recorded: list[RegistryPerformanceMetric] = []
    monkeypatch.setattr(instrumentation, "emit_registry_performance_metric", recorded.append)
    relationship = build_registry_relationship("get_dataset_schema")

    remaining, tools = prepare_registry_tools(
        [relationship], persona=None, provider="openai", statement="Inspect datasets"
    )

    assert list(remaining) == []
    assert len(tools) == 1
    metric = next(
        metric
        for metric in recorded
        if metric.stage is RegistryPerformanceStage.REGISTRY_TOOL_PUBLICATION
    )
    payload = metric.to_safe_payload()
    assert payload["tool_count"] == 1
    assert payload["action_count"] == 1
    assert isinstance(payload["payload_bytes"], int)
    assert payload["payload_bytes"] > 0
    assert "Private description" not in str(payload)


def test_empty_effective_publication_reports_zero_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Report the serialized empty Registry tool list without loading parameters."""
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_ENABLED", "true")
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_PAYLOAD_ENABLED", "true")
    recorded: list[RegistryPerformanceMetric] = []
    monkeypatch.setattr(instrumentation, "emit_registry_performance_metric", recorded.append)

    remaining, tools = prepare_registry_tools(
        [], persona=None, provider="openai", statement="Inspect datasets"
    )

    assert list(remaining) == [] and tools == []
    payload = recorded[-1].to_safe_payload()
    assert payload["tool_count"] == 0
    assert payload["action_count"] == 0
    assert payload["schema_property_count"] == 0
    assert payload["description_char_count"] == 0
    assert payload["payload_bytes"] == 2


def test_partial_persona_publication_counts_only_returned_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exclude policy-filtered actions from the measured effective tool set."""
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_ENABLED", "true")
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_PAYLOAD_ENABLED", "true")
    monkeypatch.setattr(
        CampaignRenderingIntentPolicy,
        "should_exclude_exploration_tool",
        lambda self, **kwargs: True,
    )
    recorded: list[RegistryPerformanceMetric] = []
    monkeypatch.setattr(instrumentation, "emit_registry_performance_metric", recorded.append)
    relationships = [
        build_registry_relationship("get_dataset_schema"),
        build_registry_relationship("explore_dataset"),
        build_registry_relationship("query_dataset"),
    ]

    remaining, tools = prepare_registry_tools(
        relationships,
        persona=SimpleNamespace(name="Private Persona ID"),
        provider="anthropic",
        statement="Private tenant workspace dataset prompt",
    )

    assert list(remaining) == []
    assert len(tools) == 2
    assert recorded[-1].tool_count == 2
    assert recorded[-1].action_count == 2
    assert "explore_dataset" not in str(tools)
    assert "Private tenant" not in str(recorded[-1].to_safe_payload())
    assert "Private Persona" not in str(recorded[-1].to_safe_payload())


def test_policy_can_leave_zero_effective_grouped_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Count zero actions after the existing presentation filter excludes all."""
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_ENABLED", "true")
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_PAYLOAD_ENABLED", "true")
    monkeypatch.setattr(
        CampaignRenderingIntentPolicy,
        "should_exclude_exploration_tool",
        lambda self, **kwargs: True,
    )
    recorded: list[RegistryPerformanceMetric] = []
    monkeypatch.setattr(instrumentation, "emit_registry_performance_metric", recorded.append)

    _, tools = prepare_registry_tools(
        [build_registry_relationship("explore_dataset")],
        persona=None,
        provider="openai",
        statement="Create a campaign",
    )

    assert tools == []
    assert recorded[-1].tool_count == 0
    assert recorded[-1].action_count == 0
    assert recorded[-1].payload_bytes == 2


def test_full_publication_counts_four_groups_and_thirty_one_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Count all assigned actions without assuming full publication by default."""
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_ENABLED", "true")
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_PAYLOAD_ENABLED", "true")
    monkeypatch.setattr(
        CampaignRenderingIntentPolicy,
        "should_exclude_exploration_tool",
        lambda self, **kwargs: False,
    )
    recorded: list[RegistryPerformanceMetric] = []
    monkeypatch.setattr(instrumentation, "emit_registry_performance_metric", recorded.append)
    relationships = [
        build_registry_relationship(action)
        for actions in GROUPED_REGISTRY_ACTIONS.values()
        for action in actions
    ]

    remaining, tools = prepare_registry_tools(
        relationships, persona=None, provider="openai_responses", statement="Inspect datasets"
    )

    assert len(relationships) == 31
    assert list(remaining) == []
    assert len(tools) == 4
    assert recorded[-1].tool_count == 4
    assert recorded[-1].action_count == 31


def test_schema_complexity_and_utf8_bytes_follow_documented_rules() -> None:
    """Count nested branches and physical definitions, but do not resolve refs."""
    tool: dict[str, object] = {
        "type": "function",
        "function": {
            "name": "resource_registry_inspect",
            "description": "é",
            "parameters": {
                "type": "object",
                "description": "root",
                "properties": {
                    "action": {"enum": ["inspect"]},
                    "first": {
                        "type": "object",
                        "description": "one",
                        "properties": {"inner": {"description": "a"}},
                    },
                    "choice": {
                        "oneOf": [
                            {"properties": {"left": {}}},
                            {
                                "anyOf": [
                                    {"properties": {"right": {}}},
                                    {"$ref": "#/$defs/x", "description": "ref"},
                                ]
                            },
                        ]
                    },
                },
                "$defs": {"x": {"properties": {"resolved": {}}}},
            },
        },
    }
    original = deepcopy(tool)

    measurements = payload_metrics.measure_published_registry_tools([tool])

    serialized = json.dumps(
        [tool], ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    assert measurements.tool_count == 1
    assert measurements.action_count == 1
    assert measurements.schema_property_count == 7
    assert measurements.description_char_count == 12
    assert measurements.payload_bytes == len(serialized.encode("utf-8"))
    assert measurements.payload_bytes > len(serialized)
    assert tool == original


def test_repeated_schema_fragments_are_counted_per_occurrence() -> None:
    """Measure actual payload structure without dereferencing or deduplicating."""
    fragment = {"type": "string", "description": "abc"}
    tool: dict[str, object] = {
        "name": "resource_registry_inspect",
        "description": "group",
        "input_schema": {
            "properties": {
                "action": {"enum": ["read"]},
                "one": fragment,
                "two": deepcopy(fragment),
            },
            "allOf": [{"properties": {"three": deepcopy(fragment)}}],
        },
    }

    measurements = payload_metrics.measure_published_registry_tools([tool])

    assert measurements.schema_property_count == 4
    assert measurements.description_char_count == len("group") + 3 * len("abc")


def test_metric_collection_does_not_change_published_definitions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return exactly the same grouped contract with payload metrics off or on."""
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_ENABLED", "true")
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_PAYLOAD_ENABLED", "false")
    relationships = [build_registry_relationship("get_dataset_schema")]
    _, baseline_tools = prepare_registry_tools(
        relationships, persona=None, provider="gemini", statement="Inspect datasets"
    )
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_PAYLOAD_ENABLED", "true")
    reset_registry_performance_configuration_for_tests()

    _, measured_tools = prepare_registry_tools(
        relationships, persona=None, provider="gemini", statement="Inspect datasets"
    )

    assert measured_tools == baseline_tools
    assert relationships[0].builtin_function.description == "Private description é."


def test_payload_metric_bounds_do_not_add_untrusted_labels() -> None:
    """Clamp numeric values and retain only the approved fixed event keys."""
    metric = RegistryPerformanceMetric(
        stage=RegistryPerformanceStage.REGISTRY_TOOL_PUBLICATION,
        operation="tenant-secret",
        outcome=RegistryPerformanceOutcome.SUCCESS,
        duration_ms=1.0,
        tool_count=-1,
        action_count=999_999_999_999,
        schema_property_count=2,
        description_char_count=3,
        payload_bytes=4,
    )

    payload = metric.to_safe_payload()

    assert payload["operation"] == "unknown"
    assert payload["tool_count"] == 0
    assert payload["action_count"] == payload_metrics.MAX_PAYLOAD_METRIC_VALUE
    assert "tenant-secret" not in str(payload)
    assert set(payload) == {
        "schema_version",
        "event",
        "stage",
        "operation",
        "outcome",
        "duration_ms",
        "inclusive",
        "tool_count",
        "action_count",
        "schema_property_count",
        "description_char_count",
        "payload_bytes",
    }


@pytest.mark.parametrize("provider", ["openai", "openai_responses", "gemini", "anthropic"])
def test_payload_bytes_match_actual_registry_provider_envelope(
    monkeypatch: pytest.MonkeyPatch, provider: RegistryToolProvider
) -> None:
    """Measure the returned provider-formatted Registry list, not a neutral schema."""
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_ENABLED", "true")
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_PAYLOAD_ENABLED", "true")
    recorded: list[RegistryPerformanceMetric] = []
    monkeypatch.setattr(instrumentation, "emit_registry_performance_metric", recorded.append)

    _, tools = prepare_registry_tools(
        [build_registry_relationship("get_dataset_schema")],
        persona=None,
        provider=provider,
        statement="Inspect datasets",
    )

    expected = len(
        json.dumps(
            tools, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    )
    assert recorded[-1].payload_bytes == expected
    assert recorded[-1].action_count == 1


def test_logged_event_contains_only_sanitized_counts(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Never emit description, Persona, workspace, dataset, or prompt content."""
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_ENABLED", "true")
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_PAYLOAD_ENABLED", "true")

    with caplog.at_level(logging.INFO, logger="resource_registry.performance"):
        prepare_registry_tools(
            [build_registry_relationship("get_dataset_schema", "secret description")],
            persona=SimpleNamespace(name="secret Persona tenant ID"),
            provider="openai",
            statement="secret workspace dataset prompt credential",
        )

    publication_events = [
        json.loads(record.message)
        for record in caplog.records
        if '"stage":"registry_tool_publication"' in record.message
    ]
    assert len(publication_events) == 1
    assert publication_events[0]["tool_count"] == 1
    assert publication_events[0]["action_count"] == 1
    for secret in ("secret description", "secret Persona", "secret workspace", "credential"):
        assert secret not in caplog.text


def test_payload_collection_failure_preserves_result_and_base_metric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep successful publication unchanged when measurement serialization fails."""
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_ENABLED", "true")
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_PAYLOAD_ENABLED", "true")
    monkeypatch.setattr(
        payload_metrics,
        "measure_published_registry_tools",
        Mock(side_effect=ValueError("secret measurement failure")),
    )
    recorded: list[RegistryPerformanceMetric] = []
    monkeypatch.setattr(instrumentation, "emit_registry_performance_metric", recorded.append)

    remaining, tools = prepare_registry_tools(
        [build_registry_relationship("get_dataset_schema")],
        persona=None,
        provider="openai",
        statement="Inspect datasets",
    )

    assert list(remaining) == []
    assert len(tools) == 1
    assert recorded[-1].stage is RegistryPerformanceStage.REGISTRY_TOOL_PUBLICATION
    assert recorded[-1].outcome is RegistryPerformanceOutcome.SUCCESS
    assert "tool_count" not in recorded[-1].to_safe_payload()
    assert "secret measurement failure" not in str(recorded[-1].to_safe_payload())


def test_publication_exception_preserves_identity_without_payload_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not turn a Registry construction failure into a successful measurement."""
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_ENABLED", "true")
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_PAYLOAD_ENABLED", "true")
    original = RuntimeError("private construction error")
    monkeypatch.setattr(
        grouped_publication, "build_grouped_registry_tools", Mock(side_effect=original)
    )
    recorded: list[RegistryPerformanceMetric] = []
    monkeypatch.setattr(instrumentation, "emit_registry_performance_metric", recorded.append)

    with pytest.raises(RuntimeError) as caught:
        prepare_registry_tools(
            [build_registry_relationship("get_dataset_schema")],
            persona=None,
            provider="openai",
            statement="Inspect datasets",
        )

    assert caught.value is original
    assert caught.value.__traceback__ is not None
    assert recorded[-1].outcome is RegistryPerformanceOutcome.FAILED
    assert "tool_count" not in recorded[-1].to_safe_payload()


def test_schema_build_and_validation_keep_existing_stage_timers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Derive build and validation cost from E1.1 stages without duplicate fields."""
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_ENABLED", "true")
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_PAYLOAD_ENABLED", "true")
    recorded: list[RegistryPerformanceMetric] = []
    monkeypatch.setattr(instrumentation, "emit_registry_performance_metric", recorded.append)

    prepare_registry_tools(
        [build_registry_relationship("get_dataset_schema")],
        persona=None,
        provider="openai",
        statement="Inspect datasets",
    )
    definition = FunctionToolDefinition(
        name="resource_registry_get_dataset",
        description="Read dataset",
        parameters={
            "type": "object",
            "properties": {"dataset_id": {"type": "string"}},
            "required": ["dataset_id"],
        },
    )
    assert is_valid_grouped_registry_arguments(definition, {"dataset_id": 7}) is False

    stages = {metric.stage: metric for metric in recorded}
    assert RegistryPerformanceStage.GROUPED_SCHEMA_BUILD in stages
    assert RegistryPerformanceStage.GROUPED_SCHEMA_VALIDATION in stages
    assert RegistryPerformanceStage.REGISTRY_TOOL_PUBLICATION in stages
    assert stages[RegistryPerformanceStage.GROUPED_SCHEMA_VALIDATION].outcome is (
        RegistryPerformanceOutcome.REJECTED
    )
    assert all(metric.duration_ms >= 0 for metric in stages.values())
    assert all(
        "schema_build_duration_ms" not in metric.to_safe_payload()
        and "schema_validation_duration_ms" not in metric.to_safe_payload()
        for metric in stages.values()
    )
    assert (
        "tool_count"
        not in stages[RegistryPerformanceStage.GROUPED_SCHEMA_VALIDATION].to_safe_payload()
    )


def test_sql_and_payload_fields_coexist_without_changing_e12(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep E1.2 zero-query measurements while adding publication counts."""
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_ENABLED", "true")
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_SQL_ENABLED", "true")
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_PAYLOAD_ENABLED", "true")
    recorded: list[RegistryPerformanceMetric] = []
    monkeypatch.setattr(instrumentation, "emit_registry_performance_metric", recorded.append)

    prepare_registry_tools(
        [build_registry_relationship("get_dataset_schema")],
        persona=None,
        provider="openai",
        statement="Inspect datasets",
    )

    publication = recorded[-1].to_safe_payload()
    assert publication["db_query_count"] == 0
    assert publication["db_alias_count"] == 0
    assert publication["tool_count"] == 1
    assert publication["action_count"] == 1


def test_concurrent_publications_keep_independent_action_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Avoid cross-request mutable measurement state under worker concurrency."""
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_ENABLED", "true")
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_PAYLOAD_ENABLED", "true")
    barrier = Barrier(4)

    def wait_for_concurrent_publications(
        self: CampaignRenderingIntentPolicy, *, persona: object, statement: str
    ) -> bool:
        """Hold four workers at the presentation policy boundary."""
        _ = (self, persona, statement)
        barrier.wait(timeout=5)
        return False

    monkeypatch.setattr(
        CampaignRenderingIntentPolicy,
        "should_exclude_exploration_tool",
        wait_for_concurrent_publications,
    )
    recorded: list[RegistryPerformanceMetric] = []
    monkeypatch.setattr(instrumentation, "emit_registry_performance_metric", recorded.append)

    def publish_action_subset(count: int) -> int:
        """Publish one request's count of assigned inspect actions."""
        relationships = [
            build_registry_relationship(action)
            for action in GROUPED_REGISTRY_ACTIONS["resource_registry_inspect"][:count]
        ]
        _, tools = prepare_registry_tools(
            relationships, persona=None, provider="openai", statement="Inspect datasets"
        )
        return len(tools)

    with ThreadPoolExecutor(max_workers=4) as executor:
        assert list(executor.map(publish_action_subset, range(1, 5))) == [1, 1, 1, 1]

    publication_metrics = [
        metric
        for metric in recorded
        if metric.stage is RegistryPerformanceStage.REGISTRY_TOOL_PUBLICATION
    ]
    assert sorted(metric.action_count for metric in publication_metrics if metric.action_count) == [
        1,
        2,
        3,
        4,
    ]
