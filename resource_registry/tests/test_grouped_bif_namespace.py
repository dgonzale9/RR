"""Verify the public Registry namespace and private canonical handler resolution."""

from __future__ import annotations

from importlib import import_module
from unittest.mock import Mock

import pytest

from BotServer import builtin_functions
from BotServer.bif_execution_admission import BuiltInFunctionExecutionChannel
from resource_registry.bifs import grouped_runtime, operation_handlers
from resource_registry.bifs.grouped_tools import GROUPED_REGISTRY_ACTIONS
from resource_registry.bifs.manifest import RESOURCE_REGISTRY_BIF_MANIFEST
from resource_registry.bifs.operation_handlers import resolve_registry_operation_handler


def test_host_namespace_exports_only_grouped_registry_tools() -> None:
    """Expose grouped entry points without retired dataset replacement tools."""
    assert {
        name for name in builtin_functions.__all__ if name.startswith("resource_registry_")
    } == set(GROUPED_REGISTRY_ACTIONS)
    assert {
        name
        for name in builtin_functions._FUNCTION_MODULES
        if name.startswith("resource_registry_")
    } == set(GROUPED_REGISTRY_ACTIONS)
    for name in GROUPED_REGISTRY_ACTIONS:
        assert callable(getattr(builtin_functions, name))
    for name in RESOURCE_REGISTRY_BIF_MANIFEST:
        assert not hasattr(builtin_functions, name)


@pytest.mark.parametrize("group_name", GROUPED_REGISTRY_ACTIONS)
def test_public_group_uses_the_active_dispatch_client(
    group_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Forward the original client, request, envelope and execution channel once.

    Args:
        group_name: Public Registry entry point under test.
        monkeypatch: Fixture replacing the owned grouped dispatch boundary.
    """
    client = object()
    request = object()
    arguments = {"action": "unknown", "arguments": {}}
    expected = object()
    dispatch = Mock(return_value=expected)
    monkeypatch.setattr(grouped_runtime, "execute_grouped_registry_call", dispatch)
    entry_point = getattr(builtin_functions, group_name)
    assert (
        entry_point(
            client,
            request,
            arguments,
            execution_channel=BuiltInFunctionExecutionChannel.PLATFORM_API,
        )
        is expected
    )
    dispatch.assert_called_once_with(
        client,
        request,
        group_name,
        arguments,
        execution_channel=BuiltInFunctionExecutionChannel.PLATFORM_API,
    )


@pytest.mark.parametrize("function_name", sorted(RESOURCE_REGISTRY_BIF_MANIFEST))
def test_canonical_resolution_preserves_handlers_without_public_aliases(function_name: str) -> None:
    """Keep every original handler available internally without repopulating the host.

    Args:
        function_name: Canonical Registry operation to resolve.
    """
    action = function_name.removeprefix("resource_registry_")
    module_name = (
        "resource_registry.bifs.transformation"
        if action in GROUPED_REGISTRY_ACTIONS["resource_registry_manage_transformations"]
        else "BotServer.builtin_functions.resource_registry_functions"
    )
    assert resolve_registry_operation_handler(function_name) is getattr(
        import_module(module_name), function_name
    )
    assert not hasattr(builtin_functions, function_name)


@pytest.mark.parametrize(
    "function_name", ["echoMessage", "resource_registry_unknown", "resource_registry_inspect"]
)
def test_internal_resolution_rejects_unregistered_operations(
    function_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reject unrelated, unknown and grouped names before loading an implementation.

    Args:
        function_name: Name outside the original operation manifest.
        monkeypatch: Fixture replacing the owned module loader.
    """
    loader = Mock(side_effect=AssertionError("Unregistered names must not load modules"))
    monkeypatch.setattr(operation_handlers, "import_module", loader)
    with pytest.raises(ValueError, match="registered Registry operation"):
        operation_handlers.resolve_registry_operation_handler(function_name)
    loader.assert_not_called()
