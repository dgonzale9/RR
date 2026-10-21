from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TransformationSemanticValidationFinding:
    """Describe one deterministic and sanitized semantic validation finding."""

    code: str
    path: str
    message: str
    severity: str = "error"

    def to_dict(self) -> dict[str, str]:
        """Return a JSON-serializable representation of the finding.

        Returns:
            Finding fields containing only JSON string values.
        """
        return {
            "code": self.code,
            "path": self.path,
            "severity": self.severity,
            "message": self.message,
        }


@dataclass(frozen=True)
class TransformationSemanticValidationResult:
    """Represent the immutable result of specification-level semantic validation."""

    is_valid: bool
    findings: tuple[TransformationSemanticValidationFinding, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable representation of the result.

        Returns:
            Result status and ordered finding payloads.
        """
        return {
            "is_valid": self.is_valid,
            "findings": [finding.to_dict() for finding in self.findings],
        }


def order_semantic_findings(
    findings: list[TransformationSemanticValidationFinding],
) -> tuple[TransformationSemanticValidationFinding, ...]:
    """Return findings ordered by the accepted deterministic comparison contract.

    Args:
        findings: Findings collected during semantic validation.

    Returns:
        Findings ordered by path, code, severity, and message.
    """
    return tuple(
        sorted(
            findings,
            key=lambda finding: (
                finding.path,
                finding.code,
                finding.severity,
                finding.message,
            ),
        ),
    )
