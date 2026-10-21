from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from threading import Event

from resource_registry.transformations.output_finalization.contracts import (
    TransformationOutputFinalizationClaim,
)


class TransformationOutputFinalizationLifecycleAction(StrEnum):
    """Identify one durable output-finalization lifecycle decision."""

    SUCCEEDED = "SUCCEEDED"
    REQUEUED = "REQUEUED"
    FAILED = "FAILED"
    STALE = "STALE"


@dataclass(frozen=True)
class ClaimedTransformationOutputFinalization:
    """Return one atomically claimed output-finalization job."""

    claim: TransformationOutputFinalizationClaim
    lease_lost_event: Event


@dataclass(frozen=True)
class TransformationOutputFinalizationLifecycleResult:
    """Report one sanitized durable finalization transition."""

    tenant_id: str
    run_id: str
    attempt_number: int
    retry_count: int
    action: TransformationOutputFinalizationLifecycleAction
    error_code: str | None = None
    correlation_id: str | None = None


@dataclass(frozen=True)
class TransformationOutputFinalizationQueueSnapshot:
    """Expose bounded finalization queue and recovery gauges."""

    pending_count: int
    active_count: int
    stale_count: int
    cleanup_pending_count: int
    oldest_pending_age_seconds: float


@dataclass(frozen=True)
class TransformationOutputFinalizationWorkerStats:
    """Summarize one output-finalization worker invocation."""

    claimed_runs: int
    processed_runs: int
    recovered_runs: int
    cleaned_leases: int
