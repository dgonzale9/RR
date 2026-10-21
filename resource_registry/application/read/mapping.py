from __future__ import annotations

from resource_registry.application.contracts import (
    ExecutionState,
    HealthState,
    OutputKind,
    OutputStatus,
    RunStatus,
    StepStatus,
    TransformationOutputReference,
    TransformationRunReference,
    TransformationStepSummary,
)
from resource_registry.models import (
    TransformationRun,
    TransformationRunOutput,
    TransformationRunOutputPolicyKind,
    TransformationRunOutputStatus,
    TransformationRunStepAttempt,
)


def map_run_reference(run: TransformationRun) -> TransformationRunReference:
    """Map one runtime persistence row into a public run reference."""
    return TransformationRunReference(
        tenant_id=str(run.tenant_id),
        run_id=str(run.id),
        definition_id=str(run.definition_id),
        definition_version_id=str(run.definition_version_id),
        status=RunStatus(run.status),
    )


def map_step_summary(step: TransformationRunStepAttempt) -> TransformationStepSummary:
    """Map one attempt row without checkpoint or provider fields."""
    return TransformationStepSummary(
        step_id=step.step_id,
        attempt_number=step.run_attempt_number,
        status=StepStatus(step.status),
        duration_seconds=(step.execution_ms / 1000 if step.execution_ms is not None else None),
        error_code=step.error_code,
    )


def map_output_reference(output: TransformationRunOutput) -> TransformationOutputReference:
    """Map one output row without URI, storage, workspace, or internal policy details."""
    return TransformationOutputReference(
        output_id=str(output.id),
        logical_name=output.logical_name,
        kind=_map_output_kind(output.output_policy_kind),
        status=_map_output_status(output.status),
        dataset_id=str(output.dataset_id) if output.dataset_id is not None else None,
        dataset_version_id=(
            str(output.dataset_version_id) if output.dataset_version_id is not None else None
        ),
        artifact_id=(
            str(output.dataset_artifact_id) if output.dataset_artifact_id is not None else None
        ),
    )


def map_execution_state(status: str) -> ExecutionState:
    """Map a durable run status into the stable public execution phase."""
    return {
        RunStatus.QUEUED.value: ExecutionState.ACCEPTED,
        RunStatus.RUNNING.value: ExecutionState.IN_PROGRESS,
        RunStatus.OUTPUT_PENDING.value: ExecutionState.IN_PROGRESS,
        RunStatus.SUCCEEDED.value: ExecutionState.COMPLETED,
        RunStatus.FAILED.value: ExecutionState.FAILED,
        RunStatus.CANCELLED.value: ExecutionState.CANCELLED,
    }[status]


def map_health_state(status: str) -> HealthState:
    """Map an internal health value into its independent application enum."""
    return HealthState(status)


def _map_output_kind(policy_kind: str | None) -> OutputKind:
    if policy_kind is None:
        raise ValueError("Transformation output policy kind is unavailable.")
    mapping: dict[str, OutputKind] = {
        TransformationRunOutputPolicyKind.STANDALONE.value: OutputKind.STANDALONE,
        TransformationRunOutputPolicyKind.NEW_DATASET.value: OutputKind.DATASET,
        TransformationRunOutputPolicyKind.NEW_DATASET_VERSION.value: OutputKind.DATASET_VERSION,
    }
    return mapping[policy_kind]


def _map_output_status(status: str) -> OutputStatus:
    mapping: dict[str, OutputStatus] = {
        TransformationRunOutputStatus.PENDING.value: OutputStatus.PENDING,
        TransformationRunOutputStatus.MATERIALIZING.value: OutputStatus.MATERIALIZING,
        TransformationRunOutputStatus.MATERIALIZED.value: OutputStatus.MATERIALIZING,
        TransformationRunOutputStatus.REGISTERED.value: OutputStatus.REGISTERED,
        TransformationRunOutputStatus.CLEANUP_PENDING.value: OutputStatus.FAILED,
        TransformationRunOutputStatus.FAILED.value: OutputStatus.FAILED,
    }
    return mapping[status]
