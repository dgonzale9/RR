"""Declare the fixed RR-03.1 grouped-execution benchmark contract.

This module intentionally contains no benchmark implementation or production-tool
behaviour.  Keeping the scenario inventory in one place lets integrity tests reject
an accidental boundary relabeling before a report is written.
"""

from __future__ import annotations

from typing import Literal, TypeAlias

ExecutionBoundary: TypeAlias = Literal[
    "registry_pre_dispatch_only",
    "shared_dispatch_stubbed_before_bif",
    "canonical_dispatch_included",
    "synthetic_projection_only",
    "isolated_database_threaded",
    "independent_process_static_validation",
    "benchmark_profiling_only",
    "not_measured",
]

VALID_EXECUTION_BOUNDARIES = frozenset(
    {
        "registry_pre_dispatch_only",
        "shared_dispatch_stubbed_before_bif",
        "canonical_dispatch_included",
        "synthetic_projection_only",
        "isolated_database_threaded",
        "independent_process_static_validation",
        "benchmark_profiling_only",
        "not_measured",
    }
)

# The inventory is deliberately more granular than the report categories.  It is
# used both by the database benchmark and by its fail-closed integrity tests.
GROUPED_EXECUTION_SCENARIO_BOUNDARIES: dict[str, ExecutionBoundary] = {
    "grouped.execution.list_datasets": "canonical_dispatch_included",
    "grouped.execution.dataset_context": "canonical_dispatch_included",
    "grouped.execution.transformation_list": "canonical_dispatch_included",
    "grouped.selection.smallest_group_parsed": "registry_pre_dispatch_only",
    "grouped.selection.largest_group_json": "registry_pre_dispatch_only",
    "grouped.selection.unknown_action": "registry_pre_dispatch_only",
    "grouped.selection.wrong_group": "registry_pre_dispatch_only",
    "grouped.validation.simple_valid": "registry_pre_dispatch_only",
    "grouped.validation.transformation_complex_valid": "registry_pre_dispatch_only",
    "grouped.validation.missing_required": "registry_pre_dispatch_only",
    "grouped.validation.wrong_type": "registry_pre_dispatch_only",
    "grouped.validation.unexpected_property": "registry_pre_dispatch_only",
    "grouped.validation.invalid_nested": "registry_pre_dispatch_only",
    "grouped.projection.small_unchanged": "synthetic_projection_only",
    "grouped.projection.large_unchanged": "synthetic_projection_only",
    "grouped.projection.suggested_bif": "synthetic_projection_only",
    "grouped.projection.recommended_bif": "synthetic_projection_only",
    "grouped.projection.hint": "synthetic_projection_only",
    "grouped.projection.error": "synthetic_projection_only",
    "grouped.projection.multiple_bif_names": "synthetic_projection_only",
    "grouped.projection.malformed_json": "synthetic_projection_only",
    "grouped.projection.non_object_json": "synthetic_projection_only",
    "grouped.projection.business_data_unchanged": "synthetic_projection_only",
    "grouped.freshness.two_successes": "canonical_dispatch_included",
    "grouped.freshness.permission_revoked": "canonical_dispatch_included",
    "grouped.freshness.capability_changed": "canonical_dispatch_included",
    "grouped.freshness.visibility_changed": "canonical_dispatch_included",
    "grouped.freshness.stale_selection": "canonical_dispatch_included",
    "grouped.freshness.mixed_success_denied": "canonical_dispatch_included",
    "grouped.concurrency.1": "canonical_dispatch_included",
    "grouped.concurrency.10": "canonical_dispatch_included",
    "grouped.concurrency.25": "canonical_dispatch_included",
}

REQUIRED_GROUPED_EXECUTION_SCENARIO_NAMES = tuple(GROUPED_EXECUTION_SCENARIO_BOUNDARIES)
CANONICAL_GROUPED_EXECUTION_SCENARIO_NAMES = tuple(
    name
    for name, boundary in GROUPED_EXECUTION_SCENARIO_BOUNDARIES.items()
    if boundary == "canonical_dispatch_included"
)

GROUPED_EXECUTION_FOLLOW_ON_STORY_RECOMMENDATIONS: tuple[dict[str, str], ...] = (
    {
        "story_id": "RR-03.2",
        "story_name": "Result Normalization Reduction",
        "recommendation": "SKIP",
        "rationale": (
            "Result projection p50 was approximately 0.015-0.092 ms for the tested payloads. "
            "Reconsider only if representative larger payloads demonstrate material "
            "Registry-owned cost."
        ),
    },
    {
        "story_id": "RR-03.3",
        "story_name": "Selected-Action Validator Reuse",
        "recommendation": "DEFER",
        "rationale": (
            "Selected-action resolution and schema validation were approximately 0.02-0.13 ms. "
            "Validator reuse is not justified by the current measurements."
        ),
    },
)


def validate_execution_boundary(value: str | None) -> ExecutionBoundary:
    """Validate and return a benchmark execution-boundary label.

    Args:
        value: Label supplied by a benchmark scenario declaration.

    Returns:
        One of the sanctioned RR-03.1 boundary labels.

    Raises:
        ValueError: If the label is absent or not a sanctioned value.
    """
    if value not in VALID_EXECUTION_BOUNDARIES:
        supported = ", ".join(sorted(VALID_EXECUTION_BOUNDARIES))
        raise ValueError(f"Benchmark scenario requires a valid execution boundary: {supported}.")
    return value  # type: ignore[return-value]
