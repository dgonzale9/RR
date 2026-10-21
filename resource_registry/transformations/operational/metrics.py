from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Protocol

_ALLOWED_LABELS = frozenset(
    {"provider_key", "operation_key", "outcome", "policy_kind", "error_code"},
)


class TransformationOperationalMetricsPort(Protocol):
    """Expose bounded transformation operational metrics without backend coupling."""

    def is_available(self) -> bool:
        """Return whether the metrics sink is available."""

    def record_queue(
        self,
        *,
        queued_count: int,
        running_count: int,
        output_pending_count: int,
        oldest_queued_age_seconds: float,
    ) -> None:
        """Record queue and active-run gauges."""

    def record_claim(self, *, claimed: bool) -> None:
        """Record one queue claim attempt."""

    def record_attempt(
        self,
        *,
        outcome: str,
        error_code: str,
        execution_ms: int,
        reused_count: int,
    ) -> None:
        """Record one execution attempt and duration."""

    def record_lifecycle(self, *, action: str) -> None:
        """Record one durable lifecycle action."""

    def record_recovery(self, *, action: str) -> None:
        """Record one deterministic recovery action."""

    def record_authorization(self, *, outcome: str, error_code: str = "") -> None:
        """Record one transformation authorization decision."""

    def record_output_finalization(
        self,
        *,
        policy_kind: str,
        outcome: str,
        duration_ms: int,
        error_code: str = "",
        correlation_id: str | None = None,
    ) -> None:
        """Record one output finalization result and duration."""

    def record_provider_failure(
        self,
        *,
        provider_key: str,
        operation_key: str,
        error_code: str,
    ) -> None:
        """Record one provider failure using bounded identities."""

    def record_capability_drift(
        self,
        *,
        provider_key: str,
        operation_key: str,
    ) -> None:
        """Record one Story 5 capability-drift failure."""

    def record_checkpoint_reuse(self, *, outcome: str) -> None:
        """Record checkpoint reuse or rejection."""

    def record_finalization_queue(
        self,
        *,
        pending_count: int,
        active_count: int,
        stale_count: int,
        cleanup_pending_count: int,
        oldest_pending_age_seconds: float,
    ) -> None:
        """Record durable output-finalization and cleanup gauges."""

    def record_finalization_claim(self, *, claimed: bool) -> None:
        """Record one output-finalization claim attempt."""

    def record_finalization_lifecycle(self, *, action: str, error_code: str = "") -> None:
        """Record one output-finalization lifecycle decision."""

    def record_workspace(
        self,
        *,
        leased_bytes: int | None,
        outcome: str,
        error_code: str = "",
    ) -> None:
        """Record bounded workspace usage, cleanup, and quota outcomes."""


class ProductionMetricsAdapter:
    """Emit bounded gauge, counter, and histogram observations to structured logging."""

    def __init__(self, *, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger("transformation-operations")

    def is_available(self) -> bool:
        return not self._logger.disabled and self._logger.hasHandlers()

    def record_queue(
        self,
        *,
        queued_count: int,
        running_count: int,
        output_pending_count: int,
        oldest_queued_age_seconds: float,
    ) -> None:
        self._emit("gauge", "transformation_queue_depth", queued_count, {"outcome": "queued"})
        self._emit("gauge", "transformation_active_runs", running_count, {})
        self._emit(
            "gauge",
            "transformation_output_pending",
            output_pending_count,
            {},
        )
        self._emit(
            "gauge",
            "transformation_oldest_queued_age_seconds",
            oldest_queued_age_seconds,
            {},
        )

    def record_claim(self, *, claimed: bool) -> None:
        self._emit(
            "counter",
            "transformation_claims_total",
            1,
            {"outcome": "claimed" if claimed else "idle"},
        )

    def record_attempt(
        self,
        *,
        outcome: str,
        error_code: str,
        execution_ms: int,
        reused_count: int,
    ) -> None:
        labels = {"outcome": outcome, "error_code": error_code}
        self._emit("counter", "transformation_attempts_total", 1, labels)
        self._emit(
            "histogram",
            "transformation_execution_duration_seconds",
            execution_ms / 1000,
            {"outcome": outcome},
        )
        self._emit(
            "counter",
            "transformation_checkpoint_reuse_total",
            reused_count,
            {"outcome": "reused"},
        )

    def record_lifecycle(self, *, action: str) -> None:
        action_value = str(action)
        self._emit(
            "counter",
            "transformation_runs_total",
            1,
            {"outcome": action_value},
        )
        if action_value == "CANCELLED":
            self._emit("counter", "transformation_cancellations_total", 1, {})
        if action_value == "REQUEUED":
            self._emit(
                "counter",
                "transformation_retries_total",
                1,
                {"outcome": action_value},
            )
        if action_value == "FAILED":
            self._emit("counter", "transformation_failures_total", 1, {})

    def record_recovery(self, *, action: str) -> None:
        self._emit(
            "counter",
            "transformation_recoveries_total",
            1,
            {"outcome": str(action)},
        )

    def record_authorization(self, *, outcome: str, error_code: str = "") -> None:
        self._emit(
            "counter",
            "transformation_authorization_decisions_total",
            1,
            {"outcome": outcome, "error_code": error_code},
        )

    def record_output_finalization(
        self,
        *,
        policy_kind: str,
        outcome: str,
        duration_ms: int,
        error_code: str = "",
        correlation_id: str | None = None,
    ) -> None:
        labels = {
            "policy_kind": policy_kind,
            "outcome": outcome,
            "error_code": error_code,
        }
        self._emit(
            "counter",
            "transformation_output_finalizations_total",
            1,
            labels,
            correlation_id=correlation_id,
        )
        self._emit(
            "histogram",
            "transformation_output_finalization_duration_seconds",
            duration_ms / 1000,
            {"policy_kind": policy_kind, "outcome": outcome},
            correlation_id=correlation_id,
        )

    def record_provider_failure(
        self,
        *,
        provider_key: str,
        operation_key: str,
        error_code: str,
    ) -> None:
        self._emit(
            "counter",
            "transformation_provider_failures_total",
            1,
            {
                "provider_key": provider_key,
                "operation_key": operation_key,
                "error_code": error_code,
            },
        )

    def record_capability_drift(
        self,
        *,
        provider_key: str,
        operation_key: str,
    ) -> None:
        self._emit(
            "counter",
            "transformation_capability_drift_total",
            1,
            {"provider_key": provider_key, "operation_key": operation_key},
        )

    def record_checkpoint_reuse(self, *, outcome: str) -> None:
        self._emit(
            "counter",
            "transformation_checkpoint_reuse_total",
            1,
            {"outcome": outcome},
        )

    def record_finalization_queue(
        self,
        *,
        pending_count: int,
        active_count: int,
        stale_count: int,
        cleanup_pending_count: int,
        oldest_pending_age_seconds: float,
    ) -> None:
        """Emit bounded finalization and cleanup gauges."""
        values = {
            "transformation_finalization_pending": pending_count,
            "transformation_finalization_active": active_count,
            "transformation_finalization_stale": stale_count,
            "transformation_workspace_cleanup_pending": cleanup_pending_count,
            "transformation_finalization_oldest_pending_age_seconds": (
                oldest_pending_age_seconds
            ),
        }
        for name, value in values.items():
            self._emit("gauge", name, value, {})

    def record_finalization_claim(self, *, claimed: bool) -> None:
        """Emit one bounded finalization claim observation."""
        self._emit(
            "counter",
            "transformation_finalization_claims_total",
            1,
            {"outcome": "claimed" if claimed else "idle"},
        )

    def record_finalization_lifecycle(self, *, action: str, error_code: str = "") -> None:
        """Emit retry, exhaustion, recovery, or terminal finalization outcomes."""
        self._emit(
            "counter",
            "transformation_finalization_lifecycle_total",
            1,
            {"outcome": action, "error_code": error_code},
        )

    def record_workspace(
        self,
        *,
        leased_bytes: int | None,
        outcome: str,
        error_code: str = "",
    ) -> None:
        """Emit workspace usage and lifecycle observations."""
        if leased_bytes is not None:
            self._emit("gauge", "transformation_workspace_leased_bytes", leased_bytes, {})
        self._emit(
            "counter",
            "transformation_workspace_operations_total",
            1,
            {"outcome": outcome, "error_code": error_code},
        )

    def _emit(
        self,
        metric_type: str,
        metric_name: str,
        value: int | float,
        labels: Mapping[str, str],
        *,
        correlation_id: str | None = None,
    ) -> None:
        if not set(labels).issubset(_ALLOWED_LABELS):
            raise ValueError("Transformation metric contains a high-cardinality label.")
        self._logger.info(
            "Transformation metric",
            extra={
                "metric_type": metric_type,
                "metric_name": metric_name,
                "metric_value": value,
                "metric_labels": dict(labels),
                "metric_correlation_id": correlation_id or "",
            },
        )


class NoOpMetricsAdapter:
    """Provide a disabled metrics adapter for tests and local development."""

    def is_available(self) -> bool:
        return False

    def record_queue(self, **values: object) -> None:
        _ = values

    def record_claim(self, *, claimed: bool) -> None:
        _ = claimed

    def record_attempt(self, **values: object) -> None:
        _ = values

    def record_lifecycle(self, *, action: str) -> None:
        _ = action

    def record_recovery(self, *, action: str) -> None:
        _ = action

    def record_authorization(self, *, outcome: str, error_code: str = "") -> None:
        _ = outcome, error_code

    def record_output_finalization(self, **values: object) -> None:
        _ = values

    def record_provider_failure(self, **values: object) -> None:
        _ = values

    def record_capability_drift(self, **values: object) -> None:
        _ = values

    def record_checkpoint_reuse(self, *, outcome: str) -> None:
        _ = outcome

    def record_finalization_queue(self, **values: object) -> None:
        _ = values

    def record_finalization_claim(self, *, claimed: bool) -> None:
        _ = claimed

    def record_finalization_lifecycle(self, *, action: str, error_code: str = "") -> None:
        _ = action, error_code

    def record_workspace(self, **values: object) -> None:
        _ = values
