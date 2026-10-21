from __future__ import annotations

import hashlib
from collections.abc import Mapping

from resource_registry.transformations.binding.immutable_json import thaw_json_value
from resource_registry.utils.json_checksum import canonicalize_transformation_specification


class ExecutionPlanChecksumError(ValueError):
    """Raise when plan or capability data cannot be checksummed canonically."""


def calculate_execution_plan_checksum(payload: Mapping[str, object]) -> str:
    """Calculate SHA-256 using the existing canonical specification JSON contract.

    Args:
        payload: Immutable or mutable JSON-compatible plan payload.

    Returns:
        Lowercase SHA-256 hexadecimal digest.

    Raises:
        ExecutionPlanChecksumError: If the payload is not canonical JSON-compatible.
    """
    mutable_payload = thaw_json_value(payload)
    if not isinstance(mutable_payload, dict):
        raise ExecutionPlanChecksumError("Execution-plan checksum payload must be an object.")
    try:
        canonical_json = canonicalize_transformation_specification(mutable_payload)
    except ValueError as error:
        raise ExecutionPlanChecksumError(
            "Execution-plan payload is not canonical JSON-compatible.",
        ) from error
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
