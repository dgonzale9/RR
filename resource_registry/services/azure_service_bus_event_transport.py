"""Publish immutable outbox events to Azure Service Bus through Managed Identity."""

from __future__ import annotations

from dataclasses import dataclass

from azure.identity import DefaultAzureCredential
from azure.servicebus import ServiceBusClient, ServiceBusMessage
from django.conf import settings


@dataclass(frozen=True, slots=True)
class AzureServiceBusEventTransport:
    """Send canonical event bytes to one configured Azure Service Bus Topic."""

    namespace: str
    topic: str

    @classmethod
    def from_environment(cls) -> AzureServiceBusEventTransport:
        """Build a transport from non-secret deployment configuration.

        Raises:
            RuntimeError: If Azure Service Bus configuration is absent.
        """
        namespace = getattr(settings, "RESOURCE_REGISTRY_OUTBOX_SERVICE_BUS_NAMESPACE", None)
        topic = getattr(settings, "RESOURCE_REGISTRY_OUTBOX_SERVICE_BUS_TOPIC", None)
        if not namespace or not topic:
            raise RuntimeError("Azure Service Bus namespace and topic must be configured.")
        return cls(namespace=namespace, topic=topic)

    def publish(self, *, event_id: str, canonical_event: str, partition_key: str) -> None:
        """Publish one canonical event with stable identity and stream partition metadata."""
        credential = DefaultAzureCredential()
        with ServiceBusClient(
            fully_qualified_namespace=self.namespace, credential=credential
        ) as client:
            sender = client.get_topic_sender(topic_name=self.topic)
            with sender:
                sender.send_messages(
                    ServiceBusMessage(
                        canonical_event,
                        message_id=event_id,
                        partition_key=partition_key,
                        content_type="application/json",
                    )
                )
