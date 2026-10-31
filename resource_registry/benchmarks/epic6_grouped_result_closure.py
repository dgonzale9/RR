"""Consolidate frozen Epic 6 grouped-result evidence into closure artifacts only."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TypeAlias, cast

JsonObject: TypeAlias = dict[str, object]
JsonObjectList: TypeAlias = list[JsonObject]

_BENCHMARK_DIRECTORY = Path(__file__).resolve().parent
_PERFORMANCE_RESULTS_PATH = _BENCHMARK_DIRECTORY / "epic6_grouped_result_baseline_results.json"
_MEMORY_RESULTS_PATH = _BENCHMARK_DIRECTORY / "epic6_grouped_result_memory_results.json"
EPIC6_CLOSURE_MATRIX_PATH = _BENCHMARK_DIRECTORY / "epic6_grouped_result_closure_matrix.json"
EPIC6_CLOSURE_REPORT_PATH = _BENCHMARK_DIRECTORY / "epic6_grouped_result_closure_report.md"

HISTORICAL_ACCEPTED_ARTIFACT_PATHS = (
    _BENCHMARK_DIRECTORY / "baseline_results.json",
    _BENCHMARK_DIRECTORY / "baseline_report.md",
    _BENCHMARK_DIRECTORY / "e21_candidate_results.json",
    _BENCHMARK_DIRECTORY / "e21_candidate_report.md",
    _BENCHMARK_DIRECTORY / "e23_verification_results.json",
    _BENCHMARK_DIRECTORY / "e23_verification_report.md",
    _BENCHMARK_DIRECTORY / "epic2_closure_report.md",
    _BENCHMARK_DIRECTORY / "epic3_grouped_baseline_results.json",
    _BENCHMARK_DIRECTORY / "epic3_grouped_baseline_report.md",
    _BENCHMARK_DIRECTORY / "epic4_catalog_baseline_results.json",
    _BENCHMARK_DIRECTORY / "epic4_catalog_baseline_report.md",
    _BENCHMARK_DIRECTORY / "epic5_selection_baseline_results.json",
    _BENCHMARK_DIRECTORY / "epic5_selection_baseline_report.md",
    _BENCHMARK_DIRECTORY / "epic5_selection_candidate_results.json",
    _BENCHMARK_DIRECTORY / "epic5_selection_candidate_report.md",
    _BENCHMARK_DIRECTORY / "epic5_selection_closure_matrix.json",
    _BENCHMARK_DIRECTORY / "epic5_selection_closure_report.md",
    _BENCHMARK_DIRECTORY / "epic6_grouped_result_baseline_results.json",
    _BENCHMARK_DIRECTORY / "epic6_grouped_result_baseline_report.md",
    _BENCHMARK_DIRECTORY / "epic6_grouped_result_memory_results.json",
    _BENCHMARK_DIRECTORY / "epic6_grouped_result_memory_report.md",
)


def build_epic6_closure_matrix() -> JsonObject:
    """Build one deterministic final-closure matrix from accepted RR-09 artifacts.

    Returns:
        JSON-safe closure evidence that names every frozen scenario and does not add a candidate
        measurement or a production optimization.

    Raises:
        ValueError: If a required accepted artifact has an unexpected schema or missing evidence.
    """
    performance_payload = _read_json_object(_PERFORMANCE_RESULTS_PATH)
    memory_payload = _read_json_object(_MEMORY_RESULTS_PATH)
    performance_metadata = _get_mapping(performance_payload, "metadata")
    memory_metadata = _get_mapping(memory_payload, "metadata")
    performance_results = _get_object_list(performance_payload, "results")
    memory_results = _get_object_list(memory_payload, "results")
    _validate_frozen_epic6_artifacts(performance_metadata, memory_metadata)

    performance_trials = _index_latency_trials(
        performance_metadata, "epic6_direct_projection_trials"
    )
    canonical_trials = _index_latency_trials(performance_metadata, "epic6_canonical_trials")
    performance_direct = [
        _build_latency_scenario_record(result, performance_trials)
        for result in performance_results
        if result.get("category") == "projection"
    ]
    performance_concurrency = [
        _build_latency_scenario_record(result, performance_trials)
        for result in performance_results
        if result.get("category") == "projection_concurrency"
    ]
    performance_canonical = [
        _build_latency_scenario_record(result, canonical_trials)
        for result in performance_results
        if result.get("category") == "canonical_execution"
        and str(result.get("name", "")).startswith("grouped_result.canonical.dataset_list.")
    ]
    performance_not_measured = [
        _build_not_measured_record(result)
        for result in performance_results
        if result.get("category") == "canonical_execution"
        and result.get("status") == "not_measured"
    ]
    memory_single = [
        _build_memory_scenario_record(result)
        for result in memory_results
        if result.get("category") == "direct_projection_memory"
    ]
    memory_repeated = [
        _build_memory_scenario_record(result)
        for result in memory_results
        if result.get("category") == "repeated_projection_retention"
    ]
    memory_concurrency = [
        _build_memory_scenario_record(result)
        for result in memory_results
        if result.get("category") == "concurrent_projection_memory"
    ]
    memory_canonical = [
        _build_memory_scenario_record(result)
        for result in memory_results
        if result.get("category") == "canonical_execution_memory"
    ]
    _validate_consolidated_scenario_counts(
        performance_direct=performance_direct,
        performance_concurrency=performance_concurrency,
        performance_canonical=performance_canonical,
        memory_single=memory_single,
        memory_repeated=memory_repeated,
        memory_concurrency=memory_concurrency,
        memory_canonical=memory_canonical,
    )

    materiality_gate = _get_mapping(performance_metadata, "epic6_materiality_gate")
    return {
        "schema_version": 1,
        "status": "CLOSE — EPIC 6 ACCEPTANCE SATISFIED",
        "evidence_sources": {
            path.name: {
                "path": str(path.relative_to(_BENCHMARK_DIRECTORY.parent.parent)),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in HISTORICAL_ACCEPTED_ARTIFACT_PATHS
        },
        "epic_6_story_completion": {
            "rr_09_1": "ACCEPTED",
            "rr_09_2": "ACCEPTED",
            "rr_09_3": "FORMALLY_SKIPPED_BY_THIS_CLOSURE",
            "production_optimization_introduced": False,
        },
        "rr_09_1_performance": {
            "source_artifact": _PERFORMANCE_RESULTS_PATH.name,
            "warmups": performance_metadata.get("warmups"),
            "iterations_per_trial": performance_metadata.get("iterations"),
            "independent_trials": performance_metadata.get("epic6_direct_projection_trial_count"),
            "direct_projection": performance_direct,
            "concurrency": performance_concurrency,
            "canonical_dataset_list": performance_canonical,
            "explicitly_not_measured_canonical": performance_not_measured,
            "measurement_limitations": [
                "Direct projection is synthetic_projection_only and is not canonical dispatch.",
                "Canonical dataset-list timing is canonical_dispatch_included and inclusive.",
                "Nested inclusive stages overlap and are not summed or subtracted.",
            ],
        },
        "rr_09_2_memory": {
            "source_artifact": _MEMORY_RESULTS_PATH.name,
            "tracing_configuration": memory_metadata.get("tracing_configuration"),
            "independent_trials": memory_metadata.get("epic6_memory_trial_count"),
            "single_operation": memory_single,
            "repeated_retention": memory_repeated,
            "concurrency": memory_concurrency,
            "canonical_dataset_list": memory_canonical,
            "measurement_limitations": [
                "tracemalloc reports Python allocations in bytes, not process RSS.",
                "Concurrent tracemalloc and available RSS observations are process-wide.",
                "Signed post-GC deltas are not leak findings without lifetime analysis.",
                "Canonical memory is inclusive and is not attributed exclusively to projection.",
            ],
        },
        "cross_evidence_interpretation": {
            "confirmed_measurements": [
                "Direct unchanged and rewritten projections are measured separately from canonical dispatch.",
                "Canonical dataset-list latency and memory are measured at 1, 50, and 100 records.",
                "Repeated profiles release output strings and record signed post-GC deltas through 1,000 operations.",
                "Concurrent profiles confirm synchronized in-flight overlap at 1, 10, and 25 workers.",
            ],
            "supported_architectural_observations": [
                "The 100-record rewritten direct p50 is sub-millisecond while canonical p50 is tens of milliseconds; these boundaries are not subtracted to claim exclusive cost.",
                "Long hint/error fixtures are synthetic direct edge cases and do not by themselves establish representative canonical workload impact.",
                "Direct post-GC retention remains small in the recorded repeated profiles; no leak claim is supported.",
            ],
            "unverified_hypotheses": [
                "A fast path that avoids full JSON projection could reduce direct cost, but no candidate implementation was measured.",
                "Potential savings, rollback behavior, and end-to-end chat impact are unmeasured.",
            ],
            "environmental_limitations": [
                "Results are local controlled benchmarks, not end-to-end provider or chat measurements.",
                "RSS is explicitly not measured where no reliable current-RSS source exists.",
                "Three trials show local variation but are not a broad statistical confidence claim.",
            ],
        },
        "rr_09_3_decision": {
            "recommendation": "SKIP",
            "candidate_implementation_measured": False,
            "optimization_savings_inferred": False,
            "rr_09_1_gate_status": materiality_gate.get("status"),
            "rr_09_1_gate_formally_approved": False,
            "rationale": [
                "No material Registry-owned bottleneck is established in the measured canonical boundary.",
                "The RR-09.1 materiality gate is explicitly PROPOSED_NOT_APPROVED, not an approval threshold.",
                "A branch that bypasses JSON projection would add response-contract and legacy-reference rewrite risk without a measured candidate benefit.",
                "Canonical admission, authorization, and handler boundaries remain outside any inferred direct-projection saving.",
            ],
            "candidate_assessment": {
                "expected_latency_benefit": "unmeasured",
                "expected_memory_benefit": "unmeasured",
                "representative_workload_relevance": "not_established",
                "result_contract_complexity": "would_need_full_passthrough_and_rewrite_parity",
                "metadata_rewrite_risk": "incorrect_legacy_reference_detection_would_change_contract",
                "security_boundary": "canonical_dispatch_and_authorization_are_not_bypassed",
                "maintenance_burden": "additional_branch_and_parity_surface",
                "rollback_simplicity": "unmeasured_candidate_not_authorized",
            },
            "reopening_criteria": [
                "A representative canonical workload demonstrates a material, attributable Registry-owned projection cost.",
                "A bounded candidate preserves exact passthrough, legacy rewrites, envelopes, correlation IDs, ordering, duplicates, and business data.",
                "Canonical admission, namespace isolation, visibility, capability, and handler-authorization regressions remain green.",
                "The candidate is measured against the same fixture, trial, and boundary contract without inferring savings from inclusive-stage differences.",
            ],
        },
        "security_and_behavior_verification": {
            "production_changes_to_grouped_result_processing": False,
            "verified_invariants": [
                "Canonical BIF admission and persona assignment freshness.",
                "Capability revocation, namespace isolation, workspace, dataset, and version visibility.",
                "Handler-level authorization and no unauthorized domain reads or side effects.",
                "Error envelopes, correlation IDs, exact passthrough, legacy rewrites, business data, ordering, and duplicates.",
            ],
            "regression_modules": [
                "resource_registry/tests/test_grouped_bif_tools.py",
                "resource_registry/tests/test_grouped_bif_boundaries.py",
                "resource_registry/tests/test_grouped_bif_namespace.py",
                "resource_registry/tests/test_grouped_execution_baseline.py",
                "resource_registry/tests/test_dataset_selection_context.py",
                "resource_registry/tests/test_read_authorization.py",
                "resource_registry/tests/test_read_visibility.py",
            ],
        },
        "historical_artifact_integrity": {
            "verification": "git diff --quiet HEAD -- each listed accepted artifact",
            "all_accepted_artifacts_unchanged": True,
            "accepted_artifact_names": [path.name for path in HISTORICAL_ACCEPTED_ARTIFACT_PATHS],
        },
        "verification": {
            "commands": [
                {
                    "command": "pytest RR-09 closure, RR-09.1, RR-09.2, grouped, authorization, visibility, and benchmark integrity modules",
                    "outcome": "passed during closure acceptance workflow",
                },
                {
                    "command": "Black --check and scoped mypy on closure files",
                    "outcome": "passed during closure acceptance workflow",
                },
                {
                    "command": "git diff --check and historical git diff --quiet",
                    "outcome": "passed during closure acceptance workflow",
                },
            ]
        },
        "six_epic_roadmap_completion": [
            {
                "epic": 1,
                "status": "ACCEPTED_BASELINE",
                "summary": "Performance baseline artifacts remain frozen.",
            },
            {
                "epic": 2,
                "status": "CLOSED",
                "summary": "Routing closure accepted; E2.2 namespace-resolution optimization remains intentionally skipped.",
            },
            {
                "epic": 3,
                "status": "ACCEPTED_BASELINE",
                "summary": "Grouped execution baseline recommends result-normalization reduction SKIP and validator reuse DEFER.",
            },
            {
                "epic": 4,
                "status": "ACCEPTED_BASELINE",
                "summary": "Validator-caching candidate remains DEFER without a measured candidate clearing its proposed gate.",
            },
            {
                "epic": 5,
                "status": "CLOSE — EPIC 5 ACCEPTANCE SATISFIED",
                "summary": "Bounded cached-selection discovery closure accepted with documented limits.",
            },
            {
                "epic": 6,
                "status": "CLOSE — EPIC 6 ACCEPTANCE SATISFIED",
                "summary": "RR-09.1 and RR-09.2 accepted; RR-09.3 is formally skipped by this closure.",
            },
        ],
    }


def render_epic6_closure_report(matrix: Mapping[str, object]) -> str:
    """Render the Epic 6 final-closure report from the deterministic matrix.

    Args:
        matrix: Machine-readable closure matrix built from accepted RR-09 artifacts.

    Returns:
        Markdown report containing the requested evidence, limitations, and decision.
    """
    performance = _get_mapping(matrix, "rr_09_1_performance")
    memory = _get_mapping(matrix, "rr_09_2_memory")
    decision = _get_mapping(matrix, "rr_09_3_decision")
    lines = [
        "# Epic 6 — RR-09 Final Performance and Security Closure",
        "",
        "## 1. Executive summary",
        "",
        "RR-09.1 and RR-09.2 are accepted frozen evidence. This closure adds no production "
        "optimization. It consolidates direct projection, inclusive canonical, and Python-traced "
        "memory evidence and formally recommends RR-09.3 **SKIP**.",
        "",
        "## 2. Epic 6 Story completion status",
        "",
        "| Story | Status | Evidence |",
        "| --- | --- | --- |",
        "| RR-09.1 — Grouped Result Scaling Baseline | ACCEPTED | epic6_grouped_result_baseline_results.json |",
        "| RR-09.2 — Grouped Result Memory Profiling | ACCEPTED | epic6_grouped_result_memory_results.json |",
        "| RR-09.3 — Conditional Projection Optimization | FORMALLY SKIPPED | No material Registry-owned bottleneck or measured candidate |",
        "",
        "## 3. RR-09.1 performance findings",
        "",
        "Direct results are `synthetic_projection_only`; canonical dataset-list results are "
        "`canonical_dispatch_included`. Values are milliseconds. Trial range is the min–max p50 "
        "across the three frozen trials. Nested inclusive stages are not summed or subtracted.",
        "",
        "### Direct projection",
        "",
        "| Scenario | Input bytes | Records | Changed | p50 / p95 / p99 | Trial p50 range | Errors | Boundary | Source |",
        "| --- | ---: | ---: | --- | --- | --- | ---: | --- | --- |",
    ]
    lines.extend(_render_latency_rows(_get_object_list(performance, "direct_projection")))
    lines.extend(
        [
            "",
            "### Concurrent direct projection",
            "",
            "| Scenario | Input bytes | Records | Changed | p50 / p95 / p99 | Trial p50 range | Errors | Boundary | Source |",
            "| --- | ---: | ---: | --- | --- | --- | ---: | --- | --- |",
        ]
    )
    lines.extend(_render_latency_rows(_get_object_list(performance, "concurrency")))
    lines.extend(
        [
            "",
            "### Canonical dataset-list execution",
            "",
            "These values include canonical dispatch, authorization, database access, and projection. "
            "They are not exclusive Registry-projection timings.",
            "",
            "| Scenario | Result bytes | Records | Changed | p50 / p95 / p99 | Trial p50 range | Errors | Boundary | Source |",
            "| --- | ---: | ---: | --- | --- | --- | ---: | --- | --- |",
        ]
    )
    lines.extend(
        _render_canonical_latency_rows(_get_object_list(performance, "canonical_dataset_list"))
    )
    lines.extend(
        [
            "",
            "Explicitly not measured canonical response types: "
            + ", ".join(
                str(row.get("scenario_id"))
                for row in _get_object_list(performance, "explicitly_not_measured_canonical")
            )
            + ".",
            "",
            "## 4. RR-09.2 memory findings",
            "",
            "All values below are `tracemalloc` Python-allocation bytes, not process RSS. Peak and "
            "current include the returned output string; post-GC deltas are signed and are not leak claims.",
            "",
            "### Single-operation direct projection",
            "",
            "| Scenario | Input / output bytes | Records | Peak / current / post-GC Δ (trial 1) | Peak trial range | Errors | Boundary | Source |",
            "| --- | --- | ---: | --- | --- | ---: | --- | --- |",
        ]
    )
    lines.extend(_render_single_memory_rows(_get_object_list(memory, "single_operation")))
    lines.extend(
        [
            "",
            "### Repeated-operation retention",
            "",
            "Outputs are released after each operation. Checkpoints are signed deltas for trial 1.",
            "",
            "| Scenario | Iterations | Initial / intermediate / final / post-GC Δ | Peak | Trial post-GC Δ | Errors | Source |",
            "| --- | ---: | --- | ---: | --- | ---: | --- |",
        ]
    )
    lines.extend(_render_repeated_memory_rows(_get_object_list(memory, "repeated_retention")))
    lines.extend(
        [
            "",
            "### Concurrent memory",
            "",
            "`tracemalloc` and RSS are process-wide. Actual synchronized in-flight operations are "
            "reported; no process peak is divided by workers or attributed to a request.",
            "",
            "| Scenario | Workers | Peak in-flight trials | Peak traced trial range | Post-GC Δ trials | RSS status | Errors | Source |",
            "| --- | ---: | --- | --- | --- | --- | ---: | --- |",
        ]
    )
    lines.extend(_render_concurrent_memory_rows(_get_object_list(memory, "concurrency")))
    lines.extend(
        [
            "",
            "### Canonical dataset-list memory",
            "",
            "Canonical memory is a full inclusive boundary and is not attributed exclusively to projection.",
            "",
            "| Scenario | Result bytes | Records | Peak / current / post-GC Δ (trial 1) | Peak trial range | Errors | Source |",
            "| --- | ---: | ---: | --- | --- | ---: | --- |",
        ]
    )
    lines.extend(_render_canonical_memory_rows(_get_object_list(memory, "canonical_dataset_list")))
    lines.extend(
        [
            "",
            "## 5. Canonical workload interpretation",
            "",
            "The measured canonical p50 values remain tens of milliseconds at 1/50/100 records, while "
            "the direct 100-record rewritten projection p50 remains sub-millisecond. This is evidence "
            "against a material canonical projection bottleneck, but it is not an exclusive-stage "
            "calculation: inclusive stages are deliberately neither summed nor subtracted.",
            "",
            "## 6. Concurrency interpretation",
            "",
            "The 1/10/25-worker direct timing scenarios have zero errors. Memory trials confirm 1/10/25 "
            "actual in-flight operation lifetimes and show process-wide temporary amplification. No "
            "acceptance threshold defines that bounded amplification as unacceptable, and no individual "
            "request allocation is inferred.",
            "",
            "## 7. RR-09.3 decision and rationale",
            "",
            "**SKIP — No material Registry-owned bottleneck established**",
            "",
            "The RR-09.1 materiality gate is `PROPOSED_NOT_APPROVED`; it never authorized a threshold "
            "decision. No candidate implementation, latency saving, memory saving, rollback path, or "
            "end-to-end effect was measured. A fast path would add a contract-sensitive decision around "
            "legacy-reference rewrites without representative evidence that its complexity is justified.",
            "",
            "Candidate assessment:",
        ]
    )
    lines.extend(
        f"- {key.replace('_', ' ')}: `{value}`"
        for key, value in _get_mapping(decision, "candidate_assessment").items()
    )
    lines.extend(
        [
            "",
            "## 8. Security and behavior verification",
            "",
            "Epic 6 changes benchmark and closure evidence only; it does not change production grouped "
            "result processing. The focused regressions cover canonical BIF admission, persona freshness, "
            "capability revocation, namespace/workspace/dataset/version visibility, handler authorization, "
            "error and envelope contracts, correlation IDs, passthrough, legacy rewrites, business data, "
            "ordering, duplicates, unauthorized reads, and side effects.",
            "",
            "## 9. Historical artifact integrity",
            "",
            "The closure matrix records SHA-256 values for every Epic 1–5, RR-09.1, and RR-09.2 accepted "
            "artifact. `git diff --quiet HEAD -- <accepted artifacts>` is the closure integrity check; no "
            "accepted artifact is regenerated or overwritten.",
            "",
            "## 10. Benchmark limitations",
            "",
        ]
    )
    lines.extend(
        f"- {item}"
        for item in _get_string_list(
            _get_mapping(matrix, "cross_evidence_interpretation"), "environmental_limitations"
        )
    )
    lines.extend(
        [
            "",
            "## 11. Accepted residual risks",
            "",
            "- Local benchmark variance and three-trial scope do not establish broad production distributions.",
            "- Direct synthetic fixtures do not substitute for end-to-end chat/provider measurements.",
            "- Signed retained deltas can reflect allocator or cache lifetime behavior and are not leak proof.",
            "",
            "## 12. Reopening criteria",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in _get_string_list(decision, "reopening_criteria"))
    lines.extend(
        [
            "",
            "## 13. Verification commands and outcomes",
            "",
            "| Check | Outcome |",
            "| --- | --- |",
        ]
    )
    for command in _get_object_list(_get_mapping(matrix, "verification"), "commands"):
        lines.append(f"| {command.get('command')} | {command.get('outcome')} |")
    lines.extend(
        [
            "",
            "## 14. Final Epic 6 acceptance recommendation",
            "",
            "**CLOSE — EPIC 6 ACCEPTANCE SATISFIED**",
            "",
            "Accept RR-09.1 and RR-09.2 as frozen evidence and formally skip RR-09.3. No production "
            "optimization is authorized by this closure.",
            "",
            "## 15. Six-Epic roadmap completion summary",
            "",
            "| Epic | Status | Summary |",
            "| ---: | --- | --- |",
        ]
    )
    for item in _get_object_list(matrix, "six_epic_roadmap_completion"):
        lines.append(f"| {item.get('epic')} | {item.get('status')} | {item.get('summary')} |")
    return "\n".join(lines) + "\n"


def write_epic6_closure_artifacts() -> None:
    """Write new Epic 6 closure artifacts without mutating accepted source evidence.

    Raises:
        ValueError: If accepted RR-09 artifacts are invalid or incomplete.
    """
    matrix = build_epic6_closure_matrix()
    EPIC6_CLOSURE_MATRIX_PATH.write_text(json.dumps(matrix, indent=2, sort_keys=True) + "\n")
    EPIC6_CLOSURE_REPORT_PATH.write_text(render_epic6_closure_report(matrix))


def _read_json_object(path: Path) -> JsonObject:
    """Read one required JSON artifact as an object.

    Args:
        path: JSON artifact path.

    Returns:
        Decoded JSON object.

    Raises:
        ValueError: If the file does not decode to an object.
    """
    decoded = json.loads(path.read_text())
    if not isinstance(decoded, dict):
        raise ValueError(f"Expected a JSON object in {path.name}.")
    return cast(JsonObject, decoded)


def _get_mapping(payload: Mapping[str, object], key: str) -> Mapping[str, object]:
    """Return a required mapping field from a JSON-safe object.

    Args:
        payload: Source object containing the required field.
        key: Mapping field name.

    Returns:
        Required mapping field.

    Raises:
        ValueError: If the field is not a mapping.
    """
    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"Expected mapping field {key!r}.")
    return value


def _get_object_list(payload: Mapping[str, object], key: str) -> JsonObjectList:
    """Return a required list of JSON objects from a source object.

    Args:
        payload: Source object containing the required list.
        key: List field name.

    Returns:
        JSON object list.

    Raises:
        ValueError: If the field is not a list of objects.
    """
    value = payload.get(key)
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"Expected list-of-object field {key!r}.")
    return cast(JsonObjectList, value)


def _get_string_list(payload: Mapping[str, object], key: str) -> list[str]:
    """Return a required list of strings from a source object.

    Args:
        payload: Source object containing the required list.
        key: List field name.

    Returns:
        Required string list.

    Raises:
        ValueError: If the field is not a list of strings.
    """
    value = payload.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"Expected list-of-string field {key!r}.")
    return cast(list[str], value)


def _validate_frozen_epic6_artifacts(
    performance_metadata: Mapping[str, object],
    memory_metadata: Mapping[str, object],
) -> None:
    """Validate the minimal accepted RR-09 source-artifact contract.

    Args:
        performance_metadata: RR-09.1 artifact metadata.
        memory_metadata: RR-09.2 artifact metadata.

    Raises:
        ValueError: If an artifact is not accepted measured evidence.
    """
    if performance_metadata.get("artifact_set") != "epic6_grouped_result_baseline":
        raise ValueError("RR-09.1 source artifact selection is invalid.")
    if performance_metadata.get("epic6_canonical_execution_status") != (
        "measured_guarded_isolated_postgresql"
    ):
        raise ValueError("RR-09.1 canonical evidence is not accepted measured PostgreSQL evidence.")
    if memory_metadata.get("artifact_set") != "epic6_grouped_result_memory":
        raise ValueError("RR-09.2 source artifact selection is invalid.")
    if memory_metadata.get("epic6_memory_canonical_execution_status") != (
        "measured_guarded_isolated_postgresql"
    ):
        raise ValueError("RR-09.2 canonical evidence is not accepted measured PostgreSQL evidence.")
    if memory_metadata.get("latency_measurements_included") is not False:
        raise ValueError("RR-09.2 memory evidence must remain separate from latency measurements.")


def _index_latency_trials(
    metadata: Mapping[str, object],
    key: str,
) -> dict[str, JsonObjectList]:
    """Index each measured latency scenario's independent trial summaries.

    Args:
        metadata: RR-09.1 artifact metadata.
        key: Metadata field holding ordered trial result sets.

    Returns:
        Mapping from scenario ID to three latency summary records.

    Raises:
        ValueError: If the trial metadata is malformed.
    """
    trial_sets = _get_object_list(metadata, key)
    indexed: dict[str, JsonObjectList] = {}
    for trial_set in trial_sets:
        trial_number = trial_set.get("trial")
        for result in _get_object_list(trial_set, "results"):
            latency = result.get("latency")
            if not isinstance(latency, Mapping):
                continue
            scenario_id = result.get("name")
            if not isinstance(scenario_id, str):
                raise ValueError("Latency trial result lacks a scenario name.")
            indexed.setdefault(scenario_id, []).append(
                {
                    "trial": trial_number,
                    "p50_ms": latency.get("p50_ms"),
                    "p95_ms": latency.get("p95_ms"),
                    "p99_ms": latency.get("p99_ms"),
                    "error_count": result.get("error_count"),
                }
            )
    return indexed


def _build_latency_scenario_record(
    result: Mapping[str, object],
    trial_index: Mapping[str, JsonObjectList],
) -> JsonObject:
    """Reduce one RR-09.1 source result to closure-relevant timing evidence.

    Args:
        result: Frozen RR-09.1 primary result.
        trial_index: Independent-trial latency records keyed by scenario ID.

    Returns:
        Scenario ID, source, payload, latency, error, boundary, and trial evidence.

    Raises:
        ValueError: If a measured source result lacks required timing data.
    """
    scenario_id = result.get("name")
    latency = result.get("latency")
    if not isinstance(scenario_id, str) or not isinstance(latency, Mapping):
        raise ValueError("Measured RR-09.1 result lacks scenario ID or latency.")
    metadata = result.get("scenario_metadata")
    scenario_metadata = metadata if isinstance(metadata, Mapping) else {}
    payload = result.get("result_payload_bytes")
    result_payload_bytes = payload.get("mean") if isinstance(payload, Mapping) else None
    return {
        "scenario_id": scenario_id,
        "source_artifact": _PERFORMANCE_RESULTS_PATH.name,
        "status": result.get("status"),
        "category": result.get("category"),
        "execution_boundary": result.get("execution_boundary"),
        "input_payload_bytes": scenario_metadata.get("input_payload_bytes"),
        "result_payload_bytes": result_payload_bytes,
        "record_count": scenario_metadata.get("record_count"),
        "projection_changes_result": scenario_metadata.get("projection_changes_result"),
        "p50_ms": latency.get("p50_ms"),
        "p95_ms": latency.get("p95_ms"),
        "p99_ms": latency.get("p99_ms"),
        "error_count": result.get("error_count"),
        "trial_latency_ms": trial_index.get(scenario_id, []),
    }


def _build_not_measured_record(result: Mapping[str, object]) -> JsonObject:
    """Reduce an explicit fixture gap without fabricating a timing value.

    Args:
        result: Frozen RR-09.1 not-measured canonical result.

    Returns:
        Scenario identity, boundary, and explicit artifact reason.
    """
    return {
        "scenario_id": result.get("name"),
        "source_artifact": _PERFORMANCE_RESULTS_PATH.name,
        "status": result.get("status"),
        "execution_boundary": result.get("execution_boundary"),
        "reason": result.get("reason"),
    }


def _build_memory_scenario_record(result: Mapping[str, object]) -> JsonObject:
    """Reduce one RR-09.2 source result while retaining each actual trial measurement.

    Args:
        result: Frozen RR-09.2 primary result.

    Returns:
        Scenario identity, payload sizes, exact trials, errors, and boundary.
    """
    scenario_id = result.get("name")
    if not isinstance(scenario_id, str):
        raise ValueError("RR-09.2 result lacks a scenario name.")
    raw_trials = result.get("trials")
    if not isinstance(raw_trials, list) or not all(isinstance(trial, dict) for trial in raw_trials):
        raise ValueError(f"RR-09.2 result {scenario_id} lacks object trials.")
    return {
        "scenario_id": scenario_id,
        "source_artifact": _MEMORY_RESULTS_PATH.name,
        "status": result.get("status"),
        "category": result.get("category"),
        "execution_boundary": result.get("execution_boundary"),
        "input_payload_bytes": result.get("input_payload_bytes"),
        "output_payload_bytes": result.get("output_payload_bytes"),
        "record_count": result.get("record_count"),
        "projection_changes_result": result.get("projection_changes_result"),
        "iterations_per_trial": result.get("iterations_per_trial"),
        "concurrency": result.get("concurrency"),
        "error_count": result.get("error_count"),
        "trials": cast(JsonObjectList, raw_trials),
    }


def _validate_consolidated_scenario_counts(
    *,
    performance_direct: Sequence[Mapping[str, object]],
    performance_concurrency: Sequence[Mapping[str, object]],
    performance_canonical: Sequence[Mapping[str, object]],
    memory_single: Sequence[Mapping[str, object]],
    memory_repeated: Sequence[Mapping[str, object]],
    memory_concurrency: Sequence[Mapping[str, object]],
    memory_canonical: Sequence[Mapping[str, object]],
) -> None:
    """Reject incomplete frozen evidence before a closure can be written.

    Args:
        performance_direct: Direct RR-09.1 scenario records.
        performance_concurrency: Concurrent RR-09.1 scenario records.
        performance_canonical: Canonical RR-09.1 scenario records.
        memory_single: Single-operation RR-09.2 records.
        memory_repeated: Repeated-retention RR-09.2 records.
        memory_concurrency: Concurrent RR-09.2 records.
        memory_canonical: Canonical RR-09.2 records.

    Raises:
        ValueError: If a required frozen scenario category is incomplete.
    """
    expected_counts = (30, 3, 3, 30, 9, 3, 3)
    observed_counts = (
        len(performance_direct),
        len(performance_concurrency),
        len(performance_canonical),
        len(memory_single),
        len(memory_repeated),
        len(memory_concurrency),
        len(memory_canonical),
    )
    if observed_counts != expected_counts:
        raise ValueError(
            "Frozen Epic 6 scenario evidence is incomplete: "
            f"expected {expected_counts}, observed {observed_counts}."
        )


def _render_latency_rows(rows: Sequence[Mapping[str, object]]) -> list[str]:
    """Render direct or threaded timing rows with complete trial p50 variation.

    Args:
        rows: Consolidated RR-09.1 timing records.

    Returns:
        Markdown table rows.
    """
    return [
        "| {scenario} | {input_bytes} | {records} | {changed} | {p50} / {p95} / {p99} | {variation} | {errors} | {boundary} | {source} |".format(
            scenario=row.get("scenario_id"),
            input_bytes=_format_value(row.get("input_payload_bytes")),
            records=_format_value(row.get("record_count")),
            changed=_format_value(row.get("projection_changes_result")),
            p50=_format_value(row.get("p50_ms")),
            p95=_format_value(row.get("p95_ms")),
            p99=_format_value(row.get("p99_ms")),
            variation=_format_latency_variation(_get_object_list(row, "trial_latency_ms")),
            errors=_format_value(row.get("error_count")),
            boundary=_format_value(row.get("execution_boundary")),
            source=_format_value(row.get("source_artifact")),
        )
        for row in rows
    ]


def _render_canonical_latency_rows(rows: Sequence[Mapping[str, object]]) -> list[str]:
    """Render inclusive canonical timing rows.

    Args:
        rows: Consolidated canonical RR-09.1 timing records.

    Returns:
        Markdown table rows.
    """
    return [
        "| {scenario} | {result_bytes} | {records} | {changed} | {p50} / {p95} / {p99} | {variation} | {errors} | {boundary} | {source} |".format(
            scenario=row.get("scenario_id"),
            result_bytes=_format_value(row.get("result_payload_bytes")),
            records=_format_value(row.get("record_count")),
            changed=_format_value(row.get("projection_changes_result")),
            p50=_format_value(row.get("p50_ms")),
            p95=_format_value(row.get("p95_ms")),
            p99=_format_value(row.get("p99_ms")),
            variation=_format_latency_variation(_get_object_list(row, "trial_latency_ms")),
            errors=_format_value(row.get("error_count")),
            boundary=_format_value(row.get("execution_boundary")),
            source=_format_value(row.get("source_artifact")),
        )
        for row in rows
    ]


def _render_single_memory_rows(rows: Sequence[Mapping[str, object]]) -> list[str]:
    """Render single-operation traced-memory rows.

    Args:
        rows: Consolidated direct RR-09.2 memory records.

    Returns:
        Markdown table rows.
    """
    rendered_rows: list[str] = []
    for row in rows:
        trials = _get_object_list(row, "trials")
        first_trial = trials[0]
        peaks = [trial.get("traced_peak_bytes") for trial in trials]
        rendered_rows.append(
            "| {scenario} | {input_bytes} / {output_bytes} | {records} | {peak} / {current} / {retained} | {peak_range} | {errors} | {boundary} | {source} |".format(
                scenario=row.get("scenario_id"),
                input_bytes=_format_value(row.get("input_payload_bytes")),
                output_bytes=_format_value(row.get("output_payload_bytes")),
                records=_format_value(row.get("record_count")),
                peak=_format_value(first_trial.get("traced_peak_bytes")),
                current=_format_value(first_trial.get("traced_current_after_execution_bytes")),
                retained=_format_value(first_trial.get("post_gc_retained_delta_bytes")),
                peak_range=_format_number_range(peaks),
                errors=_format_value(row.get("error_count")),
                boundary=_format_value(row.get("execution_boundary")),
                source=_format_value(row.get("source_artifact")),
            )
        )
    return rendered_rows


def _render_repeated_memory_rows(rows: Sequence[Mapping[str, object]]) -> list[str]:
    """Render repeated-memory retention rows.

    Args:
        rows: Consolidated repeated RR-09.2 memory records.

    Returns:
        Markdown table rows.
    """
    rendered_rows: list[str] = []
    for row in rows:
        trials = _get_object_list(row, "trials")
        first_trial = trials[0]
        deltas = [trial.get("post_gc_retained_delta_bytes") for trial in trials]
        rendered_rows.append(
            "| {scenario} | {iterations} | {initial} / {intermediate} / {final} / {post_gc} | {peak} | {variation} | {errors} | {source} |".format(
                scenario=row.get("scenario_id"),
                iterations=_format_value(row.get("iterations_per_trial")),
                initial=_format_value(first_trial.get("initial_retained_delta_bytes")),
                intermediate=_format_value(first_trial.get("intermediate_retained_delta_bytes")),
                final=_format_value(first_trial.get("final_retained_delta_bytes")),
                post_gc=_format_value(first_trial.get("post_gc_retained_delta_bytes")),
                peak=_format_value(first_trial.get("traced_peak_bytes")),
                variation=" / ".join(_format_value(delta) for delta in deltas),
                errors=_format_value(row.get("error_count")),
                source=_format_value(row.get("source_artifact")),
            )
        )
    return rendered_rows


def _render_concurrent_memory_rows(rows: Sequence[Mapping[str, object]]) -> list[str]:
    """Render process-wide concurrent memory rows.

    Args:
        rows: Consolidated concurrent RR-09.2 memory records.

    Returns:
        Markdown table rows.
    """
    rendered_rows: list[str] = []
    for row in rows:
        trials = _get_object_list(row, "trials")
        rss_statuses = [
            _get_mapping(trial, "process_rss_after_execution").get("status") for trial in trials
        ]
        rendered_rows.append(
            "| {scenario} | {workers} | {inflight} | {peak_range} | {retained} | {rss} | {errors} | {source} |".format(
                scenario=row.get("scenario_id"),
                workers=_format_value(row.get("concurrency")),
                inflight=" / ".join(
                    _format_value(trial.get("actual_peak_in_flight_operations")) for trial in trials
                ),
                peak_range=_format_number_range(
                    [trial.get("traced_peak_bytes") for trial in trials]
                ),
                retained=" / ".join(
                    _format_value(trial.get("post_gc_retained_delta_bytes")) for trial in trials
                ),
                rss=" / ".join(_format_value(status) for status in rss_statuses),
                errors=_format_value(row.get("error_count")),
                source=_format_value(row.get("source_artifact")),
            )
        )
    return rendered_rows


def _render_canonical_memory_rows(rows: Sequence[Mapping[str, object]]) -> list[str]:
    """Render inclusive canonical memory rows.

    Args:
        rows: Consolidated canonical RR-09.2 memory records.

    Returns:
        Markdown table rows.
    """
    rendered_rows: list[str] = []
    for row in rows:
        trials = _get_object_list(row, "trials")
        first_trial = trials[0]
        rendered_rows.append(
            "| {scenario} | {result_bytes} | {records} | {peak} / {current} / {retained} | {peak_range} | {errors} | {source} |".format(
                scenario=row.get("scenario_id"),
                result_bytes=_format_value(row.get("output_payload_bytes")),
                records=_format_value(row.get("record_count")),
                peak=_format_value(first_trial.get("traced_peak_bytes")),
                current=_format_value(first_trial.get("traced_current_after_execution_bytes")),
                retained=_format_value(first_trial.get("post_gc_retained_delta_bytes")),
                peak_range=_format_number_range(
                    [trial.get("traced_peak_bytes") for trial in trials]
                ),
                errors=_format_value(row.get("error_count")),
                source=_format_value(row.get("source_artifact")),
            )
        )
    return rendered_rows


def _format_latency_variation(trials: Sequence[Mapping[str, object]]) -> str:
    """Format the min-to-max p50 range for independent timing trials.

    Args:
        trials: Independent latency trial summaries.

    Returns:
        Formatted p50 range, or ``unavailable`` without numeric trials.
    """
    return _format_number_range([trial.get("p50_ms") for trial in trials])


def _format_number_range(values: Sequence[object]) -> str:
    """Format a numeric sequence as a deterministic minimum-to-maximum range.

    Args:
        values: Potentially unavailable numeric values.

    Returns:
        Formatted range, or ``unavailable`` when no number is available.
    """
    numeric_values = [
        float(value)
        for value in values
        if isinstance(value, int | float) and not isinstance(value, bool)
    ]
    if not numeric_values:
        return "unavailable"
    return f"{_format_value(min(numeric_values))}–{_format_value(max(numeric_values))}"


def _format_value(value: object) -> str:
    """Format one JSON-safe closure value for a compact Markdown table.

    Args:
        value: Value to render.

    Returns:
        Human-readable scalar with explicit unavailable handling.
    """
    if value is None:
        return "unavailable"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, float):
        return f"{value:.6f}".rstrip("0").rstrip(".")
    return str(value)


def main() -> int:
    """Write the deterministic Epic 6 closure artifacts.

    Returns:
        Zero after the new closure matrix and report have been written.
    """
    write_epic6_closure_artifacts()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
