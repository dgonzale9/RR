from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Protocol

from resource_registry.events.event_models import DomainEvent
from resource_registry.events.event_types import EventType


class EventPublisher(Protocol):
    """Publish immutable operational events to an append-only sink."""

    def publish(self, event: DomainEvent) -> DomainEvent:
        """Publish one immutable event."""

    def is_available(self) -> bool:
        """Return whether the publisher can accept events."""


class InMemoryEventPublisher:
    """Local append-only domain event publisher."""

    def __init__(self) -> None:
        self._events: list[DomainEvent] = []

    def publish(self, event: DomainEvent) -> DomainEvent:
        return self.record(event)

    def record(self, event: DomainEvent) -> DomainEvent:
        self._events.append(event)
        return event

    def list_events(
        self,
        *,
        event_type: EventType | None = None,
        tenant_id: str | None = None,
    ) -> list[DomainEvent]:
        events = list(self._events)
        if event_type is not None:
            events = [event for event in events if event.event_type == event_type]
        if tenant_id is not None:
            events = [event for event in events if event.tenant_id == tenant_id]
        return events

    def retrieve(self, event_id: str) -> DomainEvent | None:
        for event in self._events:
            if event.event_id == event_id:
                return event
        return None

    def is_available(self) -> bool:
        """Return true for the in-memory test publisher."""
        return True


class ProductionAppendOnlyEventPublisher:
    """Publish sanitized audit events through the configured structured logger."""

    def __init__(self, *, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger("transformation-operations")

    def publish(self, event: DomainEvent) -> DomainEvent:
        """Append one event to the production operational event stream."""
        self._logger.info(
            "Transformation audit event",
            extra={
                "audit_event_id": event.event_id,
                "audit_event_type": event.event_type.value,
                "tenant_id": event.tenant_id or "",
                "run_id": event.run_id or "",
                "attempt_number": event.attempt_number or 0,
                "output_id": event.output_id or "",
                "transition": event.transition or "",
                "actor": event.actor or "",
                "correlation_id": event.correlation_id or "",
                "audit_metadata": _thaw_event_value(event.metadata),
            },
        )
        return event

    def is_available(self) -> bool:
        """Return whether the production logger has an effective handler."""
        return not self._logger.disabled and self._logger.hasHandlers()


def _thaw_event_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_event_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_event_value(item) for item in value]
    return value
