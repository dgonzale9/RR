from __future__ import annotations

import uuid

import pytest
from django.db import connection

from resource_registry.tests.transformation_run_test_helpers import build_test_execution_plan
from resource_registry.tests.transformation_runtime_test_helpers import (
    freeze_test_provider_registry,
)
from resource_registry.transformations.execution import (
    CheckpointReuseDecision,
    CheckpointReuseDecisionKind,
    TransformationRunAttemptExecutionOutcome,
    TransformationRunAttemptExecutionRequest,
    TransformationRunExecutionService,
    TransformationStepAttemptDescriptor,
)
from resource_registry.transformations.execution.contracts import CheckpointReferenceAvailability
from resource_registry.transformations.execution.runtime import (
    DefaultTransformationProviderRuntimeContext,
    DenyingTransformationRuntimeSecretAccess,
    MonotonicTransformationDeadlineSignal,
)
from resource_registry.transformations.providers import (
    ResolvedTransformationOperationIdentity,
    TransformationOperationCapabilityDescriptor,
    TransformationProviderCapabilityDescriptor,
    TransformationProviderExecutionRequest,
    TransformationProviderExecutionResult,
    TransformationProviderIdentity,
    TransformationProviderOutputReference,
    TransformationProviderRegistry,
    TransformationProviderRuntimeContext,
    TransformationProviderValidationRequest,
    TransformationProviderValidationResult,
)

pytestmark = pytest.mark.django_db(transaction=True)


class _CancellationSignal:
    def is_cancellation_requested(self) -> bool:
        return False


class _AvailableVerifier:
    def verify_references(
        self,
        *,
        references: tuple[TransformationProviderOutputReference, ...],
    ) -> CheckpointReferenceAvailability:
        _ = references
        return CheckpointReferenceAvailability.AVAILABLE


class _Provider:
    def __init__(self, *, drift: bool = False) -> None:
        self.requests: list[TransformationProviderExecutionRequest] = []
        identity = ResolvedTransformationOperationIdentity(
            provider_key="provider",
            provider_version="v1",
            operation_key="transform",
            operation_version="op1",
        )
        operation = TransformationOperationCapabilityDescriptor(
            identity=identity,
            supported_input_formats=("PARQUET",),
            supported_output_formats=("PARQUET",),
            is_idempotent=not drift,
            is_checkpoint_reuse_safe=True,
            is_checkpoint_reconciliation_safe=False,
            supports_cooperative_cancellation=True,
            metadata={"bounded": True},
        )
        self._descriptor = TransformationProviderCapabilityDescriptor(
            identity=identity.provider_identity,
            operations=(operation,),
            metadata={"trusted": True},
        )

    @property
    def provider_identity(self) -> TransformationProviderIdentity:
        return self._descriptor.identity

    @property
    def capability_descriptor(self) -> TransformationProviderCapabilityDescriptor:
        return self._descriptor

    def validate_operation(
        self,
        *,
        request: TransformationProviderValidationRequest,
    ) -> TransformationProviderValidationResult:
        _ = request
        return TransformationProviderValidationResult(is_valid=True)

    def execute(
        self,
        *,
        request: TransformationProviderExecutionRequest,
        runtime_context: TransformationProviderRuntimeContext,
    ) -> TransformationProviderExecutionResult:
        _ = runtime_context
        assert connection.in_atomic_block is False
        self.requests.append(request)
        return TransformationProviderExecutionResult(
            identity=request.identity,
            outputs=tuple(
                TransformationProviderOutputReference(
                    logical_name=expected.logical_name,
                    materialized_reference=f"opaque://output/{expected.logical_name}",
                    data_format=expected.accepted_formats[0],
                    is_temporary=expected.is_temporary,
                )
                for expected in request.expected_outputs
            ),
        )


class _Persistence:
    def __init__(self) -> None:
        self.started = False
        self.succeeded = False
        self.failure_code: str | None = None

    def is_cancellation_requested(self, **kwargs: object) -> bool:
        _ = kwargs
        return False

    def decide_checkpoint_reuse(self, **kwargs: object) -> CheckpointReuseDecision:
        _ = kwargs
        return CheckpointReuseDecision(CheckpointReuseDecisionKind.EXECUTE, "no_checkpoint")

    def start_step(self, **kwargs: object) -> None:
        _ = kwargs
        self.started = True

    def persist_reused_step(self, **kwargs: object) -> None:
        raise AssertionError("The test checkpoint is not reusable.")

    def persist_step_success(self, **kwargs: object) -> None:
        _ = kwargs
        self.succeeded = True

    def persist_step_failure(self, **kwargs: object) -> None:
        failure = kwargs["failure"]
        self.failure_code = failure.code  # type: ignore[union-attr]

    def persist_cancellation(self, **kwargs: object) -> None:
        raise AssertionError("Cancellation was not requested.")


def _execution_request() -> TransformationRunAttemptExecutionRequest:
    plan = build_test_execution_plan(
        tenant_id=uuid.uuid4(),
        definition_id=uuid.uuid4(),
        definition_version_id=uuid.uuid4(),
        specification_checksum="a" * 64,
    )
    attempt_token = str(uuid.uuid4())
    return TransformationRunAttemptExecutionRequest(
        tenant_id=plan.tenant_id,
        run_id=str(uuid.uuid4()),
        attempt_number=1,
        attempt_token=attempt_token,
        plan=plan,
        step_attempts=(
            TransformationStepAttemptDescriptor(
                step_attempt_id=str(uuid.uuid4()),
                step_id="transform",
                step_index=0,
                run_attempt_number=1,
                run_attempt_token=attempt_token,
                attempt_idempotency_token="c" * 64,
                status="QUEUED",
            ),
        ),
        correlation_id="correlation",
        runtime_context=DefaultTransformationProviderRuntimeContext(
            cancellation_signal=_CancellationSignal(),
            deadline_signal=MonotonicTransformationDeadlineSignal(),
            secret_access=DenyingTransformationRuntimeSecretAccess(),
        ),
        checkpoint_reference_verifier=_AvailableVerifier(),
    )


def test_engine_executes_exact_provider_with_immutable_parameters_outside_transaction() -> None:
    provider = _Provider()
    registry = TransformationProviderRegistry()
    registry.register_provider(provider=provider)
    freeze_test_provider_registry(registry)
    persistence = _Persistence()
    service = TransformationRunExecutionService(
        provider_registry=registry,
        persistence=persistence,
    )

    result = service.execute_attempt(request=_execution_request())

    assert result.outcome == TransformationRunAttemptExecutionOutcome.SUCCEEDED
    assert persistence.started is True
    assert persistence.succeeded is True
    assert provider.requests[0].bound_parameters == {"threshold": 0.5}
    with pytest.raises(TypeError):
        provider.requests[0].bound_parameters["changed"] = True  # type: ignore[index]


def test_capability_drift_blocks_provider_invocation() -> None:
    provider = _Provider(drift=True)
    registry = TransformationProviderRegistry()
    registry.register_provider(provider=provider)
    freeze_test_provider_registry(registry)
    persistence = _Persistence()

    result = TransformationRunExecutionService(
        provider_registry=registry,
        persistence=persistence,
    ).execute_attempt(request=_execution_request())

    assert result.outcome == TransformationRunAttemptExecutionOutcome.FAILED
    assert result.failure is not None
    assert result.failure.code == "capability_drift"
    assert provider.requests == []
    assert persistence.failure_code == "capability_drift"
