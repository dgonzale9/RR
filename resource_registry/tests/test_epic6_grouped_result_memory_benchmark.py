"""Verify the RR-09.2 grouped-result memory benchmark contract."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest

from resource_registry.benchmarks.epic6_grouped_result import (
    EPIC6_PROJECTION_SCENARIO_NAMES,
    build_epic6_projection_fixtures,
)
from resource_registry.benchmarks.epic6_grouped_result_memory import (
    EPIC6_GROUPED_RESULT_MEMORY_ARTIFACT_SET,
    EPIC6_MEMORY_CANONICAL_SCENARIO_NAMES,
    EPIC6_MEMORY_CONCURRENCY_SCENARIO_NAMES,
    EPIC6_MEMORY_REPEATED_ITERATION_COUNTS,
    EPIC6_MEMORY_REPEATED_SCENARIO_NAMES,
    EPIC6_MEMORY_SINGLE_SCENARIO_NAMES,
    EPIC6_MEMORY_TRACING_CONFIGURATION,
    profile_epic6_projection_fixture_memory,
    profile_epic6_repeated_projection_retention,
    run_epic6_memory_concurrency,
)
from resource_registry.benchmarks.runner import get_benchmark_artifact_paths


def test_epic6_memory_artifacts_use_a_dedicated_selector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Require RR-09.2 runs to avoid RR-09.1 and historical artifact paths.

    Args:
        monkeypatch: Fixture that controls the selected artifact set.
    """
    monkeypatch.setenv(
        "RESOURCE_REGISTRY_BENCHMARK_ARTIFACT_SET",
        EPIC6_GROUPED_RESULT_MEMORY_ARTIFACT_SET,
    )

    results_path, report_path = get_benchmark_artifact_paths()

    assert results_path.name == "epic6_grouped_result_memory_results.json"
    assert report_path.name == "epic6_grouped_result_memory_report.md"


def test_epic6_memory_inventory_reuses_every_direct_projection_fixture() -> None:
    """Require all RR-09.1 payload shapes to receive single-operation evidence."""
    assert set(EPIC6_MEMORY_SINGLE_SCENARIO_NAMES) == set(EPIC6_PROJECTION_SCENARIO_NAMES)
    assert set(EPIC6_MEMORY_REPEATED_SCENARIO_NAMES) <= set(EPIC6_MEMORY_SINGLE_SCENARIO_NAMES)
    assert EPIC6_MEMORY_REPEATED_ITERATION_COUNTS == (100, 500, 1000)
    assert set(EPIC6_MEMORY_CONCURRENCY_SCENARIO_NAMES) == {
        "grouped_result.memory.concurrency.workers_001",
        "grouped_result.memory.concurrency.workers_010",
        "grouped_result.memory.concurrency.workers_025",
    }


def test_epic6_single_projection_memory_records_exact_parity_and_signed_deltas() -> None:
    """Record output-included traced measurements without clamping retained deltas."""
    fixture = next(
        item
        for item in build_epic6_projection_fixtures()
        if item.name == "grouped_result.projection.rewritten.hint.records_100"
    )

    result = profile_epic6_projection_fixture_memory(fixture)

    assert result["status"] == "measured"
    assert result["error_count"] == 0
    assert result["input_payload_bytes"] == fixture.input_payload_bytes
    assert result["output_payload_bytes"] == len(fixture.expected_result.encode("utf-8"))
    assert result["tracing_configuration"] == EPIC6_MEMORY_TRACING_CONFIGURATION
    raw_trials = result["trials"]
    assert isinstance(raw_trials, list)
    assert all(isinstance(trial, dict) for trial in raw_trials)
    trials = cast(list[dict[str, object]], raw_trials)
    assert len(trials) == 3
    for trial in trials:
        assert trial["output_parity"] == "exact"
        assert trial["output_included_during_measurement"] is True
        peak_bytes = _get_integer(trial, "traced_peak_bytes")
        current_bytes = _get_integer(trial, "traced_current_after_execution_bytes")
        _get_integer(trial, "post_gc_retained_delta_bytes")
        temporary_peak_amplification_bytes = _get_integer(
            trial, "temporary_peak_amplification_bytes"
        )
        assert peak_bytes >= current_bytes >= 0
        assert temporary_peak_amplification_bytes >= 0


def test_epic6_repeated_memory_profile_releases_each_output_and_keeps_signed_deltas() -> None:
    """Measure retention checkpoints without retaining a list of projected outputs."""
    fixture = next(
        item
        for item in build_epic6_projection_fixtures()
        if item.name == "grouped_result.projection.unchanged.records_001"
    )

    result = profile_epic6_repeated_projection_retention(fixture, iterations=100)

    assert result["status"] == "measured"
    assert result["output_retention"] == "none"
    assert result["negative_control"] is True
    raw_trials = result["trials"]
    assert isinstance(raw_trials, list)
    assert all(isinstance(trial, dict) for trial in raw_trials)
    trials = cast(list[dict[str, object]], raw_trials)
    assert len(trials) == 3
    for trial in trials:
        assert trial["completed_operations"] == 100
        assert trial["error_count"] == 0
        _get_integer(trial, "initial_retained_delta_bytes")
        _get_integer(trial, "intermediate_retained_delta_bytes")
        _get_integer(trial, "final_retained_delta_bytes")
        _get_integer(trial, "post_gc_retained_delta_bytes")
        assert _get_integer(trial, "traced_peak_bytes") >= 0


def test_epic6_concurrent_memory_profile_confirms_synchronized_operation_overlap() -> None:
    """Require measured concurrent waves to report real in-flight overlap and no failures."""
    result = run_epic6_memory_concurrency(workers=10)

    assert result["status"] == "measured"
    assert result["completed_operations"] == 10
    assert result["unexpected_error_count"] == 0
    assert result["tracemalloc_scope"] == "process_wide"
    assert result["per_request_attribution"] == "not_supported"
    raw_trials = result["trials"]
    assert isinstance(raw_trials, list)
    assert all(isinstance(trial, dict) for trial in raw_trials)
    trials = cast(list[dict[str, object]], raw_trials)
    for trial in trials:
        actual_peak_in_flight_operations = _get_integer(trial, "actual_peak_in_flight_operations")
        peak_bytes = _get_integer(trial, "traced_peak_bytes")
        current_bytes = _get_integer(trial, "traced_current_after_execution_bytes")
        _get_integer(trial, "post_gc_retained_delta_bytes")
        assert actual_peak_in_flight_operations >= 2
        assert actual_peak_in_flight_operations <= 10
        assert peak_bytes >= current_bytes >= 0


def test_epic6_memory_artifact_contains_measured_direct_and_canonical_evidence() -> None:
    """Require reproducible metadata and measured direct and guarded canonical records."""
    artifact_path = (
        Path(__file__).resolve().parents[1]
        / "benchmarks"
        / "epic6_grouped_result_memory_results.json"
    )
    payload = json.loads(artifact_path.read_text())
    metadata = payload["metadata"]
    results_by_name = {result["name"]: result for result in payload["results"]}

    assert metadata["artifact_set"] == EPIC6_GROUPED_RESULT_MEMORY_ARTIFACT_SET
    assert metadata["epic6_memory_trial_count"] == 3
    assert metadata["tracing_configuration"] == EPIC6_MEMORY_TRACING_CONFIGURATION
    assert metadata["latency_measurements_included"] is False
    assert metadata["epic6_memory_canonical_execution_status"] == (
        "measured_guarded_isolated_postgresql"
    )
    assert metadata["epic6_memory_database_guard"]["validated"] is True
    assert len(metadata["epic6_memory_canonical_trials"]) == 3
    assert set(results_by_name) == {
        *EPIC6_MEMORY_SINGLE_SCENARIO_NAMES,
        *EPIC6_MEMORY_CONCURRENCY_SCENARIO_NAMES,
        *EPIC6_MEMORY_CANONICAL_SCENARIO_NAMES,
        *{
            f"grouped_result.memory.retention.{name.rsplit('.', maxsplit=1)[-1]}."
            f"iterations_{iterations:04d}"
            for name in EPIC6_MEMORY_REPEATED_SCENARIO_NAMES
            for iterations in EPIC6_MEMORY_REPEATED_ITERATION_COUNTS
        },
    }
    for name in EPIC6_MEMORY_SINGLE_SCENARIO_NAMES:
        result = results_by_name[name]
        assert result["status"] == "measured"
        assert result["input_payload_bytes"] >= 0
        assert result["output_payload_bytes"] >= 0
        assert len(result["trials"]) == 3
    for name in EPIC6_MEMORY_CANONICAL_SCENARIO_NAMES:
        result = results_by_name[name]
        assert result["status"] == "measured"
        assert result["execution_boundary"] == "canonical_dispatch_included"
        assert result["input_payload_bytes"] is None
        assert result["output_payload_bytes"] > 0
        assert result["error_count"] == 0


def test_epic6_memory_work_does_not_modify_accepted_rr091_or_prior_artifacts() -> None:
    """Keep RR-09.1 and earlier accepted evidence byte-for-byte unchanged."""
    completed = subprocess.run(
        [
            "git",
            "diff",
            "--quiet",
            "HEAD",
            "--",
            "resource_registry/benchmarks/baseline_results.json",
            "resource_registry/benchmarks/baseline_report.md",
            "resource_registry/benchmarks/e21_candidate_results.json",
            "resource_registry/benchmarks/e21_candidate_report.md",
            "resource_registry/benchmarks/e23_verification_results.json",
            "resource_registry/benchmarks/e23_verification_report.md",
            "resource_registry/benchmarks/epic3_grouped_baseline_results.json",
            "resource_registry/benchmarks/epic3_grouped_baseline_report.md",
            "resource_registry/benchmarks/epic4_catalog_baseline_results.json",
            "resource_registry/benchmarks/epic4_catalog_baseline_report.md",
            "resource_registry/benchmarks/epic5_selection_baseline_results.json",
            "resource_registry/benchmarks/epic5_selection_baseline_report.md",
            "resource_registry/benchmarks/epic6_grouped_result_baseline_results.json",
            "resource_registry/benchmarks/epic6_grouped_result_baseline_report.md",
        ],
        check=False,
    )

    assert completed.returncode == 0


def _get_integer(record: Mapping[str, object], field_name: str) -> int:
    """Return one non-boolean integer from a JSON-safe benchmark record.

    Args:
        record: Memory record or trial being inspected.
        field_name: Required integer field name.

    Returns:
        The asserted integer value.
    """
    value = record[field_name]
    assert isinstance(value, int)
    assert not isinstance(value, bool)
    return value
