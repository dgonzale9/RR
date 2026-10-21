from __future__ import annotations

from typing import Protocol

from resource_registry.transformations.execution.contracts import (
    CheckpointReferenceAvailability,
    CheckpointReuseDecision,
    TransformationExecutionFailure,
    TransformationRunAttemptExecutionRequest,
    TransformationStepAttemptDescriptor,
)
from resource_registry.transformations.execution_plan import TransformationExecutionPlanStep
from resource_registry.transformations.providers import (
    ProviderSideEffectDisposition,
    TransformationProviderInputReference,
    TransformationProviderOutputReference,
)


class TransformationCheckpointReferenceVerifier(Protocol):
    """Verify checkpoint reference availability without exposing storage implementations."""

    def verify_references(
        self,
        *,
        references: tuple[TransformationProviderOutputReference, ...],
    ) -> CheckpointReferenceAvailability:
        """Return conservative availability for all supplied references."""


class TransformationExecutionPersistencePort(Protocol):
    """Persist fenced step and cancellation changes for the pure execution engine."""

    def is_cancellation_requested(
        self,
        *,
        request: TransformationRunAttemptExecutionRequest,
    ) -> bool:
        """Return current durable cancellation intent."""

    def decide_checkpoint_reuse(  # noqa: PLR0913
        self,
        *,
        request: TransformationRunAttemptExecutionRequest,
        step: TransformationExecutionPlanStep,
        step_attempt: TransformationStepAttemptDescriptor,
        inputs: tuple[TransformationProviderInputReference, ...],
        input_checksum: str,
        verifier: TransformationCheckpointReferenceVerifier,
    ) -> CheckpointReuseDecision:
        """Return a verified restart-safe reuse decision."""

    def start_step(
        self,
        *,
        request: TransformationRunAttemptExecutionRequest,
        step_attempt: TransformationStepAttemptDescriptor,
        input_snapshot: tuple[dict[str, object], ...],
        input_checksum: str,
    ) -> None:
        """Persist fenced step start immediately before provider invocation."""

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
        """Persist a new current-attempt checkpoint derived from a verified source."""

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
        """Persist one fenced successful checkpoint and link final output slots."""

    def persist_step_failure(
        self,
        *,
        request: TransformationRunAttemptExecutionRequest,
        step_attempt: TransformationStepAttemptDescriptor,
        failure: TransformationExecutionFailure,
        execution_ms: int,
        diagnostic_outputs: tuple[dict[str, object], ...] = (),
    ) -> None:
        """Persist one sanitized failure and cancel later queued steps."""

    def persist_cancellation(
        self,
        *,
        request: TransformationRunAttemptExecutionRequest,
        current_step_attempt: TransformationStepAttemptDescriptor | None,
        failure: TransformationExecutionFailure,
        execution_ms: int,
        diagnostic_outputs: tuple[dict[str, object], ...] = (),
    ) -> None:
        """Persist cancellation precedence and clear the current attempt token."""
