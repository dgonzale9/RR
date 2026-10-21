"""Define pure exact Provider certification to Capability conformance bindings."""

from __future__ import annotations

from dataclasses import dataclass

from resource_registry.capability_implementation_bindings import (
    CapabilityImplementationConformanceDeclaration,
)

from .certification import ProviderCertificationEvidenceRef, ProviderCertificationRecord
from .errors import ProviderCertificationBindingValidationError


@dataclass(frozen=True, slots=True)
class CertifiedProviderCapabilityBinding:
    """Associate Provider certification evidence with exact Capability conformance evidence.

    Args:
        certification_record: Exact immutable Provider certification evidence.
        conformance_declaration: Existing exact Capability conformance declaration.
    """

    certification_record: ProviderCertificationRecord
    conformance_declaration: CapabilityImplementationConformanceDeclaration

    def __post_init__(self) -> None:
        """Validate evidence association without resolving or selecting a Provider.

        Raises:
            ProviderCertificationBindingValidationError: If binding evidence is malformed.
        """
        if not isinstance(self.certification_record, ProviderCertificationRecord) or not isinstance(
            self.conformance_declaration, CapabilityImplementationConformanceDeclaration
        ):
            raise ProviderCertificationBindingValidationError("invalid certified provider binding")


@dataclass(frozen=True, slots=True)
class CertifiedProviderCapabilityBindingSet:
    """Hold immutable exact Provider certification and Capability conformance bindings.

    Args:
        bindings: Exact immutable certification/conformance binding associations.
    """

    bindings: tuple[CertifiedProviderCapabilityBinding, ...]

    def __post_init__(self) -> None:
        """Reject malformed and duplicate exact evidence associations.

        Raises:
            ProviderCertificationBindingValidationError: If bindings are invalid or duplicate.
        """
        if type(self.bindings) is not tuple:
            raise ProviderCertificationBindingValidationError("invalid certified provider bindings")
        associations: set[
            tuple[
                ProviderCertificationEvidenceRef,
                CapabilityImplementationConformanceDeclaration,
            ]
        ] = set()
        for binding in self.bindings:
            if not isinstance(binding, CertifiedProviderCapabilityBinding):
                raise ProviderCertificationBindingValidationError(
                    "invalid certified provider binding"
                )
            association = (
                binding.certification_record.evidence_reference,
                binding.conformance_declaration,
            )
            if association in associations:
                raise ProviderCertificationBindingValidationError(
                    "duplicate certified provider binding"
                )
            associations.add(association)
