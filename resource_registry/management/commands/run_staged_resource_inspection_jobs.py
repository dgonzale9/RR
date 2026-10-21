"""Run bounded private resource intake inspection jobs."""

from __future__ import annotations

from typing import cast

from django.core.management.base import BaseCommand, CommandParser

from resource_registry.api.intakes.dependencies import get_staged_resource_intake_service


class Command(BaseCommand):
    """Run queued private intake inspections without creating durable datasets."""

    help = "Run a bounded batch of staged resource inspection jobs."

    def add_arguments(self, parser: CommandParser) -> None:
        """Register the bounded job batch size.

        Args:
            parser: Django command argument parser.
        """
        parser.add_argument("--batch-size", type=int, default=10)

    def handle(self, *args: object, **options: object) -> None:
        """Run queued inspection jobs.

        Args:
            args: Unused positional command arguments.
            options: Parsed command options.
        """
        _ = args
        count = get_staged_resource_intake_service().run_inspection_jobs(
            batch_size=cast(int, options["batch_size"]),
        )
        self.stdout.write(
            self.style.SUCCESS(f"Completed {count} staged resource inspection job(s).")
        )
