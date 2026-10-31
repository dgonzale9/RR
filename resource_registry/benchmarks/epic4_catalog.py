"""Define isolated RR-04.1 catalog and static-validator benchmark workloads."""

from __future__ import annotations

import gc
import json
import os
import subprocess
import sys
import tracemalloc
from collections.abc import Callable, Mapping
from functools import partial
from time import perf_counter_ns
from types import MappingProxyType
from typing import TypeAlias, cast

from jsonschema import Draft202012Validator, FormatChecker  # type: ignore[import-untyped]

from resource_registry.application.contracts import ValidationError
from resource_registry.benchmarks.grouped_execution import ExecutionBoundary
from resource_registry.benchmarks.reporting import summarize_latency_ns
from resource_registry.benchmarks.scenarios import BenchmarkScenario
from resource_registry.bifs.grouped_publication import RegistryToolProvider, format_registry_tool
from resource_registry.bifs.grouped_tools import (
    FunctionToolDefinition,
    build_grouped_registry_tools,
)
from resource_registry.bifs.transformation.schemas import (
    GET_TRANSFORMATION_RUN_SCHEMA,
    START_TRANSFORMATION_RUN_SCHEMA,
    validate_bif_input,
)

BenchmarkRecord: TypeAlias = dict[str, object]

EPIC4_ARTIFACT_SET = "epic4_catalog_baseline"
EPIC4_MEMORY_ITERATIONS = 30
EPIC4_PROCESS_TRIAL_COUNT = 3
EPIC4_PROCESS_TIMEOUT_SECONDS = 30
RR04_MINIMUM_ABSOLUTE_REDUCTION_MS = 0.250
RR04_MINIMUM_RELATIVE_REDUCTION_PERCENT = 20.0

EPIC4_CATALOG_SCENARIO_BOUNDARIES: Mapping[str, ExecutionBoundary] = MappingProxyType(
    {
        "catalog.publication.one": "registry_pre_dispatch_only",
        "catalog.publication.partial": "registry_pre_dispatch_only",
        "catalog.publication.full": "registry_pre_dispatch_only",
        "catalog.publication.repeated": "registry_pre_dispatch_only",
        "catalog.freshness.assignment_remove": "registry_pre_dispatch_only",
        "catalog.freshness.assignment_add": "registry_pre_dispatch_only",
        "catalog.freshness.assignment_replace": "registry_pre_dispatch_only",
        "catalog.freshness.parameter_required": "registry_pre_dispatch_only",
        "catalog.freshness.parameter_type": "registry_pre_dispatch_only",
        "catalog.freshness.parameter_add_remove": "registry_pre_dispatch_only",
        "catalog.concurrent.publication.1": "isolated_database_threaded",
        "catalog.concurrent.publication.10": "isolated_database_threaded",
        "catalog.concurrent.publication.25": "isolated_database_threaded",
        "catalog.concurrent.multi_context.1": "isolated_database_threaded",
        "catalog.concurrent.multi_context.10": "isolated_database_threaded",
        "catalog.concurrent.multi_context.25": "isolated_database_threaded",
        "catalog.security.removed_assignment_execution": "canonical_dispatch_included",
        "catalog.security.capability_revoked_execution": "canonical_dispatch_included",
        "catalog.security.workspace_disabled_execution": "canonical_dispatch_included",
        "catalog.security.dataset_visibility_revoked_execution": "canonical_dispatch_included",
        "catalog.validator.construction.start": "registry_pre_dispatch_only",
        "catalog.validator.construction.read": "registry_pre_dispatch_only",
        "catalog.validator.cold.valid": "registry_pre_dispatch_only",
        "catalog.validator.warm.valid": "registry_pre_dispatch_only",
        "catalog.validator.invalid": "registry_pre_dispatch_only",
        "catalog.validator.nested.valid": "registry_pre_dispatch_only",
        "catalog.validator.different_action": "registry_pre_dispatch_only",
        "catalog.provider_format.openai": "registry_pre_dispatch_only",
        "catalog.provider_format.openai_responses": "registry_pre_dispatch_only",
        "catalog.provider_format.gemini": "registry_pre_dispatch_only",
        "catalog.provider_format.anthropic": "registry_pre_dispatch_only",
        "catalog.memory.schema_construction": "benchmark_profiling_only",
        "catalog.memory.provider_formatting": "benchmark_profiling_only",
        "catalog.memory.validator_construction_and_validation": "benchmark_profiling_only",
        "catalog.memory.catalog_construction": "benchmark_profiling_only",
        "catalog.process.publication.cold": "independent_process_static_validation",
        "catalog.process.publication.warm": "independent_process_static_validation",
        "catalog.process.validation.cold": "independent_process_static_validation",
        "catalog.process.validation.warm": "independent_process_static_validation",
    }
)

EPIC4_DATABASE_SCENARIO_NAMES = tuple(
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
)

_UUID = "00000000-0000-0000-0000-000000000001"
_NESTED_VALID_PAYLOAD: dict[str, object] = {
    "tenant_id": _UUID,
    "definition_id": "00000000-0000-0000-0000-000000000002",
    "definition_version_id": "00000000-0000-0000-0000-000000000003",
    "parameters": {"mode": "benchmark"},
    "input_bindings": [
        {
            "input_name": "source",
            "dataset_id": "00000000-0000-0000-0000-000000000004",
            "dataset_version_id": "00000000-0000-0000-0000-000000000005",
        }
    ],
    "idempotency_key": "rr04-static-validation",
}
_READ_VALID_PAYLOAD: dict[str, object] = {"run_id": _UUID}
_READ_INVALID_PAYLOAD: dict[str, object] = {"run_id": "not-a-uuid"}
_STATIC_VALIDATION_PROCESS_SCRIPT = """
import json
import sys
import time

import django

django.setup()

from resource_registry.bifs.transformation.schemas import (
    GET_TRANSFORMATION_RUN_SCHEMA,
    validate_bif_input,
)

warmups = int(sys.argv[1])
iterations = int(sys.argv[2])
payload = {"run_id": "00000000-0000-0000-0000-000000000001"}
for _ in range(warmups):
    validate_bif_input(schema=GET_TRANSFORMATION_RUN_SCHEMA, payload=payload, correlation_id=None)
samples = []
for _ in range(iterations):
    started_ns = time.perf_counter_ns()
    validate_bif_input(schema=GET_TRANSFORMATION_RUN_SCHEMA, payload=payload, correlation_id=None)
    samples.append(time.perf_counter_ns() - started_ns)
print(json.dumps(samples))
"""


def build_epic4_catalog_definition() -> FunctionToolDefinition:
    """Build a deterministic grouped definition for isolated schema and envelope measurement.

    Returns:
        Provider-neutral grouped definition containing no persisted metadata or authority.
    """
    return FunctionToolDefinition(
        name="resource_registry_inspect",
        description="RR-04.1 deterministic provider-format benchmark definition.",
        parameters={
            "type": "object",
            "required": ["action", "arguments"],
            "additionalProperties": False,
            "properties": {
                "action": {"type": "string", "enum": ["list_datasets"]},
                "arguments": {
                    "type": "object",
                    "properties": {"limit": {"type": "integer"}},
                    "additionalProperties": False,
                },
            },
        },
    )


def build_epic4_schema_definitions() -> tuple[FunctionToolDefinition, ...]:
    """Build deterministic operation definitions for allocation measurement.

    Returns:
        Static Registry operation definitions that exercise grouped schema deep-copy work.
    """
    return tuple(
        FunctionToolDefinition(
            name=f"resource_registry_{action}",
            description="RR-04.1 deterministic schema benchmark operation.",
            parameters={
                "type": "object",
                "properties": {
                    "tenant_id": {"type": "string"},
                    "dataset_id": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["tenant_id"],
            },
        )
        for action in ("list_datasets", "get_dataset", "get_dataset_schema", "query_dataset")
    )


def build_static_transformation_validation_scenarios() -> tuple[BenchmarkScenario, ...]:
    """Build source-controlled static-schema validation and envelope scenarios.

    Returns:
        Scenarios that isolate validator construction, validator work, and provider formatting.
    """
    warm_validator = Draft202012Validator(
        START_TRANSFORMATION_RUN_SCHEMA,
        format_checker=FormatChecker(),
    )
    definition = build_epic4_catalog_definition()

    def validate_start_payload() -> bool:
        """Validate the actual nested transformation payload through production validation.

        Returns:
            True after a successful production validation.
        """
        validate_bif_input(
            schema=START_TRANSFORMATION_RUN_SCHEMA,
            payload=_NESTED_VALID_PAYLOAD,
            correlation_id=None,
        )
        return True

    def validate_invalid_read_payload() -> bool:
        """Verify that the production validator rejects an invalid static-schema payload.

        Returns:
            False when the invalid payload is rejected as expected.
        """
        try:
            validate_bif_input(
                schema=GET_TRANSFORMATION_RUN_SCHEMA,
                payload=_READ_INVALID_PAYLOAD,
                correlation_id=None,
            )
        except ValidationError:
            return False
        return True

    def validate_warm_payload() -> bool:
        """Validate a nested payload with a benchmark-local preconstructed validator.

        Returns:
            Whether the static payload satisfies the current source-controlled schema.
        """
        return bool(warm_validator.is_valid(_NESTED_VALID_PAYLOAD))

    def format_provider_tool(provider: str) -> dict[str, object]:
        """Format one static grouped definition in a declared provider envelope.

        Args:
            provider: Supported provider identifier from the controlled scenario inventory.

        Returns:
            A freshly formatted provider tool envelope.
        """
        return format_registry_tool(definition, cast(RegistryToolProvider, provider))

    def classify_validation(value: object) -> str:
        """Classify expected valid and invalid schema-validation outcomes.

        Args:
            value: Boolean validation outcome emitted by a scenario.

        Returns:
            ``success`` for valid payloads, ``rejected`` for expected invalid payloads.
        """
        return "success" if value is True else "rejected" if value is False else "failed"

    return (
        BenchmarkScenario(
            "catalog.validator.construction.start",
            "validator_construction",
            "source_controlled_static_schema",
            lambda: Draft202012Validator(
                START_TRANSFORMATION_RUN_SCHEMA, format_checker=FormatChecker()
            ),
            execution_boundary=EPIC4_CATALOG_SCENARIO_BOUNDARIES[
                "catalog.validator.construction.start"
            ],
        ),
        BenchmarkScenario(
            "catalog.validator.construction.read",
            "validator_construction",
            "source_controlled_static_schema",
            lambda: Draft202012Validator(
                GET_TRANSFORMATION_RUN_SCHEMA, format_checker=FormatChecker()
            ),
            execution_boundary=EPIC4_CATALOG_SCENARIO_BOUNDARIES[
                "catalog.validator.construction.read"
            ],
        ),
        BenchmarkScenario(
            "catalog.validator.cold.valid",
            "validation",
            "source_controlled_static_schema",
            validate_start_payload,
            classify_validation,
            EPIC4_CATALOG_SCENARIO_BOUNDARIES["catalog.validator.cold.valid"],
        ),
        BenchmarkScenario(
            "catalog.validator.warm.valid",
            "validation",
            "benchmark_local_validator_only",
            validate_warm_payload,
            classify_validation,
            EPIC4_CATALOG_SCENARIO_BOUNDARIES["catalog.validator.warm.valid"],
        ),
        BenchmarkScenario(
            "catalog.validator.invalid",
            "validation",
            "source_controlled_static_schema",
            validate_invalid_read_payload,
            classify_validation,
            EPIC4_CATALOG_SCENARIO_BOUNDARIES["catalog.validator.invalid"],
        ),
        BenchmarkScenario(
            "catalog.validator.nested.valid",
            "validation",
            "source_controlled_static_schema",
            validate_start_payload,
            classify_validation,
            EPIC4_CATALOG_SCENARIO_BOUNDARIES["catalog.validator.nested.valid"],
        ),
        BenchmarkScenario(
            "catalog.validator.different_action",
            "validation",
            "source_controlled_static_schema",
            _validate_read_payload,
            classify_validation,
            EPIC4_CATALOG_SCENARIO_BOUNDARIES["catalog.validator.different_action"],
        ),
        *tuple(
            BenchmarkScenario(
                f"catalog.provider_format.{provider}",
                "provider_formatting",
                "provider_specific_static_definition",
                partial(format_provider_tool, provider),
                execution_boundary=EPIC4_CATALOG_SCENARIO_BOUNDARIES[
                    f"catalog.provider_format.{provider}"
                ],
            )
            for provider in ("openai", "openai_responses", "gemini", "anthropic")
        ),
    )


def _validate_read_payload() -> bool:
    """Validate one real source-controlled read schema through production validation.

    Returns:
        True after the read payload passes the production validator.
    """
    validate_bif_input(
        schema=GET_TRANSFORMATION_RUN_SCHEMA,
        payload=_READ_VALID_PAYLOAD,
        correlation_id=None,
    )
    return True


def build_epic4_memory_results() -> tuple[BenchmarkRecord, ...]:
    """Measure bounded allocations separately from normal benchmark latency runs.

    Returns:
        Profiling-only records for schema construction, provider formatting, and validation.
    """
    schema_definitions = build_epic4_schema_definitions()
    definition = build_epic4_catalog_definition()
    return (
        measure_memory_usage(
            name="catalog.memory.schema_construction",
            operation=lambda: build_grouped_registry_tools(schema_definitions),
            iterations=EPIC4_MEMORY_ITERATIONS,
        ),
        measure_memory_usage(
            name="catalog.memory.provider_formatting",
            operation=lambda: format_registry_tool(definition, "openai"),
            iterations=EPIC4_MEMORY_ITERATIONS,
        ),
        measure_memory_usage(
            name="catalog.memory.validator_construction_and_validation",
            operation=_validate_read_payload,
            iterations=EPIC4_MEMORY_ITERATIONS,
        ),
        _not_measured_memory_result(),
    )


def _not_measured_memory_result() -> BenchmarkRecord:
    """Declare database-backed catalog allocation profiling unavailable before its guarded run.

    Returns:
        A profiling-only unavailable record that cannot be mistaken for a zero-byte measurement.
    """
    return {
        "name": "catalog.memory.catalog_construction",
        "category": "memory",
        "kind": "isolated_database_profiling",
        "execution_boundary": "not_measured",
        "status": "not_measured",
        "reason": (
            "Catalog allocation profiling requires the explicit isolated PostgreSQL benchmark "
            "command; no synthetic substitute is used."
        ),
        "warmups": 0,
        "iterations": 0,
        "sample_count": 0,
        "error_count": 0,
        "outcomes": {},
        "latency": None,
        "stages": {},
        "result_payload_bytes": None,
        "authorization_context_build_count_per_operation": None,
        "memory": None,
    }


def measure_memory_usage(
    *,
    name: str,
    operation: Callable[[], object],
    iterations: int,
) -> BenchmarkRecord:
    """Measure allocation and retained-byte deltas without timing the profiled work.

    Args:
        name: Stable benchmark scenario identifier.
        operation: Isolated operation whose allocation behavior is measured.
        iterations: Number of successful operations to profile.

    Returns:
        A profiling-only benchmark record. Latency is intentionally unavailable because
        ``tracemalloc`` materially changes wall-clock timing.

    Raises:
        ValueError: If the profiling iteration count is not positive.
    """
    if iterations < 1:
        raise ValueError("iterations must be positive")
    was_tracing = tracemalloc.is_tracing()
    if not was_tracing:
        tracemalloc.start()
    gc.collect()
    tracemalloc.clear_traces()
    before = tracemalloc.take_snapshot()
    error_count = 0
    for _ in range(iterations):
        try:
            operation()
        except Exception:
            error_count += 1
    gc.collect()
    after = tracemalloc.take_snapshot()
    current_bytes, peak_bytes = tracemalloc.get_traced_memory()
    retained_bytes = sum(
        statistic.size_diff
        for statistic in after.compare_to(before, "lineno")
        if statistic.size_diff > 0
    )
    if not was_tracing:
        tracemalloc.stop()
    return {
        "name": name,
        "category": "memory",
        "kind": "bounded_tracemalloc",
        "execution_boundary": EPIC4_CATALOG_SCENARIO_BOUNDARIES[name],
        "status": "failed" if error_count else "measured",
        "reason": (
            "Profiled allocations are reported separately from latency."
            if not error_count
            else None
        ),
        "warmups": 0,
        "iterations": iterations,
        "sample_count": iterations - error_count,
        "error_count": error_count,
        "outcomes": {"success": iterations - error_count, "failed": error_count},
        "latency": None,
        "stages": {},
        "result_payload_bytes": None,
        "authorization_context_build_count_per_operation": None,
        "memory": {
            "allocation_bytes": retained_bytes,
            "retained_bytes": retained_bytes,
            "current_traced_bytes": current_bytes,
            "peak_traced_bytes": peak_bytes,
        },
        "memory_methodology": (
            "bounded tracemalloc snapshot delta after garbage collection; this is not "
            "memory-leak detection"
        ),
    }


def run_independent_process_validation_scenarios(
    *,
    warmups: int,
    iterations: int,
) -> tuple[BenchmarkRecord, ...]:
    """Measure static validation in actual independent subprocess workers.

    Args:
        warmups: Excluded validation iterations per warm-process trial.
        iterations: Measured validation iterations per warm-process trial.

    Returns:
        Cold and warm independent-process benchmark records, or explicit failed records if
        process isolation cannot complete within the bounded timeout.
    """
    return (
        _not_measured_process_publication_case(name="catalog.process.publication.cold"),
        _not_measured_process_publication_case(name="catalog.process.publication.warm"),
        _run_process_validation_case(
            name="catalog.process.validation.cold",
            warmups=0,
            iterations=1,
        ),
        _run_process_validation_case(
            name="catalog.process.validation.warm",
            warmups=warmups,
            iterations=iterations,
        ),
    )


def _not_measured_process_publication_case(*, name: str) -> BenchmarkRecord:
    """Declare a database publication process case unavailable without sharing a test database.

    Args:
        name: Stable cold or warm process scenario identifier.

    Returns:
        Explicit unavailable process-isolation record.
    """
    return {
        "name": name,
        "category": "independent_process",
        "kind": "isolated_database_subprocess",
        "execution_boundary": "not_measured",
        "status": "not_measured",
        "reason": (
            "The isolated pytest transaction database is not safely shareable with independent "
            "publication workers; no thread substitute is labeled as independent process work."
        ),
        "warmups": 0,
        "iterations": 0,
        "sample_count": 0,
        "error_count": 0,
        "outcomes": {},
        "latency": None,
        "stages": {},
        "result_payload_bytes": None,
        "authorization_context_build_count_per_operation": None,
        "process": {
            "process_count": 0,
            "state_shared_between_trials": None,
        },
    }


def _run_process_validation_case(
    *,
    name: str,
    warmups: int,
    iterations: int,
) -> BenchmarkRecord:
    """Run one static-validation workload across bounded independent process trials.

    Args:
        name: Stable process benchmark scenario identifier.
        warmups: Excluded child-process iterations.
        iterations: Timed child-process iterations.

    Returns:
        One sanitized process-isolation result record.
    """
    validation_samples: list[int] = []
    process_wall_samples: list[int] = []
    error_count = 0
    for _ in range(EPIC4_PROCESS_TRIAL_COUNT):
        started_ns = perf_counter_ns()
        child_environment = {
            **os.environ,
            "DJANGO_SETTINGS_MODULE": "BotPlatform.test_settings",
        }
        try:
            completed_process = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    _STATIC_VALIDATION_PROCESS_SCRIPT,
                    str(warmups),
                    str(iterations),
                ],
                capture_output=True,
                check=False,
                cwd=os.getcwd(),
                env=child_environment,
                text=True,
                timeout=EPIC4_PROCESS_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            process_wall_samples.append(perf_counter_ns() - started_ns)
            error_count += 1
            continue
        process_wall_samples.append(perf_counter_ns() - started_ns)
        if completed_process.returncode != 0:
            error_count += 1
            continue
        try:
            payload = json.loads(completed_process.stdout)
        except json.JSONDecodeError:
            error_count += 1
            continue
        if not isinstance(payload, list) or not all(isinstance(value, int) for value in payload):
            error_count += 1
            continue
        validation_samples.extend(payload)
    status = "measured" if error_count == 0 else "failed"
    return {
        "name": name,
        "category": "independent_process",
        "kind": "subprocess_static_validation",
        "execution_boundary": EPIC4_CATALOG_SCENARIO_BOUNDARIES[name],
        "status": status,
        "reason": (
            "Each trial uses a new subprocess interpreter; no process-global Python state is shared."
            if status == "measured"
            else "An independent static-validation process did not complete safely."
        ),
        "warmups": warmups,
        "iterations": iterations * EPIC4_PROCESS_TRIAL_COUNT,
        "sample_count": len(validation_samples),
        "error_count": error_count,
        "outcomes": {"success": len(validation_samples), "failed": error_count},
        "latency": summarize_latency_ns(validation_samples),
        "stages": {},
        "result_payload_bytes": None,
        "authorization_context_build_count_per_operation": None,
        "process": {
            "process_count": EPIC4_PROCESS_TRIAL_COUNT,
            "process_wall_latency": summarize_latency_ns(process_wall_samples),
            "state_shared_between_trials": False,
            "process_launcher": "subprocess",
        },
    }


def evaluate_catalog_optimization_gate(
    *,
    baseline_p50_ms: float | None,
    candidate_p50_ms: float | None,
) -> BenchmarkRecord:
    """Apply the RR-04.1 prerequisite gate for a later measured cache candidate.

    Args:
        baseline_p50_ms: Current measured p50 for the Registry-owned operation.
        candidate_p50_ms: Measured p50 from a separately implemented candidate, if authorized.

    Returns:
        Explicit gate evidence. An absent candidate always defers RR-04.2 because hypothetical
        speedups are not evidence.
    """
    absolute_reduction_ms: float | None = None
    relative_reduction_percent: float | None = None
    if (
        isinstance(baseline_p50_ms, float)
        and isinstance(candidate_p50_ms, float)
        and baseline_p50_ms > 0
    ):
        absolute_reduction_ms = baseline_p50_ms - candidate_p50_ms
        relative_reduction_percent = absolute_reduction_ms / baseline_p50_ms * 100
    has_measured_candidate = (
        absolute_reduction_ms is not None and relative_reduction_percent is not None
    )
    qualifies = (
        absolute_reduction_ms is not None
        and relative_reduction_percent is not None
        and absolute_reduction_ms >= RR04_MINIMUM_ABSOLUTE_REDUCTION_MS
        and relative_reduction_percent >= RR04_MINIMUM_RELATIVE_REDUCTION_PERCENT
    )
    return {
        "baseline_p50_ms": baseline_p50_ms,
        "candidate_p50_ms": candidate_p50_ms,
        "absolute_reduction_ms": absolute_reduction_ms,
        "relative_reduction_percent": relative_reduction_percent,
        "minimum_absolute_reduction_ms": RR04_MINIMUM_ABSOLUTE_REDUCTION_MS,
        "minimum_relative_reduction_percent": RR04_MINIMUM_RELATIVE_REDUCTION_PERCENT,
        "candidate_measured": has_measured_candidate,
        "recommendation": "PROCEED" if qualifies else "DEFER",
        "rationale": (
            "A future candidate may be reviewed because it clears both RR-04.1 thresholds."
            if qualifies
            else "RR-04.2 remains unauthorized without a measured candidate that clears both "
            "absolute and relative Registry-owned thresholds."
        ),
    }
