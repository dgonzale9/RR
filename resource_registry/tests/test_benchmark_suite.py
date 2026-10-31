"""Verify the Registry benchmark contract without external services."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import cast

import pytest

from resource_registry.benchmarks.fixtures import build_routing_cases
from resource_registry.benchmarks.grouped_execution import (
    GROUPED_EXECUTION_FOLLOW_ON_STORY_RECOMMENDATIONS,
)
from resource_registry.benchmarks.reporting import render_report, summarize_latency_ns
from resource_registry.benchmarks.runner import (
    DATABASE_ADAPTER_ROUTING_SCENARIO_NAMES,
    get_benchmark_artifact_paths,
    run_scenario,
    temporary_performance_configuration,
)
from resource_registry.benchmarks.scenarios import BenchmarkScenario, build_scenarios
from resource_registry.performance.instrumentation import get_registry_performance_configuration


def test_nearest_rank_percentiles_do_not_interpolate() -> None:
    summary = summarize_latency_ns([1_000_000, 2_000_000, 3_000_000, 4_000_000])

    assert summary is not None
    assert summary["p50_ms"] == 2.0
    assert summary["p95_ms"] == 4.0
    assert summary["p99_ms"] == 4.0
    assert summary["tail_percentiles_unstable"] is True


def test_scenario_registry_has_unique_names_and_required_groups() -> None:
    scenarios = build_scenarios()
    names = [scenario.name for scenario in scenarios]

    assert len(names) == len(set(names))
    assert {"routing.listing", "publication.full", "validation.invalid"} <= set(names)
    assert {"routing", "publication", "validation", "execution"} <= {
        scenario.category for scenario in scenarios
    }


def test_database_adapter_scenarios_are_registered_for_the_isolated_baseline() -> None:
    """Require every Epic 2 adapter workload in the database-backed suite."""
    assert DATABASE_ADAPTER_ROUTING_SCENARIO_NAMES == (
        "routing.adapter_listing_authorized",
        "routing.adapter_schema_authorized",
        "routing.adapter_workspace_ambiguous",
        "routing.adapter_revoked_before_bif",
    )


def test_e21_candidate_artifacts_do_not_replace_the_accepted_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Require post-optimization measurements to use distinct Registry-only artifacts."""
    monkeypatch.setenv("RESOURCE_REGISTRY_BENCHMARK_ARTIFACT_SET", "e21_candidate")

    results_path, report_path = get_benchmark_artifact_paths()

    assert results_path.name == "e21_candidate_results.json"
    assert report_path.name == "e21_candidate_report.md"


def test_e23_verification_artifacts_remain_separate_from_accepted_measurements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Require final Epic 2 verification to preserve both accepted comparison inputs."""
    monkeypatch.setenv("RESOURCE_REGISTRY_BENCHMARK_ARTIFACT_SET", "e23_verification")

    results_path, report_path = get_benchmark_artifact_paths()

    assert results_path.name == "e23_verification_results.json"
    assert report_path.name == "e23_verification_report.md"
    assert "# Resource Registry E2.3 verification benchmark" in render_report(
        {"artifact_set": "e23_verification"}, []
    )


def test_epic3_report_describes_canonical_database_concurrency() -> None:
    """Describe the RR-03.1 execution boundary without reusing routing-only wording."""
    report = render_report(
        {"artifact_set": "epic3_grouped_baseline"},
        [],
    )

    assert "delegates to `super().callGenericFunction()`" in report
    assert "independent request/session state" in report
    assert "pure routing only" not in report


def test_epic3_report_maps_follow_on_stories_to_measured_recommendations() -> None:
    """Render the approved RR-03.2 and RR-03.3 recommendations without swapping them."""
    report = render_report(
        {
            "artifact_set": "epic3_grouped_baseline",
            "follow_on_story_recommendations": [
                dict(recommendation)
                for recommendation in GROUPED_EXECUTION_FOLLOW_ON_STORY_RECOMMENDATIONS
            ],
        },
        [],
    )

    assert "RR-03.2 — Result Normalization Reduction: **SKIP**" in report
    assert "RR-03.3 — Selected-Action Validator Reuse: **DEFER**" in report
    assert report.index("### Follow-on story recommendations") < report.index(
        "## Future comparison"
    )


def test_authorization_context_builds_are_counted_per_measured_operation() -> None:
    """Expose duplicate E1.1 authorization-context construction in a scenario result."""
    performance_logger = logging.getLogger("resource_registry.performance")

    def emit_duplicate_authorization_events() -> None:
        for _ in range(2):
            performance_logger.info(
                json.dumps(
                    {
                        "event": "resource_registry.performance",
                        "stage": "authorization_context_build",
                        "duration_ms": 1.0,
                        "db_query_count": 1,
                        "db_duration_ms": 0.5,
                        "db_alias_count": 1,
                    }
                )
            )

    result = run_scenario(
        BenchmarkScenario(
            "test.duplicate_auth",
            "quality",
            "synthetic",
            emit_duplicate_authorization_events,
            execution_boundary="registry_pre_dispatch_only",
        ),
        warmups=1,
        iterations=2,
    )

    assert result["status"] == "measured"
    assert result["authorization_context_build_count_per_operation"] == 2.0


def test_adapter_report_labels_shared_dispatch_stubbing_and_trial_consistency() -> None:
    """Keep adapter execution boundaries and three-trial evidence visible in Markdown."""
    result = {
        "name": "routing.adapter_listing_authorized",
        "category": "routing",
        "kind": "isolated_database",
        "status": "measured",
        "sample_count": 120,
        "error_count": 0,
        "latency": {"p50_ms": 1.0, "p95_ms": 2.0, "p99_ms": 3.0},
        "stages": {"deterministic_routing": {"mean_db_query_count": 4.0}},
        "authorization_context_build_count_per_operation": 2.0,
        "execution_boundary": "shared_dispatch_stubbed_before_bif",
    }
    report = render_report(
        {
            "git_revision": "test",
            "dirty": True,
            "adapter_routing_trial_method": "three isolated fixture trials",
            "adapter_routing_trials": [
                {"trial": trial, "results": [result]} for trial in range(1, 4)
            ],
        },
        [result],
    )

    assert "shared_dispatch_stubbed_before_bif" in report
    assert "auth contexts / operation" in report
    assert "Adapter routing trial consistency" in report


def test_candidate_report_renders_pre_optimization_adapter_comparison() -> None:
    """Require candidate artifacts to display factual E2.1 before/after measurements."""
    comparison = {
        "name": "routing.adapter_listing_authorized",
        "before": {
            "p50_ms": 60.0,
            "p95_ms": 90.0,
            "p99_ms": 100.0,
            "sql_query_count": 35.0,
            "sql_duration_ms": 36.0,
            "authorization_context_build_count_per_operation": 2.0,
            "error_count": 0.0,
        },
        "after": {
            "p50_ms": 45.0,
            "p95_ms": 70.0,
            "p99_ms": 80.0,
            "sql_query_count": 20.0,
            "sql_duration_ms": 22.0,
            "authorization_context_build_count_per_operation": 1.0,
            "error_count": 0.0,
        },
        "percentage_difference": {
            "p50_ms": -25.0,
            "p95_ms": -22.2,
            "p99_ms": -20.0,
            "sql_query_count": -42.9,
            "sql_duration_ms": -38.9,
            "authorization_context_build_count_per_operation": -50.0,
            "error_count": None,
        },
    }

    report = render_report(
        {
            "artifact_set": "e21_candidate",
            "pre_optimization_adapter_comparison": [comparison],
        },
        [],
    )

    assert "Resource Registry E2.1 candidate benchmark" in report
    assert "Pre-E2.1 adapter comparison" in report
    assert "35.000 → 20.000 / -42.900%" in report


def test_failed_scenario_is_reported_without_exception_details() -> None:
    def fail_safely() -> None:
        raise ValueError("private dataset and credential")

    result = run_scenario(
        BenchmarkScenario(
            "test.failure",
            "quality",
            "synthetic",
            fail_safely,
            execution_boundary="registry_pre_dispatch_only",
        ),
        warmups=0,
        iterations=2,
    )

    assert result["status"] == "failed"
    assert result["error_count"] == 2
    assert result["latency"] is None
    assert "private" not in json.dumps(result)


def test_failed_outcome_classifier_is_reported_without_omission() -> None:
    def fail_to_classify(_value: object) -> str:
        raise ValueError("private classifier details")

    result = run_scenario(
        BenchmarkScenario(
            "test.classifier_failure",
            "quality",
            "synthetic",
            lambda: 1,
            fail_to_classify,
            "registry_pre_dispatch_only",
        ),
        warmups=0,
        iterations=2,
    )

    assert result["status"] == "failed"
    assert result["error_count"] == 2
    assert "private" not in json.dumps(result)


def test_missing_metrics_are_unavailable_in_report() -> None:
    result = run_scenario(
        BenchmarkScenario(
            "test.no_metrics",
            "quality",
            "synthetic",
            lambda: None,
            execution_boundary="registry_pre_dispatch_only",
        ),
        warmups=0,
        iterations=2,
    )
    report = render_report({"git_revision": "test", "dirty": True}, [result])

    assert "unavailable" in report
    assert "test.no_metrics" in report


def test_routing_fixtures_are_deterministic() -> None:
    first = build_routing_cases()
    second = build_routing_cases()

    assert first == second
    assert first["listing"].tenant_id == "benchmark-tenant"


def test_invalid_run_configuration_is_rejected() -> None:
    with pytest.raises(ValueError):
        run_scenario(
            BenchmarkScenario(
                "test.invalid",
                "quality",
                "synthetic",
                lambda: None,
                execution_boundary="registry_pre_dispatch_only",
            ),
            warmups=-1,
            iterations=1,
        )


def test_benchmark_configuration_restores_process_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_ENABLED", "false")
    with temporary_performance_configuration(enabled=True, sql_enabled=True, payload_enabled=True):
        configuration = get_registry_performance_configuration()
        assert configuration.enabled is True
        assert configuration.sql_enabled is True
        assert configuration.payload_enabled is True
    assert get_registry_performance_configuration().enabled is False


def test_publication_records_actual_e13_payload_counts() -> None:
    publication = next(item for item in build_scenarios() if item.name == "publication.full")
    with temporary_performance_configuration(enabled=True, sql_enabled=True, payload_enabled=True):
        result = run_scenario(publication, warmups=1, iterations=2)

    assert result["status"] == "measured"
    stage = cast(Mapping[str, Mapping[str, object]], result["stages"])["registry_tool_publication"]
    assert stage["action_count"] == 31
    assert stage["tool_count"] == 4
    assert isinstance(stage["payload_bytes"], int) and stage["payload_bytes"] > 0
    assert stage["mean_db_query_count"] == 0


def test_report_shows_payload_complexity_concurrency_and_overhead() -> None:
    report = render_report(
        {"git_revision": "test", "dirty": True},
        [
            {
                "name": "publication.one",
                "category": "publication",
                "kind": "synthetic",
                "status": "measured",
                "sample_count": 2,
                "error_count": 0,
                "latency": {"p50_ms": 1.0, "p95_ms": 2.0, "p99_ms": 2.0},
                "stages": {
                    "registry_tool_publication": {
                        "tool_count": 1,
                        "action_count": 1,
                        "schema_property_count": 5,
                        "description_char_count": 100,
                        "payload_bytes": 512,
                    }
                },
            },
            {
                "name": "concurrency.routing_10",
                "category": "concurrency",
                "kind": "synthetic_threaded",
                "status": "measured",
                "sample_count": 10,
                "error_count": 0,
                "latency": {"p50_ms": 1.0, "p95_ms": 2.0, "p99_ms": 2.0},
                "stages": {},
                "throughput_ops_per_second": 100.0,
            },
            {
                "name": "overhead.e11",
                "category": "overhead",
                "kind": "synthetic",
                "status": "measured",
                "sample_count": 10,
                "error_count": 0,
                "latency": {"p50_ms": 1.0, "p95_ms": 2.0, "p99_ms": 2.0},
                "stages": {},
                "p50_delta_ms": 0.1,
                "p50_delta_percent": 10.0,
            },
            {
                "name": "routing.bridge_failure",
                "category": "routing",
                "kind": "integration",
                "status": "not_measured",
                "reason": "Shared bridge unavailable.",
                "sample_count": 0,
                "error_count": 0,
                "latency": None,
                "stages": {},
            },
        ],
    )

    assert "schema_property_count" in report
    assert "description_char_count" in report
    assert "throughput_ops_per_second" in report
    assert "p50_delta_percent" in report
    assert "Unmeasured: routing.bridge_failure" in report
