"""Define pure immutable Governance/Entitlement narrowing declarations for existing bindings."""

from __future__ import annotations

from dataclasses import dataclass

from resource_registry.capability_definitions import CapabilityContractRef

from .errors import CapabilityBindingNarrowingValidationError

__all__ = (  # noqa: RUF022
    "ExistingCapabilityBindingRef",
    "GovernanceNarrowingConstraintRef",
    "CapabilityBindingNarrowingDeclaration",
    "CapabilityBindingNarrowingDeclarationSet",
)

_MAXIMUM_REFERENCE_LENGTH = 512


@dataclass(frozen=True, slots=True)
class ExistingCapabilityBindingRef:
    """Identify one exact opaque existing binding supplied by its authoritative owner.

    Args:
        value: Exact nonblank owner-issued binding reference without normalization.
        contract_ref: Exact capability contract reference carried by the existing binding.

    Raises:
        CapabilityBindingNarrowingValidationError: If either reference component is invalid.
    """

    value: str
    contract_ref: CapabilityContractRef

    def __post_init__(self) -> None:
        """Validate the supplied binding reference without resolving its owner or existence.

        Raises:
            CapabilityBindingNarrowingValidationError: If either reference component is invalid.
        """
        if not _is_valid_opaque_reference(self.value):
            raise CapabilityBindingNarrowingValidationError(
                "invalid existing capability binding reference"
            )
        if not isinstance(self.contract_ref, CapabilityContractRef):
            raise CapabilityBindingNarrowingValidationError(
                "invalid existing capability binding contract"
            )


@dataclass(frozen=True, slots=True)
class GovernanceNarrowingConstraintRef:
    """Identify one exact immutable Governance/Entitlement narrowing constraint.

    Args:
        value: Exact nonblank constraint revision reference without normalization.

    Raises:
        CapabilityBindingNarrowingValidationError: If the reference is not a bounded string.
    """

    value: str

    def __post_init__(self) -> None:
        """Validate the constraint reference without evaluating policy or entitlement state.

        Raises:
            CapabilityBindingNarrowingValidationError: If the reference is invalid.
        """
        if not _is_valid_opaque_reference(self.value):
            raise CapabilityBindingNarrowingValidationError(
                "invalid governance narrowing constraint reference"
            )


@dataclass(frozen=True, slots=True)
class CapabilityBindingNarrowingDeclaration:
    """Associate one existing binding with one Governance/Entitlement narrowing constraint.

    Args:
        existing_binding_ref: Exact existing owner-issued binding subject to the constraint.
        narrowing_constraint_ref: Exact Governance/Entitlement-owned constraint reference.

    Raises:
        CapabilityBindingNarrowingValidationError: If a declaration component is invalid.
    """

    existing_binding_ref: ExistingCapabilityBindingRef
    narrowing_constraint_ref: GovernanceNarrowingConstraintRef

    def __post_init__(self) -> None:
        """Validate declaration components without resolving a live authority decision.

        Raises:
            CapabilityBindingNarrowingValidationError: If a declaration component is invalid.
        """
        if not isinstance(self.existing_binding_ref, ExistingCapabilityBindingRef):
            raise CapabilityBindingNarrowingValidationError(
                "invalid capability binding narrowing existing binding"
            )
        if not isinstance(self.narrowing_constraint_ref, GovernanceNarrowingConstraintRef):
            raise CapabilityBindingNarrowingValidationError(
                "invalid capability binding narrowing constraint"
            )


@dataclass(frozen=True, slots=True)
class CapabilityBindingNarrowingDeclarationSet:
    """Validate immutable exact Governance/Entitlement narrowing declarations.

    Args:
        declarations: Exact immutable narrowing declarations in declaration order.

    Raises:
        CapabilityBindingNarrowingValidationError: If declarations are malformed or duplicate.
    """

    declarations: tuple[CapabilityBindingNarrowingDeclaration, ...]

    def __post_init__(self) -> None:
        """Validate declaration exactness and duplicate binding/constraint associations.

        Raises:
            CapabilityBindingNarrowingValidationError: If declarations are invalid or duplicate.
        """
        if type(self.declarations) is not tuple:
            raise CapabilityBindingNarrowingValidationError(
                "invalid capability binding narrowing declarations"
            )

        declared_associations: set[
            tuple[ExistingCapabilityBindingRef, GovernanceNarrowingConstraintRef]
        ] = set()
        for declaration in self.declarations:
            if not isinstance(declaration, CapabilityBindingNarrowingDeclaration):
                raise CapabilityBindingNarrowingValidationError(
                    "invalid capability binding narrowing declaration"
                )

            association = (
                declaration.existing_binding_ref,
                declaration.narrowing_constraint_ref,
            )
            if association in declared_associations:
                raise CapabilityBindingNarrowingValidationError(
                    "duplicate capability binding narrowing declaration"
                )
            declared_associations.add(association)


def _is_valid_opaque_reference(value: object) -> bool:
    """Check whether an opaque reference is bounded without changing its exact value.

    Args:
        value: Candidate opaque reference value.

    Returns:
        Whether the value is a bounded, nonblank string.
    """
    return (
        type(value) is str and 0 < len(value) <= _MAXIMUM_REFERENCE_LENGTH and not value.isspace()
    )
