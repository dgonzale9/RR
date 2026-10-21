from __future__ import annotations

import signal
from threading import Event

from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser

from resource_registry.services.dataset_intelligence_job_worker_runner import (
    DatasetIntelligenceJobWorkerRunner,
)


class Command(BaseCommand):
    """Run Dataset Intelligence Job worker polling loop."""

    help = "Run Dataset Intelligence Job worker polling loop."

    def add_arguments(self, parser: CommandParser) -> None:
        """Add command-line options for the intelligence worker."""
        parser.add_argument(
            "--once",
            action="store_true",
            help="Run a single claim/execute cycle and exit.",
        )
        parser.add_argument(
            "--poll-interval-seconds",
            type=float,
            default=float(
                getattr(
                    settings,
                    "RESOURCE_REGISTRY_INTELLIGENCE_JOB_WORKER_POLL_INTERVAL_SECONDS",
                    2.0,
                ),
            ),
            help="Polling interval between idle cycles.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=int(getattr(settings, "RESOURCE_REGISTRY_INTELLIGENCE_JOB_WORKER_BATCH_SIZE", 5)),
            help="Maximum queued jobs to claim per cycle.",
        )
        parser.add_argument(
            "--max-jobs",
            type=int,
            default=0,
            help="Maximum jobs to process before exit. Zero means unlimited.",
        )
        parser.add_argument(
            "--tenant-id",
            type=str,
            default=None,
            help="Optional tenant filter for scoped administrative execution.",
        )

    def handle(self, *args: object, **options: object) -> None:
        """Run the intelligence worker from the management command entrypoint."""
        stop_event = Event()
        self._install_signal_handlers(stop_event=stop_event)
        runner = DatasetIntelligenceJobWorkerRunner(
            poll_interval_seconds=float(options["poll_interval_seconds"]),
            batch_size=int(options["batch_size"]),
        )
        max_jobs = int(options["max_jobs"])
        normalized_max_jobs = None if max_jobs <= 0 else max_jobs
        tenant_candidate = options.get("tenant_id")
        tenant_id = str(tenant_candidate) if tenant_candidate else None
        try:
            stats = runner.run(
                once=bool(options["once"]),
                max_jobs=normalized_max_jobs,
                tenant_id=tenant_id,
                stop_event=stop_event,
            )
            self.stdout.write(
                self.style.SUCCESS(
                    "Dataset intelligence worker finished: "
                    f"claimed={stats.claimed_jobs} processed={stats.processed_jobs}",
                ),
            )
        except KeyboardInterrupt:
            stop_event.set()
            self.stdout.write(self.style.WARNING("Dataset intelligence worker interrupted."))

    def _install_signal_handlers(self, *, stop_event: Event) -> None:
        def _signal_handler(_signal_number: int, _frame: object) -> None:  # pragma: no cover
            stop_event.set()

        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, _signal_handler)
