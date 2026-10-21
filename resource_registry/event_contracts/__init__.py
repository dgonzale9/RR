"""Expose pure immutable Event schema and source registry contracts."""

from .contracts import (
    EventCategory,
    EventClassification,
    EventCompatibilityAssessment,
    EventCompatibilityDeclaration,
    EventCompatibilityOutcome,
    EventEnvelope,
    EventPayloadField,
    EventSchemaDeclaration,
    EventSource,
    EventSubject,
    EventSubjectProfile,
    EventType,
    EventVersion,
    assess_event_compatibility,
)
from .errors import EventContractValidationError
from .registry import EventSourceRegistry

__all__ = (
    "EventCategory",
    "EventClassification",
    "EventCompatibilityAssessment",
    "EventCompatibilityDeclaration",
    "EventCompatibilityOutcome",
    "EventContractValidationError",
    "EventEnvelope",
    "EventPayloadField",
    "EventSchemaDeclaration",
    "EventSource",
    "EventSourceRegistry",
    "EventSubject",
    "EventSubjectProfile",
    "EventType",
    "EventVersion",
    "assess_event_compatibility",
)
