"""Summarize sanitized benchmark samples and render a comparison baseline."""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from typing import cast


def summarize_latency_ns(samples_ns: Sequence[int]) -> dict[str, int | float | bool | None] | None:
    """Summarize successful wall-clock samples using nearest-rank percentiles.

    Args:
        samples_ns: Measured iterations only, in monotonic nanoseconds.

    Returns:
        Millisecond distribution or None when no successful sample exists.
    """
    if not samples_ns:
        return None
    ordered_ms = sorted(sample / 1_000_000 for sample in samples_ns)
    count = len(ordered_ms)

    def nearest_rank(percent: int) -> float:
        return ordered_ms[max(0, math.ceil(count * percent / 100) - 1)]

    return {
        "sample_count": count,
        "p50_ms": nearest_rank(50),
        "p95_ms": nearest_rank(95),
        "p99_ms": nearest_rank(99),
        "mean_ms": statistics.fmean(ordered_ms),
        "min_ms": ordered_ms[0],
        "max_ms": ordered_ms[-1],
        "stddev_ms": statistics.stdev(ordered_ms) if count > 1 else None,
        "tail_percentiles_unstable": count < 100,
    }


def _format_number(value: object, decimals: int = 3) -> str:
    """Display a measured number or an explicit unavailable marker.

    Args:
        value: Numeric aggregate or missing value.
        decimals: Decimal places to display.

    Returns:
        Human-readable number or ``unavailable``.
    """
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "unavailable"
    return f"{value:.{decimals}f}"


def _render_category(category: str, results: Sequence[Mapping[str, object]]) -> str:
    """Render one result group without exposing workload inputs.

    Args:
        category: Fixed benchmark category.
        results: Sanitized result records.

    Returns:
        Markdown table with wall and Registry-local metrics.
    """
    lines = [
        f"### {category.replace('_', ' ').title()}",
        "",
        "| Scenario | Kind | Status | n / errors | p50 / p95 / p99 (ms) | tail | "
        "SQL count / duration (ms) | Auth contexts / operation | Execution boundary | "
        "Result bytes / actions |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for result in results:
        if result.get("category") != category:
            continue
        latency = result.get("latency")
        distribution = latency if isinstance(latency, Mapping) else {}
        stages = result.get("stages")
        stage_records = cast(Mapping[str, object], stages) if isinstance(stages, Mapping) else {}
        raw_database_sql = result.get("database_sql")
        database_sql = raw_database_sql if isinstance(raw_database_sql, Mapping) else {}
        primary_stage = {
            "publication": "registry_tool_publication",
            "schema": "grouped_schema_build",
            "validation": "grouped_schema_validation",
            "selection": "selection_prompt_context",
            "execution": "grouped_call_total",
            "routing": "deterministic_routing",
        }.get(category, "")
        metric = stage_records.get(primary_stage)
        stage = metric if isinstance(metric, Mapping) else {}
        sql = (
            f"{_format_number(database_sql.get('mean_query_count'), 1)} / "
            f"{_format_number(database_sql.get('mean_duration_ms'))}"
            if database_sql.get("measurement_scope") == "whole_operation"
            else (
                f"{_format_number(stage.get('mean_db_query_count'), 1)} / "
                f"{_format_number(stage.get('mean_db_duration_ms'))}"
                if stage
                else "unavailable"
            )
        )
        publication_payload = (
            f"{_format_number(stage.get('payload_bytes'), 0)} / "
            f"{_format_number(stage.get('action_count'), 0)}"
            if category == "publication" and stage
            else "unavailable"
        )
        result_size = result.get("result_payload_bytes")
        result_size_values = result_size if isinstance(result_size, Mapping) else {}
        payload = (
            f"{_format_number(result_size_values.get('mean'), 0)} / unavailable"
            if result_size_values
            else publication_payload
        )
        tails = " / ".join(
            _format_number(distribution.get(key)) for key in ("p50_ms", "p95_ms", "p99_ms")
        )
        tail_note = "unstable" if distribution.get("tail_percentiles_unstable") else "supported"
        if not distribution:
            tail_note = "unavailable"
        authorization_context_builds = _format_number(
            result.get("authorization_context_build_count_per_operation"),
            1,
        )
        execution_boundary = str(result.get("execution_boundary", "unavailable"))
        lines.append(
            f"| {result['name']} | {result['kind']} | {result['status']} | "
            f"{result['sample_count']} / {result['error_count']} | {tails} | "
            f"{tail_note} | {sql} | {authorization_context_builds} | {execution_boundary} | "
            f"{payload} |"
        )
        if result.get("reason"):
            lines.append(f"\n{result['name']}: {result['reason']}")
    return "\n".join(lines)


def _render_adapter_trial_consistency(metadata: Mapping[str, object]) -> list[str]:
    """Render the three-trial adapter summary when database benchmark evidence is present.

    Args:
        metadata: Sanitized baseline metadata that may include nested adapter trial summaries.

    Returns:
        Markdown lines, or an explicit unavailable statement when no trial evidence exists.
    """
    raw_trials = metadata.get("adapter_routing_trials")
    if not isinstance(raw_trials, list):
        return [
            "### Adapter routing trial consistency",
            "",
            "Adapter routing trials were not measured in this baseline.",
            "",
        ]
    scenario_trials: dict[str, list[Mapping[str, object]]] = {}
    for trial in raw_trials:
        if not isinstance(trial, Mapping):
            continue
        trial_results = trial.get("results")
        if not isinstance(trial_results, list):
            continue
        for result in trial_results:
            if isinstance(result, Mapping):
                scenario_trials.setdefault(str(result.get("name")), []).append(result)
    lines = [
        "### Adapter routing trial consistency",
        "",
        f"Method: {metadata.get('adapter_routing_trial_method', 'unavailable')}",
        "",
        "| Scenario | trial p50 ms (1 / 2 / 3) | deterministic SQL count (1 / 2 / 3) | "
        "auth contexts / operation (1 / 2 / 3) | n / errors per trial |",
        "| --- | --- | --- | --- | --- |",
    ]
    for scenario_name in sorted(scenario_trials):
        records = scenario_trials[scenario_name]
        latency_values: list[str] = []
        sql_values: list[str] = []
        authorization_values: list[str] = []
        sample_values: list[str] = []
        for record in records:
            raw_latency = record.get("latency")
            latency = raw_latency if isinstance(raw_latency, Mapping) else {}
            stages = record.get("stages")
            stage_records = stages if isinstance(stages, Mapping) else {}
            raw_stage = stage_records.get("deterministic_routing")
            stage = raw_stage if isinstance(raw_stage, Mapping) else {}
            latency_values.append(_format_number(latency.get("p50_ms")))
            sql_values.append(_format_number(stage.get("mean_db_query_count"), 1))
            authorization_values.append(
                _format_number(record.get("authorization_context_build_count_per_operation"), 1)
            )
            sample_values.append(
                f"{_format_number(record.get('sample_count'), 0)} / "
                f"{_format_number(record.get('error_count'), 0)}"
            )
        lines.append(
            f"| {scenario_name} | {' / '.join(latency_values)} | {' / '.join(sql_values)} | "
            f"{' / '.join(authorization_values)} | {' / '.join(sample_values)} |"
        )
    return [*lines, ""]


def _render_grouped_execution_trial_consistency(metadata: Mapping[str, object]) -> list[str]:
    """Render RR-03.1 trial evidence without conflating inclusive stages.

    Args:
        metadata: Sanitized baseline metadata that may contain grouped trial results.

    Returns:
        Markdown lines for the three independent grouped-execution trials.
    """
    raw_trials = metadata.get("grouped_execution_trials")
    if not isinstance(raw_trials, list):
        return []
    by_name: dict[str, list[Mapping[str, object]]] = {}
    for trial in raw_trials:
        if not isinstance(trial, Mapping) or not isinstance(trial.get("results"), list):
            continue
        for result in cast(list[object], trial["results"]):
            if isinstance(result, Mapping):
                by_name.setdefault(str(result.get("name")), []).append(result)
    lines = [
        "### Grouped execution trial consistency",
        "",
        f"Method: {metadata.get('grouped_execution_trial_method', 'unavailable')}",
        "",
        "| Scenario | trial p50 ms (1 / 2 / 3) | grouped bridge ms (1 / 2 / 3) | "
        "canonical-inclusive ms (1 / 2 / 3) | SQL count (1 / 2 / 3) | n / errors |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for scenario_name in sorted(by_name):
        values = by_name[scenario_name]
        p50s: list[str] = []
        bridges: list[str] = []
        totals: list[str] = []
        sql_counts: list[str] = []
        samples: list[str] = []
        for value in values:
            latency = value.get("latency")
            latency_values = latency if isinstance(latency, Mapping) else {}
            stages = value.get("stages")
            stage_values = stages if isinstance(stages, Mapping) else {}
            bridge = stage_values.get("grouped_dispatch_bridge")
            bridge_values = bridge if isinstance(bridge, Mapping) else {}
            total = stage_values.get("grouped_call_total")
            total_values = total if isinstance(total, Mapping) else {}
            p50s.append(_format_number(latency_values.get("p50_ms")))
            bridges.append(_format_number(bridge_values.get("mean_duration_ms")))
            totals.append(_format_number(total_values.get("mean_duration_ms")))
            sql_counts.append(_format_number(total_values.get("mean_db_query_count"), 1))
            samples.append(
                f"{_format_number(value.get('sample_count'), 0)} / "
                f"{_format_number(value.get('error_count'), 0)}"
            )
        lines.append(
            f"| {scenario_name} | {' / '.join(p50s)} | {' / '.join(bridges)} | "
            f"{' / '.join(totals)} | {' / '.join(sql_counts)} | {' / '.join(samples)} |"
        )
    return [*lines, ""]


def _render_epic4_catalog_trial_consistency(metadata: Mapping[str, object]) -> list[str]:
    """Render RR-04.1 catalog trial evidence, including denied-path outcomes.

    Args:
        metadata: Sanitized artifact metadata that may contain RR-04.1 trial records.

    Returns:
        Markdown lines for three isolated catalog trials, if available.
    """
    raw_trials = metadata.get("epic4_catalog_trials")
    if not isinstance(raw_trials, list):
        return []
    by_name: dict[str, list[Mapping[str, object]]] = {}
    for trial in raw_trials:
        if not isinstance(trial, Mapping) or not isinstance(trial.get("results"), list):
            continue
        for result in cast(list[object], trial["results"]):
            if isinstance(result, Mapping):
                by_name.setdefault(str(result.get("name")), []).append(result)
    lines = [
        "### Catalog trial consistency",
        "",
        f"Method: {metadata.get('epic4_catalog_trial_method', 'unavailable')}",
        "",
        "| Scenario | trial p50 ms (1 / 2 / 3) | publication SQL count (1 / 2 / 3) | "
        "success / rejected / failed (1 / 2 / 3) | n / unexpected errors (1 / 2 / 3) |",
        "| --- | --- | --- | --- | --- |",
    ]
    for scenario_name in sorted(by_name):
        values = by_name[scenario_name]
        p50s: list[str] = []
        sql_counts: list[str] = []
        outcomes: list[str] = []
        samples: list[str] = []
        for value in values:
            latency = value.get("latency")
            latency_values = latency if isinstance(latency, Mapping) else {}
            stages = value.get("stages")
            stage_values = stages if isinstance(stages, Mapping) else {}
            publication_stage = stage_values.get("registry_tool_publication")
            publication_values = publication_stage if isinstance(publication_stage, Mapping) else {}
            raw_outcomes = value.get("outcomes")
            outcome_values = raw_outcomes if isinstance(raw_outcomes, Mapping) else {}
            p50s.append(_format_number(latency_values.get("p50_ms")))
            sql_counts.append(_format_number(publication_values.get("mean_db_query_count"), 1))
            outcomes.append(
                " / ".join(
                    _format_number(outcome_values.get(name, 0), 0)
                    for name in ("success", "rejected", "failed")
                )
            )
            samples.append(
                f"{_format_number(value.get('sample_count'), 0)} / "
                f"{_format_number(value.get('error_count'), 0)}"
            )
        lines.append(
            f"| {scenario_name} | {' / '.join(p50s)} | {' / '.join(sql_counts)} | "
            f"{' / '.join(outcomes)} | {' / '.join(samples)} |"
        )
    return [*lines, ""]


def _render_epic5_selection_trial_consistency(metadata: Mapping[str, object]) -> list[str]:
    """Render RR-05.1 trial variation without attributing root SQL to internal stages.

    Args:
        metadata: Sanitized baseline metadata that may contain selection trial results.

    Returns:
        Markdown lines for independent selection trials, or an explicit unavailable statement.
    """
    raw_trials = metadata.get("epic5_selection_trials")
    if not isinstance(raw_trials, list):
        return [
            "### Selection trial consistency",
            "",
            "Selection trials were not measured in this baseline.",
            "",
        ]
    by_name: dict[str, list[Mapping[str, object]]] = {}
    for trial in raw_trials:
        if not isinstance(trial, Mapping) or not isinstance(trial.get("results"), list):
            continue
        for result in cast(list[object], trial["results"]):
            if isinstance(result, Mapping):
                by_name.setdefault(str(result.get("name")), []).append(result)
    lines = [
        "### Selection trial consistency",
        "",
        f"Method: {metadata.get('epic5_selection_trial_method', 'unavailable')}",
        "",
        "Whole-operation SQL is measured by a benchmark-local Django `execute_wrapper`; it is "
        "not attribution to authorization, namespace admission, visibility, version validation, "
        "or context assembly. Inclusive Registry stages, where present, remain separate.",
        "",
        "| Scenario | trial p50 / p95 / p99 ms (1 / 2 / 3) | whole-operation SQL count / "
        "duration ms (1 / 2 / 3) | n / errors per trial |",
        "| --- | --- | --- | --- |",
    ]
    for scenario_name in sorted(by_name):
        latency_values: list[str] = []
        sql_values: list[str] = []
        sample_values: list[str] = []
        for result in by_name[scenario_name]:
            raw_latency = result.get("latency")
            latency = raw_latency if isinstance(raw_latency, Mapping) else {}
            raw_database_sql = result.get("database_sql")
            database_sql = raw_database_sql if isinstance(raw_database_sql, Mapping) else {}
            latency_values.append(
                " / ".join(
                    _format_number(latency.get(key)) for key in ("p50_ms", "p95_ms", "p99_ms")
                )
            )
            sql_values.append(
                f"{_format_number(database_sql.get('mean_query_count'), 1)} / "
                f"{_format_number(database_sql.get('mean_duration_ms'))}"
            )
            sample_values.append(
                f"{_format_number(result.get('sample_count'), 0)} / "
                f"{_format_number(result.get('error_count'), 0)}"
            )
        lines.append(
            f"| {scenario_name} | {'; '.join(latency_values)} | "
            f"{'; '.join(sql_values)} | {' / '.join(sample_values)} |"
        )
    return [*lines, ""]


def _render_epic5_candidate_comparison(metadata: Mapping[str, object]) -> list[str]:
    """Render RR-05.2 equivalent-scenario comparison metrics.

    Args:
        metadata: Candidate artifact metadata with frozen-baseline comparison rows.

    Returns:
        Markdown comparison rows, or no rows when this is not an RR-05.2 candidate artifact.
    """
    raw_comparisons = metadata.get("epic5_selection_baseline_comparison")
    if not isinstance(raw_comparisons, list):
        return []
    lines = [
        "### RR-05.2 frozen-baseline comparison",
        "",
        "Equivalent Registry pre-dispatch boundaries only; this is not an end-to-end `/chat` claim.",
        "",
        "| Scenario | p50 ms (before → after) | p95 ms (before → after) | "
        "p99 ms (before → after) | SQL count (before → after) | "
        "SQL duration ms (before → after) | payload bytes (before → after) | errors |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for raw_comparison in raw_comparisons:
        if not isinstance(raw_comparison, Mapping):
            continue
        before = raw_comparison.get("before")
        after = raw_comparison.get("after")
        if not isinstance(before, Mapping) or not isinstance(after, Mapping):
            continue

        def format_pair(
            metric_name: str,
            decimals: int = 3,
            before_values: Mapping[object, object] = before,
            after_values: Mapping[object, object] = after,
        ) -> str:
            return (
                f"{_format_number(before_values.get(metric_name), decimals)} → "
                f"{_format_number(after_values.get(metric_name), decimals)}"
            )

        lines.append(
            f"| {raw_comparison.get('name', 'unavailable')} | {format_pair('p50_ms')} | "
            f"{format_pair('p95_ms')} | {format_pair('p99_ms')} | "
            f"{format_pair('sql_query_count', 1)} | {format_pair('sql_duration_ms')} | "
            f"{format_pair('payload_bytes', 0)} | "
            f"{_format_number(before.get('error_count'), 0)} → "
            f"{_format_number(after.get('error_count'), 0)} |"
        )
    return [*lines, ""]


def _render_follow_on_story_recommendations(metadata: Mapping[str, object]) -> list[str]:
    """Render the approved RR-03 follow-on mapping from artifact metadata.

    Args:
        metadata: Sanitized artifact metadata with optional story recommendations.

    Returns:
        Markdown lines for valid follow-on recommendations, if present.
    """
    raw_recommendations = metadata.get("follow_on_story_recommendations")
    if not isinstance(raw_recommendations, list):
        return []
    lines = ["### Follow-on story recommendations", ""]
    for recommendation in raw_recommendations:
        if not isinstance(recommendation, Mapping):
            continue
        story_id = recommendation.get("story_id")
        story_name = recommendation.get("story_name")
        decision = recommendation.get("recommendation")
        rationale = recommendation.get("rationale")
        if not all(isinstance(value, str) and value for value in (story_id, story_name, decision)):
            continue
        explanation = f" {rationale}" if isinstance(rationale, str) and rationale else ""
        lines.append(f"- {story_id} — {story_name}: **{decision}**.{explanation}")
    return [*lines, ""] if len(lines) > 2 else []


def _render_pre_optimization_adapter_comparison(metadata: Mapping[str, object]) -> list[str]:
    """Render factual E2.1 before/after measurements when candidate evidence exists.

    Args:
        metadata: Sanitized metadata that may contain adapter comparison measurements.

    Returns:
        Markdown comparison lines, or no lines for non-candidate reports.
    """
    comparisons = metadata.get("pre_optimization_adapter_comparison")
    if not isinstance(comparisons, list):
        return []
    lines = [
        "### Pre-E2.1 adapter comparison",
        "",
        "Reference: accepted `baseline_results.json`. SQL values are the inclusive "
        "`deterministic_routing` stage only and are not summed with nested stages.",
        "",
        "| Scenario | p50 ms (before → after / Δ%) | p95 ms (before → after / Δ%) | "
        "p99 ms (before → after / Δ%) | SQL count (before → after / Δ%) | "
        "SQL duration ms (before → after / Δ%) | auth builds (before → after / Δ%) | "
        "errors (before → after) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for comparison in comparisons:
        if not isinstance(comparison, Mapping):
            continue
        before = comparison.get("before")
        after = comparison.get("after")
        percentage_difference = comparison.get("percentage_difference")
        if not all(
            isinstance(values, Mapping) for values in (before, after, percentage_difference)
        ):
            continue
        before_values = cast(Mapping[str, object], before)
        after_values = cast(Mapping[str, object], after)
        percentage_values = cast(Mapping[str, object], percentage_difference)

        def display_difference(
            metric_name: str,
            before_snapshot: Mapping[str, object] = before_values,
            after_snapshot: Mapping[str, object] = after_values,
            percentage_snapshot: Mapping[str, object] = percentage_values,
        ) -> str:
            return (
                f"{_format_number(before_snapshot.get(metric_name))} → "
                f"{_format_number(after_snapshot.get(metric_name))} / "
                f"{_format_number(percentage_snapshot.get(metric_name))}%"
            )

        lines.append(
            f"| {comparison.get('name', 'unavailable')} | {display_difference('p50_ms')} | "
            f"{display_difference('p95_ms')} | {display_difference('p99_ms')} | "
            f"{display_difference('sql_query_count')} | "
            f"{display_difference('sql_duration_ms')} | "
            f"{display_difference('authorization_context_build_count_per_operation')} | "
            f"{_format_number(before_values.get('error_count'), 0)} → "
            f"{_format_number(after_values.get('error_count'), 0)} |"
        )
    return [*lines, ""]


def _render_epic4_memory_metrics(results: Sequence[Mapping[str, object]]) -> list[str]:
    """Render profiling-only RR-04.1 allocation evidence apart from latency tables.

    Args:
        results: Sanitized benchmark records.

    Returns:
        Markdown rows for memory records, or an explicit unavailable note.
    """
    memory_results = [result for result in results if result.get("category") == "memory"]
    lines = [
        "### Allocation and retained memory (profiling-only)",
        "",
        "`tracemalloc` changes timing; these measurements are intentionally not latency data and "
        "do not establish the absence of memory leaks.",
        "",
        "| Scenario | Status | allocation bytes | retained bytes | current traced bytes | "
        "peak traced bytes | errors |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    if not memory_results:
        lines.append(
            "| unavailable | not_measured | unavailable | unavailable | unavailable | unavailable | 0 |"
        )
    for result in memory_results:
        raw_memory = result.get("memory")
        memory = raw_memory if isinstance(raw_memory, Mapping) else {}
        lines.append(
            f"| {result.get('name', 'unavailable')} | {result.get('status', 'unavailable')} | "
            f"{_format_number(memory.get('allocation_bytes'), 0)} | "
            f"{_format_number(memory.get('retained_bytes'), 0)} | "
            f"{_format_number(memory.get('current_traced_bytes'), 0)} | "
            f"{_format_number(memory.get('peak_traced_bytes'), 0)} | "
            f"{_format_number(result.get('error_count'), 0)} |"
        )
    return [*lines, ""]


def _render_epic4_process_metrics(results: Sequence[Mapping[str, object]]) -> list[str]:
    """Render RR-04.1 subprocess-worker evidence without conflating it with threads.

    Args:
        results: Sanitized benchmark records.

    Returns:
        Markdown rows for independent-process scenarios.
    """
    process_results = [
        result for result in results if result.get("category") == "independent_process"
    ]
    if not process_results:
        return []
    lines = [
        "### Independent process validation",
        "",
        "Each record starts fresh subprocess interpreters; it is not a thread benchmark.",
        "",
        "| Scenario | Status | p50 / p95 / p99 (ms) | process count | shared state | errors |",
        "| --- | --- | --- | ---: | --- | ---: |",
    ]
    for result in process_results:
        raw_latency = result.get("latency")
        latency = raw_latency if isinstance(raw_latency, Mapping) else {}
        raw_process = result.get("process")
        process = raw_process if isinstance(raw_process, Mapping) else {}
        tails = " / ".join(
            _format_number(latency.get(key)) for key in ("p50_ms", "p95_ms", "p99_ms")
        )
        lines.append(
            f"| {result.get('name', 'unavailable')} | {result.get('status', 'unavailable')} | "
            f"{tails} | {_format_number(process.get('process_count'), 0)} | "
            f"{process.get('state_shared_between_trials', 'unavailable')} | "
            f"{_format_number(result.get('error_count'), 0)} |"
        )
    return [*lines, ""]


def _render_epic4_optimization_gate(metadata: Mapping[str, object]) -> list[str]:
    """Render the RR-04.2 prerequisite without inventing a candidate comparison.

    Args:
        metadata: Sanitized artifact metadata with optional RR-04.1 gate evidence.

    Returns:
        Markdown decision-gate lines, or no lines for non-RR-04.1 reports.
    """
    raw_gate = metadata.get("rr04_optimization_gate")
    if not isinstance(raw_gate, Mapping):
        return []
    lines = [
        "### RR-04.2 optimization decision gate",
        "",
        "| Baseline p50 ms | Candidate p50 ms | Absolute reduction ms | Relative reduction % | "
        "Thresholds | Recommendation |",
        "| ---: | ---: | ---: | ---: | --- | --- |",
        "| "
        f"{_format_number(raw_gate.get('baseline_p50_ms'))} | "
        f"{_format_number(raw_gate.get('candidate_p50_ms'))} | "
        f"{_format_number(raw_gate.get('absolute_reduction_ms'))} | "
        f"{_format_number(raw_gate.get('relative_reduction_percent'))} | "
        f"≥ {_format_number(raw_gate.get('minimum_absolute_reduction_ms'))} ms and "
        f"≥ {_format_number(raw_gate.get('minimum_relative_reduction_percent'))}% | "
        f"{raw_gate.get('recommendation', 'DEFER')} |",
        "",
        str(raw_gate.get("rationale", "No RR-04.2 decision evidence was recorded.")),
        "",
    ]
    return lines


def _render_epic6_payload_matrix(results: Sequence[Mapping[str, object]]) -> list[str]:
    """Render RR-09.1 input size and projection-change metadata without payload content.

    Args:
        results: Sanitized direct, concurrent, and canonical RR-09.1 result records.

    Returns:
        Markdown rows that keep source bytes distinct from returned result bytes.
    """
    lines = [
        "### RR-09.1 payload and projection matrix",
        "",
        "Input bytes are the exact UTF-8 strings passed to the existing production projection "
        "function. Returned bytes and returned records are measured separately. Canonical records marked "
        "`not_measured` are fixture gaps, not zero-cost observations.",
        "",
        "| Scenario | Status | input bytes | returned bytes | returned records | rewritten | boundary |",
        "| --- | --- | ---: | ---: | ---: | --- | --- |",
    ]
    for result in results:
        if result.get("category") not in {
            "projection",
            "projection_concurrency",
            "canonical_execution",
        }:
            continue
        raw_metadata = result.get("scenario_metadata")
        scenario_metadata = raw_metadata if isinstance(raw_metadata, Mapping) else {}
        raw_result_bytes = result.get("result_payload_bytes")
        result_bytes = raw_result_bytes if isinstance(raw_result_bytes, Mapping) else {}
        raw_observed_metadata = result.get("observed_result_metadata")
        observed_metadata = (
            raw_observed_metadata if isinstance(raw_observed_metadata, Mapping) else {}
        )
        lines.append(
            f"| {result.get('name', 'unavailable')} | {result.get('status', 'unavailable')} | "
            f"{_format_number(scenario_metadata.get('input_payload_bytes'), 0)} | "
            f"{_format_number(result_bytes.get('mean'), 0)} | "
            f"{_format_number(observed_metadata.get('returned_record_count'), 0)} | "
            f"{scenario_metadata.get('projection_changes_result', 'unavailable')} | "
            f"{result.get('execution_boundary', 'unavailable')} |"
        )
    return [*lines, ""]


def _render_epic6_trial_consistency(metadata: Mapping[str, object]) -> list[str]:
    """Render direct and canonical RR-09.1 trial variation separately.

    Args:
        metadata: Sanitized RR-09.1 benchmark metadata.

    Returns:
        Markdown tables for trial p50s, throughput, and canonical status.
    """
    raw_direct_trials = metadata.get("epic6_direct_projection_trials")
    if not isinstance(raw_direct_trials, list):
        return []
    by_name: dict[str, list[Mapping[str, object]]] = {}
    for trial in raw_direct_trials:
        if not isinstance(trial, Mapping) or not isinstance(trial.get("results"), list):
            continue
        for result in cast(list[object], trial["results"]):
            if isinstance(result, Mapping):
                by_name.setdefault(str(result.get("name")), []).append(result)
    lines = [
        "### RR-09.1 direct projection trial consistency",
        "",
        f"Method: {metadata.get('epic6_direct_projection_trial_method', 'unavailable')}",
        "",
        "| Scenario | p50 ms (1 / 2 / 3) | p95 ms (1 / 2 / 3) | "
        "p99 ms (1 / 2 / 3) | n / errors (1 / 2 / 3) |",
        "| --- | --- | --- | --- | --- |",
    ]
    for scenario_name in sorted(by_name):
        values = by_name[scenario_name]
        percentile_values: dict[str, list[str]] = {"p50_ms": [], "p95_ms": [], "p99_ms": []}
        samples: list[str] = []
        for value in values:
            raw_latency = value.get("latency")
            latency = raw_latency if isinstance(raw_latency, Mapping) else {}
            for metric_name, metric_values in percentile_values.items():
                metric_values.append(_format_number(latency.get(metric_name)))
            samples.append(
                f"{_format_number(value.get('sample_count'), 0)} / "
                f"{_format_number(value.get('error_count'), 0)}"
            )
        lines.append(
            f"| {scenario_name} | {' / '.join(percentile_values['p50_ms'])} | "
            f"{' / '.join(percentile_values['p95_ms'])} | "
            f"{' / '.join(percentile_values['p99_ms'])} | {' / '.join(samples)} |"
        )
    lines.extend(
        (
            "",
            "Canonical dispatcher-inclusive evidence is recorded separately from direct projection "
            "timing. It is not subtracted from inclusive Registry stages.",
            "",
        )
    )
    return lines


def _render_epic6_canonical_trial_consistency(metadata: Mapping[str, object]) -> list[str]:
    """Render RR-09.1 canonical dataset-list trial variation without stage subtraction.

    Args:
        metadata: Sanitized RR-09.1 benchmark metadata.

    Returns:
        Markdown table for canonical dataset-list trial timing and dispatch observations.
    """
    raw_trials = metadata.get("epic6_canonical_trials")
    if not isinstance(raw_trials, list):
        return []
    by_name: dict[str, list[Mapping[str, object]]] = {}
    for trial in raw_trials:
        if not isinstance(trial, Mapping) or not isinstance(trial.get("results"), list):
            continue
        for result in cast(list[object], trial["results"]):
            if isinstance(result, Mapping) and str(result.get("name", "")).startswith(
                "grouped_result.canonical.dataset_list."
            ):
                by_name.setdefault(str(result["name"]), []).append(result)
    if not by_name:
        return []
    lines = [
        "### RR-09.1 canonical dataset-list trial consistency",
        "",
        f"Method: {metadata.get('epic6_canonical_trial_method', 'unavailable')}",
        "",
        "| Scenario | p50 ms (1 / 2 / 3) | p95 ms (1 / 2 / 3) | p99 ms (1 / 2 / 3) | "
        "returned records (1 / 2 / 3) | dispatcher calls (1 / 2 / 3) | n / errors (1 / 2 / 3) |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for scenario_name in sorted(by_name):
        percentile_values: dict[str, list[str]] = {"p50_ms": [], "p95_ms": [], "p99_ms": []}
        record_counts: list[str] = []
        dispatch_counts: list[str] = []
        samples: list[str] = []
        for result in by_name[scenario_name]:
            raw_latency = result.get("latency")
            latency = raw_latency if isinstance(raw_latency, Mapping) else {}
            for metric_name, metric_values in percentile_values.items():
                metric_values.append(_format_number(latency.get(metric_name)))
            raw_observed_metadata = result.get("observed_result_metadata")
            observed_metadata = (
                raw_observed_metadata if isinstance(raw_observed_metadata, Mapping) else {}
            )
            raw_scenario_metadata = result.get("scenario_metadata")
            scenario_metadata = (
                raw_scenario_metadata if isinstance(raw_scenario_metadata, Mapping) else {}
            )
            record_counts.append(_format_number(observed_metadata.get("returned_record_count"), 0))
            dispatch_counts.append(
                _format_number(scenario_metadata.get("canonical_dispatch_call_count"), 0)
            )
            samples.append(
                f"{_format_number(result.get('sample_count'), 0)} / "
                f"{_format_number(result.get('error_count'), 0)}"
            )
        lines.append(
            f"| {scenario_name} | {' / '.join(percentile_values['p50_ms'])} | "
            f"{' / '.join(percentile_values['p95_ms'])} | "
            f"{' / '.join(percentile_values['p99_ms'])} | {' / '.join(record_counts)} | "
            f"{' / '.join(dispatch_counts)} | {' / '.join(samples)} |"
        )
    return [
        *lines,
        "",
        "All stage timings are inclusive. They are reported separately and are not subtracted "
        "from canonical wall latency or from one another.",
        "",
    ]


def _render_epic6_materiality_gate(metadata: Mapping[str, object]) -> list[str]:
    """Render the proposed RR-09.1 gate without treating it as approval.

    Args:
        metadata: Sanitized metadata that may contain the proposed RR-09.1 gate.

    Returns:
        Markdown lines describing thresholds, evidence gaps, and the deferred recommendation.
    """
    raw_gate = metadata.get("epic6_materiality_gate")
    if not isinstance(raw_gate, Mapping):
        return []
    return [
        "### RR-09.1 proposed performance decision gate",
        "",
        "This is a proposed threshold, not product-owner approval and not authorization for RR-09.3.",
        "",
        "| Proposed absolute reduction | Proposed relative reduction | Candidate measured | Status | Recommendation |",
        "| ---: | ---: | --- | --- | --- |",
        f"| ≥ {_format_number(raw_gate.get('absolute_latency_reduction_ms_threshold'))} ms | "
        f"≥ {_format_number(raw_gate.get('relative_latency_reduction_percent_threshold'))}% | "
        f"{raw_gate.get('candidate_implementation_measured', False)} | "
        f"{raw_gate.get('status', 'unavailable')} | {raw_gate.get('recommendation', 'DEFER')} |",
        "",
        str(
            raw_gate.get("representative_workload_requirement", "No representative-workload gate.")
        ),
        str(raw_gate.get("reproducibility_requirement", "No reproducibility gate.")),
        str(raw_gate.get("security_requirement", "No security gate.")),
        "",
    ]


def render_report(metadata: Mapping[str, object], results: Sequence[Mapping[str, object]]) -> str:
    """Build a factual Markdown baseline from actual sanitized result records.

    Args:
        metadata: Allowlisted, non-sensitive runtime and configuration metadata.
        results: Measured, failed, or explicitly unavailable scenarios.

    Returns:
        Markdown baseline report for future Registry-only comparisons.
    """
    measured = sum(result.get("status") == "measured" for result in results)
    unavailable = sum(result.get("status") == "not_measured" for result in results)
    failed = sum(result.get("status") == "failed" for result in results)
    unmeasured_names = ", ".join(
        str(result["name"]) for result in results if result.get("status") == "not_measured"
    )
    selection_by_name = {str(result.get("name")): result for result in results}
    selection_one = selection_by_name.get("selection.1", {})
    selection_fifty = selection_by_name.get("selection.50", {})
    observed_scaling = "Selection scaling was not measured in this run."
    priority_hypothesis = "No measured selection comparison is available to prioritize."
    if selection_one.get("status") == selection_fifty.get("status") == "measured":
        one_latency = selection_one.get("latency")
        fifty_latency = selection_fifty.get("latency")
        if isinstance(one_latency, Mapping) and isinstance(fifty_latency, Mapping):
            observed_scaling = (
                "In the isolated database fixture, selection prompt-context p50 rose "
                f"from {_format_number(one_latency.get('p50_ms'))} ms at 1 selection to "
                f"{_format_number(fifty_latency.get('p50_ms'))} ms at 50 selections."
            )
            priority_hypothesis = (
                "The first candidate for a later optimization Epic is selection "
                "prompt-context scaling; investigate its increasing SQL count before "
                "attributing the latency increase to a specific query pattern."
            )
    artifact_set = str(metadata.get("artifact_set", "baseline"))
    report_title = {
        "baseline": "# Resource Registry E1.4 performance baseline",
        "e21_candidate": "# Resource Registry E2.1 candidate benchmark",
        "e23_verification": "# Resource Registry E2.3 verification benchmark",
        "epic3_grouped_baseline": "# Resource Registry Epic 3 grouped execution baseline",
        "epic4_catalog_baseline": (
            "# Resource Registry Epic 4 catalog and validator evidence baseline"
        ),
        "epic5_selection_baseline": (
            "# Resource Registry Epic 5 selection scaling and security baseline"
        ),
        "epic5_selection_candidate": (
            "# Resource Registry Epic 5 bounded cached-selection revalidation candidate"
        ),
        "epic6_grouped_result_baseline": (
            "# Resource Registry Epic 6 grouped-result scaling baseline"
        ),
    }.get(artifact_set, "# Resource Registry performance benchmark")
    execution_methodology_lines = [
        "- Adapter routing cases execute the live `DataRegistryExplorationRoutingAdapter`, "
        "persisted Registry authorization, Persona Workspace scope, and assistant namespace "
        "resolution. Successful adapter cases replace only the shared dispatcher boundary "
        "before canonical BIF admission; `namespace_admission` is therefore unavailable for "
        "those cases. Their execution-boundary column labels this substitution. Workspace "
        "ambiguity and revocation cases terminate in live Registry logic before that boundary.",
        "- Concurrency uses real `ThreadPoolExecutor` workers on pure routing only. "
        "Its throughput includes scheduling and GIL effects; it does not model database "
        "connection contention or provider work.",
    ]
    if artifact_set == "epic3_grouped_baseline":
        execution_methodology_lines = [
            "- RR-03.1 canonical scenarios use a test-only `AIEngineClient` subclass that "
            "delegates to `super().callGenericFunction()`; shared authorization, selector "
            "resolution, parameter validation, and Registry handlers remain active.",
            "- Concurrency uses real `ThreadPoolExecutor` workers with independent "
            "request/session state against isolated PostgreSQL. It includes database connection "
            "contention and excludes provider work.",
        ]
    if artifact_set == "epic4_catalog_baseline":
        execution_methodology_lines = [
            "- RR-04.1 catalog publication, metadata freshness, and canonical-admission cases "
            "are measured only by the guarded isolated PostgreSQL pytest command. Until then, "
            "their explicit records remain unavailable rather than using synthetic substitutes.",
            "- Historical single-Persona concurrency scenarios remain separate. New "
            "`multi_context` scenarios publish distinct Persona, Organization, Workspace, and "
            "dataset contexts concurrently, with independent worker-local database connections.",
            "- Static transformation validation uses the real source-controlled schemas. "
            "Memory profiling is a separate bounded `tracemalloc` run, and process scenarios "
            "use fresh subprocess workers with no shared process-global state.",
        ]
    if artifact_set == "epic5_selection_baseline":
        execution_methodology_lines = [
            "- RR-05.1 measures only existing cached-selection behavior on guarded isolated "
            "PostgreSQL fixtures: prompt-context construction, verified comparison-ID discovery, "
            "and version revalidation. It does not implement or benchmark a batch query.",
            "- Every selection scenario records benchmark-local whole-operation SQL with "
            "`DEBUG=False`. Those totals are not attributed to sub-stages; any existing Registry "
            "stage event is inclusive and remains separate rather than summed.",
        ]
    if artifact_set == "epic5_selection_candidate":
        execution_methodology_lines = [
            "- RR-05.2 measures bounded Registry-local cached-selection revalidation on guarded "
            "isolated PostgreSQL fixtures. It preserves the RR-05.1 operation boundaries and "
            "does not make an end-to-end `/chat` latency claim.",
            "- Dataset identifiers are revalidated in chunks of at most 50 and pinned versions "
            "are matched by their exact Dataset/Version pair. Canonical BIF admission and "
            "handler reads remain outside this discovery optimization.",
            "- Every selection scenario records benchmark-local whole-operation SQL with "
            "`DEBUG=False`; root SQL is not attributed to inner Registry stages.",
        ]
    if artifact_set == "epic6_grouped_result_baseline":
        execution_methodology_lines = [
            "- RR-09.1 invokes the unmodified production `project_registry_tool_result` directly "
            "for bounded deterministic fixtures. Direct projection timing is not canonical "
            "dispatcher or provider timing.",
            "- Canonical scenarios, when guarded PostgreSQL fixtures support them, use the live "
            "grouped entry point and inherited `AIEngineClient` dispatcher. Unsupported response "
            "types remain explicitly `not_measured`; no synthetic substitute is labeled canonical.",
            "- Direct concurrency uses per-wave barriers at 1, 10, and 25 workers. It measures "
            "latency and throughput only; RR-09.2 owns allocation and memory conclusions.",
        ]
    lines = [
        report_title,
        "",
        "## Executive summary",
        "",
        f"Measured {measured} scenarios; {unavailable} not measured; {failed} failed. "
        "Synthetic and isolated database-backed results are labeled separately. "
        "These are local measurements, not end-to-end `/chat` latency.",
        f"Unmeasured: {unmeasured_names or 'none'}.",
        observed_scaling,
        "",
        "## Methodology",
        "",
        f"- Revision: `{metadata.get('git_revision', 'unavailable')}`; "
        f"dirty tree: `{metadata.get('dirty', 'unavailable')}`.",
        f"- Artifact set: `{artifact_set}`.",
        f"- Runtime: Python {metadata.get('python_version', 'unavailable')}; "
        f"Django {metadata.get('django_version', 'unavailable')}; "
        f"{metadata.get('operating_system', 'unavailable')}/"
        f"{metadata.get('cpu_architecture', 'unavailable')}.",
        f"- Database engine: {metadata.get('database_engine', 'unavailable')}; "
        "only isolated pytest test-database scenarios may access it.",
        f"- Warmup: {metadata.get('warmups', 'unavailable')} iterations; measured: "
        f"{metadata.get('iterations', 'unavailable')} per serial scenario; "
        "warmups are excluded from distributions.",
        "- Clock: `time.perf_counter_ns()` around each operation. Nearest-rank "
        "percentiles use sorted sample at `ceil(p*n/100)-1`, with no interpolation. "
        "p95/p99 are flagged unstable below 100 successful samples.",
        "- SQL and payload counts come from existing inclusive E1.1–E1.3 Registry "
        "events. Nested stages overlap and must not be added. Missing metrics are "
        "unavailable, not zero. SQL duration is Django wrapper time, not server CPU.",
        "- Main serial run: Registry-local logger capture, sampling 1.0, E1.1/E1.2/E1.3 "
        "enabled. Overhead modes reuse identical fixtures and a test-only configuration "
        "reset; the logger is restored after each run.",
        *execution_methodology_lines,
        "",
        "## Results",
        "",
    ]
    standard_categories = (
        "routing",
        "publication",
        "schema",
        "validation",
        "selection",
        "execution",
        "concurrency",
        "overhead",
    )
    extra_categories = sorted(
        {str(result.get("category")) for result in results} - set(standard_categories)
    )
    for category in (*standard_categories, *extra_categories):
        lines.append(_render_category(category, results))
        lines.append("")
    if artifact_set == "epic4_catalog_baseline":
        lines.extend(_render_epic4_memory_metrics(results))
        lines.extend(_render_epic4_process_metrics(results))
        lines.extend(_render_epic4_catalog_trial_consistency(metadata))
    if artifact_set in {"epic5_selection_baseline", "epic5_selection_candidate"}:
        lines.extend(_render_epic5_selection_trial_consistency(metadata))
    if artifact_set == "epic5_selection_candidate":
        lines.extend(_render_epic5_candidate_comparison(metadata))
    if artifact_set == "epic6_grouped_result_baseline":
        lines.extend(_render_epic6_payload_matrix(results))
        lines.extend(_render_epic6_trial_consistency(metadata))
        lines.extend(_render_epic6_canonical_trial_consistency(metadata))
        lines.extend(_render_epic6_materiality_gate(metadata))
    lines.extend(_render_pre_optimization_adapter_comparison(metadata))
    lines.extend(_render_adapter_trial_consistency(metadata))
    lines.extend(_render_grouped_execution_trial_consistency(metadata))
    lines.extend(
        (
            "### Wall distribution detail",
            "",
            "| Scenario | mean_ms | min_ms | max_ms | stddev_ms |",
            "| --- | ---: | ---: | ---: | ---: |",
        )
    )
    for result in results:
        raw_latency = result.get("latency")
        latency = raw_latency if isinstance(raw_latency, Mapping) else {}
        lines.append(
            f"| {result['name']} | "
            + " | ".join(
                _format_number(latency.get(key))
                for key in ("mean_ms", "min_ms", "max_ms", "stddev_ms")
            )
            + " |"
        )
    lines.append("")
    lines.extend(
        (
            "### Publication payload complexity",
            "",
            "| Scenario | tool_count | action_count | payload_bytes | "
            "schema_property_count | description_char_count |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        )
    )
    for result in results:
        if result.get("category") != "publication":
            continue
        stages = result.get("stages")
        stage = stages.get("registry_tool_publication", {}) if isinstance(stages, Mapping) else {}
        lines.append(
            f"| {result['name']} | "
            + " | ".join(
                _format_number(stage.get(key), 0)
                for key in (
                    "tool_count",
                    "action_count",
                    "payload_bytes",
                    "schema_property_count",
                    "description_char_count",
                )
            )
            + " |"
        )
    lines.extend(
        (
            "",
            "### Concurrency throughput",
            "",
            "| Scenario | workers | completed | failed | throughput_ops_per_second |",
            "| --- | ---: | ---: | ---: | ---: |",
        )
    )
    for result in results:
        if result.get("category") == "concurrency":
            lines.append(
                f"| {result['name']} | {_format_number(result.get('concurrency'), 0)} | "
                f"{_format_number(result.get('completed_count'), 0)} | "
                f"{_format_number(result.get('error_count'), 0)} | "
                f"{_format_number(result.get('throughput_ops_per_second'))} |"
            )
    lines.extend(
        (
            "",
            "### Instrumentation overhead",
            "",
            "| Scenario | p50_delta_ms | p50_delta_percent |",
            "| --- | ---: | ---: |",
        )
    )
    for result in results:
        if result.get("category") == "overhead":
            lines.append(
                f"| {result['name']} | {_format_number(result.get('p50_delta_ms'))} | "
                f"{_format_number(result.get('p50_delta_percent'))} |"
            )
    lines.append("")
    lines.extend(
        (
            "### Registry stage metrics (inclusive)",
            "",
            "| Scenario | Stage | events | mean_duration_ms | "
            "mean_db_query_count | mean_db_duration_ms |",
            "| --- | --- | ---: | ---: | ---: | ---: |",
        )
    )
    for result in results:
        stages = result.get("stages")
        if not isinstance(stages, Mapping):
            continue
        for stage_name, stage_value in sorted(stages.items()):
            if not isinstance(stage_value, Mapping):
                continue
            lines.append(
                f"| {result['name']} | {stage_name} | "
                f"{_format_number(stage_value.get('event_count'), 0)} | "
                f"{_format_number(stage_value.get('mean_duration_ms'))} | "
                f"{_format_number(stage_value.get('mean_db_query_count'), 1)} | "
                f"{_format_number(stage_value.get('mean_db_duration_ms'))} |"
            )
    lines.append("")
    selection_results = {str(result.get("name")): result for result in results}
    one_selection = selection_results.get("selection.1", {})
    fifty_selections = selection_results.get("selection.50", {})
    if one_selection.get("status") == fifty_selections.get("status") == "measured":
        one_stage = cast(Mapping[str, object], one_selection.get("stages", {})).get(
            "selection_prompt_context", {}
        )
        fifty_stage = cast(Mapping[str, object], fifty_selections.get("stages", {})).get(
            "selection_prompt_context", {}
        )
        lines.append(
            "Observed selection scaling in this local fixture: p50 "
            f"{_format_number(cast(Mapping[str, object], one_selection['latency']).get('p50_ms'))} "
            "ms at 1 selection versus "
            f"{_format_number(cast(Mapping[str, object], fifty_selections['latency']).get('p50_ms'))} "
            "ms at 50; mean inclusive SQL count "
            f"{_format_number(cast(Mapping[str, object], one_stage).get('mean_db_query_count'), 1)} "
            "versus "
            f"{_format_number(cast(Mapping[str, object], fifty_stage).get('mean_db_query_count'), 1)}. "
            "This is a measured association, not a causal attribution."
        )
        lines.append("")
    lines.extend(
        (
            "## Interpretation",
            "",
            "Measured facts are the scenario rows and stage metrics above. Compare only "
            "like-for-like fixture kinds and instrumentation modes; small local differences "
            "do not establish causation or statistical significance.",
            "",
            "Potential optimization areas are hypotheses until repeated controlled "
            "comparisons show a consistent effect. Shared canonical dispatch, provider "
            "network, and full `/chat` orchestration are platform-level limits and are not "
            "Registry-exclusive measurements.",
            priority_hypothesis,
            "",
        )
    )
    lines.extend(_render_follow_on_story_recommendations(metadata))
    lines.extend(_render_epic4_optimization_gate(metadata))
    lines.extend(
        (
            "## Future comparison",
            "",
            "Run the same commands, fixture sizes, warmups, measured iterations, sampling "
            "rate, and instrumentation flags on both revisions. Compare matching scenario "
            "IDs and fixture kinds; retain raw sample counts, SQL stage rows, payload "
            "complexity, errors, and environment metadata. Repeat runs before declaring "
            "small changes meaningful.",
            "",
        )
    )
    return "\n".join(lines)
