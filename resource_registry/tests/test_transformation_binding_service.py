from __future__ import annotations

from resource_registry.transformations.binding.results import (
    BoundDatasetVersionReference,
    BoundTransformationInput,
    TransformationInputBindingRequest,
)
from resource_registry.transformations.binding.service import TransformationSpecificationBindingService
from resource_registry.transformations.dsl.model import (
    TransformationInput,
    TransformationOutput,
    TransformationParameter,
    TransformationSpecification,
    TransformationStep,
)

_THRESHOLD = 0.5


class _Resolver:
    def resolve_input_reference(
        self,
        *,
        request: TransformationInputBindingRequest,
    ) -> BoundTransformationInput:
        return BoundTransformationInput(
            input_name=request.input_name,
            dataset_version=BoundDatasetVersionReference(
                tenant_id="tenant-1",
                dataset_id=request.dataset_id,
                dataset_version_id=request.dataset_version_id,
                version_number=1,
                status="READY",
                source_uri="local://input.parquet",
                content_hash="content-hash",
            ),
        )


def _specification() -> TransformationSpecification:
    return TransformationSpecification(
        dsl_version="1.0",
        kind="transformation",
        parameters=(
            TransformationParameter(parameter_key="threshold", definition={"type": "number"}),
        ),
        inputs=(TransformationInput(values={"name": "source"}),),
        outputs=(TransformationOutput(values={"name": "result"}),),
        steps=(
            TransformationStep(
                step_id="transform",
                provider={"key": "not-resolved"},
                operation={"key": "not-resolved"},
                config={},
                inputs=(TransformationInput(values={"name": "source"}),),
                outputs=(TransformationOutput(values={"name": "result"}),),
            ),
        ),
    )


def test_service_returns_immutable_valid_aggregate_result() -> None:
    result = TransformationSpecificationBindingService().validate_and_bind(
        specification=_specification(),
        supplied_parameters={"threshold": _THRESHOLD},
        supplied_input_bindings=(
            TransformationInputBindingRequest(
                input_name="source",
                dataset_id="dataset-1",
                dataset_version_id="version-1",
            ),
        ),
        input_resolver=_Resolver(),
    )

    assert result.is_valid is True
    assert result.semantic_validation.findings == ()
    assert result.parameter_binding.bound_parameters[0].value == _THRESHOLD
    assert result.input_binding.bound_inputs[0].dataset_version.dataset_version_id == "version-1"


def test_any_phase_finding_prevents_valid_aggregate_result() -> None:
    result = TransformationSpecificationBindingService().validate_and_bind(
        specification=_specification(),
        supplied_parameters={},
        supplied_input_bindings=(),
        input_resolver=_Resolver(),
    )

    assert result.is_valid is False
    assert result.parameter_binding.findings[0].code == "missing_required_parameter"
    assert result.input_binding.findings[0].code == "missing_input_binding"
