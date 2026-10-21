"""Run bounded Azure Service Bus publication for Dataset Registry outbox entries."""

from __future__ import annotations

from typing import cast

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from resource_registry.services.azure_blob_event_archive import AzureBlobEventArchive
from resource_registry.services.azure_service_bus_event_transport import (
    AzureServiceBusEventTransport,
)
from resource_registry.services.outbox_dispatcher_service import OutboxDispatcherService


class Command(BaseCommand):
    """Dispatch a bounded batch of committed Dataset Registry outbox events."""

    help = "Publish committed Dataset Registry outbox events to Azure Service Bus."

    def add_arguments(self, parser: CommandParser) -> None:
        """Add the bounded dispatcher execution arguments.

        Args:
            parser: Django command-line parser receiving the supported arguments.
        """
        parser.add_argument("--worker-id", type=str, required=True)
        parser.add_argument("--max-events", type=int, default=100)
        parser.add_argument("--lease-seconds", type=int, default=60)

    def handle(self, *args: object, **options: object) -> None:
        """Publish due entries until the batch is exhausted or no entry is available.

        Args:
            args: Positional command arguments, which are not used.
            options: Parsed command options.

        Raises:
            CommandError: If configuration is incomplete, options are invalid, or publication fails.
        """
        _ = args
        max_events = cast(int, options["max_events"])
        lease_seconds = cast(int, options["lease_seconds"])
        worker_id = cast(str, options["worker_id"])
        if max_events < 1:
            raise CommandError("--max-events must be greater than zero.")
        if lease_seconds < 1:
            raise CommandError("--lease-seconds must be greater than zero.")
        if not bool(getattr(settings, "RESOURCE_REGISTRY_OUTBOX_ENABLED", False)):
            self.stdout.write("Outbox dispatcher is disabled by configuration.")
            return

        try:
            transport = AzureServiceBusEventTransport.from_environment()
            archive = AzureBlobEventArchive.from_environment()
        except RuntimeError as error:
            raise CommandError(
                "Azure dispatcher or event archive configuration is incomplete."
            ) from error

        dispatcher = OutboxDispatcherService(transport=transport, archive=archive)
        published_count = 0
        try:
            while published_count < max_events and dispatcher.dispatch_next(
                worker_id=worker_id,
                lease_seconds=lease_seconds,
            ):
                published_count += 1
        except Exception as error:
            raise CommandError(
                "Azure Service Bus dispatcher publication failed; event remains retryable."
            ) from error

        self.stdout.write(
            self.style.SUCCESS(
                f"Azure Service Bus dispatcher finished: published={published_count}"
            ),
        )
