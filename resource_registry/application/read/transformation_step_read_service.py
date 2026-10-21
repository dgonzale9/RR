from __future__ import annotations

from django.db import DatabaseError

from resource_registry.application.contracts import TransformationStepSummary
from resource_registry.application.read.authorization import TransformationReadAuthorizationService
from resource_registry.application.read.base import get_authorized_run, map_read_failure
from resource_registry.application.read.dtos import TransformationReadContext
from resource_registry.application.read.mapping import map_step_summary
from resource_registry.models import TransformationRunStepAttempt


class TransformationStepReadService:
    """Read ordered step-attempt summaries without execution internals."""

    def __init__(self, *, authorization: TransformationReadAuthorizationService) -> None:
        self._authorization = authorization

    def list_steps(
        self,
        *,
        context: TransformationReadContext,
        run_id: str,
    ) -> tuple[TransformationStepSummary, ...]:
        """Return all authorized attempts in run-attempt and execution order."""
        run = get_authorized_run(
            context=context,
            run_id=run_id,
            authorization=self._authorization,
        )
        try:
            rows = TransformationRunStepAttempt.objects.filter(run=run).order_by(
                "run_attempt_number",
                "step_index",
                "id",
            )
            return tuple(map_step_summary(step) for step in rows)
        except (DatabaseError, KeyError, TypeError, ValueError) as error:
            raise map_read_failure(context=context, error=error) from error
