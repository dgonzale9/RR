from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import DatabaseError

from resource_registry.application.contracts import InternalError, NotFoundError
from resource_registry.application.read.authorization import TransformationReadAuthorizationService
from resource_registry.application.read.dtos import TransformationReadContext
from resource_registry.models import TransformationRun


def get_authorized_run(
    *,
    context: TransformationReadContext,
    run_id: str,
    authorization: TransformationReadAuthorizationService,
) -> TransformationRun:
    """Return one tenant-scoped authorized run without revealing foreign-tenant existence."""
    try:
        run = TransformationRun.objects.filter(
            id=run_id,
            tenant_id=context.tenant_id,
        ).first()
    except (DjangoValidationError, ValueError):
        run = None
    except DatabaseError as error:
        raise InternalError(
            code="transformation_read_failed",
            public_message="The transformation resource could not be read.",
            retryable=True,
            correlation_id=context.correlation_id,
        ) from error
    if run is None:
        raise NotFoundError(
            code="transformation_run_not_found",
            public_message="The transformation run was not found.",
            correlation_id=context.correlation_id,
        )
    authorization.authorize_run(
        context=context,
        definition_id=str(run.definition_id),
    )
    return run


def map_read_failure(*, context: TransformationReadContext, error: Exception) -> InternalError:
    """Map an internal persistence or mapping failure into one stable application error."""
    return InternalError(
        code="transformation_read_failed",
        public_message="The transformation resource could not be read.",
        retryable=isinstance(error, DatabaseError),
        correlation_id=context.correlation_id,
    )
