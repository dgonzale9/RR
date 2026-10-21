"""Expire and clean stale private resource intake objects."""

from __future__ import annotations

from typing import cast

from django.core.management.base import BaseCommand, CommandParser

from resource_registry.api.intakes.dependencies import get_staged_resource_intake_service
from resource_registry.uploads.exceptions import UploadApplicationError


class Command(BaseCommand):
    """Expire a bounded batch of unassigned staged resource intakes."""

    help = "Expire staged resource intakes whose private retention window elapsed."

    def add_arguments(self, parser: CommandParser) -> None:
        """Register bounded cleanup command arguments.

        Args:
            parser: Django command argument parser.
        """
        parser.add_argument("--batch-size", type=int, default=100)

    def handle(self, *args: object, **options: object) -> None:
        """Expire one bounded batch and report its count.

        Args:
            args: Unused positional command arguments.
            options: Parsed command options.
        """
        _ = args
        try:
            expired = get_staged_resource_intake_service().expire_intakes(
                batch_size=cast(int, options["batch_size"]),
            )
        except UploadApplicationError as error:
            self.stderr.write(str(error))
            return
        self.stdout.write(self.style.SUCCESS(f"Expired {expired} staged resource intake(s)."))
