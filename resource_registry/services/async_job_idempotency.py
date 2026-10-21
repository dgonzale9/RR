from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

_EXCLUDED_KEYS = frozenset(
    {
        "cancellation_requested_at",
        "completed_at",
        "created_at",
        "error_code",
        "error_message",
        "execution_ms",
        "id",
        "idempotency_key",
        "job_id",
        "metadata",
        "metadata_json",
        "query_job_id",
        "intelligence_job_id",
        "ingestion_job_id",
        "request_fingerprint",
        "retry_count",
        "runtime_metadata",
        "started_at",
        "status",
        "timed_out_at",
        "timestamp",
        "updated_at",
    },
)


def build_async_job_request_fingerprint(payload: Mapping[str, Any]) -> str:
    """Build a deterministic SHA-256 fingerprint for an async job request.

    Args:
        payload: Semantic request payload for one async job family.

    Returns:
        Hex-encoded SHA-256 digest of canonical JSON bytes.
    """
    canonical_payload = normalize_async_job_fingerprint_value(payload)
    canonical_json = json.dumps(
        canonical_payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def build_async_job_idempotency_key(*, job_family: str, request_fingerprint: str) -> str:
    """Build a stable idempotency key for persisted async job rows.

    Args:
        job_family: Stable async job family key.
        request_fingerprint: Canonical request fingerprint.

    Returns:
        A family-scoped idempotency key.
    """
    return f"{job_family}:{request_fingerprint}"


def normalize_async_job_fingerprint_value(value: Any) -> Any:
    """Normalize a value into canonical JSON-compatible fingerprint material.

    Args:
        value: Arbitrary semantic request value.

    Returns:
        JSON-compatible normalized value with runtime-only keys removed.
    """
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, nested_value in value.items():
            key_text = str(key)
            if key_text.lower() in _EXCLUDED_KEYS:
                continue
            normalized[key_text] = normalize_async_job_fingerprint_value(nested_value)
        return normalized
    if isinstance(value, str):
        return value
    if isinstance(value, bool | int | float) or value is None:
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Sequence):
        return [normalize_async_job_fingerprint_value(item) for item in value]
    return str(value)
