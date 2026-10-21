from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import TypeGuard

from resource_registry.transformations.binding.immutable_json import thaw_json_value
from resource_registry.transformations.binding.results import (
    BoundTransformationInput,
)
from resource_registry.transformations.dsl.model import (
    TransformationInput,
    TransformationOutput,
    TransformationSpecification,
)
from resource_registry.transformations.execution_plan.capability_checksum import (
    calculate_registered_operation_capability_checksum,
)
from resource_registry.transformations.execution_plan.checksum import (
    ExecutionPlanChecksumError,
    calculate_execution_plan_checksum,
)
from resource_registry.transformations.execution_plan.contracts import (
    EXECUTION_PLAN_CONTRACT_VERSION,
    ExecutionPlanCapabilitySnapshot,
    ExecutionPlanInput,
    ExecutionPlanOutput,
    ExecutionPlanParameter,
    ExecutionPlanStepInputBinding,
    ExecutionPlanStepOutputBinding,
    ExecutionPlanSymbol,
    ExecutionPlanSymbolSourceKind,
    TransformationExecutionPlan,
    TransformationExecutionPlanCompilationRequest,
    TransformationExecutionPlanStep,
)
from resource_registry.transformations.execution_plan.results import (
    TransformationExecutionPlanCompilationFinding,
    TransformationExecutionPlanCompilationResult,
)
from resource_registry.transformations.providers.contracts import (
    TransformationProviderValidationRequest,
)
from resource_registry.transformations.providers.exceptions import (
    InvalidOperationKeyError,
    InvalidOperationVersionError,
    InvalidProviderKeyError,
    InvalidProviderVersionError,
    TransformationOperationNotFoundError,
    TransformationOperationVersionAmbiguousError,
    TransformationProviderNotFoundError,
    TransformationProviderVersionAmbiguousError,
)
from resource_registry.transformations.providers.registry import (
    ResolvedTransformationProviderOperation,
    TransformationProviderRegistry,
)
from resource_registry.utils.json_checksum import (
    TransformationSpecificationChecksumError,
    calculate_transformation_specification_checksum,
)

_PUBLISHED_VERSION_STATUS = "PUBLISHED"


@dataclass(frozen=True)
class _SymbolOwner:
    symbol_name: str
    source_kind: ExecutionPlanSymbolSourceKind
    data_format: str
    producer_step_id: str | None = None
    producer_step_index: int | None = None
    producer_output_index: int | None = None


class TransformationExecutionPlanCompiler:
    """Compile immutable Story 1 and Story 2 contracts into one deterministic plan."""

    def __init__(self, *, provider_registry: TransformationProviderRegistry) -> None:
        self._provider_registry = provider_registry

    def compile(
        self,
        *,
        request: TransformationExecutionPlanCompilationRequest,
    ) -> TransformationExecutionPlanCompilationResult:
        """Compile a complete plan or return deterministic sanitized findings.

        Args:
            request: Immutable definition, specification, and Story 1 binding contracts.

        Returns:
            Exactly one complete execution plan or ordered compilation findings.
        """
        findings: list[TransformationExecutionPlanCompilationFinding] = []
        self._validate_definition_snapshot(request=request, findings=findings)
        self._validate_specification_checksum(request=request, findings=findings)
        self._validate_binding_result(request=request, findings=findings)
        if findings:
            return _invalid_result(findings)

        parameters = self._compile_parameters(request=request)
        plan_inputs, input_symbols = self._compile_top_level_inputs(
            request=request,
            findings=findings,
        )
        output_owners = self._collect_step_output_owners(
            specification=request.specification,
            findings=findings,
        )
        final_output_names = tuple(
            name
            for output in request.specification.outputs or ()
            if (name := _declaration_name(output)) is not None
        )
        steps = self._compile_steps(
            specification=request.specification,
            input_symbols=input_symbols,
            output_owners=output_owners,
            final_output_names=final_output_names,
            findings=findings,
        )
        symbols = self._compile_symbols(input_symbols=input_symbols, output_owners=output_owners)
        outputs = self._compile_final_outputs(
            specification=request.specification,
            output_owners=output_owners,
            findings=findings,
        )
        if findings:
            return _invalid_result(findings)

        provisional_plan = TransformationExecutionPlan(
            contract_version=EXECUTION_PLAN_CONTRACT_VERSION,
            tenant_id=request.definition.tenant_id,
            definition_id=request.definition.definition_id,
            definition_version_id=request.definition.definition_version_id,
            dsl_version=request.definition.dsl_version,
            specification_checksum=request.definition.specification_checksum,
            parameters=parameters,
            inputs=plan_inputs,
            steps=steps,
            symbols=symbols,
            outputs=outputs,
            plan_checksum="",
        )
        try:
            plan_checksum = calculate_execution_plan_checksum(
                provisional_plan.to_checksum_dict(),
            )
        except ExecutionPlanChecksumError:
            return _invalid_result(
                [
                    _finding(
                        code="non_canonicalizable_plan_value",
                        path="$",
                        message="Execution plan contains a value outside canonical JSON.",
                    ),
                ],
            )
        plan = replace(provisional_plan, plan_checksum=plan_checksum)
        return TransformationExecutionPlanCompilationResult(is_valid=True, plan=plan)

    def _validate_definition_snapshot(
        self,
        *,
        request: TransformationExecutionPlanCompilationRequest,
        findings: list[TransformationExecutionPlanCompilationFinding],
    ) -> None:
        """Validate explicit lifecycle and identity inputs without database access."""
        snapshot = request.definition
        for field_name, value, code, path in (
            ("tenant ID", snapshot.tenant_id, "missing_tenant_identity", "$.definition.tenant_id"),
            (
                "definition ID",
                snapshot.definition_id,
                "missing_definition_identity",
                "$.definition.definition_id",
            ),
            (
                "definition version ID",
                snapshot.definition_version_id,
                "missing_definition_version_identity",
                "$.definition.definition_version_id",
            ),
        ):
            if not _is_exact_string(value):
                findings.append(
                    _finding(
                        code=code,
                        path=path,
                        message=f"Explicit {field_name} is required for plan compilation.",
                    ),
                )
        if snapshot.definition_is_archived:
            findings.append(
                _finding(
                    code="ineligible_archived_definition",
                    path="$.definition.definition_is_archived",
                    message="Archived transformation definitions cannot compile new plans.",
                ),
            )
        if snapshot.version_status != _PUBLISHED_VERSION_STATUS:
            findings.append(
                _finding(
                    code="ineligible_definition_version_status",
                    path="$.definition.version_status",
                    message="Only a PUBLISHED transformation definition version may compile.",
                ),
            )
        if not _is_exact_string(snapshot.specification_checksum):
            findings.append(
                _finding(
                    code="missing_specification_checksum",
                    path="$.definition.specification_checksum",
                    message="Persisted specification checksum is required for compilation.",
                ),
            )
        if snapshot.dsl_version != request.specification.dsl_version:
            findings.append(
                _finding(
                    code="specification_version_mismatch",
                    path="$.definition.dsl_version",
                    message="Definition snapshot and typed specification DSL versions differ.",
                ),
            )

    def _validate_specification_checksum(
        self,
        *,
        request: TransformationExecutionPlanCompilationRequest,
        findings: list[TransformationExecutionPlanCompilationFinding],
    ) -> None:
        """Recalculate and compare the canonical persisted specification checksum."""
        if not request.definition.specification_checksum:
            return
        mutable_specification = thaw_json_value(request.definition.specification_json)
        if not isinstance(mutable_specification, dict):
            findings.append(
                _finding(
                    code="specification_checksum_invalid",
                    path="$.definition.specification_json",
                    message="Specification cannot be represented by canonical JSON.",
                ),
            )
            return
        try:
            calculated = calculate_transformation_specification_checksum(mutable_specification)
        except TransformationSpecificationChecksumError:
            findings.append(
                _finding(
                    code="specification_checksum_invalid",
                    path="$.definition.specification_json",
                    message="Specification cannot be represented by canonical JSON.",
                ),
            )
            return
        if calculated != request.definition.specification_checksum:
            findings.append(
                _finding(
                    code="specification_checksum_drift",
                    path="$.definition.specification_checksum",
                    message="Persisted specification checksum does not match specification JSON.",
                ),
            )

    def _validate_binding_result(
        self,
        *,
        request: TransformationExecutionPlanCompilationRequest,
        findings: list[TransformationExecutionPlanCompilationFinding],
    ) -> None:
        """Defensively verify successful Story 1 binding coherence."""
        result = request.binding_result
        if (
            not result.is_valid
            or not result.semantic_validation.is_valid
            or not result.parameter_binding.is_valid
            or not result.input_binding.is_valid
        ):
            findings.append(
                _finding(
                    code="invalid_story1_binding_result",
                    path="$.binding_result",
                    message="Successful Story 1 semantic and binding results are required.",
                ),
            )
            return
        declared_parameters = Counter(
            parameter.parameter_key for parameter in request.specification.parameters or ()
        )
        bound_parameters = Counter(
            parameter.parameter_key for parameter in result.parameter_binding.bound_parameters
        )
        if declared_parameters != bound_parameters:
            findings.append(
                _finding(
                    code="parameter_binding_mismatch",
                    path="$.binding_result.parameter_binding",
                    message="Bound parameter names do not match specification declarations.",
                ),
            )
        declared_inputs = {
            name: declaration.values.get("required") is not False
            for declaration in request.specification.inputs or ()
            if (name := _declaration_name(declaration)) is not None
        }
        bound_input_names = Counter(
            bound_input.input_name for bound_input in result.input_binding.bound_inputs
        )
        if any(name not in declared_inputs for name in bound_input_names) or any(
            required and bound_input_names[name] != 1
            for name, required in declared_inputs.items()
        ) or any(count > 1 for count in bound_input_names.values()):
            findings.append(
                _finding(
                    code="input_binding_mismatch",
                    path="$.binding_result.input_binding",
                    message="Exact input bindings do not match specification declarations.",
                ),
            )
        for bound_input in result.input_binding.bound_inputs:
            reference = bound_input.dataset_version
            artifact = bound_input.artifact
            is_incomplete = not all(
                _is_exact_string(value)
                for value in (
                    reference.tenant_id,
                    reference.dataset_id,
                    reference.dataset_version_id,
                    reference.content_hash,
                )
            )
            is_inconsistent = (
                reference.tenant_id != request.definition.tenant_id
                or reference.status != "READY"
                or _contains_signed_query(reference.source_uri)
                or (
                    artifact is not None
                    and (
                        artifact.dataset_version_id != reference.dataset_version_id
                        or artifact.lifecycle_status != "ACTIVE"
                        or _contains_signed_query(artifact.uri)
                    )
                )
            )
            if is_incomplete or is_inconsistent:
                findings.append(
                    _finding(
                        code="input_binding_mismatch",
                        path="$.binding_result.input_binding",
                        message="Input binding contains an incomplete exact identity.",
                    ),
                )

    def _compile_parameters(
        self,
        *,
        request: TransformationExecutionPlanCompilationRequest,
    ) -> tuple[ExecutionPlanParameter, ...]:
        """Copy bound parameters in deterministic declaration-key order."""
        return tuple(
            ExecutionPlanParameter(
                parameter_key=parameter.parameter_key,
                value=parameter.value,
                classification=parameter.classification,
            )
            for parameter in sorted(
                request.binding_result.parameter_binding.bound_parameters,
                key=lambda item: item.parameter_key,
            )
        )

    def _compile_top_level_inputs(
        self,
        *,
        request: TransformationExecutionPlanCompilationRequest,
        findings: list[TransformationExecutionPlanCompilationFinding],
    ) -> tuple[tuple[ExecutionPlanInput, ...], dict[str, _SymbolOwner]]:
        """Compile exact Story 1 references and top-level symbol ownership."""
        bound_by_name = {
            bound_input.input_name: bound_input
            for bound_input in request.binding_result.input_binding.bound_inputs
        }
        compiled: list[ExecutionPlanInput] = []
        symbols: dict[str, _SymbolOwner] = {}
        for input_index, declaration in enumerate(request.specification.inputs or ()):
            name = _declaration_name(declaration)
            if name is None or name not in bound_by_name:
                continue
            bound_input = bound_by_name[name]
            data_format = self._resolve_top_level_input_format(
                declaration=declaration,
                bound_input=bound_input,
                input_index=input_index,
                findings=findings,
            )
            if data_format is None:
                continue
            artifact = bound_input.artifact
            metadata = artifact.metadata if artifact is not None else {}
            compiled_input = ExecutionPlanInput(
                logical_name=name,
                tenant_id=bound_input.dataset_version.tenant_id,
                dataset_id=bound_input.dataset_version.dataset_id,
                dataset_version_id=bound_input.dataset_version.dataset_version_id,
                version_number=bound_input.dataset_version.version_number,
                source_uri=bound_input.dataset_version.source_uri,
                content_hash=bound_input.dataset_version.content_hash,
                data_format=data_format,
                artifact_id=artifact.artifact_id if artifact else None,
                artifact_uri=artifact.uri if artifact else None,
                artifact_checksum=artifact.checksum if artifact else None,
                artifact_size_bytes=artifact.size_bytes if artifact else None,
                storage_provider=artifact.storage_provider if artifact else None,
                metadata=metadata,
            )
            compiled.append(compiled_input)
            symbols[name] = _SymbolOwner(
                symbol_name=name,
                source_kind=ExecutionPlanSymbolSourceKind.TOP_LEVEL_INPUT,
                data_format=data_format,
            )
        return tuple(compiled), symbols

    def _resolve_top_level_input_format(
        self,
        *,
        declaration: TransformationInput,
        bound_input: BoundTransformationInput,
        input_index: int,
        findings: list[TransformationExecutionPlanCompilationFinding],
    ) -> str | None:
        """Resolve an exact input format without URI or provider inference."""
        declared_format = _exact_format(declaration.values.get("format"))
        artifact_format = bound_input.artifact.artifact_format if bound_input.artifact else None
        path = f"$.inputs.{input_index}.format"
        if declared_format and artifact_format and declared_format != artifact_format:
            findings.append(
                _finding(
                    code="conflicting_format",
                    path=path,
                    message="Declared input format conflicts with the exact artifact format.",
                ),
            )
            return None
        data_format = artifact_format or declared_format
        if data_format is None:
            findings.append(
                _finding(
                    code="missing_input_format",
                    path=path,
                    message="Exact input format is required and cannot be inferred.",
                ),
            )
        return data_format

    def _collect_step_output_owners(
        self,
        *,
        specification: TransformationSpecification,
        findings: list[TransformationExecutionPlanCompilationFinding],
    ) -> dict[str, tuple[_SymbolOwner, ...]]:
        """Collect exact output ownership and format in declared step order."""
        owners: dict[str, list[_SymbolOwner]] = defaultdict(list)
        for step_index, step in enumerate(specification.steps):
            for output_index, output in enumerate(step.outputs or ()):
                name = _declaration_name(output)
                if name is None:
                    continue
                data_format = _exact_format(output.values.get("format"))
                if data_format is None:
                    findings.append(
                        _finding(
                            code="missing_output_format",
                            path=f"$.steps.{step_index}.outputs.{output_index}.format",
                            message="Every step output requires one exact declared format.",
                            step_id=step.step_id,
                        ),
                    )
                    continue
                owners[name].append(
                    _SymbolOwner(
                        symbol_name=name,
                        source_kind=ExecutionPlanSymbolSourceKind.STEP_OUTPUT,
                        data_format=data_format,
                        producer_step_id=step.step_id,
                        producer_step_index=step_index,
                        producer_output_index=output_index,
                    ),
                )
        for _name, values in sorted(owners.items()):
            if len(values) > 1:
                findings.append(
                    _finding(
                        code="ambiguous_symbol",
                        path="$.steps",
                        message="Step output symbol has more than one owner.",
                    ),
                )
        return {name: tuple(values) for name, values in sorted(owners.items())}

    def _compile_steps(
        self,
        *,
        specification: TransformationSpecification,
        input_symbols: Mapping[str, _SymbolOwner],
        output_owners: Mapping[str, tuple[_SymbolOwner, ...]],
        final_output_names: tuple[str, ...],
        findings: list[TransformationExecutionPlanCompilationFinding],
    ) -> tuple[TransformationExecutionPlanStep, ...]:
        """Compile every declared step without returning partial results on failure."""
        compiled: list[TransformationExecutionPlanStep] = []
        for step_index, step in enumerate(specification.steps):
            input_bindings, dependencies = self._compile_step_inputs(
                step_index=step_index,
                step_id=step.step_id,
                step_inputs=step.inputs or (),
                input_symbols=input_symbols,
                output_owners=output_owners,
                findings=findings,
            )
            output_bindings = tuple(
                ExecutionPlanStepOutputBinding(
                    logical_name=name,
                    output_index=output_index,
                    data_format=data_format,
                    is_final_output=name in final_output_names,
                    metadata=_declaration_metadata(output.values),
                )
                for output_index, output in enumerate(step.outputs or ())
                if (name := _declaration_name(output)) is not None
                and (data_format := _exact_format(output.values.get("format"))) is not None
            )
            resolved = self._resolve_provider(
                step_index=step_index,
                step_id=step.step_id,
                provider=step.provider,
                operation=step.operation,
                findings=findings,
            )
            if resolved is None:
                continue
            capability_snapshot = self._compile_capability_snapshot(resolved=resolved)
            self._validate_capability_formats(
                step_index=step_index,
                step_id=step.step_id,
                input_bindings=input_bindings,
                output_bindings=output_bindings,
                resolved=resolved,
                findings=findings,
            )
            self._validate_provider_configuration(
                step_index=step_index,
                step_id=step.step_id,
                configuration=step.config,
                input_bindings=input_bindings,
                output_bindings=output_bindings,
                resolved=resolved,
                findings=findings,
            )
            identity = resolved.operation.identity
            compiled.append(
                TransformationExecutionPlanStep(
                    step_index=step_index,
                    step_id=step.step_id,
                    provider_key=identity.provider_key,
                    provider_version=identity.provider_version,
                    operation_key=identity.operation_key,
                    operation_version=identity.operation_version,
                    configuration=step.config,
                    input_bindings=input_bindings,
                    output_bindings=output_bindings,
                    dependency_step_ids=dependencies,
                    capabilities=capability_snapshot,
                ),
            )
        return tuple(compiled)

    def _compile_step_inputs(  # noqa: PLR0913
        self,
        *,
        step_index: int,
        step_id: str,
        step_inputs: tuple[TransformationInput, ...],
        input_symbols: Mapping[str, _SymbolOwner],
        output_owners: Mapping[str, tuple[_SymbolOwner, ...]],
        findings: list[TransformationExecutionPlanCompilationFinding],
    ) -> tuple[tuple[ExecutionPlanStepInputBinding, ...], tuple[str, ...]]:
        """Resolve exact symbol sources and ordered dependencies for one step."""
        bindings: list[ExecutionPlanStepInputBinding] = []
        dependencies: dict[int, str] = {}
        for input_index, declaration in enumerate(step_inputs):
            name = _declaration_name(declaration)
            if name is None:
                continue
            path = f"$.steps.{step_index}.inputs.{input_index}"
            declared_format = _exact_format(declaration.values.get("format"))
            if declared_format is None:
                findings.append(
                    _finding(
                        code="missing_input_format",
                        path=f"{path}.format",
                        message="Every step input requires one exact declared format.",
                        step_id=step_id,
                    ),
                )
                continue
            candidates = list(output_owners.get(name, ()))
            if name in input_symbols:
                candidates.append(input_symbols[name])
            if not candidates:
                findings.append(
                    _finding(
                        code="unknown_symbol",
                        path=f"{path}.name",
                        message="Step input does not resolve to a known symbol.",
                        step_id=step_id,
                    ),
                )
                continue
            if len(candidates) > 1:
                findings.append(
                    _finding(
                        code="ambiguous_symbol",
                        path=f"{path}.name",
                        message="Step input resolves to more than one symbol owner.",
                        step_id=step_id,
                    ),
                )
                continue
            owner = candidates[0]
            if owner.source_kind == ExecutionPlanSymbolSourceKind.STEP_OUTPUT:
                assert owner.producer_step_index is not None
                assert owner.producer_step_id is not None
                if owner.producer_step_index == step_index:
                    findings.append(
                        _finding(
                            code="self_dependency",
                            path=f"{path}.name",
                            message="Step cannot depend on its own output.",
                            step_id=step_id,
                        ),
                    )
                    continue
                if owner.producer_step_index > step_index:
                    findings.append(
                        _finding(
                            code="forward_dependency",
                            path=f"{path}.name",
                            message="Step cannot depend on a later step output.",
                            step_id=step_id,
                        ),
                    )
                    continue
                dependencies[owner.producer_step_index] = owner.producer_step_id
            if declared_format != owner.data_format:
                findings.append(
                    _finding(
                        code="conflicting_format",
                        path=f"{path}.format",
                        message="Step input format conflicts with its source symbol format.",
                        step_id=step_id,
                    ),
                )
                continue
            bindings.append(
                ExecutionPlanStepInputBinding(
                    logical_name=name,
                    source_symbol=name,
                    source_kind=owner.source_kind,
                    data_format=declared_format,
                    producer_step_id=owner.producer_step_id,
                    producer_step_index=owner.producer_step_index,
                    producer_output_index=owner.producer_output_index,
                ),
            )
        return tuple(bindings), tuple(dependencies[index] for index in sorted(dependencies))

    def _resolve_provider(
        self,
        *,
        step_index: int,
        step_id: str,
        provider: Mapping[str, object],
        operation: Mapping[str, object],
        findings: list[TransformationExecutionPlanCompilationFinding],
    ) -> ResolvedTransformationProviderOperation | None:
        """Resolve one exact or uniquely unambiguous Story 2 provider operation."""
        provider_key = provider.get("key")
        provider_version = provider.get("version")
        operation_key = operation.get("key")
        operation_version = operation.get("version")
        try:
            return self._provider_registry.resolve_operation(
                provider_key=provider_key if isinstance(provider_key, str) else "",
                provider_version=provider_version if isinstance(provider_version, str) else None,
                operation_key=operation_key if isinstance(operation_key, str) else "",
                operation_version=(
                    operation_version if isinstance(operation_version, str) else None
                ),
            )
        except (
            InvalidProviderKeyError,
            InvalidProviderVersionError,
            TransformationProviderNotFoundError,
        ):
            code = "provider_not_found"
            message = "No trusted provider matches the requested identity."
        except TransformationProviderVersionAmbiguousError:
            code = "provider_version_ambiguous"
            message = "Provider version is ambiguous and must be explicit."
        except (
            InvalidOperationKeyError,
            InvalidOperationVersionError,
            TransformationOperationNotFoundError,
        ):
            code = "operation_not_found"
            message = "No operation matches the requested identity."
        except TransformationOperationVersionAmbiguousError:
            code = "operation_version_ambiguous"
            message = "Operation version is ambiguous and must be explicit."
        findings.append(
            _finding(
                code=code,
                path=f"$.steps.{step_index}",
                message=message,
                step_id=step_id,
                provider_key=provider_key if isinstance(provider_key, str) else None,
                provider_version=provider_version if isinstance(provider_version, str) else None,
                operation_key=operation_key if isinstance(operation_key, str) else None,
                operation_version=(
                    operation_version if isinstance(operation_version, str) else None
                ),
            ),
        )
        return None

    def _compile_capability_snapshot(
        self,
        *,
        resolved: ResolvedTransformationProviderOperation,
    ) -> ExecutionPlanCapabilitySnapshot:
        """Copy complete provider/operation capabilities and checksum them."""
        descriptor = resolved.operation
        provider_metadata = resolved.provider.capability_descriptor.metadata
        capability_checksum = calculate_registered_operation_capability_checksum(
            provider_descriptor=resolved.provider.capability_descriptor,
            operation_descriptor=descriptor,
        )
        return ExecutionPlanCapabilitySnapshot(
            provider_key=descriptor.identity.provider_key,
            provider_version=descriptor.identity.provider_version,
            operation_key=descriptor.identity.operation_key,
            operation_version=descriptor.identity.operation_version,
            supported_input_formats=descriptor.supported_input_formats,
            supported_output_formats=descriptor.supported_output_formats,
            is_idempotent=descriptor.is_idempotent,
            is_checkpoint_reuse_safe=descriptor.is_checkpoint_reuse_safe,
            is_checkpoint_reconciliation_safe=descriptor.is_checkpoint_reconciliation_safe,
            supports_cooperative_cancellation=descriptor.supports_cooperative_cancellation,
            provider_metadata=provider_metadata,
            operation_metadata=descriptor.metadata,
            capability_checksum=capability_checksum,
        )

    def _validate_capability_formats(  # noqa: PLR0913
        self,
        *,
        step_index: int,
        step_id: str,
        input_bindings: tuple[ExecutionPlanStepInputBinding, ...],
        output_bindings: tuple[ExecutionPlanStepOutputBinding, ...],
        resolved: ResolvedTransformationProviderOperation,
        findings: list[TransformationExecutionPlanCompilationFinding],
    ) -> None:
        """Require exact declared formats to be supported by provider capabilities."""
        identity = resolved.operation.identity
        for binding_index, input_binding in enumerate(input_bindings):
            if input_binding.data_format not in resolved.operation.supported_input_formats:
                findings.append(
                    _finding(
                        code="unsupported_provider_input_format",
                        path=f"$.steps.{step_index}.inputs.{binding_index}.format",
                        message="Provider operation does not support the exact input format.",
                        step_id=step_id,
                        **identity.to_dict(),
                    ),
                )
        for binding_index, output_binding in enumerate(output_bindings):
            if output_binding.data_format not in resolved.operation.supported_output_formats:
                findings.append(
                    _finding(
                        code="unsupported_provider_output_format",
                        path=f"$.steps.{step_index}.outputs.{binding_index}.format",
                        message="Provider operation does not support the exact output format.",
                        step_id=step_id,
                        **identity.to_dict(),
                    ),
                )

    def _validate_provider_configuration(  # noqa: PLR0913
        self,
        *,
        step_index: int,
        step_id: str,
        configuration: Mapping[str, object],
        input_bindings: tuple[ExecutionPlanStepInputBinding, ...],
        output_bindings: tuple[ExecutionPlanStepOutputBinding, ...],
        resolved: ResolvedTransformationProviderOperation,
        findings: list[TransformationExecutionPlanCompilationFinding],
    ) -> None:
        """Invoke provider validation only and translate sanitized findings."""
        identity = resolved.operation.identity
        validation_request = TransformationProviderValidationRequest(
            identity=identity,
            step_id=step_id,
            configuration=configuration,
            declared_input_formats=tuple(binding.data_format for binding in input_bindings),
            declared_output_formats=tuple(binding.data_format for binding in output_bindings),
        )
        try:
            result = resolved.provider.validate_operation(request=validation_request)
        except Exception:
            findings.append(
                _finding(
                    code="provider_validation_unavailable",
                    path=f"$.steps.{step_index}.config",
                    message="Provider-specific validation could not be completed safely.",
                    step_id=step_id,
                    **identity.to_dict(),
                ),
            )
            return
        for provider_finding in result.findings:
            findings.append(
                _finding(
                    code=f"provider_validation:{provider_finding.code}",
                    path=provider_finding.path,
                    message=provider_finding.message,
                    severity=provider_finding.severity,
                    step_id=step_id,
                    **identity.to_dict(),
                ),
            )

    def _compile_symbols(
        self,
        *,
        input_symbols: Mapping[str, _SymbolOwner],
        output_owners: Mapping[str, tuple[_SymbolOwner, ...]],
    ) -> tuple[ExecutionPlanSymbol, ...]:
        """Build symbols in top-level declaration then producer step order."""
        owners = list(input_symbols.values())
        owners.extend(
            sorted(
                (owner for values in output_owners.values() for owner in values),
                key=lambda owner: (
                    owner.producer_step_index if owner.producer_step_index is not None else -1,
                    owner.producer_output_index if owner.producer_output_index is not None else -1,
                ),
            ),
        )
        return tuple(
            ExecutionPlanSymbol(
                symbol_name=owner.symbol_name,
                source_kind=owner.source_kind,
                data_format=owner.data_format,
                producer_step_id=owner.producer_step_id,
                producer_step_index=owner.producer_step_index,
                producer_output_index=owner.producer_output_index,
            )
            for owner in owners
        )

    def _compile_final_outputs(
        self,
        *,
        specification: TransformationSpecification,
        output_owners: Mapping[str, tuple[_SymbolOwner, ...]],
        findings: list[TransformationExecutionPlanCompilationFinding],
    ) -> tuple[ExecutionPlanOutput, ...]:
        """Compile final outputs in top-level declaration order."""
        compiled: list[ExecutionPlanOutput] = []
        for output_index, output in enumerate(specification.outputs or ()):
            name = _declaration_name(output)
            if name is None:
                continue
            path = f"$.outputs.{output_index}"
            owners = output_owners.get(name, ())
            if not owners:
                findings.append(
                    _finding(
                        code="unknown_final_output",
                        path=f"{path}.name",
                        message="Final output has no producing step symbol.",
                    ),
                )
                continue
            if len(owners) > 1:
                findings.append(
                    _finding(
                        code="ambiguous_final_output",
                        path=f"{path}.name",
                        message="Final output has more than one producing step symbol.",
                    ),
                )
                continue
            declared_format = _exact_format(output.values.get("format"))
            if declared_format is None:
                findings.append(
                    _finding(
                        code="missing_output_format",
                        path=f"{path}.format",
                        message="Every final output requires one exact declared format.",
                    ),
                )
                continue
            owner = owners[0]
            if declared_format != owner.data_format:
                findings.append(
                    _finding(
                        code="conflicting_format",
                        path=f"{path}.format",
                        message="Final output format conflicts with its producing symbol.",
                    ),
                )
                continue
            assert owner.producer_step_id is not None
            assert owner.producer_step_index is not None
            assert owner.producer_output_index is not None
            compiled.append(
                ExecutionPlanOutput(
                    logical_name=name,
                    source_symbol=name,
                    producer_step_id=owner.producer_step_id,
                    producer_step_index=owner.producer_step_index,
                    producer_output_index=owner.producer_output_index,
                    data_format=declared_format,
                    metadata=_declaration_metadata(output.values),
                ),
            )
        return tuple(compiled)


def _declaration_name(declaration: TransformationInput | TransformationOutput) -> str | None:
    """Return an exact non-whitespace declaration name."""
    name = declaration.values.get("name")
    return name if _is_exact_string(name) else None


def _exact_format(value: object) -> str | None:
    """Return an exact non-whitespace format without normalization."""
    return value if _is_exact_string(value) else None


def _is_exact_string(value: object) -> TypeGuard[str]:
    """Return whether a value is nonempty and has no surrounding whitespace."""
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()


def _declaration_metadata(values: Mapping[str, object]) -> Mapping[str, object]:
    """Return declaration metadata excluding compiler-owned name and format fields."""
    return {key: value for key, value in values.items() if key not in {"name", "format"}}


def _contains_signed_query(value: str) -> bool:
    """Return whether a persisted reference appears to contain signed query material."""
    return "sig=" in value.lower()


def _finding(  # noqa: PLR0913
    *,
    code: str,
    path: str,
    message: str,
    severity: str = "error",
    step_id: str | None = None,
    provider_key: str | None = None,
    provider_version: str | None = None,
    operation_key: str | None = None,
    operation_version: str | None = None,
) -> TransformationExecutionPlanCompilationFinding:
    """Build one sanitized compilation finding."""
    return TransformationExecutionPlanCompilationFinding(
        code=code,
        path=path,
        severity=severity,
        message=message,
        step_id=step_id,
        provider_key=provider_key,
        provider_version=provider_version,
        operation_key=operation_key,
        operation_version=operation_version,
    )


def _invalid_result(
    findings: list[TransformationExecutionPlanCompilationFinding],
) -> TransformationExecutionPlanCompilationResult:
    """Return one invalid result without a partial plan."""
    return TransformationExecutionPlanCompilationResult(
        is_valid=False,
        findings=tuple(findings),
    )
