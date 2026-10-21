from __future__ import annotations

from collections.abc import Mapping

from resource_registry.transformations.binding.immutable_json import thaw_json_value
from resource_registry.transformations.execution_plan.checksum import (
    calculate_execution_plan_checksum,
)
from resource_registry.transformations.execution_plan.contracts import (
    ExecutionPlanCapabilitySnapshot,
)
from resource_registry.transformations.providers.contracts import (
    TransformationOperationCapabilityDescriptor,
    TransformationProviderCapabilityDescriptor,
)


def build_operation_capability_checksum_payload(  # noqa: PLR0913
    *,
    provider_key: str,
    provider_version: str,
    operation_key: str,
    operation_version: str,
    supported_input_formats: tuple[str, ...],
    supported_output_formats: tuple[str, ...],
    is_idempotent: bool,
    is_checkpoint_reuse_safe: bool,
    is_checkpoint_reconciliation_safe: bool,
    supports_cooperative_cancellation: bool,
    provider_metadata: Mapping[str, object],
    operation_metadata: Mapping[str, object],
) -> dict[str, object]:
    """Build the canonical execution-affecting capability payload."""
    return {
        "provider_key": provider_key,
        "provider_version": provider_version,
        "operation_key": operation_key,
        "operation_version": operation_version,
        "supported_input_formats": list(supported_input_formats),
        "supported_output_formats": list(supported_output_formats),
        "is_idempotent": is_idempotent,
        "is_checkpoint_reuse_safe": is_checkpoint_reuse_safe,
        "is_checkpoint_reconciliation_safe": is_checkpoint_reconciliation_safe,
        "supports_cooperative_cancellation": supports_cooperative_cancellation,
        "provider_metadata": thaw_json_value(provider_metadata),
        "operation_metadata": thaw_json_value(operation_metadata),
    }


def calculate_registered_operation_capability_checksum(
    *,
    provider_descriptor: TransformationProviderCapabilityDescriptor,
    operation_descriptor: TransformationOperationCapabilityDescriptor,
) -> str:
    """Calculate the Story 3 checksum for one currently registered operation."""
    identity = operation_descriptor.identity
    return calculate_execution_plan_checksum(
        build_operation_capability_checksum_payload(
            provider_key=identity.provider_key,
            provider_version=identity.provider_version,
            operation_key=identity.operation_key,
            operation_version=identity.operation_version,
            supported_input_formats=operation_descriptor.supported_input_formats,
            supported_output_formats=operation_descriptor.supported_output_formats,
            is_idempotent=operation_descriptor.is_idempotent,
            is_checkpoint_reuse_safe=operation_descriptor.is_checkpoint_reuse_safe,
            is_checkpoint_reconciliation_safe=(
                operation_descriptor.is_checkpoint_reconciliation_safe
            ),
            supports_cooperative_cancellation=(
                operation_descriptor.supports_cooperative_cancellation
            ),
            provider_metadata=provider_descriptor.metadata,
            operation_metadata=operation_descriptor.metadata,
        ),
    )


def calculate_capability_snapshot_checksum(
    *,
    snapshot: ExecutionPlanCapabilitySnapshot,
) -> str:
    """Recalculate one persisted plan capability snapshot checksum."""
    return calculate_execution_plan_checksum(
        build_operation_capability_checksum_payload(
            provider_key=snapshot.provider_key,
            provider_version=snapshot.provider_version,
            operation_key=snapshot.operation_key,
            operation_version=snapshot.operation_version,
            supported_input_formats=snapshot.supported_input_formats,
            supported_output_formats=snapshot.supported_output_formats,
            is_idempotent=snapshot.is_idempotent,
            is_checkpoint_reuse_safe=snapshot.is_checkpoint_reuse_safe,
            is_checkpoint_reconciliation_safe=snapshot.is_checkpoint_reconciliation_safe,
            supports_cooperative_cancellation=snapshot.supports_cooperative_cancellation,
            provider_metadata=snapshot.provider_metadata,
            operation_metadata=snapshot.operation_metadata,
        ),
    )
