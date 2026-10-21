from __future__ import annotations

import math
from collections.abc import Iterator, Mapping


class ApplicationJsonValueError(ValueError):
    """Raise when application data is not finite JSON-compatible data."""


class FrozenJsonMapping(Mapping[str, object]):
    """Store recursively immutable JSON object data in canonical key order."""

    __slots__ = ("_items", "_values")

    def __init__(self, values: Mapping[str, object] | None = None) -> None:
        source = values or {}
        if not all(isinstance(key, str) for key in source):
            raise ApplicationJsonValueError("JSON object keys must be strings.")
        self._items = tuple(
            (key, freeze_json_value(source[key]))
            for key in sorted(source)
        )
        self._values = dict(self._items)

    def __getitem__(self, key: str) -> object:
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(key for key, _value in self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __hash__(self) -> int:
        return hash(self._items)

    def __repr__(self) -> str:
        return f"FrozenJsonMapping({dict(self._items)!r})"


def freeze_json_value(value: object) -> object:
    """Recursively freeze finite JSON-compatible data."""
    if isinstance(value, FrozenJsonMapping):
        return value
    if isinstance(value, Mapping):
        return FrozenJsonMapping(value)
    if isinstance(value, list | tuple):
        return tuple(freeze_json_value(item) for item in value)
    if isinstance(value, bool | str) or value is None:
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ApplicationJsonValueError("Value must contain only finite JSON-compatible data.")


def freeze_json_mapping(value: Mapping[str, object] | None) -> FrozenJsonMapping:
    """Return one canonical recursively immutable JSON object."""
    return value if isinstance(value, FrozenJsonMapping) else FrozenJsonMapping(value)
