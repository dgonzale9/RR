"""Verify the RR-09.1 grouped-result scaling benchmark contract."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from resource_registry.benchmarks.epic6_grouped_result import (
    EPIC6_CANONICAL_SCENARIO_NAMES,
    EPIC6_CONCURRENCY_SCENARIO_NAMES,
    EPIC6_GROUPED_RESULT_ARTIFACT_SET,
    EPIC6_PROJECTION_SCENARIO_NAMES,
    EPIC6_SCENARIO_BOUNDARIES,
    build_epic6_projection_fixtures,
    build_epic6_projection_scenarios,
    evaluate_epic6_materiality_gate,
)
from resource_registry.benchmarks.reporting import render_report
from resource_registry.benchmarks.runner import get_benchmark_artifact_paths, run_scenario
from resource_registry.bifs.grouped_tools import project_registry_tool_result


def test_epic6_artifacts_are_isolated_from_accepted_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Require RR-09.1 runs to target only their dedicated artifact paths.

    Args:
        monkeypatch: Fixture that controls the selected artifact set.
    """
    monkeypatch.setenv(
        "RESOURCE_REGISTRY_BENCHMARK_ARTIFACT_SET", EPIC6_GROUPED_RESULT_ARTIFACT_SET
    )

    results_path, report_path = get_benchmark_artifact_paths()

    assert results_path.name == "epic6_grouped_result_baseline_results.json"
    assert report_path.name == "epic6_grouped_result_baseline_report.md"


def test_epic6_projection_inventory_covers_required_shapes_and_cardinalities() -> None:
    """Require bounded direct-projection cases for every RR-09.1 payload class."""
    fixtures = build_epic6_projection_fixtures()
    fixture_names = {fixture.name for fixture in fixtures}

    assert set(EPIC6_PROJECTION_SCENARIO_NAMES) == fixture_names
    for cardinality in (1, 50, 100):
        assert f"grouped_result.projection.unchanged.records_{cardinality:03d}" in fixture_names
        for metadata_field in ("hint", "error", "suggested_bif", "recommended_bif"):
            assert (
                f"grouped_result.projection.rewritten.{metadata_field}.records_{cardinality:03d}"
                in fixture_names
            )
    assert {
        "grouped_result.projection.structured.large_array",
        "grouped_result.projection.structured.deep_nesting",
        "grouped_result.projection.structured.high_cardinality_metadata",
        "grouped_result.projection.structured.schema_like",
        "grouped_result.projection.structured.long_hint",
        "grouped_result.projection.structured.long_error",
        "grouped_result.projection.structured.mixed_success_error",
        "grouped_result.projection.unusual.malformed_json",
        "grouped_result.projection.unusual.scalar_json",
        "grouped_result.projection.unusual.array_json",
        "grouped_result.projection.unusual.empty_result",
        "grouped_result.projection.unusual.unknown_reference",
        "grouped_result.projection.unusual.already_canonical_reference",
        "grouped_result.projection.unusual.similar_name",
        "grouped_result.projection.unusual.business_data_reference",
    } <= fixture_names


def test_epic6_projection_fixtures_prove_existing_behavior_parity() -> None:
    """Preserve existing projection output without modifying production behavior."""
    for fixture in build_epic6_projection_fixtures():
        assert project_registry_tool_result(fixture.payload) == fixture.expected_result


def test_epic6_direct_projection_records_payload_and_rewrite_metadata() -> None:
    """Keep direct timing distinct from canonical execution and disclose payload shape."""
    scenario = next(
        item
        for item in build_epic6_projection_scenarios()
        if item.name == "grouped_result.projection.rewritten.hint.records_100"
    )

    result = run_scenario(scenario, warmups=0, iterations=2)

    assert result["execution_boundary"] == "synthetic_projection_only"
    assert result["status"] == "measured"
    assert result["scenario_metadata"] == {
        "input_payload_bytes": scenario.metadata["input_payload_bytes"],
        "record_count": 100,
        "projection_changes_result": True,
        "measurement_boundary": "direct_production_projection_function",
    }


def test_epic6_declares_canonical_and_concurrent_boundaries_without_relabeling() -> None:
    """Keep direct, canonical, and threaded measurements distinguishable."""
    assert set(EPIC6_SCENARIO_BOUNDARIES) == {
        *EPIC6_PROJECTION_SCENARIO_NAMES,
        *EPIC6_CANONICAL_SCENARIO_NAMES,
        *EPIC6_CONCURRENCY_SCENARIO_NAMES,
    }
    assert all(
        EPIC6_SCENARIO_BOUNDARIES[name] == "synthetic_projection_only"
        for name in EPIC6_PROJECTION_SCENARIO_NAMES
    )
    assert all(
        EPIC6_SCENARIO_BOUNDARIES[name] == "canonical_dispatch_included"
        for name in EPIC6_CANONICAL_SCENARIO_NAMES
    )
    assert all(
        EPIC6_SCENARIO_BOUNDARIES[name] == "synthetic_projection_only"
        for name in EPIC6_CONCURRENCY_SCENARIO_NAMES
    )


def test_epic6_materiality_gate_is_proposed_not_product_approval() -> None:
    """Forbid RR-09.3 approval when no candidate implementation was measured."""
    decision = evaluate_epic6_materiality_gate()

    assert decision["status"] == "PROPOSED_NOT_APPROVED"
    assert decision["recommendation"] == "DEFER"
    assert decision["candidate_implementation_measured"] is False
    assert decision["absolute_latency_reduction_ms"] is None
    assert decision["relative_latency_reduction_percent"] is None


def test_epic6_report_keeps_payload_shape_and_gate_visible() -> None:
    """Render source bytes, rewrite state, trial evidence, and the proposed gate."""
    report = render_report(
        {
            "artifact_set": EPIC6_GROUPED_RESULT_ARTIFACT_SET,
            "epic6_direct_projection_trial_method": "Three direct trials.",
            "epic6_direct_projection_trials": [
                {
                    "trial": 1,
                    "results": [
                        {
                            "name": "grouped_result.projection.unchanged.records_001",
                            "latency": {"p50_ms": 0.01, "p95_ms": 0.02, "p99_ms": 0.03},
                            "sample_count": 120,
                            "error_count": 0,
                        }
                    ],
                }
            ],
            "epic6_canonical_trial_method": "Three guarded PostgreSQL trials.",
            "epic6_canonical_trials": [
                {
                    "trial": 1,
                    "results": [
                        {
                            "name": "grouped_result.canonical.dataset_list.records_001",
                            "latency": {"p50_ms": 1.01, "p95_ms": 1.02, "p99_ms": 1.03},
                            "sample_count": 120,
                            "error_count": 0,
                            "scenario_metadata": {
                                "canonical_dispatch_call_count": 125,
                            },
                            "observed_result_metadata": {"returned_record_count": 1},
                        }
                    ],
                }
            ],
            "epic6_materiality_gate": evaluate_epic6_materiality_gate(),
        },
        [
            {
                "name": "grouped_result.projection.unchanged.records_001",
                "category": "projection",
                "kind": "synthetic_direct_production_function",
                "status": "measured",
                "sample_count": 120,
                "error_count": 0,
                "latency": {"p50_ms": 0.01, "p95_ms": 0.02, "p99_ms": 0.03},
                "stages": {},
                "database_sql": None,
                "result_payload_bytes": {"mean": 100.0},
                "authorization_context_build_count_per_operation": None,
                "execution_boundary": "synthetic_projection_only",
                "scenario_metadata": {
                    "input_payload_bytes": 100,
                    "record_count": 1,
                    "projection_changes_result": False,
                },
            }
        ],
    )

    assert "Epic 6 grouped-result scaling baseline" in report
    assert "RR-09.1 payload and projection matrix" in report
    assert "RR-09.1 direct projection trial consistency" in report
    assert "RR-09.1 canonical dataset-list trial consistency" in report
    assert "RR-09.1 proposed performance decision gate" in report
    assert "PROPOSED_NOT_APPROVED" in report


def test_epic6_artifact_work_does_not_modify_historical_artifacts() -> None:
    """Keep accepted Epic 1–5 reports and results byte-for-byte unchanged."""
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


def test_epic6_artifact_contains_direct_and_canonical_postgresql_evidence() -> None:
    """Require direct evidence and the three guarded canonical dataset-list measurements."""
    artifact_path = (
        Path(__file__).resolve().parents[1]
        / "benchmarks"
        / "epic6_grouped_result_baseline_results.json"
    )
    payload = json.loads(artifact_path.read_text())
    metadata = payload["metadata"]
    results = payload["results"]
    direct_names = {*EPIC6_PROJECTION_SCENARIO_NAMES, *EPIC6_CONCURRENCY_SCENARIO_NAMES}
    canonical_dataset_list_names = {
        name
        for name in EPIC6_CANONICAL_SCENARIO_NAMES
        if name.startswith("grouped_result.canonical.dataset_list.")
    }
    unsupported_canonical_names = set(EPIC6_CANONICAL_SCENARIO_NAMES) - canonical_dataset_list_names
    results_by_name = {result["name"]: result for result in results}

    assert metadata["artifact_set"] == EPIC6_GROUPED_RESULT_ARTIFACT_SET
    assert metadata["warmups"] == 5
    assert metadata["iterations"] == 120
    assert metadata["epic6_direct_projection_trial_count"] == 3
    assert len(metadata["epic6_direct_projection_trials"]) == 3
    assert metadata["epic6_canonical_execution_status"] == "measured_guarded_isolated_postgresql"
    assert metadata["epic6_canonical_trial_count"] == 3
    assert len(metadata["epic6_canonical_trials"]) == 3
    assert metadata["epic6_database_guard"] == {
        "benchmark_opt_in": True,
        "configured_database_host": "127.0.0.1",
        "configured_database_name": "RegistryE14Isolated",
        "django_database_alias": "default",
        "read_database_host": "",
        "validated": True,
    }
    assert set(results_by_name) == {
        *direct_names,
        *EPIC6_CANONICAL_SCENARIO_NAMES,
    }
    assert all(
        results_by_name[name]["status"] == "measured"
        and results_by_name[name]["error_count"] == 0
        and results_by_name[name]["result_payload_bytes"] is not None
        for name in direct_names
    )
    assert all(
        {result["name"] for result in trial["results"]} == direct_names
        and all(
            result["status"] == "measured" and result["error_count"] == 0
            for result in trial["results"]
        )
        for trial in metadata["epic6_direct_projection_trials"]
    )
    for name in canonical_dataset_list_names:
        result = results_by_name[name]
        assert result["status"] == "measured"
        assert result["reason"] is None
        assert result["warmups"] == 5
        assert result["iterations"] == 120
        assert result["sample_count"] == 120
        assert result["error_count"] == 0
        assert result["execution_boundary"] == "canonical_dispatch_included"
        assert set(result["latency"]) >= {"p50_ms", "p95_ms", "p99_ms"}
        assert result["result_payload_bytes"]["mean"] > 0
        assert result["database_sql"]["measurement_scope"] == "whole_operation"
        assert result["database_sql"]["mean_query_count"] > 0
        assert result["database_sql"]["mean_duration_ms"] >= 0
        assert set(result["stages"]) >= {"grouped_call_total", "grouped_result_projection"}
        assert result["stages"]["grouped_call_total"]["event_count"] == 120
        assert result["stages"]["grouped_result_projection"]["event_count"] == 120
        assert result["stages"]["grouped_call_total"]["mean_duration_ms"] >= 0
        assert result["stages"]["grouped_result_projection"]["mean_duration_ms"] >= 0
        assert result["scenario_metadata"]["canonical_dispatch_call_count"] == 125
        assert result["observed_result_metadata"]["returned_record_count"] == (
            result["scenario_metadata"]["record_count"]
        )
    assert all(
        results_by_name[name]["status"] == "not_measured" and results_by_name[name]["reason"]
        for name in unsupported_canonical_names
    )
    for trial in metadata["epic6_canonical_trials"]:
        trial_results = {result["name"]: result for result in trial["results"]}
        assert set(trial_results) == set(EPIC6_CANONICAL_SCENARIO_NAMES)
        assert all(
            trial_results[name]["status"] == "measured"
            and trial_results[name]["error_count"] == 0
            and trial_results[name]["scenario_metadata"]["canonical_dispatch_call_count"] == 125
            for name in canonical_dataset_list_names
        )
