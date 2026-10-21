from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TypeGuard, cast
from urllib.parse import parse_qsl, urlsplit

from resource_registry.transformations.binding.results import ParameterValueClassification
from resource_registry.transformations.execution_plan.capability_checksum import (
    calculate_capability_snapshot_checksum,
)
from resource_registry.transformations.execution_plan.checksum import (
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
    TransformationExecutionPlanStep,
)


class TransformationExecutionPlanDeserializationError(ValueError):
    """Raise when a persisted execution-plan snapshot is invalid or tampered."""


class UnsupportedTransformationExecutionPlanVersionError(
    TransformationExecutionPlanDeserializationError,
):
    """Raise when the snapshot uses an unsupported plan contract version."""


class TransformationExecutionPlanDeserializer:
    """Strictly reconstruct and verify immutable persisted execution plans."""

    def deserialize(
        self,
        *,
        snapshot: Mapping[str, object],
    ) -> TransformationExecutionPlan:
        """Deserialize one complete supported plan snapshot."""
        return deserialize_transformation_execution_plan(snapshot)


def deserialize_transformation_execution_plan(
    snapshot: Mapping[str, object],
) -> TransformationExecutionPlan:
    """Reconstruct and verify one exact immutable Story 3 plan snapshot."""
    try:
        _require_fields(
            snapshot,
            {
                "contract_version",
                "tenant_id",
                "definition_id",
                "definition_version_id",
                "dsl_version",
                "specification_checksum",
                "parameters",
                "inputs",
                "steps",
                "symbols",
                "outputs",
                "plan_checksum",
            },
            path="$",
        )
        contract_version = _required_string(snapshot["contract_version"], path="$.contract_version")
        if contract_version != EXECUTION_PLAN_CONTRACT_VERSION:
            raise UnsupportedTransformationExecutionPlanVersionError(
                "Execution-plan contract version is not supported.",
            )
        if _contains_signed_runtime_reference(snapshot):
            raise TransformationExecutionPlanDeserializationError(
                "Execution-plan snapshot contains a signed runtime reference.",
            )
        plan = TransformationExecutionPlan(
            contract_version=contract_version,
            tenant_id=_required_string(snapshot["tenant_id"], path="$.tenant_id"),
            definition_id=_required_string(snapshot["definition_id"], path="$.definition_id"),
            definition_version_id=_required_string(
                snapshot["definition_version_id"],
                path="$.definition_version_id",
            ),
            dsl_version=_required_string(snapshot["dsl_version"], path="$.dsl_version"),
            specification_checksum=_required_string(
                snapshot["specification_checksum"],
                path="$.specification_checksum",
            ),
            parameters=tuple(
                _parse_parameter(value, index=index)
                for index, value in enumerate(
                    _required_sequence(snapshot["parameters"], "$.parameters"),
                )
            ),
            inputs=tuple(
                _parse_input(value, index=index)
                for index, value in enumerate(_required_sequence(snapshot["inputs"], "$.inputs"))
            ),
            steps=tuple(
                _parse_step(value, index=index)
                for index, value in enumerate(_required_sequence(snapshot["steps"], "$.steps"))
            ),
            symbols=tuple(
                _parse_symbol(value, index=index)
                for index, value in enumerate(_required_sequence(snapshot["symbols"], "$.symbols"))
            ),
            outputs=tuple(
                _parse_output(value, index=index)
                for index, value in enumerate(_required_sequence(snapshot["outputs"], "$.outputs"))
            ),
            plan_checksum=_required_sha256(snapshot["plan_checksum"], path="$.plan_checksum"),
        )
    except UnsupportedTransformationExecutionPlanVersionError:
        raise
    except TransformationExecutionPlanDeserializationError:
        raise
    except (TypeError, ValueError) as error:
        raise TransformationExecutionPlanDeserializationError(
            "Execution-plan snapshot does not match the supported immutable contract.",
        ) from error

    recalculated = calculate_execution_plan_checksum(plan.to_checksum_dict())
    if recalculated != plan.plan_checksum:
        raise TransformationExecutionPlanDeserializationError(
            "Execution-plan snapshot checksum does not match its canonical content.",
        )
    return plan


def _parse_parameter(value: object, *, index: int) -> ExecutionPlanParameter:
    path = f"$.parameters.{index}"
    item = _required_mapping(value, path)
    _require_fields(item, {"parameter_key", "classification", "value"}, path=path)
    try:
        classification = ParameterValueClassification(
            _required_string(item["classification"], path=f"{path}.classification"),
        )
    except ValueError as error:
        raise TransformationExecutionPlanDeserializationError(
            "Execution-plan parameter classification is invalid.",
        ) from error
    return ExecutionPlanParameter(
        parameter_key=_required_string(item["parameter_key"], path=f"{path}.parameter_key"),
        value=item["value"],
        classification=classification,
    )


def _parse_input(value: object, *, index: int) -> ExecutionPlanInput:
    path = f"$.inputs.{index}"
    item = _required_mapping(value, path)
    _require_fields(
        item,
        {
            "logical_name",
            "tenant_id",
            "dataset_id",
            "dataset_version_id",
            "version_number",
            "source_uri",
            "content_hash",
            "data_format",
            "artifact_id",
            "artifact_uri",
            "artifact_checksum",
            "artifact_size_bytes",
            "storage_provider",
            "metadata",
        },
        path=path,
    )
    return ExecutionPlanInput(
        logical_name=_required_string(item["logical_name"], path=f"{path}.logical_name"),
        tenant_id=_required_string(item["tenant_id"], path=f"{path}.tenant_id"),
        dataset_id=_required_string(item["dataset_id"], path=f"{path}.dataset_id"),
        dataset_version_id=_required_string(
            item["dataset_version_id"],
            path=f"{path}.dataset_version_id",
        ),
        version_number=_required_integer(item["version_number"], path=f"{path}.version_number"),
        source_uri=_required_string(item["source_uri"], path=f"{path}.source_uri"),
        content_hash=_required_string(item["content_hash"], path=f"{path}.content_hash"),
        data_format=_required_string(item["data_format"], path=f"{path}.data_format"),
        artifact_id=_optional_string(item["artifact_id"], path=f"{path}.artifact_id"),
        artifact_uri=_optional_string(item["artifact_uri"], path=f"{path}.artifact_uri"),
        artifact_checksum=_optional_string(
            item["artifact_checksum"],
            path=f"{path}.artifact_checksum",
        ),
        artifact_size_bytes=_optional_integer(
            item["artifact_size_bytes"],
            path=f"{path}.artifact_size_bytes",
        ),
        storage_provider=_optional_string(
            item["storage_provider"],
            path=f"{path}.storage_provider",
        ),
        metadata=_required_mapping(item["metadata"], f"{path}.metadata"),
    )


def _parse_step(value: object, *, index: int) -> TransformationExecutionPlanStep:
    path = f"$.steps.{index}"
    item = _required_mapping(value, path)
    _require_fields(
        item,
        {
            "step_index",
            "step_id",
            "provider_key",
            "provider_version",
            "operation_key",
            "operation_version",
            "configuration",
            "input_bindings",
            "output_bindings",
            "dependency_step_ids",
            "capabilities",
        },
        path=path,
    )
    step_index = _required_integer(item["step_index"], path=f"{path}.step_index")
    if step_index != index:
        raise TransformationExecutionPlanDeserializationError(
            "Execution-plan step indexes must match declared sequence order.",
        )
    return TransformationExecutionPlanStep(
        step_index=step_index,
        step_id=_required_string(item["step_id"], path=f"{path}.step_id"),
        provider_key=_required_string(item["provider_key"], path=f"{path}.provider_key"),
        provider_version=_required_string(
            item["provider_version"],
            path=f"{path}.provider_version",
        ),
        operation_key=_required_string(item["operation_key"], path=f"{path}.operation_key"),
        operation_version=_required_string(
            item["operation_version"],
            path=f"{path}.operation_version",
        ),
        configuration=_required_mapping(item["configuration"], f"{path}.configuration"),
        input_bindings=tuple(
            _parse_step_input(input_value, path=f"{path}.input_bindings.{binding_index}")
            for binding_index, input_value in enumerate(
                _required_sequence(item["input_bindings"], f"{path}.input_bindings"),
            )
        ),
        output_bindings=tuple(
            _parse_step_output(output_value, path=f"{path}.output_bindings.{binding_index}")
            for binding_index, output_value in enumerate(
                _required_sequence(item["output_bindings"], f"{path}.output_bindings"),
            )
        ),
        dependency_step_ids=tuple(
            _required_string(dependency, path=f"{path}.dependency_step_ids.{dependency_index}")
            for dependency_index, dependency in enumerate(
                _required_sequence(item["dependency_step_ids"], f"{path}.dependency_step_ids"),
            )
        ),
        capabilities=_parse_capabilities(item["capabilities"], path=f"{path}.capabilities"),
    )


def _parse_step_input(value: object, *, path: str) -> ExecutionPlanStepInputBinding:
    item = _required_mapping(value, path)
    _require_fields(
        item,
        {
            "logical_name",
            "source_symbol",
            "source_kind",
            "data_format",
            "producer_step_id",
            "producer_step_index",
            "producer_output_index",
        },
        path=path,
    )
    try:
        source_kind = ExecutionPlanSymbolSourceKind(
            _required_string(item["source_kind"], path=f"{path}.source_kind"),
        )
    except ValueError as error:
        raise TransformationExecutionPlanDeserializationError(
            "Execution-plan input source kind is invalid.",
        ) from error
    return ExecutionPlanStepInputBinding(
        logical_name=_required_string(item["logical_name"], path=f"{path}.logical_name"),
        source_symbol=_required_string(item["source_symbol"], path=f"{path}.source_symbol"),
        source_kind=source_kind,
        data_format=_required_string(item["data_format"], path=f"{path}.data_format"),
        producer_step_id=_optional_string(
            item["producer_step_id"],
            path=f"{path}.producer_step_id",
        ),
        producer_step_index=_optional_integer(
            item["producer_step_index"],
            path=f"{path}.producer_step_index",
        ),
        producer_output_index=_optional_integer(
            item["producer_output_index"],
            path=f"{path}.producer_output_index",
        ),
    )


def _parse_step_output(value: object, *, path: str) -> ExecutionPlanStepOutputBinding:
    item = _required_mapping(value, path)
    _require_fields(
        item,
        {"logical_name", "output_index", "data_format", "is_final_output", "metadata"},
        path=path,
    )
    return ExecutionPlanStepOutputBinding(
        logical_name=_required_string(item["logical_name"], path=f"{path}.logical_name"),
        output_index=_required_integer(item["output_index"], path=f"{path}.output_index"),
        data_format=_required_string(item["data_format"], path=f"{path}.data_format"),
        is_final_output=_required_boolean(
            item["is_final_output"],
            path=f"{path}.is_final_output",
        ),
        metadata=_required_mapping(item["metadata"], f"{path}.metadata"),
    )


def _parse_capabilities(value: object, *, path: str) -> ExecutionPlanCapabilitySnapshot:
    item = _required_mapping(value, path)
    _require_fields(
        item,
        {
            "provider_key",
            "provider_version",
            "operation_key",
            "operation_version",
            "supported_input_formats",
            "supported_output_formats",
            "is_idempotent",
            "is_checkpoint_reuse_safe",
            "is_checkpoint_reconciliation_safe",
            "supports_cooperative_cancellation",
            "provider_metadata",
            "operation_metadata",
            "capability_checksum",
        },
        path=path,
    )
    snapshot = ExecutionPlanCapabilitySnapshot(
        provider_key=_required_string(item["provider_key"], path=f"{path}.provider_key"),
        provider_version=_required_string(
            item["provider_version"],
            path=f"{path}.provider_version",
        ),
        operation_key=_required_string(item["operation_key"], path=f"{path}.operation_key"),
        operation_version=_required_string(
            item["operation_version"],
            path=f"{path}.operation_version",
        ),
        supported_input_formats=_string_tuple(
            item["supported_input_formats"],
            path=f"{path}.supported_input_formats",
        ),
        supported_output_formats=_string_tuple(
            item["supported_output_formats"],
            path=f"{path}.supported_output_formats",
        ),
        is_idempotent=_required_boolean(item["is_idempotent"], path=f"{path}.is_idempotent"),
        is_checkpoint_reuse_safe=_required_boolean(
            item["is_checkpoint_reuse_safe"],
            path=f"{path}.is_checkpoint_reuse_safe",
        ),
        is_checkpoint_reconciliation_safe=_required_boolean(
            item["is_checkpoint_reconciliation_safe"],
            path=f"{path}.is_checkpoint_reconciliation_safe",
        ),
        supports_cooperative_cancellation=_required_boolean(
            item["supports_cooperative_cancellation"],
            path=f"{path}.supports_cooperative_cancellation",
        ),
        provider_metadata=_required_mapping(item["provider_metadata"], f"{path}.provider_metadata"),
        operation_metadata=_required_mapping(
            item["operation_metadata"],
            f"{path}.operation_metadata",
        ),
        capability_checksum=_required_sha256(
            item["capability_checksum"],
            path=f"{path}.capability_checksum",
        ),
    )
    if calculate_capability_snapshot_checksum(snapshot=snapshot) != snapshot.capability_checksum:
        raise TransformationExecutionPlanDeserializationError(
            "Execution-plan capability checksum does not match canonical content.",
        )
    return snapshot


def _parse_symbol(value: object, *, index: int) -> ExecutionPlanSymbol:
    path = f"$.symbols.{index}"
    item = _required_mapping(value, path)
    _require_fields(
        item,
        {
            "symbol_name",
            "source_kind",
            "data_format",
            "producer_step_id",
            "producer_step_index",
            "producer_output_index",
        },
        path=path,
    )
    try:
        source_kind = ExecutionPlanSymbolSourceKind(
            _required_string(item["source_kind"], path=f"{path}.source_kind"),
        )
    except ValueError as error:
        raise TransformationExecutionPlanDeserializationError(
            "Execution-plan symbol source kind is invalid.",
        ) from error
    return ExecutionPlanSymbol(
        symbol_name=_required_string(item["symbol_name"], path=f"{path}.symbol_name"),
        source_kind=source_kind,
        data_format=_required_string(item["data_format"], path=f"{path}.data_format"),
        producer_step_id=_optional_string(
            item["producer_step_id"],
            path=f"{path}.producer_step_id",
        ),
        producer_step_index=_optional_integer(
            item["producer_step_index"],
            path=f"{path}.producer_step_index",
        ),
        producer_output_index=_optional_integer(
            item["producer_output_index"],
            path=f"{path}.producer_output_index",
        ),
    )


def _parse_output(value: object, *, index: int) -> ExecutionPlanOutput:
    path = f"$.outputs.{index}"
    item = _required_mapping(value, path)
    _require_fields(
        item,
        {
            "logical_name",
            "source_symbol",
            "producer_step_id",
            "producer_step_index",
            "producer_output_index",
            "data_format",
            "metadata",
        },
        path=path,
    )
    return ExecutionPlanOutput(
        logical_name=_required_string(item["logical_name"], path=f"{path}.logical_name"),
        source_symbol=_required_string(item["source_symbol"], path=f"{path}.source_symbol"),
        producer_step_id=_required_string(
            item["producer_step_id"],
            path=f"{path}.producer_step_id",
        ),
        producer_step_index=_required_integer(
            item["producer_step_index"],
            path=f"{path}.producer_step_index",
        ),
        producer_output_index=_required_integer(
            item["producer_output_index"],
            path=f"{path}.producer_output_index",
        ),
        data_format=_required_string(item["data_format"], path=f"{path}.data_format"),
        metadata=_required_mapping(item["metadata"], f"{path}.metadata"),
    )


def _require_fields(value: Mapping[str, object], expected: set[str], *, path: str) -> None:
    actual = set(value)
    if actual != expected:
        raise TransformationExecutionPlanDeserializationError(
            f"Execution-plan object at {path} has missing or unknown fields.",
        )


def _required_mapping(value: object, path: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise TransformationExecutionPlanDeserializationError(
            f"Execution-plan value at {path} must be an object.",
        )
    return cast(Mapping[str, object], value)


def _required_sequence(value: object, path: str) -> Sequence[object]:
    if not isinstance(value, list | tuple):
        raise TransformationExecutionPlanDeserializationError(
            f"Execution-plan value at {path} must be an array.",
        )
    return cast(Sequence[object], value)


def _required_string(value: object, *, path: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise TransformationExecutionPlanDeserializationError(
            f"Execution-plan value at {path} must be an exact non-whitespace string.",
        )
    return value


def _optional_string(value: object, *, path: str) -> str | None:
    if value is None:
        return None
    return _required_string(value, path=path)


def _required_integer(value: object, *, path: str) -> int:
    if not _is_integer(value) or value < 0:
        raise TransformationExecutionPlanDeserializationError(
            f"Execution-plan value at {path} must be a non-negative integer.",
        )
    return value


def _optional_integer(value: object, *, path: str) -> int | None:
    if value is None:
        return None
    return _required_integer(value, path=path)


def _required_boolean(value: object, *, path: str) -> bool:
    if not isinstance(value, bool):
        raise TransformationExecutionPlanDeserializationError(
            f"Execution-plan value at {path} must be a boolean.",
        )
    return value


def _required_sha256(value: object, *, path: str) -> str:
    text = _required_string(value, path=path)
    sha256_length = 64
    if len(text) != sha256_length or any(character not in "0123456789abcdef" for character in text):
        raise TransformationExecutionPlanDeserializationError(
            f"Execution-plan value at {path} must be a lowercase SHA-256 digest.",
        )
    return text


def _string_tuple(value: object, *, path: str) -> tuple[str, ...]:
    return tuple(
        _required_string(item, path=f"{path}.{index}")
        for index, item in enumerate(_required_sequence(value, path))
    )


def _is_integer(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _contains_signed_runtime_reference(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(_contains_signed_runtime_reference(item) for item in value.values())
    if isinstance(value, list | tuple):
        return any(_contains_signed_runtime_reference(item) for item in value)
    if not isinstance(value, str):
        return False
    query_keys = {key.lower() for key, _ in parse_qsl(urlsplit(value).query)}
    return bool(query_keys & {"sig", "x-amz-signature", "x-amz-expires", "se", "expires"})
