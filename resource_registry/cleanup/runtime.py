"""Wake durable cleanup from web processes after process creation, never from commands."""

from __future__ import annotations

import inspect
import logging
import os
import sys
from _thread import LockType
from concurrent.futures import Future
from threading import Event, Lock, Thread

from django.conf import settings
from django.db import close_old_connections

from BotServer.unicornai_threadpool_executor import UnicornAiThreadpoolExecutor
from resource_registry.cleanup.inventory import reconcile_historical_inventory
from resource_registry.cleanup.scan import scan_legacy_storage
from resource_registry.cleanup.sweeper import discover_cleanup_candidates
from resource_registry.cleanup.worker import CleanupWorker

_logger = logging.getLogger(__name__)


def can_start_cleanup_coordinator() -> bool:
    """Admit web entrypoints while excluding tests, commands and reload parents."""
    if not getattr(settings, "RESOURCE_REGISTRY_CLEANUP_COORDINATOR_ENABLED", True):
        return False
    if "pytest" in sys.modules or any("pytest" in value for value in sys.argv):
        return False
    if "runserver" in sys.argv:
        return os.environ.get("RUN_MAIN") == "true" or "--noreload" in sys.argv
    return any(server in sys.argv[0] for server in ("gunicorn", "uvicorn", "daphne", "uwsgi"))


def run_cleanup_batch(*, batch_size: int = 100) -> int:
    """Discover and process bounded cleanup under existing organization policies.

    Args:
        batch_size: Maximum work per discovery stage and worker batch, capped at 100.

    Returns:
        Number of storage items processed by the cleanup worker.

    Raises:
        Exception: Propagate discovery or execution failures to the scheduling boundary.
    """
    if not getattr(settings, "RESOURCE_REGISTRY_CLEANUP_COORDINATOR_ENABLED", True):
        return 0
    batch_size = max(1, min(batch_size, 100))
    close_old_connections()
    try:
        reconcile_historical_inventory(batch_size=batch_size)
        scan_legacy_storage(batch_size=batch_size)
        discover_cleanup_candidates(batch_size=batch_size)
        return CleanupWorker().run_once(batch_size=batch_size)
    finally:
        close_old_connections()


def run_cleanup_cycle() -> None:
    """Run the web coordinator cycle while keeping failures out of its executor."""
    if not getattr(settings, "RESOURCE_REGISTRY_CLEANUP_COORDINATOR_ENABLED", True):
        return
    try:
        run_cleanup_batch(
            batch_size=int(getattr(settings, "RESOURCE_REGISTRY_CLEANUP_BATCH_SIZE", 100))
        )
    except Exception:
        _logger.exception("Resource cleanup cycle failed; durable work remains pending.")


def _is_gunicorn_worker() -> bool:
    # Gunicorn loads WSGI either from Arbiter.setup (preload) or Worker.load_wsgi.
    # Inspect only the loader identity, never request data or credentials.
    frame = inspect.currentframe()
    try:
        while frame is not None:
            if frame.f_globals.get("__name__") == "gunicorn.workers.base":
                return getattr(frame.f_locals.get("self"), "pid", None) == os.getpid()
            frame = frame.f_back
        return False
    finally:
        del frame


class CleanupCoordinator:
    """Own one timer per web process and reset inherited synchronization after fork."""

    def __init__(self) -> None:
        """Initialize process-local coordination without creating threads or doing I/O."""
        self.lock: LockType = Lock()
        self.stop: Event = Event()
        self.process_id: int | None = None
        self.thread: Thread | None = None
        self.fork_registered: bool = False

    def start(self, *, after_fork: bool = False) -> None:
        """Start automatic polling only in the web process that will execute the work."""
        if not can_start_cleanup_coordinator():
            return
        with self.lock:
            if (
                self.process_id == os.getpid()
                and self.thread is not None
                and self.thread.is_alive()
            ):
                return
            if not self.fork_registered and hasattr(os, "register_at_fork"):
                os.register_at_fork(after_in_child=self.restart_after_fork)
                self.fork_registered = True
            if "gunicorn" in sys.argv[0] and not after_fork and not _is_gunicorn_worker():
                return
            self.process_id = os.getpid()
            self.thread = Thread(
                target=self.coordinate, name="resource-cleanup-coordinator", daemon=True
            )
            self.thread.start()

    def restart_after_fork(self) -> None:
        """Discard parent thread state and defer I/O until the child is initialized."""
        self.lock = Lock()
        self.stop = Event()
        self.thread = None
        self.process_id = None
        self.start(after_fork=True)

    def coordinate(self) -> None:
        """Submit at most one local sweep at a time to the existing low-priority executor."""
        future: Future[None] | None = None
        if self.stop.wait(5):
            return
        while not self.stop.is_set():
            try:
                if future is None or future.done():
                    executor = UnicornAiThreadpoolExecutor(str(settings.LOW_PRIORITY_EXECUTOR_NAME))
                    future = executor.submit(run_cleanup_cycle)
            except Exception:
                _logger.exception("Resource cleanup could not enter the background executor.")
            self.stop.wait(
                max(15, int(getattr(settings, "RESOURCE_REGISTRY_CLEANUP_INTERVAL_SECONDS", 900)))
            )


_coordinator = CleanupCoordinator()


def start_cleanup_coordinator() -> None:
    """Initialize the automatic coordinator from a configured WSGI or ASGI entrypoint."""
    _coordinator.start()
