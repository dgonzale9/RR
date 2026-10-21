"""Run the upload-backed CSV registration worker."""

from __future__ import annotations

import signal
from threading import Event
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser

from resource_registry.services.dataset_registration_job_worker_runner import (
    DatasetRegistrationJobWorkerRunner,
)


class Command(BaseCommand):
    """Run CSV registration worker polling from a management command."""

    help = "Run Dataset Registration Job worker polling loop."

    def add_arguments(self, parser: CommandParser) -> None:
        """Add worker lifecycle and scoping arguments."""
        parser.add_argument("--once", action="store_true")
        parser.add_argument(
            "--poll-interval-seconds",
            type=float,
            default=float(
                getattr(settings, "RESOURCE_REGISTRY_CSV_JOB_WORKER_POLL_INTERVAL_SECONDS", 2.0)
            ),
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=int(getattr(settings, "RESOURCE_REGISTRY_CSV_JOB_WORKER_BATCH_SIZE", 5)),
        )
        parser.add_argument(
            "--max-jobs",
            type=int,
            default=int(getattr(settings, "RESOURCE_REGISTRY_CSV_JOB_WORKER_MAX_JOBS_PER_RUN", 0)),
        )
        parser.add_argument("--tenant-id", type=str, default=None)

    def handle(self, *args: object, **options: Any) -> None:
        """Run the worker until its configured stop condition."""
        _ = args
        stop_event = Event()
        self._install_signal_handlers(stop_event)
        max_jobs = int(options["max_jobs"])
        stats = DatasetRegistrationJobWorkerRunner(
            poll_interval_seconds=float(options["poll_interval_seconds"]),
            batch_size=int(options["batch_size"]),
        ).run(
            once=bool(options["once"]),
            max_jobs=None if max_jobs <= 0 else max_jobs,
            tenant_id=str(options["tenant_id"]) if options.get("tenant_id") else None,
            stop_event=stop_event,
        )
        self.stdout.write(
            self.style.SUCCESS(
                "CSV registration worker finished: "
                f"claimed={stats.claimed_jobs} processed={stats.processed_jobs}"
            )
        )

    @staticmethod
    def _install_signal_handlers(stop_event: Event) -> None:
        def _stop(_signal_number: int, _frame: object) -> None:
            stop_event.set()

        for signal_number in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signal_number, _stop)
