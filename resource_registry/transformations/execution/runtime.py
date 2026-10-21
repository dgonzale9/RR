from __future__ import annotations

import time
from dataclasses import dataclass

from resource_registry.transformations.providers import (
    TransformationCancellationSignal,
    TransformationDeadlineSignal,
    TransformationProviderRuntimeContext,
    TransformationRuntimeSecretAccess,
)


class TransformationSecretAccessDeniedError(RuntimeError):
    """Raise because Story 5 has no approved secret grammar or backend."""


class DenyingTransformationRuntimeSecretAccess:
    """Deny all runtime secret access in Story 5."""

    def get_secret(self, *, opaque_reference: object) -> str:
        """Reject every opaque reference without interpreting it."""
        _ = opaque_reference
        raise TransformationSecretAccessDeniedError(
            "Transformation runtime secret access is not supported.",
        )


@dataclass(frozen=True)
class MonotonicTransformationDeadlineSignal:
    """Expose one cooperative monotonic attempt deadline."""

    deadline_monotonic: float | None = None

    def has_deadline_expired(self) -> bool:
        """Return whether the monotonic deadline has elapsed."""
        return self.deadline_monotonic is not None and time.monotonic() >= self.deadline_monotonic

    def get_remaining_seconds(self) -> float | None:
        """Return non-negative remaining time."""
        if self.deadline_monotonic is None:
            return None
        return max(self.deadline_monotonic - time.monotonic(), 0.0)


@dataclass(frozen=True)
class DefaultTransformationProviderRuntimeContext:
    """Compose cooperative runtime-only ports for provider invocation."""

    cancellation_signal: TransformationCancellationSignal
    deadline_signal: TransformationDeadlineSignal
    secret_access: TransformationRuntimeSecretAccess

    @property
    def cancellation(self) -> TransformationCancellationSignal:
        return self.cancellation_signal

    @property
    def deadline(self) -> TransformationDeadlineSignal:
        return self.deadline_signal

    @property
    def secrets(self) -> TransformationRuntimeSecretAccess:
        return self.secret_access


def assert_runtime_context_contract(
    context: TransformationProviderRuntimeContext,
) -> None:
    """Assert the runtime context exposes all required narrow ports."""
    _ = context.cancellation, context.deadline, context.secrets
