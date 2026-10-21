"""Test ERR-1.5-S3 immutable publication and active-selection contracts."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

from resource_registry.metadata_schema import (
    MetadataClassificationProfile,
    MetadataFieldClassification,
    MetadataFieldDefinition,
    MetadataNamespaceDeclaration,
    MetadataPayloadBounds,
    MetadataSchemaActiveSelection,
    MetadataSchemaDefinition,
    MetadataSchemaDefinitionRegistry,
    MetadataSchemaPublication,
    MetadataSchemaPublicationRegistry,
    MetadataSchemaPublicationStatus,
    MetadataSchemaPublicationValidationError,
    MetadataSchemaRegistry,
    MetadataSchemaVersion,
)
from resource_registry.registry_core import (
    MetadataClassification,
    MetadataNamespaceKey,
    MetadataSchemaKey,
    MetadataScope,
)


def _build_declaration(version: str, schema_name: str = "example") -> MetadataSchemaVersion:
    namespace = MetadataNamespaceKey(value="atlas.metadata.example")
    return MetadataSchemaVersion(
        namespace=namespace,
        schema=MetadataSchemaKey(namespace=namespace, name=schema_name),
        schema_version=version,
        semantic_owner="metadata-team",
        persistence_owner="registry-store",
        allowed_scopes=(MetadataScope.RESOURCE,),
        classification=MetadataClassification.INTERNAL,
        bounds=MetadataPayloadBounds(
            max_payload_bytes=100,
            max_nesting_depth=4,
            max_object_key_count=10,
            max_collection_item_count=20,
        ),
    )


def _build_definition(
    declaration: MetadataSchemaVersion,
    *,
    scalar_type: str = "string",
    owner: tuple[str, str] | None = None,
) -> MetadataSchemaDefinition:
    field = MetadataFieldDefinition(
        name="title",
        scalar_type=scalar_type,
        required=False,
        allows_null=True,
        semantic_owner=None if owner is None else owner[0],
        persistence_owner=None if owner is None else owner[1],
    )
    return MetadataSchemaDefinition(
        declaration=declaration,
        fields=(field,),
        classification_profile=MetadataClassificationProfile(
            schema_classification=MetadataClassification.INTERNAL,
            field_classifications=(
                MetadataFieldClassification(
                    field_name="title", classification=MetadataClassification.INTERNAL
                ),
            ),
        ),
    )


def _build_definition_registry(
    *definitions: MetadataSchemaDefinition,
) -> MetadataSchemaDefinitionRegistry:
    declarations = tuple(definition.declaration for definition in definitions)
    namespace = declarations[0].namespace
    declaration_registry = MetadataSchemaRegistry(
        namespace_declarations=(
            MetadataNamespaceDeclaration(
                namespace=namespace,
                semantic_owner="metadata-team",
                persistence_owner="registry-store",
            ),
        ),
        schema_version_declarations=declarations,
    )
    return MetadataSchemaDefinitionRegistry(
        declaration_registry=declaration_registry, definitions=definitions
    )


def _build_publication_registry(
    *definitions: MetadataSchemaDefinition,
) -> MetadataSchemaPublicationRegistry:
    return MetadataSchemaPublicationRegistry(
        definition_registry=_build_definition_registry(*definitions)
    )


def test_publication_contracts_validate_and_snapshot_registry_inputs() -> None:
    definition = _build_definition(_build_declaration("opaque/v1"))
    registry = _build_publication_registry(definition)
    publication = MetadataSchemaPublication(
        definition=definition, status=MetadataSchemaPublicationStatus.PUBLISHED
    )
    supplied_publications = [publication]
    supplied_selections = [
        MetadataSchemaActiveSelection(
            schema=definition.schema, schema_version=definition.schema_version, revision=0
        )
    ]
    populated = MetadataSchemaPublicationRegistry(
        definition_registry=_build_definition_registry(definition),
        publications=supplied_publications,
        active_selections=supplied_selections,
    )
    supplied_publications.clear()
    supplied_selections.clear()

    assert populated.publications == (publication,)
    assert populated.active_selections[0].schema_version == "opaque/v1"
    assert registry.publications == ()
    with pytest.raises(MetadataSchemaPublicationValidationError):
        MetadataSchemaPublication(definition=definition, status="published")  # type: ignore[arg-type]
    with pytest.raises(MetadataSchemaPublicationValidationError):
        MetadataSchemaActiveSelection(
            schema=definition.schema, schema_version="opaque/v1", revision=True
        )
    with pytest.raises((AttributeError, TypeError)):
        publication.status = MetadataSchemaPublicationStatus.PUBLISHED  # type: ignore[misc]
    with pytest.raises((AttributeError, TypeError)):
        populated.publications += ()


def test_publication_is_exact_idempotent_and_never_activates() -> None:
    definition = _build_definition(_build_declaration("not-semver/v1"))
    registry = _build_publication_registry(definition)

    first_change = registry.publish(definition.schema, "not-semver/v1")
    repeated_change = first_change.registry.publish(definition.schema, "not-semver/v1")

    assert first_change.result.created is True
    assert registry.publications == ()
    assert first_change.registry.publications == (first_change.result.publication,)
    assert first_change.registry.active_selections == ()
    assert repeated_change.registry is first_change.registry
    assert repeated_change.result.created is False
    assert repeated_change.result.publication == first_change.result.publication
    assert first_change.registry.resolve_publication(definition.schema, "not-semver/v1") == (
        first_change.result.publication
    )
    with pytest.raises(
        MetadataSchemaPublicationValidationError,
        match="^unknown metadata schema active selection$",
    ):
        first_change.registry.resolve_active(definition.schema)


def test_publication_and_activation_use_distinct_not_found_failures() -> None:
    definition = _build_definition(_build_declaration("v1"))
    registry = _build_publication_registry(definition)

    with pytest.raises(
        MetadataSchemaPublicationValidationError, match="^unknown metadata schema publication$"
    ):
        registry.resolve_publication(definition.schema, "v1")
    with pytest.raises(
        MetadataSchemaPublicationValidationError,
        match="^unpublished metadata schema active version$",
    ):
        registry.activate(definition.schema, "v1", expected_revision=0)
    with pytest.raises(
        MetadataSchemaPublicationValidationError, match="^unknown metadata schema definition$"
    ):
        registry.publish(definition.schema, "unknown")


def test_first_activation_requires_zero_revision_and_same_version_retry_is_idempotent() -> None:
    definition = _build_definition(_build_declaration("v1"))
    published = _build_publication_registry(definition).publish(definition.schema, "v1").registry

    with pytest.raises(
        MetadataSchemaPublicationValidationError,
        match="^metadata schema publication concurrency conflict$",
    ):
        published.activate(definition.schema, "v1", expected_revision=1)
    activated = published.activate(definition.schema, "v1", expected_revision=0)
    retried = activated.registry.activate(definition.schema, "v1", expected_revision=0)

    assert activated.result.changed is True
    assert activated.result.selection.revision == 0
    assert retried.registry is activated.registry
    assert retried.result.changed is False
    with pytest.raises(
        MetadataSchemaPublicationValidationError,
        match="^metadata schema publication concurrency conflict$",
    ):
        activated.registry.activate(definition.schema, "v1", expected_revision=1)


def test_active_replacement_uses_s2_direction_and_preserves_selection_order() -> None:
    base = _build_definition(_build_declaration("base"))
    candidate = _build_definition(_build_declaration("candidate"))
    other = _build_definition(_build_declaration("other", schema_name="other"))
    registry = _build_publication_registry(base, candidate, other)
    published = registry.publish(base.schema, "base").registry
    published = published.publish(candidate.schema, "candidate").registry
    published = published.publish(other.schema, "other").registry
    active_base = published.activate(base.schema, "base", expected_revision=0).registry
    active_other = active_base.activate(other.schema, "other", expected_revision=0).registry

    replacement = active_other.activate(base.schema, "candidate", expected_revision=0)

    assert replacement.result.changed is True
    assert replacement.result.selection.revision == 1
    assert tuple(selection.schema for selection in replacement.registry.active_selections) == (
        base.schema,
        other.schema,
    )
    assert replacement.registry.resolve_active(other.schema).revision == 0
    assert tuple(
        publication.definition.schema_version for publication in replacement.registry.publications
    ) == ("base", "candidate", "other")


def test_first_activation_skips_compatibility_and_replacement_uses_exact_direction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = _build_definition(_build_declaration("base"))
    candidate = _build_definition(_build_declaration("candidate"))
    registry = _build_publication_registry(base, candidate)
    published = registry.publish(base.schema, "base").registry
    published = published.publish(candidate.schema, "candidate").registry
    calls: list[tuple[MetadataSchemaKey, str, MetadataSchemaKey, str]] = []
    original_assessment = MetadataSchemaDefinitionRegistry.assess_writer_compatibility

    def record_assessment(
        self: MetadataSchemaDefinitionRegistry,
        *,
        base_schema: MetadataSchemaKey,
        base_schema_version: str,
        candidate_schema: MetadataSchemaKey,
        candidate_schema_version: str,
    ) -> object:
        calls.append((base_schema, base_schema_version, candidate_schema, candidate_schema_version))
        return original_assessment(
            self,
            base_schema=base_schema,
            base_schema_version=base_schema_version,
            candidate_schema=candidate_schema,
            candidate_schema_version=candidate_schema_version,
        )

    monkeypatch.setattr(
        MetadataSchemaDefinitionRegistry,
        "assess_writer_compatibility",
        record_assessment,
    )
    active = published.activate(base.schema, "base", expected_revision=0).registry
    active.activate(base.schema, "candidate", expected_revision=0)

    assert calls == [(base.schema, "base", candidate.schema, "candidate")]


def test_malformed_compatibility_evaluation_fails_closed_and_preserves_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = _build_definition(_build_declaration("base"))
    candidate = _build_definition(_build_declaration("candidate"))
    registry = _build_publication_registry(base, candidate)
    published = registry.publish(base.schema, "base").registry
    published = published.publish(candidate.schema, "candidate").registry
    active = published.activate(base.schema, "base", expected_revision=0).registry

    def return_invalid_assessment(self: MetadataSchemaDefinitionRegistry, **_: object) -> object:
        return object()

    monkeypatch.setattr(
        MetadataSchemaDefinitionRegistry,
        "assess_writer_compatibility",
        return_invalid_assessment,
    )
    with pytest.raises(
        MetadataSchemaPublicationValidationError,
        match="^invalid metadata schema publication transition$",
    ):
        active.activate(base.schema, "candidate", expected_revision=0)
    assert active.resolve_active(base.schema).schema_version == "base"


def test_incompatible_and_review_required_replacements_are_atomic() -> None:
    base = _build_definition(_build_declaration("base"))
    incompatible = _build_definition(_build_declaration("incompatible"), scalar_type="integer")
    review_required = _build_definition(
        _build_declaration("review"), owner=("other-team", "other-store")
    )
    registry = _build_publication_registry(base, incompatible, review_required)
    published = registry.publish(base.schema, "base").registry
    published = published.publish(base.schema, "incompatible").registry
    published = published.publish(base.schema, "review").registry
    active = published.activate(base.schema, "base", expected_revision=0).registry

    with pytest.raises(
        MetadataSchemaPublicationValidationError,
        match="^incompatible metadata schema active version$",
    ):
        active.activate(base.schema, "incompatible", expected_revision=0)
    with pytest.raises(
        MetadataSchemaPublicationValidationError,
        match="^metadata schema active version review required$",
    ):
        active.activate(base.schema, "review", expected_revision=0)
    assert active.resolve_active(base.schema).schema_version == "base"


def test_stale_revision_precedes_same_version_idempotency_and_invalid_input_precedes_lookup() -> (
    None
):
    definition = _build_definition(_build_declaration("v1"))
    active = _build_publication_registry(definition).publish(definition.schema, "v1").registry
    active = active.activate(definition.schema, "v1", expected_revision=0).registry

    with pytest.raises(
        MetadataSchemaPublicationValidationError,
        match="^metadata schema publication concurrency conflict$",
    ):
        active.activate(definition.schema, "v1", expected_revision=1)
    with pytest.raises(
        MetadataSchemaPublicationValidationError, match="^invalid metadata schema key$"
    ):
        active.activate("invalid", "v1", expected_revision=-1)  # type: ignore[arg-type]


def test_constructor_rejects_duplicate_and_cross_schema_selection_deterministically() -> None:
    first = _build_definition(_build_declaration("same"))
    second = _build_definition(_build_declaration("same", schema_name="other"))
    definition_registry = _build_definition_registry(first, second)
    first_publication = MetadataSchemaPublication(
        definition=first, status=MetadataSchemaPublicationStatus.PUBLISHED
    )
    second_publication = MetadataSchemaPublication(
        definition=second, status=MetadataSchemaPublicationStatus.PUBLISHED
    )
    duplicate_selection = MetadataSchemaActiveSelection(
        schema=first.schema, schema_version="same", revision=0
    )

    with pytest.raises(
        MetadataSchemaPublicationValidationError, match="^duplicate metadata schema publication$"
    ):
        MetadataSchemaPublicationRegistry(
            definition_registry=definition_registry,
            publications=(first_publication, first_publication),
        )
    with pytest.raises(
        MetadataSchemaPublicationValidationError,
        match="^conflicting metadata schema active selection$",
    ):
        MetadataSchemaPublicationRegistry(
            definition_registry=definition_registry,
            publications=(first_publication,),
            active_selections=(duplicate_selection, duplicate_selection),
        )
    with pytest.raises(
        MetadataSchemaPublicationValidationError,
        match="^invalid metadata schema active selection$",
    ):
        MetadataSchemaPublicationRegistry(
            definition_registry=definition_registry,
            publications=(second_publication,),
            active_selections=(duplicate_selection,),
        )


def test_opaque_versions_preserve_exact_insertion_order_without_sorting() -> None:
    definitions = tuple(
        _build_definition(_build_declaration(version)) for version in ("v10", "v2", "01", "label")
    )
    registry = _build_publication_registry(*definitions)
    for definition in definitions:
        registry = registry.publish(definition.schema, definition.schema_version).registry

    assert tuple(
        publication.definition.schema_version for publication in registry.publications
    ) == ("v10", "v2", "01", "label")


def test_publication_module_is_pure_and_imports_without_django_settings() -> None:
    source_path = Path(__file__).parents[1] / "metadata_schema" / "publications.py"
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
    import_environment = os.environ.copy()
    import_environment.pop("DJANGO_SETTINGS_MODULE", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from resource_registry.metadata_schema import MetadataSchemaPublicationRegistry; "
            "assert MetadataSchemaPublicationRegistry.__name__ == "
            "'MetadataSchemaPublicationRegistry'",
        ],
        cwd=Path(__file__).parents[2],
        check=False,
        capture_output=True,
        env=import_environment,
        text=True,
    )

    assert result.returncode == 0, result.stderr
