from __future__ import annotations

from collections.abc import Mapping
from dataclasses import FrozenInstanceError

import pytest

from resource_registry.transformations.providers import (
    ProviderSideEffectDisposition,
    ResolvedTransformationOperationIdentity,
    TransformationIntegrityMetadata,
    TransformationOperationCapabilityDescriptor,
    TransformationOperationIdentity,
    TransformationProviderCapabilityDescriptor,
    TransformationProviderExecutionError,
    TransformationProviderExecutionRequest,
    TransformationProviderExpectedOutput,
    TransformationProviderFailure,
    TransformationProviderIdentity,
    TransformationProviderInputReference,
    TransformationProviderOutputReference,
    TransformationProviderValidationFinding,
    TransformationProviderValidationRequest,
    TransformationProviderValidationResult,
)
from resource_registry.transformations.providers.exceptions import (
    InvalidOperationKeyError,
    InvalidOperationVersionError,
    InvalidProviderKeyError,
    InvalidProviderVersionError,
)


def _identity(
    *,
    provider_key: str = "exact-provider",
    provider_version: str = "v1.0",
    operation_key: str = "transform",
    operation_version: str = "2026-01",
) -> ResolvedTransformationOperationIdentity:
    return ResolvedTransformationOperationIdentity(
        provider_key=provider_key,
        provider_version=provider_version,
        operation_key=operation_key,
        operation_version=operation_version,
    )


@pytest.mark.parametrize(
    ("constructor", "error_type"),
    [
        (lambda: TransformationProviderIdentity("", "1"), InvalidProviderKeyError),
        (lambda: TransformationProviderIdentity(" key", "1"), InvalidProviderKeyError),
        (lambda: TransformationProviderIdentity("key", " "), InvalidProviderVersionError),
        (lambda: TransformationOperationIdentity("", "1"), InvalidOperationKeyError),
        (lambda: TransformationOperationIdentity("key", "1 "), InvalidOperationVersionError),
    ],
)
def test_identity_contracts_reject_invalid_values(constructor, error_type) -> None:
    with pytest.raises(error_type):
        constructor()


def test_identity_contracts_preserve_case_and_versions_exactly() -> None:
    first = TransformationProviderIdentity("Provider", "01.0")
    second = TransformationProviderIdentity("provider", "1.0")

    assert first.provider_key == "Provider"
    assert first.provider_version == "01.0"
    assert first != second


def test_capabilities_are_frozen_and_independent() -> None:
    operation = TransformationOperationCapabilityDescriptor(
        identity=_identity(),
        supported_input_formats=("Parquet",),
        supported_output_formats=("JSON",),
        is_idempotent=True,
        is_checkpoint_reuse_safe=False,
        is_checkpoint_reconciliation_safe=True,
        supports_cooperative_cancellation=False,
        metadata={"nested": {"enabled": True}},
    )
    descriptor = TransformationProviderCapabilityDescriptor(
        identity=operation.identity.provider_identity,
        operations=(operation,),
    )

    assert operation.is_idempotent is True
    assert operation.is_checkpoint_reuse_safe is False
    assert operation.is_checkpoint_reconciliation_safe is True
    assert descriptor.to_dict()["operations"][0]["supported_input_formats"] == ["Parquet"]  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        operation.is_idempotent = False  # type: ignore[misc]
    with pytest.raises(TypeError):
        operation.metadata["changed"] = True  # type: ignore[index]


def test_provider_validation_findings_are_sorted_and_separate_from_story_one() -> None:
    identity = _identity()
    later = TransformationProviderValidationFinding(
        identity=identity,
        code="unsupported_mode",
        path="$.config.mode",
        severity="error",
        message="Configured mode is not supported.",
    )
    earlier = TransformationProviderValidationFinding(
        identity=identity,
        code="missing_field",
        path="$.config.field",
        severity="error",
        message="Required provider field is missing.",
    )
    result = TransformationProviderValidationResult(
        is_valid=False,
        findings=(later, earlier),
    )
    request = TransformationProviderValidationRequest(
        identity=identity,
        step_id="step-1",
        configuration={"private": {"value": "not-rendered-in-finding"}},
    )

    assert result.findings == (earlier, later)
    assert "not-rendered" not in later.message
    assert isinstance(request.configuration, Mapping)
    with pytest.raises(TypeError):
        request.configuration["new"] = True  # type: ignore[index]


def test_execution_contracts_are_reference_only_and_recursively_immutable() -> None:
    identity = _identity()
    integrity = TransformationIntegrityMetadata(
        checksum_algorithm="sha256",
        checksum_value="abc",
        size_bytes=10,
        metadata={"schema": {"columns": ["id"]}},
    )
    input_reference = TransformationProviderInputReference(
        logical_name="source",
        materialized_reference="opaque://input/1",
        data_format="PARQUET",
        integrity=integrity,
    )
    request = TransformationProviderExecutionRequest(
        identity=identity,
        run_id="run-1",
        run_attempt_number=1,
        run_attempt_token="attempt-token",
        step_id="step-1",
        step_index=0,
        step_attempt_id="step-attempt-1",
        attempt_idempotency_token="attempt-token",
        configuration={"mode": "strict"},
        bound_parameters={"threshold": 0.5},
        inputs=(input_reference,),
        expected_outputs=(
            TransformationProviderExpectedOutput(
                logical_name="result",
                accepted_formats=("PARQUET",),
            ),
        ),
    )
    output = TransformationProviderOutputReference(
        logical_name="result",
        materialized_reference="opaque://output/1",
        data_format="PARQUET",
        integrity=integrity,
    )

    assert request.attempt_idempotency_token == "attempt-token"
    assert not hasattr(request.inputs[0], "rows")
    assert output.materialized_reference == "opaque://output/1"
    with pytest.raises(TypeError):
        integrity.metadata["changed"] = True  # type: ignore[index]


@pytest.mark.parametrize("disposition", tuple(ProviderSideEffectDisposition))
def test_typed_failure_preserves_retryability_and_side_effect_disposition(
    disposition: ProviderSideEffectDisposition,
) -> None:
    failure = TransformationProviderFailure(
        identity=_identity(),
        code="provider_failure",
        message="Provider operation failed safely.",
        retryable=True,
        side_effect_disposition=disposition,
    )
    error = TransformationProviderExecutionError(failure)

    assert error.failure.retryable is True
    assert error.failure.side_effect_disposition == disposition
    assert str(error) == "Provider operation failed safely."
