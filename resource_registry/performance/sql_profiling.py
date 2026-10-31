"""Count SQL executions within one Registry stage using Django's supported wrapper."""

from __future__ import annotations

import math
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from threading import Lock
from time import perf_counter_ns
from typing import Any

from django.db import connections

MAX_DB_QUERY_COUNT = 2_147_483_647
MAX_DB_ALIAS_COUNT = 1_024
MAX_DB_DURATION_MS = 86_400_000.0
_MAX_DB_DURATION_NS = int(MAX_DB_DURATION_MS * 1_000_000)


@dataclass(frozen=True)
class RegistrySqlMeasurements:
    """Carry bounded, inclusive SQL measurements without connection identities."""

    query_count: int
    duration_ms: float
    alias_count: int


class _ConnectionQueryCounter:
    """Count executions on one Django connection during one measured stage."""

    def __init__(self) -> None:
        self._lock: Lock = Lock()
        self._query_count: int = 0
        self._duration_ns: int = 0

    def execute_and_measure(
        self,
        # Django's execute_wrapper callback contract uses Any for parameters and results.
        execute: Callable[[str, Any, bool, dict[str, Any]], Any],
        sql: str,
        parameters: Any,
        many: bool,
        context: dict[str, Any],
    ) -> Any:
        """Invoke Django's SQL executor once and observe its elapsed time.

        Args:
            execute: The next Django execution wrapper or database executor.
            sql: SQL passed through unchanged and never retained.
            parameters: Query parameters passed through unchanged and never retained.
            many: Django's bulk-execution flag, passed through unchanged.
            context: Django execution context, passed through unchanged.

        Returns:
            The executor's original result.

        Raises:
            BaseException: The original executor exception, unchanged.
        """
        try:
            started_ns = perf_counter_ns()
        except Exception:
            started_ns = None
        try:
            return execute(sql, parameters, many, context)
        finally:
            try:
                elapsed_ns = max(0, perf_counter_ns() - started_ns) if started_ns is not None else 0
            except Exception:
                elapsed_ns = 0
            try:
                with self._lock:
                    self._query_count = min(MAX_DB_QUERY_COUNT, self._query_count + 1)
                    self._duration_ns = min(_MAX_DB_DURATION_NS, self._duration_ns + elapsed_ns)
            except Exception:
                pass

    def snapshot(self) -> tuple[int, int]:
        """Read this connection's bounded count and monotonic duration.

        Returns:
            Query count and elapsed nanoseconds for this stage.
        """
        with self._lock:
            return self._query_count, self._duration_ns


class RegistrySqlProfile:
    """Own per-call SQL wrappers and counters for configured Django aliases."""

    def __init__(self) -> None:
        self._wrappers: ExitStack = ExitStack()
        self._counters: list[_ConnectionQueryCounter] = []

    def start(self) -> bool:
        """Install wrappers on current-thread connections without opening them.

        Returns:
            Whether all configured alias wrappers were installed.
        """
        try:
            for database_connection in connections.all():
                counter = _ConnectionQueryCounter()
                self._wrappers.enter_context(
                    database_connection.execute_wrapper(counter.execute_and_measure)
                )
                self._counters.append(counter)
        except Exception:
            self.stop()
            return False
        return True

    def stop(self) -> None:
        """Remove per-call wrappers without changing application results."""
        try:
            self._wrappers.close()
        except Exception:
            pass

    def snapshot(self) -> RegistrySqlMeasurements:
        """Aggregate inclusive counts and durations without alias names.

        Returns:
            Bounded measurements across this call's configured connections.
        """
        query_count = 0
        duration_ns = 0
        alias_count = 0
        for counter in self._counters:
            connection_queries, connection_duration_ns = counter.snapshot()
            query_count = min(MAX_DB_QUERY_COUNT, query_count + connection_queries)
            duration_ns = min(_MAX_DB_DURATION_NS, duration_ns + connection_duration_ns)
            alias_count += connection_queries > 0
        return RegistrySqlMeasurements(
            query_count=query_count,
            duration_ms=duration_ns / 1_000_000,
            alias_count=min(MAX_DB_ALIAS_COUNT, alias_count),
        )


def start_registry_sql_profile() -> RegistrySqlProfile | None:
    """Start a best-effort SQL profile for a sampled Registry stage.

    Returns:
        Active per-call profile, or None if wrapper setup failed.
    """
    profile = RegistrySqlProfile()
    return profile if profile.start() else None


def bound_nonnegative_sql_count(value: int, maximum: int) -> int:
    """Clamp a SQL count before it enters a serialized metric.

    Args:
        value: Measured query or alias count.
        maximum: Public upper bound for the metric.

    Returns:
        An integer between zero and the upper bound.
    """
    return min(max(0, value), maximum)


def bound_nonnegative_sql_duration(value: float) -> float:
    """Clamp SQL milliseconds to a finite public numeric range.

    Args:
        value: Measured SQL execution-wrapper duration in milliseconds.

    Returns:
        A finite nonnegative duration at or below the public upper bound.
    """
    if not math.isfinite(value):
        return 0.0
    return min(max(0.0, value), MAX_DB_DURATION_MS)
