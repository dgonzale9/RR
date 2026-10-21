from __future__ import annotations

from collections.abc import Mapping, Sequence

from resource_registry.transformations.execution_plan.checksum import (
    calculate_execution_plan_checksum,
)
from resource_registry.transformations.providers import ProviderSideEffectDisposition

CHECKPOINT_CONTRACT_VERSION = "1.0"
STEP_ATTEMPT_TOKEN_CONTRACT_VERSION = "transformation-step-attempt/1.0"


def calculate_reference_snapshot_checksum(
    *,
    snapshot_kind: str,
    references: Sequence[Mapping[str, object]],
) -> str:
    """Calculate one canonical input or output reference snapshot checksum."""
    return calculate_execution_plan_checksum(
        {
            "snapshot_kind": snapshot_kind,
            "references": [dict(reference) for reference in references],
        },
    )


def calculate_step_attempt_idempotency_token(  # noqa: PLR0913
    *,
    run_id: str,
    plan_checksum: str,
    run_attempt_number: int,
    run_attempt_token: str,
    step_id: str,
    step_index: int,
    step_attempt_id: str,
) -> str:
    """Calculate the stable external idempotency token for one exact step-attempt row."""
    return calculate_execution_plan_checksum(
        {
            "token_contract": STEP_ATTEMPT_TOKEN_CONTRACT_VERSION,
            "run_id": run_id,
            "plan_checksum": plan_checksum,
            "run_attempt_number": run_attempt_number,
            "run_attempt_token": run_attempt_token,
            "step_id": step_id,
            "step_index": step_index,
            "step_attempt_id": step_attempt_id,
        },
    )


def calculate_complete_checkpoint_checksum(  # noqa: PLR0913
    *,
    run_id: str,
    run_attempt_number: int,
    run_attempt_token: str,
    plan_checksum: str,
    step_id: str,
    step_index: int,
    provider_key: str,
    provider_version: str,
    operation_key: str,
    operation_version: str,
    capability_checksum: str,
    input_snapshot: Sequence[Mapping[str, object]],
    input_checksum: str,
    output_snapshot: Sequence[Mapping[str, object]],
    output_checksum: str,
    attempt_idempotency_token: str,
    side_effect_disposition: ProviderSideEffectDisposition,
) -> str:
    """Calculate the complete source-attempt-bound restart-safe checkpoint checksum."""
    return calculate_execution_plan_checksum(
        {
            "checkpoint_contract_version": CHECKPOINT_CONTRACT_VERSION,
            "run_id": run_id,
            "run_attempt_number": run_attempt_number,
            "run_attempt_token": run_attempt_token,
            "plan_checksum": plan_checksum,
            "step_id": step_id,
            "step_index": step_index,
            "provider_key": provider_key,
            "provider_version": provider_version,
            "operation_key": operation_key,
            "operation_version": operation_version,
            "capability_checksum": capability_checksum,
            "input_snapshot": [dict(reference) for reference in input_snapshot],
            "input_checksum": input_checksum,
            "output_snapshot": [dict(reference) for reference in output_snapshot],
            "output_checksum": output_checksum,
            "attempt_idempotency_token": attempt_idempotency_token,
            "side_effect_disposition": side_effect_disposition.value,
        },
    )
