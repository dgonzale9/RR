from __future__ import annotations

from enum import StrEnum


class RunStatus(StrEnum):
    """Expose stable public transformation run states."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    OUTPUT_PENDING = "OUTPUT_PENDING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class OutputStatus(StrEnum):
    """Expose stable public transformation output states."""

    PENDING = "PENDING"
    MATERIALIZING = "MATERIALIZING"
    REGISTERED = "REGISTERED"
    FAILED = "FAILED"


class OutputKind(StrEnum):
    """Identify the public ownership form of a transformation output."""

    STANDALONE = "STANDALONE"
    DATASET = "DATASET"
    DATASET_VERSION = "DATASET_VERSION"


class StepStatus(StrEnum):
    """Expose stable public transformation step-attempt states."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ExecutionState(StrEnum):
    """Expose stable public asynchronous execution phases."""

    ACCEPTED = "ACCEPTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class HealthState(StrEnum):
    """Expose stable public health states."""

    READY = "READY"
    DEGRADED = "DEGRADED"
    NOT_READY = "NOT_READY"


class ErrorCategory(StrEnum):
    """Classify stable public application failures."""

    VALIDATION = "VALIDATION"
    AUTHORIZATION = "AUTHORIZATION"
    CONFLICT = "CONFLICT"
    NOT_FOUND = "NOT_FOUND"
    RUNTIME_UNAVAILABLE = "RUNTIME_UNAVAILABLE"
    OPERATION_FAILED = "OPERATION_FAILED"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    INTERNAL = "INTERNAL"
