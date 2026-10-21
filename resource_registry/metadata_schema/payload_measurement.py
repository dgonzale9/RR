"""Measure immutable metadata payloads with the approved RFC 8785 representation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TypeAlias, cast

import rfc8785

from resource_registry.registry_core.metadata import JsonValue

__all__ = (  # noqa: RUF022
    "MetadataPayloadMeasurementError",
    "MetadataPayloadMeasurement",
    "measure_metadata_payload",
)

_MutableJsonValue: TypeAlias = (
    None | bool | int | float | str | list["_MutableJsonValue"] | dict[str, "_MutableJsonValue"]
)


class MetadataPayloadMeasurementError(ValueError):
    """Report a payload that cannot be represented by RFC 8785."""


@dataclass(frozen=True, slots=True, kw_only=True)
class MetadataPayloadMeasurement:
    """Carry deterministic structural measurements for one metadata payload.

    Args:
        payload_bytes: UTF-8 byte length of the RFC 8785 canonical payload representation.
        nesting_depth: Maximum container depth, with the root payload object at depth one.
        object_key_count: Aggregate number of keys in all payload objects.
        collection_item_count: Aggregate number of items in all payload collections.

    Raises:
        MetadataPayloadMeasurementError: If a measurement is invalid.
    """

    payload_bytes: int
    nesting_depth: int
    object_key_count: int
    collection_item_count: int

    def __post_init__(self) -> None:
        """Validate positive, deterministic measurement values.

        Raises:
            MetadataPayloadMeasurementError: If a measurement is invalid.
        """
        if (
            type(self.payload_bytes) is not int
            or self.payload_bytes <= 0
            or type(self.nesting_depth) is not int
            or self.nesting_depth <= 0
            or type(self.object_key_count) is not int
            or self.object_key_count < 0
            or type(self.collection_item_count) is not int
            or self.collection_item_count < 0
        ):
            raise MetadataPayloadMeasurementError("invalid metadata payload measurement")


def measure_metadata_payload(payload: Mapping[str, JsonValue]) -> MetadataPayloadMeasurement:
    """Measure one immutable Core payload using its RFC 8785 canonical representation.

    Args:
        payload: Immutable JSON-compatible Core payload mapping.

    Returns:
        Canonical UTF-8 byte and structural measurements.

    Raises:
        MetadataPayloadMeasurementError: If the payload cannot be represented by RFC 8785.
    """
    if not isinstance(payload, Mapping):
        raise MetadataPayloadMeasurementError("invalid metadata payload measurement")
    normalized_payload = _to_mutable_json_value(payload)
    if not isinstance(normalized_payload, dict):
        raise MetadataPayloadMeasurementError("invalid metadata payload measurement")
    try:
        canonical_bytes = rfc8785.dumps(normalized_payload)
    except rfc8785.CanonicalizationError as error:
        raise MetadataPayloadMeasurementError(
            "metadata payload is not representable by RFC 8785"
        ) from error
    nesting_depth, object_key_count, collection_item_count = _measure_structure(normalized_payload)
    return MetadataPayloadMeasurement(
        payload_bytes=len(canonical_bytes),
        nesting_depth=nesting_depth,
        object_key_count=object_key_count,
        collection_item_count=collection_item_count,
    )


def _to_mutable_json_value(candidate: JsonValue) -> _MutableJsonValue:
    """Convert frozen Core JSON values to RFC 8785-compatible mutable containers."""
    if candidate is None or type(candidate) in (bool, int, float, str):
        return cast(_MutableJsonValue, candidate)
    if isinstance(candidate, Mapping):
        if not all(type(key) is str for key in candidate):
            raise MetadataPayloadMeasurementError("invalid metadata payload measurement")
        return cast(
            _MutableJsonValue,
            {key: _to_mutable_json_value(value) for key, value in candidate.items()},
        )
    if isinstance(candidate, tuple):
        return cast(_MutableJsonValue, [_to_mutable_json_value(value) for value in candidate])
    raise MetadataPayloadMeasurementError("invalid metadata payload measurement")


def _measure_structure(payload: dict[str, _MutableJsonValue]) -> tuple[int, int, int]:
    """Measure root-inclusive JSON container depth, object keys, and collection items."""
    maximum_depth = 1
    object_key_count = 0
    collection_item_count = 0
    pending_values: list[tuple[_MutableJsonValue, int]] = [(payload, 1)]
    while pending_values:
        current, depth = pending_values.pop()
        if isinstance(current, dict):
            maximum_depth = max(maximum_depth, depth)
            object_key_count += len(current)
            pending_values.extend((value, depth + 1) for value in current.values())
        elif isinstance(current, list):
            maximum_depth = max(maximum_depth, depth)
            collection_item_count += len(current)
            pending_values.extend((value, depth + 1) for value in current)
    return maximum_depth, object_key_count, collection_item_count
