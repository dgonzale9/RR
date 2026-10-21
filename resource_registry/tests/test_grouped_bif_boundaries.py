"""Verify that Registry grouping does not take ownership of unrelated BIFs."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from BotServer import builtin_functions
from BotServer.ai_engine_client import AIEngineClient
from BotServer.models import FunctionParameter
from resource_registry.bifs import grouped_runtime, operation_handlers
from resource_registry.bifs.grouped_publication import prepare_registry_tools


def test_empty_publication_does_not_load_parameters(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid parameter reads when no operations are assigned.

    Args:
        monkeypatch: Fixture replacing the parameter query boundary.
    """
    parameters = Mock(side_effect=AssertionError("No Registry parameters should be loaded"))
    monkeypatch.setattr(FunctionParameter.objects, "filter", parameters)
    remaining, tools = prepare_registry_tools(
        [], persona=None, provider="openai", statement="Inspect datasets"
    )
    assert list(remaining) == []
    assert tools == []
    parameters.assert_not_called()


def test_grouped_publication_only_reads_parameters_for_registry_operations() -> None:
    """Return unrelated assignments in their original order without describing them."""
    unrelated_first = SimpleNamespace(builtin_function=SimpleNamespace(name="first_tool"))
    unrelated_last = SimpleNamespace(builtin_function=SimpleNamespace(name="last_tool"))
    registry = SimpleNamespace(
        builtin_function=SimpleNamespace(
            name="resource_registry_get_dataset_schema",
            description="Read schema.",
            _prefetched_objects_cache={"functionparameter_set": []},
        )
    )
    remaining, tools = prepare_registry_tools(
        [unrelated_first, registry, unrelated_last],
        persona=None,
        provider="openai",
        statement="Inspect datasets",
    )
    assert list(remaining) == [unrelated_first, unrelated_last]
    assert len(tools) == 1
    assert tools[0]["function"]["name"] == "resource_registry_inspect"


def test_unrelated_dispatch_keeps_arguments_results_and_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Leave unrelated execution outside Registry grouping.

    Args:
        monkeypatch: Fixture replacing owned function and metadata boundaries.
    """
    client = AIEngineClient(None)
    client.persona = SimpleNamespace(name="Ordinary persona")
    function = SimpleNamespace(
        name="ordinary_echo", description="Echo", display_name="Echo", implementation=""
    )
    client.bif_execution_admission_service = Mock(
        admit=Mock(return_value=SimpleNamespace(function=function, delegation_rule_id=None))
    )
    parameter = SimpleNamespace(name="value", required=True)
    manager = Mock()
    manager.filter.return_value = [parameter]
    monkeypatch.setattr(FunctionParameter, "objects", manager)
    handler = Mock(return_value={"value": "unchanged"})
    monkeypatch.setattr(builtin_functions, "ordinary_echo", handler, raising=False)
    registry_dispatch = Mock(side_effect=AssertionError("Registry must not execute this BIF"))
    monkeypatch.setattr(grouped_runtime, "execute_grouped_registry_call", registry_dispatch)
    registry_resolution = Mock(side_effect=AssertionError("Registry must not resolve this BIF"))
    monkeypatch.setattr(
        operation_handlers, "resolve_registry_operation_handler", registry_resolution
    )
    request = SimpleNamespace()
    arguments = {"value": "unchanged"}
    assert client.callGenericFunction(request, "ordinary_echo", arguments) == {"value": "unchanged"}
    handler.assert_called_once_with(request, client.context_mgr, **arguments)
    failure = ValueError("Original handler failure")
    handler.side_effect = failure
    with pytest.raises(ValueError) as raised:
        client.callGenericFunction(request, "ordinary_echo", arguments)
    assert raised.value is failure
    registry_dispatch.assert_not_called()
    registry_resolution.assert_not_called()
    assert client.bif_execution_admission_service.admit.call_count == 2
