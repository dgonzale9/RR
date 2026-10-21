from __future__ import annotations

import math
import uuid
from dataclasses import FrozenInstanceError, replace

import pytest

from resource_registry.tests.transformation_run_test_helpers import (
    build_test_authorization_decision,
    build_test_execution_plan,
    build_test_output_policies,
)
from resource_registry.transformations.binding.immutable_json import ImmutableJsonValueError
from resource_registry.transformations.binding.results import ParameterValueClassification
from resource_registry.transformations.execution_plan import ExecutionPlanParameter
from resource_registry.transformations.runs.contracts import TransformationRunCreationRequest
from resource_registry.transformations.runs.exceptions import (
    InvalidTransformationExecutionPlanSnapshotError,
    InvalidTransformationRunCreationRequestError,
    TransformationExecutionPlanChecksumMismatchError,
    TransformationRunOutputDeclarationMismatchError,
)
from resource_registry.transformations.runs.fingerprint import (
    build_transformation_run_request_fingerprint,
)
from resource_registry.transformations.runs.output_policies import canonicalize_output_policies
from resource_registry.transformations.runs.serialization import (
    serialize_transformation_execution_plan,
)

_SPECIFICATION_CHECKSUM = "a" * 64
_SHA256_HEX_LENGTH = 64


def _identities() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    return uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def test_plan_snapshot_round_trips_parameters_inputs_and_checksum() -> None:
    tenant_id, definition_id, version_id = _identities()
    plan = build_test_execution_plan(
        tenant_id=tenant_id,
        definition_id=definition_id,
        definition_version_id=version_id,
        specification_checksum=_SPECIFICATION_CHECKSUM,
    )

    snapshot = serialize_transformation_execution_plan(plan=plan)

    assert snapshot["plan_checksum"] == plan.plan_checksum
    assert snapshot["parameters"] == [
        {"classification": "literal", "parameter_key": "threshold", "value": 0.5},
    ]
    assert snapshot["inputs"][0]["dataset_version_id"] == str(uuid.UUID(int=102))  # type: ignore[index]


def test_tampered_plan_checksum_is_rejected() -> None:
    tenant_id, definition_id, version_id = _identities()
    plan = build_test_execution_plan(
        tenant_id=tenant_id,
        definition_id=definition_id,
        definition_version_id=version_id,
        specification_checksum=_SPECIFICATION_CHECKSUM,
    )

    with pytest.raises(TransformationExecutionPlanChecksumMismatchError):
        serialize_transformation_execution_plan(plan=replace(plan, plan_checksum="0" * 64))


def test_duplicate_output_declaration_is_rejected_before_persistence() -> None:
    tenant_id, definition_id, version_id = _identities()
    plan = build_test_execution_plan(
        tenant_id=tenant_id,
        definition_id=definition_id,
        definition_version_id=version_id,
        specification_checksum=_SPECIFICATION_CHECKSUM,
        output_names=("duplicate", "duplicate"),
    )

    with pytest.raises(TransformationRunOutputDeclarationMismatchError):
        serialize_transformation_execution_plan(plan=plan)


def test_non_finite_and_signed_runtime_values_are_rejected() -> None:
    tenant_id, definition_id, version_id = _identities()
    valid = build_test_execution_plan(
        tenant_id=tenant_id,
        definition_id=definition_id,
        definition_version_id=version_id,
        specification_checksum=_SPECIFICATION_CHECKSUM,
    )
    with pytest.raises(ImmutableJsonValueError):
        ExecutionPlanParameter(
            parameter_key="threshold",
            value=math.inf,
            classification=ParameterValueClassification.LITERAL,
        )
    signed = build_test_execution_plan(
        tenant_id=tenant_id,
        definition_id=definition_id,
        definition_version_id=version_id,
        specification_checksum=_SPECIFICATION_CHECKSUM,
        source_uri="https://storage.example/input?sig=private",
    )

    assert valid.plan_checksum
    with pytest.raises(InvalidTransformationExecutionPlanSnapshotError):
        serialize_transformation_execution_plan(plan=signed)


def test_fingerprint_is_deterministic_and_changes_only_with_execution_identity() -> None:
    tenant_id, definition_id, version_id = _identities()
    values = {
        "tenant_id": str(tenant_id),
        "definition_id": str(definition_id),
        "definition_version_id": str(version_id),
        "specification_checksum": _SPECIFICATION_CHECKSUM,
        "execution_plan_checksum": "b" * 64,
        "authorization_checksum": "d" * 64,
        "output_policies": (
            {
                "logical_name": "result",
                "policy_kind": "STANDALONE",
                "output_policy_checksum": "c" * 64,
                "destination_storage_provider": "local",
                "target_dataset_id": None,
                "configuration": {},
            },
        ),
    }

    first = build_transformation_run_request_fingerprint(**values)
    repeated = build_transformation_run_request_fingerprint(**dict(reversed(tuple(values.items()))))
    changed = build_transformation_run_request_fingerprint(
        **{**values, "execution_plan_checksum": "c" * 64},
    )
    changed_policy = build_transformation_run_request_fingerprint(
        **{
            **values,
            "output_policies": (
                {
                    **values["output_policies"][0],
                    "destination_storage_provider": "azure_blob",
                },
            ),
        },
    )
    changed_authorization = build_transformation_run_request_fingerprint(
        **{**values, "authorization_checksum": "e" * 64},
    )

    assert first == repeated
    assert first != changed
    assert first != changed_policy
    assert first != changed_authorization
    assert len(first) == _SHA256_HEX_LENGTH


def test_every_compiled_output_requires_one_explicit_policy() -> None:
    tenant_id, definition_id, version_id = _identities()
    plan = build_test_execution_plan(
        tenant_id=tenant_id,
        definition_id=definition_id,
        definition_version_id=version_id,
        specification_checksum=_SPECIFICATION_CHECKSUM,
    )

    with pytest.raises(InvalidTransformationRunCreationRequestError):
        canonicalize_output_policies(outputs=plan.outputs, policies=())


def test_creation_request_is_frozen_and_keeps_caller_key_exactly() -> None:
    tenant_id, definition_id, version_id = _identities()
    plan = build_test_execution_plan(
        tenant_id=tenant_id,
        definition_id=definition_id,
        definition_version_id=version_id,
        specification_checksum=_SPECIFICATION_CHECKSUM,
    )
    request = TransformationRunCreationRequest(
        tenant_id=tenant_id,
        definition_id=definition_id,
        definition_version_id=version_id,
        execution_plan=plan,
        authorization_decision=build_test_authorization_decision(
            tenant_id=tenant_id,
            definition_id=definition_id,
            definition_version_id=version_id,
        ),
        output_policies=build_test_output_policies(
            tuple(output.logical_name for output in plan.outputs),
        ),
        idempotency_key=" Caller-Key ",
    )

    assert request.idempotency_key == " Caller-Key "
    with pytest.raises(FrozenInstanceError):
        request.idempotency_key = "changed"  # type: ignore[misc]
