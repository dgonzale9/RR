from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError, dataclass, replace
from types import MappingProxyType
from typing import cast

import pytest

from resource_registry.tests.transformation_runtime_test_helpers import (
    freeze_test_provider_registry,
)
from resource_registry.transformations.binding.immutable_json import ImmutableJsonValueError
from resource_registry.transformations.binding.input_binder import (
    TransformationInputReferenceResolver,
)
from resource_registry.transformations.binding.results import (
    BoundDatasetArtifactReference,
    BoundDatasetVersionReference,
    BoundTransformationInput,
    BoundTransformationParameter,
    TransformationInputBindingRequest,
)
from resource_registry.transformations.binding.service import TransformationSpecificationBindingService
from resource_registry.transformations.dsl.typed_parser import TransformationTypedParser
from resource_registry.transformations.execution_plan import (
    TransformationDefinitionCompilationSnapshot,
    TransformationExecutionPlanCompilationRequest,
    TransformationExecutionPlanCompilationResult,
    TransformationExecutionPlanCompiler,
)
from resource_registry.transformations.providers import (
    ResolvedTransformationOperationIdentity,
    TransformationOperationCapabilityDescriptor,
    TransformationProviderCapabilityDescriptor,
    TransformationProviderExecutionRequest,
    TransformationProviderExecutionResult,
    TransformationProviderIdentity,
    TransformationProviderRegistry,
    TransformationProviderRuntimeContext,
    TransformationProviderValidationFinding,
    TransformationProviderValidationRequest,
    TransformationProviderValidationResult,
)
from resource_registry.transformations.validation.structural_validator import (
    TransformationStructuralValidator,
)
from resource_registry.utils.json_checksum import calculate_transformation_specification_checksum

_THRESHOLD = 0.5
_STEP_COUNT = 2


class _InputResolver:
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
                source_uri="opaque://dataset-version/1",
                content_hash="content-hash",
            ),
        )


class _ArtifactInputResolver(_InputResolver):
    def resolve_input_reference(
        self,
        *,
        request: TransformationInputBindingRequest,
    ) -> BoundTransformationInput:
        bound_input = super().resolve_input_reference(request=request)
        return replace(
            bound_input,
            artifact=BoundDatasetArtifactReference(
                artifact_id="artifact-1",
                dataset_version_id=request.dataset_version_id,
                uri="opaque://artifact/1",
                artifact_role="PRIMARY",
                artifact_format="PARQUET",
                size_bytes=128,
                checksum="artifact-checksum",
                storage_provider="test",
                lifecycle_status="ACTIVE",
                metadata={"schema_version": "1"},
            ),
        )


@dataclass
class _TestProvider:
    provider_identity: TransformationProviderIdentity
    capability_descriptor: TransformationProviderCapabilityDescriptor
    validation_result: TransformationProviderValidationResult
    validation_error: Exception | None = None
    validation_calls: int = 0
    execution_calls: int = 0

    def validate_operation(
        self,
        *,
        request: TransformationProviderValidationRequest,
    ) -> TransformationProviderValidationResult:
        self.validation_calls += 1
        if self.validation_error is not None:
            raise self.validation_error
        assert "mode" in request.configuration
        return self.validation_result

    def execute(
        self,
        *,
        request: TransformationProviderExecutionRequest,
        runtime_context: TransformationProviderRuntimeContext,
    ) -> TransformationProviderExecutionResult:
        _ = request, runtime_context
        self.execution_calls += 1
        raise AssertionError("Execution-plan compilation must never execute a provider.")


def _raw_specification(  # noqa: PLR0913
    *,
    provider_version: str | None = "v1",
    operation_version: str | None = "op1",
    input_format: str | None = "PARQUET",
    output_format: str | None = "PARQUET",
    final_format: str | None = "PARQUET",
    mode: str = "strict",
) -> dict[str, object]:
    provider: dict[str, object] = {"key": "provider"}
    operation: dict[str, object] = {"key": "transform"}
    if provider_version is not None:
        provider["version"] = provider_version
    if operation_version is not None:
        operation["version"] = operation_version
    top_input: dict[str, object] = {"name": "source"}
    first_input: dict[str, object] = {"name": "source"}
    intermediate_output: dict[str, object] = {"name": "intermediate"}
    second_input: dict[str, object] = {"name": "intermediate"}
    result_output: dict[str, object] = {"name": "result"}
    final_output: dict[str, object] = {"name": "result"}
    if input_format is not None:
        top_input["format"] = input_format
        first_input["format"] = input_format
        second_input["format"] = input_format
    if output_format is not None:
        intermediate_output["format"] = output_format
        result_output["format"] = output_format
    if final_format is not None:
        final_output["format"] = final_format
    return {
        "dsl_version": "1.0",
        "kind": "transformation",
        "parameters": {"threshold": {"type": "number"}},
        "inputs": [top_input],
        "outputs": [final_output],
        "steps": [
            {
                "step_id": "first",
                "provider": dict(provider),
                "operation": dict(operation),
                "config": {"mode": mode},
                "inputs": [first_input],
                "outputs": [intermediate_output],
            },
            {
                "step_id": "second",
                "provider": dict(provider),
                "operation": dict(operation),
                "config": {"mode": mode},
                "inputs": [second_input],
                "outputs": [result_output],
            },
        ],
    }


def _provider(  # noqa: PLR0913
    *,
    provider_key: str = "provider",
    provider_version: str = "v1",
    operation_versions: tuple[str, ...] = ("op1",),
    input_formats: tuple[str, ...] = ("PARQUET",),
    output_formats: tuple[str, ...] = ("PARQUET",),
    is_idempotent: bool = True,
    validation_result: TransformationProviderValidationResult | None = None,
    validation_error: Exception | None = None,
) -> _TestProvider:
    provider_identity = TransformationProviderIdentity(provider_key, provider_version)
    operations = tuple(
        TransformationOperationCapabilityDescriptor(
            identity=ResolvedTransformationOperationIdentity(
                provider_key=provider_key,
                provider_version=provider_version,
                operation_key="transform",
                operation_version=operation_version,
            ),
            supported_input_formats=input_formats,
            supported_output_formats=output_formats,
            is_idempotent=is_idempotent,
            is_checkpoint_reuse_safe=True,
            is_checkpoint_reconciliation_safe=False,
            supports_cooperative_cancellation=True,
            metadata={"execution": {"bounded": True}},
        )
        for operation_version in operation_versions
    )
    return _TestProvider(
        provider_identity=provider_identity,
        capability_descriptor=TransformationProviderCapabilityDescriptor(
            identity=provider_identity,
            operations=operations,
            metadata={"provider": {"trusted": True}},
        ),
        validation_result=(
            validation_result
            if validation_result is not None
            else TransformationProviderValidationResult(is_valid=True)
        ),
        validation_error=validation_error,
    )


def _compile(  # noqa: PLR0913
    raw_specification: dict[str, object] | None = None,
    *,
    providers: tuple[_TestProvider, ...] | None = None,
    archived: bool = False,
    status: str = "PUBLISHED",
    checksum: str | None = None,
    threshold: float = _THRESHOLD,
    input_resolver: TransformationInputReferenceResolver | None = None,
) -> tuple[
    TransformationExecutionPlanCompilationResult,
    tuple[_TestProvider, ...],
    TransformationExecutionPlanCompilationRequest,
]:
    raw = raw_specification or _raw_specification()
    structural = TransformationStructuralValidator().validate(raw)
    assert structural.is_valid if hasattr(structural, "is_valid") else structural.success
    assert structural.normalized_specification is not None
    specification = TransformationTypedParser().parse(structural.normalized_specification)
    binding = TransformationSpecificationBindingService().validate_and_bind(
        specification=specification,
        supplied_parameters={"threshold": threshold},
        supplied_input_bindings=(
            TransformationInputBindingRequest(
                input_name="source",
                dataset_id="dataset-1",
                dataset_version_id="version-1",
            ),
        ),
        input_resolver=input_resolver or _InputResolver(),
    )
    registry = TransformationProviderRegistry()
    active_providers = providers or (_provider(),)
    for provider in active_providers:
        registry.register_provider(provider=provider)
    freeze_test_provider_registry(registry)
    snapshot = TransformationDefinitionCompilationSnapshot(
        tenant_id="tenant-1",
        definition_id="definition-1",
        definition_version_id="definition-version-1",
        definition_is_archived=archived,
        version_status=status,
        dsl_version="1.0",
        specification_checksum=checksum or calculate_transformation_specification_checksum(raw),
        specification_json=raw,
    )
    request = TransformationExecutionPlanCompilationRequest(
        definition=snapshot,
        specification=specification,
        binding_result=binding,
    )
    result = TransformationExecutionPlanCompiler(provider_registry=registry).compile(
        request=request,
    )
    return result, active_providers, request


def _compile_request(
    *,
    request: TransformationExecutionPlanCompilationRequest,
    provider: _TestProvider,
) -> TransformationExecutionPlanCompilationResult:
    registry = TransformationProviderRegistry()
    registry.register_provider(provider=provider)
    freeze_test_provider_registry(registry)
    return TransformationExecutionPlanCompiler(provider_registry=registry).compile(
        request=request,
    )


def _compile_with_semantic_bypass(
    raw_specification: dict[str, object],
) -> TransformationExecutionPlanCompilationResult:
    _result, providers, request = _compile(raw_specification)
    semantic_result = replace(
        request.binding_result.semantic_validation,
        is_valid=True,
        findings=(),
    )
    binding_result = replace(
        request.binding_result,
        is_valid=True,
        semantic_validation=semantic_result,
    )
    return _compile_request(
        request=replace(request, binding_result=binding_result),
        provider=providers[0],
    )


def test_valid_specification_compiles_complete_immutable_plan() -> None:
    result, providers, _request = _compile()

    assert result.is_valid is True
    assert result.plan is not None
    assert [step.step_id for step in result.plan.steps] == ["first", "second"]
    assert result.plan.steps[0].dependency_step_ids == ()
    assert result.plan.steps[1].dependency_step_ids == ("first",)
    assert result.plan.steps[0].capabilities.provider_key == "provider"
    assert result.plan.steps[0].capabilities.provider_version == "v1"
    assert result.plan.steps[0].capabilities.operation_key == "transform"
    assert result.plan.steps[0].capabilities.operation_version == "op1"
    assert result.plan.steps[0].capabilities.is_idempotent is True
    assert result.plan.steps[0].capabilities.is_checkpoint_reuse_safe is True
    assert result.plan.steps[0].capabilities.is_checkpoint_reconciliation_safe is False
    assert result.plan.steps[0].capabilities.supports_cooperative_cancellation is True
    assert result.plan.outputs[0].producer_step_id == "second"
    assert result.plan.outputs[0].data_format == "PARQUET"
    assert providers[0].validation_calls == _STEP_COUNT
    assert providers[0].execution_calls == 0
    with pytest.raises(FrozenInstanceError):
        result.plan.plan_checksum = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        result.plan.steps[0].configuration["mode"] = "changed"  # type: ignore[index]
    with pytest.raises(TypeError):
        result.plan.steps[0].capabilities.operation_metadata["changed"] = True  # type: ignore[index]


@pytest.mark.parametrize(
    ("archived", "status", "expected_code"),
    [
        (True, "PUBLISHED", "ineligible_archived_definition"),
        (False, "DRAFT", "ineligible_definition_version_status"),
        (False, "DEPRECATED", "ineligible_definition_version_status"),
    ],
)
def test_ineligible_lifecycle_snapshot_is_rejected(
    archived: bool,
    status: str,
    expected_code: str,
) -> None:
    result, _providers, _request = _compile(archived=archived, status=status)

    assert result.plan is None
    assert expected_code in {finding.code for finding in result.findings}


def test_specification_checksum_drift_is_rejected() -> None:
    result, _providers, _request = _compile(checksum="0" * 64)

    assert result.plan is None
    assert result.findings[0].code == "specification_checksum_drift"


def test_snapshot_rejects_non_canonical_specification_values() -> None:
    _result, _providers, request = _compile()

    with pytest.raises(ImmutableJsonValueError):
        replace(request.definition, specification_json={"unsupported": object()})


@pytest.mark.parametrize(
    ("field_name", "expected_code"),
    [
        ("tenant_id", "missing_tenant_identity"),
        ("definition_id", "missing_definition_identity"),
        ("definition_version_id", "missing_definition_version_identity"),
        ("specification_checksum", "missing_specification_checksum"),
    ],
)
def test_missing_snapshot_identity_is_rejected(
    field_name: str,
    expected_code: str,
) -> None:
    _result, _providers, request = _compile()
    if field_name == "tenant_id":
        changed_snapshot = replace(request.definition, tenant_id="")
    elif field_name == "definition_id":
        changed_snapshot = replace(request.definition, definition_id="")
    elif field_name == "definition_version_id":
        changed_snapshot = replace(request.definition, definition_version_id="")
    else:
        changed_snapshot = replace(request.definition, specification_checksum="")

    result = _compile_request(
        request=replace(request, definition=changed_snapshot),
        provider=_provider(),
    )

    assert result.plan is None
    assert expected_code in {finding.code for finding in result.findings}


def test_story_one_binding_mismatch_is_rejected() -> None:
    _result, providers, request = _compile()
    mismatched_parameter_result = replace(
        request.binding_result.parameter_binding,
        bound_parameters=(),
    )
    mismatched_binding = replace(
        request.binding_result,
        parameter_binding=mismatched_parameter_result,
    )
    mismatched_request = replace(request, binding_result=mismatched_binding)
    registry = TransformationProviderRegistry()
    registry.register_provider(provider=providers[0])
    freeze_test_provider_registry(registry)

    result = TransformationExecutionPlanCompiler(provider_registry=registry).compile(
        request=mismatched_request,
    )

    assert result.plan is None
    assert result.findings[0].code == "parameter_binding_mismatch"


def test_invalid_story_one_result_and_unexpected_parameter_are_rejected() -> None:
    _result, _providers, request = _compile()
    invalid = _compile_request(
        request=replace(
            request,
            binding_result=replace(request.binding_result, is_valid=False),
        ),
        provider=_provider(),
    )
    existing_parameters = request.binding_result.parameter_binding.bound_parameters
    unexpected_parameter_binding = replace(
        request.binding_result.parameter_binding,
        bound_parameters=(
            *existing_parameters,
            BoundTransformationParameter(parameter_key="unexpected", value=True),
        ),
    )
    unexpected = _compile_request(
        request=replace(
            request,
            binding_result=replace(
                request.binding_result,
                parameter_binding=unexpected_parameter_binding,
            ),
        ),
        provider=_provider(),
    )

    assert invalid.plan is None
    assert invalid.findings[0].code == "invalid_story1_binding_result"
    assert unexpected.plan is None
    assert unexpected.findings[0].code == "parameter_binding_mismatch"


@pytest.mark.parametrize("bound_inputs", [(), None])
def test_missing_or_unexpected_input_binding_is_rejected(
    bound_inputs: tuple[BoundTransformationInput, ...] | None,
) -> None:
    _result, _providers, request = _compile()
    existing_input = request.binding_result.input_binding.bound_inputs[0]
    changed_inputs = (
        (replace(existing_input, input_name="unexpected"),)
        if bound_inputs is None
        else bound_inputs
    )
    changed_input_binding = replace(
        request.binding_result.input_binding,
        bound_inputs=changed_inputs,
    )

    result = _compile_request(
        request=replace(
            request,
            binding_result=replace(
                request.binding_result,
                input_binding=changed_input_binding,
            ),
        ),
        provider=_provider(),
    )

    assert result.plan is None
    assert result.findings[0].code == "input_binding_mismatch"


@pytest.mark.parametrize(
    ("tenant_id", "source_uri"),
    [
        ("other-tenant", "opaque://dataset-version/1"),
        ("tenant-1", "https://storage.example/input?sig=private"),
    ],
)
def test_inconsistent_or_signed_input_binding_is_rejected(
    tenant_id: str,
    source_uri: str,
) -> None:
    _result, _providers, request = _compile()
    bound_input = request.binding_result.input_binding.bound_inputs[0]
    changed_reference = replace(
        bound_input.dataset_version,
        tenant_id=tenant_id,
        source_uri=source_uri,
    )
    changed_input_binding = replace(
        request.binding_result.input_binding,
        bound_inputs=(replace(bound_input, dataset_version=changed_reference),),
    )
    changed_request = replace(
        request,
        binding_result=replace(
            request.binding_result,
            input_binding=changed_input_binding,
        ),
    )

    result = _compile_request(request=changed_request, provider=_provider())

    assert result.plan is None
    assert result.findings[0].code == "input_binding_mismatch"


@pytest.mark.parametrize(
    ("raw", "expected_code"),
    [
        (_raw_specification(input_format=None), "missing_input_format"),
        (_raw_specification(output_format=None), "missing_output_format"),
        (_raw_specification(final_format="JSON"), "conflicting_format"),
    ],
)
def test_missing_or_conflicting_formats_are_rejected(
    raw: dict[str, object],
    expected_code: str,
) -> None:
    result, _providers, _request = _compile(raw)

    assert result.plan is None
    assert expected_code in {finding.code for finding in result.findings}


def test_exact_artifact_format_supplies_top_level_input_format() -> None:
    raw = _raw_specification()
    top_level_inputs = cast(list[dict[str, object]], raw["inputs"])
    del top_level_inputs[0]["format"]

    result, _providers, _request = _compile(
        raw,
        input_resolver=_ArtifactInputResolver(),
    )

    assert result.plan is not None
    assert result.plan.inputs[0].artifact_id == "artifact-1"
    assert result.plan.inputs[0].data_format == "PARQUET"
    assert result.plan.inputs[0].artifact_checksum == "artifact-checksum"


def test_provider_capability_format_mismatch_is_rejected() -> None:
    unsupported_input, _providers, _request = _compile(
        providers=(_provider(input_formats=("JSON",)),),
    )
    unsupported_output, _providers, _request = _compile(
        providers=(_provider(output_formats=("JSON",)),),
    )

    assert unsupported_input.plan is None
    assert "unsupported_provider_input_format" in {
        finding.code for finding in unsupported_input.findings
    }
    assert unsupported_output.plan is None
    assert "unsupported_provider_output_format" in {
        finding.code for finding in unsupported_output.findings
    }


def test_producer_consumer_format_mismatch_is_rejected() -> None:
    raw = _raw_specification()
    steps = cast(list[dict[str, object]], raw["steps"])
    second_inputs = cast(list[dict[str, object]], steps[1]["inputs"])
    second_inputs[0]["format"] = "JSON"

    result, _providers, _request = _compile(raw)

    assert result.plan is None
    assert "conflicting_format" in {finding.code for finding in result.findings}


@pytest.mark.parametrize(
    ("source_name", "expected_code"),
    [
        ("missing", "unknown_symbol"),
        ("intermediate", "self_dependency"),
        ("result", "forward_dependency"),
    ],
)
def test_compiler_defensively_rejects_invalid_symbol_dependencies(
    source_name: str,
    expected_code: str,
) -> None:
    raw = _raw_specification()
    steps = cast(list[dict[str, object]], raw["steps"])
    first_inputs = cast(list[dict[str, object]], steps[0]["inputs"])
    first_inputs[0]["name"] = source_name

    result = _compile_with_semantic_bypass(raw)

    assert result.plan is None
    assert expected_code in {finding.code for finding in result.findings}


def test_compiler_defensively_rejects_unknown_final_output() -> None:
    raw = _raw_specification()
    outputs = cast(list[dict[str, object]], raw["outputs"])
    outputs[0]["name"] = "missing"

    result = _compile_with_semantic_bypass(raw)

    assert result.plan is None
    assert "unknown_final_output" in {finding.code for finding in result.findings}


def test_compiler_defensively_rejects_ambiguous_symbols_and_final_outputs() -> None:
    raw = _raw_specification()
    steps = cast(list[dict[str, object]], raw["steps"])
    second_outputs = cast(list[dict[str, object]], steps[1]["outputs"])
    second_outputs[0]["name"] = "intermediate"
    final_outputs = cast(list[dict[str, object]], raw["outputs"])
    final_outputs[0]["name"] = "intermediate"

    result = _compile_with_semantic_bypass(raw)
    codes = {finding.code for finding in result.findings}

    assert result.plan is None
    assert "ambiguous_symbol" in codes
    assert "ambiguous_final_output" in codes


def test_omitted_provider_versions_resolve_only_when_unambiguous() -> None:
    raw = _raw_specification(provider_version=None, operation_version=None)
    valid, _providers, _request = _compile(raw)
    ambiguous, _providers, _request = _compile(
        raw,
        providers=(
            _provider(provider_version="v1"),
            _provider(provider_version="v2"),
        ),
    )

    assert valid.is_valid is True
    assert ambiguous.plan is None
    assert ambiguous.findings[0].code == "provider_version_ambiguous"


def test_missing_provider_and_ambiguous_operation_return_stable_findings() -> None:
    missing_provider_raw = deepcopy(_raw_specification())
    missing_provider_steps = cast(list[dict[str, object]], missing_provider_raw["steps"])
    missing_provider_identity = cast(dict[str, object], missing_provider_steps[0]["provider"])
    missing_provider_identity["key"] = "missing"

    missing, _providers, _request = _compile(missing_provider_raw)
    ambiguous, _providers, _request = _compile(
        _raw_specification(operation_version=None),
        providers=(_provider(operation_versions=("op1", "op2")),),
    )

    assert missing.plan is None
    assert "provider_not_found" in {finding.code for finding in missing.findings}
    assert ambiguous.plan is None
    assert ambiguous.findings[0].code == "operation_version_ambiguous"


def test_missing_operation_returns_stable_finding() -> None:
    raw = deepcopy(_raw_specification())
    steps = cast(list[dict[str, object]], raw["steps"])
    operation_identity = cast(dict[str, object], steps[0]["operation"])
    operation_identity["key"] = "missing"

    result, _providers, _request = _compile(raw)

    assert result.plan is None
    assert "operation_not_found" in {finding.code for finding in result.findings}


def test_provider_findings_and_unexpected_errors_block_plan_without_execution() -> None:
    identity = ResolvedTransformationOperationIdentity(
        provider_key="provider",
        provider_version="v1",
        operation_key="transform",
        operation_version="op1",
    )
    provider_finding = TransformationProviderValidationFinding(
        identity=identity,
        code="invalid_mode",
        path="$.config.mode",
        message="Mode is not supported.",
    )
    invalid_provider = _provider(
        validation_result=TransformationProviderValidationResult(
            is_valid=False,
            findings=(provider_finding,),
        ),
    )
    failed_provider = _provider(validation_error=RuntimeError("private remote response"))

    invalid, _providers, _request = _compile(providers=(invalid_provider,))
    failed, _providers, _request = _compile(providers=(failed_provider,))

    assert invalid.plan is None
    assert invalid.findings[0].code == "provider_validation:invalid_mode"
    assert failed.plan is None
    assert failed.findings[0].code == "provider_validation_unavailable"
    assert "private remote response" not in failed.findings[0].message
    assert invalid_provider.execution_calls == failed_provider.execution_calls == 0


def test_repeated_compilation_is_deterministic_and_changes_with_behavior() -> None:
    first, _providers, _request = _compile()
    repeated, _providers, _request = _compile()
    changed_parameter, _providers, _request = _compile(threshold=0.75)
    changed_configuration, _providers, _request = _compile(
        _raw_specification(mode="lenient"),
    )
    changed_capability, _providers, _request = _compile(
        providers=(_provider(is_idempotent=False),),
    )

    assert first.plan is not None
    assert repeated.plan is not None
    assert changed_parameter.plan is not None
    assert changed_configuration.plan is not None
    assert changed_capability.plan is not None
    assert first.plan.plan_checksum == repeated.plan.plan_checksum
    assert first.plan.plan_checksum != changed_parameter.plan.plan_checksum
    assert first.plan.plan_checksum != changed_configuration.plan.plan_checksum
    assert first.plan.plan_checksum != changed_capability.plan.plan_checksum
    assert (
        first.plan.steps[0].capabilities.capability_checksum
        != changed_capability.plan.steps[0].capabilities.capability_checksum
    )


def test_plan_checksum_changes_with_exact_input_and_provider_identity() -> None:
    original, _providers, request = _compile()
    bound_input = request.binding_result.input_binding.bound_inputs[0]
    changed_reference = replace(bound_input.dataset_version, content_hash="different-content")
    changed_bound_input = replace(bound_input, dataset_version=changed_reference)
    changed_input_binding = replace(
        request.binding_result.input_binding,
        bound_inputs=(changed_bound_input,),
    )
    changed_binding_result = replace(
        request.binding_result,
        input_binding=changed_input_binding,
    )
    changed_input = _compile_request(
        request=replace(request, binding_result=changed_binding_result),
        provider=_provider(),
    )
    changed_provider, _providers, _request = _compile(
        _raw_specification(provider_version="v2"),
        providers=(_provider(provider_version="v2"),),
    )

    assert original.plan is not None
    assert changed_input.plan is not None
    assert changed_provider.plan is not None
    assert original.plan.plan_checksum != changed_input.plan.plan_checksum
    assert original.plan.plan_checksum != changed_provider.plan.plan_checksum


def test_plan_checksum_changes_with_dependencies_and_step_order() -> None:
    dependent_raw = _raw_specification()
    independent_raw = deepcopy(dependent_raw)
    independent_steps = cast(list[dict[str, object]], independent_raw["steps"])
    second_inputs = cast(list[dict[str, object]], independent_steps[1]["inputs"])
    second_inputs[0]["name"] = "source"
    reordered_raw = deepcopy(independent_raw)
    reordered_steps = cast(list[dict[str, object]], reordered_raw["steps"])
    reordered_raw["steps"] = list(reversed(reordered_steps))

    dependent, _providers, _request = _compile(dependent_raw)
    independent, _providers, _request = _compile(independent_raw)
    reordered, _providers, _request = _compile(reordered_raw)

    assert dependent.plan is not None
    assert independent.plan is not None
    assert reordered.plan is not None
    assert dependent.plan.plan_checksum != independent.plan.plan_checksum
    assert independent.plan.plan_checksum != reordered.plan.plan_checksum


def test_plan_checksum_changes_with_final_output_metadata() -> None:
    changed_raw = deepcopy(_raw_specification())
    final_outputs = cast(list[dict[str, object]], changed_raw["outputs"])
    final_outputs[0]["delivery"] = "standalone"

    original, _providers, _request = _compile()
    changed, _providers, _request = _compile(changed_raw)

    assert original.plan is not None
    assert changed.plan is not None
    assert original.plan.plan_checksum != changed.plan.plan_checksum


def test_mapping_insertion_order_does_not_change_plan_checksum() -> None:
    first_raw = _raw_specification()
    second_raw = dict(reversed(tuple(first_raw.items())))

    first, _providers, _request = _compile(first_raw)
    second, _providers, _request = _compile(second_raw)

    assert first.plan is not None
    assert second.plan is not None
    assert first.plan.plan_checksum == second.plan.plan_checksum


def test_unused_provider_registration_order_does_not_change_plan() -> None:
    selected_first, _providers, _request = _compile(
        providers=(_provider(), _provider(provider_key="unused")),
    )
    selected_last, _providers, _request = _compile(
        providers=(_provider(provider_key="unused"), _provider()),
    )

    assert selected_first.plan is not None
    assert selected_last.plan is not None
    assert selected_first.plan == selected_last.plan


def test_safe_summary_excludes_parameters_configuration_and_references() -> None:
    result, _providers, _request = _compile()
    assert result.plan is not None

    summary = result.plan.to_summary()
    summary_values = repr(summary)

    assert "threshold" not in summary_values
    assert "strict" not in summary_values
    assert "opaque://" not in summary_values
    assert summary.step_count == _STEP_COUNT
    assert summary.formats == ("PARQUET",)


def test_compilation_snapshot_recursively_freezes_specification_json() -> None:
    raw = _raw_specification()
    result, _providers, request = _compile(raw)

    assert result.is_valid is True
    assert isinstance(request.definition.specification_json, MappingProxyType)
    with pytest.raises(TypeError):
        request.definition.specification_json["kind"] = "changed"  # type: ignore[index]
