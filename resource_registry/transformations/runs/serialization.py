from __future__ import annotations

import json
from collections.abc import Mapping
from typing import cast

from resource_registry.transformations.execution_plan import TransformationExecutionPlan
from resource_registry.transformations.execution_plan.checksum import (
    ExecutionPlanChecksumError,
    calculate_execution_plan_checksum,
)
from resource_registry.transformations.runs.exceptions import (
    InvalidTransformationExecutionPlanSnapshotError,
    TransformationExecutionPlanChecksumMismatchError,
    TransformationRunOutputDeclarationMismatchError,
)
from resource_registry.utils.json_checksum import (
    TransformationSpecificationChecksumError,
    canonicalize_transformation_specification,
)


def serialize_transformation_execution_plan(
    *,
    plan: TransformationExecutionPlan,
) -> dict[str, object]:
    """Build and verify the complete canonical JSON snapshot for persistence."""
    checksum_payload = plan.to_checksum_dict()
    try:
        recalculated_checksum = calculate_execution_plan_checksum(checksum_payload)
    except ExecutionPlanChecksumError as error:
        raise InvalidTransformationExecutionPlanSnapshotError(
            "Execution plan cannot be represented by canonical JSON.",
        ) from error
    if recalculated_checksum != plan.plan_checksum:
        raise TransformationExecutionPlanChecksumMismatchError(
            "Execution plan checksum does not match its canonical snapshot.",
        )

    _validate_output_declarations(plan=plan)
    complete_snapshot = {**checksum_payload, "plan_checksum": plan.plan_checksum}
    if _contains_signed_runtime_reference(complete_snapshot):
        raise InvalidTransformationExecutionPlanSnapshotError(
            "Execution plan contains a signed runtime reference.",
        )
    try:
        canonical_json = canonicalize_transformation_specification(complete_snapshot)
        decoded = json.loads(canonical_json)
    except (TransformationSpecificationChecksumError, json.JSONDecodeError) as error:
        raise InvalidTransformationExecutionPlanSnapshotError(
            "Execution plan cannot be represented by canonical JSON.",
        ) from error
    if not isinstance(decoded, dict):
        raise InvalidTransformationExecutionPlanSnapshotError(
            "Execution plan snapshot must be a JSON object.",
        )
    return cast(dict[str, object], decoded)


def _validate_output_declarations(*, plan: TransformationExecutionPlan) -> None:
    """Require complete unique final-output identities before persistence."""
    names: set[str] = set()
    for output in plan.outputs:
        if (
            not output.logical_name
            or not output.source_symbol
            or not output.producer_step_id
            or not output.data_format
            or output.logical_name in names
        ):
            raise TransformationRunOutputDeclarationMismatchError(
                "Execution plan final-output declarations are incomplete or duplicated.",
            )
        names.add(output.logical_name)


def _contains_signed_runtime_reference(value: object) -> bool:
    """Return whether any persisted string contains signed query material."""
    if isinstance(value, Mapping):
        return any(_contains_signed_runtime_reference(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_signed_runtime_reference(item) for item in value)
    return isinstance(value, str) and "sig=" in value.lower()
