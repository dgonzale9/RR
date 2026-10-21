"""Define pure fail-closed execution admission and idempotency contracts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum

from resource_registry.authorization.contracts import RegistryAuthorizationContext
from resource_registry.registry_core import ArtifactKey, ResourceKey, ResourceVersionKey

from .contracts import ExecutionRequest
from .errors import ExecutionRequestValidationError


class ExecutionAdmissionStatus(StrEnum):
    """Classify a pure execution-admission outcome."""

    ADMITTED = "admitted"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class ExecutionAdmissionResult:
    """Carry a fail-closed admission outcome without creating an execution instance."""

    status: ExecutionAdmissionStatus
    idempotency_scope: str | None
    rejection_reason: str | None = None


def evaluate_execution_admission(
    request: ExecutionRequest,
    authorization_context: RegistryAuthorizationContext,
) -> ExecutionAdmissionResult:
    """Evaluate exact request scope against server-derived namespace authorization.

    Args:
        request: Immutable untrusted execution request.
        authorization_context: Server-derived authorized namespace context.

    Returns:
        An admitted scope digest or a fail-closed rejection result.
    """
    if not isinstance(request, ExecutionRequest):
        raise ExecutionRequestValidationError("invalid execution admission request")
    if not isinstance(authorization_context, RegistryAuthorizationContext):
        raise ExecutionRequestValidationError("invalid execution admission context")

    namespace_ids = authorization_context.authorized_namespaces.namespace_ids
    for input_reference in request.inputs:
        target = input_reference.target
        resource = _resource_for_target(target)
        if resource is None or resource.namespace.value not in namespace_ids:
            return ExecutionAdmissionResult(
                ExecutionAdmissionStatus.REJECTED,
                None,
                "execution input namespace is not authorized",
            )

    scope_material = (
        f"{authorization_context.authorized_workspaces.organization_id}:"
        f"{authorization_context.authorized_workspaces.user_id}:"
        f"{request.to_canonical_json()}"
    )
    return ExecutionAdmissionResult(
        ExecutionAdmissionStatus.ADMITTED,
        hashlib.sha256(scope_material.encode("utf-8")).hexdigest(),
    )


def _resource_for_target(
    target: ResourceKey | ResourceVersionKey | ArtifactKey | object,
) -> ResourceKey | None:
    """Resolve a resource identity only for exact Registry Core target values."""
    if isinstance(target, ResourceKey):
        return target
    if isinstance(target, ResourceVersionKey):
        return target.resource
    if isinstance(target, ArtifactKey):
        return target.resource
    return None
