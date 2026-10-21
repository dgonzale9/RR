from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence

from resource_registry.utils.json_checksum import (
    TransformationSpecificationChecksumError,
    canonicalize_transformation_specification,
)

TRANSFORMATION_RUN_FINGERPRINT_CONTRACT_VERSION = "transformation-run-request/3.0"


class TransformationRunFingerprintError(ValueError):
    """Raise when transformation-run fingerprint material is not canonical JSON."""


def build_transformation_run_request_fingerprint(  # noqa: PLR0913
    *,
    tenant_id: str,
    definition_id: str,
    definition_version_id: str,
    specification_checksum: str,
    execution_plan_checksum: str,
    output_policies: Sequence[Mapping[str, object]],
    authorization_checksum: str,
) -> str:
    """Build the versioned canonical SHA-256 identity for one run request."""
    payload: Mapping[str, object] = {
        "fingerprint_contract": TRANSFORMATION_RUN_FINGERPRINT_CONTRACT_VERSION,
        "tenant_id": tenant_id,
        "definition_id": definition_id,
        "definition_version_id": definition_version_id,
        "specification_checksum": specification_checksum,
        "execution_plan_checksum": execution_plan_checksum,
        "output_policies": list(output_policies),
        "authorization_checksum": authorization_checksum,
    }
    try:
        canonical_json = canonicalize_transformation_specification(payload)
    except TransformationSpecificationChecksumError as error:
        raise TransformationRunFingerprintError(
            "Transformation-run fingerprint material is not canonical JSON.",
        ) from error
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
