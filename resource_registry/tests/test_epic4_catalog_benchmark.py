"""Verify the RR-04.1 catalog and validator benchmark contract."""

from __future__ import annotations

from pathlib import Path

import pytest

from resource_registry.benchmarks.epic4_catalog import (
    EPIC4_CATALOG_SCENARIO_BOUNDARIES,
    EPIC4_DATABASE_SCENARIO_NAMES,
    build_epic4_memory_results,
    build_static_transformation_validation_scenarios,
    evaluate_catalog_optimization_gate,
    run_independent_process_validation_scenarios,
)
from resource_registry.benchmarks.reporting import render_report
from resource_registry.benchmarks.runner import get_benchmark_artifact_paths, run_scenario
from resource_registry.benchmarks.test_database import validate_isolated_database_target


def test_epic4_artifacts_are_isolated_from_accepted_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Require RR-04.1 runs to write only their dedicated artifact files.

    Args:
        monkeypatch: Fixture controlling the selected artifact set.
    """
    monkeypatch.setenv("RESOURCE_REGISTRY_BENCHMARK_ARTIFACT_SET", "epic4_catalog_baseline")

    results_path, report_path = get_benchmark_artifact_paths()

    assert results_path.name == "epic4_catalog_baseline_results.json"
    assert report_path.name == "epic4_catalog_baseline_report.md"


def test_epic4_registers_database_publication_and_process_boundaries() -> None:
    """Require RR-04.1 to disclose every non-synthetic execution boundary."""
    assert EPIC4_CATALOG_SCENARIO_BOUNDARIES["catalog.publication.one"] == (
        "registry_pre_dispatch_only"
    )
    assert EPIC4_CATALOG_SCENARIO_BOUNDARIES["catalog.concurrent.publication.25"] == (
        "isolated_database_threaded"
    )
    assert EPIC4_CATALOG_SCENARIO_BOUNDARIES["catalog.concurrent.multi_context.25"] == (
        "isolated_database_threaded"
    )
    assert EPIC4_CATALOG_SCENARIO_BOUNDARIES["catalog.security.capability_revoked_execution"] == (
        "canonical_dispatch_included"
    )
    assert EPIC4_CATALOG_SCENARIO_BOUNDARIES["catalog.security.workspace_disabled_execution"] == (
        "canonical_dispatch_included"
    )
    assert (
        EPIC4_CATALOG_SCENARIO_BOUNDARIES["catalog.security.dataset_visibility_revoked_execution"]
        == "canonical_dispatch_included"
    )
    assert EPIC4_CATALOG_SCENARIO_BOUNDARIES["catalog.process.validation.cold"] == (
        "independent_process_static_validation"
    )
    assert set(EPIC4_DATABASE_SCENARIO_NAMES) == {
        name
        for name in EPIC4_CATALOG_SCENARIO_BOUNDARIES
        if name.startswith(
            (
                "catalog.publication.",
                "catalog.freshness.",
                "catalog.concurrent.",
                "catalog.security.",
            )
        )
    }


def test_epic4_rejects_nonisolated_database_targets() -> None:
    """Require the existing benchmark guard before any RR-04.1 database work runs."""
    with pytest.raises(RuntimeError, match="isolated database"):
        validate_isolated_database_target(
            database_name="production",
            database_host="database.example.invalid",
            read_database_host="replica.example.invalid",
        )


def test_epic4_decision_gate_requires_actual_absolute_and_relative_evidence() -> None:
    """Reject RR-04.2 when a candidate comparison has not been measured."""
    decision = evaluate_catalog_optimization_gate(
        baseline_p50_ms=0.8,
        candidate_p50_ms=None,
    )

    assert decision["recommendation"] == "DEFER"
    assert decision["absolute_reduction_ms"] is None
    assert decision["relative_reduction_percent"] is None


def test_epic4_memory_records_cannot_be_mistaken_for_latency_measurements() -> None:
    """Require allocation evidence to remain separate from normal timing data."""
    results = build_epic4_memory_results()

    assert all(result["latency"] is None for result in results)
    assert all(result["category"] == "memory" for result in results)
    assert all(result["status"] != "measured" or result["memory"] is not None for result in results)


def test_epic4_static_validation_payloads_are_accepted_or_rejected_as_declared() -> None:
    """Require source-controlled valid and invalid schema fixtures to be executable."""
    results = [
        run_scenario(scenario, warmups=0, iterations=1)
        for scenario in build_static_transformation_validation_scenarios()
    ]

    assert all(result["status"] == "measured" for result in results)


def test_epic4_independent_validation_uses_fresh_subprocesses() -> None:
    """Require the worker evidence to come from actual separate Python processes."""
    results = run_independent_process_validation_scenarios(
        warmups=0,
        iterations=1,
    )
    warm_result = next(
        result for result in results if result["name"] == "catalog.process.validation.warm"
    )

    assert warm_result["kind"] == "subprocess_static_validation"
    assert warm_result["status"] == "measured"
    process = warm_result["process"]
    assert isinstance(process, dict)
    assert process["process_launcher"] == "subprocess"
    assert process["state_shared_between_trials"] is False


def test_epic4_report_renders_memory_and_decision_gate_from_json_data() -> None:
    """Require the dedicated report to distinguish evidence types and defer RR-04.2."""
    decision = evaluate_catalog_optimization_gate(
        baseline_p50_ms=0.8,
        candidate_p50_ms=None,
    )
    report = render_report(
        {
            "artifact_set": "epic4_catalog_baseline",
            "rr04_optimization_gate": decision,
        },
        list(build_epic4_memory_results()),
    )

    assert "Epic 4 catalog and validator evidence baseline" in report
    assert "Allocation and retained memory" in report
    assert "RR-04.2 optimization decision gate" in report
    assert "DEFER" in report


def test_epic4_report_renders_multi_context_trial_evidence() -> None:
    """Require the report to retain the three-trial multi-context evidence table."""
    report = render_report(
        {
            "artifact_set": "epic4_catalog_baseline",
            "epic4_catalog_trial_method": "Three isolated PostgreSQL trials.",
            "epic4_catalog_trials": [
                {
                    "trial": 1,
                    "results": [
                        {
                            "name": "catalog.concurrent.multi_context.1",
                            "latency": {"p50_ms": 1.0},
                            "stages": {
                                "registry_tool_publication": {
                                    "mean_db_query_count": 2.0,
                                }
                            },
                            "outcomes": {"success": 120},
                            "sample_count": 120,
                            "error_count": 0,
                        }
                    ],
                }
            ],
        },
        [],
    )

    assert "Catalog trial consistency" in report
    assert "catalog.concurrent.multi_context.1" in report


def test_epic4_introduces_no_production_validator_or_publication_cache() -> None:
    """Reject an unapproved production cache in the audited Registry paths."""
    project_root = Path(__file__).resolve().parents[2]
    production_paths = (
        project_root / "resource_registry/bifs/grouped_publication.py",
        project_root / "resource_registry/bifs/grouped_tools.py",
        project_root / "resource_registry/bifs/transformation/schemas.py",
    )

    assert all("lru_cache" not in path.read_text() for path in production_paths)
