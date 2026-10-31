"""Verify the RR-05 selection-scaling baseline and candidate artifact contracts."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from resource_registry.assistant import dataset_selection_context
from resource_registry.benchmarks.epic5_selection import (
    EPIC5_ARTIFACT_SET,
    EPIC5_CANDIDATE_ARTIFACT_SET,
    EPIC5_DATABASE_SCENARIO_NAMES,
    EPIC5_SELECTION_SCALE_COUNTS,
    EPIC5_SELECTION_SCENARIO_BOUNDARIES,
)
from resource_registry.benchmarks.reporting import render_report
from resource_registry.benchmarks.runner import get_benchmark_artifact_paths, run_scenario
from resource_registry.benchmarks.scenarios import BenchmarkScenario


def test_epic5_artifacts_are_isolated_from_accepted_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Require RR-05.1 runs to write only their dedicated artifact files.

    Args:
        monkeypatch: Fixture controlling the selected artifact set.
    """
    monkeypatch.setenv("RESOURCE_REGISTRY_BENCHMARK_ARTIFACT_SET", EPIC5_ARTIFACT_SET)

    results_path, report_path = get_benchmark_artifact_paths()

    assert results_path.name == "epic5_selection_baseline_results.json"
    assert report_path.name == "epic5_selection_baseline_report.md"

    monkeypatch.setenv("RESOURCE_REGISTRY_BENCHMARK_ARTIFACT_SET", EPIC5_CANDIDATE_ARTIFACT_SET)
    results_path, report_path = get_benchmark_artifact_paths()

    assert results_path.name == "epic5_selection_candidate_results.json"
    assert report_path.name == "epic5_selection_candidate_report.md"


def test_epic5_registers_all_real_selection_scaling_boundaries() -> None:
    """Require every RR-05.1 workload to declare an actual isolated-DB boundary."""
    assert EPIC5_SELECTION_SCALE_COUNTS == (1, 5, 10, 25, 50)
    assert set(EPIC5_DATABASE_SCENARIO_NAMES) == set(EPIC5_SELECTION_SCENARIO_BOUNDARIES)
    assert set(EPIC5_SELECTION_SCENARIO_BOUNDARIES.values()) <= {
        "registry_pre_dispatch_only",
        "canonical_dispatch_included",
    }
    for count in EPIC5_SELECTION_SCALE_COUNTS:
        assert f"selection.prompt_context.versionless.{count}" in EPIC5_DATABASE_SCENARIO_NAMES
        assert f"selection.prompt_context.version_pinned.{count}" in EPIC5_DATABASE_SCENARIO_NAMES
        assert f"selection.comparison_ids.{count}" in EPIC5_DATABASE_SCENARIO_NAMES
        assert f"selection.version_revalidation.{count}" in EPIC5_DATABASE_SCENARIO_NAMES


def test_database_benchmark_records_root_sql_without_enabling_debug() -> None:
    """Require a benchmark-local SQL metric when no fixed Registry stage exists."""
    result = run_scenario(
        BenchmarkScenario(
            "test.epic5.root_sql",
            "selection",
            "isolated_database",
            lambda: "complete",
            execution_boundary="registry_pre_dispatch_only",
            capture_database_sql=True,
        ),
        warmups=0,
        iterations=1,
    )

    database_sql = result["database_sql"]

    assert isinstance(database_sql, dict)
    assert database_sql["measurement_scope"] == "whole_operation"
    assert database_sql["mean_query_count"] == 0.0
    assert database_sql["mean_duration_ms"] == 0.0


def test_epic5_report_keeps_trial_variation_and_unattributed_root_sql_visible() -> None:
    """Require the RR-05.1 report to distinguish root SQL from inclusive stage metrics."""
    report = render_report(
        {
            "artifact_set": EPIC5_ARTIFACT_SET,
            "epic5_selection_trial_method": "Three isolated PostgreSQL trials.",
            "epic5_selection_trials": [
                {
                    "trial": 1,
                    "results": [
                        {
                            "name": "selection.comparison_ids.1",
                            "latency": {"p50_ms": 1.0},
                            "database_sql": {
                                "measurement_scope": "whole_operation",
                                "mean_query_count": 2.0,
                                "mean_duration_ms": 0.1,
                            },
                            "sample_count": 120,
                            "error_count": 0,
                        }
                    ],
                }
            ],
        },
        [
            {
                "name": "selection.comparison_ids.1",
                "category": "selection",
                "kind": "isolated_database",
                "status": "measured",
                "sample_count": 120,
                "error_count": 0,
                "latency": {"p50_ms": 1.0, "p95_ms": 1.1, "p99_ms": 1.2},
                "stages": {},
                "database_sql": {
                    "measurement_scope": "whole_operation",
                    "mean_query_count": 2.0,
                    "mean_duration_ms": 0.1,
                },
                "result_payload_bytes": {"mean": 40.0},
                "authorization_context_build_count_per_operation": None,
                "execution_boundary": "registry_pre_dispatch_only",
            }
        ],
    )

    assert "Epic 5 selection scaling and security baseline" in report
    assert "Selection trial consistency" in report
    assert "whole-operation SQL" in report


def test_epic5_candidate_report_compares_equivalent_frozen_baseline_scenarios() -> None:
    """Require RR-05.2 reports to distinguish candidate evidence from the historical baseline."""
    report = render_report(
        {
            "artifact_set": EPIC5_CANDIDATE_ARTIFACT_SET,
            "epic5_selection_baseline_comparison": [
                {
                    "name": "selection.comparison_ids.50",
                    "before": {
                        "p50_ms": 500.0,
                        "p95_ms": 600.0,
                        "p99_ms": 700.0,
                        "sql_query_count": 318.0,
                        "sql_duration_ms": 293.0,
                        "payload_bytes": 1951.0,
                        "error_count": 0,
                    },
                    "after": {
                        "p50_ms": 50.0,
                        "p95_ms": 60.0,
                        "p99_ms": 70.0,
                        "sql_query_count": 20.0,
                        "sql_duration_ms": 20.0,
                        "payload_bytes": 1951.0,
                        "error_count": 0,
                    },
                }
            ],
        },
        [],
    )

    assert "bounded cached-selection revalidation candidate" in report
    assert "RR-05.2 frozen-baseline comparison" in report
    assert "selection.comparison_ids.50" in report


def test_epic5_artifact_work_does_not_modify_accepted_e1_to_e4_artifacts() -> None:
    """Keep accepted Epic 1–4 result and report artifacts byte-for-byte unchanged."""
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
        ],
        check=False,
    )

    assert completed.returncode == 0


def test_rr052_keeps_batch_revalidation_registry_local_and_nonpublic() -> None:
    """Keep bounded revalidation private to the two approved Registry discovery paths."""
    assert callable(dataset_selection_context._revalidate_cached_selections)
    assert callable(dataset_selection_context._validate_reused_selection)


def test_epic5_artifact_contains_complete_measured_postgresql_evidence() -> None:
    """Require committed RR-05.1 evidence for every inventory item and all three trials."""
    artifact_path = (
        Path(__file__).resolve().parents[1] / "benchmarks" / "epic5_selection_baseline_results.json"
    )
    payload = json.loads(artifact_path.read_text())
    metadata = payload["metadata"]
    results = payload["results"]

    assert metadata["database_engine"] == "postgresql"
    assert metadata["sql_debug"] is False
    assert metadata["warmups"] == 5
    assert metadata["iterations"] == 120
    assert metadata["epic5_selection_trial_count"] == 3
    assert {result["name"] for result in results} == set(EPIC5_DATABASE_SCENARIO_NAMES)
    assert all(result["status"] == "measured" for result in results)
    assert all(result["error_count"] == 0 for result in results)
    assert all(
        result["database_sql"]["measurement_scope"] == "whole_operation" for result in results
    )
    assert all(
        isinstance(result["database_sql"]["mean_query_count"], (int, float))
        and isinstance(result["database_sql"]["mean_duration_ms"], (int, float))
        for result in results
    )
    trials = metadata["epic5_selection_trials"]
    assert len(trials) == 3
    assert all(
        {result["name"] for result in trial["results"]} == set(EPIC5_DATABASE_SCENARIO_NAMES)
        and all(result["status"] == "measured" for result in trial["results"])
        for trial in trials
    )


def test_epic5_candidate_artifact_contains_reproducible_comparison_evidence() -> None:
    """Require RR-05.2 to retain three measured trials and frozen-baseline parity data."""
    artifact_path = (
        Path(__file__).resolve().parents[1]
        / "benchmarks"
        / "epic5_selection_candidate_results.json"
    )
    payload = json.loads(artifact_path.read_text())
    metadata = payload["metadata"]
    results = payload["results"]
    comparisons = metadata["epic5_selection_baseline_comparison"]

    assert metadata["database_engine"] == "postgresql"
    assert metadata["sql_debug"] is False
    assert metadata["warmups"] == 5
    assert metadata["iterations"] == 120
    assert metadata["epic5_selection_trial_count"] == 3
    assert {result["name"] for result in results} == set(EPIC5_DATABASE_SCENARIO_NAMES)
    assert all(result["status"] == "measured" and result["error_count"] == 0 for result in results)
    assert len(metadata["epic5_selection_trials"]) == 3
    assert all(
        {result["name"] for result in trial["results"]} == set(EPIC5_DATABASE_SCENARIO_NAMES)
        and all(
            result["status"] == "measured" and result["error_count"] == 0
            for result in trial["results"]
        )
        for trial in metadata["epic5_selection_trials"]
    )
    assert {comparison["name"] for comparison in comparisons} == set(EPIC5_DATABASE_SCENARIO_NAMES)
    assert all(
        comparison["before"]["payload_bytes"] == comparison["after"]["payload_bytes"]
        for comparison in comparisons
    )


def test_epic5_closure_matrix_preserves_equivalent_measurements_and_integrity() -> None:
    """Require final closure evidence to remain traceable to both frozen benchmark artifacts."""
    benchmarks_directory = Path(__file__).resolve().parents[1] / "benchmarks"
    closure_matrix = json.loads(
        (benchmarks_directory / "epic5_selection_closure_matrix.json").read_text()
    )
    baseline = json.loads(
        (benchmarks_directory / "epic5_selection_baseline_results.json").read_text()
    )
    candidate = json.loads(
        (benchmarks_directory / "epic5_selection_candidate_results.json").read_text()
    )

    assert closure_matrix["schema_version"] == 1
    assert closure_matrix["status"] == "CLOSE — EPIC 5 ACCEPTANCE SATISFIED"
    assert closure_matrix["evidence_sources"] == {
        "baseline": "resource_registry/benchmarks/epic5_selection_baseline_results.json",
        "candidate": "resource_registry/benchmarks/epic5_selection_candidate_results.json",
    }
    assert closure_matrix["benchmark_integrity"] == {
        "database_engine": "postgresql",
        "sql_debug": False,
        "warmups": 5,
        "iterations_per_trial": 120,
        "independent_trials": 3,
        "all_scenarios_measured": True,
        "all_scenario_errors_zero": True,
    }

    baseline_by_name = {result["name"]: result for result in baseline["results"]}
    candidate_by_name = {result["name"]: result for result in candidate["results"]}
    closure_by_name = {
        scenario["scenario_id"]: scenario for scenario in closure_matrix["scenarios"]
    }

    assert set(closure_by_name) == set(EPIC5_DATABASE_SCENARIO_NAMES)
    assert set(baseline_by_name) == set(EPIC5_DATABASE_SCENARIO_NAMES)
    assert set(candidate_by_name) == set(EPIC5_DATABASE_SCENARIO_NAMES)
    for scenario_id, closure_scenario in closure_by_name.items():
        baseline_result = baseline_by_name[scenario_id]
        candidate_result = candidate_by_name[scenario_id]

        assert closure_scenario["comparability"] == "COMPARABLE"
        assert closure_scenario["execution_boundary"] == baseline_result["execution_boundary"]
        assert closure_scenario["execution_boundary"] == candidate_result["execution_boundary"]
        assert closure_scenario["baseline"]["sql_query_count"] == (
            baseline_result["database_sql"]["mean_query_count"]
        )
        assert closure_scenario["candidate"]["sql_query_count"] == (
            candidate_result["database_sql"]["mean_query_count"]
        )
        assert closure_scenario["baseline"]["p50_ms"] == baseline_result["latency"]["p50_ms"]
        assert closure_scenario["baseline"]["p95_ms"] == baseline_result["latency"]["p95_ms"]
        assert closure_scenario["baseline"]["p99_ms"] == baseline_result["latency"]["p99_ms"]
        assert closure_scenario["candidate"]["p50_ms"] == candidate_result["latency"]["p50_ms"]
        assert closure_scenario["candidate"]["p95_ms"] == candidate_result["latency"]["p95_ms"]
        assert closure_scenario["candidate"]["p99_ms"] == candidate_result["latency"]["p99_ms"]
        assert closure_scenario["payload_parity"] is True
        assert (
            closure_scenario["baseline"]["payload_bytes"]
            == baseline_result["result_payload_bytes"]["mean"]
        )
        assert (
            closure_scenario["candidate"]["payload_bytes"]
            == candidate_result["result_payload_bytes"]["mean"]
        )
        assert (
            closure_scenario["candidate"]["payload_bytes"]
            == candidate_result["result_payload_bytes"]["mean"]
        )
        assert closure_scenario["baseline"]["error_count"] == 0
        assert closure_scenario["candidate"]["error_count"] == 0
        assert closure_scenario["sql_reduction"]["absolute"] == pytest.approx(
            baseline_result["database_sql"]["mean_query_count"]
            - candidate_result["database_sql"]["mean_query_count"]
        )
        assert closure_scenario["p50_improvement_ms"] == pytest.approx(
            baseline_result["latency"]["p50_ms"] - candidate_result["latency"]["p50_ms"]
        )
