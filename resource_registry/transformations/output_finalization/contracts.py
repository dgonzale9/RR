from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class TransformationOutputFinalizationClaim:
    """Carry the sole durable output-finalization fence."""

    tenant_id: str
    run_id: str
    worker_id: str
    token: str
    attempt_number: int
    claimed_at: datetime
    lease_expires_at: datetime
    deadline_at: datetime
    correlation_id: str
