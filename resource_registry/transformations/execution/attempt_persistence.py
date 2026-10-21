from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Protocol, cast
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from resource_registry.models import (
    TransformationRun,
    TransformationRunStatus,
    TransformationRunStepAttempt,
    TransformationRunStepAttemptStatus,
)
from resource_registry.transformations.execution.authorization_validation import (
    validate_transformation_authorization_snapshot,
)
from resource_registry.transformations.execution.checksum import (
    CHECKPOINT_CONTRACT_VERSION,
    calculate_step_attempt_idempotency_token,
)
from resource_registry.transformations.execution.contracts import (
    TransformationRunAttemptExecutionOutcome,
    TransformationRunAttemptExecutionRequest,
    TransformationRunAttemptExecutionResult,
    TransformationRunAttemptInitializationResult,
    TransformationStepAttemptDescriptor,
)
from resource_registry.transformations.execution.exceptions import (
    InvalidPersistedExecutionPlanError,
    PersistedExecutionPlanIdentityMismatchError,
    TransformationRunNotEligibleError,
    TransformationRunNotFoundError,
    UnsupportedPersistedExecutionPlanError,
)
from resource_registry.transformations.execution.ports import (
    TransformationCheckpointReferenceVerifier,
)
from resource_registry.transformations.execution.runtime import (
    DefaultTransformationProviderRuntimeContext,
    DenyingTransformationRuntimeSecretAccess,
)
from resource_registry.transformations.execution_plan import (
    TransformationExecutionPlan,
    TransformationExecutionPlanDeserializationError,
    UnsupportedTransformationExecutionPlanVersionError,
    deserialize_transformation_execution_plan,
)
from resource_registry.transformations.providers import TransformationProviderRuntimeContext


class TransformationRunAttemptClaim(Protocol):
    """Expose the minimal operational claim values required for atomic integration."""

    @property
    def worker_id(self) -> str:
        """Return the operational worker identity."""
        ...

    @property
    def claimed_at(self) -> datetime:
        """Return the claim timestamp."""
        ...

    @property
    def lease_expires_at(self) -> datetime:
        """Return the initial lease expiration."""
        ...

    @property
    def attempt_deadline_at(self) -> datetime | None:
        """Return the optional persisted attempt deadline."""
        ...


class TransformationRunAttemptPersistenceService:
    """Initialize one explicit tenant-scoped run attempt without queue selection."""

    def initialize_attempt(  # noqa: PLR0913
        self,
        *,
        tenant_id: str,
        run_id: str,
        runtime_context: TransformationProviderRuntimeContext,
        checkpoint_reference_verifier: TransformationCheckpointReferenceVerifier,
        claim: TransformationRunAttemptClaim,
        correlation_id: str | None = None,
    ) -> TransformationRunAttemptInitializationResult:
        """Atomically initialize the exact queued run and its step-attempt rows.

        Args:
            tenant_id: Exact tenant UUID owning the run.
            run_id: Exact explicit run UUID; no queue discovery occurs.
            runtime_context: Runtime-only cooperative provider ports.
            checkpoint_reference_verifier: Reference availability verifier for safe reuse.
            claim: Story 6 operational ownership persisted with the attempt.
            correlation_id: Optional safe execution correlation override.

        Returns:
            An immutable initialized envelope or a pre-start cancellation result.

        Raises:
            TransformationRunNotFoundError: If the tenant-scoped run does not exist.
            TransformationRunNotEligibleError: If the run is not queued.
            InvalidPersistedExecutionPlanError: If the durable plan is invalid or mismatched.
        """
        parsed_tenant_id = _parse_uuid(tenant_id, field_name="tenant ID")
        parsed_run_id = _parse_uuid(run_id, field_name="run ID")
        safe_runtime_context = DefaultTransformationProviderRuntimeContext(
            cancellation_signal=runtime_context.cancellation,
            deadline_signal=runtime_context.deadline,
            secret_access=DenyingTransformationRuntimeSecretAccess(),
        )
        with transaction.atomic():
            run = self._lock_run(tenant_id=parsed_tenant_id, run_id=parsed_run_id)
            if run.status != TransformationRunStatus.QUEUED:
                raise TransformationRunNotEligibleError(
                    "The explicit transformation run is not queued.",
                )
            if run.cancellation_requested_at is not None:
                now = timezone.now()
                run.status = TransformationRunStatus.CANCELLED
                run.current_attempt_token = None
                run.completed_at = now
                run.execution_ms = 0
                run.save(
                    update_fields=[
                        "status",
                        "current_attempt_token",
                        "completed_at",
                        "execution_ms",
                        "updated_at",
                    ],
                )
                return TransformationRunAttemptInitializationResult(
                    is_initialized=False,
                    execution_request=None,
                    cancellation_result=TransformationRunAttemptExecutionResult(
                        run_id=str(run.id),
                        attempt_number=run.latest_attempt_number,
                        attempt_token="",
                        outcome=TransformationRunAttemptExecutionOutcome.CANCELLED,
                        completed_count=0,
                        failed_count=0,
                        cancelled_count=0,
                        reused_count=0,
                        final_reference_count=0,
                        retryable=False,
                        execution_ms=0,
                    ),
                )

            plan = self._deserialize_and_verify_plan(run=run)
            validate_transformation_authorization_snapshot(run=run, plan=plan)
            attempt_number = run.latest_attempt_number + 1
            attempt_token = uuid.uuid4()
            descriptors: list[TransformationStepAttemptDescriptor] = []
            step_attempts: list[TransformationRunStepAttempt] = []
            for step in plan.steps:
                step_attempt_id = uuid.uuid4()
                idempotency_token = calculate_step_attempt_idempotency_token(
                    run_id=str(run.id),
                    plan_checksum=plan.plan_checksum,
                    run_attempt_number=attempt_number,
                    run_attempt_token=str(attempt_token),
                    step_id=step.step_id,
                    step_index=step.step_index,
                    step_attempt_id=str(step_attempt_id),
                )
                step_attempts.append(
                    TransformationRunStepAttempt(
                        id=step_attempt_id,
                        run=run,
                        run_attempt_number=attempt_number,
                        run_attempt_token=attempt_token,
                        step_index=step.step_index,
                        step_id=step.step_id,
                        provider_key=step.provider_key,
                        provider_version=step.provider_version,
                        operation_key=step.operation_key,
                        operation_version=step.operation_version,
                        status=TransformationRunStepAttemptStatus.QUEUED,
                        checkpoint_contract_version=CHECKPOINT_CONTRACT_VERSION,
                        plan_checksum=plan.plan_checksum,
                        capability_checksum=step.capabilities.capability_checksum,
                        attempt_idempotency_token=idempotency_token,
                    ),
                )
                descriptors.append(
                    TransformationStepAttemptDescriptor(
                        step_attempt_id=str(step_attempt_id),
                        step_id=step.step_id,
                        step_index=step.step_index,
                        run_attempt_number=attempt_number,
                        run_attempt_token=str(attempt_token),
                        attempt_idempotency_token=idempotency_token,
                        status=TransformationRunStepAttemptStatus.QUEUED,
                    ),
                )
            TransformationRunStepAttempt.objects.bulk_create(step_attempts)
            run.status = TransformationRunStatus.RUNNING
            run.latest_attempt_number = attempt_number
            run.current_attempt_token = attempt_token
            run.claim_owner = claim.worker_id
            run.claimed_at = claim.claimed_at
            run.heartbeat_at = claim.claimed_at
            run.lease_expires_at = claim.lease_expires_at
            run.attempt_deadline_at = claim.attempt_deadline_at
            if run.started_at is None:
                run.started_at = timezone.now()
            run.save(
                update_fields=[
                    "status",
                    "latest_attempt_number",
                    "current_attempt_token",
                    "claim_owner",
                    "claimed_at",
                    "heartbeat_at",
                    "lease_expires_at",
                    "attempt_deadline_at",
                    "started_at",
                    "updated_at",
                ],
            )

        return TransformationRunAttemptInitializationResult(
            is_initialized=True,
            execution_request=TransformationRunAttemptExecutionRequest(
                tenant_id=str(parsed_tenant_id),
                run_id=str(parsed_run_id),
                attempt_number=attempt_number,
                attempt_token=str(attempt_token),
                plan=plan,
                step_attempts=tuple(descriptors),
                correlation_id=correlation_id or run.correlation_id,
                runtime_context=safe_runtime_context,
                checkpoint_reference_verifier=checkpoint_reference_verifier,
            ),
        )

    def _lock_run(self, *, tenant_id: UUID, run_id: UUID) -> TransformationRun:
        try:
            return TransformationRun.objects.select_for_update().get(
                tenant_id=tenant_id,
                id=run_id,
            )
        except TransformationRun.DoesNotExist as error:
            raise TransformationRunNotFoundError(
                "The explicit tenant-scoped transformation run was not found.",
            ) from error

    def _deserialize_and_verify_plan(
        self,
        *,
        run: TransformationRun,
    ) -> TransformationExecutionPlan:
        snapshot = run.execution_plan_snapshot_json
        if not isinstance(snapshot, Mapping):
            raise InvalidPersistedExecutionPlanError(
                "The persisted execution-plan snapshot is not an object.",
            )
        try:
            plan = deserialize_transformation_execution_plan(
                cast(Mapping[str, object], snapshot),
            )
        except UnsupportedTransformationExecutionPlanVersionError as error:
            raise UnsupportedPersistedExecutionPlanError(
                "The persisted execution-plan contract version is unsupported.",
            ) from error
        except TransformationExecutionPlanDeserializationError as error:
            raise InvalidPersistedExecutionPlanError(
                "The persisted execution-plan snapshot failed strict verification.",
            ) from error
        expected = (
            str(run.tenant_id),
            str(run.definition_id),
            str(run.definition_version_id),
            run.dsl_version,
            run.specification_checksum,
            run.plan_contract_version,
            run.execution_plan_checksum,
        )
        actual = (
            plan.tenant_id,
            plan.definition_id,
            plan.definition_version_id,
            plan.dsl_version,
            plan.specification_checksum,
            plan.contract_version,
            plan.plan_checksum,
        )
        if actual != expected:
            raise PersistedExecutionPlanIdentityMismatchError(
                "The persisted execution plan does not match its run identity.",
            )
        return plan


def _parse_uuid(value: str, *, field_name: str) -> UUID:
    try:
        return UUID(value)
    except (TypeError, ValueError, AttributeError) as error:
        raise TransformationRunNotFoundError(f"Invalid {field_name}.") from error
