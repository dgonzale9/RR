from __future__ import annotations

from django.test import SimpleTestCase

from resource_registry.services.async_job_execution_timeout import (
    build_execution_timeout_message,
    has_execution_timeout_elapsed,
    normalize_execution_timeout_seconds,
)


class AsyncJobExecutionTimeoutTests(SimpleTestCase):
    def test_normalize_execution_timeout_seconds_disables_none_zero_and_negative(self) -> None:
        self.assertIsNone(normalize_execution_timeout_seconds(None))
        self.assertIsNone(normalize_execution_timeout_seconds(0))
        self.assertIsNone(normalize_execution_timeout_seconds(-1))

    def test_normalize_execution_timeout_seconds_preserves_positive_values(self) -> None:
        self.assertEqual(normalize_execution_timeout_seconds(2), 2.0)
        self.assertEqual(normalize_execution_timeout_seconds(2.5), 2.5)

    def test_has_execution_timeout_elapsed_is_disabled_without_timeout(self) -> None:
        self.assertFalse(has_execution_timeout_elapsed(elapsed_seconds=999, timeout_seconds=None))

    def test_has_execution_timeout_elapsed_uses_normalized_boundary(self) -> None:
        self.assertFalse(has_execution_timeout_elapsed(elapsed_seconds=0.9, timeout_seconds=1))
        self.assertTrue(has_execution_timeout_elapsed(elapsed_seconds=1, timeout_seconds=1))

    def test_build_execution_timeout_message_includes_family_and_duration(self) -> None:
        message = build_execution_timeout_message(
            job_family="Dataset query job",
            timeout_seconds=10,
        )

        self.assertIn("Dataset query job", message)
        self.assertIn("10 seconds", message)
