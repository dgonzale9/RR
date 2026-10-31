"""Verify the RR-09 final evidence-consolidation closure contract."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from resource_registry.benchmarks.epic6_grouped_result import (
    EPIC6_CANONICAL_SCENARIO_NAMES,
    EPIC6_CONCURRENCY_SCENARIO_NAMES,
    EPIC6_PROJECTION_SCENARIO_NAMES,
)
from resource_registry.benchmarks.epic6_grouped_result_closure import (
    EPIC6_CLOSURE_MATRIX_PATH,
    EPIC6_CLOSURE_REPORT_PATH,
    HISTORICAL_ACCEPTED_ARTIFACT_PATHS,
    build_epic6_closure_matrix,
    render_epic6_closure_report,
)
from resource_registry.benchmarks.epic6_grouped_result_memory import (
    EPIC6_MEMORY_CANONICAL_SCENARIO_NAMES,
    EPIC6_MEMORY_CONCURRENCY_SCENARIO_NAMES,
    EPIC6_MEMORY_SINGLE_SCENARIO_NAMES,
)


def test_epic6_closure_matrix_matches_the_frozen_artifact_extraction() -> None:
    """Require the committed closure matrix to be a complete rendering of frozen evidence."""
    matrix = build_epic6_closure_matrix()
    committed_matrix = json.loads(EPIC6_CLOSURE_MATRIX_PATH.read_text())

    assert committed_matrix == matrix
    assert matrix["status"] == "CLOSE — EPIC 6 ACCEPTANCE SATISFIED"
    expected_decision = {
        "recommendation": "SKIP",
        "candidate_implementation_measured": False,
        "optimization_savings_inferred": False,
        "rr_09_1_gate_status": "PROPOSED_NOT_APPROVED",
        "rr_09_1_gate_formally_approved": False,
    }
    decision = _get_mapping(matrix, "rr_09_3_decision")
    assert {key: decision[key] for key in expected_decision} == expected_decision


def test_epic6_closure_matrix_references_every_actual_rr091_and_rr092_scenario() -> None:
    """Require closure data to preserve scenario IDs and boundaries from both frozen artifacts."""
    matrix = build_epic6_closure_matrix()
    performance = _get_mapping(matrix, "rr_09_1_performance")
    memory = _get_mapping(matrix, "rr_09_2_memory")

    direct_projection = _get_object_rows(performance, "direct_projection")
    performance_concurrency = _get_object_rows(performance, "concurrency")
    canonical_performance = _get_object_rows(performance, "canonical_dataset_list")
    memory_single_operation = _get_object_rows(memory, "single_operation")
    memory_repeated_retention = _get_object_rows(memory, "repeated_retention")
    memory_concurrency = _get_object_rows(memory, "concurrency")
    canonical_memory = _get_object_rows(memory, "canonical_dataset_list")

    assert {row["scenario_id"] for row in direct_projection} == set(EPIC6_PROJECTION_SCENARIO_NAMES)
    assert {row["scenario_id"] for row in performance_concurrency} == set(
        EPIC6_CONCURRENCY_SCENARIO_NAMES
    )
    assert {row["scenario_id"] for row in canonical_performance} == {
        name for name in EPIC6_CANONICAL_SCENARIO_NAMES if ".dataset_list." in name
    }
    assert {row["scenario_id"] for row in memory_single_operation} == set(
        EPIC6_MEMORY_SINGLE_SCENARIO_NAMES
    )
    assert {row["scenario_id"] for row in memory_concurrency} == set(
        EPIC6_MEMORY_CONCURRENCY_SCENARIO_NAMES
    )
    assert {row["scenario_id"] for row in canonical_memory} == set(
        EPIC6_MEMORY_CANONICAL_SCENARIO_NAMES
    )
    scenario_row_groups = (
        direct_projection,
        performance_concurrency,
        canonical_performance,
        memory_single_operation,
        memory_repeated_retention,
        memory_concurrency,
        canonical_memory,
    )
    assert all(row["source_artifact"] for rows in scenario_row_groups for row in rows)


def test_epic6_closure_report_is_rendered_from_the_committed_matrix() -> None:
    """Require the report to describe the actual machine-readable closure decision."""
    matrix = build_epic6_closure_matrix()
    committed_report = EPIC6_CLOSURE_REPORT_PATH.read_text()

    assert committed_report == render_epic6_closure_report(matrix)
    for heading in (
        "## 1. Executive summary",
        "## 2. Epic 6 Story completion status",
        "## 3. RR-09.1 performance findings",
        "## 4. RR-09.2 memory findings",
        "## 5. Canonical workload interpretation",
        "## 6. Concurrency interpretation",
        "## 7. RR-09.3 decision and rationale",
        "## 8. Security and behavior verification",
        "## 9. Historical artifact integrity",
        "## 10. Benchmark limitations",
        "## 11. Accepted residual risks",
        "## 12. Reopening criteria",
        "## 13. Verification commands and outcomes",
        "## 14. Final Epic 6 acceptance recommendation",
        "## 15. Six-Epic roadmap completion summary",
    ):
        assert heading in committed_report
    assert "**SKIP — No material Registry-owned bottleneck established**" in committed_report
    assert (
        "| Scenario | Result bytes | Records | Changed | p50 / p95 / p99 | Trial p50 range "
        "| Errors | Boundary | Source |"
    ) in committed_report


def test_epic6_closure_records_source_hashes_and_preserves_historical_artifacts() -> None:
    """Require closure sources to remain byte-stable and traceable to actual files."""
    matrix = build_epic6_closure_matrix()
    sources = _get_mapping(matrix, "evidence_sources")

    assert set(sources) == {path.name for path in HISTORICAL_ACCEPTED_ARTIFACT_PATHS}
    for path in HISTORICAL_ACCEPTED_ARTIFACT_PATHS:
        source = _get_mapping(sources, path.name)
        assert source["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()

    completed = subprocess.run(
        ["git", "diff", "--quiet", "HEAD", "--", *map(str, HISTORICAL_ACCEPTED_ARTIFACT_PATHS)],
        check=False,
    )

    assert completed.returncode == 0


def test_epic6_closure_artifacts_are_dedicated_new_files() -> None:
    """Require the final closure to avoid overwriting accepted RR-09 artifacts."""
    benchmark_directory = Path(__file__).resolve().parents[1] / "benchmarks"

    assert (
        EPIC6_CLOSURE_MATRIX_PATH
        == benchmark_directory / "epic6_grouped_result_closure_matrix.json"
    )
    assert (
        EPIC6_CLOSURE_REPORT_PATH == benchmark_directory / "epic6_grouped_result_closure_report.md"
    )


def _get_mapping(payload: Mapping[str, object], key: str) -> Mapping[str, object]:
    """Return one test-validated mapping value.

    Args:
        payload: JSON-safe object under test.
        key: Required mapping field.

    Returns:
        The mapping stored at ``key``.
    """
    value = payload[key]
    assert isinstance(value, Mapping)
    return value


def _get_object_rows(payload: Mapping[str, object], key: str) -> list[Mapping[str, object]]:
    """Return test-validated JSON object rows.

    Args:
        payload: JSON-safe object under test.
        key: Required list-of-object field.

    Returns:
        Mapping rows stored at ``key``.
    """
    value = payload[key]
    assert isinstance(value, list)
    assert all(isinstance(row, Mapping) for row in value)
    return cast(list[Mapping[str, object]], value)
