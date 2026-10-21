from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import ClassVar, Protocol

from resource_registry.application.contracts.base import ApplicationContract
from resource_registry.application.contracts.enums import ErrorCategory
from resource_registry.application.contracts.immutable import (
    FrozenJsonMapping,
    freeze_json_mapping,
)

_STABLE_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_UNSAFE_METADATA_KEY_PARTS = frozenset(
    {
        "checkpoint",
        "execution_plan",
        "internal_id",
        "password",
        "payload",
        "provider_message",
        "runtime_exception",
        "secret",
        "signed_url",
        "sql",
        "stack",
        "storage_path",
        "token",
        "traceback",
    },
)


@dataclass(frozen=True, kw_only=True)
class ApplicationError(ApplicationContract, Exception):
    """Represent one stable sanitized transport-neutral application failure."""

    category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    code: str
    public_message: str
    retryable: bool = False
    correlation_id: str | None = None
    metadata: FrozenJsonMapping = field(default_factory=FrozenJsonMapping)

    def __init__(
        self,
        *,
        code: str,
        public_message: str,
        retryable: bool = False,
        correlation_id: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        """Validate the stable code and recursively freeze explicitly safe metadata."""
        if not _STABLE_CODE_PATTERN.fullmatch(code):
            raise ValueError("Application error code must be stable lower snake case.")
        if not public_message.strip():
            raise ValueError("Application error public message cannot be empty.")
        frozen_metadata = freeze_json_mapping(metadata)
        _validate_safe_metadata(frozen_metadata)
        object.__setattr__(self, "contract_version", self.contract_version)
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "public_message", public_message)
        object.__setattr__(self, "retryable", retryable)
        object.__setattr__(self, "correlation_id", correlation_id)
        object.__setattr__(self, "metadata", frozen_metadata)
        Exception.__init__(self, public_message)

    def __str__(self) -> str:
        return self.public_message


@dataclass(frozen=True, kw_only=True, init=False)
class ValidationError(ApplicationError):
    """Report a stable public request validation failure."""

    category = ErrorCategory.VALIDATION


@dataclass(frozen=True, kw_only=True, init=False)
class AuthorizationError(ApplicationError):
    """Report a stable public authorization failure."""

    category = ErrorCategory.AUTHORIZATION


@dataclass(frozen=True, kw_only=True, init=False)
class ConflictError(ApplicationError):
    """Report a stable public state or idempotency conflict."""

    category = ErrorCategory.CONFLICT


@dataclass(frozen=True, kw_only=True, init=False)
class NotFoundError(ApplicationError):
    """Report a stable public resource visibility failure."""

    category = ErrorCategory.NOT_FOUND


@dataclass(frozen=True, kw_only=True, init=False)
class RuntimeUnavailableError(ApplicationError):
    """Report that the internal runtime cannot safely accept the operation."""

    category = ErrorCategory.RUNTIME_UNAVAILABLE


@dataclass(frozen=True, kw_only=True, init=False)
class OperationFailedError(ApplicationError):
    """Report a sanitized terminal operation failure."""

    category = ErrorCategory.OPERATION_FAILED


@dataclass(frozen=True, kw_only=True, init=False)
class TimeoutError(ApplicationError):
    """Report a stable application timeout without leaking runtime details."""

    category = ErrorCategory.TIMEOUT


@dataclass(frozen=True, kw_only=True, init=False)
class CancelledError(ApplicationError):
    """Report a stable public cancellation outcome."""

    category = ErrorCategory.CANCELLED


@dataclass(frozen=True, kw_only=True, init=False)
class InternalError(ApplicationError):
    """Report a fail-closed internal failure without its implementation details."""

    category = ErrorCategory.INTERNAL


class ApplicationErrorTranslator(Protocol):
    """Define future ownership for mapping an internal exception into an application error."""

    def translate(
        self,
        *,
        error: Exception,
        correlation_id: str | None,
    ) -> ApplicationError:
        """Translate an internal error without exposing the source exception."""


def _validate_safe_metadata(metadata: Mapping[str, object]) -> None:
    for key, value in metadata.items():
        normalized_key = key.lower()
        if any(part in normalized_key for part in _UNSAFE_METADATA_KEY_PARTS):
            raise ValueError("Application error metadata contains an unsafe key.")
        _validate_safe_metadata_value(value)


def _validate_safe_metadata_value(value: object) -> None:
    if isinstance(value, Mapping):
        _validate_safe_metadata(value)
    elif isinstance(value, tuple):
        for item in value:
            _validate_safe_metadata_value(item)
