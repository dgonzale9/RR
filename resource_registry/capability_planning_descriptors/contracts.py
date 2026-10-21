"""Define pure immutable, non-authoritative capability planning descriptors."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from resource_registry.capability_definitions import CapabilityContractRef

from .errors import CapabilityPlanningDescriptorValidationError

__all__ = (
    "CapabilityPlanningDescriptor",
    "CapabilityPlanningDescriptorRef",
    "CapabilityPlanningDescriptorSet",
)

_MAXIMUM_REFERENCE_LENGTH = 512


@dataclass(frozen=True, slots=True)
class CapabilityPlanningDescriptorRef:
    """Identify one exact application-owned planning reference.

    Args:
        value: Exact nonblank opaque descriptor identity.
        contract_ref: Exact capability contract represented by the descriptor.
    """

    value: str
    contract_ref: CapabilityContractRef

    def __post_init__(self) -> None:
        """Validate exact descriptor identity without resolving Catalog or authority."""
        if (
            type(self.value) is not str
            or not self.value
            or self.value.isspace()
            or len(self.value) > _MAXIMUM_REFERENCE_LENGTH
        ):
            raise CapabilityPlanningDescriptorValidationError(
                "invalid capability planning descriptor reference"
            )
        if not isinstance(self.contract_ref, CapabilityContractRef):
            raise CapabilityPlanningDescriptorValidationError(
                "invalid capability planning descriptor contract"
            )


@dataclass(frozen=True, slots=True)
class CapabilityPlanningDescriptor:
    """Carry bounded safe planning evidence as untrusted intent.

    Args:
        descriptor_ref: Exact planning descriptor reference.
        purpose_category: Exact bounded safe category without executable meaning.
        freshness_at: UTC freshness snapshot supplied by Catalog evidence.
    """

    descriptor_ref: CapabilityPlanningDescriptorRef
    purpose_category: str
    freshness_at: datetime

    def __post_init__(self) -> None:
        """Validate safe evidence without granting authority or invoking a planner."""
        if not isinstance(self.descriptor_ref, CapabilityPlanningDescriptorRef):
            raise CapabilityPlanningDescriptorValidationError(
                "invalid capability planning descriptor"
            )
        if (
            type(self.purpose_category) is not str
            or not self.purpose_category
            or self.purpose_category.isspace()
            or len(self.purpose_category) > _MAXIMUM_REFERENCE_LENGTH
        ):
            raise CapabilityPlanningDescriptorValidationError(
                "invalid capability planning purpose category"
            )
        if not isinstance(self.freshness_at, datetime) or self.freshness_at.tzinfo is not UTC:
            raise CapabilityPlanningDescriptorValidationError(
                "invalid capability planning freshness"
            )


@dataclass(frozen=True, slots=True)
class CapabilityPlanningDescriptorSet:
    """Validate immutable exact planning descriptors in declaration order.

    Args:
        descriptors: Exact immutable planning descriptors.
    """

    descriptors: tuple[CapabilityPlanningDescriptor, ...]

    def __post_init__(self) -> None:
        """Reject malformed or duplicate descriptor references without ranking or selection."""
        if type(self.descriptors) is not tuple:
            raise CapabilityPlanningDescriptorValidationError(
                "invalid capability planning descriptors"
            )
        refs: set[CapabilityPlanningDescriptorRef] = set()
        for descriptor in self.descriptors:
            if not isinstance(descriptor, CapabilityPlanningDescriptor):
                raise CapabilityPlanningDescriptorValidationError(
                    "invalid capability planning descriptor"
                )
            if descriptor.descriptor_ref in refs:
                raise CapabilityPlanningDescriptorValidationError(
                    "duplicate capability planning descriptor reference"
                )
            refs.add(descriptor.descriptor_ref)
