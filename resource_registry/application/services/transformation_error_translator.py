from __future__ import annotations

from builtins import TimeoutError as BuiltinTimeoutError
from concurrent.futures import CancelledError as FutureCancelledError

from django.db import DatabaseError

from resource_registry.application.contracts import (
    ApplicationError,
    AuthorizationError,
    CancelledError,
    ConflictError,
    InternalError,
    NotFoundError,
    OperationFailedError,
    RuntimeUnavailableError,
    ValidationError,
)
from resource_registry.application.contracts import (
    TimeoutError as ApplicationTimeoutError,
)
from resource_registry.application.transformation_runtime import RuntimeBootstrapException
from resource_registry.read.exceptions import (
    TransformationDefinitionNotFoundError,
    TransformationDefinitionVersionNotFoundError,
)
from resource_registry.transformations.binding.exceptions import (
    TransformationInputReferenceResolutionError,
)
from resource_registry.transformations.operational.exceptions import (
    TransformationGovernanceDeniedError,
    TransformationPermissionDeniedError,
    TransformationSecurityPolicyConfigurationError,
)
from resource_registry.transformations.runs.exceptions import (
    ArchivedTransformationRunDefinitionError,
    IneligibleTransformationRunDefinitionVersionError,
    InvalidTransformationRunCreationRequestError,
    InvalidTransformationRunIdempotencyKeyError,
    TransformationRunActiveDuplicateResolutionError,
    TransformationRunCreationError,
    TransformationRunDefinitionNotFoundError,
    TransformationRunDefinitionVersionNotFoundError,
    TransformationRunIdempotencyConflictError,
)
from resource_registry.transformations.validation.exceptions import TransformationDslError

_NOT_FOUND_ERRORS = (
    TransformationDefinitionNotFoundError
    | TransformationDefinitionVersionNotFoundError
    | TransformationRunDefinitionNotFoundError
    | TransformationRunDefinitionVersionNotFoundError
)
_CONFLICT_ERRORS = (
    TransformationRunIdempotencyConflictError
    | TransformationRunActiveDuplicateResolutionError
    | ArchivedTransformationRunDefinitionError
    | IneligibleTransformationRunDefinitionVersionError
)
_VALIDATION_ERRORS = (
    InvalidTransformationRunCreationRequestError
    | InvalidTransformationRunIdempotencyKeyError
    | TransformationInputReferenceResolutionError
    | TransformationDslError
)
_UNAVAILABLE_ERRORS = (
    RuntimeBootstrapException | TransformationSecurityPolicyConfigurationError | DatabaseError
)


class TransformationApplicationErrorTranslator:
    """Translate known internal failures into stable sanitized application errors."""

    def translate(  # noqa: PLR0911
        self,
        *,
        error: Exception,
        correlation_id: str | None,
    ) -> ApplicationError:
        """Return a stable application error without copying internal exception details."""
        if isinstance(error, ApplicationError):
            return error
        if isinstance(
            error,
            TransformationPermissionDeniedError | TransformationGovernanceDeniedError,
        ):
            return AuthorizationError(
                code="transformation_execution_not_authorized",
                public_message="The transformation execution is not authorized.",
                correlation_id=correlation_id,
            )
        if isinstance(error, _NOT_FOUND_ERRORS):
            return NotFoundError(
                code="transformation_definition_not_found",
                public_message="The transformation definition or version was not found.",
                correlation_id=correlation_id,
            )
        if isinstance(error, _CONFLICT_ERRORS):
            return ConflictError(
                code="transformation_execution_conflict",
                public_message="The transformation execution conflicts with current state.",
                correlation_id=correlation_id,
            )
        if isinstance(error, _VALIDATION_ERRORS):
            return ValidationError(
                code="invalid_transformation_execution_request",
                public_message="The transformation execution request is invalid.",
                correlation_id=correlation_id,
            )
        if isinstance(error, TransformationRunCreationError):
            return OperationFailedError(
                code="transformation_operation_failed",
                public_message="The transformation execution could not be accepted.",
                correlation_id=correlation_id,
            )
        if isinstance(error, _UNAVAILABLE_ERRORS):
            return RuntimeUnavailableError(
                code="transformation_runtime_unavailable",
                public_message="The transformation runtime is unavailable.",
                retryable=True,
                correlation_id=correlation_id,
            )
        if isinstance(error, FutureCancelledError):
            return CancelledError(
                code="transformation_execution_cancelled",
                public_message="The transformation execution was cancelled.",
                correlation_id=correlation_id,
            )
        if isinstance(error, BuiltinTimeoutError):
            return ApplicationTimeoutError(
                code="transformation_execution_timeout",
                public_message="The transformation execution request timed out.",
                retryable=True,
                correlation_id=correlation_id,
            )
        return InternalError(
            code="transformation_internal_error",
            public_message="The transformation execution could not be accepted.",
            correlation_id=correlation_id,
        )
