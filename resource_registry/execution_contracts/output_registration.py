"""Define pure owner-bound output-registration and lineage admission contracts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum

from resource_registry.authorization.contracts import RegistryAuthorizationContext
from resource_registry.registry_core import (
    ArtifactKey,
    OwnerDescriptor,
    ResourceKey,
    ResourceVersionKey,
)

from .contracts import ExecutionResult, ExecutionResultStatus
from .errors import ExecutionRequestValidationError
from .lifecycle import ExecutionInstanceRef, ExecutionLifecycleSnapshot, ExecutionLifecycleState

__all__ = (
    "ExecutionLineageReference",
    "ExecutionOutputRegistrationDecision",
    "ExecutionOutputRegistrationRequest",
    "ExecutionOutputRegistrationStatus",
    "evaluate_execution_output_registration",
)

ExecutionOwnedOutputReference = ResourceKey | ResourceVersionKey | ArtifactKey


class ExecutionOutputRegistrationStatus(StrEnum):
    """Classify a pure output-registration admission decision."""

    ADMITTED = "admitted"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class ExecutionLineageReference:
    """Bind one exact source reference to one owner-issued output reference."""

    source: ExecutionOwnedOutputReference
    output: ExecutionOwnedOutputReference

    def __post_init__(self) -> None:
        """Validate exact same-namespace lineage references without creating lineage."""
        if not isinstance(
            self.source, (ResourceKey, ResourceVersionKey, ArtifactKey)
        ) or not isinstance(self.output, (ResourceKey, ResourceVersionKey, ArtifactKey)):
            raise ExecutionRequestValidationError("invalid execution lineage reference")
        if (
            _resource_for_reference(self.source).namespace
            != _resource_for_reference(self.output).namespace
        ):
            raise ExecutionRequestValidationError("execution lineage namespaces do not match")


@dataclass(frozen=True, slots=True)
class ExecutionOutputRegistrationRequest:
    """Carry one idempotent owner-registration intent without mutating an owner aggregate."""

    instance_ref: ExecutionInstanceRef
    snapshot: ExecutionLifecycleSnapshot
    result: ExecutionResult
    owner: OwnerDescriptor
    outputs: tuple[ExecutionOwnedOutputReference, ...]
    lineage: tuple[ExecutionLineageReference, ...] = ()

    def __post_init__(self) -> None:
        """Validate exact result, owner, output, and lineage evidence without registration."""
        if not isinstance(self.instance_ref, ExecutionInstanceRef) or not isinstance(
            self.snapshot, ExecutionLifecycleSnapshot
        ):
            raise ExecutionRequestValidationError("invalid execution output registration instance")
        if self.instance_ref != self.snapshot.instance_ref or not isinstance(
            self.result, ExecutionResult
        ):
            raise ExecutionRequestValidationError("invalid execution output registration result")
        if not isinstance(self.owner, OwnerDescriptor):
            raise ExecutionRequestValidationError("invalid execution output registration owner")
        if (
            type(self.outputs) is not tuple
            or not self.outputs
            or any(
                not isinstance(output, (ResourceKey, ResourceVersionKey, ArtifactKey))
                for output in self.outputs
            )
        ):
            raise ExecutionRequestValidationError("invalid execution output registration outputs")
        if type(self.lineage) is not tuple or any(
            not isinstance(reference, ExecutionLineageReference) for reference in self.lineage
        ):
            raise ExecutionRequestValidationError("invalid execution output registration lineage")


@dataclass(frozen=True, slots=True)
class ExecutionOutputRegistrationDecision:
    """Carry a pure owner-registration decision and stable idempotency key."""

    status: ExecutionOutputRegistrationStatus
    request: ExecutionOutputRegistrationRequest
    idempotency_key: str | None
    rejection_reason: str | None = None


def evaluate_execution_output_registration(
    request: ExecutionOutputRegistrationRequest,
    authorization_context: RegistryAuthorizationContext,
) -> ExecutionOutputRegistrationDecision:
    """Admit an exact owner-registration command without performing owner mutation."""
    if not isinstance(request, ExecutionOutputRegistrationRequest):
        raise ExecutionRequestValidationError("invalid execution output registration request")
    if not isinstance(authorization_context, RegistryAuthorizationContext):
        raise ExecutionRequestValidationError(
            "invalid execution output registration authorization context"
        )
    if request.snapshot.state is not ExecutionLifecycleState.COMPLETED:
        return _reject(request, "execution output registration requires completed lifecycle")
    if request.result.status not in {ExecutionResultStatus.SUCCESS, ExecutionResultStatus.PARTIAL}:
        return _reject(request, "execution output registration requires successful result")
    namespace = request.snapshot.namespace
    if namespace.value not in authorization_context.authorized_namespaces.namespace_ids:
        return _reject(request, "execution output registration namespace is not authorized")
    if any(_resource_for_reference(output).namespace != namespace for output in request.outputs):
        return _reject(request, "execution output registration output namespace does not match")
    if any(
        _resource_for_reference(reference.source).namespace != namespace
        or _resource_for_reference(reference.output).namespace != namespace
        for reference in request.lineage
    ):
        return _reject(request, "execution output registration lineage namespace does not match")
    material = ":".join(
        (
            request.instance_ref.value,
            request.owner.bounded_context_identifier,
            *sorted(_reference_key(output) for output in request.outputs),
        )
    )
    return ExecutionOutputRegistrationDecision(
        ExecutionOutputRegistrationStatus.ADMITTED,
        request,
        hashlib.sha256(material.encode("utf-8")).hexdigest(),
    )


def _reject(
    request: ExecutionOutputRegistrationRequest, reason: str
) -> ExecutionOutputRegistrationDecision:
    """Build one deterministic rejected registration decision."""
    return ExecutionOutputRegistrationDecision(
        ExecutionOutputRegistrationStatus.REJECTED, request, None, reason
    )


def _resource_for_reference(reference: ExecutionOwnedOutputReference) -> ResourceKey:
    """Resolve the exact ResourceKey carried by an owner-issued output reference."""
    return reference if isinstance(reference, ResourceKey) else reference.resource


def _reference_key(reference: ExecutionOwnedOutputReference) -> str:
    """Build a stable opaque registration-key component for one exact output reference."""
    resource = _resource_for_reference(reference)
    return f"{resource.namespace.value}:{resource.resource_type.value}:{resource.resource_id}:{reference!r}"
