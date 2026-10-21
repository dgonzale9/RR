from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID


class TransformationSpecificationChecksumError(ValueError):
    """Raise when a transformation specification cannot be canonically checksummed."""


def canonicalize_transformation_specification(
    specification: Mapping[str, object] | list[object],
) -> str:
    """Serialize a parsed transformation specification as canonical JSON.

    Args:
        specification: Parsed JSON object or array representing a transformation specification.

    Returns:
        Compact canonical JSON with recursively sorted object keys.

    Raises:
        TransformationSpecificationChecksumError: If the specification is not a supported
            parsed JSON object or array.
    """
    _validate_top_level_transformation_specification(specification)
    try:
        return json.dumps(
            specification,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (RecursionError, TypeError, ValueError) as error:
        raise TransformationSpecificationChecksumError(
            "Transformation specification cannot be serialized as canonical JSON.",
        ) from error


def calculate_transformation_specification_checksum(
    specification: Mapping[str, object] | list[object],
) -> str:
    """Calculate the SHA-256 checksum for a parsed transformation specification.

    Args:
        specification: Parsed JSON object or array representing a transformation specification.

    Returns:
        Lowercase 64-character SHA-256 hexadecimal digest.

    Raises:
        TransformationSpecificationChecksumError: If the specification is not a supported
            parsed JSON object or array.
    """
    canonical_json = canonicalize_transformation_specification(specification)
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def _validate_top_level_transformation_specification(specification: object) -> None:
    """Validate that the top-level value is a parsed JSON object or array."""
    if not isinstance(specification, Mapping | list):
        raise TransformationSpecificationChecksumError(
            "Transformation specification must be a parsed JSON object or array.",
        )
    _validate_json_compatible_value(specification)


def _validate_json_compatible_value(value: object) -> None:
    """Validate that a value is compatible with the approved canonical JSON contract."""
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            if not isinstance(key, str):
                raise TransformationSpecificationChecksumError(
                    "Transformation specification object keys must be strings.",
                )
            _validate_json_compatible_value(nested_value)
        return

    if isinstance(value, list):
        for nested_value in value:
            _validate_json_compatible_value(nested_value)
        return

    if isinstance(value, bool | str) or value is None:
        return

    if isinstance(value, int):
        return

    if isinstance(value, float):
        if not math.isfinite(value):
            raise TransformationSpecificationChecksumError(
                "Transformation specification numbers must be finite.",
            )
        return

    if isinstance(value, Decimal | UUID | datetime | date | time | set | tuple):
        raise TransformationSpecificationChecksumError(
            "Transformation specification contains unsupported non-standard JSON values.",
        )

    raise TransformationSpecificationChecksumError(
        "Transformation specification contains unsupported values.",
    )
