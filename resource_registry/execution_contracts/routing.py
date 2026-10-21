"""Define pure deterministic routing over exact conformance declarations."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from resource_registry.capability_implementation_bindings import (
    CapabilityImplementationConformanceDeclaration,
    CapabilityImplementationConformanceDeclarationSet,
)

from .contracts import ExecutionRequest
from .errors import ExecutionRequestValidationError


@dataclass(frozen=True, slots=True)
class ExecutionRouteDecision:
    """Carry a deterministic exact implementation choice without invocation."""

    chosen: CapabilityImplementationConformanceDeclaration
    rejected_implementation_references: tuple[str, ...]
    checksum: str


def route_execution_request(
    request: ExecutionRequest,
    declarations: CapabilityImplementationConformanceDeclarationSet,
) -> ExecutionRouteDecision:
    """Choose one exact conformance declaration with stable opaque-identity ordering."""
    if not isinstance(request, ExecutionRequest):
        raise ExecutionRequestValidationError("invalid execution routing request")
    if not isinstance(declarations, CapabilityImplementationConformanceDeclarationSet):
        raise ExecutionRequestValidationError("invalid execution routing declarations")
    candidates = tuple(
        sorted(
            (
                declaration
                for declaration in declarations.declarations
                if declaration.contract_ref == request.contract_ref
            ),
            key=lambda declaration: declaration.implementation_ref.value,
        )
    )
    if not candidates:
        raise ExecutionRequestValidationError("no exact execution routing candidate")
    if len({candidate.implementation_ref for candidate in candidates}) != len(candidates):
        raise ExecutionRequestValidationError("duplicate execution routing candidate")
    chosen = candidates[0]
    rejected = tuple(candidate.implementation_ref.value for candidate in candidates[1:])
    checksum = hashlib.sha256(
        f"{request.to_canonical_json()}:{chosen.implementation_ref.value}:"
        f"{chosen.conformance_evidence_ref.value}".encode()
    ).hexdigest()
    return ExecutionRouteDecision(chosen, rejected, checksum)
