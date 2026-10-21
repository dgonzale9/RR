from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from uuid import uuid4

from resource_registry.events.event_types import EventType


@dataclass(frozen=True)
class DomainEvent:
    event_type: EventType
    tenant_id: str | None
    dataset_id: str | None = None
    dataset_version_id: str | None = None
    actor: str | None = None
    correlation_id: str | None = None
    run_id: str | None = None
    attempt_number: int | None = None
    output_id: str | None = None
    transition: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid4()))
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        """Recursively freeze event metadata."""
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))


def _freeze_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType(
        {key: _freeze_value(nested) for key, nested in value.items()},
    )


def _freeze_value(value: object) -> object:
    if isinstance(value, Mapping):
        return _freeze_mapping(value)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return tuple(_freeze_value(item) for item in value)
    return value
