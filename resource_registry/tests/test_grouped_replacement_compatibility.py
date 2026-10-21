"""Keep ATLAS-666 replacement tools independent from grouped Registry publication."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

import pytest

from BotServer.ai_engine_clients.anthropic_client import AnthropicClient
from BotServer.ai_engine_clients.gemini_client import GeminiClient
from BotServer.ai_engine_clients.openai_client import ChatGPTClient
from BotServer.ai_engine_clients.openai_resp_client import OpenAIResponseClient
from BotServer.models import (
    BuiltInFunctionPersonaRelationship,
    FunctionParameter,
    MCPServerPersonaRelationship,
)
from resource_registry.assistant.runtime_adapter import DataRegistryExplorationRoutingAdapter
from resource_registry.bifs.grouped_publication import RegistryToolProvider, prepare_registry_tools


@pytest.mark.parametrize("provider", ["openai", "openai_responses", "gemini", "anthropic"])
def test_replacement_assignments_bypass_grouped_publication(
    provider: RegistryToolProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Leave replacement tools to each client's original formatter without parameter queries.

    Args:
        provider: Provider-specific envelope under test.
        monkeypatch: Fixture guarding against reads of independent tool parameters.
    """
    replacements = [
        SimpleNamespace(builtin_function=SimpleNamespace(name=name))
        for name in (
            "resource_registry_replace_dataset",
            "resource_registry_get_replacement_status",
        )
    ]
    grouped = SimpleNamespace(
        builtin_function=SimpleNamespace(
            name="resource_registry_get_dataset_schema",
            description="Read schema.",
            _prefetched_objects_cache={"functionparameter_set": []},
        )
    )
    parameter_query = Mock(side_effect=AssertionError("Independent tools must bypass the adapter"))
    monkeypatch.setattr(FunctionParameter.objects, "filter", parameter_query)
    remaining, tools = prepare_registry_tools(
        cast(list[BuiltInFunctionPersonaRelationship], [replacements[0], grouped, replacements[1]]),
        persona=None,
        provider=provider,
        statement="Inspect datasets",
        allow_dataset_replacement=True,
    )
    assert list(remaining) == replacements
    assert [cast(dict[str, object], tool.get("function", tool))["name"] for tool in tools] == [
        "resource_registry_inspect"
    ]
    parameter_query.assert_not_called()


@pytest.mark.parametrize("provider", ["openai", "openai_responses", "gemini", "anthropic"])
def test_normal_chat_hides_replacement_but_keeps_status(
    provider: RegistryToolProvider,
) -> None:
    """Keep the write tool out of each provider's published independent assignments."""
    replacements = [
        SimpleNamespace(builtin_function=SimpleNamespace(name=name))
        for name in (
            "resource_registry_replace_dataset",
            "resource_registry_get_replacement_status",
        )
    ]

    remaining, tools = prepare_registry_tools(
        cast(list[BuiltInFunctionPersonaRelationship], replacements),
        persona=None,
        provider=provider,
        statement="Upload this file to chat.",
    )

    assert [row.builtin_function.name for row in remaining] == [
        "resource_registry_get_replacement_status"
    ]
    assert tools == []


@pytest.mark.parametrize(
    "client_type", [ChatGPTClient, OpenAIResponseClient, GeminiClient, AnthropicClient]
)
@pytest.mark.parametrize("import_requested", [False, True, None])
def test_provider_passes_server_import_decision_to_tool_publication(
    client_type: type[ChatGPTClient] | type[OpenAIResponseClient],
    import_requested: bool | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pass only the server's current-turn decision into every provider's tool list."""

    class PublicationReached(Exception):
        """Stop execution once the provider publishes its assigned tools."""

    observed: list[bool] = []

    def capture_publication(*args: object, **kwargs: object) -> None:
        observed.append(bool(kwargs["allow_dataset_replacement"]))
        raise PublicationReached

    monkeypatch.setattr(
        "resource_registry.bifs.grouped_publication.prepare_registry_tools", capture_publication
    )
    monkeypatch.setattr(BuiltInFunctionPersonaRelationship.objects, "filter", lambda **kwargs: [])
    monkeypatch.setattr(MCPServerPersonaRelationship.objects, "filter", lambda **kwargs: [])
    monkeypatch.setattr(
        DataRegistryExplorationRoutingAdapter, "try_route", lambda *args, **kwargs: None
    )
    client = client_type.__new__(client_type)
    client.persona = SimpleNamespace(
        stop_sequences=None,
        use_fine_tuned_model=False,
        fine_tuned_model=None,
        ai_engine=SimpleNamespace(
            reasoning_effort=None,
            ai_model=SimpleNamespace(
                vendor_model="test-model",
                vendor=SimpleNamespace(supports_function_calling=True),
            ),
        ),
    )
    monkeypatch.setattr(client, "handle_tool_loop_circuit_breaker", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        client, "getPrompt", lambda *args: [{"role": "system", "content": "system"}]
    )
    monkeypatch.setattr(client, "get_request_vendor_model", lambda: "test-model")

    request = SimpleNamespace()
    if import_requested is not None:
        request.resource_registry_chat_import_requested = import_requested

    with pytest.raises(PublicationReached):
        client.executeInteraction(
            request,
            user_id="tester",
            statement="Register attached file" if import_requested else "Upload file to chat",
            video_files=None,
            video_url=None,
            temperature=0.2,
            max_tokens=100,
            frequency_penalty=0.0,
            presence_penalty=0.0,
            recursion_count=0,
        )

    assert observed == [True if import_requested is None else import_requested]
