from __future__ import annotations

import inspect
from collections.abc import Mapping
from dataclasses import FrozenInstanceError

import pytest

from resource_registry.transformations.dsl import (
    ParsedTransformationSpecification,
    TransformationInput,
    TransformationMetadata,
    TransformationOutput,
    TransformationParameter,
    TransformationSpecification,
    TransformationStep,
    TransformationTypedParser,
    TransformationTypedParserError,
)
from resource_registry.transformations.dsl.typed_parser import TransformationTypedParser as ParserClass
from resource_registry.transformations.validation.structural_validator import (
    TransformationStructuralValidator,
)


def _valid_specification() -> dict[str, object]:
    return {
        "dsl_version": "1.0",
        "kind": "transformation",
        "metadata": {"name": "Generic transformation", "labels": ["safe", "typed"]},
        "parameters": {
            "threshold": {"type": "number", "default": 0.75},
            "mode": {"type": "string", "enum": ["strict", "lenient"]},
        },
        "inputs": [{"name": "source", "required": True}],
        "outputs": [{"name": "result"}],
        "steps": [
            {
                "step_id": "inspect-source",
                "provider": {"key": "any-provider", "version": "1.0"},
                "operation": {"key": "inspect", "version": "1.0"},
                "config": {"mode": "strict"},
                "outputs": [{"name": "inspected"}],
            },
            {
                "step_id": "normalize-source",
                "provider": {"key": "any-provider"},
                "operation": {"key": "normalize"},
                "config": {"trim": True},
                "inputs": [{"name": "inspected"}],
                "outputs": [{"name": "normalized"}],
                "extensions": {"resource_registry.example": {"enabled": True}},
            },
        ],
        "extensions": {"resource_registry.example": {"notes": ["preserve", "me"]}},
    }


def _structurally_validated_specification() -> Mapping[str, object]:
    result = TransformationStructuralValidator().validate(_valid_specification())

    assert result.success is True
    assert result.normalized_specification is not None
    return result.normalized_specification


def test_typed_parser_maps_structurally_valid_specification() -> None:
    typed_specification = TransformationTypedParser().parse(_structurally_validated_specification())

    assert isinstance(typed_specification, TransformationSpecification)
    assert typed_specification.dsl_version == "1.0"
    assert typed_specification.kind == "transformation"
    assert typed_specification.metadata == TransformationMetadata(
        values={"name": "Generic transformation", "labels": ("safe", "typed")},
    )
    assert typed_specification.parameters == (
        TransformationParameter(
            parameter_key="threshold",
            definition={"type": "number", "default": 0.75},
        ),
        TransformationParameter(
            parameter_key="mode",
            definition={"type": "string", "enum": ("strict", "lenient")},
        ),
    )
    assert typed_specification.inputs == (
        TransformationInput(values={"name": "source", "required": True}),
    )
    assert typed_specification.outputs == (TransformationOutput(values={"name": "result"}),)
    assert typed_specification.steps[0] == TransformationStep(
        step_id="inspect-source",
        provider={"key": "any-provider", "version": "1.0"},
        operation={"key": "inspect", "version": "1.0"},
        config={"mode": "strict"},
        outputs=(TransformationOutput(values={"name": "inspected"}),),
    )
    assert typed_specification.steps[1].extensions == {
        "resource_registry.example": {"enabled": True},
    }
    assert typed_specification.extensions == {
        "resource_registry.example": {"notes": ("preserve", "me")},
    }


def test_typed_parser_accepts_story_one_parsed_specification() -> None:
    parsed_specification = ParsedTransformationSpecification(
        specification=_structurally_validated_specification(),
    )

    typed_specification = TransformationTypedParser().parse(parsed_specification)

    assert typed_specification.steps[0].step_id == "inspect-source"


def test_typed_parser_preserves_step_order() -> None:
    typed_specification = TransformationTypedParser().parse(_structurally_validated_specification())

    assert [step.step_id for step in typed_specification.steps] == [
        "inspect-source",
        "normalize-source",
    ]


def test_typed_parser_preserves_optional_field_absence_without_inventing_defaults() -> None:
    specification = {
        "dsl_version": "1.0",
        "kind": "transformation",
        "steps": [
            {
                "step_id": "single-step",
                "provider": {"key": "any-provider"},
                "operation": {"key": "any-operation"},
                "config": {},
            },
        ],
    }
    result = TransformationStructuralValidator().validate(specification)
    assert result.success is True

    typed_specification = TransformationTypedParser().parse(result.normalized_specification)

    assert typed_specification.metadata is None
    assert typed_specification.parameters is None
    assert typed_specification.inputs is None
    assert typed_specification.outputs is None
    assert typed_specification.extensions is None
    assert typed_specification.steps[0].inputs is None
    assert typed_specification.steps[0].outputs is None
    assert typed_specification.steps[0].extensions is None


def test_typed_parser_outputs_are_immutable() -> None:
    typed_specification = TransformationTypedParser().parse(_structurally_validated_specification())

    with pytest.raises(FrozenInstanceError):
        typed_specification.dsl_version = "2.0"  # type: ignore[misc]
    with pytest.raises(TypeError):
        typed_specification.steps[0].config["mode"] = "lenient"  # type: ignore[index]
    assert isinstance(typed_specification.steps, tuple)


def test_typed_parser_is_deterministic_for_repeated_parsing() -> None:
    specification = _structurally_validated_specification()

    first = TransformationTypedParser().parse(specification)
    second = TransformationTypedParser().parse(specification)

    assert first == second


def test_typed_parser_does_not_validate_provider_existence() -> None:
    typed_specification = TransformationTypedParser().parse(_structurally_validated_specification())

    assert typed_specification.steps[0].provider["key"] == "any-provider"


def test_typed_parser_rejects_malformed_structures_that_escape_validation() -> None:
    specification = {
        "dsl_version": "1.0",
        "kind": "transformation",
        "steps": [
            {
                "step_id": "bad-step",
                "provider": "not-an-object",
                "operation": {"key": "inspect"},
                "config": {},
            },
        ],
    }

    with pytest.raises(TransformationTypedParserError, match="provider"):
        TransformationTypedParser().parse(specification)


def test_typed_parser_rejects_empty_steps_if_validation_was_bypassed() -> None:
    specification = {"dsl_version": "1.0", "kind": "transformation", "steps": []}

    with pytest.raises(TransformationTypedParserError, match="at least one step"):
        TransformationTypedParser().parse(specification)


def test_typed_parser_has_no_database_or_runtime_dependencies() -> None:
    source = inspect.getsource(ParserClass)

    assert "TransformationDefinition" not in source
    assert "ReadService" not in source
    assert "ProviderResolver" not in source
    assert "provider_resolution" not in source
    assert "execute" not in source.lower()
