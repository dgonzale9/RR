from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import cast

from resource_registry.transformations.binding.immutable_json import freeze_json_value
from resource_registry.transformations.binding.results import (
    ParameterValueClassification,
    TransformationSpecificationBindingResult,
)
from resource_registry.transformations.dsl.model import TransformationSpecification

EXECUTION_PLAN_CONTRACT_VERSION = "1.0"


class ExecutionPlanSymbolSourceKind(StrEnum):
    """Identify the immutable owner category for an execution-plan symbol."""

    TOP_LEVEL_INPUT = "TOP_LEVEL_INPUT"
    STEP_OUTPUT = "STEP_OUTPUT"


@dataclass(frozen=True)
class TransformationDefinitionCompilationSnapshot:
    """Supply definition lifecycle and canonical specification data without ORM objects."""

    tenant_id: str
    definition_id: str
    definition_version_id: str
    definition_is_archived: bool
    version_status: str
    dsl_version: str
    specification_checksum: str
    specification_json: Mapping[str, object]

    def __post_init__(self) -> None:
        """Recursively freeze the normalized specification JSON."""
        object.__setattr__(
            self,
            "specification_json",
            _freeze_mapping(self.specification_json),
        )


@dataclass(frozen=True)
class TransformationExecutionPlanCompilationRequest:
    """Contain immutable inputs required by the pure execution-plan compiler."""

    definition: TransformationDefinitionCompilationSnapshot
    specification: TransformationSpecification
    binding_result: TransformationSpecificationBindingResult


@dataclass(frozen=True)
class ExecutionPlanParameter:
    """Snapshot one bound parameter independently from step configuration."""

    parameter_key: str
    value: object
    classification: ParameterValueClassification

    def __post_init__(self) -> None:
        """Recursively freeze the bound JSON value."""
        object.__setattr__(self, "value", freeze_json_value(self.value))

    def to_dict(self) -> dict[str, object]:
        """Return a canonical JSON-compatible parameter payload."""
        return {
            "parameter_key": self.parameter_key,
            "classification": self.classification.value,
            "value": _thaw_value(self.value),
        }


@dataclass(frozen=True)
class ExecutionPlanInput:
    """Snapshot one exact Story 1 top-level input reference."""

    logical_name: str
    tenant_id: str
    dataset_id: str
    dataset_version_id: str
    version_number: int
    source_uri: str
    content_hash: str
    data_format: str
    artifact_id: str | None = None
    artifact_uri: str | None = None
    artifact_checksum: str | None = None
    artifact_size_bytes: int | None = None
    storage_provider: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Recursively freeze safe reference metadata."""
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))

    def to_dict(self) -> dict[str, object]:
        """Return a canonical JSON-compatible exact input snapshot."""
        return {
            "logical_name": self.logical_name,
            "tenant_id": self.tenant_id,
            "dataset_id": self.dataset_id,
            "dataset_version_id": self.dataset_version_id,
            "version_number": self.version_number,
            "source_uri": self.source_uri,
            "content_hash": self.content_hash,
            "data_format": self.data_format,
            "artifact_id": self.artifact_id,
            "artifact_uri": self.artifact_uri,
            "artifact_checksum": self.artifact_checksum,
            "artifact_size_bytes": self.artifact_size_bytes,
            "storage_provider": self.storage_provider,
            "metadata": _thaw_mapping(self.metadata),
        }


@dataclass(frozen=True)
class ExecutionPlanStepInputBinding:
    """Bind one step input to an exact top-level or prior-step symbol."""

    logical_name: str
    source_symbol: str
    source_kind: ExecutionPlanSymbolSourceKind
    data_format: str
    producer_step_id: str | None = None
    producer_step_index: int | None = None
    producer_output_index: int | None = None

    def to_dict(self) -> dict[str, object]:
        """Return a canonical JSON-compatible step input binding."""
        return {
            "logical_name": self.logical_name,
            "source_symbol": self.source_symbol,
            "source_kind": self.source_kind.value,
            "data_format": self.data_format,
            "producer_step_id": self.producer_step_id,
            "producer_step_index": self.producer_step_index,
            "producer_output_index": self.producer_output_index,
        }


@dataclass(frozen=True)
class ExecutionPlanStepOutputBinding:
    """Describe one exact step-produced symbol and its output intent."""

    logical_name: str
    output_index: int
    data_format: str
    is_final_output: bool
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Recursively freeze output declaration metadata."""
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))

    def to_dict(self) -> dict[str, object]:
        """Return a canonical JSON-compatible step output binding."""
        return {
            "logical_name": self.logical_name,
            "output_index": self.output_index,
            "data_format": self.data_format,
            "is_final_output": self.is_final_output,
            "metadata": _thaw_mapping(self.metadata),
        }


@dataclass(frozen=True)
class ExecutionPlanCapabilitySnapshot:
    """Copy all execution-affecting provider capabilities into one plan step."""

    provider_key: str
    provider_version: str
    operation_key: str
    operation_version: str
    supported_input_formats: tuple[str, ...]
    supported_output_formats: tuple[str, ...]
    is_idempotent: bool
    is_checkpoint_reuse_safe: bool
    is_checkpoint_reconciliation_safe: bool
    supports_cooperative_cancellation: bool
    provider_metadata: Mapping[str, object]
    operation_metadata: Mapping[str, object]
    capability_checksum: str

    def __post_init__(self) -> None:
        """Freeze format sequences and descriptor metadata."""
        object.__setattr__(self, "supported_input_formats", tuple(self.supported_input_formats))
        object.__setattr__(self, "supported_output_formats", tuple(self.supported_output_formats))
        object.__setattr__(self, "provider_metadata", _freeze_mapping(self.provider_metadata))
        object.__setattr__(self, "operation_metadata", _freeze_mapping(self.operation_metadata))

    def to_dict(self) -> dict[str, object]:
        """Return a canonical JSON-compatible capability snapshot."""
        return {
            "provider_key": self.provider_key,
            "provider_version": self.provider_version,
            "operation_key": self.operation_key,
            "operation_version": self.operation_version,
            "supported_input_formats": list(self.supported_input_formats),
            "supported_output_formats": list(self.supported_output_formats),
            "is_idempotent": self.is_idempotent,
            "is_checkpoint_reuse_safe": self.is_checkpoint_reuse_safe,
            "is_checkpoint_reconciliation_safe": self.is_checkpoint_reconciliation_safe,
            "supports_cooperative_cancellation": self.supports_cooperative_cancellation,
            "provider_metadata": _thaw_mapping(self.provider_metadata),
            "operation_metadata": _thaw_mapping(self.operation_metadata),
            "capability_checksum": self.capability_checksum,
        }


@dataclass(frozen=True)
class TransformationExecutionPlanStep:
    """Represent one fully bound and provider-resolved execution-plan step."""

    step_index: int
    step_id: str
    provider_key: str
    provider_version: str
    operation_key: str
    operation_version: str
    configuration: Mapping[str, object]
    input_bindings: tuple[ExecutionPlanStepInputBinding, ...]
    output_bindings: tuple[ExecutionPlanStepOutputBinding, ...]
    dependency_step_ids: tuple[str, ...]
    capabilities: ExecutionPlanCapabilitySnapshot

    def __post_init__(self) -> None:
        """Freeze configuration and sequence fields."""
        object.__setattr__(self, "configuration", _freeze_mapping(self.configuration))
        object.__setattr__(self, "input_bindings", tuple(self.input_bindings))
        object.__setattr__(self, "output_bindings", tuple(self.output_bindings))
        object.__setattr__(self, "dependency_step_ids", tuple(self.dependency_step_ids))

    def to_dict(self) -> dict[str, object]:
        """Return the canonical JSON-compatible compiled-step payload."""
        return {
            "step_index": self.step_index,
            "step_id": self.step_id,
            "provider_key": self.provider_key,
            "provider_version": self.provider_version,
            "operation_key": self.operation_key,
            "operation_version": self.operation_version,
            "configuration": _thaw_mapping(self.configuration),
            "input_bindings": [binding.to_dict() for binding in self.input_bindings],
            "output_bindings": [binding.to_dict() for binding in self.output_bindings],
            "dependency_step_ids": list(self.dependency_step_ids),
            "capabilities": self.capabilities.to_dict(),
        }


@dataclass(frozen=True)
class ExecutionPlanSymbol:
    """Record exact ownership and format for one plan symbol."""

    symbol_name: str
    source_kind: ExecutionPlanSymbolSourceKind
    data_format: str
    producer_step_id: str | None = None
    producer_step_index: int | None = None
    producer_output_index: int | None = None

    def to_dict(self) -> dict[str, object]:
        """Return a canonical JSON-compatible symbol payload."""
        return {
            "symbol_name": self.symbol_name,
            "source_kind": self.source_kind.value,
            "data_format": self.data_format,
            "producer_step_id": self.producer_step_id,
            "producer_step_index": self.producer_step_index,
            "producer_output_index": self.producer_output_index,
        }


@dataclass(frozen=True)
class ExecutionPlanOutput:
    """Bind one declared final output to its exact producing step symbol."""

    logical_name: str
    source_symbol: str
    producer_step_id: str
    producer_step_index: int
    producer_output_index: int
    data_format: str
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Recursively freeze final-output declaration metadata."""
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))

    def to_dict(self) -> dict[str, object]:
        """Return a canonical JSON-compatible final-output payload."""
        return {
            "logical_name": self.logical_name,
            "source_symbol": self.source_symbol,
            "producer_step_id": self.producer_step_id,
            "producer_step_index": self.producer_step_index,
            "producer_output_index": self.producer_output_index,
            "data_format": self.data_format,
            "metadata": _thaw_mapping(self.metadata),
        }


@dataclass(frozen=True)
class TransformationExecutionPlan:
    """Own the complete immutable and replayable transformation execution contract."""

    contract_version: str
    tenant_id: str
    definition_id: str
    definition_version_id: str
    dsl_version: str
    specification_checksum: str
    parameters: tuple[ExecutionPlanParameter, ...]
    inputs: tuple[ExecutionPlanInput, ...]
    steps: tuple[TransformationExecutionPlanStep, ...]
    symbols: tuple[ExecutionPlanSymbol, ...]
    outputs: tuple[ExecutionPlanOutput, ...]
    plan_checksum: str

    def __post_init__(self) -> None:
        """Freeze all plan sequence fields."""
        object.__setattr__(self, "parameters", tuple(self.parameters))
        object.__setattr__(self, "inputs", tuple(self.inputs))
        object.__setattr__(self, "steps", tuple(self.steps))
        object.__setattr__(self, "symbols", tuple(self.symbols))
        object.__setattr__(self, "outputs", tuple(self.outputs))

    def to_checksum_dict(self) -> dict[str, object]:
        """Return every execution-affecting plan field except the plan checksum."""
        return {
            "contract_version": self.contract_version,
            "tenant_id": self.tenant_id,
            "definition_id": self.definition_id,
            "definition_version_id": self.definition_version_id,
            "dsl_version": self.dsl_version,
            "specification_checksum": self.specification_checksum,
            "parameters": [parameter.to_dict() for parameter in self.parameters],
            "inputs": [plan_input.to_dict() for plan_input in self.inputs],
            "steps": [step.to_dict() for step in self.steps],
            "symbols": [symbol.to_dict() for symbol in self.symbols],
            "outputs": [output.to_dict() for output in self.outputs],
        }

    def to_summary(self) -> TransformationExecutionPlanSummary:
        """Build a deterministic safe summary without parameters, configurations, or references."""
        identities = tuple(
            (
                step.provider_key,
                step.provider_version,
                step.operation_key,
                step.operation_version,
            )
            for step in self.steps
        )
        formats = tuple(
            sorted(
                {
                    binding.data_format
                    for step in self.steps
                    for binding in step.input_bindings
                }
                | {
                    binding.data_format
                    for step in self.steps
                    for binding in step.output_bindings
                },
            ),
        )
        return TransformationExecutionPlanSummary(
            definition_id=self.definition_id,
            definition_version_id=self.definition_version_id,
            specification_checksum=self.specification_checksum,
            plan_checksum=self.plan_checksum,
            step_count=len(self.steps),
            input_count=len(self.inputs),
            output_count=len(self.outputs),
            provider_operation_identities=identities,
            formats=formats,
        )


@dataclass(frozen=True)
class TransformationExecutionPlanSummary:
    """Expose deterministic non-sensitive plan metadata."""

    definition_id: str
    definition_version_id: str
    specification_checksum: str
    plan_checksum: str
    step_count: int
    input_count: int
    output_count: int
    provider_operation_identities: tuple[tuple[str, str, str, str], ...]
    formats: tuple[str, ...]


def _freeze_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    """Freeze an immutable JSON mapping with deterministic key order."""
    frozen = freeze_json_value(value)
    if not isinstance(frozen, Mapping):
        raise TypeError("Expected an immutable JSON object.")
    return cast(Mapping[str, object], frozen)


def _thaw_mapping(value: Mapping[str, object]) -> dict[str, object]:
    """Return a mutable JSON-compatible copy of an immutable mapping."""
    return {key: _thaw_value(nested_value) for key, nested_value in value.items()}


def _thaw_value(value: object) -> object:
    """Return a mutable JSON-compatible copy of an immutable JSON value."""
    if isinstance(value, Mapping):
        return _thaw_mapping(cast(Mapping[str, object], value))
    if isinstance(value, tuple):
        return [_thaw_value(item) for item in value]
    return value
