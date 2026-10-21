from __future__ import annotations

from collections.abc import Mapping

import pytest

from resource_registry.transformations.binding.parameter_binder import TransformationParameterBinder
from resource_registry.transformations.dsl.model import (
    TransformationParameter,
    TransformationSpecification,
    TransformationStep,
)


def _specification(
    definitions: dict[str, object],
) -> TransformationSpecification:
    return TransformationSpecification(
        dsl_version="1.0",
        kind="transformation",
        parameters=tuple(
            TransformationParameter(parameter_key=key, definition=value)
            for key, value in definitions.items()
        ),
        steps=(
            TransformationStep(
                step_id="step",
                provider={"key": "placeholder"},
                operation={"key": "placeholder"},
                config={},
            ),
        ),
    )


def test_missing_required_parameter_and_undeclared_parameter_are_rejected() -> None:
    result = TransformationParameterBinder().bind(
        specification=_specification({"threshold": {"type": "number"}}),
        supplied_parameters={"unknown": "sensitive-value"},
    )

    assert [finding.code for finding in result.findings] == [
        "missing_required_parameter",
        "undeclared_parameter",
    ]
    assert all("sensitive-value" not in finding.message for finding in result.findings)
    assert result.bound_parameters == ()


def test_default_and_supplied_values_bind_without_coercion() -> None:
    specification = _specification(
        {
            "mode": {"type": "string", "default": "strict"},
            "threshold": {"type": "number"},
        },
    )

    valid_result = TransformationParameterBinder().bind(
        specification=specification,
        supplied_parameters={"threshold": 2},
    )
    invalid_result = TransformationParameterBinder().bind(
        specification=specification,
        supplied_parameters={"threshold": "2"},
    )

    assert valid_result.is_valid is True
    assert [(item.parameter_key, item.value) for item in valid_result.bound_parameters] == [
        ("mode", "strict"),
        ("threshold", 2),
    ]
    assert invalid_result.findings[0].code == "invalid_parameter_value"


def test_invalid_default_is_rejected_without_echoing_it() -> None:
    result = TransformationParameterBinder().bind(
        specification=_specification(
            {"threshold": {"type": "integer", "default": "private-default"}},
        ),
        supplied_parameters={},
    )

    assert result.findings[0].code == "invalid_parameter_default"
    assert "private-default" not in result.findings[0].message


def test_nested_bound_json_is_recursively_immutable_and_deterministic() -> None:
    specification = _specification(
        {
            "config": {
                "type": "object",
                "properties": {"items": {"type": "array"}},
                "required": ["items"],
            },
        },
    )
    supplied = {"config": {"items": [{"second": 2, "first": 1}]}}

    first = TransformationParameterBinder().bind(
        specification=specification,
        supplied_parameters=supplied,
    )
    second = TransformationParameterBinder().bind(
        specification=specification,
        supplied_parameters=supplied,
    )

    assert first == second
    value = first.bound_parameters[0].value
    assert isinstance(value, Mapping)
    with pytest.raises(TypeError):
        value["items"] = ()  # type: ignore[index]
    items = value["items"]
    assert isinstance(items, tuple)
    assert list(items[0]) == ["first", "second"]  # type: ignore[arg-type]


def test_secret_like_json_remains_ordinary_data_without_resolution() -> None:
    marker = {"secret_ref": "ENVIRONMENT_NAME"}
    result = TransformationParameterBinder().bind(
        specification=_specification({"config": {"type": "object"}}),
        supplied_parameters={"config": marker},
    )

    assert result.is_valid is True
    assert result.bound_parameters[0].classification == "literal"
    assert result.bound_parameters[0].value["secret_ref"] == "ENVIRONMENT_NAME"  # type: ignore[index]
