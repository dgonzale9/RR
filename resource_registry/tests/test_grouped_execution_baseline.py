"""Verify the Epic 3 grouped-execution benchmark contract without a database."""

from __future__ import annotations

import subprocess

import pytest

from resource_registry.benchmarks.grouped_execution import (
    CANONICAL_GROUPED_EXECUTION_SCENARIO_NAMES,
    GROUPED_EXECUTION_SCENARIO_BOUNDARIES,
    REQUIRED_GROUPED_EXECUTION_SCENARIO_NAMES,
)
from resource_registry.benchmarks.runner import get_benchmark_artifact_paths, run_scenario
from resource_registry.benchmarks.scenarios import BenchmarkScenario
from resource_registry.benchmarks.test_database import validate_isolated_database_target


def test_epic3_artifacts_are_separate_from_accepted_e1_and_e2_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Require RR-03.1 runs to use their dedicated result and report paths."""
    monkeypatch.setenv("RESOURCE_REGISTRY_BENCHMARK_ARTIFACT_SET", "epic3_grouped_baseline")

    results_path, report_path = get_benchmark_artifact_paths()

    assert results_path.name == "epic3_grouped_baseline_results.json"
    assert report_path.name == "epic3_grouped_baseline_report.md"


def test_required_grouped_execution_scenarios_have_explicit_valid_boundaries() -> None:
    """Require every RR-03.1 workload to declare its actual execution boundary."""
    assert set(REQUIRED_GROUPED_EXECUTION_SCENARIO_NAMES) == set(
        GROUPED_EXECUTION_SCENARIO_BOUNDARIES
    )
    assert set(GROUPED_EXECUTION_SCENARIO_BOUNDARIES.values()) <= {
        "registry_pre_dispatch_only",
        "shared_dispatch_stubbed_before_bif",
        "canonical_dispatch_included",
        "synthetic_projection_only",
        "not_measured",
    }
    assert set(CANONICAL_GROUPED_EXECUTION_SCENARIO_NAMES) <= {
        name
        for name, boundary in GROUPED_EXECUTION_SCENARIO_BOUNDARIES.items()
        if boundary == "canonical_dispatch_included"
    }


def test_benchmark_results_retain_the_explicit_execution_boundary() -> None:
    """Keep synthetic measurements from being presented as canonical dispatch timing."""
    result = run_scenario(
        BenchmarkScenario(
            "test.grouped_pre_dispatch",
            "execution",
            "synthetic",
            lambda: {"success": True},
            execution_boundary="registry_pre_dispatch_only",
        ),
        warmups=0,
        iterations=2,
    )

    assert result["execution_boundary"] == "registry_pre_dispatch_only"


def test_benchmark_scenarios_reject_missing_or_invalid_execution_boundaries() -> None:
    """Fail closed when a benchmark would otherwise obscure its execution boundary."""
    with pytest.raises(ValueError, match="execution boundary"):
        BenchmarkScenario("test.missing_boundary", "execution", "synthetic", lambda: None)
    with pytest.raises(ValueError, match="execution boundary"):
        BenchmarkScenario(
            "test.invalid_boundary",
            "execution",
            "synthetic",
            lambda: None,
            execution_boundary="unknown",  # type: ignore[arg-type]
        )


def test_database_benchmark_guard_rejects_unsafe_targets() -> None:
    """Reject a non-local or incorrectly named database before any benchmark setup occurs."""
    with pytest.raises(RuntimeError, match="isolated database"):
        validate_isolated_database_target(
            database_name="production",
            database_host="database.example.test",
            read_database_host="",
        )


def test_registered_stub_and_synthetic_scenarios_cannot_claim_canonical_dispatch() -> None:
    """Keep declared RR-03.1 substitutes distinguishable from real dispatcher measurements."""
    assert all(
        GROUPED_EXECUTION_SCENARIO_BOUNDARIES[name] != "canonical_dispatch_included"
        for name in REQUIRED_GROUPED_EXECUTION_SCENARIO_NAMES
        if name.startswith(("grouped.selection.", "grouped.validation.", "grouped.projection."))
    )


def test_rr03_artifact_work_does_not_modify_accepted_e1_or_e2_artifacts() -> None:
    """Keep baseline and E2 accepted result/report artifacts byte-for-byte untouched."""
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
        ],
        check=False,
    )

    assert completed.returncode == 0
