from __future__ import annotations

import pytest

from resource_registry.transformations.dsl.parser import TransformationSpecificationParser
from resource_registry.transformations.validation.exceptions import (
    TransformationSpecificationParseError,
)


def _valid_specification() -> dict[str, object]:
    return {
        "dsl_version": "1.0",
        "kind": "transformation",
        "steps": [
            {
                "step_id": "inspect-source",
                "provider": {"key": "unknown-provider", "version": "1.0"},
                "operation": {"key": "inspect", "version": "1.0"},
                "config": {"strict": True},
            },
        ],
    }


def test_parser_returns_immutable_specification_for_valid_document() -> None:
    parser = TransformationSpecificationParser()

    parsed = parser.parse(_valid_specification())

    assert parsed.specification["dsl_version"] == "1.0"
    with pytest.raises(TypeError):
        parsed.specification["dsl_version"] = "2.0"  # type: ignore[index]
    assert isinstance(parsed.specification["steps"], tuple)


def test_parser_rejects_invalid_root_type() -> None:
    parser = TransformationSpecificationParser()

    with pytest.raises(TransformationSpecificationParseError):
        parser.parse([])


def test_parser_rejects_malformed_json_object() -> None:
    parser = TransformationSpecificationParser()

    with pytest.raises(TransformationSpecificationParseError):
        parser.parse({"dsl_version": "1.0", 1: "invalid-key"})  # type: ignore[dict-item]


def test_parser_rejects_unsupported_parsed_json_values() -> None:
    parser = TransformationSpecificationParser()

    with pytest.raises(TransformationSpecificationParseError):
        parser.parse({"dsl_version": "1.0", "values": {"not-json"}})


def test_parser_does_not_validate_provider_or_schema_semantics() -> None:
    parser = TransformationSpecificationParser()

    parsed = parser.parse({"hello": "world", "provider": {"key": "missing-provider"}})

    assert parsed.specification["hello"] == "world"
