from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import ClassVar

from resource_registry.application.contracts import RuntimeUnavailableError
from resource_registry.application.read import (
    TransformationDiagnosticsReadService,
    TransformationOutputReadService,
    TransformationRunReadService,
    TransformationStepReadService,
)
from resource_registry.application.services import TransformationExecutionApplicationService


@dataclass(frozen=True, kw_only=True)
class TransformationRestApplicationServices:
    """Contain the complete immutable application dependency set used by REST views."""

    execution: TransformationExecutionApplicationService
    runs: TransformationRunReadService
    steps: TransformationStepReadService
    outputs: TransformationOutputReadService
    diagnostics: TransformationDiagnosticsReadService


class _TransformationRestServiceState:
    """Hold the write-once transport composition reference."""

    services: ClassVar[TransformationRestApplicationServices | None] = None
    lock: ClassVar[Lock] = Lock()


def configure_transformation_rest_application_services(
    *,
    services: TransformationRestApplicationServices,
) -> None:
    """Install the application services composed by the authoritative composition boundary.

    Args:
        services: Fully composed immutable application service graph.

    Raises:
        RuntimeError: If a different service graph was already installed.
    """
    with _TransformationRestServiceState.lock:
        configured = _TransformationRestServiceState.services
        if configured is not None and configured is not services:
            raise RuntimeError("Transformation REST application services are already configured.")
        _TransformationRestServiceState.services = services


def get_transformation_rest_application_services(
    *,
    correlation_id: str | None,
) -> TransformationRestApplicationServices:
    """Return configured services or fail closed without constructing runtime dependencies."""
    services = _TransformationRestServiceState.services
    if services is None:
        raise RuntimeUnavailableError(
            code="transformation_application_services_unavailable",
            public_message="Transformation services are unavailable.",
            retryable=True,
            correlation_id=correlation_id,
        )
    return services
