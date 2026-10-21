from __future__ import annotations

import time
from collections.abc import Mapping
from typing import cast

from resource_registry.transformations.binding.results import ParameterValueClassification
from resource_registry.transformations.execution.checksum import (
    calculate_reference_snapshot_checksum,
)
from resource_registry.transformations.execution.contracts import (
    CheckpointReuseDecisionKind,
    TransformationExecutionFailure,
    TransformationRunAttemptExecutionOutcome,
    TransformationRunAttemptExecutionRequest,
    TransformationRunAttemptExecutionResult,
    TransformationStepAttemptDescriptor,
)
from resource_registry.transformations.execution.exceptions import (
    StaleTransformationAttemptError,
    TransformationCancellationPrecedenceError,
)
from resource_registry.transformations.execution.ports import (
    TransformationCheckpointReferenceVerifier,
    TransformationExecutionPersistencePort,
)
from resource_registry.transformations.execution.references import (
    InvalidTransformationReferenceError,
    serialize_input_reference,
    serialize_output_reference,
    validate_canonical_reference,
)
from resource_registry.transformations.execution_plan import (
    ExecutionPlanSymbolSourceKind,
    TransformationExecutionPlanStep,
)
from resource_registry.transformations.execution_plan.capability_checksum import (
    calculate_registered_operation_capability_checksum,
)
from resource_registry.transformations.providers import (
    ProviderSideEffectDisposition,
    ResolvedTransformationOperationIdentity,
    TransformationIntegrityMetadata,
    TransformationProviderExecutionError,
    TransformationProviderExecutionRequest,
    TransformationProviderExpectedOutput,
    TransformationProviderInputReference,
    TransformationProviderOutputReference,
    TransformationProviderRegistry,
    TransformationProviderValidationFinding,
    TransformationProviderValidationRequest,
    TransformationProviderValidationResult,
)
from resource_registry.transformations.providers.exceptions import (
    TransformationOperationNotFoundError,
    TransformationProviderNotFoundError,
)
from resource_registry.transformations.providers.registry import (
    ResolvedTransformationProviderOperation,
)


class TransformationRunExecutionService:
    """Execute one initialized transformation run attempt without ORM dependencies."""

    def __init__(
        self,
        *,
        provider_registry: TransformationProviderRegistry,
        persistence: TransformationExecutionPersistencePort,
    ) -> None:
        self._provider_registry = provider_registry
        self._persistence = persistence

    def execute_attempt(  # noqa: PLR0911, PLR0912, PLR0915
        self,
        *,
        request: TransformationRunAttemptExecutionRequest,
    ) -> TransformationRunAttemptExecutionResult:
        """Execute plan steps sequentially and return the immutable attempt outcome.

        Args:
            request: Fully initialized, immutable run-attempt envelope.

        Returns:
            A safe in-memory result for future Story 6 lifecycle handling.
        """
        started = time.monotonic()
        parameter_failure, bound_parameters = _build_bound_parameters(request=request)
        if parameter_failure is not None:
            return self._fail_before_invocation(
                request=request,
                step=request.plan.steps[0],
                descriptor=request.step_attempts[0],
                failure=parameter_failure,
                started=started,
                completed_count=0,
                reused_count=0,
            )
        descriptors = {descriptor.step_id: descriptor for descriptor in request.step_attempts}
        top_level_inputs = _build_top_level_inputs(request=request)
        produced_outputs: dict[str, TransformationProviderOutputReference] = {}
        completed_count = 0
        reused_count = 0
        final_reference_count = 0

        for step in request.plan.steps:
            descriptor = descriptors[step.step_id]
            if self._is_cancelled(request=request):
                return self._cancel_attempt(
                    request=request,
                    descriptor=descriptor,
                    started=started,
                    completed_count=completed_count,
                    reused_count=reused_count,
                    final_reference_count=final_reference_count,
                    disposition=ProviderSideEffectDisposition.NOT_STARTED,
                )
            if request.runtime_context.deadline.has_deadline_expired():
                failure = _failure_for_step(
                    step=step,
                    code="execution_timeout",
                    message="The transformation execution deadline expired before invocation.",
                    retryable=True,
                    disposition=ProviderSideEffectDisposition.NOT_STARTED,
                )
                return self._fail_before_invocation(
                    request=request,
                    step=step,
                    descriptor=descriptor,
                    failure=failure,
                    started=started,
                    completed_count=completed_count,
                    reused_count=reused_count,
                )
            try:
                inputs = _resolve_step_inputs(
                    step=step,
                    top_level_inputs=top_level_inputs,
                    produced_outputs=produced_outputs,
                )
                input_snapshot = tuple(serialize_input_reference(value) for value in inputs)
            except (KeyError, ValueError, InvalidTransformationReferenceError):
                failure = _failure_for_step(
                    step=step,
                    code="invalid_provider_result",
                    message="A required step input reference is missing or invalid.",
                    retryable=False,
                    disposition=ProviderSideEffectDisposition.NOT_STARTED,
                )
                return self._fail_before_invocation(
                    request=request,
                    step=step,
                    descriptor=descriptor,
                    failure=failure,
                    started=started,
                    completed_count=completed_count,
                    reused_count=reused_count,
                )
            input_checksum = calculate_reference_snapshot_checksum(
                snapshot_kind="inputs",
                references=input_snapshot,
            )
            verifier = cast(
                TransformationCheckpointReferenceVerifier,
                request.checkpoint_reference_verifier,
            )
            try:
                decision = self._persistence.decide_checkpoint_reuse(
                    request=request,
                    step=step,
                    step_attempt=descriptor,
                    inputs=inputs,
                    input_checksum=input_checksum,
                    verifier=verifier,
                )
            except StaleTransformationAttemptError:
                return _stale_result(request=request, started=started)
            if decision.decision == CheckpointReuseDecisionKind.BLOCKED:
                failure = _failure_for_step(
                    step=step,
                    code=decision.reason_code,
                    message="The prior step attempt cannot be executed or safely reused.",
                    retryable=False,
                    disposition=ProviderSideEffectDisposition.UNKNOWN,
                )
                return self._fail_before_invocation(
                    request=request,
                    step=step,
                    descriptor=descriptor,
                    failure=failure,
                    started=started,
                    completed_count=completed_count,
                    reused_count=reused_count,
                )
            if decision.decision == CheckpointReuseDecisionKind.REUSE:
                elapsed = _elapsed_ms(started)
                try:
                    self._persistence.persist_reused_step(
                        request=request,
                        step=step,
                        step_attempt=descriptor,
                        decision=decision,
                        input_snapshot=input_snapshot,
                        input_checksum=input_checksum,
                        execution_ms=elapsed,
                    )
                except TransformationCancellationPrecedenceError:
                    return self._cancel_attempt(
                        request=request,
                        descriptor=descriptor,
                        started=started,
                        completed_count=completed_count,
                        reused_count=reused_count,
                        final_reference_count=final_reference_count,
                        disposition=ProviderSideEffectDisposition.NOT_STARTED,
                    )
                except StaleTransformationAttemptError:
                    return _stale_result(request=request, started=started)
                _record_outputs(
                    step=step,
                    outputs=decision.outputs,
                    produced_outputs=produced_outputs,
                )
                completed_count += 1
                reused_count += 1
                final_reference_count += _count_final_references(step=step)
                continue

            resolved, provider_resolution_failure = self._resolve_and_validate_provider(step=step)
            if provider_resolution_failure is not None:
                return self._fail_before_invocation(
                    request=request,
                    step=step,
                    descriptor=descriptor,
                    failure=provider_resolution_failure,
                    started=started,
                    completed_count=completed_count,
                    reused_count=reused_count,
                )
            assert resolved is not None
            expected_outputs = tuple(
                TransformationProviderExpectedOutput(
                    logical_name=binding.logical_name,
                    accepted_formats=(binding.data_format,),
                    is_temporary=not binding.is_final_output,
                )
                for binding in step.output_bindings
            )
            provider_request = TransformationProviderExecutionRequest(
                identity=_identity(step),
                run_id=request.run_id,
                run_attempt_number=request.attempt_number,
                run_attempt_token=request.attempt_token,
                step_id=step.step_id,
                step_index=step.step_index,
                step_attempt_id=descriptor.step_attempt_id,
                attempt_idempotency_token=descriptor.attempt_idempotency_token,
                configuration=step.configuration,
                bound_parameters=bound_parameters,
                inputs=inputs,
                expected_outputs=expected_outputs,
                correlation_metadata={"correlation_id": request.correlation_id},
            )
            try:
                validation = resolved.provider.validate_operation(
                    request=TransformationProviderValidationRequest(
                        identity=_identity(step),
                        step_id=step.step_id,
                        configuration=step.configuration,
                        declared_input_formats=tuple(
                            dict.fromkeys(binding.data_format for binding in step.input_bindings)
                        ),
                        declared_output_formats=tuple(
                            dict.fromkeys(binding.data_format for binding in step.output_bindings)
                        ),
                    ),
                )
            except Exception:  # Provider validation is an explicit sanitized trust boundary.
                validation = TransformationProviderValidationResult(
                    is_valid=False,
                    findings=(
                        TransformationProviderValidationFinding(
                            identity=_identity(step),
                            code="provider_runtime_validation_failed",
                            path="$.configuration",
                            message="Provider runtime validation failed unexpectedly.",
                        ),
                    ),
                )
            if not validation.is_valid:
                failure = _failure_for_step(
                    step=step,
                    code="provider_runtime_validation_failed",
                    message="Provider runtime validation rejected the compiled operation.",
                    retryable=False,
                    disposition=ProviderSideEffectDisposition.NOT_STARTED,
                )
                return self._fail_before_invocation(
                    request=request,
                    step=step,
                    descriptor=descriptor,
                    failure=failure,
                    started=started,
                    completed_count=completed_count,
                    reused_count=reused_count,
                )
            try:
                self._persistence.start_step(
                    request=request,
                    step_attempt=descriptor,
                    input_snapshot=input_snapshot,
                    input_checksum=input_checksum,
                )
            except TransformationCancellationPrecedenceError:
                return self._cancel_attempt(
                    request=request,
                    descriptor=descriptor,
                    started=started,
                    completed_count=completed_count,
                    reused_count=reused_count,
                    final_reference_count=final_reference_count,
                    disposition=ProviderSideEffectDisposition.NOT_STARTED,
                )
            except StaleTransformationAttemptError:
                return _stale_result(request=request, started=started)

            provider_started = time.monotonic()
            try:
                provider_result = resolved.provider.execute(
                    request=provider_request,
                    runtime_context=request.runtime_context,
                )
            except TransformationProviderExecutionError as error:
                provider_failure = error.failure
                failure = _failure_for_step(
                    step=step,
                    code="provider_execution_failed",
                    message=provider_failure.message,
                    retryable=provider_failure.retryable,
                    disposition=provider_failure.side_effect_disposition,
                )
                return self._persist_runtime_failure(
                    request=request,
                    descriptor=descriptor,
                    failure=failure,
                    started=started,
                    completed_count=completed_count,
                    reused_count=reused_count,
                )
            except Exception:  # Providers are an explicit trust boundary.
                failure = _failure_for_step(
                    step=step,
                    code="unexpected_execution_failure",
                    message="The transformation provider failed unexpectedly.",
                    retryable=step.capabilities.is_idempotent,
                    disposition=ProviderSideEffectDisposition.UNKNOWN,
                )
                return self._persist_runtime_failure(
                    request=request,
                    descriptor=descriptor,
                    failure=failure,
                    started=started,
                    completed_count=completed_count,
                    reused_count=reused_count,
                )
            provider_execution_ms = _elapsed_ms(provider_started)
            try:
                outputs = _validate_provider_result(
                    step=step,
                    identity=_identity(step),
                    result_identity=provider_result.identity,
                    outputs=provider_result.outputs,
                )
                output_snapshot = tuple(serialize_output_reference(value) for value in outputs)
            except (ValueError, InvalidTransformationReferenceError):
                failure = _failure_for_step(
                    step=step,
                    code="invalid_provider_result",
                    message="The provider returned an invalid reference-only result.",
                    retryable=False,
                    disposition=provider_result.side_effect_disposition,
                )
                return self._persist_runtime_failure(
                    request=request,
                    descriptor=descriptor,
                    failure=failure,
                    started=started,
                    completed_count=completed_count,
                    reused_count=reused_count,
                )
            if self._is_cancelled(request=request):
                return self._cancel_attempt(
                    request=request,
                    descriptor=descriptor,
                    started=started,
                    completed_count=completed_count,
                    reused_count=reused_count,
                    final_reference_count=final_reference_count,
                    disposition=provider_result.side_effect_disposition,
                    diagnostic_outputs=output_snapshot,
                )
            if request.runtime_context.deadline.has_deadline_expired():
                disposition = provider_result.side_effect_disposition
                failure = _failure_for_step(
                    step=step,
                    code="execution_timeout",
                    message="The transformation execution deadline expired after invocation.",
                    retryable=(
                        disposition == ProviderSideEffectDisposition.NOT_STARTED
                        or step.capabilities.is_idempotent
                    ),
                    disposition=disposition,
                )
                return self._persist_runtime_failure(
                    request=request,
                    descriptor=descriptor,
                    failure=failure,
                    started=started,
                    completed_count=completed_count,
                    reused_count=reused_count,
                    diagnostic_outputs=output_snapshot,
                )
            output_checksum = calculate_reference_snapshot_checksum(
                snapshot_kind="outputs",
                references=output_snapshot,
            )
            try:
                self._persistence.persist_step_success(
                    request=request,
                    step=step,
                    step_attempt=descriptor,
                    outputs=outputs,
                    output_snapshot=output_snapshot,
                    output_checksum=output_checksum,
                    side_effect_disposition=provider_result.side_effect_disposition,
                    execution_ms=provider_execution_ms,
                )
            except TransformationCancellationPrecedenceError:
                return self._cancel_attempt(
                    request=request,
                    descriptor=descriptor,
                    started=started,
                    completed_count=completed_count,
                    reused_count=reused_count,
                    final_reference_count=final_reference_count,
                    disposition=provider_result.side_effect_disposition,
                    diagnostic_outputs=output_snapshot,
                )
            except StaleTransformationAttemptError:
                return _stale_result(request=request, started=started)
            _record_outputs(step=step, outputs=outputs, produced_outputs=produced_outputs)
            completed_count += 1
            final_reference_count += _count_final_references(step=step)

        if self._is_cancelled(request=request):
            return self._cancel_attempt(
                request=request,
                descriptor=None,
                started=started,
                completed_count=completed_count,
                reused_count=reused_count,
                final_reference_count=final_reference_count,
                disposition=ProviderSideEffectDisposition.NOT_STARTED,
            )
        return TransformationRunAttemptExecutionResult(
            run_id=request.run_id,
            attempt_number=request.attempt_number,
            attempt_token=request.attempt_token,
            outcome=TransformationRunAttemptExecutionOutcome.SUCCEEDED,
            completed_count=completed_count,
            failed_count=0,
            cancelled_count=0,
            reused_count=reused_count,
            final_reference_count=final_reference_count,
            retryable=False,
            execution_ms=_elapsed_ms(started),
        )

    def _resolve_and_validate_provider(
        self,
        *,
        step: TransformationExecutionPlanStep,
    ) -> tuple[
        ResolvedTransformationProviderOperation | None,
        TransformationExecutionFailure | None,
    ]:
        try:
            resolved = self._provider_registry.resolve_operation(
                provider_key=step.provider_key,
                provider_version=step.provider_version,
                operation_key=step.operation_key,
                operation_version=step.operation_version,
            )
        except TransformationProviderNotFoundError:
            return None, _failure_for_step(
                step=step,
                code="provider_not_found",
                message="The exact transformation provider is not registered.",
                retryable=False,
                disposition=ProviderSideEffectDisposition.NOT_STARTED,
            )
        except TransformationOperationNotFoundError:
            return None, _failure_for_step(
                step=step,
                code="operation_not_found",
                message="The exact transformation operation is not registered.",
                retryable=False,
                disposition=ProviderSideEffectDisposition.NOT_STARTED,
            )
        actual_checksum = calculate_registered_operation_capability_checksum(
            provider_descriptor=resolved.provider.capability_descriptor,
            operation_descriptor=resolved.operation,
        )
        if actual_checksum != step.capabilities.capability_checksum:
            return None, _failure_for_step(
                step=step,
                code="capability_drift",
                message="Registered provider capabilities differ from the execution plan.",
                retryable=False,
                disposition=ProviderSideEffectDisposition.NOT_STARTED,
                metadata={
                    "expected_checksum": step.capabilities.capability_checksum,
                    "actual_checksum": actual_checksum,
                },
            )
        return resolved, None

    def _fail_before_invocation(  # noqa: PLR0913
        self,
        *,
        request: TransformationRunAttemptExecutionRequest,
        step: TransformationExecutionPlanStep,
        descriptor: TransformationStepAttemptDescriptor,
        failure: TransformationExecutionFailure,
        started: float,
        completed_count: int,
        reused_count: int,
    ) -> TransformationRunAttemptExecutionResult:
        _ = step
        return self._persist_runtime_failure(
            request=request,
            descriptor=descriptor,
            failure=failure,
            started=started,
            completed_count=completed_count,
            reused_count=reused_count,
        )

    def _persist_runtime_failure(  # noqa: PLR0913
        self,
        *,
        request: TransformationRunAttemptExecutionRequest,
        descriptor: TransformationStepAttemptDescriptor,
        failure: TransformationExecutionFailure,
        started: float,
        completed_count: int,
        reused_count: int,
        diagnostic_outputs: tuple[dict[str, object], ...] = (),
    ) -> TransformationRunAttemptExecutionResult:
        if self._is_cancelled(request=request):
            return self._cancel_attempt(
                request=request,
                descriptor=descriptor,
                started=started,
                completed_count=completed_count,
                reused_count=reused_count,
                final_reference_count=0,
                disposition=failure.side_effect_disposition,
                diagnostic_outputs=diagnostic_outputs,
            )
        try:
            self._persistence.persist_step_failure(
                request=request,
                step_attempt=descriptor,
                failure=failure,
                execution_ms=_elapsed_ms(started),
                diagnostic_outputs=diagnostic_outputs,
            )
        except TransformationCancellationPrecedenceError:
            return self._cancel_attempt(
                request=request,
                descriptor=descriptor,
                started=started,
                completed_count=completed_count,
                reused_count=reused_count,
                final_reference_count=sum(
                    _count_final_references(step=step)
                    for step in request.plan.steps[:completed_count]
                ),
                disposition=failure.side_effect_disposition,
                diagnostic_outputs=diagnostic_outputs,
            )
        except StaleTransformationAttemptError:
            return _stale_result(request=request, started=started)
        return TransformationRunAttemptExecutionResult(
            run_id=request.run_id,
            attempt_number=request.attempt_number,
            attempt_token=request.attempt_token,
            outcome=TransformationRunAttemptExecutionOutcome.FAILED,
            completed_count=completed_count,
            failed_count=1,
            cancelled_count=max(len(request.plan.steps) - completed_count - 1, 0),
            reused_count=reused_count,
            final_reference_count=sum(
                _count_final_references(step=step)
                for step in request.plan.steps[:completed_count]
            ),
            retryable=failure.retryable,
            execution_ms=_elapsed_ms(started),
            failure=failure,
        )

    def _cancel_attempt(  # noqa: PLR0913
        self,
        *,
        request: TransformationRunAttemptExecutionRequest,
        descriptor: TransformationStepAttemptDescriptor | None,
        started: float,
        completed_count: int,
        reused_count: int,
        final_reference_count: int,
        disposition: ProviderSideEffectDisposition,
        diagnostic_outputs: tuple[dict[str, object], ...] = (),
    ) -> TransformationRunAttemptExecutionResult:
        code = (
            "side_effect_ambiguous"
            if disposition == ProviderSideEffectDisposition.UNKNOWN
            else "execution_cancelled"
        )
        failure = TransformationExecutionFailure(
            code=code,
            message="Transformation execution was cancelled.",
            retryable=False,
            side_effect_disposition=disposition,
        )
        try:
            self._persistence.persist_cancellation(
                request=request,
                current_step_attempt=descriptor,
                failure=failure,
                execution_ms=_elapsed_ms(started),
                diagnostic_outputs=diagnostic_outputs,
            )
        except StaleTransformationAttemptError:
            return _stale_result(request=request, started=started)
        return TransformationRunAttemptExecutionResult(
            run_id=request.run_id,
            attempt_number=request.attempt_number,
            attempt_token=request.attempt_token,
            outcome=TransformationRunAttemptExecutionOutcome.CANCELLED,
            completed_count=completed_count,
            failed_count=(
                1
                if descriptor is not None
                and disposition
                in (ProviderSideEffectDisposition.APPLIED, ProviderSideEffectDisposition.UNKNOWN)
                else 0
            ),
            cancelled_count=max(len(request.plan.steps) - completed_count, 0),
            reused_count=reused_count,
            final_reference_count=final_reference_count,
            retryable=False,
            execution_ms=_elapsed_ms(started),
            failure=failure,
        )

    def _is_cancelled(self, *, request: TransformationRunAttemptExecutionRequest) -> bool:
        return (
            request.runtime_context.cancellation.is_cancellation_requested()
            or self._persistence.is_cancellation_requested(request=request)
        )


def _build_bound_parameters(
    *,
    request: TransformationRunAttemptExecutionRequest,
) -> tuple[TransformationExecutionFailure | None, Mapping[str, object]]:
    values: dict[str, object] = {}
    for parameter in request.plan.parameters:
        if parameter.classification != ParameterValueClassification.LITERAL:
            return (
                TransformationExecutionFailure(
                    code="unsupported_secret_parameter",
                    message="Only literal bound parameters are supported for execution.",
                    retryable=False,
                    side_effect_disposition=ProviderSideEffectDisposition.NOT_STARTED,
                ),
                {},
            )
        values[parameter.parameter_key] = parameter.value
    return None, values


def _build_top_level_inputs(
    *,
    request: TransformationRunAttemptExecutionRequest,
) -> dict[str, TransformationProviderInputReference]:
    output: dict[str, TransformationProviderInputReference] = {}
    for plan_input in request.plan.inputs:
        reference = plan_input.artifact_uri or plan_input.source_uri
        validate_canonical_reference(reference)
        output[plan_input.logical_name] = TransformationProviderInputReference(
            logical_name=plan_input.logical_name,
            materialized_reference=reference,
            data_format=plan_input.data_format,
            integrity=TransformationIntegrityMetadata(
                size_bytes=plan_input.artifact_size_bytes,
                version_identifier=plan_input.dataset_version_id,
                metadata={
                    "content_hash": plan_input.content_hash,
                    "artifact_checksum": plan_input.artifact_checksum,
                },
            ),
            metadata={
                "dataset_id": plan_input.dataset_id,
                "dataset_version_id": plan_input.dataset_version_id,
                "artifact_id": plan_input.artifact_id,
                "storage_provider": plan_input.storage_provider,
            },
        )
    return output


def _resolve_step_inputs(
    *,
    step: TransformationExecutionPlanStep,
    top_level_inputs: Mapping[str, TransformationProviderInputReference],
    produced_outputs: Mapping[str, TransformationProviderOutputReference],
) -> tuple[TransformationProviderInputReference, ...]:
    resolved: list[TransformationProviderInputReference] = []
    for binding in step.input_bindings:
        if binding.source_kind == ExecutionPlanSymbolSourceKind.TOP_LEVEL_INPUT:
            source = top_level_inputs[binding.source_symbol]
        else:
            produced = produced_outputs[binding.source_symbol]
            source = TransformationProviderInputReference(
                logical_name=binding.logical_name,
                materialized_reference=produced.materialized_reference,
                data_format=produced.data_format,
                integrity=produced.integrity,
                metadata=produced.metadata,
            )
        if source.data_format != binding.data_format:
            raise ValueError("Step input format differs from its compiled binding.")
        if source.logical_name != binding.logical_name:
            source = TransformationProviderInputReference(
                logical_name=binding.logical_name,
                materialized_reference=source.materialized_reference,
                data_format=source.data_format,
                integrity=source.integrity,
                metadata=source.metadata,
            )
        resolved.append(source)
    return tuple(resolved)


def _validate_provider_result(
    *,
    step: TransformationExecutionPlanStep,
    identity: ResolvedTransformationOperationIdentity,
    result_identity: ResolvedTransformationOperationIdentity,
    outputs: tuple[TransformationProviderOutputReference, ...],
) -> tuple[TransformationProviderOutputReference, ...]:
    if result_identity != identity or len(outputs) != len(step.output_bindings):
        raise ValueError("Provider result identity or output count is invalid.")
    by_name = {output.logical_name: output for output in outputs}
    if len(by_name) != len(outputs):
        raise ValueError("Provider output logical names must be unique.")
    validated: list[TransformationProviderOutputReference] = []
    for binding in step.output_bindings:
        output = by_name.get(binding.logical_name)
        if (
            output is None
            or output.data_format != binding.data_format
            or output.is_temporary != (not binding.is_final_output)
        ):
            raise ValueError("Provider output does not match its exact declaration.")
        validate_canonical_reference(output.materialized_reference)
        validated.append(output)
    return tuple(validated)


def _identity(step: TransformationExecutionPlanStep) -> ResolvedTransformationOperationIdentity:
    return ResolvedTransformationOperationIdentity(
        provider_key=step.provider_key,
        provider_version=step.provider_version,
        operation_key=step.operation_key,
        operation_version=step.operation_version,
    )


def _failure_for_step(  # noqa: PLR0913
    *,
    step: TransformationExecutionPlanStep,
    code: str,
    message: str,
    retryable: bool,
    disposition: ProviderSideEffectDisposition,
    metadata: Mapping[str, object] | None = None,
) -> TransformationExecutionFailure:
    return TransformationExecutionFailure(
        code=code,
        message=message,
        retryable=retryable,
        side_effect_disposition=disposition,
        step_id=step.step_id,
        step_index=step.step_index,
        provider_key=step.provider_key,
        provider_version=step.provider_version,
        operation_key=step.operation_key,
        operation_version=step.operation_version,
        metadata=metadata or {},
    )


def _record_outputs(
    *,
    step: TransformationExecutionPlanStep,
    outputs: tuple[TransformationProviderOutputReference, ...],
    produced_outputs: dict[str, TransformationProviderOutputReference],
) -> None:
    by_name = {output.logical_name: output for output in outputs}
    for binding in step.output_bindings:
        produced_outputs[binding.logical_name] = by_name[binding.logical_name]


def _count_final_references(*, step: TransformationExecutionPlanStep) -> int:
    return sum(binding.is_final_output for binding in step.output_bindings)


def _elapsed_ms(started: float) -> int:
    return max(int((time.monotonic() - started) * 1000), 0)


def _stale_result(
    *,
    request: TransformationRunAttemptExecutionRequest,
    started: float,
) -> TransformationRunAttemptExecutionResult:
    failure = TransformationExecutionFailure(
        code="stale_attempt",
        message="The transformation run attempt no longer owns the persistence fence.",
        retryable=False,
        side_effect_disposition=ProviderSideEffectDisposition.UNKNOWN,
    )
    return TransformationRunAttemptExecutionResult(
        run_id=request.run_id,
        attempt_number=request.attempt_number,
        attempt_token=request.attempt_token,
        outcome=TransformationRunAttemptExecutionOutcome.STALE,
        completed_count=0,
        failed_count=0,
        cancelled_count=0,
        reused_count=0,
        final_reference_count=0,
        retryable=False,
        execution_ms=_elapsed_ms(started),
        failure=failure,
    )
