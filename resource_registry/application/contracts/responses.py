from __future__ import annotations

from dataclasses import dataclass

from resource_registry.application.contracts.base import ApplicationContract
from resource_registry.application.contracts.enums import ExecutionState
from resource_registry.application.contracts.outputs import TransformationOutputReference
from resource_registry.application.contracts.runs import (
    TransformationExecutionSummary,
    TransformationRunReference,
    TransformationStepSummary,
)


@dataclass(frozen=True, kw_only=True)
class TransformationExecutionAccepted(ApplicationContract):
    """Confirm durable acceptance without implying synchronous execution."""

    run: TransformationRunReference
    execution_state: ExecutionState
    idempotent_replay: bool
    correlation_id: str


@dataclass(frozen=True, kw_only=True)
class TransformationExecutionResult(ApplicationContract):
    """Return one immutable transport-neutral transformation result snapshot."""

    run: TransformationRunReference
    summary: TransformationExecutionSummary
    steps: tuple[TransformationStepSummary, ...]
    outputs: tuple[TransformationOutputReference, ...]

    def __post_init__(self) -> None:
        """Normalize step and output ordering for deterministic equality and serialization."""
        object.__setattr__(
            self,
            "steps",
            tuple(sorted(self.steps, key=lambda step: (step.attempt_number, step.step_id))),
        )
        object.__setattr__(
            self,
            "outputs",
            tuple(sorted(self.outputs, key=lambda output: (output.logical_name, output.output_id))),
        )
