"""Define the comparable RR-05 cached-selection benchmark inventory."""

from __future__ import annotations

from types import MappingProxyType

from resource_registry.benchmarks.grouped_execution import ExecutionBoundary

EPIC5_ARTIFACT_SET = "epic5_selection_baseline"
EPIC5_CANDIDATE_ARTIFACT_SET = "epic5_selection_candidate"
EPIC5_SELECTION_ARTIFACT_SETS = frozenset({EPIC5_ARTIFACT_SET, EPIC5_CANDIDATE_ARTIFACT_SET})
EPIC5_SELECTION_SCALE_COUNTS = (1, 5, 10, 25, 50)

_PROMPT_CONTEXT_VERSIONLESS_SCENARIOS = tuple(
    f"selection.prompt_context.versionless.{count}" for count in EPIC5_SELECTION_SCALE_COUNTS
)
_PROMPT_CONTEXT_VERSION_PINNED_SCENARIOS = tuple(
    f"selection.prompt_context.version_pinned.{count}" for count in EPIC5_SELECTION_SCALE_COUNTS
)
_COMPARISON_DISCOVERY_SCENARIOS = tuple(
    f"selection.comparison_ids.{count}" for count in EPIC5_SELECTION_SCALE_COUNTS
)
_VERSION_REVALIDATION_SCENARIOS = tuple(
    f"selection.version_revalidation.{count}" for count in EPIC5_SELECTION_SCALE_COUNTS
)

EPIC5_DATABASE_SCENARIO_NAMES = (
    *_PROMPT_CONTEXT_VERSIONLESS_SCENARIOS,
    *_PROMPT_CONTEXT_VERSION_PINNED_SCENARIOS,
    *_COMPARISON_DISCOVERY_SCENARIOS,
    *_VERSION_REVALIDATION_SCENARIOS,
    "selection.mixed_validity.prompt_context",
    "selection.mixed_validity.comparison_ids",
    "selection.canonical_admission_freshness",
)

EPIC5_SELECTION_SCENARIO_BOUNDARIES: MappingProxyType[str, ExecutionBoundary] = MappingProxyType(
    {
        **{
            name: "registry_pre_dispatch_only"
            for name in (
                *_PROMPT_CONTEXT_VERSIONLESS_SCENARIOS,
                *_PROMPT_CONTEXT_VERSION_PINNED_SCENARIOS,
                *_COMPARISON_DISCOVERY_SCENARIOS,
                *_VERSION_REVALIDATION_SCENARIOS,
                "selection.mixed_validity.prompt_context",
                "selection.mixed_validity.comparison_ids",
            )
        },
        "selection.canonical_admission_freshness": "canonical_dispatch_included",
    }
)
