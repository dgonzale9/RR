"""Verify ERR-3.5 pure non-authoritative capability Catalog projections."""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from resource_registry.capability_catalog_projection import (
    CapabilityAvailabilityObservation,
    CapabilityCatalogDescriptor,
    CapabilityCatalogProjectionKey,
    CapabilityCatalogProjectionSet,
    CapabilityCatalogProjectionValidationError,
)
from resource_registry.capability_definitions import (
    CapabilityContractRef,
    CapabilityKey,
    CapabilityVersion,
)
from resource_registry.registry_core import OwnerDescriptor, OwnershipClassification

_CONTRACTS_PATH = (
    Path(__file__).resolve().parents[1] / "capability_catalog_projection" / "contracts.py"
)
_OBSERVED_AT = datetime(2026, 7, 29, 12, tzinfo=UTC)


def _build_descriptor(key: str = "capability-projection-1") -> CapabilityCatalogDescriptor:
    """Build one valid exact capability Catalog descriptor fixture.

    Args:
        key: Exact Catalog-only projection key.

    Returns:
        A valid immutable descriptor.
    """
    return CapabilityCatalogDescriptor(
        CapabilityCatalogProjectionKey(key),
        CapabilityContractRef(
            CapabilityKey("atlas.document.content.read"), CapabilityVersion("1.0.0")
        ),
        OwnerDescriptor("capability-definition", OwnershipClassification.REGISTRY_NATIVE),
        "projection-r1",
        "event-14",
        _OBSERVED_AT,
        CapabilityAvailabilityObservation(
            "observed-unknown", _OBSERVED_AT, _OBSERVED_AT + timedelta(minutes=5)
        ),
        ("resource-owner:binding:14",),
        ("governance:constraint:7",),
    )


def test_projection_package_exposes_only_pure_contracts() -> None:
    """Expose the approved non-authoritative projection vocabulary only."""
    from resource_registry import capability_catalog_projection

    assert capability_catalog_projection.__all__ == (
        "CapabilityCatalogProjectionValidationError",
        "CapabilityCatalogProjectionKey",
        "CapabilityAvailabilityObservation",
        "CapabilityCatalogDescriptor",
        "CapabilityCatalogProjectionSet",
    )
    assert CapabilityCatalogDescriptor.__slots__ == (
        "projection_key",
        "contract_ref",
        "source_authority",
        "projection_revision",
        "source_event_position",
        "projected_at",
        "availability_observation",
        "binding_references",
        "narrowing_constraint_references",
    )


def test_descriptor_preserves_exact_safe_evidence_and_immutability() -> None:
    """Preserve exact descriptor evidence without a query or eligibility decision."""
    descriptor = _build_descriptor()
    projection_set = CapabilityCatalogProjectionSet((descriptor,))

    assert projection_set.descriptors == (descriptor,)
    assert (
        descriptor.availability_observation.expires_at
        > descriptor.availability_observation.observed_at
    )
    with pytest.raises(FrozenInstanceError):
        descriptor.projection_revision = "projection-r2"  # type: ignore[misc]


def test_projection_set_rejects_duplicate_keys() -> None:
    """Reject duplicate exact Catalog-only projection keys."""
    descriptor = _build_descriptor()
    with pytest.raises(
        CapabilityCatalogProjectionValidationError,
        match="^duplicate capability catalog projection key$",
    ):
        CapabilityCatalogProjectionSet((descriptor, descriptor))


@pytest.mark.parametrize(
    ("factory", "message"),
    (
        (lambda: CapabilityCatalogProjectionKey(""), "invalid capability catalog projection key"),
        (
            lambda: CapabilityAvailabilityObservation("availability", _OBSERVED_AT, _OBSERVED_AT),
            "invalid capability availability expires at",
        ),
        (
            lambda: CapabilityCatalogProjectionSet([]),  # type: ignore[arg-type]
            "invalid capability catalog descriptors",
        ),
    ),
)
def test_projection_contracts_reject_malformed_values(factory: object, message: str) -> None:
    """Reject malformed projection values without interpreting their owner evidence.

    Args:
        factory: Callable fixture producing one malformed projection value.
        message: Exact expected validation message.
    """
    with pytest.raises(CapabilityCatalogProjectionValidationError, match=f"^{message}$"):
        factory()  # type: ignore[operator]


def test_projection_contracts_have_no_runtime_or_authorization_dependencies() -> None:
    """Keep descriptors independent from runtime, persistence, and live authorization boundaries."""
    syntax_tree = ast.parse(_CONTRACTS_PATH.read_text(encoding="utf-8"))
    imported_modules = {
        (node.module or "").split(".", maxsplit=1)[0]
        for node in ast.walk(syntax_tree)
        if isinstance(node, ast.ImportFrom) and node.level == 0
    }
    assert imported_modules.isdisjoint(
        {
            "django",
            "BotPlatform",
            "BotServer",
            "MCPServer",
            "authorization",
            "providers",
            "execution",
            "catalog",
            "search",
            "settings",
        }
    )
