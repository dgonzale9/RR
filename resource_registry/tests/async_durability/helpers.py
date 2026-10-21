from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

VerificationStatus = Literal["PASS", "FAIL", "NOT CONFIRMED", "UNKNOWN"]
GapSeverity = Literal["Critical", "High", "Medium", "Low", ""]


@dataclass(frozen=True)
class AsyncVerificationScenario:
    """Describe one async durability verification scenario."""

    area: str
    expected_behavior: str
    evidence_source: str


@dataclass(frozen=True)
class AsyncVerificationResult:
    """Capture the observed outcome for an async durability scenario."""

    scenario: AsyncVerificationScenario
    status: VerificationStatus
    verification_tests: tuple[str, ...]
    observed_behavior: str
    confirmed_gap_id: str = ""
    severity: GapSeverity = ""
    notes: str = ""


@dataclass
class AsyncVerificationMatrixBuilder:
    """Build an auditable async verification matrix from scenario results."""

    results: list[AsyncVerificationResult] = field(default_factory=list)

    def record_result(self, result: AsyncVerificationResult) -> None:
        """Record one verification result for matrix generation.

        Args:
            result: Scenario result to add to the matrix.
        """
        self.results.append(result)

    def build_summary(self) -> dict[str, VerificationStatus]:
        """Build a status lookup keyed by verification area.

        Returns:
            A mapping of verification area to the latest recorded status.
        """
        return {result.scenario.area: result.status for result in self.results}


class VerificationDecisionGate:
    """Determine whether follow-up hardening is required from verified results."""

    def evaluate(self, results: list[AsyncVerificationResult]) -> str:
        """Evaluate the Story 1 decision gate.

        Args:
            results: Completed verification results.

        Returns:
            ``Story 2 Required`` when one or more confirmed gaps exist, otherwise
            ``Story 2 Not Required``.
        """
        has_confirmed_gap = any(
            result.status == "FAIL" and bool(result.confirmed_gap_id) for result in results
        )
        return "Story 2 Required" if has_confirmed_gap else "Story 2 Not Required"
