"""Define pure immutable execution request and input-reference contracts."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TypeAlias

from resource_registry.capability_definitions import CapabilityContractRef, CapabilityExecutionMode
from resource_registry.registry_core import (
    ArtifactKey,
    OpaqueReference,
    ResourceKey,
    ResourceVersionKey,
)

from .errors import ExecutionRequestValidationError

__all__ = ("ExecutionInputReference", "ExecutionRequest", "ExecutionRequestRef")

_MAXIMUM_REFERENCE_LENGTH = 512
_MAXIMUM_INPUT_NAME_LENGTH = 128
_MAXIMUM_ROLE_LENGTH = 128

ExecutionInputTarget: TypeAlias = ResourceKey | ResourceVersionKey | ArtifactKey | OpaqueReference


@dataclass(frozen=True, slots=True)
class ExecutionRequestRef:
    """Identify one opaque execution request without admitting an execution instance.

    Args:
        value: Exact nonblank Execution-owned request identity.

    Raises:
        ExecutionRequestValidationError: If the reference identity is malformed.
    """

    value: str

    def __post_init__(self) -> None:
        """Validate the opaque request identity without allocating or persisting it.

        Raises:
            ExecutionRequestValidationError: If the reference identity is malformed.
        """
        if not _is_non_blank_string(self.value, _MAXIMUM_REFERENCE_LENGTH):
            raise ExecutionRequestValidationError("invalid execution request reference")


@dataclass(frozen=True, slots=True)
class ExecutionInputReference:
    """Bind one named exact input reference to an immutable execution request.

    Args:
        name: Unique caller-declared input name within one request.
        target: Exact owner-issued resource, version, artifact, or qualified external reference.
        role: Optional bounded descriptive role without executable meaning.

    Raises:
        ExecutionRequestValidationError: If the input reference is malformed.
    """

    name: str
    target: ExecutionInputTarget
    role: str | None = None

    def __post_init__(self) -> None:
        """Validate exact input evidence without resolving or loading owner data.

        Raises:
            ExecutionRequestValidationError: If the input reference is malformed.
        """
        if not _is_non_blank_string(self.name, _MAXIMUM_INPUT_NAME_LENGTH):
            raise ExecutionRequestValidationError("invalid execution input name")
        if not isinstance(
            self.target, (ResourceKey, ResourceVersionKey, ArtifactKey, OpaqueReference)
        ):
            raise ExecutionRequestValidationError("invalid execution input target")
        if self.role is not None and not _is_non_blank_string(self.role, _MAXIMUM_ROLE_LENGTH):
            raise ExecutionRequestValidationError("invalid execution input role")


@dataclass(frozen=True, slots=True)
class ExecutionRequest:
    """Carry immutable exact execution intent without authority or routing behavior.

    Args:
        request_ref: Opaque execution request identity.
        contract_ref: Exact capability contract requested for later admission.
        inputs: Ordered, uniquely named exact input references.
        requested_execution_mode: Optional descriptive execution mode requested by the caller.
        requested_deadline_at: Optional UTC deadline requested by the caller.

    Raises:
        ExecutionRequestValidationError: If any request field is malformed or duplicated.
    """

    request_ref: ExecutionRequestRef
    contract_ref: CapabilityContractRef
    inputs: tuple[ExecutionInputReference, ...] = ()
    requested_execution_mode: CapabilityExecutionMode | None = None
    requested_deadline_at: datetime | None = None

    def __post_init__(self) -> None:
        """Validate exact untrusted request intent without admission or execution.

        Raises:
            ExecutionRequestValidationError: If the request fields are malformed or duplicated.
        """
        if not isinstance(self.request_ref, ExecutionRequestRef):
            raise ExecutionRequestValidationError("invalid execution request reference")
        if not isinstance(self.contract_ref, CapabilityContractRef):
            raise ExecutionRequestValidationError("invalid execution request contract")
        if type(self.inputs) is not tuple:
            raise ExecutionRequestValidationError("invalid execution request inputs")

        input_names: set[str] = set()
        for input_reference in self.inputs:
            if not isinstance(input_reference, ExecutionInputReference):
                raise ExecutionRequestValidationError("invalid execution request input")
            if input_reference.name in input_names:
                raise ExecutionRequestValidationError("duplicate execution input name")
            input_names.add(input_reference.name)

        if self.requested_execution_mode is not None and not isinstance(
            self.requested_execution_mode, CapabilityExecutionMode
        ):
            raise ExecutionRequestValidationError("invalid requested execution mode")
        if self.requested_deadline_at is not None and (
            not isinstance(self.requested_deadline_at, datetime)
            or self.requested_deadline_at.tzinfo is not UTC
        ):
            raise ExecutionRequestValidationError("invalid requested execution deadline")

    def to_canonical_json(self) -> str:
        """Serialize the immutable request into deterministic canonical JSON.

        Returns:
            The request's sorted, compact JSON representation.
        """
        return json.dumps(
            _serialize_execution_request(self),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )


@dataclass(frozen=True, slots=True)
class ExecutionPlanRef:
    """Identify one exact immutable execution plan.

    Args:
        value: Opaque plan identity.
        checksum: Canonical checksum evidence for the plan.
    """

    value: str
    checksum: str

    def __post_init__(self) -> None:
        """Validate plan identity and checksum without routing or persistence."""
        if not _is_non_blank_string(self.value, _MAXIMUM_REFERENCE_LENGTH):
            raise ExecutionRequestValidationError("invalid execution plan reference")
        if not _is_non_blank_string(self.checksum, _MAXIMUM_REFERENCE_LENGTH):
            raise ExecutionRequestValidationError("invalid execution plan checksum")


@dataclass(frozen=True, slots=True)
class ExecutionStep:
    """Describe one exact non-executable plan step.

    Args:
        step_id: Stable step identity unique within a plan.
        contract_ref: Exact capability contract for the step.
        input_names: Ordered names of declared request inputs consumed by the step.
    """

    step_id: str
    contract_ref: CapabilityContractRef
    input_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Validate step structure without binding an implementation or provider."""
        if not _is_non_blank_string(self.step_id, _MAXIMUM_INPUT_NAME_LENGTH):
            raise ExecutionRequestValidationError("invalid execution step id")
        if not isinstance(self.contract_ref, CapabilityContractRef):
            raise ExecutionRequestValidationError("invalid execution step contract")
        if type(self.input_names) is not tuple or any(
            not _is_non_blank_string(name, _MAXIMUM_INPUT_NAME_LENGTH) for name in self.input_names
        ):
            raise ExecutionRequestValidationError("invalid execution step inputs")
        if len(set(self.input_names)) != len(self.input_names):
            raise ExecutionRequestValidationError("duplicate execution step input")


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    """Carry an immutable exact execution plan without routing evidence.

    Args:
        plan_ref: Exact plan identity and checksum.
        request_ref: Exact request from which the plan was derived.
        steps: Ordered uniquely identified declarative steps.
    """

    plan_ref: ExecutionPlanRef
    request_ref: ExecutionRequestRef
    steps: tuple[ExecutionStep, ...]

    def __post_init__(self) -> None:
        """Validate immutable plan structure without creating an execution instance."""
        if not isinstance(self.plan_ref, ExecutionPlanRef):
            raise ExecutionRequestValidationError("invalid execution plan reference")
        if not isinstance(self.request_ref, ExecutionRequestRef):
            raise ExecutionRequestValidationError("invalid execution plan request reference")
        if type(self.steps) is not tuple or not self.steps:
            raise ExecutionRequestValidationError("invalid execution plan steps")
        if any(not isinstance(step, ExecutionStep) for step in self.steps):
            raise ExecutionRequestValidationError("invalid execution plan step")
        if len({step.step_id for step in self.steps}) != len(self.steps):
            raise ExecutionRequestValidationError("duplicate execution plan step")


class ExecutionResultStatus(StrEnum):
    """Classify one immutable normalized execution result."""

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILURE = "failure"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """Carry an immutable normalized result without lifecycle mutation.

    Args:
        plan_ref: Exact plan that produced the result.
        status: Normalized logical result status.
        output_references: Exact owner-issued output evidence references.
    """

    plan_ref: ExecutionPlanRef
    status: ExecutionResultStatus
    output_references: tuple[ExecutionInputTarget, ...] = ()

    def __post_init__(self) -> None:
        """Validate result evidence without registering an owner output."""
        if not isinstance(self.plan_ref, ExecutionPlanRef):
            raise ExecutionRequestValidationError("invalid execution result plan reference")
        if not isinstance(self.status, ExecutionResultStatus):
            raise ExecutionRequestValidationError("invalid execution result status")
        if type(self.output_references) is not tuple or any(
            not isinstance(value, (ResourceKey, ResourceVersionKey, ArtifactKey, OpaqueReference))
            for value in self.output_references
        ):
            raise ExecutionRequestValidationError("invalid execution result outputs")


@dataclass(frozen=True, slots=True)
class ExecutionCheckpoint:
    """Carry immutable exact step-completion evidence for a future resume boundary.

    Args:
        plan_ref: Exact plan governing the completed step.
        step_id: Exact completed step identity.
        output_references: Exact output evidence available for later verification.
    """

    plan_ref: ExecutionPlanRef
    step_id: str
    output_references: tuple[ExecutionInputTarget, ...] = ()

    def __post_init__(self) -> None:
        """Validate checkpoint evidence without deciding resume safety."""
        if not isinstance(self.plan_ref, ExecutionPlanRef):
            raise ExecutionRequestValidationError("invalid execution checkpoint plan reference")
        if not _is_non_blank_string(self.step_id, _MAXIMUM_INPUT_NAME_LENGTH):
            raise ExecutionRequestValidationError("invalid execution checkpoint step")
        if type(self.output_references) is not tuple or any(
            not isinstance(value, (ResourceKey, ResourceVersionKey, ArtifactKey, OpaqueReference))
            for value in self.output_references
        ):
            raise ExecutionRequestValidationError("invalid execution checkpoint outputs")


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    """Carry immutable non-authoritative execution evidence.

    Args:
        request_ref: Exact submitted request identity.
        plan_ref: Exact plan identity and checksum.
        contract_ref: Exact requested capability contract.
        created_at: UTC time at which context evidence was formed.
    """

    request_ref: ExecutionRequestRef
    plan_ref: ExecutionPlanRef
    contract_ref: CapabilityContractRef
    created_at: datetime

    def __post_init__(self) -> None:
        """Validate context evidence without treating it as authorization."""
        if not isinstance(self.request_ref, ExecutionRequestRef):
            raise ExecutionRequestValidationError("invalid execution context request reference")
        if not isinstance(self.plan_ref, ExecutionPlanRef):
            raise ExecutionRequestValidationError("invalid execution context plan reference")
        if not isinstance(self.contract_ref, CapabilityContractRef):
            raise ExecutionRequestValidationError("invalid execution context contract")
        if not isinstance(self.created_at, datetime) or self.created_at.tzinfo is not UTC:
            raise ExecutionRequestValidationError("invalid execution context timestamp")


def _is_non_blank_string(candidate_value: object, maximum_length: int) -> bool:
    """Determine whether a value is a bounded nonblank plain string.

    Args:
        candidate_value: Value to validate.
        maximum_length: Maximum allowed character length.

    Returns:
        Whether the value is an accepted bounded string.
    """
    return (
        type(candidate_value) is str
        and bool(candidate_value.strip())
        and len(candidate_value) <= maximum_length
    )


def _serialize_execution_request(request: ExecutionRequest) -> Mapping[str, object]:
    """Build the canonical JSON-compatible representation of one request.

    Args:
        request: Exact immutable request to serialize.

    Returns:
        A JSON-compatible mapping with no authority or provider details.
    """
    return {
        "contract": {
            "key": request.contract_ref.capability_key.value,
            "version": str(request.contract_ref.capability_version),
        },
        "inputs": tuple(
            _serialize_input_reference(input_reference) for input_reference in request.inputs
        ),
        "request_ref": request.request_ref.value,
        "requested_deadline_at": (
            request.requested_deadline_at.isoformat()
            if request.requested_deadline_at is not None
            else None
        ),
        "requested_execution_mode": (
            request.requested_execution_mode.value
            if request.requested_execution_mode is not None
            else None
        ),
    }


def _serialize_input_reference(input_reference: ExecutionInputReference) -> Mapping[str, object]:
    """Build the canonical JSON-compatible representation of one input reference.

    Args:
        input_reference: Exact immutable input reference to serialize.

    Returns:
        A JSON-compatible input-reference mapping.
    """
    return {
        "name": input_reference.name,
        "role": input_reference.role,
        "target": _serialize_input_target(input_reference.target),
    }


def _serialize_input_target(target: ExecutionInputTarget) -> Mapping[str, object]:
    """Build the canonical JSON-compatible representation of one exact input target.

    Args:
        target: Exact owner-issued input target.

    Returns:
        A JSON-compatible target mapping.
    """
    if isinstance(target, ResourceVersionKey):
        return {
            "kind": "resource_version",
            "resource": _serialize_resource_key(target.resource),
            "version_id": target.version_id,
        }
    if isinstance(target, ArtifactKey):
        return {
            "artifact_id": target.artifact_id,
            "kind": "artifact",
            "resource": _serialize_resource_key(target.resource),
            "version": (
                _serialize_input_target(target.version) if target.version is not None else None
            ),
        }
    if isinstance(target, ResourceKey):
        return {"kind": "resource", **_serialize_resource_key(target)}
    return _serialize_opaque_reference(target)


def _serialize_resource_key(resource_key: ResourceKey) -> Mapping[str, str]:
    """Build the canonical JSON-compatible representation of one resource key.

    Args:
        resource_key: Exact immutable resource identity.

    Returns:
        A JSON-compatible resource-key mapping.
    """
    return {
        "namespace": str(resource_key.namespace.value),
        "resource_id": resource_key.resource_id,
        "resource_type": resource_key.resource_type.value,
    }


def _serialize_opaque_reference(reference: OpaqueReference) -> Mapping[str, object]:
    """Build the canonical JSON-compatible representation of an external reference.

    Args:
        reference: Exact qualified owner-issued external reference.

    Returns:
        A JSON-compatible opaque-reference mapping.
    """
    return {
        "kind": "opaque",
        "owner": {
            "bounded_context_identifier": reference.owner.bounded_context_identifier,
            "classification": reference.owner.ownership_classification.value,
        },
        "parent": (
            _serialize_opaque_reference(reference.parent) if reference.parent is not None else None
        ),
        "value": reference.value,
    }
