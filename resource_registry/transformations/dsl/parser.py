from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from resource_registry.transformations.validation.exceptions import (
    TransformationSpecificationParseError,
)

JsonObject = Mapping[str, object]


@dataclass(frozen=True)
class ParsedTransformationSpecification:
    """Represent an immutable parsed transformation specification document."""

    specification: JsonObject


class TransformationSpecificationParser:
    """Parse persisted transformation specification JSON into immutable structures."""

    def parse(self, specification: object) -> ParsedTransformationSpecification:
        """Parse a persisted transformation specification JSON document.

        Args:
            specification: Parsed JSON value loaded from persistence.

        Returns:
            Immutable parsed transformation specification.

        Raises:
            TransformationSpecificationParseError: If the document is not a JSON object or
                contains values outside the supported parsed JSON contract.
        """
        if not isinstance(specification, Mapping):
            raise TransformationSpecificationParseError(
                "Transformation specification root must be a JSON object.",
            )
        frozen_specification = _freeze_json_value(specification)
        if not isinstance(frozen_specification, Mapping):
            raise TransformationSpecificationParseError(
                "Transformation specification root must be a JSON object.",
            )
        return ParsedTransformationSpecification(specification=frozen_specification)


def _freeze_json_value(value: object) -> object:
    """Recursively freeze supported JSON-compatible containers."""
    if isinstance(value, Mapping):
        frozen_items: dict[str, object] = {}
        for key, nested_value in value.items():
            if not isinstance(key, str):
                raise TransformationSpecificationParseError(
                    "Transformation specification object keys must be strings.",
                )
            frozen_items[key] = _freeze_json_value(nested_value)
        return MappingProxyType(frozen_items)

    if isinstance(value, list):
        return tuple(_freeze_json_value(nested_value) for nested_value in value)

    if isinstance(value, bool | str) or value is None:
        return value

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        if not math.isfinite(value):
            raise TransformationSpecificationParseError(
                "Transformation specification numbers must be finite.",
            )
        return value

    raise TransformationSpecificationParseError(
        "Transformation specification contains unsupported parsed JSON values.",
    )


def thaw_json_value(value: object) -> object:
    """Return a mutable JSON-compatible copy of a parsed immutable value."""
    if isinstance(value, Mapping):
        return {key: thaw_json_value(nested_value) for key, nested_value in value.items()}

    if isinstance(value, tuple):
        return [thaw_json_value(nested_value) for nested_value in value]

    return value
