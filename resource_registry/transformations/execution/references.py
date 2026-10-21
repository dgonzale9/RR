from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast
from urllib.parse import parse_qsl, urlsplit

from resource_registry.transformations.binding.immutable_json import freeze_json_value
from resource_registry.transformations.providers import (
    TransformationIntegrityMetadata,
    TransformationProviderInputReference,
    TransformationProviderOutputReference,
)


class InvalidTransformationReferenceError(ValueError):
    """Raise when a provider reference is not a canonical reference-only value."""


def serialize_input_reference(
    reference: TransformationProviderInputReference,
) -> dict[str, object]:
    """Serialize one immutable provider input reference canonically."""
    return _serialize_reference(
        logical_name=reference.logical_name,
        materialized_reference=reference.materialized_reference,
        data_format=reference.data_format,
        integrity=reference.integrity,
        metadata=reference.metadata,
    )


def serialize_output_reference(
    reference: TransformationProviderOutputReference,
) -> dict[str, object]:
    """Serialize one immutable provider output reference canonically."""
    return {
        **_serialize_reference(
            logical_name=reference.logical_name,
            materialized_reference=reference.materialized_reference,
            data_format=reference.data_format,
            integrity=reference.integrity,
            metadata=reference.metadata,
        ),
        "is_temporary": reference.is_temporary,
    }


def deserialize_output_references(
    snapshot: object,
) -> tuple[TransformationProviderOutputReference, ...]:
    """Strictly reconstruct canonical provider output references."""
    if not isinstance(snapshot, list):
        raise InvalidTransformationReferenceError("Output checkpoint must be a list.")
    return tuple(_deserialize_output_reference(value) for value in snapshot)


def validate_canonical_reference(value: str) -> None:
    """Reject empty, signed, or expiring provider reference strings."""
    if not value or value != value.strip():
        raise InvalidTransformationReferenceError("Reference must be non-empty and canonical.")
    query_keys = {key.lower() for key, _ in parse_qsl(urlsplit(value).query)}
    if query_keys & {"sig", "x-amz-signature", "x-amz-expires", "se", "expires"}:
        raise InvalidTransformationReferenceError(
            "Signed or expiring provider references are not permitted.",
        )


def _serialize_reference(
    *,
    logical_name: str,
    materialized_reference: str,
    data_format: str,
    integrity: TransformationIntegrityMetadata,
    metadata: Mapping[str, object],
) -> dict[str, object]:
    validate_canonical_reference(materialized_reference)
    return {
        "logical_name": logical_name,
        "materialized_reference": materialized_reference,
        "data_format": data_format,
        "integrity": {
            "checksum_algorithm": integrity.checksum_algorithm,
            "checksum_value": integrity.checksum_value,
            "size_bytes": integrity.size_bytes,
            "etag": integrity.etag,
            "version_identifier": integrity.version_identifier,
            "metadata": _thaw_mapping(integrity.metadata),
        },
        "metadata": _thaw_mapping(metadata),
    }


def _deserialize_output_reference(value: object) -> TransformationProviderOutputReference:
    reference = _require_mapping(value)
    _require_exact_fields(
        reference,
        {
            "logical_name",
            "materialized_reference",
            "data_format",
            "integrity",
            "metadata",
            "is_temporary",
        },
    )
    materialized_reference = _require_string(reference["materialized_reference"])
    validate_canonical_reference(materialized_reference)
    integrity_value = _require_mapping(reference["integrity"])
    _require_exact_fields(
        integrity_value,
        {
            "checksum_algorithm",
            "checksum_value",
            "size_bytes",
            "etag",
            "version_identifier",
            "metadata",
        },
    )
    is_temporary = reference["is_temporary"]
    if not isinstance(is_temporary, bool):
        raise InvalidTransformationReferenceError("Output temporary intent must be boolean.")
    size_bytes = integrity_value["size_bytes"]
    if size_bytes is not None and (not isinstance(size_bytes, int) or isinstance(size_bytes, bool)):
        raise InvalidTransformationReferenceError("Reference size must be an integer or null.")
    return TransformationProviderOutputReference(
        logical_name=_require_string(reference["logical_name"]),
        materialized_reference=materialized_reference,
        data_format=_require_string(reference["data_format"]),
        integrity=TransformationIntegrityMetadata(
            checksum_algorithm=_optional_string(integrity_value["checksum_algorithm"]),
            checksum_value=_optional_string(integrity_value["checksum_value"]),
            size_bytes=size_bytes,
            etag=_optional_string(integrity_value["etag"]),
            version_identifier=_optional_string(integrity_value["version_identifier"]),
            metadata=_require_mapping(integrity_value["metadata"]),
        ),
        is_temporary=is_temporary,
        metadata=_require_mapping(reference["metadata"]),
    )


def _require_mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise InvalidTransformationReferenceError("Reference value must be an object.")
    frozen = freeze_json_value(value)
    if not isinstance(frozen, Mapping):
        raise InvalidTransformationReferenceError("Reference value must be an object.")
    return cast(Mapping[str, object], frozen)


def _require_exact_fields(value: Mapping[str, object], expected: set[str]) -> None:
    if set(value) != expected:
        raise InvalidTransformationReferenceError("Reference object has an invalid field set.")


def _require_string(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise InvalidTransformationReferenceError("Reference field must be an exact string.")
    return value


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return _require_string(value)


def _thaw_mapping(value: Mapping[str, object]) -> dict[str, object]:
    return {key: _thaw_value(nested) for key, nested in value.items()}


def _thaw_value(value: object) -> object:
    if isinstance(value, Mapping):
        return _thaw_mapping(cast(Mapping[str, object], value))
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_thaw_value(nested) for nested in value]
    return value
