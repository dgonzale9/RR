from __future__ import annotations

from typing import Protocol, runtime_checkable

from resource_registry.transformations.providers.contracts import (
    TransformationProviderCapabilityDescriptor,
    TransformationProviderExecutionRequest,
    TransformationProviderExecutionResult,
    TransformationProviderIdentity,
    TransformationProviderValidationRequest,
    TransformationProviderValidationResult,
)


class TransformationCancellationSignal(Protocol):
    """Expose cooperative cancellation intent without owning lifecycle transitions."""

    def is_cancellation_requested(self) -> bool:
        """Return whether the provider should stop at its next safe boundary."""


class TransformationDeadlineSignal(Protocol):
    """Expose cooperative deadline state without implementing hard interruption."""

    def has_deadline_expired(self) -> bool:
        """Return whether the cooperative execution deadline has expired."""

    def get_remaining_seconds(self) -> float | None:
        """Return remaining seconds, or None when no deadline is configured."""


class TransformationRuntimeSecretAccess(Protocol):
    """Resolve a future approved opaque secret reference only at runtime."""

    def get_secret(self, *, opaque_reference: object) -> str:
        """Return a runtime secret without defining reference syntax or backend behavior."""


class TransformationProviderRuntimeContext(Protocol):
    """Provide narrow runtime-only cooperative and secret-access ports."""

    @property
    def cancellation(self) -> TransformationCancellationSignal:
        """Return the cooperative cancellation signal."""

    @property
    def deadline(self) -> TransformationDeadlineSignal:
        """Return the cooperative deadline signal."""

    @property
    def secrets(self) -> TransformationRuntimeSecretAccess:
        """Return the runtime-only secret access port."""


@runtime_checkable
class TransformationOperationProvider(Protocol):
    """Define one trusted synchronous transformation operation provider."""

    @property
    def provider_identity(self) -> TransformationProviderIdentity:
        """Return the exact provider key and version."""

    @property
    def capability_descriptor(self) -> TransformationProviderCapabilityDescriptor:
        """Return immutable provider and operation capabilities."""

    def validate_operation(
        self,
        *,
        request: TransformationProviderValidationRequest,
    ) -> TransformationProviderValidationResult:
        """Validate provider-specific immutable operation configuration."""

    def execute(
        self,
        *,
        request: TransformationProviderExecutionRequest,
        runtime_context: TransformationProviderRuntimeContext,
    ) -> TransformationProviderExecutionResult:
        """Synchronously execute one operation using reference-only inputs and outputs."""
