"""Check Registry-owned stage instrumentation at real function boundaries."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from BotServer.bif_execution_admission import BuiltInFunctionExecutionChannel
from resource_registry.assistant import runtime_adapter
from resource_registry.assistant.runtime_adapter import (
    DataRegistryExplorationRoutingAdapter,
    DataRegistryExplorationRoutingResult,
)
from resource_registry.authorization.exceptions import (
    RegistryAuthorizationAuthenticationRequiredError,
)
from resource_registry.authorization.registry_authorization_context_service import (
    RegistryAuthorizationContextService,
)
from resource_registry.bifs.grouped_runtime import execute_grouped_registry_call
from resource_registry.bifs.grouped_tools import (
    FunctionToolDefinition,
    is_valid_grouped_registry_arguments,
)
from resource_registry.performance import instrumentation
from resource_registry.performance.instrumentation import (
    RegistryPerformanceOutcome,
    RegistryPerformanceStage,
    reset_registry_performance_configuration_for_tests,
)


@pytest.fixture(autouse=True)
def enable_registry_performance(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Enable deterministic local metric capture for each integration case."""
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_ENABLED", "true")
    reset_registry_performance_configuration_for_tests()
    yield
    reset_registry_performance_configuration_for_tests()


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (None, RegistryPerformanceOutcome.FALLBACK),
        (
            DataRegistryExplorationRoutingResult(
                response_text="answered",
                tool_calls=(),
                request_payload={"status": "workspace_selected"},
            ),
            RegistryPerformanceOutcome.ROUTED,
        ),
        (
            DataRegistryExplorationRoutingResult(
                response_text="access denied",
                tool_calls=(),
                request_payload={"error_code": "resource_registry_authentication_required"},
            ),
            RegistryPerformanceOutcome.REJECTED,
        ),
        (
            DataRegistryExplorationRoutingResult(
                response_text=runtime_adapter._FAILED_EXPLORATION_RESPONSE,
                tool_calls=(),
                request_payload={},
            ),
            RegistryPerformanceOutcome.FAILED,
        ),
    ],
)
def test_routing_outcome_matches_execution_semantics(
    result: DataRegistryExplorationRoutingResult | None,
    expected: RegistryPerformanceOutcome,
) -> None:
    """Classify fallback, handled, controlled rejection, and failed evidence separately."""
    assert runtime_adapter._classify_routing_performance_outcome(result) is expected


def test_real_router_fallback_emits_registry_stage_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Measure a router attempt without vendor or database activity."""
    recorded: list[instrumentation.RegistryPerformanceMetric] = []
    monkeypatch.setattr(instrumentation, "emit_registry_performance_metric", recorded.append)
    adapter = DataRegistryExplorationRoutingAdapter()
    monkeypatch.setattr(adapter, "_has_resource_registry_capability", lambda client: False)

    assert (
        adapter.try_route(client=SimpleNamespace(), request=SimpleNamespace(), statement="secret")
        is None
    )
    assert [(metric.stage, metric.outcome) for metric in recorded] == [
        (RegistryPerformanceStage.DETERMINISTIC_ROUTING, RegistryPerformanceOutcome.FALLBACK)
    ]


def test_real_router_access_rejection_retains_response_and_metric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Report an authorization refusal without changing its deterministic response."""
    recorded: list[instrumentation.RegistryPerformanceMetric] = []
    monkeypatch.setattr(instrumentation, "emit_registry_performance_metric", recorded.append)
    monkeypatch.setattr(
        DataRegistryExplorationRoutingAdapter,
        "_has_resource_registry_capability",
        lambda self, client: True,
    )
    monkeypatch.setattr(
        RegistryAuthorizationContextService,
        "build_context",
        Mock(side_effect=RegistryAuthorizationAuthenticationRequiredError("private")),
    )

    result = DataRegistryExplorationRoutingAdapter().try_route(
        client=SimpleNamespace(persona=None),
        request=SimpleNamespace(user=None, organization=None),
        statement="list datasets",
    )

    assert result is not None
    assert result.request_payload["error_code"] == "resource_registry_authentication_required"
    assert recorded[-1].stage is RegistryPerformanceStage.DETERMINISTIC_ROUTING
    assert recorded[-1].outcome is RegistryPerformanceOutcome.REJECTED


def test_grouped_validation_failure_keeps_result_and_emits_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Observe a rejected transport envelope without invoking canonical admission."""
    recorded: list[instrumentation.RegistryPerformanceMetric] = []
    monkeypatch.setattr(instrumentation, "emit_registry_performance_metric", recorded.append)
    client = SimpleNamespace(appLogger=logging.getLogger("resource_registry.test"))
    result = execute_grouped_registry_call(
        client,
        SimpleNamespace(),
        "resource_registry_inspect",
        {"action": "not_assigned", "arguments": {}},
        execution_channel=BuiltInFunctionExecutionChannel.PLATFORM_API,
    )

    assert isinstance(result, str)
    assert json.loads(result)["code"] == "invalid_registry_tool_call"
    assert [(metric.stage, metric.outcome) for metric in recorded] == [
        (RegistryPerformanceStage.GROUPED_RESULT_PROJECTION, RegistryPerformanceOutcome.SUCCESS),
        (RegistryPerformanceStage.GROUPED_CALL_TOTAL, RegistryPerformanceOutcome.REJECTED),
    ]


def test_grouped_dispatch_exception_retains_identity_and_failed_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Leave unexpected canonical dispatch errors untouched."""
    recorded: list[instrumentation.RegistryPerformanceMetric] = []
    monkeypatch.setattr(instrumentation, "emit_registry_performance_metric", recorded.append)
    failure = RuntimeError("private operation detail")
    client = SimpleNamespace(
        appLogger=logging.getLogger("resource_registry.test"),
        bif_execution_admission_service=SimpleNamespace(admit=Mock(side_effect=failure)),
        persona=None,
    )

    with pytest.raises(RuntimeError) as caught:
        execute_grouped_registry_call(
            client,
            SimpleNamespace(),
            "resource_registry_inspect",
            {"action": "list_datasets", "arguments": {}},
            execution_channel=BuiltInFunctionExecutionChannel.PLATFORM_API,
        )

    assert caught.value is failure
    assert [(metric.stage, metric.outcome) for metric in recorded] == [
        (RegistryPerformanceStage.GROUPED_DISPATCH_BRIDGE, RegistryPerformanceOutcome.FAILED),
        (RegistryPerformanceStage.GROUPED_CALL_TOTAL, RegistryPerformanceOutcome.FAILED),
    ]


def test_schema_validation_failure_emits_rejection_without_changing_boolean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the original grouped argument validator result."""
    recorded: list[instrumentation.RegistryPerformanceMetric] = []
    monkeypatch.setattr(instrumentation, "emit_registry_performance_metric", recorded.append)
    definition = FunctionToolDefinition(
        name="resource_registry_get_dataset",
        description="Read a dataset.",
        parameters={
            "type": "object",
            "properties": {"dataset_id": {"type": "string"}},
            "required": ["dataset_id"],
        },
    )

    assert is_valid_grouped_registry_arguments(definition, {"dataset_id": 7}) is False
    assert recorded[-1].stage is RegistryPerformanceStage.GROUPED_SCHEMA_VALIDATION
    assert recorded[-1].outcome is RegistryPerformanceOutcome.REJECTED


def test_authorization_failure_is_preserved_and_never_marked_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Leave Registry authentication failure behavior with the authorization service."""
    recorded: list[instrumentation.RegistryPerformanceMetric] = []
    monkeypatch.setattr(instrumentation, "emit_registry_performance_metric", recorded.append)

    with pytest.raises(RegistryAuthorizationAuthenticationRequiredError):
        RegistryAuthorizationContextService().build_context(user=None, organization=None)

    assert [(metric.stage, metric.outcome) for metric in recorded] == [
        (RegistryPerformanceStage.AUTHORIZATION_CONTEXT_BUILD, RegistryPerformanceOutcome.FAILED)
    ]
