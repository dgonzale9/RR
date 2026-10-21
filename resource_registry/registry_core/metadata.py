"""Define immutable metadata value contracts for Registry Core."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from types import MappingProxyType
from typing import TypeAlias, cast

from .errors import RegistryCoreValidationError
from .identity import ResourceKey
from .versioning import ArtifactKey, ResourceVersionKey

__all__ = (  # noqa: RUF022
    "MetadataNamespaceKey",
    "MetadataSchemaKey",
    "MetadataClassification",
    "MetadataScope",
    "MetadataProvenance",
    "MetadataEnvelope",
)

_NAMESPACE_PATTERN = r"[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*){2,}"
_SCHEMA_NAME_PATTERN = r"[a-z][a-z0-9_-]*"
_MAXIMUM_NAMESPACE_LENGTH = 128
_MAXIMUM_SCHEMA_NAME_LENGTH = 64
JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonScalar | tuple["JsonValue", ...] | Mapping[str, "JsonValue"]


class MetadataClassification(str, Enum):
    """Classify metadata with the approved Core-safe vocabulary."""

    INTERNAL = "internal"

    @classmethod
    def _missing_(cls, value: object) -> MetadataClassification | None:
        """Reject values outside the approved classification vocabulary.

        Args:
            value: Candidate metadata classification.

        Raises:
            RegistryCoreValidationError: If the candidate is not approved.
        """
        raise RegistryCoreValidationError("invalid metadata classification")


class MetadataScope(str, Enum):
    """Declare the exact identity scope of a metadata envelope."""

    RESOURCE = "resource"
    VERSION = "version"
    ARTIFACT = "artifact"

    @classmethod
    def _missing_(cls, value: object) -> MetadataScope | None:
        """Reject values outside the approved metadata scope vocabulary.

        Args:
            value: Candidate metadata scope.

        Raises:
            RegistryCoreValidationError: If the candidate is not approved.
        """
        raise RegistryCoreValidationError("invalid metadata scope")


@dataclass(frozen=True, slots=True, kw_only=True)
class MetadataNamespaceKey:
    """Identify one opaque metadata-schema namespace without registering it.

    Args:
        value: Lowercase ASCII, three-segment metadata namespace key.

    Raises:
        RegistryCoreValidationError: If the namespace key is invalid.
    """

    value: str

    def __post_init__(self) -> None:
        """Validate the metadata namespace grammar without resolving it.

        Raises:
            RegistryCoreValidationError: If the namespace key is invalid.
        """
        if not _is_valid_namespace(self.value):
            raise RegistryCoreValidationError("invalid metadata namespace key")


@dataclass(frozen=True, slots=True, kw_only=True)
class MetadataSchemaKey:
    """Identify one schema name under an exact metadata namespace.

    Args:
        namespace: Immutable metadata namespace containing the schema name.
        name: Lowercase ASCII schema name within the namespace.

    Raises:
        RegistryCoreValidationError: If the namespace or schema name is invalid.
    """

    namespace: MetadataNamespaceKey
    name: str

    def __post_init__(self) -> None:
        """Validate the schema namespace and name in declared field order.

        Raises:
            RegistryCoreValidationError: If the namespace or schema name is invalid.
        """
        if not isinstance(self.namespace, MetadataNamespaceKey):
            raise RegistryCoreValidationError("invalid metadata schema namespace")
        if not _is_valid_schema_name(self.name):
            raise RegistryCoreValidationError("invalid metadata schema name")


@dataclass(frozen=True, slots=True, kw_only=True)
class MetadataProvenance:
    """Carry opaque source-kind and producer labels without authority semantics.

    Args:
        source_kind: Non-blank source classification preserved exactly.
        producer: Non-blank producer label preserved exactly.

    Raises:
        RegistryCoreValidationError: If either opaque label is invalid.
    """

    source_kind: str
    producer: str

    def __post_init__(self) -> None:
        """Validate provenance labels in declared field order.

        Raises:
            RegistryCoreValidationError: If either opaque label is invalid.
        """
        if not _is_non_blank_plain_string(self.source_kind):
            raise RegistryCoreValidationError("invalid metadata provenance source kind")
        if not _is_non_blank_plain_string(self.producer):
            raise RegistryCoreValidationError("invalid metadata provenance producer")


@dataclass(frozen=True, slots=True, kw_only=True)
class MetadataEnvelope:
    """Bind immutable JSON-compatible payload evidence to one exact typed subject.

    Args:
        scope: Exact identity scope of the metadata subject.
        subject: Resource, version, or artifact identity matching ``scope``.
        schema: Immutable metadata schema identity.
        schema_version: Non-blank issuer-owned opaque schema version.
        revision: Positive envelope-local revision number.
        classification: Closed Core metadata classification.
        provenance: Opaque source-kind and producer labels.
        observed_at: Timezone-aware observation timestamp normalized to UTC.
        payload: JSON-compatible mapping copied into an immutable deterministic structure.

    Raises:
        RegistryCoreValidationError: If a field violates the envelope contract.
    """

    scope: MetadataScope
    subject: ResourceKey | ResourceVersionKey | ArtifactKey
    schema: MetadataSchemaKey
    schema_version: str
    revision: int
    classification: MetadataClassification
    provenance: MetadataProvenance
    observed_at: datetime
    payload: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        """Validate and freeze envelope evidence in declared field order.

        Raises:
            RegistryCoreValidationError: If a field violates the envelope contract.
        """
        if not isinstance(self.scope, MetadataScope):
            raise RegistryCoreValidationError("invalid metadata envelope scope")
        if not _is_subject_valid_for_scope(self.scope, self.subject):
            raise RegistryCoreValidationError("invalid metadata envelope subject")
        if not isinstance(self.schema, MetadataSchemaKey):
            raise RegistryCoreValidationError("invalid metadata envelope schema")
        if not _is_non_blank_plain_string(self.schema_version):
            raise RegistryCoreValidationError("invalid metadata envelope schema version")
        if type(self.revision) is not int or self.revision <= 0:
            raise RegistryCoreValidationError("invalid metadata envelope revision")
        if not isinstance(self.classification, MetadataClassification):
            raise RegistryCoreValidationError("invalid metadata envelope classification")
        if not isinstance(self.provenance, MetadataProvenance):
            raise RegistryCoreValidationError("invalid metadata envelope provenance")
        if not _is_timezone_aware_datetime(self.observed_at):
            raise RegistryCoreValidationError("invalid metadata envelope observed at")
        frozen_payload = _freeze_payload(self.payload)
        if frozen_payload is None:
            raise RegistryCoreValidationError("invalid metadata envelope payload")
        object.__setattr__(self, "observed_at", self.observed_at.astimezone(UTC))
        object.__setattr__(self, "payload", frozen_payload)


def _is_valid_namespace(candidate_value: object) -> bool:
    """Determine whether a value has the approved metadata namespace grammar.

    Args:
        candidate_value: Candidate metadata namespace key.

    Returns:
        Whether the candidate is a valid lowercase ASCII metadata namespace key.
    """
    return (
        type(candidate_value) is str
        and len(candidate_value) <= _MAXIMUM_NAMESPACE_LENGTH
        and re.fullmatch(_NAMESPACE_PATTERN, candidate_value) is not None
    )


def _is_valid_schema_name(candidate_value: object) -> bool:
    """Determine whether a value has the approved metadata schema-name grammar.

    Args:
        candidate_value: Candidate schema name.

    Returns:
        Whether the candidate is a valid lowercase ASCII schema name.
    """
    return (
        type(candidate_value) is str
        and len(candidate_value) <= _MAXIMUM_SCHEMA_NAME_LENGTH
        and re.fullmatch(_SCHEMA_NAME_PATTERN, candidate_value) is not None
    )


def _is_non_blank_plain_string(candidate_value: object) -> bool:
    """Determine whether a value is a non-blank plain string.

    Args:
        candidate_value: Candidate opaque string.

    Returns:
        Whether the candidate is a non-blank plain string.
    """
    return type(candidate_value) is str and bool(candidate_value.strip())


def _is_subject_valid_for_scope(scope: MetadataScope, subject: object) -> bool:
    """Determine whether a metadata subject exactly matches its declared scope.

    Args:
        scope: Declared metadata scope.
        subject: Candidate typed metadata subject.

    Returns:
        Whether the subject has the exact type required by the scope.
    """
    return (
        (scope is MetadataScope.RESOURCE and isinstance(subject, ResourceKey))
        or (scope is MetadataScope.VERSION and isinstance(subject, ResourceVersionKey))
        or (scope is MetadataScope.ARTIFACT and isinstance(subject, ArtifactKey))
    )


def _is_timezone_aware_datetime(candidate_value: object) -> bool:
    """Determine whether a value is a timezone-aware datetime.

    Args:
        candidate_value: Candidate observation timestamp.

    Returns:
        Whether the candidate has a timezone offset.
    """
    return isinstance(candidate_value, datetime) and candidate_value.tzinfo is not None and (
        candidate_value.utcoffset() is not None
    )


def _freeze_payload(candidate_payload: object) -> Mapping[str, JsonValue] | None:
    """Copy a JSON-compatible payload into an immutable deterministic mapping.

    Args:
        candidate_payload: Candidate JSON-compatible payload mapping.

    Returns:
        An immutable recursively frozen payload, or ``None`` if invalid.
    """
    if not isinstance(candidate_payload, Mapping):
        return None
    try:
        frozen_payload = _freeze_json_value(candidate_payload, set())
    except RecursionError:
        return None
    return frozen_payload if isinstance(frozen_payload, Mapping) else None


def _freeze_json_value(
    candidate_value: object,
    active_container_ids: set[int],
) -> JsonValue | None:
    """Recursively freeze one JSON-compatible value while rejecting cycles.

    Args:
        candidate_value: Candidate JSON-compatible value.
        active_container_ids: Identities of containers currently being traversed.

    Returns:
        An immutable JSON-compatible value, or ``None`` if invalid.
    """
    if candidate_value is None:
        return None
    if type(candidate_value) in (bool, int, str):
        return cast(JsonScalar, candidate_value)
    if type(candidate_value) is float:
        return candidate_value if math.isfinite(candidate_value) else None
    if isinstance(candidate_value, Mapping):
        return _freeze_json_mapping(
            cast(Mapping[object, object], candidate_value),
            active_container_ids,
        )
    if type(candidate_value) in (list, tuple):
        return _freeze_json_sequence(
            cast(list[object] | tuple[object, ...], candidate_value),
            active_container_ids,
        )
    return None


def _freeze_json_mapping(
    candidate_mapping: Mapping[object, object],
    active_container_ids: set[int],
) -> Mapping[str, JsonValue] | None:
    """Freeze one JSON object mapping with sorted deterministic keys.

    Args:
        candidate_mapping: Candidate JSON object mapping.
        active_container_ids: Identities of containers currently being traversed.

    Returns:
        An immutable sorted JSON object, or ``None`` if invalid.
    """
    mapping_identity = id(candidate_mapping)
    if mapping_identity in active_container_ids:
        return None
    active_container_ids.add(mapping_identity)
    try:
        mapping_keys = list(candidate_mapping)
        if not all(type(key) is str for key in mapping_keys):
            return None
        frozen_items: list[tuple[str, JsonValue]] = []
        for key in sorted(cast(list[str], mapping_keys)):
            candidate_value = candidate_mapping[key]
            frozen_value = _freeze_json_value(candidate_value, active_container_ids)
            if candidate_value is None:
                frozen_items.append((key, None))
                continue
            if frozen_value is None:
                return None
            frozen_items.append((key, frozen_value))
        return MappingProxyType(dict(frozen_items))
    finally:
        active_container_ids.remove(mapping_identity)


def _freeze_json_sequence(
    candidate_sequence: list[object] | tuple[object, ...],
    active_container_ids: set[int],
) -> tuple[JsonValue, ...] | None:
    """Freeze one JSON array into an immutable tuple while rejecting cycles.

    Args:
        candidate_sequence: Candidate JSON array sequence.
        active_container_ids: Identities of containers currently being traversed.

    Returns:
        An immutable JSON array tuple, or ``None`` if invalid.
    """
    sequence_identity = id(candidate_sequence)
    if sequence_identity in active_container_ids:
        return None
    active_container_ids.add(sequence_identity)
    try:
        frozen_values: list[JsonValue] = []
        for candidate_value in candidate_sequence:
            frozen_value = _freeze_json_value(candidate_value, active_container_ids)
            if candidate_value is None:
                frozen_values.append(None)
                continue
            if frozen_value is None:
                return None
            frozen_values.append(frozen_value)
        return tuple(frozen_values)
    finally:
        active_container_ids.remove(sequence_identity)
