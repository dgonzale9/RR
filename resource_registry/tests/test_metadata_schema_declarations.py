"""Test ERR-1.5-S1 metadata schema declaration contracts."""

from __future__ import annotations

import ast
import inspect
import os
import subprocess
import sys
from collections.abc import Callable, Iterable, Iterator
from dataclasses import fields
from pathlib import Path
from types import MappingProxyType
from typing import cast

import pytest

from resource_registry.metadata_schema import (
    MetadataNamespaceDeclaration,
    MetadataPayloadBounds,
    MetadataSchemaRegistry,
    MetadataSchemaVersion,
)
from resource_registry.registry_core import (
    MetadataClassification,
    MetadataEnvelope,
    MetadataNamespaceKey,
    MetadataSchemaKey,
    MetadataScope,
    RegistryCoreValidationError,
)


def _build_namespace(value: str = "atlas.metadata.example") -> MetadataNamespaceKey:
    return MetadataNamespaceKey(value=value)


def _build_namespace_declaration(
    namespace: MetadataNamespaceKey | None = None,
) -> MetadataNamespaceDeclaration:
    return MetadataNamespaceDeclaration(
        namespace=namespace or _build_namespace(),
        semantic_owner="metadata-team",
        persistence_owner="registry-store",
    )


def _build_bounds() -> MetadataPayloadBounds:
    return MetadataPayloadBounds(
        max_payload_bytes=1024,
        max_nesting_depth=8,
        max_object_key_count=32,
        max_collection_item_count=64,
    )


def _build_schema_version(
    namespace: MetadataNamespaceKey | None = None,
    schema_version: str = "release-2026.07",
) -> MetadataSchemaVersion:
    schema_namespace = namespace or _build_namespace()
    return MetadataSchemaVersion(
        namespace=schema_namespace,
        schema=MetadataSchemaKey(namespace=schema_namespace, name="example"),
        schema_version=schema_version,
        semantic_owner="metadata-team",
        persistence_owner="registry-store",
        allowed_scopes=(MetadataScope.RESOURCE, MetadataScope.VERSION),
        classification=MetadataClassification.INTERNAL,
        bounds=_build_bounds(),
    )


def _build_registry() -> MetadataSchemaRegistry:
    namespace = _build_namespace()
    return MetadataSchemaRegistry(
        namespace_declarations=(_build_namespace_declaration(namespace),),
        schema_version_declarations=(_build_schema_version(namespace),),
    )


def test_exports_the_approved_s1_to_s5_public_contracts() -> None:
    from resource_registry import metadata_schema, registry_native

    assert metadata_schema.__all__ == (
        "MetadataNamespaceDeclaration",
        "MetadataPayloadBounds",
        "MetadataSchemaVersion",
        "MetadataSchemaRegistry",
        "MetadataSchemaValidationError",
        "MetadataFieldDefinition",
        "MetadataFieldClassification",
        "MetadataClassificationProfile",
        "MetadataSchemaDefinition",
        "MetadataSchemaDefinitionRegistry",
        "MetadataCompatibilityStatus",
        "MetadataCompatibilityAssessment",
        "MetadataSchemaPublicationValidationError",
        "MetadataSchemaPublicationStatus",
        "MetadataSchemaPublication",
        "MetadataSchemaActiveSelection",
        "MetadataSchemaPublicationResult",
        "MetadataSchemaActivationResult",
        "MetadataSchemaPublicationChange",
        "MetadataSchemaActivationChange",
        "MetadataSchemaPublicationRegistry",
        "MetadataEnvelopeAdmissionStatus",
        "MetadataEnvelopeAdmissionFieldEvidence",
        "MetadataEnvelopeSchemaBinding",
        "MetadataEnvelopeAdmissionResult",
        "MetadataEnvelopeAdmissionReader",
        "MetadataEnvelopeAdmission",
        "MetadataPayloadMeasurementError",
        "MetadataPayloadMeasurement",
        "measure_metadata_payload",
    )
    assert not hasattr(__import__("resource_registry"), "MetadataSchemaRegistry")
    assert not hasattr(
        __import__(
            "resource_registry.registry_core",
            fromlist=["*"],
        ),
        "MetadataSchemaRegistry",
    )
    assert not hasattr(registry_native, "MetadataSchemaRegistry")


def test_declarations_depend_only_on_registry_core_and_standard_library() -> None:
    source_path = Path(__file__).parents[1] / "metadata_schema" / "declarations.py"
    source_tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported_modules = {
        node.module
        for node in ast.walk(source_tree)
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module is not None
    }
    assert imported_modules == {
        "__future__",
        "collections.abc",
        "dataclasses",
        "types",
        "typing",
        "resource_registry.registry_core",
    }


def test_metadata_schema_imports_in_a_clean_python_process() -> None:
    import_environment = os.environ.copy()
    import_environment.pop("DJANGO_SETTINGS_MODULE", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from resource_registry.metadata_schema import MetadataSchemaRegistry; "
            "assert MetadataSchemaRegistry.__name__ == 'MetadataSchemaRegistry'",
        ],
        cwd=Path(__file__).parents[2],
        check=False,
        capture_output=True,
        env=import_environment,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_declaration_fields_are_exact_and_keyword_only() -> None:
    assert tuple(item.name for item in fields(MetadataNamespaceDeclaration)) == (
        "namespace",
        "semantic_owner",
        "persistence_owner",
    )
    assert tuple(item.name for item in fields(MetadataPayloadBounds)) == (
        "max_payload_bytes",
        "max_nesting_depth",
        "max_object_key_count",
        "max_collection_item_count",
    )
    assert tuple(item.name for item in fields(MetadataSchemaVersion)) == (
        "namespace",
        "schema",
        "schema_version",
        "semantic_owner",
        "persistence_owner",
        "allowed_scopes",
        "classification",
        "bounds",
    )
    for contract in (MetadataNamespaceDeclaration, MetadataPayloadBounds, MetadataSchemaVersion):
        assert all(
            parameter.kind is inspect.Parameter.KEYWORD_ONLY
            for parameter in inspect.signature(contract).parameters.values()
        )


def test_declarations_are_frozen_slotted_and_structurally_equal() -> None:
    namespace_declaration = _build_namespace_declaration()
    bounds = _build_bounds()
    schema_version = _build_schema_version()

    assert namespace_declaration == _build_namespace_declaration()
    assert bounds == _build_bounds()
    assert schema_version == _build_schema_version()
    assert hash(namespace_declaration) == hash(_build_namespace_declaration())
    assert hash(bounds) == hash(_build_bounds())
    assert hash(schema_version) == hash(_build_schema_version())
    assert "MetadataSchemaVersion(" in repr(schema_version)
    for declaration in (namespace_declaration, bounds, schema_version):
        assert hasattr(declaration, "__slots__")
        with pytest.raises((AttributeError, TypeError)):
            setattr(declaration, next(iter(declaration.__dataclass_fields__)), None)


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (
            lambda: MetadataNamespaceDeclaration(
                namespace=cast(MetadataNamespaceKey, "invalid"),
                semantic_owner="owner",
                persistence_owner="store",
            ),
            "invalid metadata namespace declaration key",
        ),
        (
            lambda: MetadataNamespaceDeclaration(
                namespace=_build_namespace(), semantic_owner=" ", persistence_owner="store"
            ),
            "invalid metadata namespace declaration semantic owner",
        ),
        (
            lambda: MetadataNamespaceDeclaration(
                namespace=_build_namespace(), semantic_owner="owner", persistence_owner=" "
            ),
            "invalid metadata namespace declaration persistence owner",
        ),
        (
            lambda: MetadataPayloadBounds(
                max_payload_bytes=True,
                max_nesting_depth=1,
                max_object_key_count=1,
                max_collection_item_count=1,
            ),
            "invalid metadata schema declaration bounds",
        ),
        (
            lambda: MetadataPayloadBounds(
                max_payload_bytes=1,
                max_nesting_depth=0,
                max_object_key_count=1,
                max_collection_item_count=1,
            ),
            "invalid metadata schema declaration bounds",
        ),
    ],
)
def test_intrinsic_validation_uses_stable_errors(
    factory: Callable[[], object], message: str
) -> None:
    with pytest.raises(RegistryCoreValidationError, match=f"^{message}$"):
        factory()


@pytest.mark.parametrize(
    "field_name",
    (
        "max_payload_bytes",
        "max_nesting_depth",
        "max_object_key_count",
        "max_collection_item_count",
    ),
)
@pytest.mark.parametrize("invalid_value", (0, -1, True))
def test_each_payload_bound_rejects_non_positive_or_boolean_ints(
    field_name: str,
    invalid_value: int | bool,
) -> None:
    values = dict(
        max_payload_bytes=1,
        max_nesting_depth=1,
        max_object_key_count=1,
        max_collection_item_count=1,
    )
    values[field_name] = invalid_value

    with pytest.raises(
        RegistryCoreValidationError, match="^invalid metadata schema declaration bounds$"
    ):
        MetadataPayloadBounds(**values)


def test_schema_version_requires_exact_intrinsic_contract_and_namespace_association() -> None:
    namespace = _build_namespace()
    schema = MetadataSchemaKey(namespace=namespace, name="example")
    invalid_values = (
        ("namespace", "invalid", "invalid metadata schema declaration namespace"),
        ("schema", "invalid", "invalid metadata schema declaration key"),
        ("schema_version", " ", "invalid metadata schema declaration version"),
        ("semantic_owner", " ", "invalid metadata schema declaration semantic owner"),
        ("persistence_owner", " ", "invalid metadata schema declaration persistence owner"),
        ("allowed_scopes", (), "invalid metadata schema declaration scopes"),
        ("classification", "internal", "invalid metadata schema declaration classification"),
        ("bounds", "invalid", "invalid metadata schema declaration bounds"),
    )
    values = dict(
        namespace=namespace,
        schema=schema,
        schema_version="v1",
        semantic_owner="owner",
        persistence_owner="store",
        allowed_scopes=(MetadataScope.RESOURCE,),
        classification=MetadataClassification.INTERNAL,
        bounds=_build_bounds(),
    )
    for field_name, invalid_value, message in invalid_values:
        candidate_values = {**values, field_name: invalid_value}
        with pytest.raises(RegistryCoreValidationError, match=f"^{message}$"):
            MetadataSchemaVersion(**candidate_values)  # type: ignore[arg-type]
    with pytest.raises(
        RegistryCoreValidationError, match="^metadata schema declaration namespace mismatch$"
    ):
        MetadataSchemaVersion(
            **{**values, "namespace": _build_namespace("atlas.metadata.other")}  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "allowed_scopes",
    (
        [MetadataScope.RESOURCE],
        (MetadataScope.RESOURCE, MetadataScope.RESOURCE),
        ("resource",),
    ),
)
def test_schema_version_requires_non_empty_unique_scope_tuple(allowed_scopes: object) -> None:
    namespace = _build_namespace()
    with pytest.raises(
        RegistryCoreValidationError, match="^invalid metadata schema declaration scopes$"
    ):
        MetadataSchemaVersion(
            namespace=namespace,
            schema=MetadataSchemaKey(namespace=namespace, name="example"),
            schema_version="v1",
            semantic_owner="owner",
            persistence_owner="store",
            allowed_scopes=allowed_scopes,  # type: ignore[arg-type]
            classification=MetadataClassification.INTERNAL,
            bounds=_build_bounds(),
        )


def test_schema_version_is_opaque_and_matches_metadata_envelope_version_semantics() -> None:
    declaration = _build_schema_version(schema_version=" v1 ")
    assert declaration.schema_version == " v1 "
    assert declaration.schema_version != declaration.schema_version.strip()
    assert MetadataEnvelope.__dataclass_fields__["schema_version"].type == "str"


def test_registry_signature_is_exact_and_arguments_are_required_keyword_only() -> None:
    signature = inspect.signature(MetadataSchemaRegistry)
    assert tuple(signature.parameters) == ("namespace_declarations", "schema_version_declarations")
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in signature.parameters.values()
    )
    with pytest.raises(TypeError):
        MetadataSchemaRegistry()  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        MetadataSchemaRegistry((), ())  # type: ignore[misc]


@pytest.mark.parametrize("invalid_input", [None, "invalid", b"invalid", {}, 1])
def test_registry_rejects_invalid_iterables_before_declaration_checks(
    invalid_input: object,
) -> None:
    with pytest.raises(RegistryCoreValidationError, match="^invalid metadata schema registry$"):
        MetadataSchemaRegistry(
            namespace_declarations=cast(Iterable[MetadataNamespaceDeclaration], invalid_input),
            schema_version_declarations=(),
        )
    with pytest.raises(RegistryCoreValidationError, match="^invalid metadata schema registry$"):
        MetadataSchemaRegistry(
            namespace_declarations=(),
            schema_version_declarations=cast(Iterable[MetadataSchemaVersion], invalid_input),
        )


def test_registry_snapshots_generators_once_preserves_order_and_prevents_aliasing() -> None:
    first_namespace = _build_namespace("atlas.metadata.first")
    second_namespace = _build_namespace("atlas.metadata.second")
    namespace_items = [
        _build_namespace_declaration(first_namespace),
        _build_namespace_declaration(second_namespace),
    ]
    schema_items = [_build_schema_version(first_namespace), _build_schema_version(second_namespace)]
    consumed = 0

    def generate_namespaces() -> Iterator[MetadataNamespaceDeclaration]:
        nonlocal consumed
        for item in namespace_items:
            consumed += 1
            yield item

    registry = MetadataSchemaRegistry(
        namespace_declarations=generate_namespaces(),
        schema_version_declarations=schema_items,
    )
    namespace_items.clear()
    schema_items.clear()
    assert consumed == len(registry.namespace_declarations)
    assert registry.namespace_declarations[0].namespace == first_namespace
    assert registry.schema_version_declarations[1].namespace == second_namespace
    assert isinstance(registry.namespace_declarations, tuple)
    assert isinstance(registry.schema_version_declarations, tuple)


def test_registry_validates_elements_duplicates_and_unknown_namespaces_in_documented_order() -> (
    None
):
    namespace = _build_namespace()
    declaration = _build_namespace_declaration(namespace)
    schema_version = _build_schema_version(namespace)
    with pytest.raises(RegistryCoreValidationError, match="^invalid metadata schema registry$"):
        MetadataSchemaRegistry(
            namespace_declarations=cast(Iterable[MetadataNamespaceDeclaration], ("bad",)),
            schema_version_declarations=(schema_version,),
        )
    with pytest.raises(RegistryCoreValidationError, match="^invalid metadata schema registry$"):
        MetadataSchemaRegistry(
            namespace_declarations=(declaration,),
            schema_version_declarations=cast(Iterable[MetadataSchemaVersion], ("bad",)),
        )
    with pytest.raises(
        RegistryCoreValidationError, match="^duplicate metadata namespace declaration$"
    ):
        MetadataSchemaRegistry(
            namespace_declarations=(declaration, declaration), schema_version_declarations=()
        )
    with pytest.raises(
        RegistryCoreValidationError, match="^unknown metadata namespace declaration$"
    ):
        MetadataSchemaRegistry(
            namespace_declarations=(), schema_version_declarations=(schema_version, schema_version)
        )
    with pytest.raises(
        RegistryCoreValidationError, match="^duplicate metadata schema declaration$"
    ):
        MetadataSchemaRegistry(
            namespace_declarations=(declaration,),
            schema_version_declarations=(schema_version, schema_version),
        )


def test_registry_rejects_identity_conflicts_and_preserves_validation_precedence() -> None:
    namespace = _build_namespace()
    other_namespace = _build_namespace("atlas.metadata.other")
    declaration = _build_namespace_declaration(namespace)
    conflicting_declaration = MetadataNamespaceDeclaration(
        namespace=namespace,
        semantic_owner="different-owner",
        persistence_owner="different-store",
    )
    schema_version = _build_schema_version(namespace)
    conflicting_schema_version = MetadataSchemaVersion(
        namespace=namespace,
        schema=schema_version.schema,
        schema_version=schema_version.schema_version,
        semantic_owner="different-owner",
        persistence_owner="different-store",
        allowed_scopes=(MetadataScope.ARTIFACT,),
        classification=MetadataClassification.INTERNAL,
        bounds=MetadataPayloadBounds(
            max_payload_bytes=2048,
            max_nesting_depth=4,
            max_object_key_count=8,
            max_collection_item_count=16,
        ),
    )
    unregistered_schema_version = _build_schema_version(other_namespace)

    with pytest.raises(
        RegistryCoreValidationError, match="^duplicate metadata namespace declaration$"
    ):
        MetadataSchemaRegistry(
            namespace_declarations=(declaration, conflicting_declaration),
            schema_version_declarations=(),
        )
    with pytest.raises(
        RegistryCoreValidationError, match="^duplicate metadata namespace declaration$"
    ):
        MetadataSchemaRegistry(
            namespace_declarations=(declaration, conflicting_declaration),
            schema_version_declarations=(unregistered_schema_version,),
        )
    with pytest.raises(
        RegistryCoreValidationError, match="^duplicate metadata schema declaration$"
    ):
        MetadataSchemaRegistry(
            namespace_declarations=(declaration,),
            schema_version_declarations=(schema_version, conflicting_schema_version),
        )


def test_registry_resolves_only_exact_identities_with_stable_errors() -> None:
    registry = _build_registry()
    declaration = registry.schema_version_declarations[0]
    assert registry.resolve_namespace(declaration.namespace) is registry.namespace_declarations[0]
    assert (
        registry.resolve_schema_version(declaration.schema, declaration.schema_version)
        is declaration
    )
    with pytest.raises(
        RegistryCoreValidationError, match="^invalid metadata namespace declaration key$"
    ):
        registry.resolve_namespace("invalid")  # type: ignore[arg-type]
    with pytest.raises(
        RegistryCoreValidationError, match="^unknown metadata namespace declaration$"
    ):
        registry.resolve_namespace(_build_namespace("atlas.metadata.absent"))
    with pytest.raises(
        RegistryCoreValidationError, match="^invalid metadata schema declaration key$"
    ):
        registry.resolve_schema_version("invalid", "v1")  # type: ignore[arg-type]
    with pytest.raises(
        RegistryCoreValidationError, match="^invalid metadata schema declaration version$"
    ):
        registry.resolve_schema_version(declaration.schema, " ")
    with pytest.raises(RegistryCoreValidationError, match="^unknown metadata schema declaration$"):
        registry.resolve_schema_version(declaration.schema, "v2")
    with pytest.raises(
        RegistryCoreValidationError, match="^invalid metadata schema declaration key$"
    ):
        registry.resolve_schema_version("invalid", " ")  # type: ignore[arg-type]


def test_registry_is_structurally_equal_hashable_and_does_not_expose_indexes() -> None:
    first_registry = _build_registry()
    second_registry = _build_registry()
    assert first_registry == second_registry
    assert hash(first_registry) == hash(second_registry)
    assert isinstance(first_registry._namespaces_by_key, MappingProxyType)
    assert not hasattr(first_registry, "register")
    with pytest.raises((AttributeError, TypeError)):
        first_registry.namespace_declarations += ()  # type: ignore[misc]
