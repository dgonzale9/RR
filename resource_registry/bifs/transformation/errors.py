from __future__ import annotations

import json
from collections.abc import Mapping

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

_ERROR_STATUS: tuple[tuple[type[ApplicationError], int], ...] = (
    (ValidationError, 400),
    (AuthorizationError, 403),
    (NotFoundError, 404),
    (ConflictError, 409),
    (TimeoutError, 408),
    (CancelledError, 409),
    (RuntimeUnavailableError, 503),
    (OperationFailedError, 500),
    (InternalError, 500),
)


class TransformationBifErrorMapper:
    """Map application errors into the established JSON BIF failure envelope."""

    def map_error(self, *, error: ApplicationError) -> str:
        """Return a deterministic sanitized failure envelope."""
        status = next(
            (
                status_code
                for error_type, status_code in _ERROR_STATUS
                if isinstance(error, error_type)
            ),
            500,
        )
        return _dump_json(
            {
                "success": False,
                "status": status,
                "code": error.code,
                "category": error.category.value,
                "error": error.public_message,
                "retryable": error.retryable,
                "correlation_id": error.correlation_id,
                "metadata": dict(error.metadata),
            },
        )


def success_result(*, data: Mapping[str, object]) -> str:
    """Return a deterministic success envelope consistent with existing Resource Registry BIFs."""
    return _dump_json({"success": True, "data": dict(data)})


def _dump_json(value: Mapping[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
