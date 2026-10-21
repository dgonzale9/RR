from __future__ import annotations

from dataclasses import replace

from resource_registry.events.audit_service import AuditService
from resource_registry.events.event_types import EventType
from resource_registry.models import TransformationRun
from resource_registry.transformations.operational.authorization import (
    TransformationAuthorizationService,
)
from resource_registry.transformations.operational.contracts import (
    TransformationAuthorizationContext,
)
from resource_registry.transformations.operational.logging import (
    TransformationOperationalLogContextBuilder,
    get_transformation_operational_logger,
)
from resource_registry.transformations.operational.metrics import TransformationOperationalMetricsPort
from resource_registry.transformations.runs import (
    TransformationRunCreationRequest,
    TransformationRunCreationResult,
    TransformationRunCreationService,
)


class AuthorizedTransformationRunCreationService:
    """Authorize and persist one transformation run through the Story 4 boundary."""

    def __init__(
        self,
        *,
        authorization_service: TransformationAuthorizationService,
        creation_service: TransformationRunCreationService,
        audit_service: AuditService,
        metrics: TransformationOperationalMetricsPort,
        log_context_builder: TransformationOperationalLogContextBuilder,
    ) -> None:
        self._authorization_service = authorization_service
        self._creation_service = creation_service
        self._audit_service = audit_service
        self._metrics = metrics
        self._log_context_builder = log_context_builder
        self._logger = get_transformation_operational_logger()

    def create_run(
        self,
        *,
        context: TransformationAuthorizationContext,
        request: TransformationRunCreationRequest,
    ) -> TransformationRunCreationResult:
        """Authorize the exact plan and persist its immutable security decision."""
        try:
            decision = self._authorization_service.authorize_run_creation(
                context=context,
                plan=request.execution_plan,
                output_policies=request.output_policies,
            )
        except Exception as error:
            self._record_authorization_metric(
                outcome="denied",
                error_code=error.__class__.__name__,
            )
            self._logger.warning(
                "Transformation run authorization denied",
                extra=self._log_context_builder.build(
                    values={
                        "tenant_id": context.tenant_id,
                        "definition_id": str(request.definition_id),
                        "definition_version_id": str(request.definition_version_id),
                        "correlation_id": context.correlation_id,
                        "outcome": "denied",
                        "stable_error_code": error.__class__.__name__,
                    },
                ),
            )
            raise
        self._record_authorization_metric(outcome="allowed")
        result = self._creation_service.create_run(
            request=replace(
                request,
                authorization_decision=decision,
                correlation_id=context.correlation_id,
            ),
        )
        if result.was_created:
            run = TransformationRun.objects.get(id=result.run_id, tenant_id=result.tenant_id)
            self._audit_run_created(run=run, actor=decision.authorized_actor)
            self._logger.info(
                "Transformation run created",
                extra=self._log_context_builder.build_for_run(
                    run=run,
                    outcome="queued",
                ),
            )
        return result

    def _audit_run_created(self, *, run: TransformationRun, actor: str) -> None:
        try:
            self._audit_service.record_transformation_event(
                event_type=EventType.RUN_CREATED,
                tenant_id=str(run.tenant_id),
                run_id=str(run.id),
                correlation_id=run.correlation_id,
                transition="CREATED_TO_QUEUED",
                actor=actor,
                metadata={
                    "definition_id": str(run.definition_id),
                    "definition_version_id": str(run.definition_version_id),
                },
            )
        except Exception:
            return

    def _record_authorization_metric(self, *, outcome: str, error_code: str = "") -> None:
        try:
            self._metrics.record_authorization(outcome=outcome, error_code=error_code)
        except Exception:
            return
