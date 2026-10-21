"""Narrow failures raised by transformation-run creation."""


class TransformationRunCreationError(Exception):
    """Base failure for transformation-run creation."""


class InvalidTransformationRunCreationRequestError(TransformationRunCreationError):
    """Raise when required creation identities or values are malformed."""


class InvalidTransformationExecutionPlanSnapshotError(TransformationRunCreationError):
    """Raise when an execution plan cannot produce the approved persisted snapshot."""


class TransformationExecutionPlanChecksumMismatchError(TransformationRunCreationError):
    """Raise when the supplied plan checksum does not match its canonical content."""


class TransformationRunDefinitionNotFoundError(TransformationRunCreationError):
    """Raise when the definition is absent from the requested tenant scope."""


class ArchivedTransformationRunDefinitionError(TransformationRunCreationError):
    """Raise when an archived definition cannot start a new run."""


class TransformationRunDefinitionVersionNotFoundError(TransformationRunCreationError):
    """Raise when the version is absent from the exact definition scope."""


class IneligibleTransformationRunDefinitionVersionError(TransformationRunCreationError):
    """Raise when the exact version is not published."""


class TransformationRunSpecificationChecksumMismatchError(TransformationRunCreationError):
    """Raise when persisted and compiled specification checksums differ."""


class TransformationRunPlanIdentityMismatchError(TransformationRunCreationError):
    """Raise when request, plan, and persisted identities disagree."""


class InvalidTransformationRunIdempotencyKeyError(TransformationRunCreationError):
    """Raise when an optional caller idempotency key is invalid."""


class TransformationRunIdempotencyConflictError(TransformationRunCreationError):
    """Raise when a tenant-scoped caller key is rebound to a different request."""


class TransformationRunActiveDuplicateResolutionError(TransformationRunCreationError):
    """Raise when a database duplicate cannot be resolved to an active equivalent run."""


class TransformationRunOutputDeclarationMismatchError(TransformationRunCreationError):
    """Raise when final-output declarations are incomplete or duplicated."""
