from __future__ import annotations

import signal
from threading import Event

from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser

from resource_registry.services.workbook_ingestion_job_worker_runner import (
    WorkbookIngestionJobWorkerRunner,
)


class Command(BaseCommand):
    help = "Run Workbook Ingestion Job worker polling loop."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--once",
            action="store_true",
            help="Run a single claim/execute cycle and exit.",
        )
        parser.add_argument(
            "--poll-interval-seconds",
            type=float,
            default=float(
                getattr(settings, "RESOURCE_REGISTRY_WORKBOOK_JOB_WORKER_POLL_INTERVAL_SECONDS", 2.0),
            ),
            help="Polling interval between idle cycles.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=int(getattr(settings, "RESOURCE_REGISTRY_WORKBOOK_JOB_WORKER_BATCH_SIZE", 2)),
            help="Maximum queued jobs to claim per cycle.",
        )
        parser.add_argument(
            "--max-jobs",
            type=int,
            default=int(getattr(settings, "RESOURCE_REGISTRY_WORKBOOK_JOB_WORKER_MAX_JOBS_PER_RUN", 0)),
            help="Maximum jobs to process before exit. Zero means unlimited.",
        )
        parser.add_argument(
            "--tenant-id",
            type=str,
            default=None,
            help="Optional tenant filter for scoped administrative execution.",
        )

    def handle(self, *args, **options) -> None:
        stop_event = Event()
        self._install_signal_handlers(stop_event=stop_event)
        runner = WorkbookIngestionJobWorkerRunner(
            poll_interval_seconds=float(options["poll_interval_seconds"]),
            batch_size=int(options["batch_size"]),
        )
        max_jobs = int(options["max_jobs"])
        normalized_max_jobs = None if max_jobs <= 0 else max_jobs
        try:
            stats = runner.run(
                once=bool(options["once"]),
                max_jobs=normalized_max_jobs,
                tenant_id=options.get("tenant_id"),
                stop_event=stop_event,
            )
            self.stdout.write(
                self.style.SUCCESS(
                    "Workbook ingestion worker finished: "
                    f"claimed={stats.claimed_jobs} processed={stats.processed_jobs}",
                ),
            )
        except KeyboardInterrupt:
            stop_event.set()
            self.stdout.write(self.style.WARNING("Workbook ingestion worker interrupted."))

    def _install_signal_handlers(self, *, stop_event: Event) -> None:
        def _signal_handler(_signal_number, _frame) -> None:  # pragma: no cover
            stop_event.set()

        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, _signal_handler)
