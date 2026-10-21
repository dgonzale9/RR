"""Verify standalone workspace commands without requiring a database connection."""

import pytest

from resource_registry.assistant.namespace_context import (
    AssistantNamespaceResolution,
    AssistantWorkspaceChoice,
)
from resource_registry.assistant.runtime_adapter import DataRegistryExplorationRoutingAdapter


@pytest.mark.parametrize(
    ("statement", "expected"),
    [
        ("switch to main (main)", True),
        ("Use Main.", True),
        ("Please select the workspace `main`!", True),
        ("Work in Main", True),
        ("Change to **Main** (`main`).", True),
        ("Use the - Main (`main`) Workspace.", True),
        ("Move to Main", True),
        ("Use mainland", False),
        ("Do not use Main.", False),
        ("Use Main and generate three concepts.", False),
        ("Use Main.\nGenerate three concepts.", False),
        ("Use Main and list its datasets.", False),
        ("Use approved logos. Active Workspace: main.", False),
        (
            "ORCHESTRATION MODE Route: Concept-first. Use the - Main (`main`) Workspace. "
            "Validate Resource Registry assets before producing concepts. "
            "Do not use placeholder assets. Generate three creative concepts per audience.",
            False,
        ),
        (
            "ORCHESTRATION MODE Continue the concept generation task now that Workspace "
            "is switched to **Main** (`main`). Active Workspace: main. "
            "Do not use placeholder assets. Generate three creative concepts per audience.",
            False,
        ),
    ],
)
def test_confirm_only_standalone_workspace_commands(statement: str, expected: bool) -> None:
    """Reserve terminal confirmations for workspace-only requests.

    Args:
        statement: Selection command or task message to classify.
        expected: Whether routing should stop with a workspace confirmation.
    """
    resolution = AssistantNamespaceResolution(
        status="selected",
        selected=AssistantWorkspaceChoice(
            workspace_id=1,
            workspace_name="Main",
            workspace_slug="main",
            namespace_id="test-namespace",
        ),
    )

    assert (
        DataRegistryExplorationRoutingAdapter._is_explicit_workspace_switch(
            statement=statement,
            resolution=resolution,
        )
        is expected
    )


def test_require_authorized_selection_before_confirming_workspace() -> None:
    """Avoid confirming a workspace when canonical resolution has no selection."""
    assert not DataRegistryExplorationRoutingAdapter._is_explicit_workspace_switch(
        statement="Use Main",
        resolution=AssistantNamespaceResolution(status="none"),
    )
