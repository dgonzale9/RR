from __future__ import annotations

from typing import Protocol

from resource_registry.application.contracts import AuthorizationError, ValidationError
from resource_registry.application.read.dtos import TransformationReadContext

VIEW_TRANSFORMATION_RUN_PERMISSION = "resource_registry.view_transformationrun"
VIEW_TRANSFORMATION_DIAGNOSTICS_PERMISSION = "resource_registry.view_transformation_diagnostics"


class TransformationReadGovernancePort(Protocol):
    """Expose tenant governance availability without leaking runtime policy contracts."""

    def is_available(self, *, tenant_id: str) -> bool:
        """Return whether valid tenant transformation governance is available."""


class TransformationReadAuthorizationService:
    """Authorize tenant-scoped application reads through permissions and governance."""

    def __init__(self, *, governance: TransformationReadGovernancePort) -> None:
        self._governance = governance

    def authorize_run_list(self, *, context: TransformationReadContext) -> None:
        """Authorize listing transformation runs for one tenant."""
        self._authorize_context(context=context, permission=VIEW_TRANSFORMATION_RUN_PERMISSION)

    def authorize_run(
        self,
        *,
        context: TransformationReadContext,
        definition_id: str,
    ) -> None:
        """Authorize one tenant-owned transformation run definition scope."""
        self.authorize_run_list(context=context)
        allowed_ids = context.allowed_definition_ids
        if allowed_ids is not None and definition_id not in allowed_ids:
            raise AuthorizationError(
                code="transformation_read_not_authorized",
                public_message="The transformation resource is not available.",
                correlation_id=context.correlation_id,
            )

    def authorize_output_dataset(
        self,
        *,
        context: TransformationReadContext,
        dataset_id: str | None,
    ) -> None:
        """Authorize an optional dataset-backed output scope."""
        if dataset_id is None:
            return
        allowed_ids = context.allowed_dataset_ids
        if allowed_ids is not None and dataset_id not in allowed_ids:
            raise AuthorizationError(
                code="transformation_output_read_not_authorized",
                public_message="The transformation output is not available.",
                correlation_id=context.correlation_id,
            )

    def authorize_diagnostics(self, *, context: TransformationReadContext) -> None:
        """Authorize tenant-scoped transformation diagnostics."""
        self._authorize_context(
            context=context,
            permission=VIEW_TRANSFORMATION_DIAGNOSTICS_PERMISSION,
        )

    def authorize_run_diagnostics(
        self,
        *,
        context: TransformationReadContext,
        definition_id: str,
    ) -> None:
        """Authorize diagnostics for one tenant-owned definition scope."""
        self.authorize_diagnostics(context=context)
        allowed_ids = context.allowed_definition_ids
        if allowed_ids is not None and definition_id not in allowed_ids:
            raise AuthorizationError(
                code="transformation_read_not_authorized",
                public_message="The transformation resource is not available.",
                correlation_id=context.correlation_id,
            )

    def _authorize_context(self, *, context: TransformationReadContext, permission: str) -> None:
        values = (context.tenant_id, context.actor, context.correlation_id)
        if any(not value or value != value.strip() for value in values):
            raise ValidationError(
                code="invalid_transformation_read_context",
                public_message="The transformation read context is invalid.",
                correlation_id=context.correlation_id or None,
            )
        if permission not in context.permissions:
            raise AuthorizationError(
                code="transformation_read_not_authorized",
                public_message="The transformation resource is not available.",
                correlation_id=context.correlation_id,
            )
        try:
            governance_available = self._governance.is_available(tenant_id=context.tenant_id)
        except Exception:
            governance_available = False
        if not governance_available:
            raise AuthorizationError(
                code="transformation_governance_unavailable",
                public_message="Transformation governance is unavailable.",
                retryable=True,
                correlation_id=context.correlation_id,
            )
