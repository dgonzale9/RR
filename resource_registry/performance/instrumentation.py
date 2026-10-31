"""Measure Registry stages using fixed labels and observational logging only."""

from __future__ import annotations

import json
import logging
import math
import os
import random
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from functools import wraps
from inspect import signature
from threading import Lock
from time import perf_counter_ns
from typing import Literal, ParamSpec, TypeVar

from resource_registry.performance.payload_metrics import (
    RegistryPayloadMeasurements,
    bound_nonnegative_payload_value,
)
from resource_registry.performance.sql_profiling import (
    MAX_DB_ALIAS_COUNT,
    MAX_DB_QUERY_COUNT,
    RegistrySqlProfile,
    bound_nonnegative_sql_count,
    bound_nonnegative_sql_duration,
    start_registry_sql_profile,
)

_LOGGER = logging.getLogger("resource_registry.performance")
_GROUPED_OPERATION_NAMES = frozenset(
    {
        "resource_registry_inspect",
        "resource_registry_query",
        "resource_registry_manage_outputs",
        "resource_registry_manage_transformations",
    }
)
_ALLOWED_OPERATIONS = _GROUPED_OPERATION_NAMES
_CONFIGURATION_LOCK = Lock()
_CONFIGURATION: RegistryPerformanceConfiguration | None = None
_Parameters = ParamSpec("_Parameters")
_ReturnValue = TypeVar("_ReturnValue")


class RegistryPerformanceStage(StrEnum):
    """Name one fixed Registry stage without carrying request data."""

    DETERMINISTIC_ROUTING = "deterministic_routing"
    DETERMINISTIC_DECISION_EXECUTION = "deterministic_decision_execution"
    DETERMINISTIC_RETRY_FLOW = "deterministic_retry_flow"
    CANONICAL_BIF_DISPATCH_BRIDGE = "canonical_bif_dispatch_bridge"
    ASSISTANT_NAMESPACE_RESOLUTION = "assistant_namespace_resolution"
    SELECTION_ARGUMENT_RESOLUTION = "selection_argument_resolution"
    SELECTION_PROMPT_CONTEXT = "selection_prompt_context"
    GROUPED_CALL_TOTAL = "grouped_call_total"
    GROUPED_DISPATCH_BRIDGE = "grouped_dispatch_bridge"
    REGISTRY_TOOL_PUBLICATION = "registry_tool_publication"
    GROUPED_SCHEMA_BUILD = "grouped_schema_build"
    GROUPED_SCHEMA_VALIDATION = "grouped_schema_validation"
    GROUPED_RESULT_PROJECTION = "grouped_result_projection"
    NAMESPACE_ADMISSION = "namespace_admission"
    AUTHORIZATION_CONTEXT_BUILD = "authorization_context_build"
    CAPABILITY_RESOLUTION = "capability_resolution"


class RegistryPerformanceOutcome(StrEnum):
    """Describe the observed result without exposing exception details."""

    SUCCESS = "success"
    ROUTED = "routed"
    FALLBACK = "fallback"
    REJECTED = "rejected"
    FAILED = "failed"


@dataclass(frozen=True)
class RegistryPerformanceConfiguration:
    """Carry process-stable Registry measurement settings."""

    enabled: bool
    sample_rate: float
    sink: Literal["log", "noop"]
    sql_enabled: bool = False
    payload_enabled: bool = False


@dataclass(frozen=True)
class RegistryPerformanceMetric:
    """Carry one sanitized inclusive stage duration."""

    stage: RegistryPerformanceStage
    operation: str
    outcome: RegistryPerformanceOutcome
    duration_ms: float
    db_query_count: int | None = None
    db_duration_ms: float | None = None
    db_alias_count: int | None = None
    tool_count: int | None = None
    action_count: int | None = None
    schema_property_count: int | None = None
    description_char_count: int | None = None
    payload_bytes: int | None = None

    def to_safe_payload(self) -> dict[str, str | int | float | bool]:
        """Build a fixed metric payload without request or exception values.

        Returns:
            Sanitized fields for the Registry-local logger.
        """
        payload: dict[str, str | int | float | bool] = {
            "schema_version": 1,
            "event": "resource_registry.performance",
            "stage": self.stage.value,
            "operation": _bounded_operation_name(self.operation),
            "outcome": self.outcome.value,
            "duration_ms": self.duration_ms,
            "inclusive": True,
        }
        if (
            self.db_query_count is not None
            and self.db_duration_ms is not None
            and self.db_alias_count is not None
        ):
            payload["db_query_count"] = bound_nonnegative_sql_count(
                self.db_query_count, MAX_DB_QUERY_COUNT
            )
            payload["db_duration_ms"] = bound_nonnegative_sql_duration(self.db_duration_ms)
            payload["db_alias_count"] = bound_nonnegative_sql_count(
                self.db_alias_count, MAX_DB_ALIAS_COUNT
            )
        if (
            self.tool_count is not None
            and self.action_count is not None
            and self.schema_property_count is not None
            and self.description_char_count is not None
            and self.payload_bytes is not None
        ):
            payload["tool_count"] = bound_nonnegative_payload_value(self.tool_count)
            payload["action_count"] = bound_nonnegative_payload_value(self.action_count)
            payload["schema_property_count"] = bound_nonnegative_payload_value(
                self.schema_property_count
            )
            payload["description_char_count"] = bound_nonnegative_payload_value(
                self.description_char_count
            )
            payload["payload_bytes"] = bound_nonnegative_payload_value(self.payload_bytes)
        return payload


def _read_registry_performance_configuration() -> RegistryPerformanceConfiguration:
    """Read the Registry-only environment configuration.

    Returns:
        Immutable configuration read from Registry-specific environment variables.
    """
    enabled = os.environ.get("RESOURCE_REGISTRY_PERFORMANCE_ENABLED", "false").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    try:
        sample_rate = float(os.environ.get("RESOURCE_REGISTRY_PERFORMANCE_SAMPLE_RATE", "1"))
    except ValueError:
        sample_rate = 1.0
    if not math.isfinite(sample_rate) or not 0.0 <= sample_rate <= 1.0:
        sample_rate = 1.0
    raw_sink = os.environ.get("RESOURCE_REGISTRY_PERFORMANCE_SINK", "log")
    sink: Literal["log", "noop"] = "noop" if raw_sink == "noop" else "log"
    sql_enabled = os.environ.get("RESOURCE_REGISTRY_PERFORMANCE_SQL_ENABLED", "false").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    payload_enabled = os.environ.get(
        "RESOURCE_REGISTRY_PERFORMANCE_PAYLOAD_ENABLED", "false"
    ).lower() in {"1", "true", "yes", "on"}
    return RegistryPerformanceConfiguration(
        enabled=enabled,
        sample_rate=sample_rate,
        sink=sink,
        sql_enabled=sql_enabled,
        payload_enabled=payload_enabled,
    )


def get_registry_performance_configuration() -> RegistryPerformanceConfiguration:
    """Resolve immutable Registry measurement settings once per process.

    Returns:
        Process-stable configuration, initialized safely across concurrent calls.
    """
    global _CONFIGURATION  # noqa: PLW0603
    if _CONFIGURATION is None:
        with _CONFIGURATION_LOCK:
            if _CONFIGURATION is None:
                _CONFIGURATION = _read_registry_performance_configuration()
    return _CONFIGURATION


def reset_registry_performance_configuration_for_tests() -> None:
    """Clear process configuration after test environment changes.

    This function is for tests only; production configuration is stable after first use.
    """
    global _CONFIGURATION  # noqa: PLW0603
    with _CONFIGURATION_LOCK:
        _CONFIGURATION = None


def should_sample_registry_performance(
    sample_rate: float, *, sampler: Callable[[], float] | None = None
) -> bool:
    """Apply exact sampling boundaries and probabilistic intermediate sampling.

    Args:
        sample_rate: Configured fraction of Registry stage calls to record.
        sampler: Deterministic random draw provider for tests.

    Returns:
        Whether this stage call should be measured.
    """
    if sample_rate <= 0.0:
        return False
    if sample_rate >= 1.0:
        return True
    return (sampler or random.random)() < sample_rate


def emit_registry_performance_metric(metric: RegistryPerformanceMetric) -> None:
    """Send a sanitized metric through the Registry-local logger.

    Args:
        metric: Fixed-label metric with no request or exception data.
    """
    _LOGGER.info("%s", json.dumps(metric.to_safe_payload(), separators=(",", ":")))


def _bounded_operation_name(value: object) -> str:
    """Map untrusted operation labels to a fixed Registry vocabulary.

    Args:
        value: Candidate operation name from a function call.

    Returns:
        Known canonical/group name or the fixed unknown label.
    """
    if not isinstance(value, str):
        return "unknown"
    if value in _ALLOWED_OPERATIONS:
        return value
    if not value.startswith("resource_registry_"):
        return "unknown"
    # Import lazily: the Registry BIF package imports this module during Django startup.
    from resource_registry.bifs.manifest import RESOURCE_REGISTRY_BIF_MANIFEST  # noqa: PLC0415

    return value if value in RESOURCE_REGISTRY_BIF_MANIFEST else "unknown"


def _emit_measured_stage(
    *,
    stage: RegistryPerformanceStage,
    operation: str,
    outcome: RegistryPerformanceOutcome,
    start_ns: int,
    sql_profile: RegistrySqlProfile | None = None,
    payload_result: object | None = None,
    payload_measurement: Callable[[object], RegistryPayloadMeasurements] | None = None,
) -> None:
    """Emit one stage metric without affecting the measured call.

    Args:
        stage: Fixed measured Registry stage.
        operation: Already bounded operation label.
        outcome: Fixed outcome selected from the execution result.
        start_ns: Monotonic start timestamp for the current call only.
        sql_profile: Optional completed SQL profile for this call.
        payload_result: Unmodified result from a successful Registry publication.
        payload_measurement: Optional Registry-owned numeric extraction function.
    """
    try:
        duration_ms = (perf_counter_ns() - start_ns) / 1_000_000
        sql_measurements = sql_profile.snapshot() if sql_profile is not None else None
        payload_measurements = None
        if payload_measurement is not None:
            try:
                payload_measurements = payload_measurement(payload_result)
            except Exception:
                pass
        emit_registry_performance_metric(
            RegistryPerformanceMetric(
                stage,
                operation,
                outcome,
                duration_ms,
                db_query_count=(
                    sql_measurements.query_count if sql_measurements is not None else None
                ),
                db_duration_ms=(
                    sql_measurements.duration_ms if sql_measurements is not None else None
                ),
                db_alias_count=(
                    sql_measurements.alias_count if sql_measurements is not None else None
                ),
                tool_count=(
                    payload_measurements.tool_count if payload_measurements is not None else None
                ),
                action_count=(
                    payload_measurements.action_count if payload_measurements is not None else None
                ),
                schema_property_count=(
                    payload_measurements.schema_property_count
                    if payload_measurements is not None
                    else None
                ),
                description_char_count=(
                    payload_measurements.description_char_count
                    if payload_measurements is not None
                    else None
                ),
                payload_bytes=(
                    payload_measurements.payload_bytes if payload_measurements is not None else None
                ),
            )
        )
    except Exception:
        pass


def instrument_registry_function(
    stage: RegistryPerformanceStage,
    *,
    outcome_classifier: Callable[[object], RegistryPerformanceOutcome] | None = None,
    operation_parameter: str | None = None,
    payload_measurement: Callable[[object], RegistryPayloadMeasurements] | None = None,
) -> Callable[[Callable[_Parameters, _ReturnValue]], Callable[_Parameters, _ReturnValue]]:
    """Wrap a Registry function with best-effort inclusive timing.

    Args:
        stage: Fixed Registry stage label.
        outcome_classifier: Optional mapping from the return value to a bounded outcome.
        operation_parameter: Function argument containing a canonical/group operation name.
        payload_measurement: Optional numeric-only measurement of a successful result.

    Returns:
        A decorator preserving the wrapped call's return and exception behavior.
    """

    def decorate(
        function: Callable[_Parameters, _ReturnValue],
    ) -> Callable[_Parameters, _ReturnValue]:
        function_signature = signature(function) if operation_parameter else None

        @wraps(function)
        def measured(*args: _Parameters.args, **kwargs: _Parameters.kwargs) -> _ReturnValue:
            try:
                configuration = get_registry_performance_configuration()
            except Exception:
                return function(*args, **kwargs)
            if not configuration.enabled or configuration.sink == "noop":
                return function(*args, **kwargs)
            try:
                sampled = should_sample_registry_performance(configuration.sample_rate)
            except Exception:
                sampled = False
            if not sampled:
                return function(*args, **kwargs)

            operation = "unknown"
            if function_signature is not None and operation_parameter is not None:
                try:
                    arguments = function_signature.bind_partial(*args, **kwargs).arguments
                    operation_value = arguments.get(operation_parameter)
                    if not isinstance(operation_value, str):
                        operation_value = getattr(
                            operation_value,
                            "name",
                            getattr(operation_value, "function_name", None),
                        )
                    operation = _bounded_operation_name(operation_value)
                except Exception:
                    pass

            try:
                start_ns = perf_counter_ns()
            except Exception:
                return function(*args, **kwargs)
            sql_profile = None
            if configuration.sql_enabled:
                try:
                    sql_profile = start_registry_sql_profile()
                except Exception:
                    pass
            try:
                result = function(*args, **kwargs)
            except BaseException:
                if sql_profile is not None:
                    sql_profile.stop()
                _emit_measured_stage(
                    stage=stage,
                    operation=operation,
                    outcome=RegistryPerformanceOutcome.FAILED,
                    start_ns=start_ns,
                    sql_profile=sql_profile,
                )
                raise

            if sql_profile is not None:
                sql_profile.stop()

            try:
                outcome = (
                    outcome_classifier(result)
                    if outcome_classifier is not None
                    else RegistryPerformanceOutcome.SUCCESS
                )
                if not isinstance(outcome, RegistryPerformanceOutcome):
                    outcome = RegistryPerformanceOutcome.FAILED
            except Exception:
                outcome = RegistryPerformanceOutcome.FAILED
            _emit_measured_stage(
                stage=stage,
                operation=operation,
                outcome=outcome,
                start_ns=start_ns,
                sql_profile=sql_profile,
                payload_result=result,
                payload_measurement=(
                    payload_measurement if configuration.payload_enabled else None
                ),
            )
            return result

        return measured

    return decorate
