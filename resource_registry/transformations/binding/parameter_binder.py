from __future__ import annotations

import json
from collections.abc import Mapping

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema.exceptions import SchemaError  # type: ignore[import-untyped]

from resource_registry.transformations.binding.immutable_json import (
    ImmutableJsonValueError,
    freeze_json_value,
    thaw_json_value,
)
from resource_registry.transformations.binding.results import (
    BoundTransformationParameter,
    ParameterBindingResult,
    TransformationBindingFinding,
    order_binding_findings,
)
from resource_registry.transformations.dsl.model import TransformationSpecification


class TransformationParameterBinder:
    """Bind declared transformation parameters without coercion or secret resolution."""

    def bind(
        self,
        *,
        specification: TransformationSpecification,
        supplied_parameters: Mapping[str, object],
    ) -> ParameterBindingResult:
        """Bind supplied values and declared defaults against parameter JSON Schemas.

        Args:
            specification: Typed transformation specification containing parameter declarations.
            supplied_parameters: Caller-supplied ordinary JSON parameter values.

        Returns:
            Immutable bound parameters and deterministically ordered sanitized findings.
        """
        findings: list[TransformationBindingFinding] = []
        declarations = {
            parameter.parameter_key: parameter.definition
            for parameter in specification.parameters or ()
        }
        for undeclared_key in sorted(set(supplied_parameters) - set(declarations)):
            findings.append(
                _finding(
                    code="undeclared_parameter",
                    path=_mapping_path("$.parameter_values", undeclared_key),
                    message="Supplied parameter is not declared by the specification.",
                ),
            )

        bound_parameters: list[BoundTransformationParameter] = []
        for parameter_key in sorted(declarations):
            definition = declarations[parameter_key]
            parameter_path = _mapping_path("$.parameters", parameter_key)
            if not isinstance(definition, Mapping):
                findings.append(
                    _finding(
                        code="invalid_parameter_definition",
                        path=parameter_path,
                        message="Parameter definition must be a JSON Schema object.",
                    ),
                )
                continue
            schema = thaw_json_value(definition)
            if not isinstance(schema, dict):
                findings.append(
                    _finding(
                        code="invalid_parameter_definition",
                        path=parameter_path,
                        message="Parameter definition must be a JSON Schema object.",
                    ),
                )
                continue
            try:
                Draft202012Validator.check_schema(schema)
            except SchemaError:
                findings.append(
                    _finding(
                        code="invalid_parameter_definition",
                        path=parameter_path,
                        message=(
                            "Parameter definition is not a valid JSON Schema Draft 2020-12 "
                            "schema."
                        ),
                    ),
                )
                continue

            validator = Draft202012Validator(schema)
            has_supplied_value = parameter_key in supplied_parameters
            if has_supplied_value:
                candidate_value = supplied_parameters[parameter_key]
                invalid_code = "invalid_parameter_value"
                invalid_path = _mapping_path("$.parameter_values", parameter_key)
                invalid_message = "Supplied parameter value does not satisfy its declared schema."
            elif "default" in definition:
                candidate_value = definition["default"]
                invalid_code = "invalid_parameter_default"
                invalid_path = f"{parameter_path}.default"
                invalid_message = "Parameter default does not satisfy its declared schema."
            else:
                findings.append(
                    _finding(
                        code="missing_required_parameter",
                        path=_mapping_path("$.parameter_values", parameter_key),
                        message="Required parameter was not supplied and has no default.",
                    ),
                )
                continue

            try:
                immutable_value = freeze_json_value(candidate_value)
            except ImmutableJsonValueError:
                findings.append(
                    _finding(
                        code=invalid_code,
                        path=invalid_path,
                        message=invalid_message,
                    ),
                )
                continue
            if any(validator.iter_errors(thaw_json_value(immutable_value))):
                findings.append(
                    _finding(
                        code=invalid_code,
                        path=invalid_path,
                        message=invalid_message,
                    ),
                )
                continue
            bound_parameters.append(
                BoundTransformationParameter(
                    parameter_key=parameter_key,
                    value=immutable_value,
                ),
            )

        ordered_findings = order_binding_findings(findings)
        return ParameterBindingResult(
            is_valid=not ordered_findings,
            findings=ordered_findings,
            bound_parameters=tuple(bound_parameters) if not ordered_findings else (),
        )


def _mapping_path(base_path: str, key: str) -> str:
    """Build a deterministic JSON path for an arbitrary mapping key."""
    return f"{base_path}[{json.dumps(key, ensure_ascii=False)}]"


def _finding(*, code: str, path: str, message: str) -> TransformationBindingFinding:
    """Build one sanitized parameter binding finding."""
    return TransformationBindingFinding(
        code=code,
        path=path,
        severity="error",
        message=message,
    )
