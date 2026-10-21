from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from resource_registry.transformations.binding.immutable_json import freeze_json_value
from resource_registry.transformations.execution_plan import TransformationExecutionPlan
from resource_registry.transformations.providers import (
    ProviderSideEffectDisposition,
    TransformationProviderOutputReference,
    TransformationProviderRuntimeContext,
)


class TransformationStepExecutionOutcome(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    REUSED = "REUSED"
    STALE = "STALE"


class TransformationRunAttemptExecutionOutcome(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    STALE = "STALE"


class CheckpointReuseDecisionKind(StrEnum):
    REUSE = "REUSE"
    EXECUTE = "EXECUTE"
    BLOCKED = "BLOCKED"


class CheckpointReferenceAvailability(StrEnum):
    AVAILABLE = "AVAILABLE"
    MISSING = "MISSING"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class TransformationExecutionFailure:
    """Describe one sanitized deterministic execution failure."""

    code: str
    message: str
    retryable: bool
    side_effect_disposition: ProviderSideEffectDisposition
    step_id: str | None = None
    step_index: int | None = None
    provider_key: str | None = None
    provider_version: str | None = None
    operation_key: str | None = None
    operation_version: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Freeze safe failure metadata and require stable public fields."""
        if not self.code.strip() or not self.message.strip():
            raise ValueError("Execution failure code and message are required.")
        frozen = freeze_json_value(self.metadata)
        if not isinstance(frozen, Mapping):
            raise TypeError("Execution failure metadata must be an object.")
        object.__setattr__(self, "metadata", frozen)


@dataclass(frozen=True)
class TransformationStepAttemptDescriptor:
    """Expose one immutable persisted step-attempt identity to the core engine."""

    step_attempt_id: str
    step_id: str
    step_index: int
    run_attempt_number: int
    run_attempt_token: str
    attempt_idempotency_token: str
    status: str


@dataclass(frozen=True)
class TransformationRunAttemptExecutionRequest:
    """Contain everything required to execute one initialized explicit run attempt."""

    tenant_id: str
    run_id: str
    attempt_number: int
    attempt_token: str
    plan: TransformationExecutionPlan
    step_attempts: tuple[TransformationStepAttemptDescriptor, ...]
    correlation_id: str
    runtime_context: TransformationProviderRuntimeContext
    checkpoint_reference_verifier: object

    def __post_init__(self) -> None:
        """Freeze ordered descriptors and reject incomplete attempt identity."""
        if self.attempt_number < 1 or not self.attempt_token.strip():
            raise ValueError("Initialized run attempt identity is required.")
        object.__setattr__(self, "step_attempts", tuple(self.step_attempts))


@dataclass(frozen=True)
class TransformationRunAttemptInitializationResult:
    """Return either an initialized runtime envelope or cancellation outcome."""

    is_initialized: bool
    execution_request: TransformationRunAttemptExecutionRequest | None
    cancellation_result: TransformationRunAttemptExecutionResult | None = None


@dataclass(frozen=True)
class TransformationStepExecutionResult:
    """Report one internal step execution outcome."""

    step_id: str
    step_index: int
    step_attempt_id: str
    outcome: TransformationStepExecutionOutcome
    reused: bool
    output_count: int
    execution_ms: int
    outputs: tuple[TransformationProviderOutputReference, ...] = ()
    failure: TransformationExecutionFailure | None = None

    def __post_init__(self) -> None:
        """Freeze provider output references."""
        object.__setattr__(self, "outputs", tuple(self.outputs))


@dataclass(frozen=True)
class CheckpointReuseDecision:
    """Describe whether one current step should reuse, execute, or fail closed."""

    decision: CheckpointReuseDecisionKind
    reason_code: str
    source_step_attempt_id: str | None = None
    outputs: tuple[TransformationProviderOutputReference, ...] = ()
    side_effect_disposition: ProviderSideEffectDisposition = (
        ProviderSideEffectDisposition.NOT_STARTED
    )

    def __post_init__(self) -> None:
        """Freeze checkpoint outputs."""
        object.__setattr__(self, "outputs", tuple(self.outputs))


@dataclass(frozen=True)
class TransformationRunAttemptExecutionResult:
    """Report one attempt outcome without making retry or successful-finalization decisions."""

    run_id: str
    attempt_number: int
    attempt_token: str
    outcome: TransformationRunAttemptExecutionOutcome
    completed_count: int
    failed_count: int
    cancelled_count: int
    reused_count: int
    final_reference_count: int
    retryable: bool
    execution_ms: int
    failure: TransformationExecutionFailure | None = None


@dataclass(frozen=True)
class TransformationExecutionSummary:
    """Expose safe execution counts and exact attempt identity."""

    run_id: str
    attempt_number: int
    step_count: int
    completed_count: int
    reused_count: int
    final_reference_count: int
