from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from uuid import UUID

from resource_registry.transformations.execution_plan import ExecutionPlanOutput
from resource_registry.transformations.runs.contracts import (
    OUTPUT_POLICY_CONTRACT_VERSION,
    TransformationOutputPolicy,
    TransformationOutputPolicyKind,
)
from resource_registry.transformations.runs.exceptions import (
    InvalidTransformationRunCreationRequestError,
)
from resource_registry.utils.json_checksum import (
    TransformationSpecificationChecksumError,
    canonicalize_transformation_specification,
)

_SAFE_SEGMENT_PATTERN = re.compile(r"[^a-zA-Z0-9._-]+")
_DATASET_SLUG_PATTERN = re.compile(r"^[-a-zA-Z0-9_]+$")
_NEW_DATASET_REQUIRED_FIELDS = frozenset({"dataset_slug", "dataset_name"})
_NEW_DATASET_OPTIONAL_FIELDS = frozenset(
    {"description", "owner_team", "tags"},
)
_MAX_DATASET_SLUG_LENGTH = 120
_MAX_DATASET_NAME_LENGTH = 255


def canonicalize_output_policies(
    *,
    outputs: Sequence[ExecutionPlanOutput],
    policies: Sequence[TransformationOutputPolicy],
) -> tuple[dict[str, object], ...]:
    """Validate and order explicit policies against compiled final outputs."""
    if len(outputs) != len(policies):
        raise InvalidTransformationRunCreationRequestError(
            "Every compiled output requires exactly one explicit output policy.",
        )
    by_name: dict[str, TransformationOutputPolicy] = {}
    for policy in policies:
        if policy.logical_name in by_name:
            raise InvalidTransformationRunCreationRequestError(
                "Output policy logical names must be unique.",
            )
        by_name[policy.logical_name] = policy
    if set(by_name) != {output.logical_name for output in outputs}:
        raise InvalidTransformationRunCreationRequestError(
            "Output policies must match compiled output logical names exactly.",
        )
    return tuple(_canonicalize_policy(by_name[output.logical_name]) for output in outputs)


def calculate_output_policy_checksum(policy_snapshot: Mapping[str, object]) -> str:
    """Calculate the canonical SHA-256 checksum for one output policy snapshot."""
    try:
        canonical_json = canonicalize_transformation_specification(policy_snapshot)
    except TransformationSpecificationChecksumError as error:
        raise InvalidTransformationRunCreationRequestError(
            "Output policy must contain only canonical JSON values.",
        ) from error
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def build_output_destination_key(
    *,
    tenant_id: UUID,
    run_id: UUID,
    output_index: int,
    logical_name: str,
    data_format: str,
) -> str:
    """Build one deterministic managed destination key for a run output."""
    logical_segment = _safe_segment(logical_name)
    format_segment = _safe_segment(data_format.lower()) or "bin"
    return (
        f"transformations/{tenant_id}/{run_id}/outputs/"
        f"{output_index:04d}-{logical_segment}.{format_segment}"
    )


def _canonicalize_policy(policy: TransformationOutputPolicy) -> dict[str, object]:
    if policy.contract_version != OUTPUT_POLICY_CONTRACT_VERSION:
        raise InvalidTransformationRunCreationRequestError(
            "Output policy contract version is unsupported.",
        )
    logical_name = _require_trimmed(policy.logical_name, field_name="logical name")
    destination_provider = _require_trimmed(
        policy.destination_storage_provider,
        field_name="destination storage provider",
    ).lower()
    configuration = dict(policy.configuration)
    target_dataset_id = _normalize_target_dataset_id(policy.target_dataset_id)
    if policy.policy_kind == TransformationOutputPolicyKind.STANDALONE:
        _require_empty_configuration(configuration)
        if target_dataset_id is not None:
            raise InvalidTransformationRunCreationRequestError(
                "Standalone output policies cannot target a dataset.",
            )
    elif policy.policy_kind == TransformationOutputPolicyKind.NEW_DATASET:
        _validate_new_dataset_configuration(configuration)
        if target_dataset_id is not None:
            raise InvalidTransformationRunCreationRequestError(
                "New-dataset output policies cannot target an existing dataset.",
            )
    elif policy.policy_kind == TransformationOutputPolicyKind.NEW_DATASET_VERSION:
        _require_empty_configuration(configuration)
        if target_dataset_id is None:
            raise InvalidTransformationRunCreationRequestError(
                "New-dataset-version output policies require an exact target dataset.",
            )
    else:
        raise InvalidTransformationRunCreationRequestError("Output policy kind is unsupported.")
    return {
        "contract_version": policy.contract_version,
        "logical_name": logical_name,
        "policy_kind": policy.policy_kind.value,
        "destination_storage_provider": destination_provider,
        "target_dataset_id": target_dataset_id,
        "configuration": configuration,
    }


def _validate_new_dataset_configuration(configuration: Mapping[str, object]) -> None:
    fields = set(configuration)
    if not _NEW_DATASET_REQUIRED_FIELDS.issubset(fields):
        raise InvalidTransformationRunCreationRequestError(
            "New-dataset policy requires dataset_slug and dataset_name.",
        )
    if fields - (_NEW_DATASET_REQUIRED_FIELDS | _NEW_DATASET_OPTIONAL_FIELDS):
        raise InvalidTransformationRunCreationRequestError(
            "New-dataset policy contains unsupported fields.",
        )
    dataset_slug = _require_trimmed(configuration["dataset_slug"], field_name="dataset slug")
    dataset_name = _require_trimmed(configuration["dataset_name"], field_name="dataset name")
    if (
        len(dataset_slug) > _MAX_DATASET_SLUG_LENGTH
        or _DATASET_SLUG_PATTERN.fullmatch(dataset_slug) is None
    ):
        raise InvalidTransformationRunCreationRequestError(
            "New-dataset policy dataset_slug is not a valid registry slug.",
        )
    if len(dataset_name) > _MAX_DATASET_NAME_LENGTH:
        raise InvalidTransformationRunCreationRequestError(
            "New-dataset policy dataset_name is too long.",
        )
    for optional_field in ("description", "owner_team"):
        optional_value = configuration.get(optional_field)
        if optional_value is not None:
            _require_trimmed(optional_value, field_name=optional_field.replace("_", " "))
    tags = configuration.get("tags", [])
    if not isinstance(tags, Sequence) or isinstance(tags, str | bytes | bytearray):
        raise InvalidTransformationRunCreationRequestError("Dataset tags must be a sequence.")
    for tag in tags:
        _require_trimmed(tag, field_name="dataset tag")


def _require_empty_configuration(configuration: Mapping[str, object]) -> None:
    if configuration:
        raise InvalidTransformationRunCreationRequestError(
            "This output policy does not accept configuration fields.",
        )


def _normalize_target_dataset_id(value: UUID | str | None) -> str | None:
    if value is None:
        return None
    try:
        return str(value if isinstance(value, UUID) else UUID(value))
    except (TypeError, ValueError, AttributeError) as error:
        raise InvalidTransformationRunCreationRequestError(
            "Output policy target dataset ID must be a valid UUID.",
        ) from error


def _require_trimmed(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise InvalidTransformationRunCreationRequestError(
            f"Output policy {field_name} must be a non-empty exact string.",
        )
    return value


def _safe_segment(value: str) -> str:
    normalized = _SAFE_SEGMENT_PATTERN.sub("-", value).strip("-.")
    if not normalized:
        raise InvalidTransformationRunCreationRequestError(
            "Output identity cannot produce a safe storage path.",
        )
    return normalized
