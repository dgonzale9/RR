"""Test ERR-1.5-S2 immutable metadata schema definition contracts."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

from resource_registry.metadata_schema import (
    MetadataClassificationProfile,
    MetadataCompatibilityAssessment,
    MetadataCompatibilityStatus,
    MetadataFieldClassification,
    MetadataFieldDefinition,
    MetadataNamespaceDeclaration,
    MetadataPayloadBounds,
    MetadataSchemaDefinition,
    MetadataSchemaDefinitionRegistry,
    MetadataSchemaRegistry,
    MetadataSchemaValidationError,
    MetadataSchemaVersion,
)
from resource_registry.registry_core import (
    MetadataClassification,
    MetadataNamespaceKey,
    MetadataSchemaKey,
    MetadataScope,
)


def _build_declaration(
    schema_name: str = "example",
    version: str = "opaque-beta",
    allowed_scopes: tuple[MetadataScope, ...] = (MetadataScope.RESOURCE,),
) -> MetadataSchemaVersion:
    namespace = MetadataNamespaceKey(value="atlas.metadata.example")
    return MetadataSchemaVersion(
        namespace=namespace,
        schema=MetadataSchemaKey(namespace=namespace, name=schema_name),
        schema_version=version,
        semantic_owner="metadata-team",
        persistence_owner="registry-store",
        allowed_scopes=allowed_scopes,
        classification=MetadataClassification.INTERNAL,
        bounds=MetadataPayloadBounds(
            max_payload_bytes=100,
            max_nesting_depth=4,
            max_object_key_count=10,
            max_collection_item_count=20,
        ),
    )


def _build_declaration_registry(*declarations: MetadataSchemaVersion) -> MetadataSchemaRegistry:
    namespace = declarations[0].namespace
    return MetadataSchemaRegistry(
        namespace_declarations=(
            MetadataNamespaceDeclaration(
                namespace=namespace,
                semantic_owner="metadata-team",
                persistence_owner="registry-store",
            ),
        ),
        schema_version_declarations=declarations,
    )


def _build_fields(
    *,
    required_title: bool = False,
    allows_null_title: bool = True,
    owner: tuple[str, str] | None = None,
) -> tuple[MetadataFieldDefinition, ...]:
    return (
        MetadataFieldDefinition(
            name="title",
            scalar_type="string",
            required=required_title,
            allows_null=allows_null_title,
            semantic_owner=None if owner is None else owner[0],
            persistence_owner=None if owner is None else owner[1],
        ),
        MetadataFieldDefinition(
            name="count", scalar_type="integer", required=False, allows_null=False
        ),
    )


def _build_definition(
    declaration: MetadataSchemaVersion,
    *,
    fields: tuple[MetadataFieldDefinition, ...] | None = None,
    bounds: MetadataPayloadBounds | None = None,
    profile_entries: tuple[MetadataFieldClassification, ...] | None = None,
) -> MetadataSchemaDefinition:
    field_values = fields or _build_fields()
    return MetadataSchemaDefinition(
        declaration=declaration,
        fields=field_values,
        bounds=bounds,
        classification_profile=MetadataClassificationProfile(
            schema_classification=MetadataClassification.INTERNAL,
            field_classifications=profile_entries
            or tuple(
                MetadataFieldClassification(
                    field_name=field_definition.name,
                    classification=MetadataClassification.INTERNAL,
                )
                for field_definition in field_values
            ),
        ),
    )


def test_definition_binds_exact_s1_declaration_and_preserves_field_profile_order() -> None:
    declaration = _build_declaration(version="not-semver/v2")
    definition = _build_definition(
        declaration,
        profile_entries=(
            MetadataFieldClassification(
                field_name="count", classification=MetadataClassification.INTERNAL
            ),
            MetadataFieldClassification(
                field_name="title", classification=MetadataClassification.INTERNAL
            ),
        ),
    )

    assert definition.schema == declaration.schema
    assert definition.schema_version == "not-semver/v2"
    assert tuple(item.name for item in definition.fields) == ("title", "count")
    assert tuple(
        item.field_name for item in definition.classification_profile.field_classifications
    ) == (
        "title",
        "count",
    )
    assert definition.effective_bounds == declaration.bounds
    assert definition.get_field_owner("title") == ("metadata-team", "registry-store")


@pytest.mark.parametrize("field_name", ("Title", "title.path", "", "1title"))
def test_field_definitions_reject_non_top_level_or_invalid_names(field_name: str) -> None:
    with pytest.raises(
        MetadataSchemaValidationError, match="^invalid metadata schema field declaration$"
    ):
        MetadataFieldDefinition(
            name=field_name, scalar_type="string", required=False, allows_null=True
        )


def test_field_definitions_reject_deferred_shapes_and_one_sided_owners() -> None:
    with pytest.raises(
        MetadataSchemaValidationError, match="^invalid metadata schema field declaration$"
    ):
        MetadataFieldDefinition(
            name="values", scalar_type="array", required=False, allows_null=False
        )
    with pytest.raises(
        MetadataSchemaValidationError, match="^invalid metadata schema field ownership$"
    ):
        MetadataFieldDefinition(
            name="title",
            scalar_type="string",
            required=False,
            allows_null=True,
            semantic_owner="semantic-only",
        )


def test_definition_rejects_duplicates_and_broader_or_field_level_bounds() -> None:
    declaration = _build_declaration()
    duplicate = MetadataFieldDefinition(
        name="title", scalar_type="string", required=False, allows_null=True
    )
    with pytest.raises(MetadataSchemaValidationError, match="^duplicate metadata schema field$"):
        _build_definition(declaration, fields=(duplicate, duplicate))
    with pytest.raises(MetadataSchemaValidationError, match="^invalid metadata schema bounds$"):
        _build_definition(
            declaration,
            bounds=MetadataPayloadBounds(
                max_payload_bytes=101,
                max_nesting_depth=4,
                max_object_key_count=10,
                max_collection_item_count=20,
            ),
        )
    assert not hasattr(MetadataFieldDefinition, "bounds")


def test_profile_requires_exact_floor_complete_unique_known_field_classifications() -> None:
    declaration = _build_declaration()
    duplicate_entries = (
        MetadataFieldClassification(
            field_name="title", classification=MetadataClassification.INTERNAL
        ),
        MetadataFieldClassification(
            field_name="title", classification=MetadataClassification.INTERNAL
        ),
    )
    with pytest.raises(
        MetadataSchemaValidationError, match="^duplicate metadata schema classification$"
    ):
        _build_definition(declaration, profile_entries=duplicate_entries)
    with pytest.raises(
        MetadataSchemaValidationError, match="^unknown metadata schema profile field$"
    ):
        _build_definition(
            declaration,
            profile_entries=(
                MetadataFieldClassification(
                    field_name="title", classification=MetadataClassification.INTERNAL
                ),
                MetadataFieldClassification(
                    field_name="unknown", classification=MetadataClassification.INTERNAL
                ),
                MetadataFieldClassification(
                    field_name="count", classification=MetadataClassification.INTERNAL
                ),
            ),
        )


def test_registration_is_atomic_exact_and_opaque() -> None:
    declaration = _build_declaration(version="first-not-a-number")
    definition = _build_definition(declaration)
    registry = MetadataSchemaDefinitionRegistry(
        declaration_registry=_build_declaration_registry(declaration)
    )
    registered = registry.register(definition)

    assert registry.definitions == ()
    assert registered.resolve(declaration.schema, "first-not-a-number") == definition
    with pytest.raises(
        MetadataSchemaValidationError, match="^duplicate metadata schema definition$"
    ):
        registered.register(definition)
    with pytest.raises(MetadataSchemaValidationError, match="^unknown metadata schema definition$"):
        registered.resolve(declaration.schema, "second-not-a-number")


def test_registration_rejects_unknown_or_mismatched_s1_declaration() -> None:
    declaration = _build_declaration()
    registry = MetadataSchemaDefinitionRegistry(
        declaration_registry=_build_declaration_registry(_build_declaration(version="other"))
    )
    with pytest.raises(
        MetadataSchemaValidationError, match="^unknown metadata schema declaration$"
    ):
        registry.register(_build_definition(declaration))


def test_compatibility_matrix_and_cross_schema_rejection() -> None:
    base_declaration = _build_declaration(version="base")
    candidate_declaration = _build_declaration(version="candidate")
    definitions = (
        _build_definition(base_declaration),
        _build_definition(
            candidate_declaration,
            fields=(
                *_build_fields(),
                MetadataFieldDefinition(
                    name="optional", scalar_type="boolean", required=False, allows_null=False
                ),
            ),
        ),
    )
    registry = MetadataSchemaDefinitionRegistry(
        declaration_registry=_build_declaration_registry(base_declaration, candidate_declaration),
        definitions=definitions,
    )
    assessment = registry.assess_writer_compatibility(
        base_schema=base_declaration.schema,
        base_schema_version="base",
        candidate_schema=candidate_declaration.schema,
        candidate_schema_version="candidate",
    )
    assert assessment.status is MetadataCompatibilityStatus.COMPATIBLE
    assert assessment.reasons == ()

    other_declaration = _build_declaration(schema_name="other", version="other")
    cross_registry = MetadataSchemaDefinitionRegistry(
        declaration_registry=_build_declaration_registry(base_declaration, other_declaration),
        definitions=(_build_definition(base_declaration), _build_definition(other_declaration)),
    )
    with pytest.raises(
        MetadataSchemaValidationError, match="^malformed metadata schema compatibility request$"
    ):
        cross_registry.assess_writer_compatibility(
            base_schema=base_declaration.schema,
            base_schema_version="base",
            candidate_schema=other_declaration.schema,
            candidate_schema_version="other",
        )


def test_compatibility_accepts_relaxed_requiredness_and_nullability() -> None:
    base_declaration = _build_declaration(version="base")
    candidate_declaration = _build_declaration(version="candidate")
    registry = MetadataSchemaDefinitionRegistry(
        declaration_registry=_build_declaration_registry(base_declaration, candidate_declaration),
        definitions=(
            _build_definition(
                base_declaration,
                fields=_build_fields(required_title=True, allows_null_title=False),
            ),
            _build_definition(candidate_declaration),
        ),
    )

    assessment = registry.assess_writer_compatibility(
        base_schema=base_declaration.schema,
        base_schema_version="base",
        candidate_schema=candidate_declaration.schema,
        candidate_schema_version="candidate",
    )

    assert assessment.status is MetadataCompatibilityStatus.COMPATIBLE
    assert assessment.reasons == ()


def test_compatibility_rejects_s1_scope_changes_as_malformed() -> None:
    base_declaration = _build_declaration(version="base")
    candidate_declaration = _build_declaration(
        version="candidate", allowed_scopes=(MetadataScope.VERSION,)
    )
    registry = MetadataSchemaDefinitionRegistry(
        declaration_registry=_build_declaration_registry(base_declaration, candidate_declaration),
        definitions=(_build_definition(base_declaration), _build_definition(candidate_declaration)),
    )

    with pytest.raises(
        MetadataSchemaValidationError, match="^malformed metadata schema compatibility request$"
    ):
        registry.assess_writer_compatibility(
            base_schema=base_declaration.schema,
            base_schema_version="base",
            candidate_schema=candidate_declaration.schema,
            candidate_schema_version="candidate",
        )


def test_compatibility_reports_field_reasons_in_base_field_order_then_bounds() -> None:
    base_declaration = _build_declaration(version="base")
    candidate_declaration = _build_declaration(version="candidate")
    candidate_fields = (
        MetadataFieldDefinition(
            name="title", scalar_type="integer", required=True, allows_null=False
        ),
        MetadataFieldDefinition(
            name="extra", scalar_type="string", required=True, allows_null=False
        ),
    )
    registry = MetadataSchemaDefinitionRegistry(
        declaration_registry=_build_declaration_registry(base_declaration, candidate_declaration),
        definitions=(
            _build_definition(base_declaration),
            _build_definition(
                candidate_declaration,
                fields=candidate_fields,
                bounds=MetadataPayloadBounds(
                    max_payload_bytes=99,
                    max_nesting_depth=4,
                    max_object_key_count=10,
                    max_collection_item_count=20,
                ),
                profile_entries=tuple(
                    MetadataFieldClassification(
                        field_name=field_definition.name,
                        classification=MetadataClassification.INTERNAL,
                    )
                    for field_definition in candidate_fields
                ),
            ),
        ),
    )
    assessment = registry.assess_writer_compatibility(
        base_schema=base_declaration.schema,
        base_schema_version="base",
        candidate_schema=candidate_declaration.schema,
        candidate_schema_version="candidate",
    )
    assert assessment.status is MetadataCompatibilityStatus.INCOMPATIBLE
    assert assessment.reasons == (
        "scalar_type_changed:title",
        "field_made_required:title",
        "null_disallowed:title",
        "field_removed:count",
        "required_field_added:extra",
        "effective_bounds_narrowed",
    )


def test_compatibility_preserves_field_order_across_incompatible_and_review_reasons() -> None:
    base_declaration = _build_declaration(version="base")
    candidate_declaration = _build_declaration(version="candidate")
    candidate_fields = (
        MetadataFieldDefinition(
            name="title",
            scalar_type="string",
            required=False,
            allows_null=True,
            semantic_owner="alternate-team",
            persistence_owner="alternate-store",
        ),
    )
    registry = MetadataSchemaDefinitionRegistry(
        declaration_registry=_build_declaration_registry(base_declaration, candidate_declaration),
        definitions=(
            _build_definition(base_declaration),
            _build_definition(
                candidate_declaration,
                fields=candidate_fields,
                profile_entries=(
                    MetadataFieldClassification(
                        field_name="title", classification=MetadataClassification.INTERNAL
                    ),
                ),
            ),
        ),
    )

    assessment = registry.assess_writer_compatibility(
        base_schema=base_declaration.schema,
        base_schema_version="base",
        candidate_schema=candidate_declaration.schema,
        candidate_schema_version="candidate",
    )

    assert assessment.status is MetadataCompatibilityStatus.INCOMPATIBLE
    assert assessment.reasons == ("owner_changed:title", "field_removed:count")


def test_compatibility_returns_review_required_for_field_owner_change() -> None:
    base_declaration = _build_declaration(version="base")
    candidate_declaration = _build_declaration(version="candidate")
    candidate_fields = _build_fields(owner=("alternate-team", "alternate-store"))
    registry = MetadataSchemaDefinitionRegistry(
        declaration_registry=_build_declaration_registry(base_declaration, candidate_declaration),
        definitions=(
            _build_definition(base_declaration),
            _build_definition(candidate_declaration, fields=candidate_fields),
        ),
    )

    assessment = registry.assess_writer_compatibility(
        base_schema=base_declaration.schema,
        base_schema_version="base",
        candidate_schema=candidate_declaration.schema,
        candidate_schema_version="candidate",
    )

    assert assessment.status is MetadataCompatibilityStatus.REVIEW_REQUIRED
    assert assessment.reasons == ("owner_changed:title",)


def test_immutable_snapshots_are_alias_safe_and_definition_module_is_pure() -> None:
    declaration = _build_declaration()
    supplied_fields = list(_build_fields())
    supplied_profile = list(
        MetadataFieldClassification(
            field_name=field.name, classification=MetadataClassification.INTERNAL
        )
        for field in supplied_fields
    )
    definition = MetadataSchemaDefinition(
        declaration=declaration,
        fields=supplied_fields,
        classification_profile=MetadataClassificationProfile(
            schema_classification=MetadataClassification.INTERNAL,
            field_classifications=supplied_profile,
        ),
    )
    supplied_fields.clear()
    supplied_profile.clear()
    assert tuple(field.name for field in definition.fields) == ("title", "count")
    with pytest.raises((AttributeError, TypeError)):
        definition.fields += ()

    supplied_reasons = ["field_removed:title"]
    assessment = MetadataCompatibilityAssessment(
        status=MetadataCompatibilityStatus.INCOMPATIBLE,
        reasons=supplied_reasons,
    )
    supplied_reasons.clear()
    assert assessment.reasons == ("field_removed:title",)
    with pytest.raises((AttributeError, TypeError)):
        assessment.reasons += ()

    source_path = Path(__file__).parents[1] / "metadata_schema" / "definitions.py"
    modules = {
        node.module
        for node in ast.walk(ast.parse(source_path.read_text(encoding="utf-8")))
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module is not None
    }
    assert modules == {
        "__future__",
        "collections.abc",
        "dataclasses",
        "enum",
        "types",
        "typing",
        "resource_registry.registry_core",
    }


def test_s2_contracts_import_without_django_settings() -> None:
    """Import the S2 public boundary in a clean standalone Python process."""
    import_environment = os.environ.copy()
    import_environment.pop("DJANGO_SETTINGS_MODULE", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from resource_registry.metadata_schema import MetadataSchemaDefinitionRegistry; "
            "assert MetadataSchemaDefinitionRegistry.__name__ == "
            "'MetadataSchemaDefinitionRegistry'",
        ],
        cwd=Path(__file__).parents[2],
        check=False,
        capture_output=True,
        env=import_environment,
        text=True,
    )

    assert result.returncode == 0, result.stderr
