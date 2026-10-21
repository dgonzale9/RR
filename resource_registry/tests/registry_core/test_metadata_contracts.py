"""Verify the ERR-0.3-S2 Registry Core metadata value contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime, timedelta, timezone
from enum import Enum
from inspect import Parameter, signature
from types import MappingProxyType
from typing import cast

import pytest

from resource_registry.registry_core import (
    ArtifactKey,
    MetadataClassification,
    MetadataEnvelope,
    MetadataNamespaceKey,
    MetadataProvenance,
    MetadataSchemaKey,
    MetadataScope,
    NamespaceKey,
    RegistryCoreValidationError,
    ResourceKey,
    ResourceTypeKey,
    ResourceVersionKey,
)


class _StringEnum(str, Enum):
    """Provide a string enum for strict plain-string validation coverage."""

    VALUE = "atlas.metadata.example"


def _build_resource() -> ResourceKey:
    """Build a completed Epic 0.2 resource fixture.

    Returns:
        A valid resource key.
    """
    return ResourceKey(
        NamespaceKey("8dbf68a1-9567-4d74-931f-c36c4247f80b"),
        ResourceTypeKey("atlas.data.dataset"),
        "dataset-1",
    )


def _build_schema() -> MetadataSchemaKey:
    """Build a valid metadata schema fixture.

    Returns:
        A valid metadata schema key.
    """
    return MetadataSchemaKey(
        namespace=MetadataNamespaceKey(value="atlas.metadata.example"),
        name="summary",
    )


def _build_provenance() -> MetadataProvenance:
    """Build a valid metadata provenance fixture.

    Returns:
        A valid metadata provenance value.
    """
    return MetadataProvenance(source_kind="  observed  ", producer="  cataloger  ")


def _build_envelope(**overrides: object) -> MetadataEnvelope:
    """Build a valid metadata envelope with optional field replacements.

    Args:
        overrides: Replacement values keyed by metadata envelope field name.

    Returns:
        A valid metadata envelope unless an override intentionally violates its contract.
    """
    values: dict[str, object] = {
        "scope": MetadataScope.RESOURCE,
        "subject": _build_resource(),
        "schema": _build_schema(),
        "schema_version": "  v1  ",
        "revision": 1,
        "classification": MetadataClassification.INTERNAL,
        "provenance": _build_provenance(),
        "observed_at": datetime(2026, 7, 22, 6, 30, tzinfo=timezone(timedelta(hours=-6))),
        "payload": {"z": ["value", {"a": True}], "a": None},
    }
    values.update(overrides)
    return MetadataEnvelope(**values)  # type: ignore[arg-type]


def test_metadata_module_and_public_exports_are_exact() -> None:
    """Expose only the approved metadata values from the approved module and package boundary."""
    from resource_registry import registry_core
    from resource_registry.registry_core import metadata

    assert metadata.__all__ == (
        "MetadataNamespaceKey",
        "MetadataSchemaKey",
        "MetadataClassification",
        "MetadataScope",
        "MetadataProvenance",
        "MetadataEnvelope",
    )
    assert registry_core.__all__[-6:] == metadata.__all__
    assert all(contract.__module__ == "resource_registry.registry_core.metadata" for contract in (
        MetadataNamespaceKey,
        MetadataSchemaKey,
        MetadataClassification,
        MetadataScope,
        MetadataProvenance,
        MetadataEnvelope,
    ))


@pytest.mark.parametrize(
    "contract",
    (MetadataNamespaceKey, MetadataSchemaKey, MetadataProvenance, MetadataEnvelope),
)
def test_metadata_dataclasses_are_frozen_slotted_and_keyword_only(contract: type[object]) -> None:
    """Apply the resolved dataclass profile only to the four approved dataclasses.

    Args:
        contract: Metadata dataclass to inspect.
    """
    assert contract.__dataclass_fields__ is not None  # type: ignore[attr-defined]
    assert all(
        parameter.kind is Parameter.KEYWORD_ONLY
        for parameter in signature(contract).parameters.values()
    )


def test_metadata_dataclasses_reject_positional_construction() -> None:
    """Reject positional construction for every resolved metadata dataclass."""
    with pytest.raises(TypeError):
        MetadataNamespaceKey("atlas.metadata.example")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        MetadataSchemaKey(_build_schema().namespace, "summary")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        MetadataProvenance("source", "producer")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        MetadataEnvelope(  # type: ignore[call-arg]
            MetadataScope.RESOURCE,
            _build_resource(),
            _build_schema(),
            "v1",
            1,
            MetadataClassification.INTERNAL,
            _build_provenance(),
            datetime.now(UTC),
            {},
        )


def test_metadata_namespace_and_schema_keys_validate_exact_grammar_and_field_order() -> None:
    """Construct schema identities with exact values and deterministic validation precedence."""
    namespace = MetadataNamespaceKey(value="atlas.metadata.example")
    schema = MetadataSchemaKey(namespace=namespace, name="example_schema")

    assert tuple(field.name for field in fields(MetadataNamespaceKey)) == ("value",)
    assert tuple(field.name for field in fields(MetadataSchemaKey)) == ("namespace", "name")
    assert namespace == MetadataNamespaceKey(value="atlas.metadata.example")
    assert hash(namespace) == hash(MetadataNamespaceKey(value="atlas.metadata.example"))
    assert schema == MetadataSchemaKey(namespace=namespace, name="example_schema")
    assert hash(schema) == hash(MetadataSchemaKey(namespace=namespace, name="example_schema"))
    assert repr(schema) == (
        "MetadataSchemaKey(namespace=MetadataNamespaceKey(value='atlas.metadata.example'), "
        "name='example_schema')"
    )
    with pytest.raises(RegistryCoreValidationError, match="^invalid metadata namespace key$"):
        MetadataNamespaceKey(value="Atlas.metadata.example")
    with pytest.raises(RegistryCoreValidationError, match="^invalid metadata schema namespace$"):
        MetadataSchemaKey(namespace="invalid", name="INVALID")  # type: ignore[arg-type]


def test_metadata_provenance_preserves_opaque_plain_strings_and_rejects_invalid_values() -> None:
    """Preserve provenance labels exactly while rejecting invalid values in field order."""
    provenance = _build_provenance()

    assert tuple(field.name for field in fields(MetadataProvenance)) == ("source_kind", "producer")
    assert provenance.source_kind == "  observed  "
    assert provenance.producer == "  cataloger  "
    assert provenance == _build_provenance()
    assert hash(provenance) == hash(_build_provenance())
    with pytest.raises(
        RegistryCoreValidationError,
        match="^invalid metadata provenance source kind$",
    ):
        MetadataProvenance(source_kind=_StringEnum.VALUE, producer=None)  # type: ignore[arg-type]
    with pytest.raises(RegistryCoreValidationError, match="^invalid metadata provenance producer$"):
        MetadataProvenance(source_kind="source", producer=" ")


def test_metadata_enums_have_exact_members_string_behavior_and_stable_invalid_errors() -> None:
    """Expose the resolved string-enum contracts without aliases or dataclass behavior."""
    assert MetadataClassification.__members__ == {"INTERNAL": MetadataClassification.INTERNAL}
    assert MetadataScope.__members__ == {
        "RESOURCE": MetadataScope.RESOURCE,
        "VERSION": MetadataScope.VERSION,
        "ARTIFACT": MetadataScope.ARTIFACT,
    }
    assert MetadataClassification("internal") is MetadataClassification.INTERNAL
    assert MetadataScope("artifact") is MetadataScope.ARTIFACT
    assert isinstance(MetadataClassification.INTERNAL, str)
    assert MetadataClassification.INTERNAL == "internal"
    assert hash(MetadataScope.RESOURCE) == hash("resource")
    assert not hasattr(MetadataClassification, "__dataclass_fields__")
    with pytest.raises(RegistryCoreValidationError, match="^invalid metadata classification$"):
        MetadataClassification("public")
    with pytest.raises(RegistryCoreValidationError, match="^invalid metadata scope$"):
        MetadataScope("representation")


def test_metadata_envelope_normalizes_time_and_defensively_freezes_payload() -> None:
    """Normalize timestamps and isolate nested mutable caller payload structures."""
    source_payload = {"z": ["value", {"a": True}], "a": None}
    envelope = _build_envelope(payload=source_payload)
    source_payload["z"][1]["a"] = False
    source_payload["z"].append("later")
    source_payload["new"] = "later"

    assert tuple(field.name for field in fields(MetadataEnvelope)) == (
        "scope", "subject", "schema", "schema_version", "revision", "classification",
        "provenance", "observed_at", "payload",
    )
    assert envelope.observed_at == datetime(2026, 7, 22, 12, 30, tzinfo=UTC)
    assert tuple(envelope.payload) == ("a", "z")
    assert envelope.payload["z"] == ("value", MappingProxyType({"a": True}))
    assert "new" not in envelope.payload
    with pytest.raises(TypeError):
        envelope.payload["new"] = "value"  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        envelope.revision = 2  # type: ignore[misc]
    with pytest.raises(TypeError):
        envelope.dynamic_value = "value"  # type: ignore[attr-defined]
    with pytest.raises(TypeError):
        hash(envelope)


def test_metadata_envelope_payload_equality_is_deterministic_across_input_key_order() -> None:
    """Sort nested mapping keys without changing logical metadata payload equality."""
    first_envelope = _build_envelope(payload={"z": {"b": 2, "a": 1}, "a": [False, 0]})
    second_envelope = _build_envelope(payload={"a": [False, 0], "z": {"a": 1, "b": 2}})

    assert first_envelope == second_envelope
    assert tuple(first_envelope.payload) == ("a", "z")
    assert tuple(cast(Mapping[str, object], first_envelope.payload["z"])) == ("a", "b")
    assert first_envelope.payload["a"] == (False, 0)


def test_metadata_envelope_validates_all_components_in_declared_order() -> None:
    """Reject compound-invalid envelope input by the approved validation precedence."""
    with pytest.raises(RegistryCoreValidationError, match="^invalid metadata envelope scope$"):
        _build_envelope(scope="invalid", subject=None)  # type: ignore[arg-type]
    with pytest.raises(RegistryCoreValidationError, match="^invalid metadata envelope subject$"):
        _build_envelope(subject=None, schema=None)  # type: ignore[arg-type]
    with pytest.raises(RegistryCoreValidationError, match="^invalid metadata envelope schema$"):
        _build_envelope(schema=None, schema_version=None)  # type: ignore[arg-type]
    with pytest.raises(
        RegistryCoreValidationError,
        match="^invalid metadata envelope schema version$",
    ):
        _build_envelope(schema_version=" ", revision=False)  # type: ignore[arg-type]
    with pytest.raises(RegistryCoreValidationError, match="^invalid metadata envelope revision$"):
        _build_envelope(revision=False, classification="internal")  # type: ignore[arg-type]
    with pytest.raises(
        RegistryCoreValidationError,
        match="^invalid metadata envelope classification$",
    ):
        _build_envelope(classification="internal", provenance=None)  # type: ignore[arg-type]
    with pytest.raises(RegistryCoreValidationError, match="^invalid metadata envelope provenance$"):
        _build_envelope(provenance=None, observed_at=datetime.now())  # type: ignore[arg-type]
    with pytest.raises(
        RegistryCoreValidationError,
        match="^invalid metadata envelope observed at$",
    ):
        _build_envelope(observed_at=datetime.now(), payload=set())  # type: ignore[arg-type]
    with pytest.raises(RegistryCoreValidationError, match="^invalid metadata envelope payload$"):
        _build_envelope(payload={"bad": float("nan")})


def test_metadata_envelope_requires_subject_type_that_matches_its_scope() -> None:
    """Reject otherwise valid subjects that do not match the declared metadata scope."""
    resource = _build_resource()
    version = ResourceVersionKey(resource, "version-1")
    artifact = ArtifactKey(resource=resource, artifact_id="artifact-1")

    assert _build_envelope(scope=MetadataScope.VERSION, subject=version).subject is version
    assert _build_envelope(scope=MetadataScope.ARTIFACT, subject=artifact).subject is artifact
    with pytest.raises(RegistryCoreValidationError, match="^invalid metadata envelope subject$"):
        _build_envelope(scope=MetadataScope.VERSION, subject=resource)


def test_metadata_envelope_rejects_non_json_and_cyclic_payload_values() -> None:
    """Reject non-JSON values, non-string keys, and recursive nested containers."""
    cyclic_payload: dict[str, object] = {}
    cyclic_payload["self"] = cyclic_payload
    cyclic_sequence: list[object] = []
    cyclic_sequence.append(cyclic_sequence)
    deeply_nested_value: object = None
    for _ in range(1_500):
        deeply_nested_value = [deeply_nested_value]
    for payload in (
        {1: "value"},
        {"value": b"bytes"},
        {"value": _StringEnum.VALUE},
        {"value": MetadataProvenance(source_kind="source", producer="producer")},
        {"value": datetime.now(UTC)},
        {"value": {"set"}},
        {"value": float("inf")},
        {"value": float("-inf")},
        cyclic_payload,
        {"value": cyclic_sequence},
        {"value": deeply_nested_value},
    ):
        with pytest.raises(
            RegistryCoreValidationError,
            match="^invalid metadata envelope payload$",
        ):
            _build_envelope(payload=payload)


def test_metadata_contracts_preserve_existing_identity_values_without_runtime_adoption() -> None:
    """Use completed identity values structurally without changing their existing semantics."""
    dataset = ResourceKey(
        NamespaceKey("8dbf68a1-9567-4d74-931f-c36c4247f80b"),
        ResourceTypeKey("atlas.data.dataset"),
        "  Dataset-001  ",
    )
    transformation = ResourceKey(
        NamespaceKey("8dbf68a1-9567-4d74-931f-c36c4247f80b"),
        ResourceTypeKey("atlas.transformation.definition"),
        "  Transformation-001  ",
    )

    assert _build_envelope(subject=dataset).subject is dataset
    assert _build_envelope(subject=transformation).subject is transformation
    assert dataset.resource_id == "  Dataset-001  "
    assert transformation.resource_id == "  Transformation-001  "
    assert _build_envelope() != cast(object, _build_envelope().payload)
