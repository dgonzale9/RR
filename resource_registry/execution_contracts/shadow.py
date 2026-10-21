"""Define non-mutating dry-route and shadow-plan comparison contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from resource_registry.authorization.contracts import RegistryAuthorizationContext
from resource_registry.capability_implementation_bindings import (
    CapabilityImplementationConformanceDeclarationSet,
)

from .admission import ExecutionAdmissionStatus, evaluate_execution_admission
from .contracts import ExecutionRequest
from .errors import ExecutionRequestValidationError
from .routing import ExecutionRouteDecision, route_execution_request

__all__ = (
    "ExecutionLegacyRouteEvidence",
    "ExecutionShadowPlan",
    "ExecutionShadowPlanStatus",
    "build_execution_shadow_plan",
)


class ExecutionShadowPlanStatus(StrEnum):
    """Classify a non-authoritative shadow-plan result."""

    PROPOSED = "proposed"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class ExecutionLegacyRouteEvidence:
    """Carry opaque legacy route evidence for observational comparison only."""

    route_identifier: str
    checksum: str

    def __post_init__(self) -> None:
        """Validate bounded opaque legacy evidence without resolving legacy behavior."""
        if not all(
            isinstance(value, str) and value.strip() and len(value) <= 512
            for value in (self.route_identifier, self.checksum)
        ):
            raise ExecutionRequestValidationError("invalid execution legacy route evidence")


@dataclass(frozen=True, slots=True)
class ExecutionShadowPlan:
    """Carry a non-authoritative route proposal and optional legacy comparison evidence."""

    status: ExecutionShadowPlanStatus
    route_decision: ExecutionRouteDecision | None
    legacy_evidence: ExecutionLegacyRouteEvidence | None
    differences: tuple[str, ...]
    rejection_reason: str | None = None


def build_execution_shadow_plan(
    request: ExecutionRequest,
    authorization_context: RegistryAuthorizationContext,
    declarations: CapabilityImplementationConformanceDeclarationSet,
    legacy_evidence: ExecutionLegacyRouteEvidence | None = None,
) -> ExecutionShadowPlan:
    """Build a deterministic non-mutating route proposal without execution authority."""
    if not isinstance(request, ExecutionRequest):
        raise ExecutionRequestValidationError("invalid execution shadow request")
    if not isinstance(authorization_context, RegistryAuthorizationContext):
        raise ExecutionRequestValidationError("invalid execution shadow authorization context")
    if not isinstance(declarations, CapabilityImplementationConformanceDeclarationSet):
        raise ExecutionRequestValidationError("invalid execution shadow declarations")
    if legacy_evidence is not None and not isinstance(
        legacy_evidence, ExecutionLegacyRouteEvidence
    ):
        raise ExecutionRequestValidationError("invalid execution shadow legacy evidence")
    admission = evaluate_execution_admission(request, authorization_context)
    if admission.status is ExecutionAdmissionStatus.REJECTED:
        return ExecutionShadowPlan(
            ExecutionShadowPlanStatus.REJECTED,
            None,
            legacy_evidence,
            (),
            admission.rejection_reason,
        )
    try:
        route = route_execution_request(request, declarations)
    except ExecutionRequestValidationError as error:
        return ExecutionShadowPlan(
            ExecutionShadowPlanStatus.REJECTED, None, legacy_evidence, (), str(error)
        )
    differences = (
        ()
        if legacy_evidence is None or legacy_evidence.checksum == route.checksum
        else ("route_checksum_differs",)
    )
    return ExecutionShadowPlan(
        ExecutionShadowPlanStatus.PROPOSED, route, legacy_evidence, differences
    )
