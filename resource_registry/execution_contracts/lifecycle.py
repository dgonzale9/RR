"""Define pure fail-closed coordinator lifecycle contracts.

The values in this module model the Execution Coordinator's lifecycle decision only. They do not
persist an instance, publish an outbox event, claim worker work, or invoke an engine/provider.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from resource_registry.authorization.contracts import RegistryAuthorizationContext
from resource_registry.registry_core import NamespaceKey

from .errors import ExecutionRequestValidationError

__all__ = (
    "ExecutionAttemptFence",
    "ExecutionCoordinatorTransition",
    "ExecutionCoordinatorTransitionStatus",
    "ExecutionInstanceRef",
    "ExecutionLifecycleCommand",
    "ExecutionLifecycleSnapshot",
    "ExecutionLifecycleState",
    "coordinate_execution_lifecycle_transition",
)

_MAXIMUM_REFERENCE_LENGTH = 512


class ExecutionLifecycleState(StrEnum):
    """Enumerate the canonical durable execution lifecycle states."""

    CREATED = "created"
    VALIDATED = "validated"
    PLANNED = "planned"
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    WAITING = "waiting"
    RETRYING = "retrying"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    FAILED = "failed"
    COMPLETED = "completed"
    COMPENSATED = "compensated"
    ARCHIVED = "archived"


class ExecutionLifecycleCommand(StrEnum):
    """Enumerate Coordinator-owned lifecycle commands without transport semantics."""

    VALIDATE = "validate"
    PLAN = "plan"
    QUEUE = "queue"
    START = "start"
    CLAIM = "claim"
    PAUSE = "pause"
    WAIT = "wait"
    RESUME = "resume"
    RETRY = "retry"
    CANCEL = "cancel"
    CONFIRM_CANCELLATION = "confirm_cancellation"
    COMPLETE = "complete"
    FAIL = "fail"
    COMPENSATE = "compensate"
    ARCHIVE = "archive"


class ExecutionCoordinatorTransitionStatus(StrEnum):
    """Classify one deterministic Coordinator lifecycle decision."""

    APPLIED = "applied"
    IDEMPOTENT = "idempotent"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class ExecutionInstanceRef:
    """Identify one Execution-owned logical instance.

    Args:
        value: Exact nonblank Execution-owned instance identity.

    Raises:
        ExecutionRequestValidationError: If the instance identity is malformed.
    """

    value: str

    def __post_init__(self) -> None:
        """Validate the opaque instance identity.

        Raises:
            ExecutionRequestValidationError: If the instance identity is malformed.
        """
        if not _is_non_blank_string(self.value):
            raise ExecutionRequestValidationError("invalid execution instance reference")


@dataclass(frozen=True, slots=True)
class ExecutionAttemptFence:
    """Identify one exact live attempt fence without claiming work.

    Args:
        value: Exact nonblank Coordinator-issued attempt fence identity.

    Raises:
        ExecutionRequestValidationError: If the fence identity is malformed.
    """

    value: str

    def __post_init__(self) -> None:
        """Validate the opaque attempt fence identity.

        Raises:
            ExecutionRequestValidationError: If the fence identity is malformed.
        """
        if not _is_non_blank_string(self.value):
            raise ExecutionRequestValidationError("invalid execution attempt fence")


@dataclass(frozen=True, slots=True)
class ExecutionLifecycleSnapshot:
    """Carry one immutable tenant-scoped Coordinator lifecycle snapshot.

    Args:
        instance_ref: Exact Execution-owned instance identity.
        namespace: Server-established namespace that scopes the instance.
        state: Current canonical lifecycle state.
        revision: Monotonic optimistic-concurrency revision.
        active_fence: Active attempt fence only while an attempt can apply a result.

    Raises:
        ExecutionRequestValidationError: If the snapshot is malformed or has invalid fence state.
    """

    instance_ref: ExecutionInstanceRef
    namespace: NamespaceKey
    state: ExecutionLifecycleState
    revision: int = 0
    active_fence: ExecutionAttemptFence | None = None

    def __post_init__(self) -> None:
        """Validate lifecycle ownership, revision, and fence consistency.

        Raises:
            ExecutionRequestValidationError: If the snapshot violates Coordinator invariants.
        """
        if not isinstance(self.instance_ref, ExecutionInstanceRef):
            raise ExecutionRequestValidationError("invalid execution lifecycle instance reference")
        if not isinstance(self.namespace, NamespaceKey):
            raise ExecutionRequestValidationError("invalid execution lifecycle namespace")
        if not isinstance(self.state, ExecutionLifecycleState):
            raise ExecutionRequestValidationError("invalid execution lifecycle state")
        if type(self.revision) is not int or self.revision < 0:
            raise ExecutionRequestValidationError("invalid execution lifecycle revision")
        if self.active_fence is not None and not isinstance(
            self.active_fence, ExecutionAttemptFence
        ):
            raise ExecutionRequestValidationError("invalid execution lifecycle fence")
        if self.state is ExecutionLifecycleState.RUNNING and self.active_fence is None:
            raise ExecutionRequestValidationError("running execution lifecycle requires a fence")
        if self.state is not ExecutionLifecycleState.RUNNING and self.active_fence is not None:
            raise ExecutionRequestValidationError(
                "execution lifecycle fence requires running state"
            )


@dataclass(frozen=True, slots=True)
class ExecutionCoordinatorTransition:
    """Carry one pure Coordinator transition decision and append-only evidence.

    Args:
        status: Whether the transition applied, was idempotent, or was rejected.
        snapshot: The resulting or unchanged snapshot.
        command: Coordinator command evaluated.
        previous_state: State evaluated before the command.
        rejection_reason: Stable fail-closed reason for a rejected command.
    """

    status: ExecutionCoordinatorTransitionStatus
    snapshot: ExecutionLifecycleSnapshot
    command: ExecutionLifecycleCommand
    previous_state: ExecutionLifecycleState
    rejection_reason: str | None = None


_COMMAND_TRANSITIONS: dict[
    ExecutionLifecycleCommand, tuple[ExecutionLifecycleState, ExecutionLifecycleState]
] = {
    ExecutionLifecycleCommand.VALIDATE: (
        ExecutionLifecycleState.CREATED,
        ExecutionLifecycleState.VALIDATED,
    ),
    ExecutionLifecycleCommand.PLAN: (
        ExecutionLifecycleState.VALIDATED,
        ExecutionLifecycleState.PLANNED,
    ),
    ExecutionLifecycleCommand.QUEUE: (
        ExecutionLifecycleState.PLANNED,
        ExecutionLifecycleState.QUEUED,
    ),
    ExecutionLifecycleCommand.CLAIM: (
        ExecutionLifecycleState.QUEUED,
        ExecutionLifecycleState.RUNNING,
    ),
    ExecutionLifecycleCommand.START: (
        ExecutionLifecycleState.PLANNED,
        ExecutionLifecycleState.RUNNING,
    ),
    ExecutionLifecycleCommand.PAUSE: (
        ExecutionLifecycleState.RUNNING,
        ExecutionLifecycleState.PAUSED,
    ),
    ExecutionLifecycleCommand.WAIT: (
        ExecutionLifecycleState.RUNNING,
        ExecutionLifecycleState.WAITING,
    ),
    ExecutionLifecycleCommand.RESUME: (
        ExecutionLifecycleState.WAITING,
        ExecutionLifecycleState.RUNNING,
    ),
    ExecutionLifecycleCommand.RETRY: (
        ExecutionLifecycleState.RUNNING,
        ExecutionLifecycleState.RETRYING,
    ),
    ExecutionLifecycleCommand.CONFIRM_CANCELLATION: (
        ExecutionLifecycleState.CANCELLING,
        ExecutionLifecycleState.CANCELLED,
    ),
    ExecutionLifecycleCommand.COMPLETE: (
        ExecutionLifecycleState.RUNNING,
        ExecutionLifecycleState.COMPLETED,
    ),
    ExecutionLifecycleCommand.ARCHIVE: (
        ExecutionLifecycleState.COMPLETED,
        ExecutionLifecycleState.ARCHIVED,
    ),
}


def coordinate_execution_lifecycle_transition(
    snapshot: ExecutionLifecycleSnapshot,
    command: ExecutionLifecycleCommand,
    authorization_context: RegistryAuthorizationContext,
    expected_revision: int,
    fence: ExecutionAttemptFence | None = None,
) -> ExecutionCoordinatorTransition:
    """Evaluate one fail-closed Coordinator lifecycle transition without mutation.

    Args:
        snapshot: Immutable current lifecycle snapshot supplied by the Execution store boundary.
        command: Coordinator-owned lifecycle command to evaluate.
        authorization_context: Server-derived namespace authorization context.
        expected_revision: Revision observed by the authorized command boundary.
        fence: Fence required for an active-attempt command or a new claim/resume.

    Returns:
        A deterministic applied, idempotent, or rejected transition decision.

    Raises:
        ExecutionRequestValidationError: If a contract argument is malformed.
    """
    _validate_transition_arguments(
        snapshot, command, authorization_context, expected_revision, fence
    )
    if snapshot.namespace.value not in authorization_context.authorized_namespaces.namespace_ids:
        return _rejected_transition(
            snapshot, command, "execution lifecycle namespace is not authorized"
        )
    if snapshot.revision != expected_revision:
        return _rejected_transition(
            snapshot, command, "execution lifecycle revision does not match"
        )

    source_and_target = _command_source_and_target(snapshot, command)
    if source_and_target is None:
        return _rejected_transition(snapshot, command, "invalid execution lifecycle transition")
    source_state, target_state = source_and_target
    if snapshot.state is target_state:
        return ExecutionCoordinatorTransition(
            ExecutionCoordinatorTransitionStatus.IDEMPOTENT,
            snapshot,
            command,
            snapshot.state,
        )
    if snapshot.state is not source_state:
        return _rejected_transition(snapshot, command, "invalid execution lifecycle transition")
    if not _has_required_fence(snapshot, command, fence):
        return _rejected_transition(snapshot, command, "execution lifecycle fence does not match")

    next_fence = fence if target_state is ExecutionLifecycleState.RUNNING else None
    return ExecutionCoordinatorTransition(
        ExecutionCoordinatorTransitionStatus.APPLIED,
        ExecutionLifecycleSnapshot(
            snapshot.instance_ref,
            snapshot.namespace,
            target_state,
            snapshot.revision + 1,
            next_fence,
        ),
        command,
        snapshot.state,
    )


def _command_source_and_target(
    snapshot: ExecutionLifecycleSnapshot,
    command: ExecutionLifecycleCommand,
) -> tuple[ExecutionLifecycleState, ExecutionLifecycleState] | None:
    """Resolve one canonical source and target state for a Coordinator command."""
    if (
        command is ExecutionLifecycleCommand.QUEUE
        and snapshot.state is ExecutionLifecycleState.RETRYING
    ):
        return ExecutionLifecycleState.RETRYING, ExecutionLifecycleState.QUEUED
    if (
        command is ExecutionLifecycleCommand.QUEUE
        and snapshot.state is ExecutionLifecycleState.PAUSED
    ):
        return ExecutionLifecycleState.PAUSED, ExecutionLifecycleState.QUEUED
    if command is ExecutionLifecycleCommand.CANCEL:
        if snapshot.state in {
            ExecutionLifecycleState.CREATED,
            ExecutionLifecycleState.VALIDATED,
            ExecutionLifecycleState.PLANNED,
            ExecutionLifecycleState.QUEUED,
        }:
            return snapshot.state, ExecutionLifecycleState.CANCELLED
        if snapshot.state in {ExecutionLifecycleState.RUNNING, ExecutionLifecycleState.WAITING}:
            return snapshot.state, ExecutionLifecycleState.CANCELLING
        if snapshot.state is ExecutionLifecycleState.CANCELLED:
            return ExecutionLifecycleState.CANCELLED, ExecutionLifecycleState.CANCELLED
        return None
    if command is ExecutionLifecycleCommand.FAIL:
        if snapshot.state in {ExecutionLifecycleState.RUNNING, ExecutionLifecycleState.CANCELLING}:
            return snapshot.state, ExecutionLifecycleState.FAILED
        if snapshot.state is ExecutionLifecycleState.FAILED:
            return ExecutionLifecycleState.FAILED, ExecutionLifecycleState.FAILED
    if command is ExecutionLifecycleCommand.COMPENSATE:
        if snapshot.state in {ExecutionLifecycleState.FAILED, ExecutionLifecycleState.CANCELLED}:
            return snapshot.state, ExecutionLifecycleState.COMPENSATED
        if snapshot.state is ExecutionLifecycleState.COMPENSATED:
            return ExecutionLifecycleState.COMPENSATED, ExecutionLifecycleState.COMPENSATED
    if command is ExecutionLifecycleCommand.ARCHIVE and snapshot.state in {
        ExecutionLifecycleState.FAILED,
        ExecutionLifecycleState.CANCELLED,
        ExecutionLifecycleState.COMPENSATED,
    }:
        return snapshot.state, ExecutionLifecycleState.ARCHIVED
    if (
        command is ExecutionLifecycleCommand.ARCHIVE
        and snapshot.state is ExecutionLifecycleState.ARCHIVED
    ):
        return ExecutionLifecycleState.ARCHIVED, ExecutionLifecycleState.ARCHIVED
    return _COMMAND_TRANSITIONS.get(command)


def _has_required_fence(
    snapshot: ExecutionLifecycleSnapshot,
    command: ExecutionLifecycleCommand,
    fence: ExecutionAttemptFence | None,
) -> bool:
    """Require exact fences for claim/resume and active-attempt state changes."""
    if command in {
        ExecutionLifecycleCommand.CLAIM,
        ExecutionLifecycleCommand.RESUME,
        ExecutionLifecycleCommand.START,
    }:
        return fence is not None
    if snapshot.state is ExecutionLifecycleState.RUNNING:
        return fence is not None and fence == snapshot.active_fence
    return fence is None


def _validate_transition_arguments(
    snapshot: ExecutionLifecycleSnapshot,
    command: ExecutionLifecycleCommand,
    authorization_context: RegistryAuthorizationContext,
    expected_revision: int,
    fence: ExecutionAttemptFence | None,
) -> None:
    """Validate pure Coordinator transition arguments before evaluating state."""
    if not isinstance(snapshot, ExecutionLifecycleSnapshot):
        raise ExecutionRequestValidationError("invalid execution lifecycle snapshot")
    if not isinstance(command, ExecutionLifecycleCommand):
        raise ExecutionRequestValidationError("invalid execution lifecycle command")
    if not isinstance(authorization_context, RegistryAuthorizationContext):
        raise ExecutionRequestValidationError("invalid execution lifecycle authorization context")
    if type(expected_revision) is not int or expected_revision < 0:
        raise ExecutionRequestValidationError("invalid execution lifecycle expected revision")
    if fence is not None and not isinstance(fence, ExecutionAttemptFence):
        raise ExecutionRequestValidationError("invalid execution lifecycle command fence")


def _rejected_transition(
    snapshot: ExecutionLifecycleSnapshot,
    command: ExecutionLifecycleCommand,
    reason: str,
) -> ExecutionCoordinatorTransition:
    """Build one deterministic rejected Coordinator transition."""
    return ExecutionCoordinatorTransition(
        ExecutionCoordinatorTransitionStatus.REJECTED,
        snapshot,
        command,
        snapshot.state,
        reason,
    )


def _is_non_blank_string(value: str) -> bool:
    """Return whether one bounded opaque identity value is nonblank."""
    return (
        isinstance(value, str) and bool(value.strip()) and len(value) <= _MAXIMUM_REFERENCE_LENGTH
    )
