from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import cast

from resource_registry.transformations.binding.immutable_json import (
    ImmutableJsonValueError,
    freeze_json_value,
)
from resource_registry.transformations.providers.exceptions import (
    InvalidOperationKeyError,
    InvalidOperationVersionError,
    InvalidProviderKeyError,
    InvalidProviderVersionError,
    ProviderContractInconsistencyError,
)


@dataclass(frozen=True, order=True)
class TransformationProviderIdentity:
    """Identify one exact transformation provider version."""

    provider_key: str
    provider_version: str

    def __post_init__(self) -> None:
        """Validate exact identity values without normalizing them."""
        validate_provider_key(self.provider_key)
        validate_provider_version(self.provider_version)

    def to_dict(self) -> dict[str, str]:
        """Return a JSON-serializable exact identity."""
        return {
            "provider_key": self.provider_key,
            "provider_version": self.provider_version,
        }


@dataclass(frozen=True, order=True)
class TransformationOperationIdentity:
    """Identify one exact operation version within a provider."""

    operation_key: str
    operation_version: str

    def __post_init__(self) -> None:
        """Validate exact identity values without normalizing them."""
        validate_operation_key(self.operation_key)
        validate_operation_version(self.operation_version)

    def to_dict(self) -> dict[str, str]:
        """Return a JSON-serializable exact identity."""
        return {
            "operation_key": self.operation_key,
            "operation_version": self.operation_version,
        }


@dataclass(frozen=True, order=True)
class ResolvedTransformationOperationIdentity:
    """Identify one exact provider and operation version combination."""

    provider_key: str
    provider_version: str
    operation_key: str
    operation_version: str

    def __post_init__(self) -> None:
        """Validate every exact identity component."""
        validate_provider_key(self.provider_key)
        validate_provider_version(self.provider_version)
        validate_operation_key(self.operation_key)
        validate_operation_version(self.operation_version)

    @property
    def provider_identity(self) -> TransformationProviderIdentity:
        """Return the exact provider portion of the identity."""
        return TransformationProviderIdentity(self.provider_key, self.provider_version)

    @property
    def operation_identity(self) -> TransformationOperationIdentity:
        """Return the exact operation portion of the identity."""
        return TransformationOperationIdentity(self.operation_key, self.operation_version)

    def to_dict(self) -> dict[str, str]:
        """Return a JSON-serializable fully resolved identity."""
        return {
            "provider_key": self.provider_key,
            "provider_version": self.provider_version,
            "operation_key": self.operation_key,
            "operation_version": self.operation_version,
        }


@dataclass(frozen=True)
class TransformationOperationCapabilityDescriptor:
    """Declare immutable capabilities for one exact provider operation."""

    identity: ResolvedTransformationOperationIdentity
    supported_input_formats: tuple[str, ...]
    supported_output_formats: tuple[str, ...]
    is_idempotent: bool
    is_checkpoint_reuse_safe: bool
    is_checkpoint_reconciliation_safe: bool
    supports_cooperative_cancellation: bool
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate formats and recursively freeze safe descriptor metadata."""
        _validate_exact_formats(self.supported_input_formats, field_name="input formats")
        _validate_exact_formats(self.supported_output_formats, field_name="output formats")
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))

    def to_dict(self) -> dict[str, object]:
        """Return a canonical JSON-compatible descriptor payload."""
        return {
            **self.identity.to_dict(),
            "supported_input_formats": list(self.supported_input_formats),
            "supported_output_formats": list(self.supported_output_formats),
            "is_idempotent": self.is_idempotent,
            "is_checkpoint_reuse_safe": self.is_checkpoint_reuse_safe,
            "is_checkpoint_reconciliation_safe": self.is_checkpoint_reconciliation_safe,
            "supports_cooperative_cancellation": self.supports_cooperative_cancellation,
            "metadata": _thaw_mapping(self.metadata),
        }


@dataclass(frozen=True)
class TransformationProviderCapabilityDescriptor:
    """Declare immutable operations for one exact trusted provider version."""

    identity: TransformationProviderIdentity
    operations: tuple[TransformationOperationCapabilityDescriptor, ...]
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate operation ownership and recursively freeze provider metadata."""
        immutable_operations = tuple(self.operations)
        for operation in immutable_operations:
            if operation.identity.provider_identity != self.identity:
                raise ProviderContractInconsistencyError(
                    "Operation descriptor must belong to the provider descriptor identity.",
                )
        object.__setattr__(self, "operations", immutable_operations)
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))

    def to_dict(self) -> dict[str, object]:
        """Return a deterministic JSON-compatible provider descriptor payload."""
        return {
            **self.identity.to_dict(),
            "operations": [operation.to_dict() for operation in self.operations],
            "metadata": _thaw_mapping(self.metadata),
        }


@dataclass(frozen=True)
class TransformationProviderValidationRequest:
    """Contain immutable provider-specific semantic validation inputs."""

    identity: ResolvedTransformationOperationIdentity
    step_id: str
    configuration: Mapping[str, object]
    declared_input_formats: tuple[str, ...] = ()
    declared_output_formats: tuple[str, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Freeze provider validation values without interpreting them."""
        _validate_nonempty_exact_string(self.step_id, field_name="step ID")
        _validate_exact_formats(self.declared_input_formats, field_name="input formats")
        _validate_exact_formats(self.declared_output_formats, field_name="output formats")
        object.__setattr__(self, "configuration", _freeze_mapping(self.configuration))
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))


@dataclass(frozen=True)
class TransformationProviderValidationFinding:
    """Describe one sanitized provider-specific semantic validation finding."""

    identity: ResolvedTransformationOperationIdentity
    code: str
    path: str
    message: str
    severity: str = "error"

    def __post_init__(self) -> None:
        """Require stable non-empty finding fields."""
        _validate_nonempty_exact_string(self.code, field_name="finding code")
        _validate_nonempty_exact_string(self.path, field_name="finding path")
        _validate_nonempty_exact_string(self.message, field_name="finding message")
        _validate_nonempty_exact_string(self.severity, field_name="finding severity")

    def to_dict(self) -> dict[str, str]:
        """Return a JSON-serializable finding payload."""
        return {
            **self.identity.to_dict(),
            "code": self.code,
            "path": self.path,
            "severity": self.severity,
            "message": self.message,
        }


@dataclass(frozen=True)
class TransformationProviderValidationResult:
    """Represent deterministic provider-specific semantic validation findings."""

    is_valid: bool
    findings: tuple[TransformationProviderValidationFinding, ...] = ()

    def __post_init__(self) -> None:
        """Order findings deterministically and keep validity consistent."""
        ordered_findings = tuple(
            sorted(
                self.findings,
                key=lambda finding: (
                    finding.path,
                    finding.code,
                    finding.severity,
                    finding.message,
                    finding.identity,
                ),
            ),
        )
        object.__setattr__(self, "findings", ordered_findings)
        if self.is_valid == bool(ordered_findings):
            raise ProviderContractInconsistencyError(
                "Provider validation status must match whether findings are present.",
            )


@dataclass(frozen=True)
class TransformationIntegrityMetadata:
    """Describe provider-neutral integrity metadata for a data reference."""

    checksum_algorithm: str | None = None
    checksum_value: str | None = None
    size_bytes: int | None = None
    etag: str | None = None
    version_identifier: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate integrity pairs and freeze optional metadata."""
        if (self.checksum_algorithm is None) != (self.checksum_value is None):
            raise ProviderContractInconsistencyError(
                "Checksum algorithm and value must be supplied together.",
            )
        if self.size_bytes is not None and self.size_bytes < 0:
            raise ProviderContractInconsistencyError("Reference size cannot be negative.")
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))


@dataclass(frozen=True)
class TransformationProviderInputReference:
    """Represent one provider-neutral immutable input reference without payload rows."""

    logical_name: str
    materialized_reference: str
    data_format: str
    integrity: TransformationIntegrityMetadata = field(
        default_factory=TransformationIntegrityMetadata,
    )
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate exact reference fields and freeze safe metadata."""
        _validate_nonempty_exact_string(self.logical_name, field_name="input logical name")
        _validate_nonempty_exact_string(
            self.materialized_reference,
            field_name="input materialized reference",
        )
        _validate_nonempty_exact_string(self.data_format, field_name="input format")
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))


@dataclass(frozen=True)
class TransformationProviderExpectedOutput:
    """Declare one expected logical provider output without a materialized value."""

    logical_name: str
    accepted_formats: tuple[str, ...]
    is_temporary: bool = True

    def __post_init__(self) -> None:
        """Validate exact logical and format declarations."""
        _validate_nonempty_exact_string(self.logical_name, field_name="output logical name")
        _validate_exact_formats(self.accepted_formats, field_name="accepted output formats")


@dataclass(frozen=True)
class TransformationProviderOutputReference:
    """Represent one provider-neutral immutable materialized output reference."""

    logical_name: str
    materialized_reference: str
    data_format: str
    integrity: TransformationIntegrityMetadata = field(
        default_factory=TransformationIntegrityMetadata,
    )
    is_temporary: bool = True
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate exact output reference fields and freeze metadata."""
        _validate_nonempty_exact_string(self.logical_name, field_name="output logical name")
        _validate_nonempty_exact_string(
            self.materialized_reference,
            field_name="output materialized reference",
        )
        _validate_nonempty_exact_string(self.data_format, field_name="output format")
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))


@dataclass(frozen=True)
class TransformationProviderExecutionRequest:
    """Contain one immutable synchronous provider invocation request."""

    identity: ResolvedTransformationOperationIdentity
    run_id: str
    run_attempt_number: int
    run_attempt_token: str
    step_id: str
    step_index: int
    step_attempt_id: str
    attempt_idempotency_token: str
    configuration: Mapping[str, object]
    bound_parameters: Mapping[str, object]
    inputs: tuple[TransformationProviderInputReference, ...]
    expected_outputs: tuple[TransformationProviderExpectedOutput, ...]
    correlation_metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate request identifiers and recursively freeze configuration metadata."""
        _validate_nonempty_exact_string(self.run_id, field_name="run ID")
        if self.run_attempt_number < 1:
            raise ProviderContractInconsistencyError("Run attempt number must be positive.")
        _validate_nonempty_exact_string(self.run_attempt_token, field_name="run attempt token")
        _validate_nonempty_exact_string(self.step_id, field_name="step ID")
        if self.step_index < 0:
            raise ProviderContractInconsistencyError("Step index cannot be negative.")
        _validate_nonempty_exact_string(self.step_attempt_id, field_name="step attempt ID")
        _validate_nonempty_exact_string(
            self.attempt_idempotency_token,
            field_name="attempt idempotency token",
        )
        object.__setattr__(self, "configuration", _freeze_mapping(self.configuration))
        object.__setattr__(self, "bound_parameters", _freeze_mapping(self.bound_parameters))
        object.__setattr__(self, "inputs", tuple(self.inputs))
        object.__setattr__(self, "expected_outputs", tuple(self.expected_outputs))
        object.__setattr__(
            self,
            "correlation_metadata",
            _freeze_mapping(self.correlation_metadata),
        )


class ProviderSideEffectDisposition(StrEnum):
    """Describe what a provider knows about externally visible attempt effects."""

    NOT_STARTED = "NOT_STARTED"
    APPLIED = "APPLIED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class TransformationProviderExecutionResult:
    """Return immutable reference-only provider outputs and effect disposition."""

    identity: ResolvedTransformationOperationIdentity
    outputs: tuple[TransformationProviderOutputReference, ...]
    side_effect_disposition: ProviderSideEffectDisposition = ProviderSideEffectDisposition.APPLIED
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Recursively freeze safe provider result metadata."""
        if not isinstance(self.side_effect_disposition, ProviderSideEffectDisposition):
            raise ProviderContractInconsistencyError(
                "Provider result side-effect disposition is invalid.",
            )
        object.__setattr__(self, "outputs", tuple(self.outputs))
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))


@dataclass(frozen=True)
class TransformationProviderFailure:
    """Describe one typed sanitized provider execution failure."""

    identity: ResolvedTransformationOperationIdentity
    code: str
    message: str
    retryable: bool
    side_effect_disposition: ProviderSideEffectDisposition

    def __post_init__(self) -> None:
        """Require stable non-empty sanitized failure fields."""
        _validate_nonempty_exact_string(self.code, field_name="failure code")
        _validate_nonempty_exact_string(self.message, field_name="failure message")
        if not isinstance(self.side_effect_disposition, ProviderSideEffectDisposition):
            raise ProviderContractInconsistencyError(
                "Provider failure side-effect disposition is invalid.",
            )


class TransformationProviderExecutionError(Exception):
    """Raise an expected typed provider failure without arbitrary exception payloads."""

    def __init__(self, failure: TransformationProviderFailure) -> None:
        """Initialize the exception from an immutable typed failure.

        Args:
            failure: Sanitized provider failure contract.
        """
        super().__init__(failure.message)
        self.failure = failure


def validate_provider_key(value: object) -> None:
    """Validate one exact provider key without normalizing it."""
    _validate_identity_value(value, error_type=InvalidProviderKeyError, field_name="provider key")


def validate_provider_version(value: object) -> None:
    """Validate one exact provider version without normalizing it."""
    _validate_identity_value(
        value,
        error_type=InvalidProviderVersionError,
        field_name="provider version",
    )


def validate_operation_key(value: object) -> None:
    """Validate one exact operation key without normalizing it."""
    _validate_identity_value(value, error_type=InvalidOperationKeyError, field_name="operation key")


def validate_operation_version(value: object) -> None:
    """Validate one exact operation version without normalizing it."""
    _validate_identity_value(
        value,
        error_type=InvalidOperationVersionError,
        field_name="operation version",
    )


def _validate_identity_value(
    value: object,
    *,
    error_type: type[Exception],
    field_name: str,
) -> None:
    """Validate a nonempty exact identity value and reject surrounding whitespace."""
    if not isinstance(value, str) or not value or not value.strip() or value != value.strip():
        raise error_type(f"{field_name.capitalize()} must be an exact non-whitespace string.")


def _validate_nonempty_exact_string(value: object, *, field_name: str) -> None:
    """Validate an exact nonempty contract string without rewriting it."""
    if not isinstance(value, str) or not value or not value.strip() or value != value.strip():
        raise ProviderContractInconsistencyError(
            f"{field_name.capitalize()} must be an exact non-whitespace string.",
        )


def _validate_exact_formats(formats: tuple[str, ...], *, field_name: str) -> None:
    """Validate an immutable exact format collection without normalization."""
    if not isinstance(formats, tuple):
        raise ProviderContractInconsistencyError(f"{field_name.capitalize()} must be a tuple.")
    for data_format in formats:
        _validate_nonempty_exact_string(data_format, field_name=field_name)
    if len(set(formats)) != len(formats):
        raise ProviderContractInconsistencyError(
            f"{field_name.capitalize()} must not contain duplicates.",
        )


def _freeze_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    """Freeze one JSON-compatible mapping through the shared Story 1 utility."""
    try:
        frozen = freeze_json_value(value)
    except ImmutableJsonValueError as error:
        raise ProviderContractInconsistencyError(
            "Contract metadata must contain only JSON-compatible values.",
        ) from error
    if not isinstance(frozen, Mapping):
        raise ProviderContractInconsistencyError("Contract metadata must be a JSON object.")
    return cast(Mapping[str, object], frozen)


def _thaw_mapping(value: Mapping[str, object]) -> dict[str, object]:
    """Return a JSON-compatible mutable copy for descriptor serialization."""
    output: dict[str, object] = {}
    for key, nested_value in value.items():
        if isinstance(nested_value, Mapping):
            output[key] = _thaw_mapping(cast(Mapping[str, object], nested_value))
        elif isinstance(nested_value, tuple):
            output[key] = [_thaw_contract_value(item) for item in nested_value]
        else:
            output[key] = nested_value
    return output


def _thaw_contract_value(value: object) -> object:
    """Thaw one nested immutable contract value for JSON conversion."""
    if isinstance(value, Mapping):
        return _thaw_mapping(cast(Mapping[str, object], value))
    if isinstance(value, tuple):
        return [_thaw_contract_value(item) for item in value]
    return value
