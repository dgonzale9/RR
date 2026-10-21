from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import timedelta

import pytest
from django.utils import timezone

from resource_registry.models import (
    TransformationDefinition,
    TransformationDefinitionVersion,
    TransformationDefinitionVersionStatus,
    TransformationRun,
    TransformationRunOutputStatus,
    TransformationRunStatus,
    TransformationRunStepAttempt,
)
from resource_registry.services.transformation_run_worker_contracts import (
    TransformationRunAttemptClaimMetadata,
)
from resource_registry.tests.test_transformation_run_execution_service import _Provider
from resource_registry.tests.transformation_run_test_helpers import (
    build_test_authorization_decision,
    build_test_execution_plan,
    build_test_output_policies,
)
from resource_registry.tests.transformation_runtime_test_helpers import (
    freeze_test_provider_registry,
)
from resource_registry.transformations.execution import TransformationRunAttemptExecutionOutcome
from resource_registry.transformations.execution.attempt_persistence import (
    TransformationRunAttemptPersistenceService,
)
from resource_registry.transformations.execution.checkpoint_persistence import (
    TransformationStepCheckpointService,
)
from resource_registry.transformations.execution.contracts import CheckpointReferenceAvailability
from resource_registry.transformations.execution.exceptions import StaleTransformationAttemptError
from resource_registry.transformations.execution.runtime import (
    DefaultTransformationProviderRuntimeContext,
    DenyingTransformationRuntimeSecretAccess,
    MonotonicTransformationDeadlineSignal,
)
from resource_registry.transformations.execution.service import TransformationRunExecutionService
from resource_registry.transformations.providers import (
    TransformationProviderExecutionRequest,
    TransformationProviderExecutionResult,
    TransformationProviderOutputReference,
    TransformationProviderRegistry,
    TransformationProviderRuntimeContext,
)
from resource_registry.transformations.runs import (
    TransformationRunCreationRequest,
    TransformationRunCreationService,
)

pytestmark = pytest.mark.django_db(transaction=True)
_SHA256_LENGTH = 64


class _CancellationSignal:
    def is_cancellation_requested(self) -> bool:
        return False


class _Verifier:
    def verify_references(
        self,
        *,
        references: tuple[TransformationProviderOutputReference, ...],
    ) -> CheckpointReferenceAvailability:
        _ = references
        return CheckpointReferenceAvailability.AVAILABLE


class _CancellingProvider(_Provider):
    def execute(
        self,
        *,
        request: TransformationProviderExecutionRequest,
        runtime_context: TransformationProviderRuntimeContext,
    ) -> TransformationProviderExecutionResult:
        result = super().execute(request=request, runtime_context=runtime_context)
        TransformationRun.objects.filter(id=request.run_id).update(
            cancellation_requested_at=timezone.now(),
        )
        return result


def _create_run(*, is_idempotent: bool = True) -> TransformationRun:
    definition = TransformationDefinition.objects.create(
        tenant_id=uuid.uuid4(),
        definition_key=f"definition-{uuid.uuid4()}",
        name="Transformation",
    )
    version = TransformationDefinitionVersion.objects.create(
        definition=definition,
        version_number=1,
        status=TransformationDefinitionVersionStatus.PUBLISHED,
        specification_json={"dsl_version": "1.0"},
        specification_checksum="a" * 64,
    )
    plan = build_test_execution_plan(
        tenant_id=definition.tenant_id,
        definition_id=definition.id,
        definition_version_id=version.id,
        specification_checksum="a" * 64,
        is_idempotent=is_idempotent,
    )
    result = TransformationRunCreationService().create_run(
        request=TransformationRunCreationRequest(
            tenant_id=definition.tenant_id,
            definition_id=definition.id,
            definition_version_id=version.id,
            execution_plan=plan,
            authorization_decision=build_test_authorization_decision(
                tenant_id=definition.tenant_id,
                definition_id=definition.id,
                definition_version_id=version.id,
                dataset_ids=tuple(input_item.dataset_id for input_item in plan.inputs),
            ),
            output_policies=build_test_output_policies(
                tuple(output.logical_name for output in plan.outputs),
            ),
        ),
    )
    return TransformationRun.objects.get(id=result.run_id)


def _runtime_context() -> DefaultTransformationProviderRuntimeContext:
    return DefaultTransformationProviderRuntimeContext(
        cancellation_signal=_CancellationSignal(),
        deadline_signal=MonotonicTransformationDeadlineSignal(),
        secret_access=DenyingTransformationRuntimeSecretAccess(),
    )


def _claim() -> TransformationRunAttemptClaimMetadata:
    claimed_at = timezone.now()
    return TransformationRunAttemptClaimMetadata(
        worker_id="story-five-test-worker",
        claimed_at=claimed_at,
        lease_expires_at=claimed_at + timedelta(minutes=5),
    )


def test_explicit_attempt_initialization_creates_exact_fenced_step_rows() -> None:
    run = _create_run()

    result = TransformationRunAttemptPersistenceService().initialize_attempt(
        tenant_id=str(run.tenant_id),
        run_id=str(run.id),
        runtime_context=_runtime_context(),
        checkpoint_reference_verifier=_Verifier(),
        claim=_claim(),
    )

    run.refresh_from_db()
    step = TransformationRunStepAttempt.objects.get(run=run)
    assert result.is_initialized is True
    assert result.execution_request is not None
    assert run.status == TransformationRunStatus.RUNNING
    assert run.latest_attempt_number == 1
    assert run.current_attempt_token is not None
    assert step.run_attempt_token == run.current_attempt_token
    assert step.plan_checksum == run.execution_plan_checksum
    assert (
        step.capability_checksum
        == result.execution_request.plan.steps[0].capabilities.capability_checksum
    )
    assert len(step.attempt_idempotency_token) == _SHA256_LENGTH
    assert step.checkpoint_input_checksum is None


def test_cancellation_requested_before_initialization_terminalizes_without_steps() -> None:
    run = _create_run()
    run.cancellation_requested_at = timezone.now()
    run.save(update_fields=["cancellation_requested_at", "updated_at"])

    result = TransformationRunAttemptPersistenceService().initialize_attempt(
        tenant_id=str(run.tenant_id),
        run_id=str(run.id),
        runtime_context=_runtime_context(),
        checkpoint_reference_verifier=_Verifier(),
        claim=_claim(),
    )

    run.refresh_from_db()
    assert result.is_initialized is False
    assert result.cancellation_result is not None
    assert result.cancellation_result.outcome == TransformationRunAttemptExecutionOutcome.CANCELLED
    assert run.status == TransformationRunStatus.CANCELLED
    assert run.current_attempt_token is None
    assert TransformationRunStepAttempt.objects.filter(run=run).count() == 0


def test_real_checkpoint_adapter_persists_success_and_links_pending_output_slots() -> None:
    run = _create_run()
    initialization = TransformationRunAttemptPersistenceService().initialize_attempt(
        tenant_id=str(run.tenant_id),
        run_id=str(run.id),
        runtime_context=_runtime_context(),
        checkpoint_reference_verifier=_Verifier(),
        claim=_claim(),
    )
    assert initialization.execution_request is not None
    provider = _Provider()
    registry = TransformationProviderRegistry()
    registry.register_provider(provider=provider)
    freeze_test_provider_registry(registry)

    result = TransformationRunExecutionService(
        provider_registry=registry,
        persistence=TransformationStepCheckpointService(),
    ).execute_attempt(request=initialization.execution_request)

    run.refresh_from_db()
    step = TransformationRunStepAttempt.objects.get(run=run)
    outputs = tuple(run.outputs.order_by("output_index"))
    assert result.outcome == TransformationRunAttemptExecutionOutcome.SUCCEEDED
    assert run.status == TransformationRunStatus.RUNNING
    assert run.current_attempt_token is not None
    assert step.status == "SUCCEEDED"
    assert step.checkpoint_input_checksum is not None
    assert step.checkpoint_output_checksum is not None
    assert step.checkpoint_checksum is not None
    assert all(output.status == TransformationRunOutputStatus.PENDING for output in outputs)
    assert all(output.producing_step_attempt_id == step.id for output in outputs)
    assert all(output.canonical_uri is None for output in outputs)


def test_second_attempt_reuses_only_verified_reference_checkpoint() -> None:
    run = _create_run()
    initializer = TransformationRunAttemptPersistenceService()
    first = initializer.initialize_attempt(
        tenant_id=str(run.tenant_id),
        run_id=str(run.id),
        runtime_context=_runtime_context(),
        checkpoint_reference_verifier=_Verifier(),
        claim=_claim(),
    )
    assert first.execution_request is not None
    registry = TransformationProviderRegistry()
    first_provider = _Provider()
    registry.register_provider(provider=first_provider)
    freeze_test_provider_registry(registry)
    engine = TransformationRunExecutionService(
        provider_registry=registry,
        persistence=TransformationStepCheckpointService(),
    )
    first_result = engine.execute_attempt(request=first.execution_request)
    assert first_result.outcome == TransformationRunAttemptExecutionOutcome.SUCCEEDED
    TransformationRun.objects.filter(id=run.id).update(
        status=TransformationRunStatus.QUEUED,
        current_attempt_token=None,
        claim_owner=None,
        claimed_at=None,
        heartbeat_at=None,
        lease_expires_at=None,
        attempt_deadline_at=None,
    )

    second = initializer.initialize_attempt(
        tenant_id=str(run.tenant_id),
        run_id=str(run.id),
        runtime_context=_runtime_context(),
        checkpoint_reference_verifier=_Verifier(),
        claim=_claim(),
    )
    assert second.execution_request is not None
    second_result = engine.execute_attempt(request=second.execution_request)

    attempts = tuple(
        TransformationRunStepAttempt.objects.filter(run=run).order_by("run_attempt_number"),
    )
    assert second_result.outcome == TransformationRunAttemptExecutionOutcome.SUCCEEDED
    assert second_result.reused_count == 1
    assert len(first_provider.requests) == 1
    assert attempts[1].reused_from_step_attempt_id == attempts[0].id
    assert attempts[1].checkpoint_checksum != attempts[0].checkpoint_checksum


def test_wrong_attempt_token_cannot_mutate_step_or_current_fence() -> None:
    run = _create_run()
    initialization = TransformationRunAttemptPersistenceService().initialize_attempt(
        tenant_id=str(run.tenant_id),
        run_id=str(run.id),
        runtime_context=_runtime_context(),
        checkpoint_reference_verifier=_Verifier(),
        claim=_claim(),
    )
    assert initialization.execution_request is not None
    stale_request = replace(
        initialization.execution_request,
        attempt_token=str(uuid.uuid4()),
    )

    with pytest.raises(StaleTransformationAttemptError):
        TransformationStepCheckpointService().start_step(
            request=stale_request,
            step_attempt=stale_request.step_attempts[0],
            input_snapshot=(),
            input_checksum="d" * _SHA256_LENGTH,
        )

    run.refresh_from_db()
    step = TransformationRunStepAttempt.objects.get(run=run)
    assert str(run.current_attempt_token) == initialization.execution_request.attempt_token
    assert step.status == "QUEUED"
    assert step.checkpoint_input_checksum is None


def test_cancellation_after_applied_provider_effects_fails_step_and_terminalizes_run() -> None:
    run = _create_run()
    initialization = TransformationRunAttemptPersistenceService().initialize_attempt(
        tenant_id=str(run.tenant_id),
        run_id=str(run.id),
        runtime_context=_runtime_context(),
        checkpoint_reference_verifier=_Verifier(),
        claim=_claim(),
    )
    assert initialization.execution_request is not None
    provider = _CancellingProvider()
    registry = TransformationProviderRegistry()
    registry.register_provider(provider=provider)
    freeze_test_provider_registry(registry)

    result = TransformationRunExecutionService(
        provider_registry=registry,
        persistence=TransformationStepCheckpointService(),
    ).execute_attempt(request=initialization.execution_request)

    run.refresh_from_db()
    step = TransformationRunStepAttempt.objects.get(run=run)
    assert result.outcome == TransformationRunAttemptExecutionOutcome.CANCELLED
    assert run.status == TransformationRunStatus.CANCELLED
    assert run.current_attempt_token is None
    assert step.status == "FAILED"
    assert step.side_effect_disposition == "APPLIED"
    assert step.checkpoint_checksum is None
    assert step.checkpoint_output_checksum is None
    assert step.checkpoint_outputs_json
