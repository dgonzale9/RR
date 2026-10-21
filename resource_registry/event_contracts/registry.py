"""Define pure immutable exact Event declaration registry contracts."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import EventSchemaDeclaration, EventType, EventVersion
from .errors import EventContractValidationError

__all__ = ("EventSourceRegistry",)


@dataclass(frozen=True, slots=True)
class EventSourceRegistry:
    """Resolve exact immutable Event schema declarations without runtime publication.

    Args:
        declarations: Finite exact declarations retained in deterministic declaration order.

    Raises:
        EventContractValidationError: If declarations are empty, duplicate, or source-conflicting.
    """

    declarations: tuple[EventSchemaDeclaration, ...]

    def __post_init__(self) -> None:
        """Validate one finite declaration registry without discovery or persistence.

        Raises:
            EventContractValidationError: If declarations are empty, duplicate, or source-conflicting.
        """
        if type(self.declarations) is not tuple or not self.declarations:
            raise EventContractValidationError("invalid event source registry declarations")
        source_by_type: dict[EventType, object] = {}
        declaration_keys: set[tuple[EventType, EventVersion]] = set()
        for declaration in self.declarations:
            if not isinstance(declaration, EventSchemaDeclaration):
                raise EventContractValidationError("invalid event source registry declaration")
            declaration_key = (declaration.event_type, declaration.version)
            if declaration_key in declaration_keys:
                raise EventContractValidationError("duplicate event schema declaration")
            declaration_keys.add(declaration_key)
            source = source_by_type.setdefault(declaration.event_type, declaration.source)
            if source != declaration.source:
                raise EventContractValidationError("conflicting event schema source")

    def resolve(self, event_type: EventType, version: EventVersion) -> EventSchemaDeclaration:
        """Resolve one exact registered schema declaration.

        Args:
            event_type: Exact event type to resolve.
            version: Exact schema version to resolve.

        Returns:
            The one exact registered schema declaration.

        Raises:
            EventContractValidationError: If identifiers are invalid or unregistered.
        """
        if not isinstance(event_type, EventType) or not isinstance(version, EventVersion):
            raise EventContractValidationError("invalid event schema resolution")
        for declaration in self.declarations:
            if declaration.event_type == event_type and declaration.version == version:
                return declaration
        raise EventContractValidationError("unknown event schema declaration")
