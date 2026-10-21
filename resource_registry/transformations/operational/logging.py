from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import ClassVar

from resource_registry.models import TransformationRun
from resource_registry.services.payload_sanitizer import sanitize_payload

TRANSFORMATION_OPERATIONAL_LOGGER_NAME = "transformation-operations"


class TransformationOperationalLogContextBuilder:
    """Build allowlisted sanitized transformation operational log fields."""

    _ALLOWED_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "tenant_id",
            "run_id",
            "definition_id",
            "definition_version_id",
            "worker_id",
            "attempt_number",
            "provider_key",
            "operation_key",
            "outcome",
            "duration_ms",
            "retry_count",
            "correlation_id",
            "stable_error_code",
        },
    )

    def build(
        self,
        *,
        values: Mapping[str, object],
    ) -> dict[str, object]:
        """Return only approved fields after recursive sanitization."""
        selected = {key: value for key, value in values.items() if key in self._ALLOWED_FIELDS}
        sanitized = sanitize_payload(selected)
        return sanitized if isinstance(sanitized, dict) else {}

    def build_for_run(
        self,
        *,
        run: TransformationRun,
        **values: object,
    ) -> dict[str, object]:
        """Build approved operational fields from one durable run."""
        return self.build(
            values={
                "tenant_id": str(run.tenant_id),
                "run_id": str(run.id),
                "definition_id": str(run.definition_id),
                "definition_version_id": str(run.definition_version_id),
                "retry_count": int(run.retry_count),
                "correlation_id": run.correlation_id,
                **values,
            },
        )

    def sanitize_exception(self, error: Exception) -> dict[str, str]:
        """Return a stable exception classification without its unsafe message."""
        return {
            "stable_error_code": "unexpected_operational_error",
            "outcome": error.__class__.__name__,
        }


def get_transformation_operational_logger() -> logging.Logger:
    """Return the dedicated structured transformation logger."""
    return logging.getLogger(TRANSFORMATION_OPERATIONAL_LOGGER_NAME)
