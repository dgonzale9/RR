"""Verify the ERR-0.1-S2 ownership value contracts."""

from __future__ import annotations

import re
from dataclasses import FrozenInstanceError, fields
from enum import Enum
from pathlib import Path

import pytest

from resource_registry.registry_core import (
    OwnerDescriptor,
    OwnershipClassification,
    RegistryCoreValidationError,
)

_OWNERSHIP_SOURCE_PATH = Path(__file__).resolve().parents[2] / "registry_core" / "ownership.py"


def test_ownership_classifications_match_the_authoritative_story_definition() -> None:
    """Expose exactly the two approved ownership classifications."""
    assert OwnershipClassification.__members__ == {
        "DOMAIN_BACKED": OwnershipClassification.DOMAIN_BACKED,
        "REGISTRY_NATIVE": OwnershipClassification.REGISTRY_NATIVE,
    }
    assert tuple(OwnershipClassification) == (
        OwnershipClassification.DOMAIN_BACKED,
        OwnershipClassification.REGISTRY_NATIVE,
    )
    assert tuple(classification.value for classification in OwnershipClassification) == (
        "domain_backed",
        "registry_native",
    )
    assert issubclass(OwnershipClassification, str)
    assert issubclass(OwnershipClassification, Enum)


@pytest.mark.parametrize("invalid_value", (None, "DOMAIN_BACKED", "dataset", "registry"))
def test_ownership_classification_rejects_values_outside_the_authoritative_pair(
    invalid_value: object,
) -> None:
    """Reject every classification except the two accepted values."""
    with pytest.raises(RegistryCoreValidationError, match="^invalid ownership classification$"):
        OwnershipClassification(invalid_value)


@pytest.mark.parametrize(
    ("bounded_context_identifier", "ownership_classification"),
    (
        ("a", OwnershipClassification.DOMAIN_BACKED),
        ("a0", OwnershipClassification.DOMAIN_BACKED),
        ("a-0", OwnershipClassification.DOMAIN_BACKED),
        ("a-b-c9", OwnershipClassification.DOMAIN_BACKED),
        ("creative-assembly", OwnershipClassification.DOMAIN_BACKED),
        ("registry-core", OwnershipClassification.REGISTRY_NATIVE),
    ),
)
def test_owner_descriptor_accepts_canonical_generic_values(
    bounded_context_identifier: str,
    ownership_classification: OwnershipClassification,
) -> None:
    """Construct a deterministic immutable descriptor from generic ownership values."""
    descriptor = OwnerDescriptor(bounded_context_identifier, ownership_classification)

    assert descriptor.bounded_context_identifier == bounded_context_identifier
    assert descriptor.ownership_classification is ownership_classification
    assert descriptor.__slots__ == (
        "bounded_context_identifier",
        "ownership_classification",
    )
    assert tuple(field.name for field in fields(descriptor)) == (
        "bounded_context_identifier",
        "ownership_classification",
    )


@pytest.mark.parametrize(
    "invalid_identifier",
    (
        None,
        "",
        " ",
        "0-context",
        "-context",
        "context-",
        "context--owner",
        "context owner",
        "Registry-Core",
        "registry_core",
        "registry.core",
        "registry:core",
        "registry/core",
    ),
)
def test_owner_descriptor_rejects_blank_ambiguous_and_runtime_coupled_identifiers(
    invalid_identifier: object,
) -> None:
    """Reject identifiers that could be blank, ambiguous, or executable references."""
    with pytest.raises(
        RegistryCoreValidationError,
        match="^invalid owner bounded-context identifier$",
    ):
        OwnerDescriptor(invalid_identifier, OwnershipClassification.DOMAIN_BACKED)


@pytest.mark.parametrize("invalid_classification", (None, "domain_backed", object()))
def test_owner_descriptor_requires_an_ownership_classification_value(
    invalid_classification: object,
) -> None:
    """Reject coercion and malformed classification associations."""
    with pytest.raises(RegistryCoreValidationError, match="^invalid ownership classification$"):
        OwnerDescriptor("creative-assembly", invalid_classification)


def test_owner_descriptor_is_immutable_and_equality_safe() -> None:
    """Preserve frozen value semantics for equivalent ownership declarations."""
    descriptor = OwnerDescriptor("creative-assembly", OwnershipClassification.DOMAIN_BACKED)

    assert descriptor == OwnerDescriptor("creative-assembly", OwnershipClassification.DOMAIN_BACKED)
    assert descriptor != OwnerDescriptor("registry-core", OwnershipClassification.REGISTRY_NATIVE)
    assert hash(descriptor) == hash(
        OwnerDescriptor("creative-assembly", OwnershipClassification.DOMAIN_BACKED)
    )
    assert repr(descriptor) == (
        "OwnerDescriptor(bounded_context_identifier='creative-assembly', "
        "ownership_classification=<OwnershipClassification.DOMAIN_BACKED: 'domain_backed'>)"
    )
    assert not hasattr(descriptor, "__dict__")
    with pytest.raises(FrozenInstanceError):
        descriptor.bounded_context_identifier = "other-context"  # type: ignore[misc]


def test_owner_descriptor_exposes_no_resolution_or_runtime_integration_behavior() -> None:
    """Keep the descriptor declarative rather than a resolver or service locator."""
    public_method_names = {
        name
        for name in OwnerDescriptor.__dict__
        if not name.startswith("_") and callable(getattr(OwnerDescriptor, name))
    }

    assert public_method_names == set()
    assert set(OwnerDescriptor.__dataclass_fields__) == {
        "bounded_context_identifier",
        "ownership_classification",
    }


def test_ownership_contracts_remain_generic_and_consumer_independent() -> None:
    """Exclude dataset, format, provider, and runtime-specific assumptions from Core source."""
    ownership_source = _OWNERSHIP_SOURCE_PATH.read_text(encoding="utf-8").lower()

    assert all(
        re.search(rf"\b{prohibited_term}\b", ownership_source) is None
        for prohibited_term in (
            "dataset",
            "excel",
            "csv",
            "parquet",
            "provider",
            "campaign",
            "demo",
            "django",
            "orm",
            "bif",
            "worker",
        )
    )
