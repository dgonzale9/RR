from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

from resource_registry.transformations.binding.immutable_json import freeze_json_value
from resource_registry.transformations.execution_plan import TransformationExecutionPlan

if TYPE_CHECKING:
    from resource_registry.transformations.operational.contracts import (
        TransformationAuthorizationDecision,
    )

OUTPUT_POLICY_CONTRACT_VERSION = "transformation-output-policy/1.0"


class TransformationOutputPolicyKind(StrEnum):
    """Identify one explicitly supported durable output policy."""

    STANDALONE = "STANDALONE"
    NEW_DATASET = "NEW_DATASET"
    NEW_DATASET_VERSION = "NEW_DATASET_VERSION"


@dataclass(frozen=True)
class TransformationOutputPolicy:
    """Declare immutable durable handling for one compiled final output."""

    logical_name: str
    policy_kind: TransformationOutputPolicyKind
    destination_storage_provider: str
    configuration: Mapping[str, object] = field(default_factory=dict)
    target_dataset_id: UUID | str | None = None
    contract_version: str = OUTPUT_POLICY_CONTRACT_VERSION

    def __post_init__(self) -> None:
        """Recursively freeze policy configuration values."""
        frozen = freeze_json_value(self.configuration)
        if not isinstance(frozen, Mapping):
            raise TypeError("Output policy configuration must be a mapping.")
        object.__setattr__(self, "configuration", frozen)


@dataclass(frozen=True)
class TransformationRunCreationRequest:
    """Request persistence of one explicit compiled transformation execution plan."""

    tenant_id: UUID | str
    definition_id: UUID | str
    definition_version_id: UUID | str
    execution_plan: TransformationExecutionPlan
    output_policies: tuple[TransformationOutputPolicy, ...]
    authorization_decision: TransformationAuthorizationDecision | None = None
    idempotency_key: str | None = None
    created_by_id: int | None = None
    correlation_id: str | None = None


@dataclass(frozen=True)
class TransformationRunCreationResult:
    """Describe a newly created or idempotently resolved transformation run."""

    run_id: UUID
    tenant_id: UUID
    status: str
    definition_id: UUID
    definition_version_id: UUID
    specification_checksum: str
    execution_plan_checksum: str
    request_fingerprint: str
    idempotency_key: str | None
    was_created: bool
    output_slot_count: int
