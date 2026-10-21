from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from threading import Event

from resource_registry.transformations.execution import (
    TransformationRunAttemptExecutionRequest,
    TransformationRunAttemptExecutionResult,
)


class TransformationRunLifecycleAction(StrEnum):
    """Identify one durable Story 6 lifecycle decision."""

    OUTPUT_PENDING = "OUTPUT_PENDING"
    REQUEUED = "REQUEUED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    STALE = "STALE"


@dataclass(frozen=True)
class TransformationRunAttemptClaimMetadata:
    """Supply operational ownership for atomic Story 5 attempt initialization."""

    worker_id: str
    claimed_at: datetime
    lease_expires_at: datetime
    attempt_deadline_at: datetime | None = None

    def __post_init__(self) -> None:
        """Validate exact claim ownership and ordered timestamps."""
        if not self.worker_id or self.worker_id != self.worker_id.strip():
            raise ValueError("Worker ID must be an exact non-empty string.")
        if self.lease_expires_at <= self.claimed_at:
            raise ValueError("Lease expiration must follow claim time.")
        if self.attempt_deadline_at is not None and self.attempt_deadline_at <= self.claimed_at:
            raise ValueError("Attempt deadline must follow claim time.")


@dataclass(frozen=True)
class ClaimedTransformationRun:
    """Return one atomically claimed and initialized run attempt."""

    worker_id: str
    execution_request: TransformationRunAttemptExecutionRequest
    lease_lost_event: Event


@dataclass(frozen=True)
class TransformationRunLifecycleResult:
    """Report one sanitized durable worker lifecycle transition."""

    run_id: str
    attempt_number: int
    action: TransformationRunLifecycleAction
    retry_count: int
    error_code: str | None = None
    tenant_id: str | None = None
    definition_id: str | None = None
    definition_version_id: str | None = None
    correlation_id: str | None = None


@dataclass(frozen=True)
class TransformationRunCoordinatorResult:
    """Combine Story 5 attempt and Story 6 lifecycle outcomes."""

    attempt: TransformationRunAttemptExecutionResult
    lifecycle: TransformationRunLifecycleResult


@dataclass(frozen=True)
class TransformationRunQueueMetricsSnapshot:
    """Expose safe worker queue gauges without persisted metric rows."""

    queued_count: int
    running_count: int
    output_pending_count: int
    oldest_queued_age_seconds: float
