from __future__ import annotations

from collections.abc import Mapping, Sequence

from resource_registry.transformations.binding.input_binder import (
    TransformationInputBinder,
    TransformationInputReferenceResolver,
)
from resource_registry.transformations.binding.parameter_binder import TransformationParameterBinder
from resource_registry.transformations.binding.results import (
    TransformationInputBindingRequest,
    TransformationSpecificationBindingResult,
)
from resource_registry.transformations.dsl.model import TransformationSpecification
from resource_registry.transformations.validation.semantic_validator import (
    TransformationSemanticValidator,
)


class TransformationSpecificationBindingService:
    """Orchestrate read-only semantic, parameter, and exact input binding."""

    def __init__(
        self,
        *,
        semantic_validator: TransformationSemanticValidator | None = None,
        parameter_binder: TransformationParameterBinder | None = None,
        input_binder: TransformationInputBinder | None = None,
    ) -> None:
        self._semantic_validator = semantic_validator or TransformationSemanticValidator()
        self._parameter_binder = parameter_binder or TransformationParameterBinder()
        self._input_binder = input_binder or TransformationInputBinder()

    def validate_and_bind(
        self,
        *,
        specification: TransformationSpecification,
        supplied_parameters: Mapping[str, object],
        supplied_input_bindings: Sequence[TransformationInputBindingRequest],
        input_resolver: TransformationInputReferenceResolver,
    ) -> TransformationSpecificationBindingResult:
        """Validate and bind one typed specification without execution or persistence writes.

        Args:
            specification: Structurally validated and typed transformation specification.
            supplied_parameters: Caller-supplied ordinary JSON parameter values.
            supplied_input_bindings: Caller-supplied exact input reference requests.
            input_resolver: Authorized exact-reference resolver.

        Returns:
            Immutable aggregate result for all three Story 1 phases.
        """
        semantic_validation = self._semantic_validator.validate(specification)
        parameter_binding = self._parameter_binder.bind(
            specification=specification,
            supplied_parameters=supplied_parameters,
        )
        input_binding = self._input_binder.bind(
            specification=specification,
            supplied_bindings=supplied_input_bindings,
            resolver=input_resolver,
        )
        return TransformationSpecificationBindingResult(
            is_valid=(
                semantic_validation.is_valid
                and parameter_binding.is_valid
                and input_binding.is_valid
            ),
            semantic_validation=semantic_validation,
            parameter_binding=parameter_binding,
            input_binding=input_binding,
        )
