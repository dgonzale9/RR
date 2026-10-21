from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class TransformationStructuralValidationFinding:
    """Describe one structural validation finding."""

    code: str
    message: str
    path: str = "$"
    severity: str = "error"


@dataclass(frozen=True)
class TransformationStructuralValidationResult:
    """Represent the deterministic result of structural DSL validation."""

    success: bool
    findings: tuple[TransformationStructuralValidationFinding, ...] = field(default_factory=tuple)
    warnings: tuple[TransformationStructuralValidationFinding, ...] = field(default_factory=tuple)
    normalized_specification: Mapping[str, object] | None = None
