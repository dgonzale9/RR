from __future__ import annotations

from dataclasses import dataclass

from resource_registry.application.contracts.base import (
    ApplicationContract,
    validate_duration_seconds,
    validate_progress,
)
from resource_registry.application.contracts.enums import ExecutionState, RunStatus, StepStatus


@dataclass(frozen=True, kw_only=True)
class TransformationRunReference(ApplicationContract):
    """Identify one tenant-scoped durable transformation run."""

    tenant_id: str
    run_id: str
    definition_id: str
    definition_version_id: str
    status: RunStatus


@dataclass(frozen=True, kw_only=True)
class TransformationStepSummary(ApplicationContract):
    """Summarize one public execution attempt without checkpoint or provider details."""

    step_id: str
    attempt_number: int
    status: StepStatus
    duration_seconds: float | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        """Validate transport-safe duration and attempt fields."""
        if self.attempt_number <= 0:
            raise ValueError("Application step attempt number must be positive.")
        validate_duration_seconds(self.duration_seconds)


@dataclass(frozen=True, kw_only=True)
class TransformationExecutionSummary(ApplicationContract):
    """Summarize public progress and duration for one asynchronous execution."""

    run_id: str
    status: RunStatus
    execution_state: ExecutionState
    progress: float
    completed_steps: int
    total_steps: int
    duration_seconds: float | None = None
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        """Validate bounded progress, counts, and duration."""
        validate_progress(self.progress)
        validate_duration_seconds(self.duration_seconds)
        if self.completed_steps < 0 or self.total_steps < 0:
            raise ValueError("Application step counts cannot be negative.")
        if self.completed_steps > self.total_steps:
            raise ValueError("Completed application steps cannot exceed total steps.")
