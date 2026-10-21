from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence

from resource_registry.models import TransformationRun
from resource_registry.services.payload_sanitizer import sanitize_payload
from resource_registry.transformations.execution_plan import TransformationExecutionPlan
from resource_registry.transformations.operational.contracts import (
    TRANSFORMATION_AUTHORIZATION_CONTRACT_VERSION,
    TRANSFORMATION_AUTHORIZATION_POLICY_REVISION,
    TransformationAuthorizationContext,
    TransformationAuthorizationDecision,
    TransformationProviderOperationAuthorization,
)
from resource_registry.transformations.operational.exceptions import (
    TransformationGovernanceDeniedError,
    TransformationPermissionDeniedError,
)
from resource_registry.transformations.operational.governance import (
    TransformationSecurityPolicyResolver,
)
from resource_registry.transformations.providers import TransformationProviderRegistry
from resource_registry.transformations.runs import (
    TransformationOutputPolicy,
    TransformationOutputPolicyKind,
)
from resource_registry.utils.json_checksum import canonicalize_transformation_specification

EXECUTE_TRANSFORMATION_PERMISSION = "resource_registry.execute_transformation"
CANCEL_TRANSFORMATION_RUN_PERMISSION = "resource_registry.cancel_transformationrun"
USE_TRANSFORMATION_PROVIDER_PERMISSION = "resource_registry.use_transformation_provider"
OPERATE_TRANSFORMATION_RUN_PERMISSION = "resource_registry.operate_transformationrun"
VIEW_TRANSFORMATION_DIAGNOSTICS_PERMISSION = "resource_registry.view_transformation_diagnostics"
ADD_DATASET_PERMISSION = "resource_registry.add_dataset"
ADD_DATASET_VERSION_PERMISSION = "resource_registry.add_datasetversion"
_MAX_AUTHORIZED_ACTOR_LENGTH = 255
_MAX_CORRELATION_ID_LENGTH = 128


class TransformationAuthorizationService:
    """Authorize transformation operations through exact tenant and governance intersections."""

    def __init__(
        self,
        *,
        policy_resolver: TransformationSecurityPolicyResolver,
        provider_registry: TransformationProviderRegistry,
    ) -> None:
        self._policy_resolver = policy_resolver
        self._provider_registry = provider_registry

    def authorize_run_creation(
        self,
        *,
        context: TransformationAuthorizationContext,
        plan: TransformationExecutionPlan,
        output_policies: Sequence[TransformationOutputPolicy],
    ) -> TransformationAuthorizationDecision:
        """Authorize one exact plan and return its immutable decision snapshot."""
        self._assert_context(context=context)
        if context.tenant_id != plan.tenant_id:
            raise TransformationPermissionDeniedError(
                "Transformation plan tenant does not match authorization context.",
            )
        self._require_permission(context, EXECUTE_TRANSFORMATION_PERMISSION)
        self._require_permission(context, USE_TRANSFORMATION_PROVIDER_PERMISSION)
        self._assert_definition_scope(context=context, definition_id=plan.definition_id)
        dataset_ids = {plan_input.dataset_id for plan_input in plan.inputs}
        target_dataset_ids = {
            str(policy.target_dataset_id)
            for policy in output_policies
            if policy.target_dataset_id is not None
        }
        all_dataset_ids = dataset_ids | target_dataset_ids
        self._assert_dataset_scope(context=context, dataset_ids=all_dataset_ids)
        self._assert_output_permissions(context=context, policies=output_policies)
        policy = self._policy_resolver.resolve(
            tenant_id=context.tenant_id,
            dataset_ids=tuple(sorted(all_dataset_ids)),
        )
        requested_operations = frozenset(
            TransformationProviderOperationAuthorization(
                provider_key=step.provider_key,
                provider_version=step.provider_version,
                operation_key=step.operation_key,
                operation_version=step.operation_version,
            )
            for step in plan.steps
        )
        for operation in requested_operations:
            self._provider_registry.resolve_operation(
                provider_key=operation.provider_key,
                provider_version=operation.provider_version,
                operation_key=operation.operation_key,
                operation_version=operation.operation_version,
            )
        if not requested_operations.issubset(policy.allowed_operations):
            raise TransformationGovernanceDeniedError(
                "Exact transformation provider operation is not allowed.",
            )
        requested_output_kinds = frozenset(item.policy_kind.value for item in output_policies)
        if not requested_output_kinds.issubset(policy.allowed_output_policy_kinds):
            raise TransformationGovernanceDeniedError(
                "Transformation output policy is not allowed.",
            )
        snapshot = {
            "contract_version": TRANSFORMATION_AUTHORIZATION_CONTRACT_VERSION,
            "authorization_policy_revision": TRANSFORMATION_AUTHORIZATION_POLICY_REVISION,
            "tenant_id": context.tenant_id,
            "authorized_actor": context.actor,
            "principal_kind": context.principal_kind.value,
            "definition_id": plan.definition_id,
            "definition_version_id": plan.definition_version_id,
            "dataset_ids": sorted(all_dataset_ids),
            "permissions": sorted(context.permissions),
            "provider_operations": [item.to_dict() for item in sorted(requested_operations)],
            "output_policy_kinds": sorted(requested_output_kinds),
            "governance_policy_revision": policy.tenant_policy_revision,
            "governance_provenance": [dict(item) for item in policy.policy_provenance],
        }
        sanitized = sanitize_payload(snapshot)
        if not isinstance(sanitized, Mapping):
            raise TransformationPermissionDeniedError(
                "Authorization snapshot could not be safely represented.",
            )
        checksum = hashlib.sha256(
            canonicalize_transformation_specification(sanitized).encode("utf-8"),
        ).hexdigest()
        return TransformationAuthorizationDecision(
            snapshot=sanitized,
            checksum=checksum,
            authorization_policy_revision=TRANSFORMATION_AUTHORIZATION_POLICY_REVISION,
            governance_policy_revision=policy.tenant_policy_revision,
            authorized_actor=context.actor,
        )

    def authorize_cancellation(
        self,
        *,
        context: TransformationAuthorizationContext,
        run: TransformationRun,
    ) -> None:
        """Authorize cancellation of one exact tenant-scoped run."""
        self._assert_run_scope(context=context, run=run)
        self._require_permission(context, CANCEL_TRANSFORMATION_RUN_PERMISSION)

    def authorize_operation(
        self,
        *,
        context: TransformationAuthorizationContext,
        run: TransformationRun,
    ) -> None:
        """Authorize an internal operational action on one run."""
        self._assert_run_scope(context=context, run=run)
        self._require_permission(context, OPERATE_TRANSFORMATION_RUN_PERMISSION)

    def authorize_diagnostics(self, *, context: TransformationAuthorizationContext) -> None:
        """Authorize tenant-scoped operational diagnostics."""
        self._assert_context(context=context)
        self._require_permission(context, VIEW_TRANSFORMATION_DIAGNOSTICS_PERMISSION)

    def is_available(self) -> bool:
        """Return whether the authorization service is configured."""
        return True

    def _assert_run_scope(
        self,
        *,
        context: TransformationAuthorizationContext,
        run: TransformationRun,
    ) -> None:
        self._assert_context(context=context)
        if str(run.tenant_id) != context.tenant_id:
            raise TransformationPermissionDeniedError(
                "Cross-tenant transformation operation is not allowed.",
            )
        self._assert_definition_scope(context=context, definition_id=str(run.definition_id))

    def _assert_context(self, *, context: TransformationAuthorizationContext) -> None:
        if (
            not context.tenant_id
            or not context.actor
            or not context.correlation_id
            or len(context.actor) > _MAX_AUTHORIZED_ACTOR_LENGTH
            or len(context.correlation_id) > _MAX_CORRELATION_ID_LENGTH
            or any(
                value != value.strip()
                for value in (context.tenant_id, context.actor, context.correlation_id)
            )
        ):
            raise TransformationPermissionDeniedError(
                "Complete exact transformation authorization context is required.",
            )

    def _require_permission(
        self,
        context: TransformationAuthorizationContext,
        permission: str,
    ) -> None:
        if permission not in context.permissions:
            raise TransformationPermissionDeniedError(
                f"Missing transformation permission: {permission}.",
            )

    def _assert_definition_scope(
        self,
        *,
        context: TransformationAuthorizationContext,
        definition_id: str,
    ) -> None:
        allowed = context.allowed_transformation_definition_ids
        if allowed is not None and definition_id not in allowed:
            raise TransformationPermissionDeniedError(
                "Transformation definition is outside the principal scope.",
            )

    def _assert_dataset_scope(
        self,
        *,
        context: TransformationAuthorizationContext,
        dataset_ids: set[str],
    ) -> None:
        allowed = context.allowed_dataset_ids
        if allowed is not None and not dataset_ids.issubset(allowed):
            raise TransformationPermissionDeniedError(
                "Transformation dataset is outside the principal scope.",
            )

    def _assert_output_permissions(
        self,
        *,
        context: TransformationAuthorizationContext,
        policies: Sequence[TransformationOutputPolicy],
    ) -> None:
        if any(
            policy.policy_kind == TransformationOutputPolicyKind.NEW_DATASET for policy in policies
        ):
            self._require_permission(context, ADD_DATASET_PERMISSION)
        if any(
            policy.policy_kind == TransformationOutputPolicyKind.NEW_DATASET_VERSION
            for policy in policies
        ):
            self._require_permission(context, ADD_DATASET_VERSION_PERMISSION)
