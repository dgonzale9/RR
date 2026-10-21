from __future__ import annotations

from collections.abc import Mapping

from resource_registry.transformations.dsl.exceptions import TransformationTypedParserError
from resource_registry.transformations.dsl.model import (
    TransformationInput,
    TransformationMetadata,
    TransformationOutput,
    TransformationParameter,
    TransformationSpecification,
    TransformationStep,
)
from resource_registry.transformations.dsl.parser import (
    ParsedTransformationSpecification,
    TransformationSpecificationParser,
)
from resource_registry.transformations.validation.exceptions import (
    TransformationSpecificationParseError,
)


class TransformationTypedParser:
    """Map structurally validated Transformation DSL documents into typed domain objects."""

    def __init__(self, *, parser: TransformationSpecificationParser | None = None) -> None:
        self._parser = parser or TransformationSpecificationParser()

    def parse(
        self,
        specification: object | ParsedTransformationSpecification,
    ) -> TransformationSpecification:
        """Parse a structurally validated Transformation DSL document into typed objects.

        Args:
            specification: Structurally validated specification, either as parsed JSON or
                as a `ParsedTransformationSpecification`.

        Returns:
            Immutable typed Transformation DSL specification.

        Raises:
            TransformationTypedParserError: If required typed fields cannot be mapped from the
                structurally validated document.
        """
        parsed_specification = self._parse_immutable_specification(specification)
        root = parsed_specification.specification

        return TransformationSpecification(
            dsl_version=_require_string(root, "dsl_version", "$.dsl_version"),
            kind=_require_string(root, "kind", "$.kind"),
            metadata=_optional_metadata(root),
            parameters=_optional_parameters(root),
            inputs=_optional_inputs(root, "$.inputs"),
            outputs=_optional_outputs(root, "$.outputs"),
            steps=_required_steps(root),
            extensions=_optional_mapping(root, "extensions", "$.extensions"),
        )

    def _parse_immutable_specification(
        self,
        specification: object | ParsedTransformationSpecification,
    ) -> ParsedTransformationSpecification:
        """Return an immutable parsed specification without running structural validation."""
        if isinstance(specification, ParsedTransformationSpecification):
            return specification

        try:
            return self._parser.parse(specification)
        except TransformationSpecificationParseError as error:
            if _is_structural_validator_output(specification):
                return ParsedTransformationSpecification(specification=specification)
            raise TransformationTypedParserError(str(error)) from error
        except Exception as error:
            raise TransformationTypedParserError(str(error)) from error


def _optional_metadata(root: Mapping[str, object]) -> TransformationMetadata | None:
    """Map optional metadata into a typed model."""
    metadata = _optional_mapping(root, "metadata", "$.metadata")
    if metadata is None:
        return None
    return TransformationMetadata(values=metadata)


def _is_structural_validator_output(specification: object) -> bool:
    """Return whether the value looks like an immutable structural validation result."""
    return isinstance(specification, Mapping) and isinstance(specification.get("steps"), tuple)


def _optional_parameters(
    root: Mapping[str, object],
) -> tuple[TransformationParameter, ...] | None:
    """Map optional parameter definitions into ordered typed models."""
    parameters = _optional_mapping(root, "parameters", "$.parameters")
    if parameters is None:
        return None
    return tuple(
        TransformationParameter(parameter_key=parameter_key, definition=definition)
        for parameter_key, definition in parameters.items()
    )


def _required_steps(root: Mapping[str, object]) -> tuple[TransformationStep, ...]:
    """Map required ordered steps into typed models."""
    step_values = _require_tuple(root, "steps", "$.steps")
    if not step_values:
        raise TransformationTypedParserError("Expected at least one step at $.steps.")
    return tuple(
        _map_step(step_value=step_value, path=f"$.steps.{step_index}")
        for step_index, step_value in enumerate(step_values)
    )


def _map_step(*, step_value: object, path: str) -> TransformationStep:
    """Map one step object into a typed step model."""
    step = _require_mapping_value(step_value, path)
    return TransformationStep(
        step_id=_require_string(step, "step_id", f"{path}.step_id"),
        provider=_require_mapping(step, "provider", f"{path}.provider"),
        operation=_require_mapping(step, "operation", f"{path}.operation"),
        config=_require_mapping(step, "config", f"{path}.config"),
        inputs=_optional_inputs(step, f"{path}.inputs"),
        outputs=_optional_outputs(step, f"{path}.outputs"),
        extensions=_optional_mapping(step, "extensions", f"{path}.extensions"),
    )


def _optional_inputs(
    parent: Mapping[str, object],
    path: str,
) -> tuple[TransformationInput, ...] | None:
    """Map optional input declarations into typed models."""
    input_values = _optional_tuple(parent, "inputs", path)
    if input_values is None:
        return None
    return tuple(
        TransformationInput(values=_require_mapping_value(input_value, f"{path}.{input_index}"))
        for input_index, input_value in enumerate(input_values)
    )


def _optional_outputs(
    parent: Mapping[str, object],
    path: str,
) -> tuple[TransformationOutput, ...] | None:
    """Map optional output declarations into typed models."""
    output_values = _optional_tuple(parent, "outputs", path)
    if output_values is None:
        return None
    return tuple(
        TransformationOutput(
            values=_require_mapping_value(output_value, f"{path}.{output_index}"),
        )
        for output_index, output_value in enumerate(output_values)
    )


def _require_string(parent: Mapping[str, object], field_name: str, path: str) -> str:
    """Return a required string field or raise a typed parser error."""
    value = _require_field(parent, field_name, path)
    if not isinstance(value, str):
        raise TransformationTypedParserError(f"Expected string at {path}.")
    return value


def _require_mapping(
    parent: Mapping[str, object],
    field_name: str,
    path: str,
) -> Mapping[str, object]:
    """Return a required object field or raise a typed parser error."""
    return _require_mapping_value(_require_field(parent, field_name, path), path)


def _optional_mapping(
    parent: Mapping[str, object],
    field_name: str,
    path: str,
) -> Mapping[str, object] | None:
    """Return an optional object field or raise a typed parser error."""
    if field_name not in parent:
        return None
    return _require_mapping_value(parent[field_name], path)


def _require_mapping_value(value: object, path: str) -> Mapping[str, object]:
    """Return an object value or raise a typed parser error."""
    if not isinstance(value, Mapping):
        raise TransformationTypedParserError(f"Expected object at {path}.")
    return value


def _require_tuple(
    parent: Mapping[str, object],
    field_name: str,
    path: str,
) -> tuple[object, ...]:
    """Return a required array field as an immutable tuple."""
    return _require_tuple_value(_require_field(parent, field_name, path), path)


def _optional_tuple(
    parent: Mapping[str, object],
    field_name: str,
    path: str,
) -> tuple[object, ...] | None:
    """Return an optional array field as an immutable tuple."""
    if field_name not in parent:
        return None
    return _require_tuple_value(parent[field_name], path)


def _require_tuple_value(value: object, path: str) -> tuple[object, ...]:
    """Return a tuple value or raise a typed parser error."""
    if not isinstance(value, tuple):
        raise TransformationTypedParserError(f"Expected array at {path}.")
    return value


def _require_field(parent: Mapping[str, object], field_name: str, path: str) -> object:
    """Return a required field or raise a typed parser error."""
    if field_name not in parent:
        raise TransformationTypedParserError(f"Missing required field at {path}.")
    return parent[field_name]
