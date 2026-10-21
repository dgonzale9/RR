"""Azure Service Bus event transport tests without external provider calls."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from django.conf import settings

from resource_registry.services.azure_service_bus_event_transport import (
    AzureServiceBusEventTransport,
)


@pytest.mark.parametrize(
    "missing_name",
    [
        "RESOURCE_REGISTRY_OUTBOX_SERVICE_BUS_NAMESPACE",
        "RESOURCE_REGISTRY_OUTBOX_SERVICE_BUS_TOPIC",
    ],
)
def test_transport_requires_complete_non_secret_configuration(
    monkeypatch: pytest.MonkeyPatch,
    missing_name: str,
) -> None:
    """Reject incomplete Azure Service Bus deployment configuration."""
    monkeypatch.setattr(
        settings,
        "RESOURCE_REGISTRY_OUTBOX_SERVICE_BUS_NAMESPACE",
        "events.servicebus.windows.net",
    )
    monkeypatch.setattr(settings, "RESOURCE_REGISTRY_OUTBOX_SERVICE_BUS_TOPIC", "dataset-events")
    monkeypatch.setattr(settings, missing_name, None)

    with pytest.raises(RuntimeError, match="must be configured"):
        AzureServiceBusEventTransport.from_environment()


def test_transport_publishes_canonical_event_with_stable_broker_identity() -> None:
    """Send canonical bytes with the EventId and stream partition key."""
    credential = MagicMock()
    sender = MagicMock()
    sender.__enter__.return_value = sender
    client = MagicMock()
    client.get_topic_sender.return_value = sender
    service_bus_client = MagicMock()
    service_bus_client.return_value.__enter__.return_value = client
    message = MagicMock()

    with (
        patch(
            "resource_registry.services.azure_service_bus_event_transport.DefaultAzureCredential",
            return_value=credential,
        ) as default_credential,
        patch(
            "resource_registry.services.azure_service_bus_event_transport.ServiceBusClient",
            service_bus_client,
        ),
        patch(
            "resource_registry.services.azure_service_bus_event_transport.ServiceBusMessage",
            return_value=message,
        ) as service_bus_message,
    ):
        AzureServiceBusEventTransport(
            namespace="events.servicebus.windows.net",
            topic="dataset-events",
        ).publish(
            event_id="event-123",
            canonical_event='{"event_id":"event-123"}',
            partition_key="namespace-123:dataset-456",
        )

    default_credential.assert_called_once_with()
    service_bus_client.assert_called_once_with(
        fully_qualified_namespace="events.servicebus.windows.net",
        credential=credential,
    )
    client.get_topic_sender.assert_called_once_with(topic_name="dataset-events")
    service_bus_message.assert_called_once_with(
        '{"event_id":"event-123"}',
        message_id="event-123",
        partition_key="namespace-123:dataset-456",
        content_type="application/json",
    )
    sender.send_messages.assert_called_once_with(message)
