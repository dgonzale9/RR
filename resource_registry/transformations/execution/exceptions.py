class TransformationExecutionPersistenceError(RuntimeError):
    """Base class for sanitized Story 5 persistence failures."""

    code = "unexpected_execution_failure"


class TransformationRunNotFoundError(TransformationExecutionPersistenceError):
    """Raise when the explicit tenant-scoped run does not exist."""

    code = "run_not_found"


class TransformationRunNotEligibleError(TransformationExecutionPersistenceError):
    """Raise when an explicit run cannot initialize an attempt."""

    code = "run_not_eligible"


class InvalidPersistedExecutionPlanError(TransformationExecutionPersistenceError):
    """Raise when the persisted execution plan is invalid or mismatched."""

    code = "invalid_plan_snapshot"


class UnsupportedPersistedExecutionPlanError(TransformationExecutionPersistenceError):
    """Raise when a persisted plan uses an unsupported contract version."""

    code = "unsupported_plan_contract"


class PersistedExecutionPlanIdentityMismatchError(TransformationExecutionPersistenceError):
    """Raise when a verified plan conflicts with its durable run columns."""

    code = "plan_identity_mismatch"


class InvalidPersistedAuthorizationSnapshotError(TransformationExecutionPersistenceError):
    """Raise when persisted Transformation execution authority is invalid."""

    code = "invalid_authorization_snapshot"


class UnsupportedPersistedAuthorizationSnapshotError(TransformationExecutionPersistenceError):
    """Raise when persisted Transformation authority uses an unsupported contract."""

    code = "unsupported_authorization_contract"


class StaleTransformationAttemptError(TransformationExecutionPersistenceError):
    """Raise when a fenced writer no longer owns the current run attempt."""

    code = "stale_attempt"


class TransformationCancellationPrecedenceError(TransformationExecutionPersistenceError):
    """Raise when cancellation wins before successful checkpoint persistence."""
