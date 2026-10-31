"""Register safe synthetic Registry benchmark workloads and outcomes."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import partial
from types import SimpleNamespace

from BotServer.bif_execution_admission import BuiltInFunctionExecutionChannel
from resource_registry.assistant.exploration_routing import ExplorationRoutingPolicy
from resource_registry.benchmarks.fixtures import (
    build_all_action_names,
    build_cached_publication_relationships,
    build_operation_definitions,
    build_routing_cases,
)
from resource_registry.benchmarks.grouped_execution import (
    ExecutionBoundary,
    validate_execution_boundary,
)
from resource_registry.bifs.grouped_publication import prepare_registry_tools
from resource_registry.bifs.grouped_runtime import execute_grouped_registry_call
from resource_registry.bifs.grouped_tools import (
    build_grouped_registry_tools,
    is_valid_grouped_registry_arguments,
    project_registry_tool_result,
)

_ALLOWED_OUTCOMES = frozenset({"success", "routed", "fallback", "rejected", "failed"})


@dataclass(frozen=True)
class BenchmarkScenario:
    """Describe one repeatable workload without retaining its output in the report.

    Args:
        name: Fixed report identifier.
        category: Fixed result-section name.
        kind: Synthetic or isolated database-backed fixture type.
        execute: Workload called once per iteration.
        classify: Optional bounded outcome classifier for the actual return value.
        execution_boundary: Actual point at which the workload stops or invokes shared code.
        capture_database_sql: Whether the benchmark runner measures whole-operation SQL locally.
        metadata: Static non-sensitive workload metadata for the result record.
        observe_result: Optional bounded observation extracted from each successful return value.
    """

    name: str
    category: str
    kind: str
    execute: Callable[[], object]
    classify: Callable[[object], str] | None = None
    execution_boundary: ExecutionBoundary | None = None
    capture_database_sql: bool = False
    metadata: Mapping[str, object] = field(default_factory=dict)
    observe_result: Callable[[object], Mapping[str, object]] | None = None

    def __post_init__(self) -> None:
        """Reject a scenario that could otherwise obscure its execution boundary.

        Raises:
            ValueError: If no sanctioned boundary is declared.
        """
        object.__setattr__(
            self, "execution_boundary", validate_execution_boundary(self.execution_boundary)
        )

    def classify_result(self, result: object) -> str:
        """Classify the observed result using only fixed outcome names.

        Args:
            result: The original workload result.

        Returns:
            One bounded outcome or ``failed`` for an unknown classifier value.
        """
        outcome = self.classify(result) if self.classify is not None else "success"
        return outcome if outcome in _ALLOWED_OUTCOMES else "failed"

    def observe_result_metadata(self, result: object) -> dict[str, object] | None:
        """Extract bounded observed metadata without retaining the workload result.

        Args:
            result: Original workload result that has already been classified successfully.

        Returns:
            A copy of the configured observation, or ``None`` when no observation is required.

        Raises:
            ValueError: If a configured observation is not a mapping.
        """
        if self.observe_result is None:
            return None
        observed_metadata = self.observe_result(result)
        if not isinstance(observed_metadata, Mapping):
            raise ValueError("Benchmark result observation must be a mapping.")
        return dict(observed_metadata)


def _classify_routing_result(result: object) -> str:
    """Map the pure policy's decision to benchmark outcome semantics.

    Args:
        result: One ExplorationRoutingDecision.

    Returns:
        Routed, fallback, rejected, or failed.
    """
    status = getattr(result, "status", None)
    if status in {"ready", "dataset_listing", "needs_schema"}:
        return "routed"
    if status == "bypass_normal_flow":
        return "fallback"
    if status in {"unknown_column", "missing_terms", "unsupported"}:
        return "rejected"
    return "failed"


def _classify_validation_result(result: object) -> str:
    """Classify validation without exposing submitted argument values.

    Args:
        result: Boolean validation output.

    Returns:
        Success for valid, rejected for invalid, or failed otherwise.
    """
    if result is True:
        return "success"
    if result is False:
        return "rejected"
    return "failed"


def _classify_grouped_result(result: object) -> str:
    """Classify the safe grouped error envelope from the actual return.

    Args:
        result: Original grouped call output.

    Returns:
        Rejected for a controlled 400 response, or failed otherwise.
    """
    try:
        payload = json.loads(result) if isinstance(result, str) else result
    except (TypeError, ValueError):
        return "failed"
    return "rejected" if isinstance(payload, dict) and payload.get("status") == 400 else "failed"


def build_scenarios() -> tuple[BenchmarkScenario, ...]:
    """Build the fixed synthetic scenario registry with prepared inputs.

    Returns:
        Runnable routing, publication, schema, validation, and execution cases.
    """
    routing_policy = ExplorationRoutingPolicy()
    silent_logger = logging.Logger("resource_registry.benchmarks.null", level=logging.CRITICAL)
    routing_cases = build_routing_cases()
    scenarios: list[BenchmarkScenario] = [
        BenchmarkScenario(
            name=f"routing.{name}",
            category="routing",
            kind="synthetic",
            execute=partial(routing_policy.route, request),
            classify=_classify_routing_result,
            execution_boundary="registry_pre_dispatch_only",
        )
        for name, request in routing_cases.items()
    ]
    publication_actions = {
        "none": (),
        "one": ("list_datasets",),
        "partial": ("list_datasets", "get_dataset_schema", "query_dataset", "get_query_run"),
        "full": build_all_action_names(),
    }
    for name, action_names in publication_actions.items():
        relationships = build_cached_publication_relationships(action_names)
        scenarios.append(
            BenchmarkScenario(
                name=f"publication.{name}",
                category="publication",
                kind="synthetic",
                execute=partial(
                    prepare_registry_tools,
                    relationships,
                    persona=None,
                    provider="openai",
                    statement="List datasets",
                ),
                execution_boundary="registry_pre_dispatch_only",
            )
        )
    schema_definitions = build_operation_definitions(build_all_action_names())
    validation_definition = build_operation_definitions(("query_dataset",))[0]
    nested_definition = validation_definition.__class__(
        name=validation_definition.name,
        description=validation_definition.description,
        parameters={
            "type": "object",
            "properties": {
                "filters": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "column": {"type": "string"},
                            "value": {"oneOf": [{"type": "string"}, {"type": "integer"}]},
                        },
                        "required": ["column", "value"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["filters"],
        },
    )
    scenarios.extend(
        (
            BenchmarkScenario(
                "schema.build_full",
                "schema",
                "synthetic",
                lambda: build_grouped_registry_tools(schema_definitions),
                execution_boundary="registry_pre_dispatch_only",
            ),
            BenchmarkScenario(
                "validation.valid",
                "validation",
                "synthetic",
                lambda: is_valid_grouped_registry_arguments(validation_definition, {"limit": 5}),
                _classify_validation_result,
                "registry_pre_dispatch_only",
            ),
            BenchmarkScenario(
                "validation.invalid",
                "validation",
                "synthetic",
                lambda: is_valid_grouped_registry_arguments(
                    validation_definition, {"limit": "invalid"}
                ),
                _classify_validation_result,
                "registry_pre_dispatch_only",
            ),
            BenchmarkScenario(
                "validation.nested",
                "validation",
                "synthetic",
                lambda: is_valid_grouped_registry_arguments(
                    nested_definition, {"filters": [{"column": "category", "value": 1}]}
                ),
                _classify_validation_result,
                "registry_pre_dispatch_only",
            ),
            BenchmarkScenario(
                "validation.complex_invalid",
                "validation",
                "synthetic",
                lambda: is_valid_grouped_registry_arguments(
                    nested_definition, {"filters": [{"column": "category", "value": []}]}
                ),
                _classify_validation_result,
                "registry_pre_dispatch_only",
            ),
            BenchmarkScenario(
                "execution.invalid_envelope",
                "execution",
                "synthetic",
                lambda: execute_grouped_registry_call(
                    SimpleNamespace(appLogger=silent_logger),
                    SimpleNamespace(),
                    "resource_registry_inspect",
                    {"action": "not_registered", "arguments": {}},
                    execution_channel=BuiltInFunctionExecutionChannel.LLM_TOOL,
                ),
                _classify_grouped_result,
                "registry_pre_dispatch_only",
            ),
            BenchmarkScenario(
                "execution.result_projection",
                "execution",
                "synthetic",
                lambda: project_registry_tool_result('{"success":true,"data":{}}'),
                execution_boundary="synthetic_projection_only",
            ),
        )
    )
    return tuple(scenarios)
