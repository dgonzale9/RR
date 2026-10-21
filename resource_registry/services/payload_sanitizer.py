from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

DEFAULT_BLOCKED_KEYS = frozenset(
    {
        "raw_rows",
        "preview",
        "credentials",
        "token",
        "password",
        "secret",
        "connection string",
        "signed url",
    },
)

DEFAULT_SENSITIVE_MARKERS = (
    "credentials",
    "token",
    "password",
    "secret",
    "connection string",
    "signed url",
)


def sanitize_payload(
    value: Any,
    *,
    blocked_keys: Iterable[str] = DEFAULT_BLOCKED_KEYS,
    sensitive_markers: Iterable[str] = DEFAULT_SENSITIVE_MARKERS,
) -> Any:
    """Recursively remove sensitive keys and redact sensitive string values."""
    blocked = {str(item).lower() for item in blocked_keys}
    markers = tuple(str(item).lower() for item in sensitive_markers)

    def _sanitize(item: Any) -> Any:
        if isinstance(item, Mapping):
            cleaned: dict[str, Any] = {}
            for key, nested in item.items():
                key_text = str(key)
                key_lower = key_text.lower()
                normalized_key = key_lower.replace("_", " ").replace("-", " ")
                if (
                    key_lower in blocked
                    or normalized_key in blocked
                    or any(marker in normalized_key for marker in markers)
                ):
                    continue
                cleaned[key_text] = _sanitize(nested)
            return cleaned
        if isinstance(item, Sequence) and not isinstance(item, str | bytes | bytearray):
            return [_sanitize(entry) for entry in item]
        if isinstance(item, str):
            lowered = item.lower()
            if any(marker in lowered for marker in ("sig=", "signature=", "x-amz-signature=")):
                return item.split("?", maxsplit=1)[0]
            if any(marker in lowered for marker in markers):
                return "[REDACTED]"
            return item
        return item

    return _sanitize(value)
