"""Verify opt-in Registry performance measurements without recording request data."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from resource_registry.performance import instrumentation
from resource_registry.performance.instrumentation import (
    RegistryPerformanceMetric,
    RegistryPerformanceOutcome,
    RegistryPerformanceStage,
    get_registry_performance_configuration,
    instrument_registry_function,
    reset_registry_performance_configuration_for_tests,
    should_sample_registry_performance,
)


@pytest.fixture(autouse=True)
def reset_performance_configuration() -> Iterator[None]:
    """Keep process-level configuration changes isolated between tests."""
    reset_registry_performance_configuration_for_tests()
    yield
    reset_registry_performance_configuration_for_tests()


def test_configuration_defaults_to_disabled_and_is_immutable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disable measurement unless Registry-specific opt-in is explicit."""
    monkeypatch.delenv("RESOURCE_REGISTRY_PERFORMANCE_ENABLED", raising=False)
    configuration = get_registry_performance_configuration()
    assert configuration.enabled is False
    with pytest.raises(FrozenInstanceError):
        configuration.enabled = True


def test_configuration_is_cached_until_explicit_test_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolve immutable configuration once per process."""
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_ENABLED", "true")
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_SAMPLE_RATE", "0.25")
    original = get_registry_performance_configuration()
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_ENABLED", "false")
    assert get_registry_performance_configuration() is original
    reset_registry_performance_configuration_for_tests()
    assert get_registry_performance_configuration().enabled is False


def test_concurrent_configuration_reads_share_one_immutable_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Initialize process configuration once across concurrent workers."""
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_ENABLED", "true")
    with ThreadPoolExecutor(max_workers=12) as executor:
        configurations = list(
            executor.map(lambda _: get_registry_performance_configuration(), range(40))
        )
    assert all(configuration is configurations[0] for configuration in configurations)


@pytest.mark.parametrize(
    ("enabled", "rate", "sink", "expected_enabled", "expected_rate", "expected_sink"),
    [
        ("true", "0", "log", True, 0.0, "log"),
        ("yes", "1", "noop", True, 1.0, "noop"),
        ("garbage", "2", "unknown", False, 1.0, "log"),
        ("1", "nan", "log", True, 1.0, "log"),
        ("on", "-0.1", "log", True, 1.0, "log"),
    ],
)
def test_configuration_parses_bounded_values(
    monkeypatch: pytest.MonkeyPatch,
    enabled: str,
    rate: str,
    sink: str,
    expected_enabled: bool,
    expected_rate: float,
    expected_sink: str,
) -> None:
    """Reject invalid settings without leaking their values to logs."""
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_ENABLED", enabled)
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_SAMPLE_RATE", rate)
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_SINK", sink)
    configuration = get_registry_performance_configuration()
    assert (configuration.enabled, configuration.sample_rate, configuration.sink) == (
        expected_enabled,
        expected_rate,
        expected_sink,
    )


def test_disabled_function_uses_neither_clock_nor_sink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Leave the default call path observationally inert."""
    monkeypatch.delenv("RESOURCE_REGISTRY_PERFORMANCE_ENABLED", raising=False)
    clock = Mock(side_effect=AssertionError("Clock must not run"))
    sink = Mock(side_effect=AssertionError("Sink must not run"))
    monkeypatch.setattr(instrumentation, "perf_counter_ns", clock)
    monkeypatch.setattr(instrumentation, "emit_registry_performance_metric", sink)

    @instrument_registry_function(RegistryPerformanceStage.GROUPED_SCHEMA_BUILD)
    def build_value() -> int:
        return 7

    assert build_value() == 7
    clock.assert_not_called()
    sink.assert_not_called()


def test_zero_sampling_skips_clock_and_sink_even_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Treat the zero boundary as an unsampled call."""
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_ENABLED", "true")
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_SAMPLE_RATE", "0.0")
    clock = Mock(side_effect=AssertionError("Clock must not run"))
    sink = Mock(side_effect=AssertionError("Sink must not run"))
    monkeypatch.setattr(instrumentation, "perf_counter_ns", clock)
    monkeypatch.setattr(instrumentation, "emit_registry_performance_metric", sink)

    @instrument_registry_function(RegistryPerformanceStage.GROUPED_SCHEMA_BUILD)
    def build_value() -> int:
        return 8

    assert build_value() == 8
    clock.assert_not_called()
    sink.assert_not_called()


def test_sampling_boundaries_use_a_deterministic_sampler() -> None:
    """Honor exact off/on boundaries and probabilistic intermediate values."""
    draw = Mock(return_value=0.4)
    assert should_sample_registry_performance(0.0, sampler=draw) is False
    assert should_sample_registry_performance(1.0, sampler=draw) is True
    draw.assert_not_called()
    assert should_sample_registry_performance(0.5, sampler=draw) is True
    assert should_sample_registry_performance(0.4, sampler=draw) is False
    assert draw.call_count == 2


def test_enabled_function_emits_sanitized_success_and_inclusive_timing(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Emit fixed labels and inclusive nested durations without request content."""
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_ENABLED", "true")
    monkeypatch.setattr(
        instrumentation,
        "perf_counter_ns",
        Mock(side_effect=[0, 10_000_000, 20_000_000, 50_000_000]),
    )

    @instrument_registry_function(RegistryPerformanceStage.GROUPED_SCHEMA_VALIDATION)
    def inner(secret: str) -> str:
        return secret

    @instrument_registry_function(RegistryPerformanceStage.GROUPED_CALL_TOTAL)
    def outer(secret: str) -> str:
        return inner(secret)

    with caplog.at_level(logging.INFO, logger="resource_registry.performance"):
        assert outer("tenant-secret prompt credential") == "tenant-secret prompt credential"

    metrics = [json.loads(record.message) for record in caplog.records]
    assert len(metrics) == 2
    assert {metric["duration_ms"] for metric in metrics} == {10.0, 50.0}
    assert all(metric["outcome"] == "success" and metric["inclusive"] for metric in metrics)
    assert "tenant-secret" not in caplog.text
    assert "prompt credential" not in caplog.text
    assert set(metrics[0]) == {
        "schema_version",
        "event",
        "stage",
        "operation",
        "outcome",
        "duration_ms",
        "inclusive",
    }


def test_exception_object_and_traceback_are_preserved_and_marked_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never turn application exceptions into successful metrics or new errors."""
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_ENABLED", "true")
    recorded: list[RegistryPerformanceMetric] = []
    monkeypatch.setattr(instrumentation, "emit_registry_performance_metric", recorded.append)
    failure = ValueError("private dataset credential")

    @instrument_registry_function(RegistryPerformanceStage.NAMESPACE_ADMISSION)
    def fail() -> None:
        raise failure

    with pytest.raises(ValueError) as caught:
        fail()
    assert caught.value is failure
    assert caught.value.__traceback__ is not None
    assert caught.value.__traceback__.tb_next is not None
    assert len(recorded) == 1
    assert recorded[0].outcome is RegistryPerformanceOutcome.FAILED
    assert "private dataset credential" not in str(recorded[0])


def test_sink_failure_does_not_change_return_or_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep metrics best effort even if an existing logger fails."""
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_ENABLED", "true")
    monkeypatch.setattr(
        instrumentation,
        "emit_registry_performance_metric",
        Mock(side_effect=OSError("logging down")),
    )

    @instrument_registry_function(RegistryPerformanceStage.CAPABILITY_RESOLUTION)
    def succeed() -> int:
        return 3

    assert succeed() == 3
    original = LookupError("original")

    @instrument_registry_function(RegistryPerformanceStage.CAPABILITY_RESOLUTION)
    def fail() -> None:
        raise original

    with pytest.raises(LookupError) as caught:
        fail()
    assert caught.value is original


def test_instrumentation_fault_does_not_repeat_or_replace_application_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run the application exactly once if sampling or the clock fails."""
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_ENABLED", "true")
    original = RuntimeError("application failure")
    calls = 0

    @instrument_registry_function(RegistryPerformanceStage.NAMESPACE_ADMISSION)
    def fail() -> None:
        nonlocal calls
        calls += 1
        raise original

    monkeypatch.setattr(
        instrumentation,
        "should_sample_registry_performance",
        Mock(side_effect=RuntimeError("sampler failure")),
    )
    with pytest.raises(RuntimeError) as caught:
        fail()
    assert caught.value is original
    assert calls == 1

    monkeypatch.setattr(instrumentation, "should_sample_registry_performance", lambda rate: True)
    monkeypatch.setattr(
        instrumentation,
        "perf_counter_ns",
        Mock(side_effect=RuntimeError("clock failure")),
    )
    with pytest.raises(RuntimeError) as caught:
        fail()
    assert caught.value is original
    assert calls == 2


def test_operation_label_is_bounded_even_with_untrusted_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not emit model supplied identifiers or arbitrary tool names."""
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_ENABLED", "true")
    recorded: list[RegistryPerformanceMetric] = []
    monkeypatch.setattr(instrumentation, "emit_registry_performance_metric", recorded.append)

    @instrument_registry_function(
        RegistryPerformanceStage.GROUPED_CALL_TOTAL, operation_parameter="function_name"
    )
    def run(function_name: str) -> None:
        _ = function_name

    run("tenant-secret dataset contents")
    run("resource_registry_inspect")
    run("resource_registry_query_dataset")
    assert [metric.operation for metric in recorded] == [
        "unknown",
        "resource_registry_inspect",
        "resource_registry_query_dataset",
    ]
    assert recorded[0].to_safe_payload()["operation"] == "unknown"


def test_concurrent_calls_keep_independent_outcomes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Avoid mutable request globals across worker threads."""
    monkeypatch.setenv("RESOURCE_REGISTRY_PERFORMANCE_ENABLED", "true")
    recorded: list[RegistryPerformanceMetric] = []
    monkeypatch.setattr(instrumentation, "emit_registry_performance_metric", recorded.append)

    @instrument_registry_function(
        RegistryPerformanceStage.DETERMINISTIC_ROUTING,
        outcome_classifier=lambda value: (
            RegistryPerformanceOutcome.ROUTED
            if value is not None
            else RegistryPerformanceOutcome.FALLBACK
        ),
    )
    def route(value: object | None) -> object | None:
        return value

    values: list[object | None] = [None, SimpleNamespace(), None, SimpleNamespace()] * 10
    with ThreadPoolExecutor(max_workers=8) as executor:
        assert list(executor.map(route, values)) == values
    assert len(recorded) == len(values)
    assert sum(metric.outcome is RegistryPerformanceOutcome.ROUTED for metric in recorded) == 20
    assert sum(metric.outcome is RegistryPerformanceOutcome.FALLBACK for metric in recorded) == 20
