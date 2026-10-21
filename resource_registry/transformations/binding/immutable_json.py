from __future__ import annotations

import math
from collections.abc import Mapping
from types import MappingProxyType


class ImmutableJsonValueError(ValueError):
    """Raise when a value cannot be represented as immutable JSON-compatible data."""


def freeze_json_value(value: object) -> object:
    """Recursively freeze a JSON-compatible value with deterministic object ordering.

    Args:
        value: Candidate JSON-compatible value.

    Returns:
        Immutable JSON scalar, tuple, or read-only mapping.

    Raises:
        ImmutableJsonValueError: If the value is outside the supported JSON contract.
    """
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ImmutableJsonValueError("JSON object keys must be strings.")
        return MappingProxyType(
            {
                key: freeze_json_value(value[key])
                for key in sorted(value)
            },
        )
    if isinstance(value, list | tuple):
        return tuple(freeze_json_value(item) for item in value)
    if isinstance(value, bool | str) or value is None:
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ImmutableJsonValueError("Value must contain only finite JSON-compatible data.")


def thaw_json_value(value: object) -> object:
    """Return a mutable JSON-compatible copy for JSON Schema validation.

    Args:
        value: Immutable or mutable JSON-compatible value.

    Returns:
        Mutable dictionaries/lists and unchanged scalar values.
    """
    if isinstance(value, Mapping):
        return {key: thaw_json_value(nested_value) for key, nested_value in value.items()}
    if isinstance(value, tuple | list):
        return [thaw_json_value(item) for item in value]
    return value
