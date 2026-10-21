"""Verify the ERR-0.2-S1 Registry Core identity value contracts."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from enum import Enum
from typing import cast
from uuid import UUID

import pytest

from resource_registry.registry_core import (
    NamespaceKey,
    RegistryCoreValidationError,
    ResourceKey,
    ResourceTypeKey,
)


class _StringEnum(str, Enum):
    """Provide a string-derived enum for strict input validation coverage."""

    VALUE = "atlas.data.dataset"


def _build_namespace() -> NamespaceKey:
    """Build a valid namespace key fixture.

    Returns:
        A valid immutable namespace key.
    """
    return NamespaceKey("8dbf68a1-9567-4d74-931f-c36c4247f80b")


def _build_resource_type() -> ResourceTypeKey:
    """Build a valid resource type key fixture.

    Returns:
        A valid immutable resource type key.
    """
    return ResourceTypeKey("atlas.data.dataset")


def test_identity_module_and_package_exports_are_preserved_before_s2_contracts() -> None:
    """Preserve the approved S1 identity exports before the authorized S2 append."""
    from resource_registry import registry_core
    from resource_registry.registry_core import identity

    assert identity.__all__ == ("NamespaceKey", "ResourceTypeKey", "ResourceKey")
    identity_start = registry_core.__all__.index("NamespaceKey")
    assert registry_core.__all__[identity_start : identity_start + 3] == identity.__all__
    assert NamespaceKey.__module__ == "resource_registry.registry_core.identity"
    assert ResourceTypeKey.__module__ == "resource_registry.registry_core.identity"
    assert ResourceKey.__module__ == "resource_registry.registry_core.identity"


@pytest.mark.parametrize(
    "value",
    (
        UUID("8dbf68a1-9567-4d74-931f-c36c4247f80b"),
        "8dbf68a1-9567-4d74-931f-c36c4247f80b",
    ),
)
def test_namespace_key_accepts_uuid_or_canonical_lowercase_text(value: UUID | str) -> None:
    """Construct a namespace key from an approved UUID representation."""
    namespace = NamespaceKey(value)

    assert tuple(field.name for field in fields(NamespaceKey)) == ("value",)
    assert NamespaceKey.__slots__ == ("value",)
    assert namespace.value == UUID("8dbf68a1-9567-4d74-931f-c36c4247f80b")
    assert repr(namespace) == "NamespaceKey(value=UUID('8dbf68a1-9567-4d74-931f-c36c4247f80b'))"
    assert namespace == NamespaceKey(namespace.value)
    assert namespace != cast(object, "8dbf68a1-9567-4d74-931f-c36c4247f80b")
    assert {namespace: "value"}[NamespaceKey(namespace.value)] == "value"


@pytest.mark.parametrize(
    "value",
    (
        None,
        "",
        " ",
        "8DBF68A1-9567-4D74-931F-C36C4247F80B",
        "{8dbf68a1-9567-4d74-931f-c36c4247f80b}",
        1,
        True,
    ),
)
def test_namespace_key_rejects_noncanonical_or_invalid_values(value: object) -> None:
    """Reject invalid namespace values with the approved deterministic error."""
    with pytest.raises(RegistryCoreValidationError, match="^invalid namespace key$"):
        NamespaceKey(value)  # type: ignore[call-overload]


@pytest.mark.parametrize("value", (b"namespace", [], {}, _StringEnum.VALUE))
def test_namespace_key_rejects_all_other_unsupported_types(value: object) -> None:
    """Reject unsupported namespace types without leaking implementation exceptions."""
    with pytest.raises(RegistryCoreValidationError, match="^invalid namespace key$"):
        NamespaceKey(value)  # type: ignore[call-overload]


def test_namespace_key_is_frozen() -> None:
    """Reject mutation of a namespace key."""
    with pytest.raises(FrozenInstanceError):
        _build_namespace().value = UUID("f65c59b1-c07e-41f7-84f1-3a733929431d")  # type: ignore[misc]


@pytest.mark.parametrize(
    "value",
    (
        "a.b.c",
        "atlas.data.dataset",
        "atlas.data.dataset_v2",
        "atlas.data.data-set",
        "a.b." + "c" * 124,
    ),
)
def test_resource_type_key_accepts_the_approved_grammar(value: str) -> None:
    """Construct a resource type key without registration or resolution."""
    resource_type = ResourceTypeKey(value)

    assert tuple(field.name for field in fields(ResourceTypeKey)) == ("value",)
    assert ResourceTypeKey.__slots__ == ("value",)
    assert resource_type.value == value
    assert resource_type == ResourceTypeKey(value)
    assert resource_type != ResourceTypeKey("atlas.data.other")
    assert {resource_type: "value"}[ResourceTypeKey(value)] == "value"
    assert repr(resource_type) == f"ResourceTypeKey(value='{value}')"


@pytest.mark.parametrize(
    "value",
    (
        None,
        "",
        " ",
        "atlas.data",
        "Atlas.data.dataset",
        "atlas.data.Dätaset",
        "atlas..dataset",
        ".atlas.data.dataset",
        "atlas.data.dataset.",
        "atlas.data. dataset",
        "atlas.data.1dataset",
        _StringEnum.VALUE,
        "a.b.c" + "x" * 124,
    ),
)
def test_resource_type_key_rejects_values_outside_the_approved_grammar(value: object) -> None:
    """Reject malformed resource type keys with the approved deterministic error."""
    with pytest.raises(RegistryCoreValidationError, match="^invalid resource type key$"):
        ResourceTypeKey(value)  # type: ignore[arg-type]


def test_resource_type_key_is_frozen_and_case_sensitive() -> None:
    """Preserve resource type spelling and reject mutation."""
    with pytest.raises(FrozenInstanceError):
        _build_resource_type().value = "atlas.data.other"  # type: ignore[misc]

    assert ResourceTypeKey("atlas.data.dataset") != cast(object, "atlas.data.dataset")


@pytest.mark.parametrize("value", (b"atlas.data.dataset", True, 1, [], {}))
def test_resource_type_key_rejects_all_other_unsupported_types(value: object) -> None:
    """Reject unsupported resource type key types with the approved error."""
    with pytest.raises(RegistryCoreValidationError, match="^invalid resource type key$"):
        ResourceTypeKey(value)  # type: ignore[arg-type]


def test_resource_key_has_exact_fields_and_preserves_issuer_owned_identifier() -> None:
    """Construct an immutable resource key that preserves opaque issuer text exactly."""
    resource_key = ResourceKey(
        _build_namespace(), _build_resource_type(), "  Récord-001-1234567890  "
    )

    assert tuple(field.name for field in fields(ResourceKey)) == (
        "namespace",
        "resource_type",
        "resource_id",
    )
    assert ResourceKey.__slots__ == ("namespace", "resource_type", "resource_id")
    assert resource_key.resource_id == "  Récord-001-1234567890  "
    assert resource_key == ResourceKey(
        _build_namespace(), _build_resource_type(), resource_key.resource_id
    )
    assert resource_key != ResourceKey(
        _build_namespace(), _build_resource_type(), "record-001-1234567890"
    )
    assert resource_key != cast(
        object,
        (resource_key.namespace, resource_key.resource_type, resource_key.resource_id),
    )
    assert {resource_key: "value"}[resource_key] == "value"
    assert repr(resource_key).startswith("ResourceKey(namespace=NamespaceKey(value=UUID(")


@pytest.mark.parametrize(
    ("namespace", "resource_type", "resource_id", "message"),
    (
        (None, _build_resource_type(), "id", "invalid resource key namespace"),
        (_build_namespace(), None, "id", "invalid resource key type"),
        (_build_namespace(), _build_resource_type(), None, "invalid resource key id"),
        (_build_namespace(), _build_resource_type(), "", "invalid resource key id"),
        (_build_namespace(), _build_resource_type(), " \t", "invalid resource key id"),
        (_build_namespace(), _build_resource_type(), b"id", "invalid resource key id"),
        (_build_namespace(), _build_resource_type(), True, "invalid resource key id"),
        (_build_namespace(), _build_resource_type(), 1, "invalid resource key id"),
        (
            "8dbf68a1-9567-4d74-931f-c36c4247f80b",
            _build_resource_type(),
            "id",
            "invalid resource key namespace",
        ),
        (
            _build_namespace(),
            "atlas.data.dataset",
            "id",
            "invalid resource key type",
        ),
        (None, None, None, "invalid resource key namespace"),
    ),
)
def test_resource_key_validates_components_in_field_order(
    namespace: object, resource_type: object, resource_id: object, message: str
) -> None:
    """Reject invalid resource key components with ordered deterministic errors."""
    with pytest.raises(RegistryCoreValidationError, match=f"^{message}$"):
        ResourceKey(namespace, resource_type, resource_id)  # type: ignore[arg-type]


def test_resource_key_preserves_uuid_and_numeric_shaped_identifiers_without_coercion() -> None:
    """Keep UUID-shaped, numeric-looking, and long issuer identifiers as exact strings."""
    identifiers = (
        "4AF6293B-D09E-4719-90BF-3F47A72F98E8",
        "000123",
        "x" * 4096,
    )

    for identifier in identifiers:
        resource_key = ResourceKey(_build_namespace(), _build_resource_type(), identifier)
        assert resource_key.resource_id == identifier


def test_resource_key_literal_fixture_preserves_the_approved_component_order() -> None:
    """Document the approved canonical mapping without adding a serialization API."""
    resource_key = ResourceKey(_build_namespace(), _build_resource_type(), "record-1")
    fixture = {
        "namespace": str(resource_key.namespace.value),
        "type": resource_key.resource_type.value,
        "id": resource_key.resource_id,
    }

    assert fixture == {
        "namespace": "8dbf68a1-9567-4d74-931f-c36c4247f80b",
        "type": "atlas.data.dataset",
        "id": "record-1",
    }


def test_resource_key_is_frozen() -> None:
    """Reject mutation of every resource key component."""
    with pytest.raises(FrozenInstanceError):
        ResourceKey(_build_namespace(), _build_resource_type(), "id").resource_id = "other"  # type: ignore[misc]
