from __future__ import annotations

from collections.abc import Mapping

from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed, NotAuthenticated, PermissionDenied
from rest_framework.response import Response

from resource_registry.application.contracts import (
    ApplicationError,
    AuthorizationError,
    CancelledError,
    ConflictError,
    InternalError,
    NotFoundError,
    OperationFailedError,
    RuntimeUnavailableError,
    TimeoutError,
    ValidationError,
)

_APPLICATION_ERROR_STATUS: tuple[tuple[type[ApplicationError], int], ...] = (
    (ValidationError, status.HTTP_400_BAD_REQUEST),
    (AuthorizationError, status.HTTP_403_FORBIDDEN),
    (NotFoundError, status.HTTP_404_NOT_FOUND),
    (ConflictError, status.HTTP_409_CONFLICT),
    (TimeoutError, status.HTTP_408_REQUEST_TIMEOUT),
    (CancelledError, status.HTTP_409_CONFLICT),
    (RuntimeUnavailableError, status.HTTP_503_SERVICE_UNAVAILABLE),
    (OperationFailedError, status.HTTP_500_INTERNAL_SERVER_ERROR),
    (InternalError, status.HTTP_500_INTERNAL_SERVER_ERROR),
)


def application_error_response(*, error: ApplicationError) -> Response:
    """Map one sanitized application error into its stable HTTP representation."""
    response_status = next(
        (
            status_code
            for error_type, status_code in _APPLICATION_ERROR_STATUS
            if isinstance(error, error_type)
        ),
        status.HTTP_500_INTERNAL_SERVER_ERROR,
    )
    return Response(
        {
            "contract_version": error.contract_version,
            "code": error.code,
            "category": error.category.value,
            "message": error.public_message,
            "retryable": error.retryable,
            "correlation_id": error.correlation_id,
            "metadata": dict(error.metadata),
        },
        status=response_status,
    )


def serializer_error_response(
    *,
    errors: Mapping[str, object],
    correlation_id: str | None,
) -> Response:
    """Return sanitized transport validation without exposing serializer internals."""
    error = ValidationError(
        code="invalid_transformation_rest_request",
        public_message="The transformation request is invalid.",
        correlation_id=correlation_id,
        metadata={"field_names": sorted(str(field_name) for field_name in errors)},
    )
    return application_error_response(error=error)


def authentication_error_response(*, error: Exception) -> Response | None:
    """Map DRF authentication and permission failures into the stable error schema."""
    if isinstance(error, NotAuthenticated | AuthenticationFailed):
        return Response(
            {
                "contract_version": "transformation-application/1.0",
                "code": "transformation_authentication_required",
                "category": "AUTHORIZATION",
                "message": "Authentication is required.",
                "retryable": False,
                "correlation_id": None,
                "metadata": {},
            },
            status=status.HTTP_401_UNAUTHORIZED,
        )
    if isinstance(error, PermissionDenied):
        return Response(
            {
                "contract_version": "transformation-application/1.0",
                "code": "transformation_request_not_authorized",
                "category": "AUTHORIZATION",
                "message": "The transformation request is not authorized.",
                "retryable": False,
                "correlation_id": None,
                "metadata": {},
            },
            status=status.HTTP_403_FORBIDDEN,
        )
    return None
