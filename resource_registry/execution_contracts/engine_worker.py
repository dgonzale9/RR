"""Define provider-neutral Engine and Worker interface contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from resource_registry.authorization.contracts import RegistryAuthorizationContext

from .contracts import (
    ExecutionCheckpoint,
    ExecutionContext,
    ExecutionPlan,
    ExecutionResult,
    ExecutionStep,
)
from .errors import ExecutionRequestValidationError
from .lifecycle import ExecutionAttemptFence, ExecutionLifecycleSnapshot, ExecutionLifecycleState

__all__ = (
    "ExecutionEngine",
    "ExecutionEngineCommand",
    "ExecutionEngineOutcome",
    "ExecutionEngineOutcomeKind",
    "ExecutionWorker",
    "ExecutionWorkerAssignmentDecision",
    "ExecutionWorkerAssignmentStatus",
    "evaluate_execution_worker_assignment",
)


class ExecutionEngineOutcomeKind(StrEnum):
    """Classify an Engine outcome before Coordinator lifecycle policy applies."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RETRYABLE_FAILURE = "retryable_failure"
    AMBIGUOUS_FAILURE = "ambiguous_failure"


@dataclass(frozen=True, slots=True)
class ExecutionEngineCommand:
    """Carry one exact already-routed, fenced Engine step request.

    Args:
        snapshot: Running Coordinator snapshot carrying instance, namespace, and active fence.
        plan: Immutable exact plan governing the step.
        context: Immutable exact context evidence governing the plan.
        step: Exact plan step to execute.
        fence: Exact active attempt fence supplied by a Coordinator/Worker boundary.

    Raises:
        ExecutionRequestValidationError: If the command crosses an Execution contract boundary.
    """

    snapshot: ExecutionLifecycleSnapshot
    plan: ExecutionPlan
    context: ExecutionContext
    step: ExecutionStep
    fence: ExecutionAttemptFence

    def __post_init__(self) -> None:
        """Validate an Engine command without selecting or invoking a provider.

        Raises:
            ExecutionRequestValidationError: If identity, plan, context, step, or fence is invalid.
        """
        if not isinstance(self.snapshot, ExecutionLifecycleSnapshot):
            raise ExecutionRequestValidationError("invalid execution engine snapshot")
        if not isinstance(self.plan, ExecutionPlan) or not isinstance(
            self.context, ExecutionContext
        ):
            raise ExecutionRequestValidationError("invalid execution engine plan context")
        if not isinstance(self.step, ExecutionStep):
            raise ExecutionRequestValidationError("invalid execution engine step")
        if not isinstance(self.fence, ExecutionAttemptFence):
            raise ExecutionRequestValidationError("invalid execution engine fence")
        if self.snapshot.state is not ExecutionLifecycleState.RUNNING:
            raise ExecutionRequestValidationError("execution engine requires running lifecycle")
        if self.snapshot.active_fence != self.fence:
            raise ExecutionRequestValidationError("execution engine fence does not match")
        if (
            self.plan.plan_ref != self.context.plan_ref
            or self.plan.request_ref != self.context.request_ref
        ):
            raise ExecutionRequestValidationError("execution engine plan context does not match")
        if self.step not in self.plan.steps:
            raise ExecutionRequestValidationError("execution engine step is not in plan")
        if self.step.contract_ref != self.context.contract_ref:
            raise ExecutionRequestValidationError("execution engine step contract does not match")


@dataclass(frozen=True, slots=True)
class ExecutionEngineOutcome:
    """Carry one immutable normalized Engine outcome without lifecycle mutation.

    Args:
        command: Exact Engine command that produced the outcome.
        kind: Normalized outcome classification for Coordinator policy.
        result: Optional normalized result evidence for a terminal outcome.
        checkpoint: Optional immutable checkpoint proposal for a completed step.
    """

    command: ExecutionEngineCommand
    kind: ExecutionEngineOutcomeKind
    result: ExecutionResult | None = None
    checkpoint: ExecutionCheckpoint | None = None

    def __post_init__(self) -> None:
        """Validate outcome evidence without retry, terminal, or output-registration behavior.

        Raises:
            ExecutionRequestValidationError: If result or checkpoint evidence mismatches the command.
        """
        if not isinstance(self.command, ExecutionEngineCommand):
            raise ExecutionRequestValidationError("invalid execution engine outcome command")
        if not isinstance(self.kind, ExecutionEngineOutcomeKind):
            raise ExecutionRequestValidationError("invalid execution engine outcome kind")
        if self.result is not None and self.result.plan_ref != self.command.plan.plan_ref:
            raise ExecutionRequestValidationError("execution engine result plan does not match")
        if self.checkpoint is not None and (
            self.checkpoint.plan_ref != self.command.plan.plan_ref
            or self.checkpoint.step_id != self.command.step.step_id
        ):
            raise ExecutionRequestValidationError("execution engine checkpoint does not match")
        if self.kind is ExecutionEngineOutcomeKind.SUCCEEDED and self.result is None:
            raise ExecutionRequestValidationError(
                "successful execution engine outcome requires a result"
            )


@dataclass(frozen=True, slots=True)
class ExecutionWorkerAssignmentDecision:
    """Carry a fail-closed worker-assignment decision without claiming work.

    Args:
        status: Assignment evaluation result.
        command: Exact Engine command the Worker may receive only when assigned.
        rejection_reason: Stable reason when assignment is denied.
    """

    status: ExecutionWorkerAssignmentStatus
    command: ExecutionEngineCommand
    rejection_reason: str | None = None


class ExecutionWorkerAssignmentStatus(StrEnum):
    """Classify a Worker assignment decision."""

    ASSIGNED = "assigned"
    REJECTED = "rejected"


class ExecutionEngine(Protocol):
    """Execute an already-routed step without owning lifecycle or routing policy."""

    def execute_execution_step(self, command: ExecutionEngineCommand) -> ExecutionEngineOutcome:
        """Execute one exact fenced step and return only normalized evidence."""


class ExecutionWorker(Protocol):
    """Receive one authorized fenced assignment without owning business lifecycle policy."""

    def execute_assigned_step(self, command: ExecutionEngineCommand) -> ExecutionEngineOutcome:
        """Delegate one authorized command to the configured Engine boundary."""


def evaluate_execution_worker_assignment(
    command: ExecutionEngineCommand,
    authorization_context: RegistryAuthorizationContext,
) -> ExecutionWorkerAssignmentDecision:
    """Validate a Worker assignment against trusted namespace authorization without claiming work.

    Args:
        command: Exact prevalidated Engine command.
        authorization_context: Server-derived namespace authorization context.

    Returns:
        An assigned or rejected immutable Worker decision.

    Raises:
        ExecutionRequestValidationError: If either assignment argument is malformed.
    """
    if not isinstance(command, ExecutionEngineCommand):
        raise ExecutionRequestValidationError("invalid execution worker command")
    if not isinstance(authorization_context, RegistryAuthorizationContext):
        raise ExecutionRequestValidationError("invalid execution worker authorization context")
    if (
        command.snapshot.namespace.value
        not in authorization_context.authorized_namespaces.namespace_ids
    ):
        return ExecutionWorkerAssignmentDecision(
            ExecutionWorkerAssignmentStatus.REJECTED,
            command,
            "execution worker namespace is not authorized",
        )
    return ExecutionWorkerAssignmentDecision(ExecutionWorkerAssignmentStatus.ASSIGNED, command)
