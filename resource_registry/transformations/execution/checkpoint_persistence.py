from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from resource_registry.models import (
    TransformationRun,
    TransformationRunOutput,
    TransformationRunStatus,
    TransformationRunStepAttempt,
    TransformationRunStepAttemptStatus,
)
from resource_registry.transformations.binding.immutable_json import thaw_json_value
from resource_registry.transformations.execution.checksum import (
    calculate_complete_checkpoint_checksum,
)
from resource_registry.transformations.execution.contracts import (
    CheckpointReferenceAvailability,
    CheckpointReuseDecision,
    CheckpointReuseDecisionKind,
    TransformationExecutionFailure,
    TransformationRunAttemptExecutionRequest,
    TransformationStepAttemptDescriptor,
)
from resource_registry.transformations.execution.exceptions import (
    StaleTransformationAttemptError,
    TransformationCancellationPrecedenceError,
)
from resource_registry.transformations.execution.ports import (
    TransformationCheckpointReferenceVerifier,
)
from resource_registry.transformations.execution.references import (
    InvalidTransformationReferenceError,
    deserialize_output_references,
)
from resource_registry.transformations.execution_plan import TransformationExecutionPlanStep
from resource_registry.transformations.providers import (
    ProviderSideEffectDisposition,
    TransformationProviderInputReference,
    TransformationProviderOutputReference,
)


class TransformationStepCheckpointService:
    """Persist short, fenced Story 5 step transitions and reference checkpoints."""

    def is_cancellation_requested(
        self,
        *,
        request: TransformationRunAttemptExecutionRequest,
    ) -> bool:
        """Return current durable cancellation intent for the fenced run."""
        return TransformationRun.objects.filter(
            tenant_id=request.tenant_id,
            id=request.run_id,
            status=TransformationRunStatus.RUNNING,
            latest_attempt_number=request.attempt_number,
            current_attempt_token=request.attempt_token,
            cancellation_requested_at__isnull=False,
        ).exists()

    def decide_checkpoint_reuse(  # noqa: PLR0911, PLR0913
        self,
        *,
        request: TransformationRunAttemptExecutionRequest,
        step: TransformationExecutionPlanStep,
        step_attempt: TransformationStepAttemptDescriptor,
        inputs: tuple[TransformationProviderInputReference, ...],
        input_checksum: str,
        verifier: TransformationCheckpointReferenceVerifier,
    ) -> CheckpointReuseDecision:
        """Validate the newest prior checkpoint and verify references outside transactions."""
        _ = inputs
        prior_attempt = (
            TransformationRunStepAttempt.objects.filter(
                run_id=UUID(request.run_id),
                step_id=step.step_id,
                step_index=step.step_index,
                run_attempt_number__lt=request.attempt_number,
            )
            .order_by("-run_attempt_number")
            .first()
        )
        if prior_attempt is None:
            return CheckpointReuseDecision(CheckpointReuseDecisionKind.EXECUTE, "no_checkpoint")
        if (
            prior_attempt.status != TransformationRunStepAttemptStatus.SUCCEEDED
            and not step.capabilities.is_idempotent
            and prior_attempt.side_effect_disposition
            in (
                ProviderSideEffectDisposition.APPLIED,
                ProviderSideEffectDisposition.UNKNOWN,
            )
        ):
            return CheckpointReuseDecision(
                CheckpointReuseDecisionKind.BLOCKED,
                "ambiguous_non_idempotent_attempt",
            )
        prior = (
            TransformationRunStepAttempt.objects.filter(
                run_id=UUID(request.run_id),
                step_id=step.step_id,
                step_index=step.step_index,
                run_attempt_number__lt=request.attempt_number,
                status=TransformationRunStepAttemptStatus.SUCCEEDED,
            )
            .order_by("-run_attempt_number")
            .first()
        )
        if prior is None:
            return CheckpointReuseDecision(
                CheckpointReuseDecisionKind.EXECUTE,
                "no_successful_checkpoint",
            )
        if not step.capabilities.is_checkpoint_reuse_safe:
            return self._unavailable_reuse_decision(step=step, reason="reuse_not_supported")
        if prior.side_effect_disposition == ProviderSideEffectDisposition.UNKNOWN:
            return self._unavailable_reuse_decision(step=step, reason="ambiguous_checkpoint")
        if not self._has_matching_checkpoint_semantics(
            source=prior,
            request=request,
            step=step,
            input_checksum=input_checksum,
        ):
            return self._unavailable_reuse_decision(
                step=step,
                reason="checkpoint_integrity_failure",
            )
        try:
            outputs = deserialize_output_references(prior.checkpoint_outputs_json)
        except InvalidTransformationReferenceError:
            return self._unavailable_reuse_decision(
                step=step,
                reason="checkpoint_integrity_failure",
            )
        if not outputs:
            return self._unavailable_reuse_decision(
                step=step,
                reason="checkpoint_integrity_failure",
            )
        try:
            availability = verifier.verify_references(references=outputs)
        except Exception:  # Reference verification is an external fail-closed boundary.
            availability = CheckpointReferenceAvailability.UNKNOWN
        if availability != CheckpointReferenceAvailability.AVAILABLE:
            return self._unavailable_reuse_decision(
                step=step,
                reason="checkpoint_reference_unavailable",
            )
        return CheckpointReuseDecision(
            decision=CheckpointReuseDecisionKind.REUSE,
            reason_code="checkpoint_available",
            source_step_attempt_id=str(prior.id),
            outputs=outputs,
            side_effect_disposition=ProviderSideEffectDisposition(
                prior.side_effect_disposition,
            ),
        )

    def start_step(
        self,
        *,
        request: TransformationRunAttemptExecutionRequest,
        step_attempt: TransformationStepAttemptDescriptor,
        input_snapshot: tuple[dict[str, object], ...],
        input_checksum: str,
    ) -> None:
        """Persist inputs and invocation fencing immediately before provider execution."""
        with transaction.atomic():
            self._lock_fenced_run(request=request, allow_cancellation=False)
            row = self._lock_step(
                request=request,
                descriptor=step_attempt,
                expected_statuses=(TransformationRunStepAttemptStatus.QUEUED,),
            )
            row.status = TransformationRunStepAttemptStatus.RUNNING
            row.started_at = timezone.now()
            row.checkpoint_inputs_json = list(input_snapshot)
            row.checkpoint_input_checksum = input_checksum
            row.invocation_may_have_started = True
            row.side_effect_disposition = ProviderSideEffectDisposition.NOT_STARTED
            row.save(
                update_fields=[
                    "status",
                    "started_at",
                    "checkpoint_inputs_json",
                    "checkpoint_input_checksum",
                    "invocation_may_have_started",
                    "side_effect_disposition",
                ],
            )

    def persist_reused_step(  # noqa: PLR0913
        self,
        *,
        request: TransformationRunAttemptExecutionRequest,
        step: TransformationExecutionPlanStep,
        step_attempt: TransformationStepAttemptDescriptor,
        decision: CheckpointReuseDecision,
        input_snapshot: tuple[dict[str, object], ...],
        input_checksum: str,
        execution_ms: int,
    ) -> None:
        """Persist a new source-attempt-bound checkpoint from verified references."""
        if decision.source_step_attempt_id is None:
            raise ValueError("A reusable checkpoint source is required.")
        output_snapshot = tuple(_output_snapshot(reference) for reference in decision.outputs)
        from resource_registry.transformations.execution.checksum import (
            calculate_reference_snapshot_checksum,
        )

        output_checksum = calculate_reference_snapshot_checksum(
            snapshot_kind="outputs",
            references=output_snapshot,
        )
        with transaction.atomic():
            self._lock_fenced_run(request=request, allow_cancellation=False)
            row = self._lock_step(
                request=request,
                descriptor=step_attempt,
                expected_statuses=(TransformationRunStepAttemptStatus.QUEUED,),
            )
            row.checkpoint_inputs_json = list(input_snapshot)
            row.checkpoint_input_checksum = input_checksum
            self._apply_success_checkpoint(
                row=row,
                request=request,
                step=step,
                output_snapshot=output_snapshot,
                output_checksum=output_checksum,
                disposition=decision.side_effect_disposition,
                execution_ms=execution_ms,
            )
            row.reused_from_step_attempt_id = decision.source_step_attempt_id
            row.save()
            self._link_final_output_slots(request=request, step=step, step_attempt=row)

    def persist_step_success(  # noqa: PLR0913
        self,
        *,
        request: TransformationRunAttemptExecutionRequest,
        step: TransformationExecutionPlanStep,
        step_attempt: TransformationStepAttemptDescriptor,
        outputs: tuple[TransformationProviderOutputReference, ...],
        output_snapshot: tuple[dict[str, object], ...],
        output_checksum: str,
        side_effect_disposition: ProviderSideEffectDisposition,
        execution_ms: int,
    ) -> None:
        """Persist one validated successful checkpoint and producer links."""
        _ = outputs
        with transaction.atomic():
            self._lock_fenced_run(request=request, allow_cancellation=False)
            row = self._lock_step(
                request=request,
                descriptor=step_attempt,
                expected_statuses=(TransformationRunStepAttemptStatus.RUNNING,),
            )
            self._apply_success_checkpoint(
                row=row,
                request=request,
                step=step,
                output_snapshot=output_snapshot,
                output_checksum=output_checksum,
                disposition=side_effect_disposition,
                execution_ms=execution_ms,
            )
            row.save()
            self._link_final_output_slots(request=request, step=step, step_attempt=row)

    def persist_step_failure(
        self,
        *,
        request: TransformationRunAttemptExecutionRequest,
        step_attempt: TransformationStepAttemptDescriptor,
        failure: TransformationExecutionFailure,
        execution_ms: int,
        diagnostic_outputs: tuple[dict[str, object], ...] = (),
    ) -> None:
        """Persist one sanitized failure and cancel remaining queued steps."""
        with transaction.atomic():
            self._lock_fenced_run(request=request, allow_cancellation=False)
            row = self._lock_step(
                request=request,
                descriptor=step_attempt,
                expected_statuses=(
                    TransformationRunStepAttemptStatus.QUEUED,
                    TransformationRunStepAttemptStatus.RUNNING,
                ),
            )
            self._apply_failure(
                row=row,
                failure=failure,
                execution_ms=execution_ms,
                diagnostic_outputs=diagnostic_outputs,
            )
            self._cancel_remaining_steps(request=request, exclude_id=row.id)

    def persist_cancellation(
        self,
        *,
        request: TransformationRunAttemptExecutionRequest,
        current_step_attempt: TransformationStepAttemptDescriptor | None,
        failure: TransformationExecutionFailure,
        execution_ms: int,
        diagnostic_outputs: tuple[dict[str, object], ...] = (),
    ) -> None:
        """Persist cancellation precedence without clearing a newer attempt token."""
        with transaction.atomic():
            run = self._lock_fenced_run(request=request, allow_cancellation=True)
            current_id = None
            if current_step_attempt is not None:
                row = self._lock_step(
                    request=request,
                    descriptor=current_step_attempt,
                    expected_statuses=(
                        TransformationRunStepAttemptStatus.QUEUED,
                        TransformationRunStepAttemptStatus.RUNNING,
                    ),
                )
                current_id = row.id
                if failure.side_effect_disposition in (
                    ProviderSideEffectDisposition.APPLIED,
                    ProviderSideEffectDisposition.UNKNOWN,
                ):
                    self._apply_failure(
                        row=row,
                        failure=failure,
                        execution_ms=execution_ms,
                        diagnostic_outputs=diagnostic_outputs,
                    )
                else:
                    row.status = TransformationRunStepAttemptStatus.CANCELLED
                    row.side_effect_disposition = ProviderSideEffectDisposition.NOT_STARTED
                    row.error_code = failure.code
                    row.error_message = failure.message
                    row.is_retryable = False
                    row.completed_at = timezone.now()
                    row.execution_ms = execution_ms
                    row.save()
            self._cancel_remaining_steps(request=request, exclude_id=current_id)
            run.status = TransformationRunStatus.CANCELLED
            run.current_attempt_token = None
            run.claim_owner = None
            run.claimed_at = None
            run.heartbeat_at = None
            run.lease_expires_at = None
            run.attempt_deadline_at = None
            run.completed_at = timezone.now()
            run.execution_ms = execution_ms
            run.error_code = failure.code
            run.error_message = failure.message
            run.error_metadata_json = thaw_json_value(failure.metadata)
            run.save(
                update_fields=[
                    "status",
                    "current_attempt_token",
                    "claim_owner",
                    "claimed_at",
                    "heartbeat_at",
                    "lease_expires_at",
                    "attempt_deadline_at",
                    "completed_at",
                    "execution_ms",
                    "error_code",
                    "error_message",
                    "error_metadata_json",
                    "updated_at",
                ],
            )

    def _lock_fenced_run(
        self,
        *,
        request: TransformationRunAttemptExecutionRequest,
        allow_cancellation: bool,
    ) -> TransformationRun:
        try:
            run = TransformationRun.objects.select_for_update().get(
                tenant_id=request.tenant_id,
                id=request.run_id,
                status=TransformationRunStatus.RUNNING,
                latest_attempt_number=request.attempt_number,
                current_attempt_token=request.attempt_token,
            )
        except TransformationRun.DoesNotExist as error:
            raise StaleTransformationAttemptError("The run attempt fence is stale.") from error
        if not allow_cancellation and run.cancellation_requested_at is not None:
            raise TransformationCancellationPrecedenceError(
                "Cancellation took precedence over checkpoint persistence.",
            )
        return run

    def _lock_step(
        self,
        *,
        request: TransformationRunAttemptExecutionRequest,
        descriptor: TransformationStepAttemptDescriptor,
        expected_statuses: Sequence[str],
    ) -> TransformationRunStepAttempt:
        try:
            return TransformationRunStepAttempt.objects.select_for_update().get(
                id=descriptor.step_attempt_id,
                run_id=UUID(request.run_id),
                run_attempt_number=request.attempt_number,
                run_attempt_token=request.attempt_token,
                step_id=descriptor.step_id,
                step_index=descriptor.step_index,
                status__in=expected_statuses,
            )
        except TransformationRunStepAttempt.DoesNotExist as error:
            raise StaleTransformationAttemptError("The step-attempt fence is stale.") from error

    def _has_matching_checkpoint_semantics(
        self,
        *,
        source: TransformationRunStepAttempt,
        request: TransformationRunAttemptExecutionRequest,
        step: TransformationExecutionPlanStep,
        input_checksum: str,
    ) -> bool:
        if (
            source.plan_checksum != request.plan.plan_checksum
            or source.provider_key != step.provider_key
            or source.provider_version != step.provider_version
            or source.operation_key != step.operation_key
            or source.operation_version != step.operation_version
            or source.capability_checksum != step.capabilities.capability_checksum
            or source.checkpoint_input_checksum != input_checksum
            or source.checkpoint_output_checksum is None
            or source.checkpoint_checksum is None
        ):
            return False
        expected = calculate_complete_checkpoint_checksum(
            run_id=str(source.run_id),
            run_attempt_number=source.run_attempt_number,
            run_attempt_token=str(source.run_attempt_token),
            plan_checksum=source.plan_checksum,
            step_id=source.step_id,
            step_index=source.step_index,
            provider_key=source.provider_key,
            provider_version=source.provider_version,
            operation_key=source.operation_key,
            operation_version=source.operation_version,
            capability_checksum=source.capability_checksum,
            input_snapshot=cast(list[Mapping[str, object]], source.checkpoint_inputs_json),
            input_checksum=source.checkpoint_input_checksum,
            output_snapshot=cast(list[Mapping[str, object]], source.checkpoint_outputs_json),
            output_checksum=source.checkpoint_output_checksum,
            attempt_idempotency_token=source.attempt_idempotency_token,
            side_effect_disposition=ProviderSideEffectDisposition(
                source.side_effect_disposition,
            ),
        )
        return expected == source.checkpoint_checksum

    def _unavailable_reuse_decision(
        self,
        *,
        step: TransformationExecutionPlanStep,
        reason: str,
    ) -> CheckpointReuseDecision:
        decision = (
            CheckpointReuseDecisionKind.EXECUTE
            if step.capabilities.is_idempotent
            else CheckpointReuseDecisionKind.BLOCKED
        )
        return CheckpointReuseDecision(decision=decision, reason_code=reason)

    def _apply_success_checkpoint(  # noqa: PLR0913
        self,
        *,
        row: TransformationRunStepAttempt,
        request: TransformationRunAttemptExecutionRequest,
        step: TransformationExecutionPlanStep,
        output_snapshot: tuple[dict[str, object], ...],
        output_checksum: str,
        disposition: ProviderSideEffectDisposition,
        execution_ms: int,
    ) -> None:
        if row.checkpoint_input_checksum is None:
            raise StaleTransformationAttemptError("The successful step has no input checkpoint.")
        row.checkpoint_outputs_json = list(output_snapshot)
        row.checkpoint_output_checksum = output_checksum
        row.side_effect_disposition = disposition
        row.checkpoint_checksum = calculate_complete_checkpoint_checksum(
            run_id=request.run_id,
            run_attempt_number=request.attempt_number,
            run_attempt_token=request.attempt_token,
            plan_checksum=request.plan.plan_checksum,
            step_id=step.step_id,
            step_index=step.step_index,
            provider_key=step.provider_key,
            provider_version=step.provider_version,
            operation_key=step.operation_key,
            operation_version=step.operation_version,
            capability_checksum=step.capabilities.capability_checksum,
            input_snapshot=cast(list[Mapping[str, object]], row.checkpoint_inputs_json),
            input_checksum=row.checkpoint_input_checksum,
            output_snapshot=output_snapshot,
            output_checksum=output_checksum,
            attempt_idempotency_token=row.attempt_idempotency_token,
            side_effect_disposition=disposition,
        )
        row.status = TransformationRunStepAttemptStatus.SUCCEEDED
        row.completed_at = timezone.now()
        row.execution_ms = execution_ms
        row.is_retryable = None
        row.error_code = None
        row.error_classification = None
        row.error_message = None

    def _apply_failure(
        self,
        *,
        row: TransformationRunStepAttempt,
        failure: TransformationExecutionFailure,
        execution_ms: int,
        diagnostic_outputs: tuple[dict[str, object], ...],
    ) -> None:
        row.status = TransformationRunStepAttemptStatus.FAILED
        row.checkpoint_outputs_json = list(diagnostic_outputs)
        row.checkpoint_output_checksum = None
        row.checkpoint_checksum = None
        row.side_effect_disposition = failure.side_effect_disposition
        row.is_retryable = failure.retryable
        row.error_code = failure.code
        row.error_classification = failure.code
        row.error_message = failure.message
        row.completed_at = timezone.now()
        row.execution_ms = execution_ms
        row.save()

    def _cancel_remaining_steps(
        self,
        *,
        request: TransformationRunAttemptExecutionRequest,
        exclude_id: UUID | str | None,
    ) -> None:
        queryset = TransformationRunStepAttempt.objects.filter(
            run_id=UUID(request.run_id),
            run_attempt_number=request.attempt_number,
            run_attempt_token=request.attempt_token,
            status=TransformationRunStepAttemptStatus.QUEUED,
        )
        if exclude_id is not None:
            queryset = queryset.exclude(id=exclude_id)
        queryset.update(
            status=TransformationRunStepAttemptStatus.CANCELLED,
            completed_at=timezone.now(),
            side_effect_disposition=ProviderSideEffectDisposition.NOT_STARTED,
        )

    def _link_final_output_slots(
        self,
        *,
        request: TransformationRunAttemptExecutionRequest,
        step: TransformationExecutionPlanStep,
        step_attempt: TransformationRunStepAttempt,
    ) -> None:
        plan_outputs = tuple(
            output for output in request.plan.outputs if output.producer_step_id == step.step_id
        )
        if not plan_outputs:
            return
        rows = tuple(
            TransformationRunOutput.objects.select_for_update()
            .filter(run_id=UUID(request.run_id), producer_step_id=step.step_id)
            .order_by("output_index"),
        )
        if len(rows) != len(plan_outputs):
            raise StaleTransformationAttemptError("Final output slots do not match the plan.")
        for row, output in zip(rows, plan_outputs, strict=True):
            if (
                row.output_index != request.plan.outputs.index(output)
                or row.logical_name != output.logical_name
                or row.source_symbol != output.source_symbol
                or row.producer_step_id != output.producer_step_id
                or row.producer_step_index != output.producer_step_index
                or row.producer_output_index != output.producer_output_index
                or row.data_format != output.data_format
            ):
                raise StaleTransformationAttemptError("Final output slot identity is invalid.")
            row.producing_step_attempt = step_attempt
            row.save(update_fields=["producing_step_attempt", "updated_at"])


def _output_snapshot(reference: TransformationProviderOutputReference) -> dict[str, object]:
    from resource_registry.transformations.execution.references import serialize_output_reference

    return serialize_output_reference(reference)
