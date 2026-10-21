from __future__ import annotations

import ast
import json
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import FrozenInstanceError, fields, is_dataclass
from enum import StrEnum
from pathlib import Path

import pytest

from resource_registry.application.contracts import (
    APPLICATION_CONTRACT_VERSION,
    ApplicationContract,
    ApplicationError,
    AuthorizationError,
    CancelledError,
    ConflictError,
    ErrorCategory,
    ExecutionState,
    HealthState,
    InternalError,
    NotFoundError,
    OperationFailedError,
    OutputKind,
    OutputStatus,
    RunStatus,
    RuntimeUnavailableError,
    StepStatus,
    TimeoutError,
    TransformationDiagnosticSummary,
    TransformationExecutionAccepted,
    TransformationExecutionRequest,
    TransformationExecutionResult,
    TransformationExecutionSummary,
    TransformationInputBinding,
    TransformationOutputReference,
    TransformationRunReference,
    TransformationStepSummary,
    ValidationError,
)

_CONTRACT_ROOT = Path("resource_registry/application/contracts")
_FORBIDDEN_IMPORT_PREFIXES = (
    "django",
    "rest_framework",
    "resource_registry.models",
    "resource_registry.api",
    "resource_registry.serializers",
    "resource_registry.storage",
    "resource_registry.services",
    "resource_registry.transformations",
    "BotServer",
)


def _binding(name: str = "source") -> TransformationInputBinding:
    return TransformationInputBinding(
        input_name=name,
        dataset_id=f"dataset-{name}",
        dataset_version_id=f"version-{name}",
    )


def _request(*, reverse_order: bool = False) -> TransformationExecutionRequest:
    bindings: tuple[TransformationInputBinding, ...] = (
        _binding("beta"),
        _binding("alpha"),
    )
    parameters = {"zeta": [{"enabled": True}], "alpha": 1}
    if reverse_order:
        bindings = tuple(reversed(bindings))
        parameters = {"alpha": 1, "zeta": [{"enabled": True}]}
    return TransformationExecutionRequest(
        tenant_id="tenant-one",
        definition_id="definition-one",
        definition_version_id="version-one",
        parameters=parameters,
        input_bindings=bindings,
        caller_idempotency_key="caller-key",
        correlation_id="correlation-one",
    )


def _run() -> TransformationRunReference:
    return TransformationRunReference(
        tenant_id="tenant-one",
        run_id="run-one",
        definition_id="definition-one",
        definition_version_id="version-one",
        status=RunStatus.QUEUED,
    )


def _to_json_value(value: object) -> object:
    if isinstance(value, StrEnum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _to_json_value(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, Mapping):
        return {key: _to_json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_to_json_value(item) for item in value]
    return value


def test_request_is_recursively_immutable_deterministic_and_hashable() -> None:
    first = _request()
    second = _request(reverse_order=True)

    assert first == second
    assert hash(first) == hash(second)
    assert tuple(first.parameters) == ("alpha", "zeta")
    assert tuple(binding.input_name for binding in first.input_bindings) == ("alpha", "beta")
    nested = first.parameters["zeta"]
    assert isinstance(nested, tuple)
    with pytest.raises(TypeError):
        first.parameters["new"] = "value"  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        first.tenant_id = "changed"  # type: ignore[misc]


def test_request_rejects_duplicate_input_names_and_non_json_parameters() -> None:
    with pytest.raises(ValueError, match="unique"):
        TransformationExecutionRequest(
            tenant_id="tenant-one",
            definition_id="definition-one",
            definition_version_id="version-one",
            parameters={},
            input_bindings=(_binding(), _binding()),
            caller_idempotency_key="caller-key",
            correlation_id="correlation-one",
        )
    with pytest.raises(ValueError, match="JSON-compatible"):
        TransformationExecutionRequest(
            tenant_id="tenant-one",
            definition_id="definition-one",
            definition_version_id="version-one",
            parameters={"invalid": object()},
            input_bindings=(),
            caller_idempotency_key="caller-key",
            correlation_id="correlation-one",
        )


def test_contracts_are_json_compatible_with_canonical_field_and_tuple_ordering() -> None:
    output = TransformationOutputReference(
        output_id="output-one",
        logical_name="result",
        kind=OutputKind.DATASET_VERSION,
        status=OutputStatus.REGISTERED,
        dataset_id="dataset-one",
        dataset_version_id="dataset-version-one",
        artifact_id="artifact-one",
    )
    step = TransformationStepSummary(
        step_id="step-one",
        attempt_number=1,
        status=StepStatus.SUCCEEDED,
        duration_seconds=0.5,
    )
    summary = TransformationExecutionSummary(
        run_id="run-one",
        status=RunStatus.SUCCEEDED,
        execution_state=ExecutionState.COMPLETED,
        progress=1.0,
        completed_steps=1,
        total_steps=1,
        duration_seconds=0.5,
    )
    result = TransformationExecutionResult(
        run=_run(),
        summary=summary,
        steps=(step,),
        outputs=(output,),
    )

    serialized = json.dumps(
        _to_json_value(result),
        sort_keys=False,
        separators=(",", ":"),
        allow_nan=False,
    )

    assert serialized.startswith('{"contract_version":"transformation-application/1.0"')
    assert '"kind":"DATASET_VERSION"' in serialized
    assert "storage" not in serialized
    assert "checkpoint" not in serialized
    assert hash(result)


def test_response_and_diagnostic_contracts_are_frozen_and_hashable() -> None:
    accepted = TransformationExecutionAccepted(
        run=_run(),
        execution_state=ExecutionState.ACCEPTED,
        idempotent_replay=False,
        correlation_id="correlation-one",
    )
    diagnostics = TransformationDiagnosticSummary(
        health_state=HealthState.READY,
        aggregate_counts={"RUNNING": 1, "QUEUED": 2},
        warnings=("worker_lag", "audit_degraded", "worker_lag"),
        durations={"execution": 0.1, "finalization": 0.15},
        duration_seconds=0.25,
        progress=0.5,
    )

    assert hash(accepted)
    assert hash(diagnostics)
    assert diagnostics.warnings == ("audit_degraded", "worker_lag")
    assert tuple(diagnostics.aggregate_counts) == ("QUEUED", "RUNNING")
    assert tuple(diagnostics.durations) == ("execution", "finalization")
    assert accepted.contract_version == APPLICATION_CONTRACT_VERSION
    assert diagnostics.contract_version == APPLICATION_CONTRACT_VERSION

    with pytest.raises(ValueError, match="progress"):
        TransformationDiagnosticSummary(
            health_state=HealthState.READY,
            aggregate_counts={},
            warnings=(),
            progress=float("nan"),
        )


def test_output_contracts_support_all_public_kinds_without_storage_details() -> None:
    standalone = TransformationOutputReference(
        output_id="standalone-one",
        logical_name="standalone",
        kind=OutputKind.STANDALONE,
        status=OutputStatus.REGISTERED,
    )
    dataset = TransformationOutputReference(
        output_id="dataset-output",
        logical_name="dataset",
        kind=OutputKind.DATASET,
        status=OutputStatus.REGISTERED,
        dataset_id="dataset-one",
    )
    dataset_version = TransformationOutputReference(
        output_id="version-output",
        logical_name="version",
        kind=OutputKind.DATASET_VERSION,
        status=OutputStatus.REGISTERED,
        dataset_id="dataset-one",
        dataset_version_id="version-one",
    )

    assert {standalone.kind, dataset.kind, dataset_version.kind} == set(OutputKind)
    assert all(
        isinstance(hash(output), int) for output in (standalone, dataset, dataset_version)
    )
    assert not {
        "uri",
        "path",
        "signed_url",
        "provider",
        "checkpoint",
    }.intersection(field.name for field in fields(TransformationOutputReference))


def test_error_hierarchy_has_stable_categories_and_safe_immutable_metadata() -> None:
    error_types = (
        (ValidationError, ErrorCategory.VALIDATION),
        (AuthorizationError, ErrorCategory.AUTHORIZATION),
        (ConflictError, ErrorCategory.CONFLICT),
        (NotFoundError, ErrorCategory.NOT_FOUND),
        (RuntimeUnavailableError, ErrorCategory.RUNTIME_UNAVAILABLE),
        (OperationFailedError, ErrorCategory.OPERATION_FAILED),
        (TimeoutError, ErrorCategory.TIMEOUT),
        (CancelledError, ErrorCategory.CANCELLED),
        (InternalError, ErrorCategory.INTERNAL),
    )

    for error_type, category in error_types:
        error = error_type(
            code="stable_error",
            public_message="The operation could not be completed.",
            retryable=category in {ErrorCategory.RUNTIME_UNAVAILABLE, ErrorCategory.TIMEOUT},
            correlation_id="correlation-one",
            metadata={"field_names": ["definition_id"]},
        )
        assert isinstance(error, ApplicationError)
        assert error.category == category
        assert hash(error)
        assert str(error) == error.public_message
        assert error.contract_version == APPLICATION_CONTRACT_VERSION
        with pytest.raises(FrozenInstanceError):
            error.args = ("changed",)


def test_errors_reject_unstable_codes_and_recursively_unsafe_metadata() -> None:
    with pytest.raises(ValueError, match="lower snake"):
        ValidationError(code="Invalid-Request", public_message="Invalid request.")
    with pytest.raises(ValueError, match="unsafe"):
        InternalError(
            code="internal_failure",
            public_message="The operation failed.",
            metadata={"details": [{"provider_message": "not public"}]},
        )


def test_public_enum_values_are_explicit_and_stable() -> None:
    assert tuple(RunStatus) == (
        RunStatus.QUEUED,
        RunStatus.RUNNING,
        RunStatus.OUTPUT_PENDING,
        RunStatus.SUCCEEDED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    )
    assert tuple(OutputKind) == (
        OutputKind.STANDALONE,
        OutputKind.DATASET,
        OutputKind.DATASET_VERSION,
    )
    assert tuple(HealthState) == (
        HealthState.READY,
        HealthState.DEGRADED,
        HealthState.NOT_READY,
    )
    assert set(ErrorCategory) == {
        ErrorCategory.VALIDATION,
        ErrorCategory.AUTHORIZATION,
        ErrorCategory.CONFLICT,
        ErrorCategory.NOT_FOUND,
        ErrorCategory.RUNTIME_UNAVAILABLE,
        ErrorCategory.OPERATION_FAILED,
        ErrorCategory.TIMEOUT,
        ErrorCategory.CANCELLED,
        ErrorCategory.INTERNAL,
    }


def test_application_contract_modules_have_only_approved_dependencies() -> None:
    for source_path in _CONTRACT_ROOT.glob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imported_modules = _imported_modules(tree)
        assert not any(
            module.startswith(_FORBIDDEN_IMPORT_PREFIXES) for module in imported_modules
        ), source_path


def test_contract_package_imports_without_django_initialization() -> None:
    script = """
import sys
from resource_registry.application.contracts import TransformationExecutionRequest
assert TransformationExecutionRequest.__name__ == "TransformationExecutionRequest"
assert "django" not in sys.modules
"""
    subprocess.run([sys.executable, "-c", script], check=True)


def test_every_concrete_dto_inherits_the_versioned_application_contract() -> None:
    contract_types = (
        TransformationInputBinding,
        TransformationExecutionRequest,
        TransformationRunReference,
        TransformationStepSummary,
        TransformationExecutionSummary,
        TransformationOutputReference,
        TransformationDiagnosticSummary,
        TransformationExecutionAccepted,
        TransformationExecutionResult,
        ApplicationError,
    )
    assert all(issubclass(contract_type, ApplicationContract) for contract_type in contract_types)


def _imported_modules(tree: ast.AST) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    return imported
