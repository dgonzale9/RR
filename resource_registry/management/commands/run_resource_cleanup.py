"""Run one bounded Registry cleanup batch for an external scheduler."""

from typing import cast

from django.core.management.base import BaseCommand, CommandError, CommandParser

from resource_registry.cleanup.runtime import run_cleanup_batch


class Command(BaseCommand):
    """Process eligible cleanup without changing retention or deletion authority."""

    help = "Discover and process one bounded batch of policy-authorized resource cleanup."

    def add_arguments(self, parser: CommandParser) -> None:
        """Register the finite cleanup work limit.

        Args:
            parser: Django management command argument parser.
        """
        parser.add_argument("--batch-size", type=int, default=100)

    def handle(self, *args: object, **options: object) -> None:
        """Execute cleanup once and report infrastructure failures to the scheduler.

        Args:
            args: Unused positional arguments.
            options: Parsed command options.

        Raises:
            CommandError: If the batch limit is invalid or the cleanup cycle fails.
        """
        batch_size = cast(int, options["batch_size"])
        if not 1 <= batch_size <= 100:
            raise CommandError("--batch-size must be between 1 and 100.")
        try:
            processed = run_cleanup_batch(batch_size=batch_size)
        except Exception as error:
            raise CommandError(
                "Resource cleanup cycle failed; durable work remains pending."
            ) from error
        self.stdout.write(self.style.SUCCESS(f"Resource cleanup items processed: {processed}"))
