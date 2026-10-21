"""Outbox dispatcher management-command tests."""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings


class OutboxDispatcherCommandTests(SimpleTestCase):
    """Verify the command composes Azure transport with bounded dispatching."""

    def test_command_dispatches_until_no_due_entry_remains(self) -> None:
        """Publish no more than the requested batch through the composed dispatcher."""
        transport = MagicMock()
        archive = MagicMock()
        dispatcher = MagicMock()
        dispatcher.dispatch_next.side_effect = [True, True, False]
        stdout = io.StringIO()

        with (
            patch(
                "resource_registry.management.commands.run_outbox_dispatcher."
                "AzureServiceBusEventTransport.from_environment",
                return_value=transport,
            ),
            patch(
                "resource_registry.management.commands.run_outbox_dispatcher."
                "AzureBlobEventArchive.from_environment",
                return_value=archive,
            ),
            patch(
                "resource_registry.management.commands.run_outbox_dispatcher.OutboxDispatcherService",
                return_value=dispatcher,
            ) as dispatcher_service,
        ):
            with override_settings(RESOURCE_REGISTRY_OUTBOX_ENABLED=True):
                call_command(
                    "run_outbox_dispatcher",
                    "--worker-id",
                    "worker-a",
                    "--max-events",
                    "10",
                    "--lease-seconds",
                    "45",
                    stdout=stdout,
                )

        dispatcher_service.assert_called_once_with(transport=transport, archive=archive)
        assert dispatcher.dispatch_next.call_count == 3
        dispatcher.dispatch_next.assert_called_with(worker_id="worker-a", lease_seconds=45)
        assert "published=2" in stdout.getvalue()

    def test_command_fails_closed_when_azure_configuration_is_missing(self) -> None:
        """Avoid attempting publication when the non-secret broker configuration is incomplete."""
        with (
            patch(
                "resource_registry.management.commands.run_outbox_dispatcher."
                "AzureServiceBusEventTransport.from_environment",
                side_effect=RuntimeError("missing"),
            ),
            self.assertRaisesRegex(CommandError, "configuration is incomplete"),
        ):
            with override_settings(RESOURCE_REGISTRY_OUTBOX_ENABLED=True):
                call_command("run_outbox_dispatcher", "--worker-id", "worker-a")

    def test_command_fails_closed_when_event_archive_configuration_is_missing(self) -> None:
        """Avoid broker publication when immutable archive configuration is incomplete."""
        with (
            patch(
                "resource_registry.management.commands.run_outbox_dispatcher."
                "AzureServiceBusEventTransport.from_environment",
                return_value=MagicMock(),
            ),
            patch(
                "resource_registry.management.commands.run_outbox_dispatcher."
                "AzureBlobEventArchive.from_environment",
                side_effect=RuntimeError("missing"),
            ),
            self.assertRaisesRegex(CommandError, "configuration is incomplete"),
        ):
            with override_settings(RESOURCE_REGISTRY_OUTBOX_ENABLED=True):
                call_command("run_outbox_dispatcher", "--worker-id", "worker-a")

    def test_command_hides_transport_failure_details_from_cli_output(self) -> None:
        """Expose a stable operational failure while preserving retry ownership in the dispatcher."""
        dispatcher = MagicMock()
        dispatcher.dispatch_next.side_effect = RuntimeError("credential secret")

        with (
            patch(
                "resource_registry.management.commands.run_outbox_dispatcher."
                "AzureServiceBusEventTransport.from_environment",
                return_value=MagicMock(),
            ),
            patch(
                "resource_registry.management.commands.run_outbox_dispatcher."
                "AzureBlobEventArchive.from_environment",
                return_value=MagicMock(),
            ),
            patch(
                "resource_registry.management.commands.run_outbox_dispatcher.OutboxDispatcherService",
                return_value=dispatcher,
            ),
            self.assertRaisesRegex(CommandError, "event remains retryable"),
        ):
            with override_settings(RESOURCE_REGISTRY_OUTBOX_ENABLED=True):
                call_command("run_outbox_dispatcher", "--worker-id", "worker-a")

    def test_command_does_not_initialize_azure_adapters_while_disabled(self) -> None:
        """Keep external publication fully inactive until the approval flag is enabled."""
        stdout = io.StringIO()
        with (
            patch(
                "resource_registry.management.commands.run_outbox_dispatcher."
                "AzureServiceBusEventTransport.from_environment",
            ) as transport_factory,
            patch(
                "resource_registry.management.commands.run_outbox_dispatcher."
                "AzureBlobEventArchive.from_environment",
            ) as archive_factory,
            override_settings(RESOURCE_REGISTRY_OUTBOX_ENABLED=False),
        ):
            call_command("run_outbox_dispatcher", "--worker-id", "worker-a", stdout=stdout)

        transport_factory.assert_not_called()
        archive_factory.assert_not_called()
        assert "disabled by configuration" in stdout.getvalue()
