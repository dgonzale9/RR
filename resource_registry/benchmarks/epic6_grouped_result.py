"""Define deterministic RR-09.1 grouped-result benchmark workloads.

This module measures the existing grouped-result projection function only.  It does not
alter production result processing or claim that a candidate optimization has been tested.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from threading import Barrier
from time import perf_counter_ns

from resource_registry.benchmarks.grouped_execution import ExecutionBoundary
from resource_registry.benchmarks.reporting import summarize_latency_ns
from resource_registry.benchmarks.scenarios import BenchmarkScenario
from resource_registry.bifs.grouped_tools import project_registry_tool_result

EPIC6_GROUPED_RESULT_ARTIFACT_SET = "epic6_grouped_result_baseline"
EPIC6_WARMUP_COUNT = 5
EPIC6_MEASURED_ITERATIONS = 120
EPIC6_TRIAL_COUNT = 3
EPIC6_CONCURRENCY_LEVELS = (1, 10, 25)

_INSPECT_OPERATION = "resource_registry_get_dataset"
_INSPECT_GROUP = "resource_registry_inspect"
_INSPECT_ACTION = "get_dataset"
_QUERY_OPERATION = "resource_registry_start_query_job"
_QUERY_GROUP = "resource_registry_query"
_QUERY_ACTION = "start_query_job"


@dataclass(frozen=True)
class ProjectionFixture:
    """Describe one deterministic payload for the unmodified projection function.

    Args:
        name: Stable direct-projection scenario identifier.
        payload: Exact input string passed to the production projection function.
        expected_result: Exact response that the current production function must return.
        record_count: Logical records represented by the bounded fixture.
        projection_changes_result: Whether the expected result differs from the input string.
        fixture_kind: Bounded payload shape used to interpret the scenario.
    """

    name: str
    payload: str
    expected_result: str
    record_count: int
    projection_changes_result: bool
    fixture_kind: str

    @property
    def input_payload_bytes(self) -> int:
        """Return the UTF-8 serialized input size.

        Returns:
            Number of bytes in the exact input supplied to the projection function.
        """
        return len(self.payload.encode("utf-8"))


def _encode_payload(payload: object) -> str:
    """Encode one deterministic benchmark fixture without changing its key order.

    Args:
        payload: JSON-compatible benchmark-only value.

    Returns:
        UTF-8-safe JSON text using the production projection serializer's defaults.
    """
    return json.dumps(payload, ensure_ascii=False)


def _build_records(record_count: int) -> list[dict[str, object]]:
    """Build deterministic business records without legacy transport metadata.

    Args:
        record_count: Number of bounded synthetic records to create.

    Returns:
        Ordered records with stable IDs, values, and duplicate labels.
    """
    return [
        {
            "id": index,
            "label": f"benchmark-record-{index % 7}",
            "amount": index * 1.25,
            "tags": ["stable", "duplicate" if index % 2 else "unique"],
        }
        for index in range(record_count)
    ]


def _build_rewritten_payload(
    *,
    metadata_field: str,
    record_count: int,
) -> tuple[str, str]:
    """Build one source/expected pair for a supported top-level metadata rewrite.

    Args:
        metadata_field: Supported projection metadata field.
        record_count: Number of bounded business records included beside the metadata.

    Returns:
        Input and expected strings for exact projection parity checks.

    Raises:
        ValueError: If a caller requests an unsupported metadata field.
    """
    payload: dict[str, object] = {
        "success": False,
        "status": 409,
        "correlation_id": "rr091-benchmark-correlation",
        "data": {"records": _build_records(record_count)},
    }
    expected_payload = dict(payload)
    if metadata_field == "suggested_bif":
        payload[metadata_field] = _INSPECT_OPERATION
        expected_payload[metadata_field] = _INSPECT_GROUP
        expected_payload["suggested_action"] = _INSPECT_ACTION
    elif metadata_field == "recommended_bif":
        payload[metadata_field] = _QUERY_OPERATION
        expected_payload[metadata_field] = _QUERY_GROUP
        expected_payload["recommended_action"] = _QUERY_ACTION
    elif metadata_field == "hint":
        payload[metadata_field] = f"Call {_INSPECT_OPERATION} after reviewing the records."
        expected_payload[metadata_field] = (
            f"Call {_INSPECT_GROUP} with action='{_INSPECT_ACTION}' after reviewing the records."
        )
    elif metadata_field == "error":
        payload[metadata_field] = f"Retry {_INSPECT_OPERATION} after correcting the request."
        expected_payload[metadata_field] = (
            f"Retry {_INSPECT_GROUP} with action='{_INSPECT_ACTION}' after correcting the request."
        )
    else:
        raise ValueError("Unsupported grouped-result benchmark metadata field.")
    return _encode_payload(payload), _encode_payload(expected_payload)


def _build_deeply_nested_payload(depth: int) -> dict[str, object]:
    """Build a bounded nested payload without a rewriteable transport field.

    Args:
        depth: Number of deterministic object nesting levels.

    Returns:
        JSON-compatible nested benchmark data.
    """
    nested: dict[str, object] = {"leaf": "stable"}
    for level in range(depth):
        nested = {f"level_{level}": nested}
    return {"success": True, "data": nested}


def build_epic6_projection_fixtures() -> tuple[ProjectionFixture, ...]:
    """Build the complete deterministic RR-09.1 direct-projection matrix.

    Returns:
        Fixtures covering unchanged, rewritten, structured, and unusual payload behavior.
    """
    fixtures: list[ProjectionFixture] = []
    for record_count in (1, 50, 100):
        unchanged_payload = _encode_payload(
            {"success": True, "data": {"records": _build_records(record_count)}}
        )
        fixtures.append(
            ProjectionFixture(
                name=f"grouped_result.projection.unchanged.records_{record_count:03d}",
                payload=unchanged_payload,
                expected_result=unchanged_payload,
                record_count=record_count,
                projection_changes_result=False,
                fixture_kind="unchanged_record_array",
            )
        )
        for metadata_field in ("hint", "error", "suggested_bif", "recommended_bif"):
            payload, expected_result = _build_rewritten_payload(
                metadata_field=metadata_field,
                record_count=record_count,
            )
            fixtures.append(
                ProjectionFixture(
                    name=(
                        "grouped_result.projection.rewritten."
                        f"{metadata_field}.records_{record_count:03d}"
                    ),
                    payload=payload,
                    expected_result=expected_result,
                    record_count=record_count,
                    projection_changes_result=True,
                    fixture_kind=f"rewritten_{metadata_field}",
                )
            )

    structured_payloads = (
        (
            "grouped_result.projection.structured.large_array",
            {"success": True, "data": {"records": _build_records(100)}},
            100,
            False,
            "large_record_array",
        ),
        (
            "grouped_result.projection.structured.deep_nesting",
            _build_deeply_nested_payload(24),
            1,
            False,
            "deeply_nested_object",
        ),
        (
            "grouped_result.projection.structured.high_cardinality_metadata",
            {
                "success": True,
                "metadata": {f"metadata_{index:03d}": f"value-{index}" for index in range(128)},
            },
            128,
            False,
            "high_cardinality_metadata",
        ),
        (
            "grouped_result.projection.structured.schema_like",
            {
                "success": True,
                "data": {
                    "columns": [
                        {
                            "name": f"column_{index:03d}",
                            "type": "string",
                            "nullable": index % 2 == 0,
                            "description": f"Deterministic schema column {index}",
                        }
                        for index in range(100)
                    ]
                },
            },
            100,
            False,
            "schema_like_columns",
        ),
        (
            "grouped_result.projection.structured.long_hint",
            {
                "success": False,
                "correlation_id": "rr091-long-hint",
                "hint": f"{_INSPECT_OPERATION} " + "hint-content " * 1_024,
            },
            1,
            True,
            "long_rewritten_hint",
        ),
        (
            "grouped_result.projection.structured.long_error",
            {
                "success": False,
                "correlation_id": "rr091-long-error",
                "error": f"{_INSPECT_OPERATION} " + "error-content " * 1_024,
            },
            1,
            True,
            "long_rewritten_error",
        ),
        (
            "grouped_result.projection.structured.mixed_success_error",
            {
                "success": False,
                "status": 409,
                "correlation_id": "rr091-mixed-envelope",
                "data": {"records": _build_records(100)},
                "error": f"Use {_INSPECT_OPERATION} before retrying.",
            },
            100,
            True,
            "mixed_error_envelope",
        ),
    )
    for name, payload_object, record_count, changes_result, fixture_kind in structured_payloads:
        payload = _encode_payload(payload_object)
        expected_result = payload
        if changes_result:
            expected_result = payload.replace(
                _INSPECT_OPERATION,
                f"{_INSPECT_GROUP} with action='{_INSPECT_ACTION}'",
            )
        fixtures.append(
            ProjectionFixture(
                name=name,
                payload=payload,
                expected_result=expected_result,
                record_count=record_count,
                projection_changes_result=changes_result,
                fixture_kind=fixture_kind,
            )
        )

    unusual_payloads = (
        ("malformed_json", "{not-json", 0, False, "malformed_json"),
        ("scalar_json", _encode_payload("scalar result"), 0, False, "json_scalar"),
        ("array_json", _encode_payload(["one", "two"]), 2, False, "json_array"),
        ("empty_result", "", 0, False, "empty_string"),
        (
            "unknown_reference",
            _encode_payload({"suggested_bif": "resource_registry_unknown"}),
            0,
            False,
            "unknown_legacy_reference",
        ),
        (
            "already_canonical_reference",
            _encode_payload({"suggested_bif": _INSPECT_GROUP}),
            0,
            False,
            "grouped_reference",
        ),
        (
            "similar_name",
            _encode_payload({"hint": f"Use {_INSPECT_OPERATION}_extra."}),
            0,
            False,
            "similar_name_reference",
        ),
        (
            "business_data_reference",
            _encode_payload(
                {
                    "success": True,
                    "data": {
                        "suggested_bif": _INSPECT_OPERATION,
                        "message": f"{_INSPECT_OPERATION} is business data.",
                        "records": _build_records(2),
                    },
                }
            ),
            2,
            False,
            "business_data_reference",
        ),
    )
    fixtures.extend(
        ProjectionFixture(
            name=f"grouped_result.projection.unusual.{name}",
            payload=payload,
            expected_result=payload,
            record_count=record_count,
            projection_changes_result=changes_result,
            fixture_kind=fixture_kind,
        )
        for name, payload, record_count, changes_result, fixture_kind in unusual_payloads
    )
    return tuple(fixtures)


EPIC6_PROJECTION_SCENARIO_NAMES = tuple(
    fixture.name for fixture in build_epic6_projection_fixtures()
)
EPIC6_CANONICAL_SCENARIO_NAMES = (
    "grouped_result.canonical.dataset_list.records_001",
    "grouped_result.canonical.dataset_list.records_050",
    "grouped_result.canonical.dataset_list.records_100",
    "grouped_result.canonical.dataset_versions",
    "grouped_result.canonical.transformation_runs",
    "grouped_result.canonical.query_results",
    "grouped_result.canonical.exploration_results",
    "grouped_result.canonical.schema_response",
    "grouped_result.canonical.profile_response",
    "grouped_result.canonical.context_response",
)
EPIC6_CONCURRENCY_SCENARIO_NAMES = tuple(
    f"grouped_result.projection.concurrency_{workers}" for workers in EPIC6_CONCURRENCY_LEVELS
)
EPIC6_SCENARIO_BOUNDARIES: Mapping[str, ExecutionBoundary] = {
    **{name: "synthetic_projection_only" for name in EPIC6_PROJECTION_SCENARIO_NAMES},
    **{name: "canonical_dispatch_included" for name in EPIC6_CANONICAL_SCENARIO_NAMES},
    **{name: "synthetic_projection_only" for name in EPIC6_CONCURRENCY_SCENARIO_NAMES},
}
EPIC6_REQUIRED_SCENARIO_NAMES = tuple(EPIC6_SCENARIO_BOUNDARIES)


def build_epic6_projection_scenarios() -> tuple[BenchmarkScenario, ...]:
    """Build direct measurements of the current production projection function.

    Returns:
        Direct-production projection scenarios with serialized input metadata.
    """
    scenarios: list[BenchmarkScenario] = []
    for fixture in build_epic6_projection_fixtures():
        scenarios.append(
            BenchmarkScenario(
                name=fixture.name,
                category="projection",
                kind="synthetic_direct_production_function",
                execute=partial(_project_epic6_fixture, fixture),
                classify=partial(_classify_epic6_projection_fixture, fixture),
                execution_boundary=EPIC6_SCENARIO_BOUNDARIES[fixture.name],
                metadata={
                    "input_payload_bytes": fixture.input_payload_bytes,
                    "record_count": fixture.record_count,
                    "projection_changes_result": fixture.projection_changes_result,
                    "measurement_boundary": "direct_production_projection_function",
                },
            )
        )
    return tuple(scenarios)


def _project_epic6_fixture(fixture: ProjectionFixture) -> str:
    """Run the unchanged production projection function for one fixture.

    Args:
        fixture: Bounded payload and expected output contract.

    Returns:
        Production projection result for the fixture's exact source string.
    """
    return project_registry_tool_result(fixture.payload)


def _classify_epic6_projection_fixture(fixture: ProjectionFixture, result: object) -> str:
    """Classify exact direct-projection parity for one deterministic fixture.

    Args:
        fixture: Bounded payload and expected output contract.
        result: Value returned by the production projection function.

    Returns:
        ``success`` for exact parity, otherwise ``failed``.
    """
    return "success" if result == fixture.expected_result else "failed"


def run_epic6_projection_concurrency(
    *,
    workers: int,
    warmups: int,
    operations: int,
) -> dict[str, object]:
    """Measure bounded concurrent calls to the current projection function.

    Args:
        workers: Simultaneous thread-pool workers.
        warmups: Excluded individual projection invocations.
        operations: Measured calls; must be at least one full worker wave.

    Returns:
        Latency, throughput, and input-payload metadata for the bounded direct workload.

    Raises:
        ValueError: If worker, warmup, or operation counts are invalid.
    """
    if workers not in EPIC6_CONCURRENCY_LEVELS:
        raise ValueError("RR-09.1 concurrency requires a registered worker level.")
    if warmups < 0 or operations < workers:
        raise ValueError("warmups must be nonnegative and operations must cover one worker wave")
    fixture = next(
        item
        for item in build_epic6_projection_fixtures()
        if item.name == "grouped_result.projection.rewritten.hint.records_100"
    )
    for _ in range(warmups):
        if project_registry_tool_result(fixture.payload) != fixture.expected_result:
            raise RuntimeError("RR-09.1 warmup projection parity failed.")

    samples_ns: list[int] = []
    errors = 0

    def project_once(barrier: Barrier) -> int:
        """Project the bounded payload after all workers reach the same wave.

        Args:
            barrier: Per-wave synchronization barrier.

        Returns:
            Individual direct projection duration in nanoseconds.

        Raises:
            RuntimeError: If the current production projection violates parity.
        """
        barrier.wait(timeout=10)
        started_ns = perf_counter_ns()
        result = project_registry_tool_result(fixture.payload)
        elapsed_ns = perf_counter_ns() - started_ns
        if result != fixture.expected_result:
            raise RuntimeError("RR-09.1 concurrent projection parity failed.")
        return elapsed_ns

    started_wall_ns = perf_counter_ns()
    with ThreadPoolExecutor(max_workers=workers) as executor:
        remaining_operations = operations
        while remaining_operations:
            wave_size = min(workers, remaining_operations)
            barrier = Barrier(wave_size)
            futures = [executor.submit(project_once, barrier) for _ in range(wave_size)]
            for future in futures:
                try:
                    samples_ns.append(future.result())
                except Exception:
                    errors += 1
            remaining_operations -= wave_size
    wall_seconds = (perf_counter_ns() - started_wall_ns) / 1_000_000_000
    scenario_name = f"grouped_result.projection.concurrency_{workers}"
    return {
        "name": scenario_name,
        "category": "projection_concurrency",
        "kind": "synthetic_threaded_direct_production_function",
        "execution_boundary": EPIC6_SCENARIO_BOUNDARIES[scenario_name],
        "status": "failed" if errors else "measured",
        "reason": None,
        "warmups": warmups,
        "iterations": operations,
        "concurrency": workers,
        "sample_count": len(samples_ns),
        "completed_count": len(samples_ns),
        "error_count": errors,
        "outcomes": {"success": len(samples_ns), "failed": errors},
        "latency": summarize_latency_ns(samples_ns),
        "throughput_ops_per_second": len(samples_ns) / wall_seconds if wall_seconds else None,
        "stages": {},
        "result_payload_bytes": {"mean": float(len(fixture.expected_result.encode("utf-8")))},
        "database_sql": None,
        "authorization_context_build_count_per_operation": None,
        "scenario_metadata": {
            "input_payload_bytes": fixture.input_payload_bytes,
            "record_count": fixture.record_count,
            "projection_changes_result": fixture.projection_changes_result,
            "measurement_boundary": "direct_production_projection_function",
        },
    }


def build_epic6_not_measured_canonical_results() -> list[dict[str, object]]:
    """Declare unsupported canonical result workloads without fabricating measurements.

    Returns:
        Exact canonical scenario records marked unavailable until guarded database evidence exists.
    """
    reasons = {
        "grouped_result.canonical.dataset_versions": (
            "The existing isolated benchmark fixture has no version-list action assignment and no "
            "representative version-cardinality fixture."
        ),
        "grouped_result.canonical.transformation_runs": (
            "The existing isolated benchmark fixture has no representative persisted "
            "transformation-run cardinality fixture."
        ),
        "grouped_result.canonical.query_results": (
            "The existing isolated benchmark fixture does not provide an authorized query-result "
            "storage fixture without substituting the canonical handler."
        ),
        "grouped_result.canonical.exploration_results": (
            "The existing isolated benchmark fixture does not provide a deterministic authorized "
            "exploration-result storage fixture."
        ),
        "grouped_result.canonical.schema_response": (
            "The existing isolated benchmark fixture lacks representative persisted schema "
            "metadata at the requested cardinalities."
        ),
        "grouped_result.canonical.profile_response": (
            "The existing isolated benchmark fixture lacks representative persisted profile "
            "metadata at the requested cardinalities."
        ),
        "grouped_result.canonical.context_response": (
            "The existing isolated benchmark fixture lacks representative combined context "
            "metadata at the requested cardinalities."
        ),
    }
    results: list[dict[str, object]] = []
    for name in EPIC6_CANONICAL_SCENARIO_NAMES:
        if name.startswith("grouped_result.canonical.dataset_list."):
            reason = "Run the guarded RR-09.1 isolated PostgreSQL benchmark command."
        else:
            reason = reasons[name]
        results.append(
            {
                "name": name,
                "category": "canonical_execution",
                "kind": "isolated_database",
                "status": "not_measured",
                "execution_boundary": EPIC6_SCENARIO_BOUNDARIES[name],
                "reason": reason,
                "warmups": 0,
                "iterations": 0,
                "sample_count": 0,
                "error_count": 0,
                "outcomes": {},
                "latency": None,
                "stages": {},
                "result_payload_bytes": None,
                "database_sql": None,
                "authorization_context_build_count_per_operation": None,
                "scenario_metadata": {
                    "measurement_boundary": "canonical_dispatch_included",
                    "record_count": (
                        int(name.rsplit("_", maxsplit=1)[-1])
                        if name.startswith("grouped_result.canonical.dataset_list.")
                        else None
                    ),
                },
            }
        )
    return results


def evaluate_epic6_materiality_gate() -> dict[str, object]:
    """Declare the proposed RR-09.1 decision gate without approving optimization work.

    Returns:
        Proposed thresholds and reproducibility requirements with an explicit deferred decision.
    """
    return {
        "status": "PROPOSED_NOT_APPROVED",
        "recommendation": "DEFER",
        "candidate_implementation_measured": False,
        "absolute_latency_reduction_ms_threshold": 0.5,
        "relative_latency_reduction_percent_threshold": 10.0,
        "representative_workload_requirement": (
            "At least one 100-record rewritten result and one canonical authorized result "
            "must exceed the proposed materiality threshold without security or behavior regressions."
        ),
        "reproducibility_requirement": (
            "Three independent trials with five warmups and 120 measured serial iterations, "
            "plus bounded 1/10/25-worker concurrency evidence."
        ),
        "security_requirement": (
            "Projection parity, canonical admission, namespace isolation, visibility, persona "
            "freshness, capability revocation, and error-contract regressions must pass."
        ),
        "absolute_latency_reduction_ms": None,
        "relative_latency_reduction_percent": None,
    }


def validate_epic6_scenario_inventory(scenarios: Sequence[BenchmarkScenario]) -> None:
    """Validate that direct scenarios retain the fixed RR-09.1 inventory.

    Args:
        scenarios: Direct projection scenarios prepared for one trial.

    Raises:
        ValueError: If a scenario name or boundary diverges from the declared contract.
    """
    names = {scenario.name for scenario in scenarios}
    if names != set(EPIC6_PROJECTION_SCENARIO_NAMES):
        raise ValueError(
            "RR-09.1 direct projection scenarios do not match the registered inventory."
        )
    if any(
        scenario.execution_boundary != EPIC6_SCENARIO_BOUNDARIES[scenario.name]
        for scenario in scenarios
    ):
        raise ValueError("RR-09.1 direct projection boundary labels do not match the contract.")
