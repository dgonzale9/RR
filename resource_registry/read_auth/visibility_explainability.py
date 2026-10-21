from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from resource_registry.read_auth.visibility_diagnostics import VisibilityDiagnosticsReport


@dataclass(frozen=True)
class VisibilityDecision:
    field_name: str
    field_type: Literal["schema_column", "profile_metric"]
    action: Literal["visible", "masked", "hidden"]
    reason: str
    matched_rule: str | None
    matched_classifications: tuple[str, ...] = ()
    policy_source: str | None = None
    policy_revision: int | None = None
    role: str | None = None
    scopes: tuple[str, ...] = ()


@dataclass(frozen=True)
class VisibilityExplanation:
    dataset_id: str | None
    policy_key: str
    policy_source: str
    policy_revision: int | None
    decisions: tuple[VisibilityDecision, ...] = ()
    summary: dict[str, int] = field(default_factory=dict)
    diagnostics: VisibilityDiagnosticsReport | None = None


@dataclass(frozen=True)
class FilteredWithExplanation:
    filtered: Any
    explanation: VisibilityExplanation
