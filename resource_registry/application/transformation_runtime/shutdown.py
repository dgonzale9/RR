from __future__ import annotations

import logging
from threading import Event

from resource_registry.application.transformation_runtime.contracts import (
    RuntimeShutdownResult,
    TransformationRuntime,
    TransformationRuntimeLifecycleStatus,
)


class RuntimeShutdownCoordinator:
    """Coordinate best-effort shutdown without mutating the runtime graph."""

    def shutdown(
        self,
        *,
        runtime: TransformationRuntime,
        stop_event: Event,
    ) -> RuntimeShutdownResult:
        """Stop new polling and flush configured operational logging handlers."""
        _ = runtime
        errors: list[str] = []
        stop_event.set()
        logger = logging.getLogger("transformation-operations")
        for handler in tuple(logger.handlers):
            try:
                handler.flush()
            except Exception:
                errors.append("operational_handler_flush_failed")
        return RuntimeShutdownResult(
            status=TransformationRuntimeLifecycleStatus.STOPPED,
            errors=tuple(errors),
        )
