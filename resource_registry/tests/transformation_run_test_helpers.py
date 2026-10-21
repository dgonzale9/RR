from __future__ import annotations

import hashlib
from dataclasses import replace
from uuid import UUID

from resource_registry.transformations.binding.results import ParameterValueClassification
from resource_registry.transformations.execution_plan import (
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
    TransformationExecutionPlanStep,
)
from resource_registry.transformations.execution_plan.capability_checksum import (
    calculate_capability_snapshot_checksum,
)
from resource_registry.transformations.execution_plan.checksum import (
    calculate_execution_plan_checksum,
)
from resource_registry.transformations.operational.contracts import (
    TRANSFORMATION_AUTHORIZATION_CONTRACT_VERSION,
    TransformationAuthorizationDecision,
)
from resource_registry.transformations.runs import (
    TransformationOutputPolicy,
    TransformationOutputPolicyKind,
)
from resource_registry.utils.json_checksum import canonicalize_transformation_specification


def build_test_authorization_decision(
    *,
    tenant_id: UUID,
    definition_id: UUID,
    definition_version_id: UUID,
    dataset_ids: tuple[str, ...] = (),
) -> TransformationAuthorizationDecision:
    """Build one deterministic authorized Story 8 snapshot for persistence tests."""
    snapshot: dict[str, object] = {
        "contract_version": TRANSFORMATION_AUTHORIZATION_CONTRACT_VERSION,
        "authorization_policy_revision": 1,
        "tenant_id": str(tenant_id),
        "authorized_actor": "test-actor",
        "principal_kind": "SERVICE",
        "definition_id": str(definition_id),
        "definition_version_id": str(definition_version_id),
        "dataset_ids": sorted(dataset_ids),
        "permissions": ["resource_registry.execute_transformation"],
        "provider_operations": [],
        "output_policy_kinds": ["STANDALONE"],
        "governance_policy_revision": 1,
        "governance_provenance": [],
    }
    checksum = hashlib.sha256(
        canonicalize_transformation_specification(snapshot).encode("utf-8"),
    ).hexdigest()
    return TransformationAuthorizationDecision(
        snapshot=snapshot,
        checksum=checksum,
        authorization_policy_revision=1,
        governance_policy_revision=1,
        authorized_actor="test-actor",
    )


def build_test_output_policies(
    output_names: tuple[str, ...] = ("result", "audit"),
    *,
    destination_storage_provider: str = "local",
) -> tuple[TransformationOutputPolicy, ...]:
    """Build explicit standalone policies for transformation-run tests."""
    return tuple(
        TransformationOutputPolicy(
            logical_name=output_name,
            policy_kind=TransformationOutputPolicyKind.STANDALONE,
            destination_storage_provider=destination_storage_provider,
        )
        for output_name in output_names
    )


def build_test_execution_plan(  # noqa: PLR0913
    *,
    tenant_id: UUID,
    definition_id: UUID,
    definition_version_id: UUID,
    specification_checksum: str,
    parameter_value: float = 0.5,
    source_uri: str = "opaque://dataset-version/1",
    output_names: tuple[str, ...] = ("result", "audit"),
    is_idempotent: bool = True,
) -> TransformationExecutionPlan:
    """Build a complete checksummed Story 3 plan for Story 4 persistence tests."""
    provisional_capabilities = ExecutionPlanCapabilitySnapshot(
        provider_key="provider",
        provider_version="v1",
        operation_key="transform",
        operation_version="op1",
        supported_input_formats=("PARQUET",),
        supported_output_formats=("PARQUET",),
        is_idempotent=is_idempotent,
        is_checkpoint_reuse_safe=True,
        is_checkpoint_reconciliation_safe=False,
        supports_cooperative_cancellation=True,
        provider_metadata={"trusted": True},
        operation_metadata={"bounded": True},
        capability_checksum="",
    )
    capabilities = replace(
        provisional_capabilities,
        capability_checksum=calculate_capability_snapshot_checksum(
            snapshot=provisional_capabilities,
        ),
    )
    output_bindings = tuple(
        ExecutionPlanStepOutputBinding(
            logical_name=output_name,
            output_index=output_index,
            data_format="PARQUET",
            is_final_output=True,
            metadata={"position": output_index},
        )
        for output_index, output_name in enumerate(output_names)
    )
    step = TransformationExecutionPlanStep(
        step_index=0,
        step_id="transform",
        provider_key="provider",
        provider_version="v1",
        operation_key="transform",
        operation_version="op1",
        configuration={"mode": "strict"},
        input_bindings=(
            ExecutionPlanStepInputBinding(
                logical_name="source",
                source_symbol="source",
                source_kind=ExecutionPlanSymbolSourceKind.TOP_LEVEL_INPUT,
                data_format="PARQUET",
            ),
        ),
        output_bindings=output_bindings,
        dependency_step_ids=(),
        capabilities=capabilities,
    )
    symbols = (
        ExecutionPlanSymbol(
            symbol_name="source",
            source_kind=ExecutionPlanSymbolSourceKind.TOP_LEVEL_INPUT,
            data_format="PARQUET",
        ),
        *(
            ExecutionPlanSymbol(
                symbol_name=output_name,
                source_kind=ExecutionPlanSymbolSourceKind.STEP_OUTPUT,
                data_format="PARQUET",
                producer_step_id="transform",
                producer_step_index=0,
                producer_output_index=output_index,
            )
            for output_index, output_name in enumerate(output_names)
        ),
    )
    outputs = tuple(
        ExecutionPlanOutput(
            logical_name=output_name,
            source_symbol=output_name,
            producer_step_id="transform",
            producer_step_index=0,
            producer_output_index=output_index,
            data_format="PARQUET",
            metadata={"position": output_index},
        )
        for output_index, output_name in enumerate(output_names)
    )
    provisional = TransformationExecutionPlan(
        contract_version=EXECUTION_PLAN_CONTRACT_VERSION,
        tenant_id=str(tenant_id),
        definition_id=str(definition_id),
        definition_version_id=str(definition_version_id),
        dsl_version="1.0",
        specification_checksum=specification_checksum,
        parameters=(
            ExecutionPlanParameter(
                parameter_key="threshold",
                value=parameter_value,
                classification=ParameterValueClassification.LITERAL,
            ),
        ),
        inputs=(
            ExecutionPlanInput(
                logical_name="source",
                tenant_id=str(tenant_id),
                dataset_id=str(UUID(int=101)),
                dataset_version_id=str(UUID(int=102)),
                version_number=1,
                source_uri=source_uri,
                content_hash="content-hash",
                data_format="PARQUET",
                metadata={"schema_version": "1"},
            ),
        ),
        steps=(step,),
        symbols=symbols,
        outputs=outputs,
        plan_checksum="",
    )
    return replace(
        provisional,
        plan_checksum=calculate_execution_plan_checksum(provisional.to_checksum_dict()),
    )
