from __future__ import annotations

import pytest
from jsonschema import Draft202012Validator

from resource_registry.transformations.validation.exceptions import TransformationDslSchemaVersionError
from resource_registry.transformations.validation.structural_validator import (
    TransformationStructuralValidator,
    load_transformation_schema,
)


def _valid_specification() -> dict[str, object]:
    return {
        "dsl_version": "1.0",
        "kind": "transformation",
        "metadata": {"name": "Generic transformation"},
        "parameters": {"schema": {"type": "object", "additionalProperties": False}},
        "inputs": [{"name": "source"}],
        "outputs": [{"name": "result"}],
        "steps": [
            {
                "step_id": "normalize-source",
                "provider": {"key": "any-provider", "version": "1.0"},
                "operation": {"key": "normalize", "version": "1.0"},
                "config": {"mode": "strict"},
                "inputs": [{"name": "source"}],
                "outputs": [{"name": "normalized"}],
            },
        ],
        "extensions": {"resource_registry.example": {"enabled": True}},
    }


def test_structural_validator_accepts_valid_specification() -> None:
    result = TransformationStructuralValidator().validate(_valid_specification())

    assert result.success is True
    assert result.findings == ()
    assert result.warnings == ()
    assert result.normalized_specification is not None
    assert result.normalized_specification["dsl_version"] == "1.0"


def test_structural_validator_reports_missing_required_fields() -> None:
    result = TransformationStructuralValidator().validate({"dsl_version": "1.0"})

    assert result.success is False
    assert result.normalized_specification is None
    assert any("required" in finding.message for finding in result.findings)


def test_structural_validator_reports_unsupported_dsl_version() -> None:
    specification = _valid_specification()
    specification["dsl_version"] = "2.0"

    result = TransformationStructuralValidator().validate(specification)

    assert result.success is False
    assert result.findings[0].code == "unsupported_dsl_version"
    assert result.findings[0].path == "$.dsl_version"


def test_structural_validator_rejects_unknown_top_level_core_fields() -> None:
    specification = _valid_specification()
    specification["unexpected"] = True

    result = TransformationStructuralValidator().validate(specification)

    assert result.success is False
    assert any("Additional properties" in finding.message for finding in result.findings)


def test_structural_validator_rejects_empty_steps() -> None:
    specification = _valid_specification()
    specification["steps"] = []

    result = TransformationStructuralValidator().validate(specification)

    assert result.success is False
    assert any(finding.path == "$.steps" for finding in result.findings)


def test_structural_validator_reports_malformed_documents() -> None:
    result = TransformationStructuralValidator().validate({"dsl_version": "1.0", "bad": {1, 2}})

    assert result.success is False
    assert result.findings[0].code == "parse_error"


def test_structural_validator_allows_valid_extensions() -> None:
    specification = _valid_specification()
    specification["extensions"] = {
        "resource_registry.example": {
            "notes": ["safe", "deterministic"],
            "enabled": True,
        },
    }

    result = TransformationStructuralValidator().validate(specification)

    assert result.success is True


def test_structural_validator_rejects_invalid_extensions() -> None:
    specification = _valid_specification()
    specification["extensions"] = ["not", "an", "object"]

    result = TransformationStructuralValidator().validate(specification)

    assert result.success is False
    assert any(finding.path == "$.extensions" for finding in result.findings)


def test_structural_validator_rejects_unknown_step_fields() -> None:
    specification = _valid_specification()
    steps = specification["steps"]
    assert isinstance(steps, list)
    steps[0]["future_field"] = "not-yet-supported"

    result = TransformationStructuralValidator().validate(specification)

    assert result.success is False
    assert any("Additional properties" in finding.message for finding in result.findings)


def test_structural_validator_is_deterministic_for_repeated_validation() -> None:
    validator = TransformationStructuralValidator()
    specification = _valid_specification()

    first = validator.validate(specification)
    second = validator.validate(specification)

    assert first == second


def test_schema_loader_returns_draft_2020_12_schema() -> None:
    schema = load_transformation_schema()

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["additionalProperties"] is False
    Draft202012Validator.check_schema(schema)


def test_schema_loader_rejects_unsupported_schema_version() -> None:
    with pytest.raises(TransformationDslSchemaVersionError):
        load_transformation_schema(dsl_version="2.0")


def test_schema_defines_required_and_optional_fields() -> None:
    schema = load_transformation_schema()
    properties = schema["properties"]

    assert schema["required"] == ["dsl_version", "kind", "steps"]
    for optional_field in ("metadata", "parameters", "inputs", "outputs", "extensions"):
        assert optional_field in properties
