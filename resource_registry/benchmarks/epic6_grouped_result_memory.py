"""Profile RR-09.2 grouped-result allocation and retention behavior only.

The functions in this module deliberately run the existing production projection function.
They do not alter grouped-result behavior, authorization, dispatch, or latency measurements.
"""

from __future__ import annotations

import gc
import os
import sys
import tracemalloc
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event, Lock
from typing import TypeAlias

from resource_registry.benchmarks.epic6_grouped_result import (
    EPIC6_CANONICAL_SCENARIO_NAMES,
    EPIC6_PROJECTION_SCENARIO_NAMES,
    ProjectionFixture,
    build_epic6_projection_fixtures,
)
from resource_registry.bifs.grouped_tools import project_registry_tool_result

MemoryRecord: TypeAlias = dict[str, object]
MemoryTrial: TypeAlias = dict[str, object]

EPIC6_GROUPED_RESULT_MEMORY_ARTIFACT_SET = "epic6_grouped_result_memory"
EPIC6_MEMORY_TRIAL_COUNT = 3
EPIC6_MEMORY_TRACEBACK_FRAME_LIMIT = 10
EPIC6_MEMORY_STABILIZATION_ITERATIONS = 2
EPIC6_MEMORY_CONCURRENCY_LEVELS = (1, 10, 25)
EPIC6_MEMORY_REPEATED_ITERATION_COUNTS = (100, 500, 1000)
EPIC6_MEMORY_TRACING_CONFIGURATION: Mapping[str, object] = {
    "library": "tracemalloc",
    "traceback_frame_limit": EPIC6_MEMORY_TRACEBACK_FRAME_LIMIT,
    "units": "bytes",
    "input_fixture_prepared_before_tracing": True,
    "output_string_included_in_current_and_peak": True,
    "post_gc_delta_is_signed": True,
    "global_tracing_policy": "not_measured_when_already_active",
}
EPIC6_MEMORY_SINGLE_SCENARIO_NAMES = EPIC6_PROJECTION_SCENARIO_NAMES
EPIC6_MEMORY_CANONICAL_SCENARIO_NAMES = tuple(
    name
    for name in EPIC6_CANONICAL_SCENARIO_NAMES
    if name.startswith("grouped_result.canonical.dataset_list.")
)
EPIC6_MEMORY_REPEATED_SCENARIO_NAMES = (
    "grouped_result.projection.unchanged.records_001",
    "grouped_result.projection.rewritten.hint.records_100",
    "grouped_result.projection.structured.long_hint",
)
EPIC6_MEMORY_CONCURRENCY_SCENARIO_NAMES = tuple(
    f"grouped_result.memory.concurrency.workers_{workers:03d}"
    for workers in EPIC6_MEMORY_CONCURRENCY_LEVELS
)


def measure_epic6_memory_operation(
    *,
    execute: Callable[[], object],
    is_expected_result: Callable[[object], bool],
) -> MemoryTrial:
    """Measure one prepared operation with a clean, owned ``tracemalloc`` boundary.

    The caller must create the input fixture and callable before invoking this function. The
    returned result remains strongly referenced until the current/peak readings are captured,
    then is released before garbage collection. A pre-existing global tracing session cannot be
    isolated safely and is reported as ``not_measured`` rather than reset or silently blended.

    Args:
        execute: Prepared production operation to execute exactly once.
        is_expected_result: Exact parity predicate for the operation's returned value.

    Returns:
        One bounded trial containing traced bytes, exact-parity status, and a signed post-GC
        retained delta.
    """
    if tracemalloc.is_tracing():
        return {
            "status": "not_measured",
            "reason": "tracemalloc_was_already_active",
            "output_parity": "not_checked",
            "output_payload_bytes": None,
        }

    gc.collect()
    tracemalloc.start(EPIC6_MEMORY_TRACEBACK_FRAME_LIMIT)
    try:
        tracemalloc.clear_traces()
        baseline_current_bytes, _ = tracemalloc.get_traced_memory()
        try:
            result = execute()
        except Exception:
            return {
                "status": "failed",
                "reason": "operation_raised",
                "output_parity": "not_checked",
                "output_payload_bytes": None,
            }
        current_after_execution_bytes, peak_bytes = tracemalloc.get_traced_memory()
        try:
            output_parity = "exact" if is_expected_result(result) else "mismatch"
        except Exception:
            output_parity = "verification_failed"
        output_payload_bytes = _get_serialized_string_bytes(result)
        del result
        gc.collect()
        post_gc_current_bytes, _ = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    return {
        "status": "measured" if output_parity == "exact" else "failed",
        "reason": None if output_parity == "exact" else "output_parity_failure",
        "traced_baseline_current_bytes": baseline_current_bytes,
        "traced_current_after_execution_bytes": current_after_execution_bytes,
        "traced_peak_bytes": peak_bytes,
        "post_gc_current_bytes": post_gc_current_bytes,
        "post_gc_retained_delta_bytes": post_gc_current_bytes - baseline_current_bytes,
        "temporary_peak_amplification_bytes": max(0, peak_bytes - baseline_current_bytes),
        "output_parity": output_parity,
        "output_payload_bytes": output_payload_bytes,
        "output_included_during_measurement": True,
    }


def profile_epic6_projection_fixture_memory(fixture: ProjectionFixture) -> MemoryRecord:
    """Profile three exact direct-projection trials for one RR-09.1 fixture.

    Args:
        fixture: Prepared deterministic source and expected result contract.

    Returns:
        One direct-projection memory record with actual input/output byte sizes and trial data.
    """
    _stabilize_epic6_fixture(fixture)
    trials: list[MemoryTrial] = []
    for trial_number in range(1, EPIC6_MEMORY_TRIAL_COUNT + 1):
        trial = measure_epic6_memory_operation(
            execute=lambda: project_registry_tool_result(fixture.payload),
            is_expected_result=lambda result: result == fixture.expected_result,
        )
        trial["trial"] = trial_number
        trials.append(trial)
    return _build_memory_record(
        name=fixture.name,
        category="direct_projection_memory",
        kind="tracemalloc_direct_production_function",
        execution_boundary="synthetic_projection_only",
        input_payload_bytes=fixture.input_payload_bytes,
        output_payload_bytes=len(fixture.expected_result.encode("utf-8")),
        record_count=fixture.record_count,
        projection_changes_result=fixture.projection_changes_result,
        trials=trials,
        scenario_metadata={
            "fixture_kind": fixture.fixture_kind,
            "measurement_boundary": "direct_production_projection_function",
        },
    )


def profile_epic6_repeated_projection_retention(
    fixture: ProjectionFixture,
    *,
    iterations: int,
) -> MemoryRecord:
    """Profile repeated direct projections while releasing every result immediately.

    Args:
        fixture: Prepared deterministic source and expected result contract.
        iterations: Number of sequential projections per independent trial.

    Returns:
        Retention-checkpoint evidence without output accumulation.

    Raises:
        ValueError: If ``iterations`` is not one of the registered RR-09.2 counts.
    """
    if iterations not in EPIC6_MEMORY_REPEATED_ITERATION_COUNTS:
        raise ValueError("RR-09.2 repeated retention requires a registered iteration count.")
    _stabilize_epic6_fixture(fixture)
    trials = [
        _measure_repeated_projection_trial(
            fixture, iterations=iterations, trial_number=trial_number
        )
        for trial_number in range(1, EPIC6_MEMORY_TRIAL_COUNT + 1)
    ]
    result = _build_memory_record(
        name=_get_retention_scenario_name(fixture, iterations=iterations),
        category="repeated_projection_retention",
        kind="tracemalloc_repeated_direct_production_function",
        execution_boundary="synthetic_projection_only",
        input_payload_bytes=fixture.input_payload_bytes,
        output_payload_bytes=len(fixture.expected_result.encode("utf-8")),
        record_count=fixture.record_count,
        projection_changes_result=fixture.projection_changes_result,
        trials=trials,
        scenario_metadata={
            "fixture_kind": fixture.fixture_kind,
            "measurement_boundary": "repeated_direct_production_projection_function",
        },
    )
    result["iterations_per_trial"] = iterations
    result["output_retention"] = "none"
    result["negative_control"] = fixture.name == "grouped_result.projection.unchanged.records_001"
    result["retention_interpretation"] = (
        "Signed post-GC deltas can reflect allocator or cache behavior and are not leak evidence "
        "without repeatable lifetime analysis."
    )
    return result


def run_epic6_memory_concurrency(*, workers: int) -> MemoryRecord:
    """Measure synchronized concurrent direct projections as process-wide allocation evidence.

    Args:
        workers: Registered number of concurrently live projection operations.

    Returns:
        Three synchronized threaded trials. Traced and RSS values are process-wide and are not
        divided or attributed to individual requests.

    Raises:
        ValueError: If ``workers`` is not a registered RR-09.2 concurrency level.
    """
    if workers not in EPIC6_MEMORY_CONCURRENCY_LEVELS:
        raise ValueError("RR-09.2 concurrency requires a registered worker level.")
    fixture = next(
        item
        for item in build_epic6_projection_fixtures()
        if item.name == "grouped_result.projection.rewritten.hint.records_100"
    )
    _stabilize_epic6_fixture(fixture)
    trials = [
        _measure_concurrent_projection_trial(fixture, workers=workers, trial_number=trial_number)
        for trial_number in range(1, EPIC6_MEMORY_TRIAL_COUNT + 1)
    ]
    result = _build_memory_record(
        name=f"grouped_result.memory.concurrency.workers_{workers:03d}",
        category="concurrent_projection_memory",
        kind="tracemalloc_threaded_direct_production_function",
        execution_boundary="synthetic_projection_only",
        input_payload_bytes=fixture.input_payload_bytes,
        output_payload_bytes=len(fixture.expected_result.encode("utf-8")),
        record_count=fixture.record_count,
        projection_changes_result=fixture.projection_changes_result,
        trials=trials,
        scenario_metadata={
            "fixture_kind": fixture.fixture_kind,
            "measurement_boundary": "synchronized_direct_projection_operation_lifetime",
            "synchronization": "start_barrier_and_output_release_event",
        },
    )
    result["concurrency"] = workers
    result["completed_operations"] = (
        sum(_get_trial_integer(trial, "completed_operations") for trial in trials)
        // EPIC6_MEMORY_TRIAL_COUNT
    )
    result["unexpected_error_count"] = sum(
        _get_trial_integer(trial, "unexpected_error_count") for trial in trials
    )
    result["tracemalloc_scope"] = "process_wide"
    result["process_rss_scope"] = "process_wide"
    result["per_request_attribution"] = "not_supported"
    result["gil_and_scheduling_limitation"] = (
        "Python's GIL and local scheduling affect execution overlap. The start barrier and held "
        "output lifetime confirm concurrent operations, not simultaneous CPU execution."
    )
    return result


def build_epic6_memory_direct_results() -> list[MemoryRecord]:
    """Build the complete direct RR-09.2 memory evidence matrix.

    Returns:
        Single-operation, repeated-retention, and synchronized concurrency records in stable order.

    Raises:
        ValueError: If the RR-09.1 fixture inventory no longer supports the declared matrix.
    """
    fixtures_by_name = {fixture.name: fixture for fixture in build_epic6_projection_fixtures()}
    if set(fixtures_by_name) != set(EPIC6_MEMORY_SINGLE_SCENARIO_NAMES):
        raise ValueError("RR-09.2 direct fixture inventory differs from RR-09.1.")
    results = [
        profile_epic6_projection_fixture_memory(fixture) for fixture in fixtures_by_name.values()
    ]
    for fixture_name in EPIC6_MEMORY_REPEATED_SCENARIO_NAMES:
        fixture = fixtures_by_name[fixture_name]
        for iterations in EPIC6_MEMORY_REPEATED_ITERATION_COUNTS:
            results.append(
                profile_epic6_repeated_projection_retention(fixture, iterations=iterations)
            )
    results.extend(
        run_epic6_memory_concurrency(workers=workers) for workers in EPIC6_MEMORY_CONCURRENCY_LEVELS
    )
    return results


def build_epic6_memory_canonical_placeholders() -> list[MemoryRecord]:
    """Declare guarded canonical memory scenarios before PostgreSQL evidence is recorded.

    Returns:
        Explicit unmeasured canonical records; they are replaced only by the guarded database test.
    """
    return [
        {
            "name": name,
            "category": "canonical_execution_memory",
            "kind": "isolated_postgresql_tracemalloc",
            "execution_boundary": "canonical_dispatch_included",
            "status": "not_measured",
            "reason": "Run the guarded RR-09.2 isolated PostgreSQL memory benchmark command.",
            "trial_count": 0,
            "error_count": 0,
            "input_payload_bytes": None,
            "output_payload_bytes": None,
            "record_count": int(name.rsplit("_", maxsplit=1)[-1]),
            "projection_changes_result": False,
            "tracing_configuration": dict(EPIC6_MEMORY_TRACING_CONFIGURATION),
            "trials": [],
            "scenario_metadata": {
                "measurement_boundary": "canonical_dispatch_included",
                "full_execution_boundary": True,
            },
        }
        for name in EPIC6_MEMORY_CANONICAL_SCENARIO_NAMES
    ]


def build_epic6_memory_decision() -> dict[str, object]:
    """Declare the bounded RR-09.2 evidence decision without proposing an optimization.

    Returns:
        Explicit conclusion, limitations, and RR-09.3 gate state.
    """
    return {
        "classification": "A_NO_MEANINGFUL_REGISTRY_OWNED_MEMORY_PROBLEM",
        "candidate_implementation_measured": False,
        "optimization_savings_inferred": False,
        "leak_claim": "not_made",
        "rationale": (
            "The benchmark distinguishes temporary Python-traced allocations from signed "
            "post-GC retention deltas. Direct results are projection-only, while canonical "
            "results are inclusive dispatch measurements and are not attributed to projection."
        ),
        "rr_09_3": "BLOCKED_NO_MEASURED_CANDIDATE_IMPLEMENTATION",
    }


def render_epic6_memory_report(
    metadata: Mapping[str, object],
    results: Sequence[Mapping[str, object]],
) -> str:
    """Render the RR-09.2 report without presenting allocation observations as latency data.

    Args:
        metadata: Allowlisted reproducibility and measurement configuration metadata.
        results: Sanitized direct, repeated, concurrent, and canonical memory records.

    Returns:
        Markdown report with memory units, scopes, limits, and the bounded decision.
    """
    lines = [
        "# Resource Registry Epic 6 grouped-result memory profiling",
        "",
        "## Scope and methodology",
        "",
        "- RR-09.2 executes the unmodified `project_registry_tool_result` production function.",
        "- Input fixtures are created before tracing. Current and peak readings include the returned "
        "output string; inputs are not allocated inside the tracing boundary.",
        "- `tracemalloc` values are Python allocation observations in bytes. They are not RSS, "
        "latency, SQL, or per-request memory measurements.",
        "- Post-GC retained deltas are signed. A nonzero delta can reflect allocator or cache lifetime "
        "behavior and is not called a memory leak by this benchmark.",
        "- Concurrent `tracemalloc` and RSS readings are process-wide. Peak values are never divided "
        "by workers or attributed to a request. The GIL and local scheduling limit CPU-parallelism "
        "interpretation.",
        "- Canonical values include real guarded dispatch, authorization, and PostgreSQL work; they are "
        "not attributed exclusively to grouped-result projection.",
        "- No latency timing, SQL instrumentation, or RR-09.1 timing comparison runs within this "
        "memory profile.",
        "",
        "## Reproducibility",
        "",
        f"- Git revision: {metadata.get('git_revision', 'unavailable')}",
        f"- Python: {metadata.get('python_version', 'unavailable')}",
        f"- Operating system: {metadata.get('operating_system', 'unavailable')}",
        f"- Trace configuration: `{metadata.get('tracing_configuration', {})}`",
        f"- Trial count: {metadata.get('epic6_memory_trial_count', 'unavailable')}",
        "",
        "## Single-operation direct projection memory",
        "",
        "| Scenario | Status | Input bytes | Output bytes | Peak bytes (trial 1) | Current bytes (trial 1) | Post-GC delta bytes (trial 1) | Parity |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for result in results:
        if result.get("category") != "direct_projection_memory":
            continue
        trial = _get_first_trial(result)
        lines.append(
            "| {name} | {status} | {input_bytes} | {output_bytes} | {peak} | {current} | {retained} | {parity} |".format(
                name=result.get("name", "unavailable"),
                status=result.get("status", "unavailable"),
                input_bytes=result.get("input_payload_bytes", "unavailable"),
                output_bytes=result.get("output_payload_bytes", "unavailable"),
                peak=trial.get("traced_peak_bytes", "unavailable"),
                current=trial.get("traced_current_after_execution_bytes", "unavailable"),
                retained=trial.get("post_gc_retained_delta_bytes", "unavailable"),
                parity=trial.get("output_parity", "unavailable"),
            )
        )
    lines.extend(
        [
            "",
            "## Repeated-operation retention",
            "",
            "Outputs are released after every projection; no list of outputs is retained.",
            "",
            "| Scenario | Iterations | Status | Initial delta | Intermediate delta | Final delta | Post-GC delta | Peak bytes |",
            "| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for result in results:
        if result.get("category") != "repeated_projection_retention":
            continue
        trial = _get_first_trial(result)
        lines.append(
            "| {name} | {iterations} | {status} | {initial} | {intermediate} | {final} | {post_gc} | {peak} |".format(
                name=result.get("name", "unavailable"),
                iterations=result.get("iterations_per_trial", "unavailable"),
                status=result.get("status", "unavailable"),
                initial=trial.get("initial_retained_delta_bytes", "unavailable"),
                intermediate=trial.get("intermediate_retained_delta_bytes", "unavailable"),
                final=trial.get("final_retained_delta_bytes", "unavailable"),
                post_gc=trial.get("post_gc_retained_delta_bytes", "unavailable"),
                peak=trial.get("traced_peak_bytes", "unavailable"),
            )
        )
    lines.extend(
        [
            "",
            "## Concurrent projection memory",
            "",
            "| Scenario | Status | Peak in-flight (trial 1) | Completed | Errors | Peak traced bytes (trial 1) | Post-GC delta (trial 1) | RSS |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for result in results:
        if result.get("category") != "concurrent_projection_memory":
            continue
        trial = _get_first_trial(result)
        process_rss = trial.get("process_rss_after_execution")
        rss_status = (
            process_rss.get("status", "unavailable")
            if isinstance(process_rss, Mapping)
            else "unavailable"
        )
        lines.append(
            "| {name} | {status} | {inflight} | {completed} | {errors} | {peak} | {retained} | {rss} |".format(
                name=result.get("name", "unavailable"),
                status=result.get("status", "unavailable"),
                inflight=trial.get("actual_peak_in_flight_operations", "unavailable"),
                completed=result.get("completed_operations", "unavailable"),
                errors=result.get("unexpected_error_count", "unavailable"),
                peak=trial.get("traced_peak_bytes", "unavailable"),
                retained=trial.get("post_gc_retained_delta_bytes", "unavailable"),
                rss=rss_status,
            )
        )
    lines.extend(
        [
            "",
            "## Guarded canonical execution memory",
            "",
            "| Scenario | Status | Output bytes | Peak bytes (trial 1) | Current bytes (trial 1) | Post-GC delta bytes (trial 1) | Boundary |",
            "| --- | --- | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for result in results:
        if result.get("category") != "canonical_execution_memory":
            continue
        trial = _get_first_trial(result)
        lines.append(
            "| {name} | {status} | {output_bytes} | {peak} | {current} | {retained} | {boundary} |".format(
                name=result.get("name", "unavailable"),
                status=result.get("status", "unavailable"),
                output_bytes=result.get("output_payload_bytes", "unavailable"),
                peak=trial.get("traced_peak_bytes", "unavailable"),
                current=trial.get("traced_current_after_execution_bytes", "unavailable"),
                retained=trial.get("post_gc_retained_delta_bytes", "unavailable"),
                boundary=result.get("execution_boundary", "unavailable"),
            )
        )
    decision = metadata.get("epic6_memory_decision")
    lines.extend(["", "## Decision analysis", ""])
    if isinstance(decision, Mapping):
        lines.extend(
            [
                f"- Classification: `{decision.get('classification', 'unavailable')}`",
                f"- Leak claim: `{decision.get('leak_claim', 'unavailable')}`",
                f"- Optimization savings inferred: `{decision.get('optimization_savings_inferred', 'unavailable')}`",
                f"- RR-09.3: `{decision.get('rr_09_3', 'unavailable')}`",
                f"- Rationale: {decision.get('rationale', 'unavailable')}",
            ]
        )
    return "\n".join(lines) + "\n"


def _get_serialized_string_bytes(result: object) -> int | None:
    """Return UTF-8 bytes for a returned string without serializing non-string values.

    Args:
        result: Result produced by one benchmark operation.

    Returns:
        UTF-8 byte count for strings, otherwise ``None``.
    """
    return len(result.encode("utf-8")) if isinstance(result, str) else None


def _stabilize_epic6_fixture(fixture: ProjectionFixture) -> None:
    """Warm one fixture without tracing and assert its existing exact behavior.

    Args:
        fixture: Prepared deterministic source and expected result contract.

    Raises:
        RuntimeError: If an unmodified production projection violates its existing contract.
    """
    for _ in range(EPIC6_MEMORY_STABILIZATION_ITERATIONS):
        if project_registry_tool_result(fixture.payload) != fixture.expected_result:
            raise RuntimeError("RR-09.2 stabilization projection parity failed.")
    gc.collect()


def _measure_repeated_projection_trial(
    fixture: ProjectionFixture,
    *,
    iterations: int,
    trial_number: int,
) -> MemoryTrial:
    """Measure one repeated projection trial without retaining its results.

    Args:
        fixture: Prepared deterministic source and expected result contract.
        iterations: Sequential calls to make in the trial.
        trial_number: Stable one-based independent trial number.

    Returns:
        Checkpoint deltas and parity/error counts for the completed trial.
    """
    if tracemalloc.is_tracing():
        return {
            "trial": trial_number,
            "status": "not_measured",
            "reason": "tracemalloc_was_already_active",
            "completed_operations": 0,
            "error_count": 0,
        }
    gc.collect()
    tracemalloc.start(EPIC6_MEMORY_TRACEBACK_FRAME_LIMIT)
    try:
        tracemalloc.clear_traces()
        baseline_current_bytes, _ = tracemalloc.get_traced_memory()
        intermediate_after_operations = iterations // 2
        intermediate_current_bytes = baseline_current_bytes
        completed_operations = 0
        error_count = 0
        for operation_number in range(1, iterations + 1):
            try:
                result = project_registry_tool_result(fixture.payload)
            except Exception:
                error_count += 1
                break
            if result != fixture.expected_result:
                error_count += 1
                del result
                break
            del result
            completed_operations += 1
            if operation_number == intermediate_after_operations:
                intermediate_current_bytes, _ = tracemalloc.get_traced_memory()
        final_current_bytes, peak_bytes = tracemalloc.get_traced_memory()
        gc.collect()
        post_gc_current_bytes, _ = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return {
        "trial": trial_number,
        "status": "measured" if error_count == 0 else "failed",
        "reason": None if error_count == 0 else "projection_or_parity_failure",
        "completed_operations": completed_operations,
        "error_count": error_count,
        "initial_retained_delta_bytes": 0,
        "intermediate_retained_delta_bytes": intermediate_current_bytes - baseline_current_bytes,
        "final_retained_delta_bytes": final_current_bytes - baseline_current_bytes,
        "post_gc_retained_delta_bytes": post_gc_current_bytes - baseline_current_bytes,
        "traced_peak_bytes": peak_bytes,
        "output_retention": "none",
    }


def _measure_concurrent_projection_trial(
    fixture: ProjectionFixture,
    *,
    workers: int,
    trial_number: int,
) -> MemoryTrial:
    """Measure one synchronized concurrent operation-lifetime wave.

    Args:
        fixture: Prepared deterministic source and expected result contract.
        workers: Number of registered concurrent workers in the wave.
        trial_number: Stable one-based independent trial number.

    Returns:
        Process-wide traced/RSS observations and actual in-flight overlap counts.
    """
    if tracemalloc.is_tracing():
        return {
            "trial": trial_number,
            "status": "not_measured",
            "reason": "tracemalloc_was_already_active",
            "completed_operations": 0,
            "unexpected_error_count": 0,
        }
    start_barrier = Barrier(workers)
    outputs_ready = Event()
    release_outputs = Event()
    state_lock = Lock()
    in_flight_operations = 0
    peak_in_flight_operations = 0
    completed_operations = 0
    unexpected_error_count = 0
    output_parity_failures = 0

    def run_projection_operation() -> bool:
        """Execute one projection while holding its output through the synchronized boundary.

        Returns:
            Whether the production result exactly matched the prepared expected output.
        """
        nonlocal completed_operations
        nonlocal in_flight_operations
        nonlocal peak_in_flight_operations
        nonlocal unexpected_error_count
        nonlocal output_parity_failures
        entered_operation = False
        result: object | None = None
        parity = False
        try:
            start_barrier.wait(timeout=10)
            with state_lock:
                in_flight_operations += 1
                entered_operation = True
                peak_in_flight_operations = max(peak_in_flight_operations, in_flight_operations)
            result = project_registry_tool_result(fixture.payload)
            parity = result == fixture.expected_result
            if not parity:
                with state_lock:
                    output_parity_failures += 1
        except Exception:
            with state_lock:
                unexpected_error_count += 1
        finally:
            with state_lock:
                completed_operations += 1
                if completed_operations == workers:
                    outputs_ready.set()
            release_outputs.wait(timeout=10)
            del result
            if entered_operation:
                with state_lock:
                    in_flight_operations -= 1
        return parity

    gc.collect()
    tracemalloc.start(EPIC6_MEMORY_TRACEBACK_FRAME_LIMIT)
    try:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            tracemalloc.clear_traces()
            baseline_current_bytes, _ = tracemalloc.get_traced_memory()
            futures = [executor.submit(run_projection_operation) for _ in range(workers)]
            if not outputs_ready.wait(timeout=10):
                with state_lock:
                    unexpected_error_count += workers
            current_after_execution_bytes, peak_bytes = tracemalloc.get_traced_memory()
            process_rss_after_execution = _read_process_rss_bytes()
            release_outputs.set()
            for future in futures:
                try:
                    future.result(timeout=10)
                except Exception:
                    with state_lock:
                        unexpected_error_count += 1
            del future
            del futures
        del executor
        gc.collect()
        post_gc_current_bytes, _ = tracemalloc.get_traced_memory()
        process_rss_after_gc = _read_process_rss_bytes()
    finally:
        release_outputs.set()
        tracemalloc.stop()
    return {
        "trial": trial_number,
        "status": (
            "measured" if unexpected_error_count == 0 and output_parity_failures == 0 else "failed"
        ),
        "reason": (
            None
            if unexpected_error_count == 0 and output_parity_failures == 0
            else "concurrent_projection_or_parity_failure"
        ),
        "completed_operations": completed_operations,
        "unexpected_error_count": unexpected_error_count,
        "output_parity_failures": output_parity_failures,
        "actual_peak_in_flight_operations": peak_in_flight_operations,
        "traced_baseline_current_bytes": baseline_current_bytes,
        "traced_current_after_execution_bytes": current_after_execution_bytes,
        "traced_peak_bytes": peak_bytes,
        "post_gc_current_bytes": post_gc_current_bytes,
        "post_gc_retained_delta_bytes": post_gc_current_bytes - baseline_current_bytes,
        "temporary_peak_amplification_bytes": max(0, peak_bytes - baseline_current_bytes),
        "process_rss_after_execution": process_rss_after_execution,
        "process_rss_after_gc": process_rss_after_gc,
        "output_included_during_measurement": True,
    }


def _read_process_rss_bytes() -> dict[str, object]:
    """Read Linux current RSS only when the operating-system source is reliable.

    Returns:
        A measured current RSS value on Linux ``/proc`` systems, otherwise an explicit
        ``not_measured`` record rather than a platform-dependent estimate.
    """
    statm_path = Path("/proc/self/statm")
    if not sys.platform.startswith("linux") or not statm_path.is_file():
        return {
            "status": "not_measured",
            "reason": "no_portable_current_rss_source_on_this_platform",
            "bytes": None,
        }
    try:
        resident_pages = int(statm_path.read_text().split()[1])
        page_size = os.sysconf("SC_PAGE_SIZE")
    except (IndexError, OSError, ValueError):
        return {
            "status": "not_measured",
            "reason": "linux_proc_statm_unavailable",
            "bytes": None,
        }
    return {
        "status": "measured",
        "source": "linux_proc_self_statm_resident_pages",
        "bytes": resident_pages * page_size,
    }


def _build_memory_record(
    *,
    name: str,
    category: str,
    kind: str,
    execution_boundary: str,
    input_payload_bytes: int | None,
    output_payload_bytes: int | None,
    record_count: int,
    projection_changes_result: bool,
    trials: Sequence[MemoryTrial],
    scenario_metadata: Mapping[str, object],
) -> MemoryRecord:
    """Build one sanitized memory record from independent trial details.

    Args:
        name: Stable scenario identity.
        category: Stable memory report section.
        kind: Measurement implementation identity.
        execution_boundary: Declared production boundary included by the scenario.
        input_payload_bytes: Actual prepared UTF-8 input size when applicable.
        output_payload_bytes: Actual expected UTF-8 output size when applicable.
        record_count: Logical record count represented by the bounded scenario.
        projection_changes_result: Whether production projection should rewrite the input string.
        trials: Independent memory measurements.
        scenario_metadata: Fixed non-sensitive scenario detail.

    Returns:
        One JSON-safe result record with status/error summaries and signed trial values.
    """
    error_count = sum(1 for trial in trials if trial.get("status") != "measured")
    return {
        "name": name,
        "category": category,
        "kind": kind,
        "execution_boundary": execution_boundary,
        "status": "measured" if error_count == 0 else "failed",
        "reason": None if error_count == 0 else "one_or_more_trials_not_measured_or_failed",
        "trial_count": len(trials),
        "error_count": error_count,
        "input_payload_bytes": input_payload_bytes,
        "output_payload_bytes": output_payload_bytes,
        "record_count": record_count,
        "projection_changes_result": projection_changes_result,
        "tracing_configuration": dict(EPIC6_MEMORY_TRACING_CONFIGURATION),
        "trials": [dict(trial) for trial in trials],
        "scenario_metadata": dict(scenario_metadata),
    }


def _get_retention_scenario_name(fixture: ProjectionFixture, *, iterations: int) -> str:
    """Return the stable retention scenario identifier used by the artifact contract.

    Args:
        fixture: Prepared direct-projection fixture.
        iterations: Registered repeat count.

    Returns:
        Stable RR-09.2 repeated-retention result name.
    """
    return (
        "grouped_result.memory.retention."
        f"{fixture.name.rsplit('.', maxsplit=1)[-1]}.iterations_{iterations:04d}"
    )


def _get_first_trial(result: Mapping[str, object]) -> Mapping[str, object]:
    """Return the first trial mapping for concise report tables.

    Args:
        result: One sanitized artifact result record.

    Returns:
        First trial mapping, or an empty mapping when no trial was recorded.
    """
    trials = result.get("trials")
    if not isinstance(trials, Sequence) or isinstance(trials, str | bytes) or not trials:
        return {}
    first_trial = trials[0]
    return first_trial if isinstance(first_trial, Mapping) else {}


def _get_trial_integer(trial: Mapping[str, object], field_name: str) -> int:
    """Return one non-boolean integer field from a sanitized memory trial.

    Args:
        trial: One JSON-safe memory trial record.
        field_name: Integer field to read from that record.

    Returns:
        The recorded integer, or zero when the field is unavailable or malformed.
    """
    value = trial.get(field_name)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
