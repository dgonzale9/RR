"""Verify the ERR-0.2-S2 Registry Core versioning value contracts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import MISSING, FrozenInstanceError, fields
from enum import Enum
from inspect import Parameter, signature
from typing import cast

import pytest

from resource_registry.registry_core import (
    ArtifactKey,
    NamespaceKey,
    RegistryCoreValidationError,
    ResourceKey,
    ResourceTypeKey,
    ResourceVersionKey,
)


class _StringEnum(str, Enum):
    """Provide a string-derived enum for strict input validation coverage."""

    VALUE = "issuer-value"


_DATASET_RESOURCE_IDENTIFIER = "4af6293b-d09e-4719-90bf-3f47a72f98e8"
_DATASET_VERSION_IDENTIFIER = "167647bb-fc7a-4f89-8f1b-8d0d78b1ba92"
_JSON_SHAPED_OPAQUE_IDENTIFIER = '{"arbitrary": "json"}'
_TRANSFORMATION_VERSION_IDENTIFIER = "4AF6293B-D09E-4719-90BF-3F47A72F98E8"


def _build_resource_key(resource_id: str = "record-1") -> ResourceKey:
    """Build a valid resource key fixture.

    Args:
        resource_id: Issuer-owned resource identifier to include.

    Returns:
        A valid immutable resource key.
    """
    return ResourceKey(
        NamespaceKey("8dbf68a1-9567-4d74-931f-c36c4247f80b"),
        ResourceTypeKey("atlas.data.dataset"),
        resource_id,
    )


def _build_resource_version_key(version_id: str = "version-1") -> ResourceVersionKey:
    """Build a valid resource version key fixture.

    Args:
        version_id: Issuer-owned version identifier to include.

    Returns:
        A valid immutable resource version key.
    """
    return ResourceVersionKey(_build_resource_key(), version_id)


def test_versioning_module_and_package_exports_are_exact() -> None:
    """Expose only the two approved S2 versioning contracts in order."""
    from resource_registry import registry_core
    from resource_registry.registry_core import versioning

    assert versioning.__all__ == ("ResourceVersionKey", "ArtifactKey")
    versioning_start = registry_core.__all__.index("ResourceVersionKey")
    assert registry_core.__all__[versioning_start : versioning_start + 2] == versioning.__all__
    assert ResourceVersionKey.__module__ == "resource_registry.registry_core.versioning"
    assert ArtifactKey.__module__ == "resource_registry.registry_core.versioning"


def test_resource_version_key_has_exact_fields_and_value_semantics() -> None:
    """Construct a version key that preserves opaque issuer text exactly."""
    resource_key = _build_resource_key("  Récord-001  ")
    version_key = ResourceVersionKey(resource_key, "  VÉRSION-001  ")

    assert tuple(field.name for field in fields(ResourceVersionKey)) == ("resource", "version_id")
    assert ResourceVersionKey.__slots__ == ("resource", "version_id")
    assert version_key.resource is resource_key
    assert version_key.version_id == "  VÉRSION-001  "
    assert version_key == ResourceVersionKey(resource_key, "  VÉRSION-001  ")
    assert version_key != ResourceVersionKey(resource_key, "version-001")
    assert version_key != cast(object, (resource_key, "  VÉRSION-001  "))
    assert {version_key: "value"}[ResourceVersionKey(resource_key, "  VÉRSION-001  ")] == "value"
    assert {version_key, ResourceVersionKey(resource_key, "  VÉRSION-001  ")} == {version_key}
    assert repr(version_key).startswith("ResourceVersionKey(resource=ResourceKey(")


@pytest.mark.parametrize(
    ("resource", "version_id", "message"),
    (
        (None, "version", "invalid resource version key resource"),
        ("resource", "version", "invalid resource version key resource"),
        (_build_resource_key(), None, "invalid resource version key id"),
        (_build_resource_key(), "", "invalid resource version key id"),
        (_build_resource_key(), " \t", "invalid resource version key id"),
        (_build_resource_key(), b"version", "invalid resource version key id"),
        (_build_resource_key(), True, "invalid resource version key id"),
        (_build_resource_key(), 1, "invalid resource version key id"),
        (_build_resource_key(), _StringEnum.VALUE, "invalid resource version key id"),
        (None, None, "invalid resource version key resource"),
    ),
)
def test_resource_version_key_validates_components_in_field_order(
    resource: object, version_id: object, message: str
) -> None:
    """Reject invalid version key components with ordered deterministic errors.

    Args:
        resource: Candidate resource value.
        version_id: Candidate issuer-owned version identifier.
        message: Expected validation message.
    """
    with pytest.raises(RegistryCoreValidationError, match=f"^{message}$"):
        ResourceVersionKey(resource, version_id)  # type: ignore[arg-type]


def test_resource_version_key_preserves_long_and_numeric_shaped_ids_without_coercion() -> None:
    """Keep opaque version identifiers unchanged without parsing or normalization."""
    for version_id in ("000123", "4AF6293B-D09E-4719-90BF-3F47A72F98E8", "x" * 4096):
        assert _build_resource_version_key(version_id).version_id == version_id


def test_versioning_keys_preserve_dataset_transformation_and_json_shaped_identifiers() -> None:
    """Preserve authoritative opaque identifier fixtures without interpretation.

    The Dataset resource and version UUIDs are the canonical literals in ADR-004. The
    Transformation UUID fixture is the established uppercase opaque-ID regression value, and the
    JSON-shaped fixture is the accepted Epic 0.1 opaque-reference regression value.
    """
    dataset_resource = _build_resource_key(_DATASET_RESOURCE_IDENTIFIER)
    dataset_version = ResourceVersionKey(dataset_resource, _DATASET_VERSION_IDENTIFIER)
    transformation_version = ResourceVersionKey(
        dataset_resource,
        _TRANSFORMATION_VERSION_IDENTIFIER,
    )
    artifact_key = ArtifactKey(
        resource=dataset_resource,
        version=dataset_version,
        artifact_id=_JSON_SHAPED_OPAQUE_IDENTIFIER,
    )

    assert dataset_version.resource is dataset_resource
    assert dataset_version.version_id == _DATASET_VERSION_IDENTIFIER
    assert transformation_version.resource is dataset_resource
    assert transformation_version.version_id == _TRANSFORMATION_VERSION_IDENTIFIER
    assert artifact_key.resource is dataset_resource
    assert artifact_key.version is dataset_version
    assert artifact_key.artifact_id == _JSON_SHAPED_OPAQUE_IDENTIFIER


def test_resource_version_key_is_frozen_and_slotted() -> None:
    """Reject mutation and dynamic attributes on a version key."""
    version_key = _build_resource_version_key()

    with pytest.raises(FrozenInstanceError):
        version_key.version_id = "other"  # type: ignore[misc]
    with pytest.raises(TypeError):
        version_key.dynamic_value = "value"  # type: ignore[attr-defined]


def test_artifact_key_has_exact_keyword_only_fields_defaults_and_value_semantics() -> None:
    """Construct keyword-only artifact keys with approved structural semantics."""
    resource_key = _build_resource_key("  Récord-001  ")
    version_key = ResourceVersionKey(resource_key, "  VERSION-001  ")
    artifact_key = ArtifactKey(
        resource=resource_key,
        version=version_key,
        artifact_id="  ÁrTIFACT-001  ",
    )
    constructor_parameters = signature(ArtifactKey).parameters

    assert tuple(field.name for field in fields(ArtifactKey)) == (
        "resource",
        "version",
        "artifact_id",
    )
    assert ArtifactKey.__slots__ == ("resource", "version", "artifact_id")
    assert fields(ArtifactKey)[0].default is MISSING
    assert fields(ArtifactKey)[1].default is None
    assert fields(ArtifactKey)[2].default is MISSING
    assert tuple(parameter.kind for parameter in constructor_parameters.values()) == (
        Parameter.KEYWORD_ONLY,
        Parameter.KEYWORD_ONLY,
        Parameter.KEYWORD_ONLY,
    )
    assert artifact_key.resource is resource_key
    assert artifact_key.version is version_key
    assert artifact_key.artifact_id == "  ÁrTIFACT-001  "
    assert ArtifactKey(resource=resource_key, artifact_id="artifact") == ArtifactKey(
        resource=resource_key,
        artifact_id="artifact",
    )
    assert ArtifactKey(resource=resource_key, artifact_id="artifact") != ArtifactKey(
        resource=resource_key,
        version=version_key,
        artifact_id="artifact",
    )
    assert {artifact_key: "value"}[
        ArtifactKey(
            resource=resource_key,
            version=version_key,
            artifact_id="  ÁrTIFACT-001  ",
        )
    ] == "value"
    assert {
        artifact_key,
        ArtifactKey(resource=resource_key, version=version_key, artifact_id="  ÁrTIFACT-001  "),
    } == {artifact_key}
    assert repr(artifact_key).startswith("ArtifactKey(resource=ResourceKey(")
    assert repr(artifact_key).index("resource=") < repr(artifact_key).index("version=")
    assert repr(artifact_key).index("version=") < repr(artifact_key).index("artifact_id=")


@pytest.mark.parametrize(
    "call_artifact_key",
    (
        lambda: ArtifactKey(),  # type: ignore[call-arg]
        lambda: ArtifactKey(resource=_build_resource_key()),  # type: ignore[call-arg]
        lambda: ArtifactKey(_build_resource_key(), None, "artifact"),  # type: ignore[misc]
        lambda: ArtifactKey(_build_resource_key(), artifact_id="artifact"),  # type: ignore[misc]
    ),
)
def test_artifact_key_rejects_missing_or_positional_arguments(
    call_artifact_key: Callable[[], ArtifactKey],
) -> None:
    """Reject unsupported constructor calls through Python signature binding.

    Args:
        call_artifact_key: Zero-argument callable that attempts invalid construction.
    """
    with pytest.raises(TypeError):
        call_artifact_key()


def test_artifact_key_validates_version_before_invalid_artifact_id() -> None:
    """Reject an invalid version before evaluating an invalid artifact identifier."""
    with pytest.raises(RegistryCoreValidationError, match="^invalid artifact key version$"):
        ArtifactKey(
            resource=_build_resource_key(),
            version="invalid-version",  # type: ignore[arg-type]
            artifact_id=None,  # type: ignore[arg-type]
        )


def test_artifact_key_validates_parent_mismatch_before_invalid_artifact_id() -> None:
    """Reject a mismatched version parent before evaluating an invalid artifact identifier."""
    with pytest.raises(
        RegistryCoreValidationError,
        match="^artifact key version resource mismatch$",
    ):
        ArtifactKey(
            resource=_build_resource_key("other-record"),
            version=_build_resource_version_key(),
            artifact_id=None,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("resource", "version", "artifact_id", "message"),
    (
        (None, None, "artifact", "invalid artifact key resource"),
        ("resource", None, "artifact", "invalid artifact key resource"),
        (_build_resource_key(), "version", "artifact", "invalid artifact key version"),
        (
            _build_resource_key("other-record"),
            _build_resource_version_key(),
            "artifact",
            "artifact key version resource mismatch",
        ),
        (_build_resource_key(), None, None, "invalid artifact key id"),
        (_build_resource_key(), None, "", "invalid artifact key id"),
        (_build_resource_key(), None, " \n", "invalid artifact key id"),
        (_build_resource_key(), None, b"artifact", "invalid artifact key id"),
        (_build_resource_key(), None, True, "invalid artifact key id"),
        (_build_resource_key(), None, 1, "invalid artifact key id"),
        (_build_resource_key(), None, _StringEnum.VALUE, "invalid artifact key id"),
        (None, "version", None, "invalid artifact key resource"),
    ),
)
def test_artifact_key_validates_components_in_field_order(
    resource: object, version: object, artifact_id: object, message: str
) -> None:
    """Reject invalid artifact key components with ordered deterministic errors.

    Args:
        resource: Candidate resource value.
        version: Candidate optional resource version value.
        artifact_id: Candidate issuer-owned artifact identifier.
        message: Expected validation message.
    """
    with pytest.raises(RegistryCoreValidationError, match=f"^{message}$"):
        ArtifactKey(
            resource=resource,  # type: ignore[arg-type]
            version=version,  # type: ignore[arg-type]
            artifact_id=artifact_id,  # type: ignore[arg-type]
        )


def test_artifact_key_preserves_long_and_numeric_shaped_ids_without_coercion() -> None:
    """Keep opaque artifact identifiers unchanged without parsing or normalization."""
    resource_key = _build_resource_key()
    for artifact_id in ("000123", "4AF6293B-D09E-4719-90BF-3F47A72F98E8", "x" * 4096):
        assert (
            ArtifactKey(resource=resource_key, artifact_id=artifact_id).artifact_id == artifact_id
        )


def test_artifact_key_is_frozen_and_slotted() -> None:
    """Reject mutation and dynamic attributes on an artifact key."""
    artifact_key = ArtifactKey(resource=_build_resource_key(), artifact_id="artifact")

    with pytest.raises(FrozenInstanceError):
        artifact_key.artifact_id = "other"  # type: ignore[misc]
    with pytest.raises(TypeError):
        artifact_key.dynamic_value = "value"  # type: ignore[attr-defined]
