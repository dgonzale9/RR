"""Define pure immutable Event schema, source, subject, and envelope contracts."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import TypeAlias

from resource_registry.registry_core import (
    ArtifactKey,
    MetadataClassification,
    NamespaceKey,
    ResourceKey,
    ResourceVersionKey,
)

from .errors import EventContractValidationError

__all__ = (
    "EventCategory",
    "EventClassification",
    "EventCompatibilityAssessment",
    "EventCompatibilityDeclaration",
    "EventCompatibilityOutcome",
    "EventEnvelope",
    "EventPayloadField",
    "EventSchemaDeclaration",
    "EventSource",
    "EventSubject",
    "EventSubjectProfile",
    "EventType",
    "EventVersion",
    "assess_event_compatibility",
)

_IDENTIFIER_PATTERN = re.compile(r"[a-z][a-z0-9_.-]*\Z", re.ASCII)
_FIELD_NAME_PATTERN = re.compile(r"[a-z][a-z0-9_]*\Z", re.ASCII)
_MAXIMUM_IDENTIFIER_LENGTH = 128
_MAXIMUM_PAYLOAD_FIELDS = 64
_MAXIMUM_PAYLOAD_DEPTH = 8
_MAXIMUM_PAYLOAD_ITEMS = 512
_NON_EXACT_SUBJECT_IDENTIFIERS = frozenset({"current", "latest"})
_FORBIDDEN_TOKENS = frozenset(
    {
        "authorization",
        "callable",
        "class",
        "credential",
        "endpoint",
        "http",
        "import",
        "password",
        "policy",
        "principal",
        "secret",
        "token",
        "url",
    }
)

JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonScalar | tuple["JsonValue", ...] | Mapping[str, "JsonValue"]


class EventCategory(StrEnum):
    """Classify an event using the closed ADR-011 event vocabulary."""

    INTEGRATION = "integration"
    PROJECTION = "projection"
    EXECUTION = "execution"
    CAPABILITY = "capability"
    PROVIDER = "provider"
    MIGRATION = "migration"
    AUDIT = "audit"
    SECURITY = "security"
    OPERATIONAL = "operational"


class EventSubjectProfile(StrEnum):
    """Declare the exact Registry identities required by an event subject."""

    RESOURCE = "resource"
    RESOURCE_VERSION = "resource_version"
    ARTIFACT = "artifact"


class EventCompatibilityOutcome(StrEnum):
    """Classify a declared schema compatibility relationship."""

    INITIAL = "initial"
    BACKWARD_COMPATIBLE = "backward_compatible"
    BREAKING = "breaking"
    REPLACEMENT = "replacement"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class EventType:
    """Identify one stable event semantic without transport routing details.

    Args:
        value: Lowercase opaque event semantic identifier.

    Raises:
        EventContractValidationError: If the identifier is malformed or unsafe.
    """

    value: str

    def __post_init__(self) -> None:
        """Validate one exact event semantic identifier.

        Raises:
            EventContractValidationError: If the identifier is malformed or unsafe.
        """
        if not _is_safe_identifier(self.value):
            raise EventContractValidationError("invalid event type")


@dataclass(frozen=True, slots=True)
class EventVersion:
    """Identify one positive immutable schema version.

    Args:
        value: Positive schema version without implicit latest resolution.

    Raises:
        EventContractValidationError: If the version is not positive.
    """

    value: int

    def __post_init__(self) -> None:
        """Validate the exact positive schema version.

        Raises:
            EventContractValidationError: If the version is not positive.
        """
        if type(self.value) is not int or self.value <= 0:
            raise EventContractValidationError("invalid event version")


@dataclass(frozen=True, slots=True)
class EventSource:
    """Describe one bounded, non-secret owner-controlled event publishing source.

    Args:
        bounded_context: Exact bounded context owning the source.
        service: Exact owner-controlled publishing service identity.
        deployment: Exact non-secret deployment identity.

    Raises:
        EventContractValidationError: If a source identity is malformed or unsafe.
    """

    bounded_context: str
    service: str
    deployment: str

    def __post_init__(self) -> None:
        """Validate bounded source identity without authenticating or discovering it.

        Raises:
            EventContractValidationError: If a source identity is malformed or unsafe.
        """
        if not all(
            _is_safe_identifier(value)
            for value in (self.bounded_context, self.service, self.deployment)
        ):
            raise EventContractValidationError("invalid event source")


@dataclass(frozen=True, slots=True)
class EventClassification:
    """Declare an ADR-011 category and the existing Core-safe classification.

    Args:
        category: Closed ADR-011 event category.
        metadata_classification: Existing Registry Core classification.

    Raises:
        EventContractValidationError: If classification expands the approved vocabulary.
    """

    category: EventCategory
    metadata_classification: MetadataClassification

    def __post_init__(self) -> None:
        """Validate classification without evaluating policy or inferring data sensitivity.

        Raises:
            EventContractValidationError: If classification expands the approved vocabulary.
        """
        if (
            not isinstance(self.category, EventCategory)
            or self.metadata_classification is not MetadataClassification.INTERNAL
        ):
            raise EventContractValidationError("invalid event classification")


@dataclass(frozen=True, slots=True)
class EventSubject:
    """Bind exact owner-issued Registry identities within one Namespace.

    Args:
        resource: Exact owner-issued resource identity.
        version: Optional exact version belonging to ``resource``.
        artifact: Optional exact artifact belonging to ``resource``.

    Raises:
        EventContractValidationError: If identities are missing or cross Namespace/resource.
    """

    resource: ResourceKey
    version: ResourceVersionKey | None = None
    artifact: ArtifactKey | None = None

    def __post_init__(self) -> None:
        """Validate exact subject identities without resolution or authorization.

        Raises:
            EventContractValidationError: If identities are missing or cross Namespace/resource.
        """
        if not isinstance(self.resource, ResourceKey):
            raise EventContractValidationError("invalid event subject resource")
        if self.version is not None and (
            not isinstance(self.version, ResourceVersionKey)
            or self.version.resource != self.resource
        ):
            raise EventContractValidationError("invalid event subject version")
        if self.artifact is not None and (
            not isinstance(self.artifact, ArtifactKey) or self.artifact.resource != self.resource
        ):
            raise EventContractValidationError("invalid event subject artifact")
        subject_identifiers = [self.resource.resource_id]
        if self.version is not None:
            subject_identifiers.append(self.version.version_id)
        if self.artifact is not None:
            subject_identifiers.append(self.artifact.artifact_id)
        if any(identifier in _NON_EXACT_SUBJECT_IDENTIFIERS for identifier in subject_identifiers):
            raise EventContractValidationError("non-exact event subject identity")

    @property
    def namespace(self) -> NamespaceKey:
        """Return the one exact Namespace shared by all subject identities.

        Returns:
            The resource Namespace identity.
        """
        return self.resource.namespace

    @property
    def profile(self) -> EventSubjectProfile:
        """Return the exact declared profile represented by the subject.

        Returns:
            Artifact, resource-version, or resource profile in that precedence order.
        """
        if self.artifact is not None:
            return EventSubjectProfile.ARTIFACT
        if self.version is not None:
            return EventSubjectProfile.RESOURCE_VERSION
        return EventSubjectProfile.RESOURCE


@dataclass(frozen=True, slots=True)
class EventPayloadField:
    """Declare one bounded, non-executable typed payload field.

    Args:
        name: Lowercase field name.
        value_kind: Declarative JSON-compatible scalar or container kind.
        required: Whether the field must occur in an envelope payload.
        absence_default: Explicit default required when ``required`` is false.

    Raises:
        EventContractValidationError: If the payload field is malformed or unsafe.
    """

    name: str
    value_kind: str
    required: bool
    absence_default: JsonValue | None = None

    def __post_init__(self) -> None:
        """Validate a declarative field without choosing a wire-schema language.

        Raises:
            EventContractValidationError: If the payload field is malformed or unsafe.
        """
        if (
            type(self.name) is not str
            or not _is_safe_payload_field_name(self.name)
            or not _is_safe_identifier(self.value_kind)
            or type(self.required) is not bool
            or (not self.required and self.absence_default is None)
            or not _is_json_value(self.absence_default)
        ):
            raise EventContractValidationError("invalid event payload field")


@dataclass(frozen=True, slots=True)
class EventSchemaDeclaration:
    """Freeze one exact schema declaration and its owner/source constraints.

    Args:
        event_type: Exact semantic event type.
        version: Exact positive schema version.
        source: Source that exclusively owns this event type.
        classification: Closed category and Core-safe classification.
        subject_profiles: Finite allowed subject profiles.
        payload_fields: Finite declarative payload shape.
        ordering_guarantee: Bounded semantic ordering guarantee identifier.

    Raises:
        EventContractValidationError: If declaration facts are malformed or ambiguous.
    """

    event_type: EventType
    version: EventVersion
    source: EventSource
    classification: EventClassification
    subject_profiles: tuple[EventSubjectProfile, ...]
    payload_fields: tuple[EventPayloadField, ...]
    ordering_guarantee: str

    def __post_init__(self) -> None:
        """Validate an immutable declaration without publishing or storing it.

        Raises:
            EventContractValidationError: If declaration facts are malformed or ambiguous.
        """
        if not (
            isinstance(self.event_type, EventType)
            and isinstance(self.version, EventVersion)
            and isinstance(self.source, EventSource)
            and isinstance(self.classification, EventClassification)
            and type(self.subject_profiles) is tuple
            and self.subject_profiles
            and len(set(self.subject_profiles)) == len(self.subject_profiles)
            and all(isinstance(profile, EventSubjectProfile) for profile in self.subject_profiles)
            and type(self.payload_fields) is tuple
            and len(self.payload_fields) <= _MAXIMUM_PAYLOAD_FIELDS
            and all(
                isinstance(field_value, EventPayloadField) for field_value in self.payload_fields
            )
            and len({field_value.name for field_value in self.payload_fields})
            == len(self.payload_fields)
            and _is_safe_identifier(self.ordering_guarantee)
        ):
            raise EventContractValidationError("invalid event schema declaration")


@dataclass(frozen=True, slots=True)
class EventCompatibilityDeclaration:
    """Declare one directional relationship between exact schema declarations.

    Args:
        predecessor: Earlier exact schema declaration, or ``None`` for initial schema.
        successor: Later exact schema declaration.
        declared_outcome: Explicit compatibility outcome asserted for the relationship.
        predecessor_supported: Whether the predecessor remains in the declared compatibility window.

    Raises:
        EventContractValidationError: If a relationship is malformed or not directional.
    """

    predecessor: EventSchemaDeclaration | None
    successor: EventSchemaDeclaration
    declared_outcome: EventCompatibilityOutcome
    predecessor_supported: bool = True

    def __post_init__(self) -> None:
        """Validate relationship shape without consumer inference or migration execution.

        Raises:
            EventContractValidationError: If a relationship is malformed or not directional.
        """
        if (
            not isinstance(self.successor, EventSchemaDeclaration)
            or not isinstance(self.declared_outcome, EventCompatibilityOutcome)
            or type(self.predecessor_supported) is not bool
        ):
            raise EventContractValidationError("invalid event compatibility declaration")
        if self.predecessor is None:
            if (
                self.declared_outcome is not EventCompatibilityOutcome.INITIAL
                or not self.predecessor_supported
            ):
                raise EventContractValidationError(
                    "invalid initial event compatibility declaration"
                )
            return
        if not isinstance(self.predecessor, EventSchemaDeclaration):
            raise EventContractValidationError("invalid event compatibility predecessor")
        if (
            self.predecessor.event_type == self.successor.event_type
            and self.predecessor.version.value >= self.successor.version.value
        ):
            raise EventContractValidationError("invalid event compatibility direction")
        if (
            self.predecessor.event_type != self.successor.event_type
            and self.declared_outcome is not EventCompatibilityOutcome.REPLACEMENT
        ):
            raise EventContractValidationError("invalid event compatibility replacement")


@dataclass(frozen=True, slots=True)
class EventCompatibilityAssessment:
    """Carry the deterministic assessment of one declared compatibility relationship.

    Args:
        declaration: Exact directional relationship assessed.
        outcome: Deterministically derived compatibility outcome.

    Raises:
        EventContractValidationError: If the assessment does not match the declaration.
    """

    declaration: EventCompatibilityDeclaration
    outcome: EventCompatibilityOutcome

    def __post_init__(self) -> None:
        """Validate assessment identity without accepting a permissive unsupported claim.

        Raises:
            EventContractValidationError: If the assessment does not match the declaration.
        """
        if not isinstance(self.declaration, EventCompatibilityDeclaration) or not isinstance(
            self.outcome, EventCompatibilityOutcome
        ):
            raise EventContractValidationError("invalid event compatibility assessment")


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    """Carry one immutable in-memory event fact without delivery infrastructure.

    Args:
        event_id: Exact producer-created opaque event identity.
        schema: Exact declaration bound to this event.
        source: Exact source matching the schema owner.
        subject: Exact Registry subject matching the schema profile.
        namespace: Exact Namespace shared with the subject.
        classification: Classification matching the schema declaration.
        occurred_at: Time the semantic fact occurred.
        recorded_at: Time the fact was recorded.
        correlation_id: Optional bounded opaque correlation identity.
        causation_id: Optional bounded opaque causation identity.
        payload: Bounded JSON-compatible declared payload values.

    Raises:
        EventContractValidationError: If the envelope is malformed or conflicts with its schema.
    """

    event_id: str
    schema: EventSchemaDeclaration
    source: EventSource
    subject: EventSubject
    namespace: NamespaceKey
    classification: EventClassification
    occurred_at: datetime
    recorded_at: datetime
    correlation_id: str | None
    causation_id: str | None
    payload: Mapping[str, JsonValue]
    checksum: str = field(init=False)

    def __post_init__(self) -> None:
        """Validate and freeze an event fact without publishing, persisting, or dispatching it.

        Raises:
            EventContractValidationError: If the envelope is malformed or conflicts with its schema.
        """
        normalized_payload = _normalize_payload(self.payload)
        if (
            not _is_safe_identifier(self.event_id)
            or not isinstance(self.schema, EventSchemaDeclaration)
            or self.source != self.schema.source
            or not isinstance(self.subject, EventSubject)
            or not isinstance(self.namespace, NamespaceKey)
            or self.namespace != self.subject.namespace
            or self.subject.profile not in self.schema.subject_profiles
            or self.classification != self.schema.classification
            or not _is_utc_datetime(self.occurred_at)
            or not _is_utc_datetime(self.recorded_at)
            or self.recorded_at < self.occurred_at
            or not _is_optional_identifier(self.correlation_id)
            or not _is_optional_identifier(self.causation_id)
        ):
            raise EventContractValidationError("invalid event envelope")
        _validate_payload_matches_declaration(normalized_payload, self.schema.payload_fields)
        object.__setattr__(self, "payload", MappingProxyType(normalized_payload))
        object.__setattr__(self, "checksum", _event_checksum(self, normalized_payload))


def assess_event_compatibility(
    declaration: EventCompatibilityDeclaration,
) -> EventCompatibilityAssessment:
    """Assess one exact schema relationship without inferring consumer behavior.

    Args:
        declaration: Explicit predecessor/successor relationship to assess.

    Returns:
        Immutable deterministic compatibility assessment.

    Raises:
        EventContractValidationError: If a declared compatibility claim is inaccurate.
    """
    if not isinstance(declaration, EventCompatibilityDeclaration):
        raise EventContractValidationError("invalid event compatibility declaration")
    outcome = _derive_compatibility_outcome(declaration)
    if declaration.declared_outcome is not outcome:
        raise EventContractValidationError("event compatibility outcome does not match declaration")
    return EventCompatibilityAssessment(declaration, outcome)


def _derive_compatibility_outcome(
    declaration: EventCompatibilityDeclaration,
) -> EventCompatibilityOutcome:
    """Derive an exact compatibility outcome from declarative schema facts."""
    if declaration.predecessor is None:
        return EventCompatibilityOutcome.INITIAL
    predecessor = declaration.predecessor
    successor = declaration.successor
    if not declaration.predecessor_supported:
        return EventCompatibilityOutcome.UNSUPPORTED
    if predecessor.event_type != successor.event_type:
        return EventCompatibilityOutcome.REPLACEMENT
    if predecessor.source != successor.source:
        return EventCompatibilityOutcome.REPLACEMENT
    if (
        predecessor.classification != successor.classification
        or predecessor.subject_profiles != successor.subject_profiles
        or predecessor.ordering_guarantee != successor.ordering_guarantee
    ):
        return EventCompatibilityOutcome.BREAKING
    predecessor_fields = {
        field_value.name: field_value for field_value in predecessor.payload_fields
    }
    successor_fields = {field_value.name: field_value for field_value in successor.payload_fields}
    for field_name, predecessor_field in predecessor_fields.items():
        successor_field = successor_fields.get(field_name)
        if successor_field != predecessor_field:
            return EventCompatibilityOutcome.BREAKING
    if any(
        field_value.required
        for name, field_value in successor_fields.items()
        if name not in predecessor_fields
    ):
        return EventCompatibilityOutcome.BREAKING
    return EventCompatibilityOutcome.BACKWARD_COMPATIBLE


def _validate_payload_matches_declaration(
    payload: Mapping[str, JsonValue], fields: tuple[EventPayloadField, ...]
) -> None:
    """Reject unknown, missing, or type-incompatible payload fields."""
    declared_fields = {field_value.name: field_value for field_value in fields}
    if set(payload) - set(declared_fields):
        raise EventContractValidationError("event payload contains undeclared field")
    for field_value in fields:
        if field_value.name not in payload:
            if field_value.required:
                raise EventContractValidationError("event payload misses required field")
            continue
        if not _payload_value_matches_kind(payload[field_value.name], field_value.value_kind):
            raise EventContractValidationError(
                "event payload field type does not match declaration"
            )


def _normalize_payload(value: object) -> dict[str, JsonValue]:
    """Copy one bounded JSON-compatible mapping into immutable-compatible values."""
    if not isinstance(value, Mapping) or len(value) > _MAXIMUM_PAYLOAD_ITEMS:
        raise EventContractValidationError("invalid event payload")
    normalized: dict[str, JsonValue] = {}
    for key, item in value.items():
        if type(key) is not str or not _is_safe_payload_field_name(key):
            raise EventContractValidationError("invalid event payload field name")
        normalized[key] = _normalize_json_value(item, depth=0)
    return normalized


def _normalize_json_value(value: object, *, depth: int) -> JsonValue:
    """Normalize one bounded JSON-compatible value without preserving mutable containers."""
    if depth > _MAXIMUM_PAYLOAD_DEPTH:
        raise EventContractValidationError("event payload exceeds maximum depth")
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise EventContractValidationError("invalid event payload value")
        return value
    if isinstance(value, Mapping):
        if len(value) > _MAXIMUM_PAYLOAD_ITEMS:
            raise EventContractValidationError("event payload exceeds maximum items")
        return MappingProxyType(
            {
                _validated_payload_mapping_key(key): _normalize_json_value(item, depth=depth + 1)
                for key, item in value.items()
            }
        )
    if isinstance(value, tuple | list):
        if len(value) > _MAXIMUM_PAYLOAD_ITEMS:
            raise EventContractValidationError("event payload exceeds maximum items")
        return tuple(_normalize_json_value(item, depth=depth + 1) for item in value)
    raise EventContractValidationError("invalid event payload value")


def _validated_payload_mapping_key(value: object) -> str:
    """Return a safe nested payload key or reject it."""
    if type(value) is not str or not _is_safe_payload_field_name(value):
        raise EventContractValidationError("invalid event payload field name")
    return value


def _is_json_value(value: object) -> bool:
    """Determine whether a value can be normalized as a bounded JSON value."""
    try:
        _normalize_json_value(value, depth=0)
    except EventContractValidationError:
        return False
    return True


def _payload_value_matches_kind(value: JsonValue, value_kind: str) -> bool:
    """Determine whether a normalized payload value matches its declared JSON kind."""
    return {
        "null": value is None,
        "boolean": type(value) is bool,
        "integer": type(value) is int,
        "number": type(value) in (int, float),
        "string": type(value) is str,
        "array": type(value) is tuple,
        "object": isinstance(value, Mapping),
    }.get(value_kind, False)


def _is_safe_identifier(value: object) -> bool:
    """Determine whether one opaque identifier is bounded and non-runtime-shaped."""
    return (
        type(value) is str
        and len(value) <= _MAXIMUM_IDENTIFIER_LENGTH
        and _IDENTIFIER_PATTERN.fullmatch(value) is not None
        and value not in {"current", "latest"}
        and not any(token in value.lower() for token in _FORBIDDEN_TOKENS)
    )


def _is_safe_payload_field_name(value: object) -> bool:
    """Determine whether a payload field name excludes protected or runtime-shaped data."""
    return (
        type(value) is str
        and _FIELD_NAME_PATTERN.fullmatch(value) is not None
        and not any(token in value.lower() for token in _FORBIDDEN_TOKENS)
    )


def _is_optional_identifier(value: object) -> bool:
    """Determine whether an optional correlation or causation identity is safe."""
    return value is None or _is_safe_identifier(value)


def _is_utc_datetime(value: object) -> bool:
    """Determine whether a datetime is timezone-aware UTC evidence."""
    return isinstance(value, datetime) and value.tzinfo is UTC


def _event_checksum(envelope: EventEnvelope, payload: Mapping[str, JsonValue]) -> str:
    """Calculate the deterministic semantic checksum of one immutable event fact."""
    material = {
        "causation_id": envelope.causation_id,
        "classification": {
            "category": envelope.classification.category.value,
            "metadata": envelope.classification.metadata_classification.value,
        },
        "correlation_id": envelope.correlation_id,
        "event_id": envelope.event_id,
        "event_type": envelope.schema.event_type.value,
        "event_version": envelope.schema.version.value,
        "namespace": str(envelope.namespace.value),
        "occurred_at": envelope.occurred_at.isoformat(),
        "payload": _json_ready_value(payload),
        "recorded_at": envelope.recorded_at.isoformat(),
        "source": {
            "bounded_context": envelope.source.bounded_context,
            "deployment": envelope.source.deployment,
            "service": envelope.source.service,
        },
        "subject": _subject_checksum_material(envelope.subject),
    }
    serialized = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _subject_checksum_material(subject: EventSubject) -> dict[str, object]:
    """Build safe canonical checksum material for exact subject identities."""
    material: dict[str, object] = {
        "resource_id": subject.resource.resource_id,
        "resource_type": subject.resource.resource_type.value,
    }
    if subject.version is not None:
        material["version_id"] = subject.version.version_id
    if subject.artifact is not None:
        material["artifact_id"] = subject.artifact.artifact_id
    return material


def _json_ready_value(value: JsonValue) -> object:
    """Convert immutable JSON values into standard-library JSON serialization values."""
    if isinstance(value, Mapping):
        return {key: _json_ready_value(item) for key, item in value.items()}
    if type(value) is tuple:
        return [_json_ready_value(item) for item in value]
    return value
