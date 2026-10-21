from __future__ import annotations

from django.test import SimpleTestCase

from resource_registry.tests.async_durability.helpers import (
    AsyncVerificationMatrixBuilder,
    AsyncVerificationResult,
    AsyncVerificationScenario,
    VerificationDecisionGate,
)


class AsyncVerificationHelperTests(SimpleTestCase):
    """Verify the test-only async durability reporting helpers."""

    def test_matrix_builder_records_status_by_area(self) -> None:
        scenario = AsyncVerificationScenario(
            area="Restart recovery",
            expected_behavior="Abandoned running jobs recover after restart.",
            evidence_source="automated test",
        )
        result = AsyncVerificationResult(
            scenario=scenario,
            status="FAIL",
            verification_tests=("test_restart_recovery_observes_running_job_is_not_reclaimed",),
            observed_behavior="Running job remains running.",
            confirmed_gap_id="G-001",
            severity="High",
        )
        builder = AsyncVerificationMatrixBuilder()

        builder.record_result(result)

        self.assertEqual(builder.build_summary(), {"Restart recovery": "FAIL"})

    def test_decision_gate_requires_story_2_for_confirmed_gap(self) -> None:
        scenario = AsyncVerificationScenario(
            area="Cancellation behavior",
            expected_behavior="Workers can cancel jobs.",
            evidence_source="automated test",
        )
        result = AsyncVerificationResult(
            scenario=scenario,
            status="FAIL",
            verification_tests=("test_cancellation_contract_is_not_exposed",),
            observed_behavior="No cancellation method exists.",
            confirmed_gap_id="G-002",
            severity="High",
        )

        decision = VerificationDecisionGate().evaluate([result])

        self.assertEqual(decision, "Story 2 Required")

    def test_decision_gate_does_not_require_story_2_without_confirmed_gap(self) -> None:
        scenario = AsyncVerificationScenario(
            area="Timeout behavior",
            expected_behavior="Timeout behavior is verified.",
            evidence_source="automated test",
        )
        result = AsyncVerificationResult(
            scenario=scenario,
            status="NOT CONFIRMED",
            verification_tests=("test_timeout_behavior_not_confirmed",),
            observed_behavior="No runtime timeout scenario executed.",
        )

        decision = VerificationDecisionGate().evaluate([result])

        self.assertEqual(decision, "Story 2 Not Required")
