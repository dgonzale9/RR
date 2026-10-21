from __future__ import annotations

from collections.abc import Callable
from threading import Event, Thread

from django.db import close_old_connections

from resource_registry.models import TransformationRun, TransformationRunStatus


class DurableTransformationRunCancellationSignal:
    """Expose durable cancellation and lease loss to Story 5 providers."""

    def __init__(
        self,
        *,
        tenant_id: str,
        run_id: str,
        attempt_number: int,
        attempt_token: str,
        lease_lost_event: Event,
    ) -> None:
        self._tenant_id = tenant_id
        self._run_id = run_id
        self._attempt_number = attempt_number
        self._attempt_token = attempt_token
        self._lease_lost_event = lease_lost_event

    def is_cancellation_requested(self) -> bool:
        """Return true for durable cancellation, lease loss, or a stale fence."""
        if self._lease_lost_event.is_set():
            return True
        return not TransformationRun.objects.filter(
            tenant_id=self._tenant_id,
            id=self._run_id,
            status=TransformationRunStatus.RUNNING,
            latest_attempt_number=self._attempt_number,
            current_attempt_token=self._attempt_token,
            cancellation_requested_at__isnull=True,
        ).exists()


class TransformationRunHeartbeat:
    """Renew one fenced lease from a companion thread during blocking execution."""

    def __init__(
        self,
        *,
        renew_lease: Callable[[], bool],
        interval_seconds: float,
        lease_lost_event: Event,
    ) -> None:
        self._renew_lease = renew_lease
        self._interval_seconds = max(float(interval_seconds), 0.1)
        self._lease_lost_event = lease_lost_event
        self._stop_event = Event()
        self._thread = Thread(target=self._run, name="transformation-run-heartbeat", daemon=True)

    def start(self) -> None:
        """Start companion lease renewal."""
        self._thread.start()

    def stop(self) -> None:
        """Stop renewal and wait briefly for the companion thread."""
        self._stop_event.set()
        self._thread.join(timeout=self._interval_seconds + 1.0)

    def _run(self) -> None:
        close_old_connections()
        try:
            while not self._stop_event.wait(self._interval_seconds):
                if not self._renew_lease():
                    self._lease_lost_event.set()
                    return
        finally:
            close_old_connections()
