"""Verify the ERR-0.1-S3 opaque-reference and projection-envelope contracts."""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, fields
from enum import Enum
from pathlib import Path

import pytest

from resource_registry.registry_core import (
    REGISTRY_CORE_CONTRACT_VERSION,
    OpaqueReference,
    OwnerDescriptor,
    OwnershipClassification,
    ProjectionEnvelope,
    ProjectionTombstoneState,
    RegistryCoreValidationError,
    projection_envelopes,
    references,
)

_CORE_SOURCE_DIRECTORY = Path(__file__).resolve().parents[2] / "registry_core"


class _StringEnum(str, Enum):
    """Provide a str-derived enum for incorrect-type validation coverage."""

    VALUE = "value"
    VERSION = "0.1"


def _build_owner() -> OwnerDescriptor:
    """Build an owner descriptor suitable for generic Core contract fixtures.

    Returns:
        A valid immutable owner descriptor.
    """
    return OwnerDescriptor("example-owner", OwnershipClassification.DOMAIN_BACKED)


def _build_reference(value: str = "subject") -> OpaqueReference:
    """Build an opaque reference suitable for generic Core contract fixtures.

    Args:
        value: Opaque value to include in the reference.

    Returns:
        A valid immutable opaque reference.
    """
    return OpaqueReference(_build_owner(), value)


def _build_envelope(**overrides: object) -> ProjectionEnvelope:
    """Build a valid projection envelope with optional field overrides.

    Args:
        overrides: Replacement values keyed by projection-envelope field name.

    Returns:
        A valid projection envelope unless an override intentionally violates validation.
    """
    values: dict[str, object] = {
        "contract_version": REGISTRY_CORE_CONTRACT_VERSION,
        "source_owner": _build_owner(),
        "subject": _build_reference(),
        "idempotency_key": "message-1",
    }
    values.update(overrides)
    return ProjectionEnvelope(**values)  # type: ignore[arg-type]


def test_s3_modules_and_package_exports_are_preserved_before_s2_contracts() -> None:
    """Preserve the approved Epic 0.1 and S1 public API before the S2 append."""
    from resource_registry import registry_core

    assert references.__all__ == ("OpaqueReference",)
    assert projection_envelopes.__all__ == ("ProjectionEnvelope", "ProjectionTombstoneState")
    assert registry_core.__all__ == (
        "REGISTRY_CORE_CONTRACT_VERSION",
        "OwnerDescriptor",
        "OwnershipClassification",
        "OpaqueReference",
        "ProjectionEnvelope",
        "ProjectionTombstoneState",
        "RegistryCoreValidationError",
        "NamespaceKey",
        "ResourceTypeKey",
        "ResourceKey",
        "ResourceVersionKey",
        "ArtifactKey",
        "MetadataNamespaceKey",
        "MetadataSchemaKey",
        "MetadataClassification",
        "MetadataScope",
        "MetadataProvenance",
        "MetadataEnvelope",
    )


def test_opaque_reference_has_the_exact_fields_defaults_and_value_semantics() -> None:
    """Construct references with approved fields, defaults, equality, hash, and repr behavior."""
    parent = _build_reference("parent")
    reference = OpaqueReference(_build_owner(), "  urn:example:subject  ", parent)

    assert tuple(field.name for field in fields(OpaqueReference)) == ("owner", "value", "parent")
    assert OpaqueReference.__slots__ == ("owner", "value", "parent")
    assert fields(OpaqueReference)[2].default is None
    assert reference.value == "  urn:example:subject  "
    assert reference == OpaqueReference(_build_owner(), "  urn:example:subject  ", parent)
    assert hash(reference) == hash(
        OpaqueReference(_build_owner(), "  urn:example:subject  ", parent)
    )
    assert reference != OpaqueReference(_build_owner(), "other", parent)
    assert repr(reference) == (
        "OpaqueReference(owner=OwnerDescriptor(bounded_context_identifier='example-owner', "
        "ownership_classification=<OwnershipClassification.DOMAIN_BACKED: 'domain_backed'>), "
        "value='  urn:example:subject  ', parent=OpaqueReference(owner=OwnerDescriptor("
        "bounded_context_identifier='example-owner', ownership_classification="
        "<OwnershipClassification.DOMAIN_BACKED: 'domain_backed'>), value='parent', parent=None))"
    )


@pytest.mark.parametrize(
    ("owner", "value", "parent", "message"),
    (
        (None, "valid", None, "invalid opaque reference owner"),
        (_build_owner(), None, None, "invalid opaque reference value"),
        (_build_owner(), "", None, "invalid opaque reference value"),
        (_build_owner(), " \t", None, "invalid opaque reference value"),
        (_build_owner(), b"value", None, "invalid opaque reference value"),
        (_build_owner(), True, None, "invalid opaque reference value"),
        (_build_owner(), 1, None, "invalid opaque reference value"),
        (_build_owner(), 1.0, None, "invalid opaque reference value"),
        (_build_owner(), _StringEnum.VALUE, None, "invalid opaque reference value"),
        (_build_owner(), "valid", "parent", "invalid opaque reference parent"),
        (_build_owner(), "valid", [], "invalid opaque reference parent"),
    ),
)
def test_opaque_reference_rejects_invalid_fields_in_declaration_order(
    owner: object, value: object, parent: object, message: str
) -> None:
    """Reject incorrectly typed or blank reference fields with exact messages.

    Args:
        owner: Candidate reference owner.
        value: Candidate opaque value.
        parent: Candidate parent reference.
        message: Expected validation message.
    """
    with pytest.raises(RegistryCoreValidationError, match=f"^{message}$"):
        OpaqueReference(owner, value, parent)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "opaque_value",
    (
        "00000000-0000-0000-0000-000000000000",
        "https://example.invalid/resource?x=1",
        "/a/path/to/a/resource",
        '{"arbitrary": "json"}',
        "MiXeD.Case Value",
    ),
)
def test_opaque_reference_preserves_every_non_blank_string_without_interpretation(
    opaque_value: str,
) -> None:
    """Accept opaque non-blank values without parsing or normalization.

    Args:
        opaque_value: Opaque string whose exact form must be retained.
    """
    assert _build_reference(opaque_value).value == opaque_value


def test_projection_tombstone_state_has_exact_members_and_rejects_invalid_construction() -> None:
    """Expose exactly the approved tombstone state values and validation behavior."""
    assert ProjectionTombstoneState.__members__ == {
        "PRESENT": ProjectionTombstoneState.PRESENT,
        "TOMBSTONE": ProjectionTombstoneState.TOMBSTONE,
    }
    assert tuple(ProjectionTombstoneState) == (
        ProjectionTombstoneState.PRESENT,
        ProjectionTombstoneState.TOMBSTONE,
    )
    assert tuple(member.value for member in ProjectionTombstoneState) == ("present", "tombstone")
    assert issubclass(ProjectionTombstoneState, str)
    assert issubclass(ProjectionTombstoneState, Enum)
    with pytest.raises(RegistryCoreValidationError, match="^invalid projection tombstone state$"):
        ProjectionTombstoneState("unknown")


def test_projection_envelope_has_exact_fields_defaults_and_value_semantics() -> None:
    """Construct envelopes with approved fields, defaults, equality, hash, and repr behavior."""
    first_payload_reference = _build_reference("first")
    second_payload_reference = _build_reference("second")
    envelope = _build_envelope(
        ordering_token="source-order-2",
        payload=(first_payload_reference, second_payload_reference),
    )

    assert tuple(field.name for field in fields(ProjectionEnvelope)) == (
        "contract_version",
        "source_owner",
        "subject",
        "idempotency_key",
        "ordering_token",
        "tombstone_state",
        "payload",
    )
    assert ProjectionEnvelope.__slots__ == tuple(field.name for field in fields(ProjectionEnvelope))
    assert fields(ProjectionEnvelope)[4].default is None
    assert fields(ProjectionEnvelope)[5].default is ProjectionTombstoneState.PRESENT
    assert fields(ProjectionEnvelope)[6].default == ()
    assert envelope == _build_envelope(
        ordering_token="source-order-2",
        payload=(first_payload_reference, second_payload_reference),
    )
    assert hash(envelope) == hash(
        _build_envelope(
            ordering_token="source-order-2",
            payload=(first_payload_reference, second_payload_reference),
        )
    )
    assert envelope != _build_envelope(
        ordering_token="source-order-2", payload=(second_payload_reference, first_payload_reference)
    )
    assert "ProjectionEnvelope(contract_version='0.1', source_owner=" in repr(envelope)
    assert "payload=(OpaqueReference(" in repr(envelope)


@pytest.mark.parametrize(
    ("overrides", "message"),
    (
        ({"contract_version": None}, "unsupported projection contract version"),
        ({"contract_version": ""}, "unsupported projection contract version"),
        ({"contract_version": "0.2"}, "unsupported projection contract version"),
        ({"contract_version": True}, "unsupported projection contract version"),
        ({"contract_version": b"0.1"}, "unsupported projection contract version"),
        ({"contract_version": _StringEnum.VERSION}, "unsupported projection contract version"),
        ({"source_owner": "owner"}, "invalid projection source owner"),
        ({"source_owner": None}, "invalid projection source owner"),
        ({"subject": "subject"}, "invalid projection subject"),
        ({"subject": None}, "invalid projection subject"),
        ({"idempotency_key": None}, "invalid projection idempotency key"),
        ({"idempotency_key": " \n"}, "invalid projection idempotency key"),
        ({"idempotency_key": b"key"}, "invalid projection idempotency key"),
        ({"idempotency_key": 1}, "invalid projection idempotency key"),
        ({"idempotency_key": _StringEnum.VALUE}, "invalid projection idempotency key"),
        ({"ordering_token": "\t"}, "invalid projection ordering token"),
        ({"ordering_token": 1}, "invalid projection ordering token"),
        ({"ordering_token": b"1"}, "invalid projection ordering token"),
        ({"ordering_token": _StringEnum.VALUE}, "invalid projection ordering token"),
        ({"tombstone_state": "present"}, "invalid projection tombstone state"),
        ({"tombstone_state": None}, "invalid projection tombstone state"),
        ({"payload": []}, "invalid projection payload"),
        ({"payload": {}}, "invalid projection payload"),
        ({"payload": {_build_reference()}}, "invalid projection payload"),
        ({"payload": (reference for reference in ())}, "invalid projection payload"),
        ({"payload": ("not-a-reference",)}, "invalid projection payload"),
        (
            {
                "tombstone_state": ProjectionTombstoneState.TOMBSTONE,
                "payload": (_build_reference(),),
            },
            "tombstone projection payload must be empty",
        ),
    ),
)
def test_projection_envelope_rejects_invalid_fields_with_exact_messages(
    overrides: dict[str, object], message: str
) -> None:
    """Reject malformed envelope values with the exact authoritative message.

    Args:
        overrides: Invalid replacement values for a valid envelope fixture.
        message: Expected validation message.
    """
    with pytest.raises(RegistryCoreValidationError, match=f"^{message}$"):
        _build_envelope(**overrides)


def test_projection_envelope_validation_stops_at_the_first_invalid_field() -> None:
    """Apply deterministic validation in the declared field order."""
    with pytest.raises(
        RegistryCoreValidationError, match="^unsupported projection contract version$"
    ):
        _build_envelope(contract_version="0.2", source_owner="not-an-owner")


def test_projection_envelope_supports_present_and_tombstone_evidence_only() -> None:
    """Retain required tombstone identity evidence while enforcing an empty payload."""
    present = _build_envelope()
    tombstone = _build_envelope(tombstone_state=ProjectionTombstoneState.TOMBSTONE)

    assert present.tombstone_state is ProjectionTombstoneState.PRESENT
    assert tombstone.tombstone_state is ProjectionTombstoneState.TOMBSTONE
    assert tombstone.payload == ()
    assert tombstone.ordering_token is None
    assert tombstone.subject == _build_reference()
    assert tombstone.idempotency_key == "message-1"


def test_projection_payload_is_recursively_immutable_and_not_defensively_copied() -> None:
    """Accept only tuples of frozen opaque references, preserving tuple identity and order."""
    payload = (_build_reference("first"), _build_reference("second"))
    envelope = _build_envelope(payload=payload)

    assert envelope.payload is payload
    assert envelope.payload == payload
    assert hash(envelope) == hash(_build_envelope(payload=payload))
    with pytest.raises(FrozenInstanceError):
        envelope.payload = ()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        envelope.payload[0].value = "changed"  # type: ignore[misc]


def test_s3_contracts_are_frozen_slotted_and_lack_runtime_behaviors() -> None:
    """Reject mutation and exclude parsing, resolution, serialization, and state APIs."""
    reference = _build_reference()
    envelope = _build_envelope()

    for value, field_name in ((reference, "value"), (envelope, "idempotency_key")):
        assert not hasattr(value, "__dict__")
        with pytest.raises(FrozenInstanceError):
            setattr(value, field_name, "changed")
        with pytest.raises(FrozenInstanceError):
            delattr(value, field_name)
        with pytest.raises(TypeError):
            value.unexpected = "value"  # type: ignore[attr-defined]

    prohibited_public_names = {
        "authorize",
        "canonicalize",
        "checksum",
        "deserialize",
        "dereference",
        "parse",
        "resolve",
        "serialize",
        "to_dict",
    }
    assert not prohibited_public_names.intersection(OpaqueReference.__dict__)
    assert not prohibited_public_names.intersection(ProjectionEnvelope.__dict__)


def test_s3_sources_contain_no_runtime_or_typed_identity_imports() -> None:
    """Keep S3 stdlib-only and free of deferred runtime implementation concerns."""
    imported_modules = {
        node.module
        for source_path in sorted(_CORE_SOURCE_DIRECTORY.glob("*.py"))
        for node in ast.walk(ast.parse(source_path.read_text(encoding="utf-8")))
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module is not None
    }

    assert not {
        "django",
        "resource_registry.events",
        "resource_registry.models",
        "resource_registry.providers",
        "resource_registry.services",
        "resource_registry.storage",
        "resource_registry.transformations",
    }.intersection(imported_modules)


def test_projection_idempotency_and_ordering_are_in_memory_value_evidence_only() -> None:
    """Preserve exact documented idempotency and opaque ordering values without state."""
    owner = _build_owner()
    first = _build_envelope(source_owner=owner, idempotency_key="same", ordering_token="10")
    second = _build_envelope(
        source_owner=owner,
        subject=_build_reference("other-subject"),
        idempotency_key="same",
        ordering_token="2",
    )

    assert (
        first.contract_version,
        first.source_owner,
        first.idempotency_key,
    ) == (second.contract_version, second.source_owner, second.idempotency_key)
    assert first.ordering_token == "10"
    assert second.ordering_token == "2"
    assert not hasattr(ProjectionEnvelope, "idempotency_identity")
    assert not hasattr(ProjectionEnvelope, "compare_ordering_tokens")
