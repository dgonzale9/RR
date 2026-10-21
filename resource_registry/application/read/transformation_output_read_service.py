from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import DatabaseError

from resource_registry.application.contracts import NotFoundError, TransformationOutputReference
from resource_registry.application.read.authorization import TransformationReadAuthorizationService
from resource_registry.application.read.base import get_authorized_run, map_read_failure
from resource_registry.application.read.dtos import TransformationReadContext
from resource_registry.application.read.mapping import map_output_reference
from resource_registry.models import TransformationRunOutput


class TransformationOutputReadService:
    """Read sanitized output references without storage implementation details."""

    def __init__(self, *, authorization: TransformationReadAuthorizationService) -> None:
        self._authorization = authorization

    def list_outputs(
        self,
        *,
        context: TransformationReadContext,
        run_id: str,
    ) -> tuple[TransformationOutputReference, ...]:
        """Return ordered authorized output references for one run."""
        run = get_authorized_run(
            context=context,
            run_id=run_id,
            authorization=self._authorization,
        )
        try:
            rows = tuple(
                TransformationRunOutput.objects.filter(run=run).order_by("output_index", "id")
            )
            for output in rows:
                self._authorization.authorize_output_dataset(
                    context=context,
                    dataset_id=str(output.dataset_id) if output.dataset_id is not None else None,
                )
            return tuple(map_output_reference(output) for output in rows)
        except (DatabaseError, KeyError, TypeError, ValueError) as error:
            raise map_read_failure(context=context, error=error) from error

    def get_output(
        self,
        *,
        context: TransformationReadContext,
        output_id: str,
    ) -> TransformationOutputReference:
        """Return one tenant-scoped authorized output reference."""
        try:
            output = (
                TransformationRunOutput.objects.select_related("run")
                .filter(id=output_id, run__tenant_id=context.tenant_id)
                .first()
            )
        except (DjangoValidationError, ValueError):
            output = None
        except DatabaseError as error:
            raise map_read_failure(context=context, error=error) from error
        if output is None:
            raise NotFoundError(
                code="transformation_output_not_found",
                public_message="The transformation output was not found.",
                correlation_id=context.correlation_id,
            )
        self._authorization.authorize_run(
            context=context,
            definition_id=str(output.run.definition_id),
        )
        self._authorization.authorize_output_dataset(
            context=context,
            dataset_id=str(output.dataset_id) if output.dataset_id is not None else None,
        )
        try:
            return map_output_reference(output)
        except (KeyError, TypeError, ValueError) as error:
            raise map_read_failure(context=context, error=error) from error
