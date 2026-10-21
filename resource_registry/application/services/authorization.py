from __future__ import annotations

from resource_registry.application.contracts import AuthorizationError, TransformationExecutionRequest
from resource_registry.application.services.results import (
    TransformationExecutionCallerContext,
    TransformationExecutionGovernancePort,
)

EXECUTE_TRANSFORMATION_PERMISSION = "resource_registry.execute_transformation"
USE_TRANSFORMATION_PROVIDER_PERMISSION = "resource_registry.use_transformation_provider"


class TransformationExecutionEntryAuthorizationService:
    """Authorize the public application entry before runtime defense-in-depth checks."""

    def __init__(self, *, governance: TransformationExecutionGovernancePort) -> None:
        self._governance = governance

    def authorize(
        self,
        *,
        request: TransformationExecutionRequest,
        caller: TransformationExecutionCallerContext,
    ) -> None:
        """Authorize tenant, permissions, definition, datasets, and governance availability."""
        if caller.tenant_id != request.tenant_id:
            self._deny(correlation_id=request.correlation_id)
        if (
            not isinstance(caller.actor, str)
            or not caller.actor
            or caller.actor != caller.actor.strip()
        ):
            self._deny(correlation_id=request.correlation_id)
        if caller.principal_kind not in {"USER", "SERVICE"}:
            self._deny(correlation_id=request.correlation_id)
        required_permissions = {
            EXECUTE_TRANSFORMATION_PERMISSION,
            USE_TRANSFORMATION_PROVIDER_PERMISSION,
        }
        if not isinstance(caller.permissions, frozenset) or not required_permissions.issubset(
            caller.permissions,
        ):
            self._deny(correlation_id=request.correlation_id)
        allowed_definitions = caller.allowed_definition_ids
        if allowed_definitions is not None and (
            not isinstance(allowed_definitions, frozenset)
            or request.definition_id not in allowed_definitions
        ):
            self._deny(correlation_id=request.correlation_id)
        allowed_datasets = caller.allowed_dataset_ids
        requested_datasets = {binding.dataset_id for binding in request.input_bindings}
        if allowed_datasets is not None and (
            not isinstance(allowed_datasets, frozenset)
            or not requested_datasets.issubset(allowed_datasets)
        ):
            self._deny(correlation_id=request.correlation_id)
        try:
            governance_available = self._governance.is_available(tenant_id=request.tenant_id)
        except Exception:
            governance_available = False
        if not governance_available:
            raise AuthorizationError(
                code="transformation_governance_unavailable",
                public_message="Transformation governance is unavailable.",
                retryable=True,
                correlation_id=request.correlation_id,
            )

    @staticmethod
    def _deny(*, correlation_id: str) -> None:
        raise AuthorizationError(
            code="transformation_execution_not_authorized",
            public_message="The transformation execution is not authorized.",
            correlation_id=correlation_id,
        )
