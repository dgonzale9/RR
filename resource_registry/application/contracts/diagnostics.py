from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from resource_registry.application.contracts.base import (
    ApplicationContract,
    validate_duration_seconds,
    validate_progress,
)
from resource_registry.application.contracts.enums import HealthState
from resource_registry.application.contracts.immutable import (
    FrozenJsonMapping,
    freeze_json_mapping,
)


@dataclass(frozen=True, kw_only=True)
class TransformationDiagnosticSummary(ApplicationContract):
    """Expose transport-safe aggregate transformation diagnostics."""

    health_state: HealthState
    aggregate_counts: FrozenJsonMapping
    warnings: tuple[str, ...]
    durations: FrozenJsonMapping
    duration_seconds: float | None = None
    progress: float | None = None

    def __init__(  # noqa: PLR0913
        self,
        *,
        health_state: HealthState,
        aggregate_counts: Mapping[str, object] | None,
        warnings: tuple[str, ...],
        durations: Mapping[str, object] | None = None,
        duration_seconds: float | None = None,
        progress: float | None = None,
    ) -> None:
        """Normalize aggregates and warning ordering without exposing diagnostic payloads."""
        validate_duration_seconds(duration_seconds)
        validate_progress(progress)
        object.__setattr__(self, "contract_version", self.contract_version)
        object.__setattr__(self, "health_state", health_state)
        object.__setattr__(self, "aggregate_counts", freeze_json_mapping(aggregate_counts))
        object.__setattr__(self, "warnings", tuple(sorted(set(warnings))))
        object.__setattr__(self, "durations", freeze_json_mapping(durations))
        object.__setattr__(self, "duration_seconds", duration_seconds)
        object.__setattr__(self, "progress", progress)
