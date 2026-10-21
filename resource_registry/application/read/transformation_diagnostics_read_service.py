from __future__ import annotations

from collections import Counter

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import DatabaseError

from resource_registry.application.contracts import (
    NotFoundError,
    RunStatus,
    RuntimeUnavailableError,
    TransformationDiagnosticSummary,
)
from resource_registry.application.read.authorization import TransformationReadAuthorizationService
from resource_registry.application.read.base import map_read_failure
from resource_registry.application.read.dtos import TransformationReadContext
from resource_registry.application.read.mapping import map_health_state
from resource_registry.models import (
    TransformationRun,
    TransformationRunOutput,
    TransformationRunStepAttempt,
    TransformationRunStepAttemptStatus,
)
from resource_registry.transformations.operational.health import (
    TransformationOperationalHealthService,
)


class TransformationDiagnosticsReadService:
    """Read safe run diagnostics from durable aggregates and runtime health."""

    def __init__(
        self,
        *,
        authorization: TransformationReadAuthorizationService,
        health: TransformationOperationalHealthService,
    ) -> None:
        self._authorization = authorization
        self._health = health

    def get_run_diagnostics(
        self,
        *,
        context: TransformationReadContext,
        run_id: str,
    ) -> TransformationDiagnosticSummary:
        """Return authorized aggregate diagnostics without deserializing runtime payloads."""
        run = self._get_tenant_run(context=context, run_id=run_id)
        self._authorization.authorize_run_diagnostics(
            context=context,
            definition_id=str(run.definition_id),
        )
        try:
            steps = tuple(
                TransformationRunStepAttempt.objects.filter(run=run).only(
                    "status",
                    "error_code",
                    "execution_ms",
                    "run_attempt_number",
                )
            )
            outputs = tuple(
                TransformationRunOutput.objects.filter(run=run).only(
                    "status",
                    "error_code",
                    "materialization_started_at",
                    "completed_at",
                )
            )
        except DatabaseError as error:
            raise map_read_failure(context=context, error=error) from error
        try:
            health = self._health.get_health(tenant_id=context.tenant_id)
        except Exception as error:
            raise RuntimeUnavailableError(
                code="transformation_health_unavailable",
                public_message="Transformation health is unavailable.",
                retryable=True,
                correlation_id=context.correlation_id,
            ) from error
        latest_steps = tuple(
            step for step in steps if step.run_attempt_number == run.latest_attempt_number
        )
        completed_steps = sum(
            step.status == TransformationRunStepAttemptStatus.SUCCEEDED for step in latest_steps
        )
        progress = _calculate_progress(
            run_status=run.status,
            completed_steps=completed_steps,
            total_steps=len(latest_steps),
        )
        warnings = {
            error_code
            for error_code in (
                run.error_code,
                *(step.error_code for step in steps),
                *(output.error_code for output in outputs),
            )
            if error_code
        }
        if health.status.value != "READY":
            warnings.add(f"runtime_health_{health.status.value.lower()}")
        if run.retry_count:
            warnings.add("execution_retried")
        if run.finalization_retry_count:
            warnings.add("finalization_retried")
        return TransformationDiagnosticSummary(
            health_state=map_health_state(health.status.value),
            aggregate_counts={
                "retry_count": run.retry_count,
                "finalization_retry_count": run.finalization_retry_count,
                "steps_by_status": dict(Counter(step.status for step in steps)),
                "outputs_by_status": dict(Counter(output.status for output in outputs)),
            },
            warnings=tuple(warnings),
            durations={
                "execution": run.execution_ms / 1000 if run.execution_ms is not None else 0.0,
                "finalization": _calculate_finalization_seconds(outputs),
            },
            duration_seconds=(run.execution_ms / 1000 if run.execution_ms is not None else None),
            progress=progress,
        )

    @staticmethod
    def _get_tenant_run(
        *,
        context: TransformationReadContext,
        run_id: str,
    ) -> TransformationRun:
        try:
            run = TransformationRun.objects.filter(
                id=run_id,
                tenant_id=context.tenant_id,
            ).first()
        except (DjangoValidationError, ValueError):
            run = None
        except DatabaseError as error:
            raise map_read_failure(context=context, error=error) from error
        if run is None:
            raise NotFoundError(
                code="transformation_run_not_found",
                public_message="The transformation run was not found.",
                correlation_id=context.correlation_id,
            )
        return run


def _calculate_progress(*, run_status: str, completed_steps: int, total_steps: int) -> float:
    if run_status == RunStatus.SUCCEEDED.value:
        return 1.0
    if total_steps == 0:
        return 0.0
    return completed_steps / total_steps


def _calculate_finalization_seconds(outputs: tuple[TransformationRunOutput, ...]) -> float:
    duration = 0.0
    for output in outputs:
        if output.materialization_started_at is not None and output.completed_at is not None:
            duration += max(
                (output.completed_at - output.materialization_started_at).total_seconds(),
                0.0,
            )
    return duration
