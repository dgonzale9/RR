"""Poll the dataset proposal queue using the existing Registry storage and parsers."""

import signal
from threading import Event

from django.core.management.base import BaseCommand, CommandParser

from resource_registry.management_domain.worker import DatasetChangeWorker


class Command(BaseCommand):
    """Run bounded proposal preparation alongside existing intake inspection workers."""

    help = "Prepare private dataset changes for enabled organizations."

    def add_arguments(self, parser: CommandParser) -> None:
        """Configure bounded batches and optional one-cycle execution."""
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--batch-size", type=int, default=5)
        parser.add_argument("--poll-interval-seconds", type=float, default=2.0)

    def handle(self, *args: object, **options: object) -> None:
        """Prepare proposals until interrupted, never publishing them automatically."""
        stopped = Event()

        def stop_worker(signal_number: int, frame: object) -> None:
            stopped.set()

        for signal_number in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signal_number, stop_worker)
        worker = DatasetChangeWorker()
        while not stopped.is_set():
            processed = worker.run_once(batch_size=int(str(options["batch_size"])))
            if options["once"]:
                self.stdout.write(f"Prepared proposals: {processed}")
                return
            stopped.wait(max(0.1, float(str(options["poll_interval_seconds"]))))
