"""Verify ERR-3.6 pure capability planning descriptors."""

from datetime import UTC, datetime

import pytest

from resource_registry.capability_definitions import (
    CapabilityContractRef,
    CapabilityKey,
    CapabilityVersion,
)
from resource_registry.capability_planning_descriptors import (
    CapabilityPlanningDescriptor,
    CapabilityPlanningDescriptorRef,
    CapabilityPlanningDescriptorSet,
    CapabilityPlanningDescriptorValidationError,
)


def _ref(value: str = "planning-1") -> CapabilityPlanningDescriptorRef:
    """Build an exact planning reference."""
    return CapabilityPlanningDescriptorRef(
        value,
        CapabilityContractRef(
            CapabilityKey("atlas.document.content.read"), CapabilityVersion("1.0.0")
        ),
    )


def _descriptor(value: str = "planning-1") -> CapabilityPlanningDescriptor:
    """Build a safe untrusted planning descriptor."""
    return CapabilityPlanningDescriptor(
        _ref(value), "document-read", datetime(2026, 7, 29, tzinfo=UTC)
    )


def test_planning_descriptor_is_immutable_and_exact() -> None:
    """Preserve safe evidence without selection behavior."""
    descriptor = _descriptor()
    assert CapabilityPlanningDescriptorSet((descriptor,)).descriptors == (descriptor,)
    assert descriptor.descriptor_ref.contract_ref.capability_version == CapabilityVersion("1.0.0")


def test_planning_set_rejects_duplicate_references() -> None:
    """Reject duplicate exact planning references."""
    descriptor = _descriptor()
    with pytest.raises(
        CapabilityPlanningDescriptorValidationError,
        match="duplicate capability planning descriptor reference",
    ):
        CapabilityPlanningDescriptorSet((descriptor, descriptor))


@pytest.mark.parametrize("value", ("", "   ", "x" * 513))
def test_reference_rejects_malformed_values(value: str) -> None:
    """Reject malformed opaque planning identities."""
    with pytest.raises(CapabilityPlanningDescriptorValidationError):
        _ref(value)
