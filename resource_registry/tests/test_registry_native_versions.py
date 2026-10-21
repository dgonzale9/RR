"""Verify the ERR-1.4-S1 pure Registry-native declaration contracts."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from resource_registry.identity_type_module import ResourceTypeDeclaration, ResourceTypeRegistry
from resource_registry.namespace_module import NamespaceDeclaration, NamespaceRegistry
from resource_registry.registry_core import (
    ArtifactKey,
    MetadataClassification,
    MetadataEnvelope,
    MetadataNamespaceKey,
    MetadataProvenance,
    MetadataSchemaKey,
    MetadataScope,
    NamespaceKey,
    OwnerDescriptor,
    OwnershipClassification,
    RegistryCoreValidationError,
    ResourceKey,
    ResourceTypeKey,
    ResourceVersionKey,
)
from resource_registry.registry_native import (
    RegistryNativeArtifact,
    RegistryNativeRepresentation,
    RegistryNativeVersion,
    RegistryNativeVersionRegistry,
)
from resource_registry.registry_native_resource_module import (
    RegistryNativeResource,
    RegistryNativeResourceLifecycle,
    RegistryNativeResourceRegistry,
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_NATIVE_PACKAGE_ROOT = _REPOSITORY_ROOT / "resource_registry" / "registry_native"
_PROHIBITED_IMPORT_ROOTS = frozenset(
    {
        "django",
        "BotPlatform",
        "resource_registry.api",
        "resource_registry.application",
        "resource_registry.bifs",
        "resource_registry.models",
        "resource_registry.storage",
    }
)


def _build_resource_key(resource_id: str = "native-1") -> ResourceKey:
    """Build one valid native resource key.

    Args:
        resource_id: Exact resource-local identifier.

    Returns:
        A valid Registry-native resource key.
    """
    return ResourceKey(
        NamespaceKey(UUID("8dbf68a1-9567-4d74-931f-c36c4247f80b")),
        ResourceTypeKey("atlas.registry.reference"),
        resource_id,
    )


def _build_version_key(version_id: str = "v1") -> ResourceVersionKey:
    """Build a valid version key for the default resource.

    Args:
        version_id: Exact version-local identifier.

    Returns:
        A valid version key.
    """
    return ResourceVersionKey(_build_resource_key(), version_id)


def _build_artifact_key(artifact_id: str = "source") -> ArtifactKey:
    """Build a valid version-owned artifact key.

    Args:
        artifact_id: Exact artifact-local identifier.

    Returns:
        A valid artifact key.
    """
    return ArtifactKey(
        resource=_build_resource_key(),
        version=_build_version_key(),
        artifact_id=artifact_id,
    )


def _build_metadata(scope: MetadataScope, subject: object) -> MetadataEnvelope:
    """Build a valid metadata envelope with an exact supplied subject.

    Args:
        scope: Required metadata scope.
        subject: Exact typed metadata subject for the scope.

    Returns:
        A valid immutable metadata envelope.
    """
    return MetadataEnvelope(
        scope=scope,
        subject=subject,  # type: ignore[arg-type]
        schema=MetadataSchemaKey(
            namespace=MetadataNamespaceKey(value="atlas.registry.reference"),
            name="details",
        ),
        schema_version="1",
        revision=1,
        classification=MetadataClassification.INTERNAL,
        provenance=MetadataProvenance(source_kind="registry", producer="fixture"),
        observed_at=datetime(2026, 7, 22, tzinfo=UTC),
        payload={"label": "native"},
    )


def _build_native_resource_registry() -> RegistryNativeResourceRegistry:
    """Build a registry admitting the default fixture resource.

    Returns:
        An admitted immutable Registry-native resource registry.
    """
    resource_key = _build_resource_key()
    return RegistryNativeResourceRegistry(
        ResourceTypeRegistry(
            (
                ResourceTypeDeclaration(
                    resource_key.resource_type,
                    OwnerDescriptor("registry-core", OwnershipClassification.REGISTRY_NATIVE),
                ),
            )
        ),
        NamespaceRegistry((NamespaceDeclaration(resource_key.namespace, 42),)),
        (
            RegistryNativeResource(
                resource_key,
                RegistryNativeResourceLifecycle.DRAFT,
                (),
            ),
        ),
    )


def _build_version(ordinal: int = 1, version_id: str = "v1") -> RegistryNativeVersion:
    """Build a valid native version declaration.

    Args:
        ordinal: Positive resource-local ordinal.
        version_id: Exact version-local identifier.

    Returns:
        A valid immutable native version.
    """
    version_key = _build_version_key(version_id)
    return RegistryNativeVersion(
        _build_resource_key(),
        version_key,
        ordinal,
        (_build_metadata(MetadataScope.VERSION, version_key),),
    )


def _build_artifact(artifact_id: str = "source") -> RegistryNativeArtifact:
    """Build a valid native artifact declaration.

    Args:
        artifact_id: Exact artifact-local identifier.

    Returns:
        A valid immutable native artifact.
    """
    artifact_key = _build_artifact_key(artifact_id)
    return RegistryNativeArtifact(
        _build_version_key(),
        artifact_key,
        (_build_metadata(MetadataScope.ARTIFACT, artifact_key),),
    )


def _build_registry() -> RegistryNativeVersionRegistry:
    """Build a complete native version declaration registry.

    Returns:
        A valid immutable native version registry.
    """
    artifact = _build_artifact()
    return RegistryNativeVersionRegistry(
        _build_native_resource_registry(),
        (_build_version(),),
        (artifact,),
        (RegistryNativeRepresentation(artifact.artifact_key, "json", "application/json"),),
    )


def test_native_version_package_exports_only_authorized_contracts() -> None:
    """Expose the exact approved public package boundary without root exports."""
    from resource_registry import registry_native

    assert registry_native.__all__ == (
        "RegistryNativeVersion",
        "RegistryNativeArtifact",
        "RegistryNativeRepresentation",
        "RegistryNativeVersionRegistry",
    )
    assert not hasattr(__import__("resource_registry"), "RegistryNativeVersion")
    from resource_registry import registry_core, registry_native_resource_module

    assert not hasattr(registry_core, "RegistryNativeVersion")
    assert not hasattr(registry_native_resource_module, "RegistryNativeVersion")
    assert tuple(field.name for field in fields(RegistryNativeVersion)) == (
        "resource_key",
        "version_key",
        "ordinal",
        "metadata",
    )
    assert tuple(field.name for field in fields(RegistryNativeArtifact)) == (
        "version_key",
        "artifact_key",
        "metadata",
    )
    assert tuple(field.name for field in fields(RegistryNativeRepresentation)) == (
        "artifact_key",
        "representation_id",
        "media_type",
    )


def test_native_version_package_has_clean_import_and_no_runtime_dependencies() -> None:
    """Import the package in isolation and reject prohibited dependency imports."""
    environment = os.environ.copy()
    environment.pop("DJANGO_SETTINGS_MODULE", None)
    process = subprocess.run(
        [sys.executable, "-c", "import resource_registry.registry_native"],
        cwd=_REPOSITORY_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert process.returncode == 0, process.stderr
    for source_path in _NATIVE_PACKAGE_ROOT.glob("*.py"):
        syntax_tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imported_modules = {
            imported_name.name.split(".")[0]
            for node in ast.walk(syntax_tree)
            if isinstance(node, ast.Import)
            for imported_name in node.names
        }
        imported_modules.update(
            node.module
            for node in ast.walk(syntax_tree)
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module is not None
        )
        assert not any(
            imported_module in _PROHIBITED_IMPORT_ROOTS
            or any(
                imported_module.startswith(f"{prohibited_import_root}.")
                for prohibited_import_root in _PROHIBITED_IMPORT_ROOTS
            )
            for imported_module in imported_modules
        )


def test_native_declarations_are_immutable_and_preserve_value_semantics() -> None:
    """Preserve frozen equality, hashing, and deterministic dataclass representations."""
    version = _build_version()
    artifact = _build_artifact()
    representation = RegistryNativeRepresentation(artifact.artifact_key, "json", "application/json")

    assert version == _build_version()
    assert representation == RegistryNativeRepresentation(
        artifact.artifact_key,
        "json",
        "application/json",
    )
    assert hash(representation) == hash(
        RegistryNativeRepresentation(artifact.artifact_key, "json", "application/json")
    )
    assert "RegistryNativeVersion(" in repr(version)
    with pytest.raises(FrozenInstanceError):
        version.ordinal = 2  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        artifact.metadata = ()  # type: ignore[misc]


def test_native_declarations_protect_against_metadata_and_collection_aliases() -> None:
    """Freeze metadata inputs and reject mutable declaration collections."""
    version_key = _build_version_key()
    metadata_payload = {"nested": ["original"]}
    metadata = MetadataEnvelope(
        scope=MetadataScope.VERSION,
        subject=version_key,
        schema=MetadataSchemaKey(
            namespace=MetadataNamespaceKey(value="atlas.registry.reference"),
            name="details",
        ),
        schema_version="1",
        revision=1,
        classification=MetadataClassification.INTERNAL,
        provenance=MetadataProvenance(source_kind="registry", producer="fixture"),
        observed_at=datetime(2026, 7, 22, tzinfo=UTC),
        payload=metadata_payload,
    )
    version = RegistryNativeVersion(_build_resource_key(), version_key, 1, (metadata,))
    metadata_payload["nested"].append("mutated")

    assert version.metadata[0].payload["nested"] == ("original",)
    with pytest.raises(RegistryCoreValidationError, match="^invalid native version metadata$"):
        RegistryNativeVersion(_build_resource_key(), version_key, 1, [metadata])  # type: ignore[arg-type]
    with pytest.raises(RegistryCoreValidationError, match="^invalid native version registry$"):
        RegistryNativeVersionRegistry(
            _build_native_resource_registry(),
            [_build_version()],  # type: ignore[arg-type]
            (),
            (),
        )


def test_native_declarations_apply_declared_field_validation_precedence() -> None:
    """Report the first invalid public field when multiple inputs are defective."""
    with pytest.raises(
        RegistryCoreValidationError,
        match="^invalid native version resource key$",
    ):
        RegistryNativeVersion("invalid", "invalid", True, [])  # type: ignore[arg-type]
    with pytest.raises(
        RegistryCoreValidationError,
        match="^invalid native artifact version key$",
    ):
        RegistryNativeArtifact("invalid", "invalid", [])  # type: ignore[arg-type]
    with pytest.raises(
        RegistryCoreValidationError,
        match="^invalid native representation artifact key$",
    ):
        RegistryNativeRepresentation("invalid", " ", " ")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("factory", "message"),
    (
        (
            lambda: RegistryNativeVersion("invalid", _build_version_key(), 1, ()),
            "invalid native version resource key",
        ),
        (
            lambda: RegistryNativeVersion(_build_resource_key(), "invalid", 1, ()),
            "invalid native version key",
        ),
        (
            lambda: RegistryNativeVersion(_build_resource_key(), _build_version_key(), True, ()),
            "invalid native version ordinal",
        ),
        (
            lambda: RegistryNativeArtifact("invalid", _build_artifact_key(), ()),
            "invalid native artifact version key",
        ),
        (
            lambda: RegistryNativeRepresentation("invalid", "json", "application/json"),
            "invalid native representation artifact key",
        ),
        (
            lambda: RegistryNativeRepresentation(_build_artifact_key(), " ", "application/json"),
            "invalid native representation id",
        ),
        (
            lambda: RegistryNativeRepresentation(_build_artifact_key(), "json", " "),
            "invalid native representation media type",
        ),
    ),
)
def test_native_declarations_reject_invalid_fields_in_declared_order(
    factory: object,
    message: str,
) -> None:
    """Reject invalid declaration fields with exact stable errors.

    Args:
        factory: Deferred invalid construction callable.
        message: Expected stable validation message.
    """
    with pytest.raises(RegistryCoreValidationError, match=f"^{message}$"):
        factory()  # type: ignore[operator]


def test_native_declarations_reject_mismatched_metadata_and_parent_identity() -> None:
    """Reject metadata or key associations that cross exact parent identities."""
    with pytest.raises(RegistryCoreValidationError, match="^invalid native version key$"):
        RegistryNativeVersion(
            _build_resource_key(),
            ResourceVersionKey(_build_resource_key("other"), "v1"),
            1,
            (),
        )
    with pytest.raises(RegistryCoreValidationError, match="^invalid native version metadata$"):
        RegistryNativeVersion(
            _build_resource_key(),
            _build_version_key(),
            1,
            (_build_metadata(MetadataScope.VERSION, _build_version_key("v2")),),
        )
    with pytest.raises(RegistryCoreValidationError, match="^invalid native artifact key$"):
        RegistryNativeArtifact(
            _build_version_key(),
            ArtifactKey(resource=_build_resource_key(), artifact_id="unversioned"),
            (),
        )
    with pytest.raises(RegistryCoreValidationError, match="^invalid native artifact metadata$"):
        RegistryNativeArtifact(
            _build_version_key(),
            _build_artifact_key(),
            (_build_metadata(MetadataScope.ARTIFACT, _build_artifact_key("other")),),
        )


def test_native_version_registry_preserves_declaration_order_and_exact_resolution() -> None:
    """Preserve declared tuples while resolving only exact declaration identities."""
    first_version = _build_version(2, "v2")
    second_version = _build_version(1, "v1")
    first_artifact = _build_artifact("source")
    second_artifact = _build_artifact("preview")
    registry = RegistryNativeVersionRegistry(
        _build_native_resource_registry(),
        (first_version, second_version),
        (first_artifact, second_artifact),
        (
            RegistryNativeRepresentation(first_artifact.artifact_key, "json", "application/json"),
            RegistryNativeRepresentation(second_artifact.artifact_key, "json", "application/json"),
        ),
    )

    assert registry.versions == (first_version, second_version)
    assert registry.resolve_versions_for_resource(_build_resource_key()) == (
        second_version,
        first_version,
    )
    assert registry.resolve_version(second_version.version_key) == second_version
    assert registry.resolve_artifact(second_artifact.artifact_key) == second_artifact
    assert registry.resolve_representation(second_artifact.artifact_key, "json").artifact_key == (
        second_artifact.artifact_key
    )
    with pytest.raises(
        RegistryCoreValidationError,
        match="^unknown native version parent resource$",
    ):
        registry.resolve_version(_build_version_key("missing"))
    with pytest.raises(
        RegistryCoreValidationError,
        match="^unknown native artifact parent version$",
    ):
        registry.resolve_artifact(_build_artifact_key("missing"))
    with pytest.raises(
        RegistryCoreValidationError,
        match="^unknown native representation parent artifact$",
    ):
        registry.resolve_representation(second_artifact.artifact_key, "xml")


def test_native_version_registry_allows_empty_declarations_and_hashable_values() -> None:
    """Represent empty native declaration sets without mutable or unhashable state."""
    empty_registry = RegistryNativeVersionRegistry(
        _build_native_resource_registry(),
        (),
        (),
        (),
    )
    version_without_metadata = RegistryNativeVersion(
        _build_resource_key(),
        _build_version_key(),
        1,
        (),
    )
    artifact_without_metadata = RegistryNativeArtifact(
        _build_version_key(),
        _build_artifact_key(),
        (),
    )

    assert empty_registry.versions == ()
    assert empty_registry.artifacts == ()
    assert empty_registry.representations == ()
    assert empty_registry.resolve_versions_for_resource(_build_resource_key()) == ()
    assert hash(version_without_metadata) == hash(version_without_metadata)
    assert hash(artifact_without_metadata) == hash(artifact_without_metadata)
    assert hash(empty_registry) == hash(empty_registry)


@pytest.mark.parametrize(
    ("versions", "artifacts", "representations", "message"),
    (
        ((_build_version(), _build_version()), (), (), "duplicate native version key"),
        (
            (_build_version(1, "v1"), _build_version(1, "v2")),
            (),
            (),
            "duplicate native version ordinal",
        ),
        (
            (_build_version(),),
            (_build_artifact(), _build_artifact()),
            (),
            "duplicate native artifact key",
        ),
        (
            (_build_version(),),
            (_build_artifact(),),
            (
                RegistryNativeRepresentation(_build_artifact_key(), "json", "application/json"),
                RegistryNativeRepresentation(_build_artifact_key(), "json", "text/json"),
            ),
            "duplicate native representation",
        ),
    ),
)
def test_native_version_registry_rejects_exact_duplicate_declarations(
    versions: tuple[RegistryNativeVersion, ...],
    artifacts: tuple[RegistryNativeArtifact, ...],
    representations: tuple[RegistryNativeRepresentation, ...],
    message: str,
) -> None:
    """Reject duplicate declaration keys only after all parents are known.

    Args:
        versions: Candidate native versions.
        artifacts: Candidate native artifacts.
        representations: Candidate native representations.
        message: Expected exact duplicate error.
    """
    with pytest.raises(RegistryCoreValidationError, match=f"^{message}$"):
        RegistryNativeVersionRegistry(
            _build_native_resource_registry(), versions, artifacts, representations
        )


def test_native_version_registry_rejects_unknown_parents_before_duplicate_checks() -> None:
    """Fail closed when a declaration parent is absent before duplicate validation runs."""
    unknown_version = RegistryNativeVersion(
        _build_resource_key("other"),
        ResourceVersionKey(_build_resource_key("other"), "v1"),
        1,
        (),
    )
    with pytest.raises(
        RegistryCoreValidationError,
        match="^unknown native version parent resource$",
    ):
        RegistryNativeVersionRegistry(
            _build_native_resource_registry(),
            (unknown_version, unknown_version),
            (),
            (),
        )
    with pytest.raises(
        RegistryCoreValidationError,
        match="^unknown native artifact parent version$",
    ):
        RegistryNativeVersionRegistry(
            _build_native_resource_registry(),
            (),
            (_build_artifact(),),
            (),
        )
    with pytest.raises(
        RegistryCoreValidationError,
        match="^unknown native representation parent artifact$",
    ):
        RegistryNativeVersionRegistry(
            _build_native_resource_registry(),
            (_build_version(),),
            (),
            (RegistryNativeRepresentation(_build_artifact_key(), "json", "application/json"),),
        )
