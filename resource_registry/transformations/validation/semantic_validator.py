from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from typing import TypeGuard

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema.exceptions import SchemaError  # type: ignore[import-untyped]

from resource_registry.transformations.dsl.model import (
    TransformationInput,
    TransformationOutput,
    TransformationSpecification,
)
from resource_registry.transformations.validation.semantic_results import (
    TransformationSemanticValidationFinding,
    TransformationSemanticValidationResult,
    order_semantic_findings,
)


class TransformationSemanticValidator:
    """Validate provider-independent semantics of a typed Transformation DSL specification."""

    def validate(
        self,
        specification: TransformationSpecification,
    ) -> TransformationSemanticValidationResult:
        """Validate symbol, ordering, output, and parameter-definition semantics.

        Args:
            specification: Structurally validated and typed transformation specification.

        Returns:
            Immutable result with deterministically ordered sanitized findings.
        """
        findings: list[TransformationSemanticValidationFinding] = []
        self._validate_step_ids(specification=specification, findings=findings)
        top_level_input_names = self._validate_declarations(
            declarations=specification.inputs,
            collection_path="$.inputs",
            invalid_code="invalid_input_name",
            duplicate_code="duplicate_top_level_input_name",
            declaration_kind="input",
            findings=findings,
        )
        self._validate_declarations(
            declarations=specification.outputs,
            collection_path="$.outputs",
            invalid_code="invalid_output_name",
            duplicate_code="duplicate_top_level_output_name",
            declaration_kind="output",
            findings=findings,
        )
        output_producers = self._validate_step_outputs(
            specification=specification,
            findings=findings,
        )
        self._validate_step_inputs(
            specification=specification,
            top_level_input_names=top_level_input_names,
            output_producers=output_producers,
            findings=findings,
        )
        self._validate_final_outputs(
            specification=specification,
            output_producers=output_producers,
            findings=findings,
        )
        self._validate_parameter_definitions(specification=specification, findings=findings)
        ordered_findings = order_semantic_findings(findings)
        return TransformationSemanticValidationResult(
            is_valid=not ordered_findings,
            findings=ordered_findings,
        )

    def _validate_step_ids(
        self,
        *,
        specification: TransformationSpecification,
        findings: list[TransformationSemanticValidationFinding],
    ) -> None:
        """Validate exact step identifiers without normalizing their persisted values."""
        step_id_counts = Counter(step.step_id for step in specification.steps)
        for step_index, step in enumerate(specification.steps):
            path = f"$.steps.{step_index}.step_id"
            if not _is_valid_symbol_name(step.step_id):
                findings.append(
                    _finding(
                        code="empty_step_id",
                        path=path,
                        message="Step ID must be a non-empty string.",
                    ),
                )
            if step_id_counts[step.step_id] > 1:
                findings.append(
                    _finding(
                        code="duplicate_step_id",
                        path=path,
                        message="Step ID must be unique within the specification.",
                    ),
                )

    def _validate_declarations(  # noqa: PLR0913
        self,
        *,
        declarations: Sequence[TransformationInput | TransformationOutput] | None,
        collection_path: str,
        invalid_code: str,
        duplicate_code: str,
        declaration_kind: str,
        findings: list[TransformationSemanticValidationFinding],
    ) -> tuple[str, ...]:
        """Validate named top-level input or output declarations."""
        if declarations is None:
            return ()
        names = tuple(_declaration_name(declaration) for declaration in declarations)
        name_counts = Counter(name for name in names if name is not None)
        for declaration_index, name in enumerate(names):
            path = f"{collection_path}.{declaration_index}.name"
            if not _is_valid_symbol_name(name):
                findings.append(
                    _finding(
                        code=invalid_code,
                        path=path,
                        message=f"Top-level {declaration_kind} name must be a non-empty string.",
                    ),
                )
                continue
            if name_counts[name] > 1:
                findings.append(
                    _finding(
                        code=duplicate_code,
                        path=path,
                        message=f"Top-level {declaration_kind} name must be unique.",
                    ),
                )
        return tuple(name for name in names if _is_valid_symbol_name(name))

    def _validate_step_outputs(
        self,
        *,
        specification: TransformationSpecification,
        findings: list[TransformationSemanticValidationFinding],
    ) -> Mapping[str, tuple[int, ...]]:
        """Validate step output symbols and return their ordered producer indexes."""
        producers: dict[str, list[int]] = defaultdict(list)
        output_locations: list[tuple[int, int, str | None]] = []
        for step_index, step in enumerate(specification.steps):
            for output_index, output in enumerate(step.outputs or ()):
                name = _declaration_name(output)
                output_locations.append((step_index, output_index, name))
                if _is_valid_symbol_name(name):
                    producers[name].append(step_index)

        for step_index, output_index, name in output_locations:
            path = f"$.steps.{step_index}.outputs.{output_index}.name"
            if not _is_valid_symbol_name(name):
                findings.append(
                    _finding(
                        code="invalid_output_name",
                        path=path,
                        message="Step output symbol must be a non-empty string.",
                    ),
                )
                continue
            if len(producers[name]) > 1:
                findings.append(
                    _finding(
                        code="duplicate_step_output_symbol",
                        path=path,
                        message="Step output symbol must have exactly one producing step.",
                    ),
                )
        return {name: tuple(indexes) for name, indexes in sorted(producers.items())}

    def _validate_step_inputs(
        self,
        *,
        specification: TransformationSpecification,
        top_level_input_names: tuple[str, ...],
        output_producers: Mapping[str, tuple[int, ...]],
        findings: list[TransformationSemanticValidationFinding],
    ) -> None:
        """Validate step input references against declaration order."""
        top_level_counts = Counter(top_level_input_names)
        for step_index, step in enumerate(specification.steps):
            for input_index, input_declaration in enumerate(step.inputs or ()):
                name = _declaration_name(input_declaration)
                path = f"$.steps.{step_index}.inputs.{input_index}.name"
                if not _is_valid_symbol_name(name):
                    findings.append(
                        _finding(
                            code="invalid_input_name",
                            path=path,
                            message="Step input reference must be a non-empty string.",
                        ),
                    )
                    continue
                producers = output_producers.get(name, ())
                source_count = top_level_counts[name] + len(producers)
                if source_count > 1:
                    findings.append(
                        _finding(
                            code="ambiguous_input_reference",
                            path=path,
                            message="Step input reference resolves to more than one symbol.",
                        ),
                    )
                    continue
                if top_level_counts[name] == 1:
                    continue
                if not producers:
                    findings.append(
                        _finding(
                            code="unresolved_input_reference",
                            path=path,
                            message="Step input reference does not resolve to a declared symbol.",
                        ),
                    )
                    continue
                producer_index = producers[0]
                if producer_index == step_index:
                    code = "self_input_reference"
                    message = "Step input cannot reference an output produced by the same step."
                elif producer_index > step_index:
                    code = "forward_input_reference"
                    message = "Step input cannot reference an output produced by a later step."
                else:
                    continue
                findings.append(_finding(code=code, path=path, message=message))

    def _validate_final_outputs(
        self,
        *,
        specification: TransformationSpecification,
        output_producers: Mapping[str, tuple[int, ...]],
        findings: list[TransformationSemanticValidationFinding],
    ) -> None:
        """Validate that every declared final output has one producing step."""
        for output_index, output in enumerate(specification.outputs or ()):
            name = _declaration_name(output)
            if not _is_valid_symbol_name(name):
                continue
            producers = output_producers.get(name, ())
            path = f"$.outputs.{output_index}.name"
            if not producers:
                findings.append(
                    _finding(
                        code="unresolved_final_output",
                        path=path,
                        message="Final output does not resolve to a produced step output.",
                    ),
                )
            elif len(producers) > 1:
                findings.append(
                    _finding(
                        code="ambiguous_final_output",
                        path=path,
                        message="Final output resolves to more than one producing step.",
                    ),
                )

    def _validate_parameter_definitions(
        self,
        *,
        specification: TransformationSpecification,
        findings: list[TransformationSemanticValidationFinding],
    ) -> None:
        """Validate parameter definitions as JSON Schema Draft 2020-12 schemas."""
        for parameter in specification.parameters or ():
            path = _mapping_path("$.parameters", parameter.parameter_key)
            if not isinstance(parameter.definition, Mapping):
                findings.append(
                    _finding(
                        code="invalid_parameter_definition",
                        path=path,
                        message="Parameter definition must be a JSON Schema object.",
                    ),
                )
                continue
            try:
                Draft202012Validator.check_schema(dict(parameter.definition))
            except SchemaError:
                findings.append(
                    _finding(
                        code="invalid_parameter_definition",
                        path=path,
                        message=(
                            "Parameter definition is not a valid JSON Schema Draft 2020-12 "
                            "schema."
                        ),
                    ),
                )


def _declaration_name(declaration: TransformationInput | TransformationOutput) -> str | None:
    """Return a declaration name when it is represented as a string."""
    name = declaration.values.get("name")
    return name if isinstance(name, str) else None


def _is_valid_symbol_name(value: object) -> TypeGuard[str]:
    """Return whether a symbol is a non-whitespace string without rewriting it."""
    return isinstance(value, str) and bool(value.strip())


def _mapping_path(base_path: str, key: str) -> str:
    """Build a deterministic JSON path for an arbitrary mapping key."""
    return f"{base_path}[{json.dumps(key, ensure_ascii=False)}]"


def _finding(
    *,
    code: str,
    path: str,
    message: str,
) -> TransformationSemanticValidationFinding:
    """Build one sanitized error finding."""
    return TransformationSemanticValidationFinding(
        code=code,
        path=path,
        severity="error",
        message=message,
    )
