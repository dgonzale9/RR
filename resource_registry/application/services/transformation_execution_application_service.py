from __future__ import annotations

from uuid import UUID

from resource_registry.application.contracts import (
    ExecutionState,
    InternalError,
    RunStatus,
    TransformationExecutionAccepted,
    TransformationExecutionRequest,
    TransformationRunReference,
)
from resource_registry.application.services.authorization import (
    TransformationExecutionEntryAuthorizationService,
)
from resource_registry.application.services.results import (
    TransformationExecutionCallerContext,
    TransformationRuntimeInvocationPort,
    TransformationRuntimeInvocationResult,
)
from resource_registry.application.services.transformation_error_translator import (
    TransformationApplicationErrorTranslator,
)
from resource_registry.application.services.validation import TransformationExecutionRequestValidator


class TransformationExecutionApplicationService:
    """Orchestrate validated authorized runtime submission without execution logic."""

    def __init__(
        self,
        *,
        validator: TransformationExecutionRequestValidator,
        authorization: TransformationExecutionEntryAuthorizationService,
        runtime: TransformationRuntimeInvocationPort,
        error_translator: TransformationApplicationErrorTranslator,
    ) -> None:
        self._validator = validator
        self._authorization = authorization
        self._runtime = runtime
        self._error_translator = error_translator

    def execute(
        self,
        *,
        request: TransformationExecutionRequest,
        caller: TransformationExecutionCallerContext,
    ) -> TransformationExecutionAccepted:
        """Validate, authorize, delegate, translate, and return an immutable accepted DTO."""
        try:
            self._validator.validate(request=request)
            self._authorization.authorize(request=request, caller=caller)
            result = self._runtime.submit_execution(request=request, caller=caller)
            return self._map_result(request=request, result=result)
        except Exception as error:
            translated = self._error_translator.translate(
                error=error,
                correlation_id=request.correlation_id,
            )
            raise translated from None

    @staticmethod
    def _map_result(
        *,
        request: TransformationExecutionRequest,
        result: TransformationRuntimeInvocationResult,
    ) -> TransformationExecutionAccepted:
        if (
            result.tenant_id != request.tenant_id
            or result.definition_id != request.definition_id
            or result.definition_version_id != request.definition_version_id
            or not isinstance(result.run_id, str)
            or not _is_canonical_uuid(result.run_id)
            or not isinstance(result.was_created, bool)
        ):
            raise InternalError(
                code="transformation_runtime_result_mismatch",
                public_message="The transformation execution could not be accepted.",
                correlation_id=request.correlation_id,
            )
        try:
            status = RunStatus(result.status)
            execution_state = _map_execution_state(status=status)
        except ValueError as error:
            raise InternalError(
                code="transformation_runtime_result_invalid",
                public_message="The transformation execution could not be accepted.",
                correlation_id=request.correlation_id,
            ) from error
        return TransformationExecutionAccepted(
            run=TransformationRunReference(
                tenant_id=result.tenant_id,
                run_id=result.run_id,
                definition_id=result.definition_id,
                definition_version_id=result.definition_version_id,
                status=status,
            ),
            execution_state=execution_state,
            idempotent_replay=not result.was_created,
            correlation_id=request.correlation_id,
        )


def _map_execution_state(*, status: RunStatus) -> ExecutionState:
    return {
        RunStatus.QUEUED: ExecutionState.ACCEPTED,
        RunStatus.RUNNING: ExecutionState.IN_PROGRESS,
        RunStatus.OUTPUT_PENDING: ExecutionState.IN_PROGRESS,
        RunStatus.SUCCEEDED: ExecutionState.COMPLETED,
        RunStatus.FAILED: ExecutionState.FAILED,
        RunStatus.CANCELLED: ExecutionState.CANCELLED,
    }[status]


def _is_canonical_uuid(value: str) -> bool:
    try:
        return str(UUID(value)) == value.lower()
    except ValueError:
        return False
