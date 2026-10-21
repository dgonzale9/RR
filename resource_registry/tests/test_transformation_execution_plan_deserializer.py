from __future__ import annotations

import copy
import uuid

import pytest

from resource_registry.tests.transformation_run_test_helpers import build_test_execution_plan
from resource_registry.transformations.execution_plan import (
    TransformationExecutionPlanDeserializationError,
    UnsupportedTransformationExecutionPlanVersionError,
    deserialize_transformation_execution_plan,
)
from resource_registry.transformations.runs.serialization import (
    serialize_transformation_execution_plan,
)


def _snapshot() -> dict[str, object]:
    plan = build_test_execution_plan(
        tenant_id=uuid.uuid4(),
        definition_id=uuid.uuid4(),
        definition_version_id=uuid.uuid4(),
        specification_checksum="a" * 64,
    )
    return serialize_transformation_execution_plan(plan=plan)


def test_valid_snapshot_reconstructs_exact_immutable_plan() -> None:
    snapshot = _snapshot()

    plan = deserialize_transformation_execution_plan(snapshot)

    assert plan.plan_checksum == snapshot["plan_checksum"]
    assert plan.to_checksum_dict()["steps"] == snapshot["steps"]
    with pytest.raises(TypeError):
        plan.steps[0].configuration["changed"] = True  # type: ignore[index]


@pytest.mark.parametrize("mutation", ["unknown", "missing", "plan_checksum", "capability"])
def test_invalid_snapshot_shape_or_checksum_fails_closed(mutation: str) -> None:
    snapshot = copy.deepcopy(_snapshot())
    if mutation == "unknown":
        snapshot["unknown"] = True
    elif mutation == "missing":
        del snapshot["dsl_version"]
    elif mutation == "plan_checksum":
        snapshot["plan_checksum"] = "f" * 64
    else:
        steps = snapshot["steps"]
        assert isinstance(steps, list)
        capabilities = steps[0]["capabilities"]
        capabilities["is_idempotent"] = False

    with pytest.raises(TransformationExecutionPlanDeserializationError):
        deserialize_transformation_execution_plan(snapshot)


def test_unsupported_contract_and_signed_reference_fail_closed() -> None:
    unsupported = _snapshot()
    unsupported["contract_version"] = "2.0"
    with pytest.raises(UnsupportedTransformationExecutionPlanVersionError):
        deserialize_transformation_execution_plan(unsupported)

    signed = _snapshot()
    inputs = signed["inputs"]
    assert isinstance(inputs, list)
    inputs[0]["source_uri"] = "https://example.test/input?sig=secret"
    with pytest.raises(TransformationExecutionPlanDeserializationError):
        deserialize_transformation_execution_plan(signed)
