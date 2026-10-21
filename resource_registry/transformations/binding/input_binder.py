from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from typing import Protocol

from resource_registry.transformations.binding.exceptions import (
    TransformationInputReferenceResolutionError,
)
from resource_registry.transformations.binding.results import (
    BoundTransformationInput,
    TransformationBindingFinding,
    TransformationInputBindingRequest,
    TransformationInputBindingResult,
    order_binding_findings,
)
from resource_registry.transformations.dsl.model import TransformationSpecification


class TransformationInputReferenceResolver(Protocol):
    """Resolve exact input requests without exposing persistence to the binding core."""

    def resolve_input_reference(
        self,
        *,
        request: TransformationInputBindingRequest,
    ) -> BoundTransformationInput:
        """Resolve one exact authorized input reference."""


class TransformationInputBinder:
    """Bind declared top-level inputs through an exact reference resolver."""

    def bind(
        self,
        *,
        specification: TransformationSpecification,
        supplied_bindings: Sequence[TransformationInputBindingRequest],
        resolver: TransformationInputReferenceResolver,
    ) -> TransformationInputBindingResult:
        """Bind explicit caller references to declared top-level inputs.

        Args:
            specification: Typed transformation specification containing input declarations.
            supplied_bindings: Explicit dataset-version and optional artifact binding requests.
            resolver: Authorized exact-reference resolver implemented outside the binding core.

        Returns:
            Immutable exact references and deterministically ordered sanitized findings.
        """
        findings: list[TransformationBindingFinding] = []
        declaration_required: dict[str, bool] = {}
        declaration_counts: Counter[str] = Counter()
        for declaration in specification.inputs or ():
            name = declaration.values.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            declaration_counts[name] += 1
            declaration_required[name] = declaration.values.get("required") is not False

        binding_counts = Counter(binding.input_name for binding in supplied_bindings)
        supplied_by_name = {
            binding.input_name: binding
            for binding in supplied_bindings
            if binding_counts[binding.input_name] == 1
        }
        for binding_index, binding in enumerate(supplied_bindings):
            path = f"$.input_bindings.{binding_index}.input_name"
            if binding.input_name not in declaration_counts:
                findings.append(
                    _finding(
                        code="undeclared_input_binding",
                        path=path,
                        message="Supplied input binding is not declared by the specification.",
                    ),
                )
            elif binding_counts[binding.input_name] > 1:
                findings.append(
                    _finding(
                        code="duplicate_input_binding",
                        path=path,
                        message="Top-level input may be bound at most once.",
                    ),
                )

        for input_name in sorted(declaration_required):
            if declaration_counts[input_name] > 1:
                continue
            if declaration_required[input_name] and input_name not in supplied_by_name:
                findings.append(
                    _finding(
                        code="missing_input_binding",
                        path=_mapping_path("$.input_bindings", input_name),
                        message="Required top-level input does not have an exact binding.",
                    ),
                )

        bound_inputs: list[BoundTransformationInput] = []
        for input_name in sorted(supplied_by_name):
            if declaration_counts[input_name] != 1:
                continue
            request = supplied_by_name[input_name]
            try:
                bound_inputs.append(resolver.resolve_input_reference(request=request))
            except TransformationInputReferenceResolutionError as error:
                findings.append(
                    _finding(
                        code=error.code,
                        path=_mapping_path("$.input_bindings", input_name),
                        message=error.message,
                    ),
                )

        ordered_findings = order_binding_findings(findings)
        return TransformationInputBindingResult(
            is_valid=not ordered_findings,
            findings=ordered_findings,
            bound_inputs=tuple(bound_inputs) if not ordered_findings else (),
        )


def _mapping_path(base_path: str, key: str) -> str:
    """Build a deterministic JSON path for an arbitrary mapping key."""
    return f"{base_path}[{json.dumps(key, ensure_ascii=False)}]"


def _finding(*, code: str, path: str, message: str) -> TransformationBindingFinding:
    """Build one sanitized input binding finding."""
    return TransformationBindingFinding(
        code=code,
        path=path,
        severity="error",
        message=message,
    )
