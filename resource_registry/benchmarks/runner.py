"""Execute isolated Registry workloads and persist sanitized baseline artifacts."""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import subprocess
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from statistics import fmean
from threading import Barrier, Lock
from time import perf_counter_ns
from typing import cast

import django
from django.db import connections

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "BotPlatform.settings")
django.setup()

from resource_registry.assistant.exploration_routing import ExplorationRoutingPolicy
from resource_registry.benchmarks.epic4_catalog import (
    EPIC4_ARTIFACT_SET,
    EPIC4_CATALOG_SCENARIO_BOUNDARIES,
    build_epic4_memory_results,
    build_static_transformation_validation_scenarios,
    evaluate_catalog_optimization_gate,
    run_independent_process_validation_scenarios,
)
from resource_registry.benchmarks.epic5_selection import (
    EPIC5_ARTIFACT_SET,
    EPIC5_CANDIDATE_ARTIFACT_SET,
    EPIC5_DATABASE_SCENARIO_NAMES,
    EPIC5_SELECTION_ARTIFACT_SETS,
    EPIC5_SELECTION_SCENARIO_BOUNDARIES,
)
from resource_registry.benchmarks.epic6_grouped_result import (
    EPIC6_CONCURRENCY_LEVELS,
    EPIC6_GROUPED_RESULT_ARTIFACT_SET,
    EPIC6_MEASURED_ITERATIONS,
    EPIC6_TRIAL_COUNT,
    EPIC6_WARMUP_COUNT,
    build_epic6_not_measured_canonical_results,
    build_epic6_projection_scenarios,
    evaluate_epic6_materiality_gate,
    run_epic6_projection_concurrency,
    validate_epic6_scenario_inventory,
)
from resource_registry.benchmarks.epic6_grouped_result_memory import (
    EPIC6_GROUPED_RESULT_MEMORY_ARTIFACT_SET,
    EPIC6_MEMORY_TRACING_CONFIGURATION,
    EPIC6_MEMORY_TRIAL_COUNT,
    build_epic6_memory_canonical_placeholders,
    build_epic6_memory_decision,
    build_epic6_memory_direct_results,
    render_epic6_memory_report,
)
from resource_registry.benchmarks.fixtures import build_routing_cases
from resource_registry.benchmarks.grouped_execution import (
    GROUPED_EXECUTION_FOLLOW_ON_STORY_RECOMMENDATIONS,
    REQUIRED_GROUPED_EXECUTION_SCENARIO_NAMES,
)
from resource_registry.benchmarks.reporting import render_report, summarize_latency_ns
from resource_registry.benchmarks.scenarios import BenchmarkScenario, build_scenarios
from resource_registry.performance.instrumentation import (
    RegistryPerformanceStage,
    reset_registry_performance_configuration_for_tests,
)

_BENCHMARK_DIRECTORY = Path(__file__).resolve().parent
_BENCHMARK_ARTIFACT_SET_ENVIRONMENT_VARIABLE = "RESOURCE_REGISTRY_BENCHMARK_ARTIFACT_SET"
_BENCHMARK_ARTIFACT_STEMS = {
    "baseline": "baseline",
    "e21_candidate": "e21_candidate",
    "e23_verification": "e23_verification",
    "epic3_grouped_baseline": "epic3_grouped_baseline",
    EPIC4_ARTIFACT_SET: EPIC4_ARTIFACT_SET,
    EPIC5_ARTIFACT_SET: EPIC5_ARTIFACT_SET,
    EPIC5_CANDIDATE_ARTIFACT_SET: EPIC5_CANDIDATE_ARTIFACT_SET,
    EPIC6_GROUPED_RESULT_ARTIFACT_SET: EPIC6_GROUPED_RESULT_ARTIFACT_SET,
    EPIC6_GROUPED_RESULT_MEMORY_ARTIFACT_SET: EPIC6_GROUPED_RESULT_MEMORY_ARTIFACT_SET,
}
_PERFORMANCE_LOGGER = logging.getLogger("resource_registry.performance")
_CONFIG_KEYS = (
    "RESOURCE_REGISTRY_PERFORMANCE_ENABLED",
    "RESOURCE_REGISTRY_PERFORMANCE_SAMPLE_RATE",
    "RESOURCE_REGISTRY_PERFORMANCE_SINK",
    "RESOURCE_REGISTRY_PERFORMANCE_SQL_ENABLED",
    "RESOURCE_REGISTRY_PERFORMANCE_PAYLOAD_ENABLED",
)
_EVENT_NUMBERS = (
    "duration_ms",
    "db_query_count",
    "db_duration_ms",
    "db_alias_count",
    "tool_count",
    "action_count",
    "schema_property_count",
    "description_char_count",
    "payload_bytes",
)
DATABASE_ADAPTER_ROUTING_SCENARIO_NAMES = (
    "routing.adapter_listing_authorized",
    "routing.adapter_schema_authorized",
    "routing.adapter_workspace_ambiguous",
    "routing.adapter_revoked_before_bif",
)


class _DatabaseSqlCapture:
    """Measure one benchmark operation's database calls without Django DEBUG query logging."""

    def __init__(self) -> None:
        """Initialize zeroed counters for a single operation."""
        self.query_count = 0
        self.duration_ns = 0

    def __call__(
        self,
        execute: Callable[..., object],
        sql: str,
        parameters: object,
        many: bool,
        context: Mapping[str, object],
    ) -> object:
        """Execute and measure one database operation.

        Args:
            execute: Django database wrapper continuation.
            sql: SQL statement text, intentionally not retained.
            parameters: SQL parameters, intentionally not retained.
            many: Whether Django is executing a batch statement.
            context: Django execution context, intentionally not retained.

        Returns:
            Result returned by Django's database wrapper.
        """
        _ = sql, parameters, many, context
        started_ns = perf_counter_ns()
        try:
            return execute(sql, parameters, many, context)
        finally:
            self.query_count += 1
            self.duration_ns += perf_counter_ns() - started_ns


@contextmanager
def capture_benchmark_database_sql(enabled: bool) -> Iterator[_DatabaseSqlCapture | None]:
    """Capture whole-operation SQL only for an explicit database-backed benchmark scenario.

    This uses Django's per-connection ``execute_wrapper`` and is independent of ``DEBUG`` and
    Registry stage instrumentation. It intentionally reports only whole-operation totals, so it
    cannot be mistaken for attribution to authorization, visibility, or context assembly.

    Args:
        enabled: Whether the current scenario requires whole-operation SQL evidence.

    Yields:
        Local SQL counters, or ``None`` for scenarios that did not request capture.
    """
    if not enabled:
        yield None
        return
    capture = _DatabaseSqlCapture()
    with connections["default"].execute_wrapper(capture):
        yield capture


def get_benchmark_artifact_paths() -> tuple[Path, Path]:
    """Return the sanctioned Registry-only result and report paths for this run.

    The default remains the accepted baseline for E1 work. Later Epic 2 runs must select their
    dedicated artifact set, which prevents a candidate or final verification execution from
    replacing accepted pre-optimization measurements.

    Returns:
        Machine-readable JSON path followed by Markdown report path.

    Raises:
        ValueError: If the requested artifact set is not an explicit supported value.
    """
    artifact_set = os.environ.get(_BENCHMARK_ARTIFACT_SET_ENVIRONMENT_VARIABLE, "baseline").strip()
    artifact_stem = _BENCHMARK_ARTIFACT_STEMS.get(artifact_set)
    if artifact_stem is None:
        supported_artifact_sets = ", ".join(sorted(_BENCHMARK_ARTIFACT_STEMS))
        raise ValueError(
            f"Unsupported Registry benchmark artifact set; use one of: {supported_artifact_sets}."
        )
    return (
        _BENCHMARK_DIRECTORY / f"{artifact_stem}_results.json",
        _BENCHMARK_DIRECTORY / f"{artifact_stem}_report.md",
    )


def get_benchmark_artifact_set() -> str:
    """Return the validated Registry benchmark artifact-set identifier.

    Returns:
        Explicit allowed artifact-set identifier for report metadata.
    """
    results_path, _report_path = get_benchmark_artifact_paths()
    return results_path.name.removesuffix("_results.json")


class _RegistryMetricCapture(logging.Handler):
    """Collect only existing fixed-stage, numeric Registry performance events."""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self._events: list[dict[str, str | int | float]] = []
        self._event_lock = Lock()

    def emit(self, record: logging.LogRecord) -> None:
        """Retain allowlisted fields from one Registry-local log event.

        Args:
            record: Logger record emitted by E1.1 instrumentation.
        """
        try:
            payload = json.loads(record.getMessage())
        except (TypeError, ValueError):
            return
        if not isinstance(payload, dict) or payload.get("event") != "resource_registry.performance":
            return
        stage = payload.get("stage")
        if stage not in {member.value for member in RegistryPerformanceStage}:
            return
        event: dict[str, str | int | float] = {"stage": stage}
        for key in _EVENT_NUMBERS:
            value = payload.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                event[key] = value
        with self._event_lock:
            self._events.append(event)

    def drain_events(self) -> list[dict[str, str | int | float]]:
        """Return and clear collected numeric events for one iteration.

        Returns:
            Previously captured, sanitized stage events.
        """
        with self._event_lock:
            events = self._events
            self._events = []
        return events


@contextmanager
def capture_registry_metrics() -> Iterator[_RegistryMetricCapture]:
    """Capture Registry-local events without changing root logging configuration.

    Yields:
        Numeric-only event collector, removed and closed on exit.
    """
    handler = _RegistryMetricCapture()
    previous_level = _PERFORMANCE_LOGGER.level
    previous_propagate = _PERFORMANCE_LOGGER.propagate
    _PERFORMANCE_LOGGER.addHandler(handler)
    _PERFORMANCE_LOGGER.setLevel(logging.INFO)
    _PERFORMANCE_LOGGER.propagate = False
    try:
        yield handler
    finally:
        _PERFORMANCE_LOGGER.removeHandler(handler)
        _PERFORMANCE_LOGGER.setLevel(previous_level)
        _PERFORMANCE_LOGGER.propagate = previous_propagate
        handler.close()


@contextmanager
def temporary_performance_configuration(
    *, enabled: bool, sql_enabled: bool, payload_enabled: bool
) -> Iterator[None]:
    """Isolate one benchmark-only E1.1–E1.3 configuration mode.

    Args:
        enabled: Whether stage timing is active.
        sql_enabled: Whether sampled stages install E1.2 wrappers.
        payload_enabled: Whether E1.3 measures publication output.

    Yields:
        The temporary configuration active only in this benchmark process.
    """
    previous = {key: os.environ.get(key) for key in _CONFIG_KEYS}
    os.environ.update(
        {
            "RESOURCE_REGISTRY_PERFORMANCE_ENABLED": str(enabled).lower(),
            "RESOURCE_REGISTRY_PERFORMANCE_SAMPLE_RATE": "1.0",
            "RESOURCE_REGISTRY_PERFORMANCE_SINK": "log",
            "RESOURCE_REGISTRY_PERFORMANCE_SQL_ENABLED": str(sql_enabled).lower(),
            "RESOURCE_REGISTRY_PERFORMANCE_PAYLOAD_ENABLED": str(payload_enabled).lower(),
        }
    )
    reset_registry_performance_configuration_for_tests()
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        reset_registry_performance_configuration_for_tests()


def _summarize_stage_events(
    events: Sequence[Mapping[str, str | int | float]],
) -> dict[str, dict[str, int | float | None]]:
    """Aggregate inclusive event values by fixed stage, never across stages.

    Args:
        events: Allowlisted events from measured iterations only.

    Returns:
        Per-stage numeric means and publication shape, with missing fields absent.
    """
    grouped: dict[str, list[Mapping[str, str | int | float]]] = {}
    for event in events:
        grouped.setdefault(str(event["stage"]), []).append(event)
    summaries: dict[str, dict[str, int | float | None]] = {}
    for stage, stage_events in grouped.items():
        summary: dict[str, int | float | None] = {"event_count": len(stage_events)}
        for key in ("duration_ms", "db_query_count", "db_duration_ms", "db_alias_count"):
            values = [float(event[key]) for event in stage_events if key in event]
            if values:
                summary[f"mean_{key}"] = fmean(values)
        for key in (
            "tool_count",
            "action_count",
            "schema_property_count",
            "description_char_count",
            "payload_bytes",
        ):
            values = [int(event[key]) for event in stage_events if key in event]
            if values:
                summary[key] = values[-1] if len(set(values)) == 1 else None
        summaries[stage] = summary
    return summaries


def _measure_result_payload_bytes(result: object) -> int | None:
    """Measure a returned benchmark value without retaining its business data.

    Args:
        result: Value returned by the scenario execution.

    Returns:
        UTF-8 encoded result size, or ``None`` when no bounded JSON representation exists.
    """
    if isinstance(result, str):
        return len(result.encode("utf-8"))
    try:
        encoded = json.dumps(result, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return None
    return len(encoded.encode("utf-8"))


def _summarize_result_payload_bytes(values: Sequence[int]) -> dict[str, float] | None:
    """Summarize actual result sizes separately from timing metrics.

    Args:
        values: Measured successful result sizes in bytes.

    Returns:
        Fixed byte-size aggregates, or ``None`` when result serialization is unavailable.
    """
    if not values:
        return None
    return {"mean": fmean(values), "min": float(min(values)), "max": float(max(values))}


def _summarize_database_sql(
    query_counts: Sequence[int],
    durations_ns: Sequence[int],
    *,
    enabled: bool,
) -> dict[str, float | str] | None:
    """Summarize benchmark-local whole-operation SQL without stage attribution.

    Args:
        query_counts: Successful-operation SQL query counts.
        durations_ns: Successful-operation SQL wrapper durations in nanoseconds.
        enabled: Whether the scenario explicitly requested root SQL measurement.

    Returns:
        Whole-operation SQL means, or ``None`` when the scenario did not request capture.
    """
    if not enabled:
        return None
    operation_count = len(query_counts)
    if operation_count == 0:
        return {
            "measurement_scope": "whole_operation",
            "mean_query_count": 0.0,
            "mean_duration_ms": 0.0,
        }
    return {
        "measurement_scope": "whole_operation",
        "mean_query_count": fmean(query_counts),
        "mean_duration_ms": fmean(durations_ns) / 1_000_000,
    }


def run_scenario(
    scenario: BenchmarkScenario, *, warmups: int, iterations: int
) -> dict[str, object]:
    """Measure one scenario while reporting failures without exception details.

    Args:
        scenario: Fixed workload and safe outcome classifier.
        warmups: Unreported warmup calls.
        iterations: Timed measured calls.

    Returns:
        Sanitized result including actual successful sample and error counts.

    Raises:
        ValueError: If requested run counts are invalid.
    """
    if warmups < 0 or iterations < 1:
        raise ValueError("warmups must be nonnegative and iterations positive")
    samples_ns: list[int] = []
    result_payload_bytes: list[int] = []
    database_sql_query_counts: list[int] = []
    database_sql_durations_ns: list[int] = []
    stage_events: list[Mapping[str, str | int | float]] = []
    observed_result_metadata: dict[str, object] | None = None
    outcomes: dict[str, int] = {}
    errors = 0
    with capture_registry_metrics() as capture:
        for _ in range(warmups):
            try:
                with capture_benchmark_database_sql(scenario.capture_database_sql):
                    scenario.execute()
            except Exception:
                return {
                    "name": scenario.name,
                    "category": scenario.category,
                    "kind": scenario.kind,
                    "execution_boundary": scenario.execution_boundary,
                    "status": "failed",
                    "reason": "Warmup failed; no measured iteration was run.",
                    "warmups": warmups,
                    "iterations": iterations,
                    "sample_count": 0,
                    "error_count": 1,
                    "outcomes": {"failed": 1},
                    "latency": None,
                    "stages": {},
                    "result_payload_bytes": None,
                    "database_sql": _summarize_database_sql(
                        (), (), enabled=scenario.capture_database_sql
                    ),
                    "authorization_context_build_count_per_operation": None,
                    "observed_result_metadata": None,
                }
            capture.drain_events()
        for _ in range(iterations):
            started_ns = perf_counter_ns()
            try:
                with capture_benchmark_database_sql(scenario.capture_database_sql) as sql_capture:
                    result = scenario.execute()
            except Exception:
                errors += 1
                outcomes["failed"] = outcomes.get("failed", 0) + 1
                capture.drain_events()
                continue
            elapsed_ns = perf_counter_ns() - started_ns
            try:
                outcome = scenario.classify_result(result)
            except Exception:
                outcome = "failed"
            if outcome != "failed":
                try:
                    current_observed_metadata = scenario.observe_result_metadata(result)
                except Exception:
                    outcome = "failed"
                else:
                    if observed_result_metadata is None:
                        observed_result_metadata = current_observed_metadata
                    elif current_observed_metadata != observed_result_metadata:
                        outcome = "failed"
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
            if outcome == "failed":
                errors += 1
            else:
                samples_ns.append(elapsed_ns)
                if sql_capture is not None:
                    database_sql_query_counts.append(sql_capture.query_count)
                    database_sql_durations_ns.append(sql_capture.duration_ns)
                payload_bytes = _measure_result_payload_bytes(result)
                if payload_bytes is not None:
                    result_payload_bytes.append(payload_bytes)
            stage_events.extend(capture.drain_events())
    stage_summaries = _summarize_stage_events(stage_events)
    authorization_context_stage = stage_summaries.get("authorization_context_build", {})
    authorization_context_event_count = authorization_context_stage.get("event_count")
    authorization_context_build_count_per_operation = (
        float(authorization_context_event_count) / iterations
        if isinstance(authorization_context_event_count, int)
        else None
    )
    return {
        "name": scenario.name,
        "category": scenario.category,
        "kind": scenario.kind,
        "execution_boundary": scenario.execution_boundary,
        "status": "failed" if errors else "measured",
        "reason": None,
        "warmups": warmups,
        "iterations": iterations,
        "sample_count": len(samples_ns),
        "error_count": errors,
        "outcomes": outcomes,
        "latency": summarize_latency_ns(samples_ns),
        "stages": stage_summaries,
        "result_payload_bytes": _summarize_result_payload_bytes(result_payload_bytes),
        "database_sql": _summarize_database_sql(
            database_sql_query_counts,
            database_sql_durations_ns,
            enabled=scenario.capture_database_sql,
        ),
        "authorization_context_build_count_per_operation": (
            authorization_context_build_count_per_operation
        ),
        "scenario_metadata": dict(scenario.metadata),
        "observed_result_metadata": observed_result_metadata,
    }


def run_concurrency_scenario(*, workers: int, operations: int) -> dict[str, object]:
    """Measure true thread-pool routing concurrency without database connections.

    Args:
        workers: Simultaneous worker count.
        operations: Total operations; must be a multiple of workers.

    Returns:
        Wall-latency distribution and completed/failed throughput counts.
    """
    if workers < 1 or operations < workers or operations % workers:
        raise ValueError("operations must be a positive multiple of workers")
    policy = ExplorationRoutingPolicy()
    request = build_routing_cases()["explore"]
    samples_ns: list[int] = []
    errors = 0

    def route_once(barrier: Barrier) -> int:
        barrier.wait(timeout=10)
        started_ns = perf_counter_ns()
        decision = policy.route(request)
        if decision.status != "ready":
            raise RuntimeError("unexpected routing outcome")
        return perf_counter_ns() - started_ns

    started_wall_ns = perf_counter_ns()
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for _ in range(operations // workers):
            barrier = Barrier(workers)
            futures = [executor.submit(route_once, barrier) for _ in range(workers)]
            for future in futures:
                try:
                    samples_ns.append(future.result())
                except Exception:
                    errors += 1
    wall_seconds = (perf_counter_ns() - started_wall_ns) / 1_000_000_000
    return {
        "name": f"concurrency.routing_{workers}",
        "category": "concurrency",
        "kind": "synthetic_threaded",
        "execution_boundary": "registry_pre_dispatch_only",
        "status": "failed" if errors else "measured",
        "reason": None,
        "warmups": 0,
        "iterations": operations,
        "concurrency": workers,
        "sample_count": len(samples_ns),
        "completed_count": len(samples_ns),
        "error_count": errors,
        "throughput_ops_per_second": len(samples_ns) / wall_seconds if wall_seconds else None,
        "outcomes": {"routed": len(samples_ns), "failed": errors},
        "latency": summarize_latency_ns(samples_ns),
        "stages": {},
    }


def run_concurrent_database_scenario(
    scenario: BenchmarkScenario,
    *,
    workers: int,
    warmups: int,
    iterations: int,
) -> dict[str, object]:
    """Measure independent database-backed grouped calls at a bounded concurrency level.

    Each ``scenario.execute`` call is responsible for creating its own request, session, and
    client.  This runner only schedules those independent calls; it neither reuses request
    state nor turns a failed invocation into a retry.

    Args:
        scenario: Database workload with an explicit execution boundary.
        workers: Maximum simultaneous request workers.
        warmups: Excluded operations before timing.
        iterations: Total measured operations across all worker batches.

    Returns:
        Sanitized timing, result-size, and Registry instrumentation evidence.

    Raises:
        ValueError: If concurrency or run counts are invalid.
    """
    if workers < 1 or warmups < 0 or iterations < 1:
        raise ValueError("workers and iterations must be positive; warmups must be nonnegative")

    def execute_batch(executor: ThreadPoolExecutor, operation_count: int) -> list[object]:
        barrier = Barrier(operation_count)

        def invoke_once() -> object:
            barrier.wait(timeout=10)
            return scenario.execute()

        futures = [executor.submit(invoke_once) for _ in range(operation_count)]
        values: list[object] = []
        for future in futures:
            values.append(future.result())
        return values

    outcomes: dict[str, int] = {}
    samples_ns: list[int] = []
    result_payload_bytes: list[int] = []
    errors = 0
    stage_events: list[Mapping[str, str | int | float]] = []
    with capture_registry_metrics() as capture, ThreadPoolExecutor(max_workers=workers) as executor:
        remaining_warmups = warmups
        while remaining_warmups:
            batch_size = min(workers, remaining_warmups)
            try:
                execute_batch(executor, batch_size)
            except Exception:
                return {
                    "name": scenario.name,
                    "category": scenario.category,
                    "kind": scenario.kind,
                    "execution_boundary": scenario.execution_boundary,
                    "status": "failed",
                    "reason": "Warmup failed; no measured iteration was run.",
                    "warmups": warmups,
                    "iterations": iterations,
                    "concurrency": workers,
                    "sample_count": 0,
                    "completed_count": 0,
                    "error_count": 1,
                    "outcomes": {"failed": 1},
                    "latency": None,
                    "stages": {},
                    "result_payload_bytes": None,
                    "authorization_context_build_count_per_operation": None,
                }
            capture.drain_events()
            remaining_warmups -= batch_size
        remaining_iterations = iterations
        while remaining_iterations:
            batch_size = min(workers, remaining_iterations)
            started_ns = perf_counter_ns()
            try:
                values = execute_batch(executor, batch_size)
            except Exception:
                errors += batch_size
                outcomes["failed"] = outcomes.get("failed", 0) + batch_size
                capture.drain_events()
                remaining_iterations -= batch_size
                continue
            elapsed_ns = perf_counter_ns() - started_ns
            per_operation_ns = elapsed_ns // batch_size
            for value in values:
                try:
                    outcome = scenario.classify_result(value)
                except Exception:
                    outcome = "failed"
                outcomes[outcome] = outcomes.get(outcome, 0) + 1
                if outcome == "failed":
                    errors += 1
                    continue
                samples_ns.append(per_operation_ns)
                payload_bytes = _measure_result_payload_bytes(value)
                if payload_bytes is not None:
                    result_payload_bytes.append(payload_bytes)
            stage_events.extend(capture.drain_events())
            remaining_iterations -= batch_size
    stage_summaries = _summarize_stage_events(stage_events)
    authorization_context_stage = stage_summaries.get("authorization_context_build", {})
    authorization_context_event_count = authorization_context_stage.get("event_count")
    authorization_context_build_count_per_operation = (
        float(authorization_context_event_count) / iterations
        if isinstance(authorization_context_event_count, int)
        else None
    )
    return {
        "name": scenario.name,
        "category": scenario.category,
        "kind": scenario.kind,
        "execution_boundary": scenario.execution_boundary,
        "status": "failed" if errors else "measured",
        "reason": None,
        "warmups": warmups,
        "iterations": iterations,
        "concurrency": workers,
        "sample_count": len(samples_ns),
        "completed_count": len(samples_ns),
        "error_count": errors,
        "outcomes": outcomes,
        "latency": summarize_latency_ns(samples_ns),
        "stages": stage_summaries,
        "result_payload_bytes": _summarize_result_payload_bytes(result_payload_bytes),
        "authorization_context_build_count_per_operation": (
            authorization_context_build_count_per_operation
        ),
    }


def run_overhead_scenarios(*, warmups: int, iterations: int) -> list[dict[str, object]]:
    """Run identical publication work under five isolated instrumentation modes.

    Args:
        warmups: Excluded warmup calls in every mode.
        iterations: Measured calls in every mode.

    Returns:
        Comparable wall-time distributions and relative median overhead.
    """
    publication = next(item for item in build_scenarios() if item.name == "publication.partial")
    modes = (
        ("disabled", False, False, False),
        ("e11", True, False, False),
        ("e11_sql", True, True, False),
        ("e11_payload", True, False, True),
        ("e11_sql_payload", True, True, True),
    )
    results: list[dict[str, object]] = []
    baseline_p50: float | None = None
    for mode, enabled, sql_enabled, payload_enabled in modes:
        with temporary_performance_configuration(
            enabled=enabled, sql_enabled=sql_enabled, payload_enabled=payload_enabled
        ):
            result = run_scenario(publication, warmups=warmups, iterations=iterations)
        result["name"] = f"overhead.{mode}"
        result["category"] = "overhead"
        result["instrumentation"] = {
            "enabled": enabled,
            "sql_enabled": sql_enabled,
            "payload_enabled": payload_enabled,
            "sample_rate": 1.0,
            "sink": "registry_local_capture",
        }
        latency = result["latency"]
        raw_p50 = cast(Mapping[str, object], latency).get("p50_ms") if latency else None
        p50 = float(raw_p50) if isinstance(raw_p50, (int, float)) else None
        if mode == "disabled":
            baseline_p50 = p50
        result["p50_delta_ms"] = (
            p50 - baseline_p50 if p50 is not None and baseline_p50 is not None else None
        )
        result["p50_delta_percent"] = (
            (p50 / baseline_p50 - 1) * 100
            if p50 is not None and baseline_p50 is not None and baseline_p50 > 0
            else None
        )
        results.append(result)
    return results


def not_measured_result(
    name: str,
    category: str,
    reason: str,
    *,
    kind: str | None = None,
) -> dict[str, object]:
    """Declare a required scenario unavailable rather than inventing data.

    Args:
        name: Fixed scenario name.
        category: Fixed result category.
        reason: Non-sensitive dependency or scope limitation.
        kind: Optional fixed fixture kind. Defaults to the established category mapping.

    Returns:
        Explicit zero-sample record with unavailable metrics.
    """
    return {
        "name": name,
        "category": category,
        "kind": kind
        or ("isolated_database" if category in {"selection", "publication"} else "integration"),
        "status": "not_measured",
        "execution_boundary": "not_measured",
        "reason": reason,
        "warmups": 0,
        "iterations": 0,
        "sample_count": 0,
        "error_count": 0,
        "outcomes": {},
        "latency": None,
        "stages": {},
        "result_payload_bytes": None,
        "authorization_context_build_count_per_operation": None,
    }


def build_metadata(*, warmups: int, iterations: int) -> dict[str, object]:
    """Record only allowlisted reproducibility metadata.

    Args:
        warmups: Serial scenario warmup count.
        iterations: Serial scenario measurement count.

    Returns:
        Runtime and benchmark settings with no host, database, or user identity.
    """
    revision = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        capture_output=True,
        check=False,
        text=True,
    )
    dirty = subprocess.run(
        ["git", "status", "--porcelain"], capture_output=True, check=False, text=True
    )
    return {
        "git_revision": revision.stdout.strip() if revision.returncode == 0 else "unavailable",
        "dirty": bool(dirty.stdout.strip()) if dirty.returncode == 0 else "unavailable",
        "python_version": platform.python_version(),
        "django_version": django.get_version(),
        "operating_system": platform.system(),
        "cpu_architecture": platform.machine(),
        "database_engine": "not_used_in_synthetic_run",
        "warmups": warmups,
        "iterations": iterations,
        "main_instrumentation": {
            "enabled": True,
            "sql_enabled": True,
            "payload_enabled": True,
            "sample_rate": 1.0,
            "sink": "registry_local_capture",
        },
        "concurrency_levels": [1, 10, 25],
    }


def persist_baseline(
    metadata: Mapping[str, object], results: Sequence[Mapping[str, object]]
) -> None:
    """Write the sanitized machine-readable and Markdown baseline under Registry.

    Args:
        metadata: Allowlisted environment details.
        results: Actual scenario records, including explicit unavailable cases.
    """
    results_path, report_path = get_benchmark_artifact_paths()
    payload = {"schema_version": 1, "metadata": dict(metadata), "results": list(results)}
    results_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    if metadata.get("artifact_set") == EPIC6_GROUPED_RESULT_MEMORY_ARTIFACT_SET:
        report_path.write_text(render_epic6_memory_report(metadata, results))
    else:
        report_path.write_text(render_report(metadata, results))


def main(argv: Sequence[str] | None = None) -> int:
    """Run the safe synthetic suite and write a reproducible baseline draft.

    Args:
        argv: Optional command-line arguments for tests or direct invocation.

    Returns:
        Zero if all executable scenarios completed, otherwise one.
    """
    parser = argparse.ArgumentParser(description="Run isolated Resource Registry benchmarks")
    parser.add_argument("--warmups", type=int, default=5)
    parser.add_argument("--iterations", type=int, default=120)
    arguments = parser.parse_args(argv)
    if arguments.warmups < 0 or arguments.iterations < 1:
        parser.error("warmups must be nonnegative and iterations positive")
    artifact_set = get_benchmark_artifact_set()
    if artifact_set == "epic3_grouped_baseline":
        results = [
            not_measured_result(
                name,
                "grouped_execution",
                "Run the isolated RR-03.1 database benchmark command to replace this placeholder.",
                kind=(
                    "isolated_database"
                    if name in REQUIRED_GROUPED_EXECUTION_SCENARIO_NAMES
                    else None
                ),
            )
            for name in REQUIRED_GROUPED_EXECUTION_SCENARIO_NAMES
        ]
        metadata = build_metadata(warmups=arguments.warmups, iterations=arguments.iterations)
        metadata["artifact_set"] = artifact_set
        metadata["methodology"] = (
            "RR-03.1 placeholders are replaced only by the isolated database benchmark test."
        )
        metadata["follow_on_story_recommendations"] = [
            dict(recommendation)
            for recommendation in GROUPED_EXECUTION_FOLLOW_ON_STORY_RECOMMENDATIONS
        ]
        persist_baseline(metadata, results)
        print("Recorded RR-03.1 placeholders; run the isolated database benchmark next.")
        return 0
    if artifact_set == EPIC4_ARTIFACT_SET:
        results = _run_epic4_catalog_baseline(
            warmups=arguments.warmups,
            iterations=arguments.iterations,
        )
        metadata = build_metadata(warmups=arguments.warmups, iterations=arguments.iterations)
        metadata["artifact_set"] = artifact_set
        metadata["methodology"] = (
            "RR-04.1 static scenarios use source-controlled transformation schemas. "
            "Persona catalog and canonical-admission scenarios are explicit isolated-database "
            "placeholders until the guarded pytest benchmark replaces them."
        )
        metadata["memory_methodology"] = (
            "tracemalloc allocation and retention deltas are separate from uninstrumented latency "
            "and are not memory-leak detection."
        )
        metadata["process_methodology"] = (
            "Each cold or warm trial starts a new subprocess interpreter; process-global Python "
            "state is not shared between trials."
        )
        baseline_p50_ms = _get_result_p50(results, "catalog.validator.cold.valid")
        metadata["rr04_optimization_gate"] = evaluate_catalog_optimization_gate(
            baseline_p50_ms=baseline_p50_ms,
            candidate_p50_ms=None,
        )
        persist_baseline(metadata, results)
        print("Recorded RR-04.1 static evidence and isolated-database placeholders.")
        return int(any(result["status"] == "failed" for result in results))
    if artifact_set in EPIC5_SELECTION_ARTIFACT_SETS:
        results = [
            not_measured_result(
                name,
                "selection",
                "Run the guarded RR-05.1 isolated PostgreSQL benchmark command.",
                kind="isolated_database",
            )
            for name in EPIC5_DATABASE_SCENARIO_NAMES
        ]
        for result in results:
            result["execution_boundary"] = EPIC5_SELECTION_SCENARIO_BOUNDARIES[str(result["name"])]
        metadata = build_metadata(warmups=arguments.warmups, iterations=arguments.iterations)
        metadata["artifact_set"] = artifact_set
        metadata["methodology"] = (
            "RR-05.1 placeholders are replaced only by three independent guarded isolated "
            "PostgreSQL trials. Prompt-context, verified comparison discovery, and version "
            "revalidation use the current production selection paths without batching."
            if artifact_set == EPIC5_ARTIFACT_SET
            else "RR-05.2 candidate placeholders are replaced only by three independent "
            "guarded isolated PostgreSQL trials. Prompt-context and verified comparison "
            "discovery use bounded Registry-local cached-selection revalidation."
        )
        metadata["selection_scenario_boundaries"] = dict(EPIC5_SELECTION_SCENARIO_BOUNDARIES)
        persist_baseline(metadata, results)
        print("Recorded RR-05 isolated-database placeholders; run the database benchmark next.")
        return 0
    if artifact_set == EPIC6_GROUPED_RESULT_ARTIFACT_SET:
        if (
            arguments.warmups != EPIC6_WARMUP_COUNT
            or arguments.iterations != EPIC6_MEASURED_ITERATIONS
        ):
            parser.error("RR-09.1 requires exactly 5 warmups and 120 measured iterations.")
        projection_scenarios = build_epic6_projection_scenarios()
        validate_epic6_scenario_inventory(projection_scenarios)
        direct_trials: list[dict[str, object]] = []
        primary_direct_results: list[dict[str, object]] = []
        for trial_number in range(1, EPIC6_TRIAL_COUNT + 1):
            with temporary_performance_configuration(
                enabled=True,
                sql_enabled=True,
                payload_enabled=True,
            ):
                trial_results = [
                    run_scenario(
                        scenario,
                        warmups=arguments.warmups,
                        iterations=arguments.iterations,
                    )
                    for scenario in projection_scenarios
                ]
                with capture_registry_metrics():
                    trial_results.extend(
                        run_epic6_projection_concurrency(
                            workers=workers,
                            warmups=arguments.warmups,
                            operations=workers * ((arguments.iterations + workers - 1) // workers),
                        )
                        for workers in EPIC6_CONCURRENCY_LEVELS
                    )
            if trial_number == 1:
                primary_direct_results = trial_results
            direct_trials.append({"trial": trial_number, "results": trial_results})
        results = [*primary_direct_results, *build_epic6_not_measured_canonical_results()]
        metadata = build_metadata(warmups=arguments.warmups, iterations=arguments.iterations)
        metadata.update(
            {
                "artifact_set": artifact_set,
                "methodology": (
                    "RR-09.1 measures the unmodified production grouped-result projection function "
                    "with deterministic direct fixtures. Guarded PostgreSQL canonical list evidence "
                    "replaces only its explicit placeholders; unsupported canonical scenarios remain "
                    "not_measured with their exact fixture limitation."
                ),
                "epic6_direct_projection_trial_count": EPIC6_TRIAL_COUNT,
                "epic6_direct_projection_trial_method": (
                    "Three sequential direct-projection trials with five excluded warmups and 120 "
                    "measured serial invocations. Concurrent direct-projection workloads use per-wave "
                    "barriers at 1, 10, and 25 workers."
                ),
                "epic6_direct_projection_trials": direct_trials,
                "epic6_canonical_execution_status": "pending_guarded_isolated_postgresql",
                "epic6_parse_serialization_attribution": (
                    "Direct projection timing measures the unmodified production function as one unit. "
                    "JSON decoding, rewrite checks, and encoding are not exclusively attributed because "
                    "RR-09.1 does not modify production interfaces or instrument implementation internals."
                ),
                "epic6_materiality_gate": evaluate_epic6_materiality_gate(),
            }
        )
        persist_baseline(metadata, results)
        print("Recorded RR-09.1 direct evidence and canonical placeholders; run PostgreSQL next.")
        return int(any(result["status"] == "failed" for result in results))
    if artifact_set == EPIC6_GROUPED_RESULT_MEMORY_ARTIFACT_SET:
        if arguments.warmups != 0 or arguments.iterations != EPIC6_MEMORY_TRIAL_COUNT:
            parser.error("RR-09.2 requires --warmups 0 and --iterations 3 for trial selection.")
        with temporary_performance_configuration(
            enabled=False,
            sql_enabled=False,
            payload_enabled=False,
        ):
            results = build_epic6_memory_direct_results()
        results.extend(build_epic6_memory_canonical_placeholders())
        metadata = build_metadata(warmups=arguments.warmups, iterations=arguments.iterations)
        metadata.update(
            {
                "artifact_set": artifact_set,
                "methodology": (
                    "RR-09.2 profiles Python allocations with isolated tracemalloc boundaries. "
                    "It is separate from RR-09.1 latency, SQL, and performance-event evidence."
                ),
                "main_instrumentation": {
                    "enabled": False,
                    "sql_enabled": False,
                    "payload_enabled": False,
                    "reason": "RR-09.2 excludes Registry performance instrumentation.",
                },
                "latency_measurements_included": False,
                "tracing_configuration": dict(EPIC6_MEMORY_TRACING_CONFIGURATION),
                "epic6_memory_trial_count": EPIC6_MEMORY_TRIAL_COUNT,
                "epic6_memory_trial_method": (
                    "Three independent traced trials per single, repeated, and concurrent "
                    "scenario. Inputs are prepared before each tracing boundary; returned output "
                    "strings remain alive for current/peak readings and are released before GC."
                ),
                "epic6_memory_canonical_execution_status": "pending_guarded_isolated_postgresql",
                "epic6_memory_canonical_limitation": (
                    "Canonical results remain placeholders until the guarded PostgreSQL benchmark "
                    "test profiles the full inclusive dispatch boundary."
                ),
                "epic6_memory_decision": build_epic6_memory_decision(),
            }
        )
        persist_baseline(metadata, results)
        print(
            "Recorded RR-09.2 direct memory evidence and canonical placeholders; run PostgreSQL next."
        )
        return int(any(result["status"] == "failed" for result in results))
    with temporary_performance_configuration(enabled=True, sql_enabled=True, payload_enabled=True):
        results = [
            run_scenario(item, warmups=arguments.warmups, iterations=arguments.iterations)
            for item in build_scenarios()
        ]
    results.extend(
        run_concurrency_scenario(
            workers=workers, operations=workers * ((120 + workers - 1) // workers)
        )
        for workers in (1, 10, 25)
    )
    results.extend(
        run_overhead_scenarios(warmups=arguments.warmups, iterations=arguments.iterations)
    )
    for count in (0, 1, 10, 50):
        results.append(
            not_measured_result(
                f"selection.{count}",
                "selection",
                "Run the explicit isolated pytest database benchmark command in README.md.",
            )
        )
    for name in ("revoked", "stale"):
        results.append(
            not_measured_result(
                f"selection.{name}",
                "selection",
                "Run the explicit isolated pytest database benchmark command in README.md.",
            )
        )
    results.extend(
        (
            not_measured_result(
                "publication.persona_queryset",
                "publication",
                "Run the explicit isolated pytest database benchmark command in README.md.",
            ),
            not_measured_result(
                "execution.canonical_dispatch_bridge",
                "execution",
                "Shared dispatcher and provider integration are outside Registry-only benchmarking.",
            ),
            not_measured_result(
                "execution.admission_rejection",
                "execution",
                "Run the explicit isolated pytest database benchmark command in README.md.",
            ),
            not_measured_result(
                "execution.validation_rejection",
                "execution",
                "Run the explicit isolated pytest database benchmark command in README.md.",
            ),
            not_measured_result(
                "routing.bridge_failure",
                "routing",
                "No safe provider-free bridge failure scenario is available in this synthetic run.",
            ),
            *(
                not_measured_result(
                    name,
                    "routing",
                    "Run the explicit isolated pytest database benchmark command in README.md.",
                    kind="isolated_database",
                )
                for name in DATABASE_ADAPTER_ROUTING_SCENARIO_NAMES
            ),
        )
    )
    metadata = build_metadata(warmups=arguments.warmups, iterations=arguments.iterations)
    metadata["artifact_set"] = artifact_set
    persist_baseline(metadata, results)
    print(f"Recorded {len(results)} Registry scenarios; result files remain in resource_registry.")
    return int(any(result["status"] == "failed" for result in results))


def _run_epic4_catalog_baseline(*, warmups: int, iterations: int) -> list[dict[str, object]]:
    """Run safe RR-04.1 static work and declare database-only work unavailable.

    Args:
        warmups: Excluded warmup calls for normal latency scenarios.
        iterations: Measured calls for normal latency scenarios.

    Returns:
        Sanitized static, memory, process, and database-placeholder benchmark records.
    """
    with temporary_performance_configuration(enabled=True, sql_enabled=True, payload_enabled=True):
        results = [
            run_scenario(scenario, warmups=warmups, iterations=iterations)
            for scenario in build_static_transformation_validation_scenarios()
        ]
    results.extend(build_epic4_memory_results())
    results.extend(
        run_independent_process_validation_scenarios(warmups=warmups, iterations=iterations)
    )
    static_names = {str(result["name"]) for result in results}
    for name in EPIC4_CATALOG_SCENARIO_BOUNDARIES:
        if name in static_names:
            continue
        results.append(
            not_measured_result(
                name,
                "publication" if name.startswith("catalog.publication") else "security",
                "Run the explicit isolated RR-04.1 pytest database benchmark command.",
                kind="isolated_database",
            )
        )
    return results


def _get_result_p50(results: Sequence[Mapping[str, object]], name: str) -> float | None:
    """Return one measured latency median without coercing unavailable metrics.

    Args:
        results: Sanitized benchmark records.
        name: Scenario name whose p50 is required.

    Returns:
        Measured millisecond p50, or ``None`` when it is unavailable.
    """
    result = next((record for record in results if record.get("name") == name), None)
    latency = result.get("latency") if isinstance(result, Mapping) else None
    value = latency.get("p50_ms") if isinstance(latency, Mapping) else None
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


if __name__ == "__main__":
    sys.exit(main())
