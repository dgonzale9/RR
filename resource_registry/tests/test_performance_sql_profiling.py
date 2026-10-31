"""Verify opt-in SQL profiling on existing Registry performance stages."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from threading import Barrier, local
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.db import DatabaseError, connection
from django.test import override_settings

from resource_registry.authorization.exceptions import (
    RegistryAuthorizationAuthenticationRequiredError,
)
from resource_registry.authorization.registry_authorization_context_service import (
    RegistryAuthorizationContextService,
)
from resource_registry.bifs.grouped_tools import (
    FunctionToolDefinition,
    is_valid_grouped_registry_arguments,
)
from resource_registry.performance import instrumentation, sql_profiling
from resource_registry.performance.instrumentation import (
    RegistryPerformanceMetric,
    RegistryPerformanceOutcome,
    RegistryPerformanceStage,
    get_registry_performance_configuration,
    instrument_registry_function,
    reset_registry_performance_configuration_for_tests,
)


class _FakeDatabaseConnection:
    """Mimic Django's scoped execution-wrapper behavior without a database."""

    def __init__(self) -> None:
        self._wrappers: list[Callable[..., int]] = []

    @contextmanager
    def execute_wrapper(self, wrapper: Callable[..., int]) -> Iterator[None]:
        """Install one wrapper until the measured call exits."""
        self._wrappers.append(wrapper)
        try:
            yield
        finally:
            self._wrappers.pop()

    def execute(self, failure: BaseException | None = None) -> int:
        """Execute one fake SQL command through all installed wrappers."""

        def execute_command(
            sql: str, parameters: object, many: bool, context: Mapping[str, object]
        ) -> int:
            _ = (sql, parameters, many, context)
            if failure is not None:
                raise failure
            return 1

        execute: Callable[[str, object, bool, Mapping[str, object]], int] = execute_command
        for wrapper in self._wrappers:
            next_execute = execute

            def wrapped_execute(
                sql: str,
                parameters: object,
                many: bool,
                context: Mapping[str, object],
                *,
                current_wrapper: Callable[..., int] = wrapper,
                current_execute: Callable[
                    [str, object, bool, Mapping[str, object]], int
                ] = next_execute,
            ) -> int:
                return current_wrapper(current_execute, sql, parameters, many, context)

            execute = wrapped_execute
        return execute("SELECT private_secret", ("credential",), False, {})

    def has_no_wrappers(self) -> bool:
        """Report whether all scoped wrappers were removed."""
        return not self._wrappers


class _ThreadLocalConnections:
    """Give each test worker its own fake Django-style connection."""

    def __init__(self) -> None:
        self._local: local = local()

    def all(self) -> list[_FakeDatabaseConnection]:
        """Return the connection for the current worker thread."""
        if not hasattr(self._local, "connection"):
            self._local.connection = _FakeDatabaseConnection()
        return [self._local.connection]


@pytest.fixture(autouse=True)
def reset_performance_configuration() -> Iterator[None]:
    """Reset cached Registry configuration around each SQL profiling test."""
    reset_registry_performance_configuration_for_tests()
    yield
    reset_registry_performance_configuration_for_tests()


def test_sql_profiling_is_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Require a separate SQL opt-in even when stage timing is enabled."""
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_ENABLED", "true")
    monkeypatch.delenv("RESOURCE_REGISTRY_PERFORMANCE_SQL_ENABLED", raising=False)
    assert get_registry_performance_configuration().sql_enabled is False


def test_sql_configuration_is_immutable_and_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve SQL opt-in once with the existing immutable E1.1 configuration."""
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_SQL_ENABLED", "true")
    first = get_registry_performance_configuration()
    assert first.sql_enabled is True
    with pytest.raises(FrozenInstanceError):
        first.sql_enabled = False
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_SQL_ENABLED", "false")
    assert get_registry_performance_configuration() is first
    reset_registry_performance_configuration_for_tests()
    assert get_registry_performance_configuration().sql_enabled is False


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [("true", True), ("yes", True), ("1", True), ("on", True), ("garbage", False)],
)
def test_sql_configuration_parses_only_known_true_values(
    monkeypatch: pytest.MonkeyPatch, raw_value: str, expected: bool
) -> None:
    """Keep unknown values disabled without logging their contents."""
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_SQL_ENABLED", raw_value)
    assert get_registry_performance_configuration().sql_enabled is expected


@pytest.mark.parametrize(
    ("performance_enabled", "sample_rate", "sink"),
    [("false", "1", "log"), ("true", "0", "log"), ("true", "1", "noop")],
)
def test_inactive_performance_paths_do_not_start_sql_profiling(
    monkeypatch: pytest.MonkeyPatch,
    performance_enabled: str,
    sample_rate: str,
    sink: str,
) -> None:
    """Do not touch database wrappers on disabled, unsampled, or no-op paths."""
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_ENABLED", performance_enabled)
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_SQL_ENABLED", "true")
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_SAMPLE_RATE", sample_rate)
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_SINK", sink)
    start = Mock(side_effect=AssertionError("SQL profiler must not start"))
    monkeypatch.setattr(instrumentation, "start_registry_sql_profile", start)

    @instrument_registry_function(RegistryPerformanceStage.NAMESPACE_ADMISSION)
    def return_value() -> int:
        return 4

    assert return_value() == 4
    start.assert_not_called()


def test_sql_flag_disabled_skips_wrapper_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep E1.1 events unchanged when only stage timing is enabled."""
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_ENABLED", "true")
    monkeypatch.delenv("RESOURCE_REGISTRY_PERFORMANCE_SQL_ENABLED", raising=False)
    start = Mock(side_effect=AssertionError("SQL profiler must not start"))
    monkeypatch.setattr(instrumentation, "start_registry_sql_profile", start)
    recorded: list[RegistryPerformanceMetric] = []
    monkeypatch.setattr(instrumentation, "emit_registry_performance_metric", recorded.append)

    @instrument_registry_function(RegistryPerformanceStage.CAPABILITY_RESOLUTION)
    def return_value() -> int:
        return 4

    assert return_value() == 4
    start.assert_not_called()
    assert "db_query_count" not in recorded[0].to_safe_payload()


def test_sql_enabled_zero_query_stage_emits_zero_measurements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retain zero-query stages in the baseline instead of omitting SQL fields."""
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_ENABLED", "true")
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_SQL_ENABLED", "true")
    recorded: list[RegistryPerformanceMetric] = []
    monkeypatch.setattr(instrumentation, "emit_registry_performance_metric", recorded.append)

    @instrument_registry_function(RegistryPerformanceStage.NAMESPACE_ADMISSION)
    def validate_without_database() -> bool:
        return False

    assert validate_without_database() is False
    assert recorded[0].to_safe_payload()["db_query_count"] == 0
    assert recorded[0].to_safe_payload()["db_alias_count"] == 0
    assert recorded[0].to_safe_payload()["db_duration_ms"] == 0.0


def test_nested_sql_profiles_are_inclusive_and_not_aggregated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Count the inner query once per stage, not twice within the outer stage."""
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_ENABLED", "true")
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_SQL_ENABLED", "true")
    fake_connection = _FakeDatabaseConnection()
    monkeypatch.setattr(
        sql_profiling, "connections", SimpleNamespace(all=lambda: [fake_connection])
    )
    recorded: list[RegistryPerformanceMetric] = []
    monkeypatch.setattr(instrumentation, "emit_registry_performance_metric", recorded.append)

    @instrument_registry_function(RegistryPerformanceStage.CAPABILITY_RESOLUTION)
    def inner() -> None:
        assert fake_connection.execute() == 1

    @instrument_registry_function(RegistryPerformanceStage.GROUPED_CALL_TOTAL)
    def outer() -> None:
        assert fake_connection.execute() == 1
        inner()

    outer()

    assert [(metric.stage, metric.db_query_count) for metric in recorded] == [
        (RegistryPerformanceStage.CAPABILITY_RESOLUTION, 1),
        (RegistryPerformanceStage.GROUPED_CALL_TOTAL, 2),
    ]
    assert all(metric.db_duration_ms is not None for metric in recorded)
    assert fake_connection.has_no_wrappers()


def test_multiple_aliases_are_counted_once_without_emitting_alias_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Observe default and replica connections without exposing their aliases."""
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_ENABLED", "true")
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_SQL_ENABLED", "true")
    primary = _FakeDatabaseConnection()
    replica = _FakeDatabaseConnection()
    monkeypatch.setattr(
        sql_profiling, "connections", SimpleNamespace(all=lambda: [primary, replica])
    )
    recorded: list[RegistryPerformanceMetric] = []
    monkeypatch.setattr(instrumentation, "emit_registry_performance_metric", recorded.append)

    @instrument_registry_function(RegistryPerformanceStage.AUTHORIZATION_CONTEXT_BUILD)
    def read_both_connections() -> None:
        primary.execute()
        replica.execute()
        replica.execute()

    read_both_connections()

    payload = recorded[0].to_safe_payload()
    assert payload["db_query_count"] == 3
    assert payload["db_alias_count"] == 2
    assert "primary" not in str(payload)
    assert "replica" not in str(payload)
    assert primary.has_no_wrappers() and replica.has_no_wrappers()


def test_failing_sql_preserves_exception_identity_and_failed_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Count attempted SQL while preserving the original exception and traceback."""
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_ENABLED", "true")
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_SQL_ENABLED", "true")
    fake_connection = _FakeDatabaseConnection()
    monkeypatch.setattr(
        sql_profiling, "connections", SimpleNamespace(all=lambda: [fake_connection])
    )
    recorded: list[RegistryPerformanceMetric] = []
    monkeypatch.setattr(instrumentation, "emit_registry_performance_metric", recorded.append)
    failure = ValueError("private SQL and credential")

    @instrument_registry_function(RegistryPerformanceStage.NAMESPACE_ADMISSION)
    def execute_failing_query() -> None:
        fake_connection.execute(failure=failure)

    with pytest.raises(ValueError) as caught:
        execute_failing_query()

    assert caught.value is failure
    assert caught.value.__traceback__ is not None
    assert caught.value.__traceback__.tb_next is not None
    assert recorded[0].outcome is RegistryPerformanceOutcome.FAILED
    assert recorded[0].db_query_count == 1
    assert "private SQL" not in str(recorded[0].to_safe_payload())
    assert fake_connection.has_no_wrappers()


def test_profiler_setup_failure_does_not_change_application_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fall back to an E1.1-only event when database wrapper setup fails."""
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_ENABLED", "true")
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_SQL_ENABLED", "true")
    failure = RuntimeError("connection enumeration failed")
    monkeypatch.setattr(
        sql_profiling, "connections", SimpleNamespace(all=Mock(side_effect=failure))
    )
    recorded: list[RegistryPerformanceMetric] = []
    monkeypatch.setattr(instrumentation, "emit_registry_performance_metric", recorded.append)

    @instrument_registry_function(RegistryPerformanceStage.CAPABILITY_RESOLUTION)
    def return_value() -> int:
        return 5

    assert return_value() == 5
    assert "db_query_count" not in recorded[0].to_safe_payload()


def test_unexpected_profiler_start_exception_keeps_application_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not mask an application exception with an unexpected profiler error."""
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_ENABLED", "true")
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_SQL_ENABLED", "true")
    monkeypatch.setattr(
        instrumentation,
        "start_registry_sql_profile",
        Mock(side_effect=RuntimeError("profiler failure")),
    )
    recorded: list[RegistryPerformanceMetric] = []
    monkeypatch.setattr(instrumentation, "emit_registry_performance_metric", recorded.append)
    original = LookupError("original")

    @instrument_registry_function(RegistryPerformanceStage.CAPABILITY_RESOLUTION)
    def fail() -> None:
        raise original

    with pytest.raises(LookupError) as caught:
        fail()
    assert caught.value is original
    assert recorded[0].outcome is RegistryPerformanceOutcome.FAILED
    assert "db_query_count" not in recorded[0].to_safe_payload()


def test_partial_profiler_setup_removes_prior_alias_wrapper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not leave a wrapper installed if another alias cannot be profiled."""
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_ENABLED", "true")
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_SQL_ENABLED", "true")
    first_connection = _FakeDatabaseConnection()

    class FailingConnection:
        """Raise while installing the second alias wrapper."""

        @contextmanager
        def execute_wrapper(self, wrapper: Callable[..., int]) -> Iterator[None]:
            """Simulate Django wrapper setup failure."""
            _ = wrapper
            raise RuntimeError("private alias failure")
            yield

    monkeypatch.setattr(
        sql_profiling,
        "connections",
        SimpleNamespace(all=lambda: [first_connection, FailingConnection()]),
    )
    recorded: list[RegistryPerformanceMetric] = []
    monkeypatch.setattr(instrumentation, "emit_registry_performance_metric", recorded.append)

    @instrument_registry_function(RegistryPerformanceStage.CAPABILITY_RESOLUTION)
    def return_value() -> int:
        return first_connection.execute()

    assert return_value() == 1
    assert first_connection.has_no_wrappers()
    assert "db_query_count" not in recorded[0].to_safe_payload()


def test_sql_clock_failure_does_not_change_execution_or_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the query count and original result when profiler timing fails."""
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_ENABLED", "true")
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_SQL_ENABLED", "true")
    fake_connection = _FakeDatabaseConnection()
    monkeypatch.setattr(
        sql_profiling, "connections", SimpleNamespace(all=lambda: [fake_connection])
    )
    monkeypatch.setattr(
        sql_profiling, "perf_counter_ns", Mock(side_effect=RuntimeError("clock failed"))
    )
    recorded: list[RegistryPerformanceMetric] = []
    monkeypatch.setattr(instrumentation, "emit_registry_performance_metric", recorded.append)

    @instrument_registry_function(RegistryPerformanceStage.CAPABILITY_RESOLUTION)
    def execute_query() -> int:
        return fake_connection.execute()

    assert execute_query() == 1
    assert recorded[0].db_query_count == 1
    assert recorded[0].db_duration_ms == 0.0


def test_concurrent_calls_use_independent_sql_counters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep per-thread query counts separate under overlapping calls."""
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_ENABLED", "true")
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_SQL_ENABLED", "true")
    fake_connections = _ThreadLocalConnections()
    monkeypatch.setattr(sql_profiling, "connections", fake_connections)
    barrier = Barrier(4)
    recorded: list[RegistryPerformanceMetric] = []
    monkeypatch.setattr(instrumentation, "emit_registry_performance_metric", recorded.append)

    @instrument_registry_function(RegistryPerformanceStage.GROUPED_CALL_TOTAL)
    def execute_queries(count: int) -> int:
        barrier.wait(timeout=5)
        for _ in range(count):
            fake_connections.all()[0].execute()
        return count

    with ThreadPoolExecutor(max_workers=4) as executor:
        assert list(executor.map(execute_queries, range(1, 5))) == list(range(1, 5))

    assert sorted(metric.db_query_count for metric in recorded if metric.db_query_count) == list(
        range(1, 5)
    )
    assert all(metric.db_alias_count == 1 for metric in recorded)


def test_sql_metric_values_are_bounded_and_finite() -> None:
    """Keep even malformed internal measurements within a numeric schema."""
    metric = RegistryPerformanceMetric(
        stage=RegistryPerformanceStage.GROUPED_CALL_TOTAL,
        operation="unknown",
        outcome=RegistryPerformanceOutcome.SUCCESS,
        duration_ms=1.0,
        db_query_count=-1,
        db_duration_ms=math.inf,
        db_alias_count=999_999,
    )
    payload = metric.to_safe_payload()
    assert payload["db_query_count"] == 0
    assert payload["db_duration_ms"] == 0.0
    assert payload["db_alias_count"] == sql_profiling.MAX_DB_ALIAS_COUNT


def test_authorization_and_validation_failures_keep_public_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Observe failures without bypassing canonical Registry checks."""
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_ENABLED", "true")
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_SQL_ENABLED", "true")
    recorded: list[RegistryPerformanceMetric] = []
    monkeypatch.setattr(instrumentation, "emit_registry_performance_metric", recorded.append)

    with pytest.raises(RegistryAuthorizationAuthenticationRequiredError):
        RegistryAuthorizationContextService().build_context(user=None, organization=None)
    definition = FunctionToolDefinition(
        name="resource_registry_get_dataset",
        description="Read a dataset.",
        parameters={
            "type": "object",
            "properties": {"dataset_id": {"type": "string"}},
            "required": ["dataset_id"],
        },
    )
    assert is_valid_grouped_registry_arguments(definition, {"dataset_id": 7}) is False

    assert [metric.outcome for metric in recorded] == [
        RegistryPerformanceOutcome.FAILED,
        RegistryPerformanceOutcome.REJECTED,
    ]
    assert all(metric.db_query_count == 0 for metric in recorded)


@pytest.mark.django_db
@pytest.mark.parametrize("query_count", [0, 1, 2])
def test_enabled_sql_profiling_counts_executions_without_sql_text(
    monkeypatch: pytest.MonkeyPatch, query_count: int
) -> None:
    """Attach count, elapsed SQL time, and alias count to the existing event."""
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_ENABLED", "true")
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_SQL_ENABLED", "true")
    recorded: list[RegistryPerformanceMetric] = []
    monkeypatch.setattr(instrumentation, "emit_registry_performance_metric", recorded.append)

    @instrument_registry_function(RegistryPerformanceStage.CAPABILITY_RESOLUTION)
    def read_database() -> None:
        if query_count:
            with connection.cursor() as cursor:
                for _ in range(query_count):
                    cursor.execute("SELECT 1")

    with override_settings(DEBUG=False):
        read_database()

    assert len(recorded) == 1
    payload = recorded[0].to_safe_payload()
    assert payload["db_query_count"] == query_count
    assert isinstance(payload["db_duration_ms"], float)
    assert payload["db_duration_ms"] >= 0
    assert payload["db_alias_count"] == (1 if query_count else 0)
    assert "SELECT" not in str(payload)
    assert "default" not in str(payload)


@pytest.mark.django_db
def test_real_database_error_preserves_exception_and_counts_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Count a failing Django SQL execution without changing its exception."""
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_ENABLED", "true")
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_SQL_ENABLED", "true")
    recorded: list[RegistryPerformanceMetric] = []
    caught_inside: list[DatabaseError] = []
    monkeypatch.setattr(instrumentation, "emit_registry_performance_metric", recorded.append)

    @instrument_registry_function(RegistryPerformanceStage.CAPABILITY_RESOLUTION)
    def execute_invalid_query() -> None:
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT FROM")
        except DatabaseError as failure:
            caught_inside.append(failure)
            raise

    with pytest.raises(DatabaseError) as caught:
        execute_invalid_query()

    assert caught.value is caught_inside[0]
    assert recorded[0].outcome is RegistryPerformanceOutcome.FAILED
    assert recorded[0].db_query_count == 1
    assert "SELECT FROM" not in str(recorded[0].to_safe_payload())
