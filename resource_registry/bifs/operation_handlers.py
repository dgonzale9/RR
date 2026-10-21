"""Resolve canonical Registry handlers independently of the public BIF namespace."""

from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
from typing import cast

from resource_registry.bifs.grouped_tools import GROUPED_REGISTRY_ACTIONS
from resource_registry.bifs.manifest import RESOURCE_REGISTRY_BIF_MANIFEST


def resolve_registry_operation_handler(function_name: str) -> Callable[..., object]:
    """Load an original Registry implementation after canonical execution admission.

    Args:
        function_name: Original operation name from the Registry manifest.

    Returns:
        The unchanged operation handler, without adding a public host alias.

    Raises:
        ValueError: If the name is not a registered Registry operation.
    """
    if function_name not in RESOURCE_REGISTRY_BIF_MANIFEST:
        raise ValueError("Expected a registered Registry operation.")
    action = function_name.removeprefix("resource_registry_")
    module_name = (
        "resource_registry.bifs.transformation"
        if action in GROUPED_REGISTRY_ACTIONS["resource_registry_manage_transformations"]
        else "BotServer.builtin_functions.resource_registry_functions"
    )
    return cast(Callable[..., object], getattr(import_module(module_name), function_name))
