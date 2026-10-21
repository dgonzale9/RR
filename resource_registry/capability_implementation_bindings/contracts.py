"""Define pure immutable capability implementation conformance declarations."""

from __future__ import annotations

from dataclasses import dataclass

from resource_registry.capability_definitions import CapabilityContractRef

from .errors import CapabilityImplementationBindingValidationError

__all__ = (  # noqa: RUF022
    "CapabilityImplementationRef",
    "ConformanceEvidenceRef",
    "CapabilityImplementationConformanceDeclaration",
    "CapabilityImplementationConformanceDeclarationSet",
)

_MAXIMUM_REFERENCE_LENGTH = 512


@dataclass(frozen=True, slots=True)
class CapabilityImplementationRef:
    """Identify one exact opaque implementation supplied by the provider/runtime boundary.

    Args:
        value: Exact nonblank opaque implementation identity without normalization.

    Raises:
        CapabilityImplementationBindingValidationError: If the reference is not a bounded string.
    """

    value: str

    def __post_init__(self) -> None:
        """Validate the opaque implementation identity without resolving it.

        Raises:
            CapabilityImplementationBindingValidationError: If the reference is invalid.
        """
        if not _is_valid_opaque_reference(self.value):
            raise CapabilityImplementationBindingValidationError(
                "invalid capability implementation reference"
            )


@dataclass(frozen=True, slots=True)
class ConformanceEvidenceRef:
    """Identify one exact immutable conformance-evidence record.

    Args:
        value: Exact nonblank opaque evidence identity without normalization.

    Raises:
        CapabilityImplementationBindingValidationError: If the reference is not a bounded string.
    """

    value: str

    def __post_init__(self) -> None:
        """Validate the opaque conformance-evidence identity without evaluating it.

        Raises:
            CapabilityImplementationBindingValidationError: If the reference is invalid.
        """
        if not _is_valid_opaque_reference(self.value):
            raise CapabilityImplementationBindingValidationError(
                "invalid conformance evidence reference"
            )


@dataclass(frozen=True, slots=True)
class CapabilityImplementationConformanceDeclaration:
    """Declare one certified implementation's conformance to one exact capability contract.

    Args:
        implementation_ref: Exact opaque implementation identity from the provider/runtime owner.
        contract_ref: Exact portable capability contract version satisfied by the implementation.
        conformance_evidence_ref: Exact immutable evidence supporting the conformance claim.

    Raises:
        CapabilityImplementationBindingValidationError: If a declaration component is invalid.
    """

    implementation_ref: CapabilityImplementationRef
    contract_ref: CapabilityContractRef
    conformance_evidence_ref: ConformanceEvidenceRef

    def __post_init__(self) -> None:
        """Validate declaration components without resolving or invoking an implementation.

        Raises:
            CapabilityImplementationBindingValidationError: If a declaration component is invalid.
        """
        if not isinstance(self.implementation_ref, CapabilityImplementationRef):
            raise CapabilityImplementationBindingValidationError(
                "invalid capability implementation binding implementation"
            )
        if not isinstance(self.contract_ref, CapabilityContractRef):
            raise CapabilityImplementationBindingValidationError(
                "invalid capability implementation binding contract"
            )
        if not isinstance(self.conformance_evidence_ref, ConformanceEvidenceRef):
            raise CapabilityImplementationBindingValidationError(
                "invalid capability implementation binding evidence"
            )


@dataclass(frozen=True, slots=True)
class CapabilityImplementationConformanceDeclarationSet:
    """Validate immutable exact capability implementation conformance declarations.

    Args:
        declarations: Exact immutable conformance declarations in declaration order.

    Raises:
        CapabilityImplementationBindingValidationError: If declarations are malformed or duplicate.
    """

    declarations: tuple[CapabilityImplementationConformanceDeclaration, ...]

    def __post_init__(self) -> None:
        """Validate declaration exactness and duplicate implementation/contract associations.

        Raises:
            CapabilityImplementationBindingValidationError: If declarations are invalid or
                duplicate.
        """
        if type(self.declarations) is not tuple:
            raise CapabilityImplementationBindingValidationError(
                "invalid capability implementation binding declarations"
            )

        declared_associations: set[tuple[CapabilityImplementationRef, CapabilityContractRef]] = (
            set()
        )
        for declaration in self.declarations:
            if not isinstance(declaration, CapabilityImplementationConformanceDeclaration):
                raise CapabilityImplementationBindingValidationError(
                    "invalid capability implementation binding declaration"
                )

            association = (declaration.implementation_ref, declaration.contract_ref)
            if association in declared_associations:
                raise CapabilityImplementationBindingValidationError(
                    "duplicate capability implementation binding declaration"
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
