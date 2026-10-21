from __future__ import annotations

from uuid import UUID

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import DatabaseError
from django.db.models import QuerySet

from resource_registry.application.contracts import (
    AuthorizationError,
    TransformationRunReference,
    ValidationError,
)
from resource_registry.application.read.authorization import TransformationReadAuthorizationService
from resource_registry.application.read.base import get_authorized_run, map_read_failure
from resource_registry.application.read.dtos import (
    TransformationReadContext,
    TransformationRunFilters,
)
from resource_registry.application.read.mapping import map_run_reference
from resource_registry.models import TransformationRun

DEFAULT_TRANSFORMATION_READ_LIMIT = 50
MAXIMUM_TRANSFORMATION_READ_LIMIT = 100


class TransformationRunReadService:
    """Read tenant-scoped transformation runs into immutable application DTOs."""

    def __init__(self, *, authorization: TransformationReadAuthorizationService) -> None:
        self._authorization = authorization

    def get_run(
        self,
        *,
        context: TransformationReadContext,
        run_id: str,
    ) -> TransformationRunReference:
        """Return one authorized run reference or a stable application error."""
        run = get_authorized_run(
            context=context,
            run_id=run_id,
            authorization=self._authorization,
        )
        try:
            return map_run_reference(run)
        except (KeyError, TypeError, ValueError) as error:
            raise map_read_failure(context=context, error=error) from error

    def list_runs(
        self,
        *,
        context: TransformationReadContext,
        filters: TransformationRunFilters | None = None,
        limit: int = DEFAULT_TRANSFORMATION_READ_LIMIT,
        offset: int = 0,
    ) -> tuple[TransformationRunReference, ...]:
        """Return a deterministically ordered authorized page of run references."""
        self._authorization.authorize_run_list(context=context)
        active_filters = filters or TransformationRunFilters()
        if (
            active_filters.created_after is not None
            and active_filters.created_before is not None
            and active_filters.created_after > active_filters.created_before
        ):
            raise ValidationError(
                code="invalid_transformation_run_date_range",
                public_message="The transformation run date range is invalid.",
                correlation_id=context.correlation_id,
            )
        if (
            active_filters.definition_id is not None
            and context.allowed_definition_ids is not None
            and active_filters.definition_id not in context.allowed_definition_ids
        ):
            raise AuthorizationError(
                code="transformation_read_not_authorized",
                public_message="The transformation resource is not available.",
                correlation_id=context.correlation_id,
            )
        page_limit = _normalize_limit(limit)
        page_offset = max(offset, 0)
        try:
            queryset = self._apply_filters(
                TransformationRun.objects.filter(tenant_id=context.tenant_id),
                filters=active_filters,
                context=context,
            )
            rows = tuple(
                queryset.order_by("-created_at", "-id")[
                    page_offset : page_offset + page_limit
                ]
            )
            return tuple(map_run_reference(run) for run in rows)
        except (DatabaseError, DjangoValidationError, KeyError, TypeError, ValueError) as error:
            raise map_read_failure(context=context, error=error) from error

    @staticmethod
    def _apply_filters(
        queryset: QuerySet[TransformationRun],
        *,
        filters: TransformationRunFilters,
        context: TransformationReadContext,
    ) -> QuerySet[TransformationRun]:
        allowed_ids = context.allowed_definition_ids
        if allowed_ids is not None:
            queryset = queryset.filter(definition_id__in=allowed_ids)
        if filters.definition_id is not None:
            queryset = queryset.filter(
                definition_id=_parse_uuid_filter(
                    value=filters.definition_id,
                    context=context,
                ),
            )
        if filters.definition_version_id is not None:
            queryset = queryset.filter(
                definition_version_id=_parse_uuid_filter(
                    value=filters.definition_version_id,
                    context=context,
                ),
            )
        if filters.status is not None:
            queryset = queryset.filter(status=filters.status.value)
        if filters.created_after is not None:
            queryset = queryset.filter(created_at__gte=filters.created_after)
        if filters.created_before is not None:
            queryset = queryset.filter(created_at__lte=filters.created_before)
        if filters.correlation_id is not None:
            queryset = queryset.filter(correlation_id=filters.correlation_id)
        return queryset


def _normalize_limit(limit: int) -> int:
    if limit <= 0:
        return DEFAULT_TRANSFORMATION_READ_LIMIT
    return min(limit, MAXIMUM_TRANSFORMATION_READ_LIMIT)


def _parse_uuid_filter(*, value: str, context: TransformationReadContext) -> UUID:
    try:
        return UUID(value)
    except ValueError as error:
        raise ValidationError(
            code="invalid_transformation_run_filter",
            public_message="A transformation run filter is invalid.",
            correlation_id=context.correlation_id,
        ) from error
