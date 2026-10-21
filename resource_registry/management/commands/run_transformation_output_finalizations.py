from __future__ import annotations

import signal
from threading import Event
from types import FrameType
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from resource_registry.application.transformation_runtime import (
    RuntimeCompositionRoot,
    RuntimeShutdownCoordinator,
)


class Command(BaseCommand):
    """Run the durable transformation output-finalization worker."""

    help = "Run the durable Transformation Output Finalization worker polling loop."

    def add_arguments(self, parser: CommandParser) -> None:
        """Register bounded process-control options."""
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--poll-interval-seconds", type=float, default=None)
        parser.add_argument("--max-jobs", type=int, default=0)
        parser.add_argument("--worker-id", type=str, default=None)
        parser.add_argument("--tenant-id", type=str, default=None)

    def handle(self, *args: Any, **options: Any) -> None:
        """Bootstrap the immutable runtime and delegate to its finalization runner."""
        _ = args
        stop_event = Event()
        self._install_signal_handlers(stop_event=stop_event)
        poll_interval = options.get("poll_interval_seconds")
        overrides = (
            {"finalization_poll_interval_seconds": float(poll_interval)}
            if poll_interval is not None
            else None
        )
        bootstrap = RuntimeCompositionRoot().bootstrap(
            worker_overrides=overrides,
            worker_id=options.get("worker_id"),
        )
        if bootstrap.runtime is None or not bootstrap.has_reached_ready:
            raise CommandError("Transformation runtime bootstrap failed closed.")
        runtime = bootstrap.runtime
        readiness_report = runtime.readiness_validator.validate()
        if readiness_report.blocking_failures:
            raise CommandError("Transformation runtime readiness validation failed closed.")
        try:
            max_jobs = int(options["max_jobs"])
            stats = runtime.output_finalization_worker_runner.run(
                once=bool(options["once"]),
                max_jobs=None if max_jobs <= 0 else max_jobs,
                tenant_id=options.get("tenant_id"),
                stop_event=stop_event,
            )
        finally:
            RuntimeShutdownCoordinator().shutdown(runtime=runtime, stop_event=stop_event)
        self.stdout.write(
            self.style.SUCCESS(
                "Transformation output finalization worker finished: "
                f"claimed={stats.claimed_runs} processed={stats.processed_runs} "
                f"recovered={stats.recovered_runs} cleaned={stats.cleaned_leases}",
            ),
        )

    def _install_signal_handlers(self, *, stop_event: Event) -> None:
        """Install cooperative process shutdown handlers."""

        def _signal_handler(  # pragma: no cover
            _signal_number: int,
            _frame: FrameType | None,
        ) -> None:
            stop_event.set()

        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, _signal_handler)
