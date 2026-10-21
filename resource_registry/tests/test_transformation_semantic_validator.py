from __future__ import annotations

from resource_registry.transformations.dsl.model import (
    TransformationInput,
    TransformationOutput,
    TransformationParameter,
    TransformationSpecification,
    TransformationStep,
)
from resource_registry.transformations.validation.semantic_validator import (
    TransformationSemanticValidator,
)


def _input(name: object) -> TransformationInput:
    return TransformationInput(values={"name": name})


def _output(name: object) -> TransformationOutput:
    return TransformationOutput(values={"name": name})


def _step(
    step_id: str,
    *,
    inputs: tuple[TransformationInput, ...] = (),
    outputs: tuple[TransformationOutput, ...] = (),
) -> TransformationStep:
    return TransformationStep(
        step_id=step_id,
        provider={"key": "placeholder"},
        operation={"key": "placeholder"},
        config={},
        inputs=inputs or None,
        outputs=outputs or None,
    )


def _specification(
    *,
    inputs: tuple[TransformationInput, ...] = (_input("source"),),
    outputs: tuple[TransformationOutput, ...] = (_output("result"),),
    steps: tuple[TransformationStep, ...] | None = None,
    parameters: tuple[TransformationParameter, ...] | None = None,
) -> TransformationSpecification:
    return TransformationSpecification(
        dsl_version="1.0",
        kind="transformation",
        inputs=inputs,
        outputs=outputs,
        steps=steps
        or (
            _step("first", inputs=(_input("source"),), outputs=(_output("intermediate"),)),
            _step("second", inputs=(_input("intermediate"),), outputs=(_output("result"),)),
        ),
        parameters=parameters,
    )


def _codes(specification: TransformationSpecification) -> list[str]:
    result = TransformationSemanticValidator().validate(specification)
    return [finding.code for finding in result.findings]


def test_valid_ordered_specification_has_no_findings() -> None:
    result = TransformationSemanticValidator().validate(_specification())

    assert result.is_valid is True
    assert result.findings == ()
    assert result.to_dict() == {"is_valid": True, "findings": []}


def test_step_ids_and_declaration_names_are_validated_deterministically() -> None:
    specification = _specification(
        inputs=(_input("source"), _input("source")),
        outputs=(_output("result"), _output("result")),
        steps=(
            _step(" ", outputs=(_output("duplicate"),)),
            _step(" ", outputs=(_output("duplicate"),)),
        ),
    )

    result = TransformationSemanticValidator().validate(specification)

    assert "empty_step_id" in _codes(specification)
    assert "duplicate_step_id" in _codes(specification)
    assert "duplicate_top_level_input_name" in _codes(specification)
    assert "duplicate_top_level_output_name" in _codes(specification)
    assert "duplicate_step_output_symbol" in _codes(specification)
    assert result.findings == tuple(
        sorted(
            result.findings,
            key=lambda finding: (
                finding.path,
                finding.code,
                finding.severity,
                finding.message,
            ),
        ),
    )


def test_ordered_reference_failures_have_stable_codes() -> None:
    specification = _specification(
        steps=(
            _step("forward", inputs=(_input("later"),), outputs=(_output("self"),)),
            _step(
                "self",
                inputs=(_input("self-output"), _input("missing")),
                outputs=(_output("later"), _output("self-output")),
            ),
        ),
    )

    codes = _codes(specification)

    assert "forward_input_reference" in codes
    assert "self_input_reference" in codes
    assert "unresolved_input_reference" in codes
    assert "unresolved_final_output" in codes


def test_ambiguous_reference_is_rejected() -> None:
    specification = _specification(
        inputs=(_input("shared"),),
        outputs=(_output("result"),),
        steps=(
            _step("producer", outputs=(_output("shared"),)),
            _step("consumer", inputs=(_input("shared"),), outputs=(_output("result"),)),
        ),
    )

    assert "ambiguous_input_reference" in _codes(specification)


def test_invalid_parameter_definition_is_reported_without_schema_details() -> None:
    specification = _specification(
        parameters=(
            TransformationParameter(
                parameter_key="threshold",
                definition={"type": "not-a-json-schema-type"},
            ),
        ),
    )

    result = TransformationSemanticValidator().validate(specification)

    assert result.findings[0].code == "invalid_parameter_definition"
    assert "not-a-json-schema-type" not in result.findings[0].message


def test_repeated_semantic_validation_is_identical() -> None:
    validator = TransformationSemanticValidator()
    specification = _specification(
        steps=(_step("bad", inputs=(_input("missing"),), outputs=(_output("other"),)),),
    )

    assert validator.validate(specification) == validator.validate(specification)
