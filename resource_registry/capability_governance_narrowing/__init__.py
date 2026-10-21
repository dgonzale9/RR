"""Expose pure immutable Governance/Entitlement binding-narrowing declarations."""

from .contracts import (
    CapabilityBindingNarrowingDeclaration,
    CapabilityBindingNarrowingDeclarationSet,
    ExistingCapabilityBindingRef,
    GovernanceNarrowingConstraintRef,
)
from .errors import CapabilityBindingNarrowingValidationError

__all__ = (  # noqa: RUF022
    "CapabilityBindingNarrowingValidationError",
    "ExistingCapabilityBindingRef",
    "GovernanceNarrowingConstraintRef",
    "CapabilityBindingNarrowingDeclaration",
    "CapabilityBindingNarrowingDeclarationSet",
)
