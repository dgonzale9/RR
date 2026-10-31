from __future__ import annotations

import json
from collections.abc import Collection, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from resource_registry.assistant.namespace_context import AssistantNamespaceResolution
from resource_registry.bifs.manifest import RESOURCE_REGISTRY_BIF_MANIFEST

READ_ONLY_RESOURCE_REGISTRY_FUNCTIONS = {
    "resource_registry_list_datasets",
    "resource_registry_get_dataset",
    "resource_registry_get_dataset_context",
    "resource_registry_get_dataset_schema",
    "resource_registry_get_dataset_profile",
    "resource_registry_list_dataset_versions",
    "resource_registry_list_external_jobs",
    "resource_registry_query_dataset",
    "resource_registry_plan_query",
    "resource_registry_analyze_dataset",
    "resource_registry_explore_dataset",
    "resource_registry_create_chart",
    "resource_registry_plan_export",
    "resource_registry_get_query_run",
    "resource_registry_get_analysis_artifact",
    "resource_registry_get_chart_artifact",
    "resource_registry_get_export_plan",
    "resource_registry_generate_artifact",
}

SENSITIVE_MARKERS = (
    "raw_rows",
    "preview",
    "credentials",
    "token",
    "password",
    "secret",
    "sig=",
    "Traceback",
    "Internal Server Error",
)


@dataclass
class FakeFunctionParameter:
    name: str
    type: str
    required: bool
    description: str


@dataclass
class FakeBuiltInFunction:
    name: str
    description: str
    implementation: str = ""
    display_name: str | None = None


@dataclass
class FakeRelationship:
    builtin_function: FakeBuiltInFunction


@dataclass
class FakeResponse:
    payload: dict[str, Any]

    @property
    def status_code(self) -> int:
        return 200

    @property
    def text(self) -> str:
        return json.dumps(self.payload)

    def json(self) -> dict[str, Any]:
        return self.payload


class FakeManager:
    def __init__(self, functions: dict[str, FakeBuiltInFunction]) -> None:
        self._functions = functions

    def get(self, name: str):
        if name not in self._functions:
            raise Exception(f"Function {name} not found")
        return self._functions[name]


class FakePersonaBifRelationshipManager:
    """Emulate Persona BIF assignment queries for runtime tests."""

    def __init__(self, assigned_function_names: Collection[str]) -> None:
        self._assigned_function_names = frozenset(assigned_function_names)
        self._has_match = False

    def filter(self, **criteria: object) -> FakePersonaBifRelationshipManager:
        """Record whether the requested BIF names intersect configured assignments."""
        requested_names = criteria.get("builtin_function__name__in", ())
        if not isinstance(requested_names, Collection):
            self._has_match = False
            return self
        self._has_match = bool(self._assigned_function_names.intersection(requested_names))
        return self

    def exists(self) -> bool:
        """Return whether the latest assignment query matched."""
        return self._has_match


class FakeParamManager:
    def __init__(self, params_by_name: dict[str, list[FakeFunctionParameter]]) -> None:
        self._params_by_name = params_by_name

    def get(self, function, name: str):
        for param in self._params_by_name.get(function.name, []):
            if param.name == name:
                return param
        raise Exception(f"Parameter {name} not found for {function.name}")

    def filter(self, function, required: bool = False):
        params = self._params_by_name.get(function.name, [])
        if required:
            return [param for param in params if param.required]
        return params


class FakeExecutionAdmissionService:
    """Admit functions backed by the test's patched global function manager."""

    def admit(
        self,
        *,
        active_persona: object,
        requested_function_name: str,
        execution_channel: object | None = None,
    ) -> SimpleNamespace:
        """Return a minimal admitted descriptor for legacy runtime contract tests."""
        _ = active_persona, execution_channel
        from BotServer.models import BuiltInFunction

        return SimpleNamespace(
            function=BuiltInFunction.objects.get(name=requested_function_name),
            delegation_rule_id=None,
        )


class FakeContextManager:
    def __init__(self) -> None:
        self.function_updates: list[dict[str, Any]] = []
        self.registry_entries: dict[str, dict[str, Any]] = {}

    def set_last_request(self, request, request_json):
        _ = request
        request._last_request = request_json

    def set_last_response(self, request, response_json):
        _ = request
        _ = response_json

    def get_shared_context(self, request):
        _ = request
        return {}

    def get_context_statements(self, request):
        _ = request
        return {}

    def get_context_images(self, request):
        _ = request
        return []

    def get_context_documents(self, request):
        _ = request
        return []

    def get_context_videos(self, request):
        _ = request
        return []

    def update_current_context_entry(self, request, function=None, **kwargs):
        _ = request
        _ = kwargs
        if function is not None:
            self.function_updates.append(function)

    def add_registry_entry(self, request, key: str, entry_type: str, value: Any) -> None:
        _ = request
        self.registry_entries[key] = {"type": entry_type, "value": value}

    def get_all_registry_entries(self, request) -> dict[str, dict[str, Any]]:
        _ = request
        return dict(self.registry_entries)

    def add_reasoning_to_context(self, request, item):
        _ = request
        _ = item

    def get_context_embeddings(self, request):
        _ = request


class AllowAllCapabilityPolicy:
    def assert_can_call(self, function_name, context):
        _ = function_name
        _ = context


class FakeResolver:
    def resolve(
        self,
        *,
        tenant_id: str,
        actor: str | None = None,
        roles: tuple[str, ...] = (),
        scopes: tuple[str, ...] = (),
        session_enabled_capabilities: frozenset[str] | None = None,
        session_disabled_capabilities: frozenset[str] | None = None,
        dataset_id: str | None = None,
    ):
        _ = actor
        _ = session_enabled_capabilities
        _ = session_disabled_capabilities
        _ = dataset_id
        return SimpleNamespace(
            tenant_id=tenant_id,
            policy_source="system_default",
            policy_revision=None,
            enabled_capabilities=frozenset(
                {
                    "resource_registry.dataset.list",
                    "resource_registry.dataset.read",
                    "resource_registry.dataset.context.read",
                    "resource_registry.dataset.schema.read",
                    "resource_registry.dataset.profile.read",
                    "resource_registry.dataset.versions.read",
                    "resource_registry.external_jobs.read",
                    "resource_registry.dataset.query.read",
                    "resource_registry.dataset.derived.create",
                    "resource_registry.dataset.query.plan",
                    "resource_registry.dataset.analysis.read",
                    "resource_registry.dataset.exploration.read",
                    "resource_registry.dataset.chart.read",
                    "resource_registry.dataset.export.plan",
                    "resource_registry.dataset.query_run.read",
                    "resource_registry.dataset.analysis_artifact.read",
                    "resource_registry.dataset.chart_artifact.read",
                    "resource_registry.dataset.export_plan.read",
                    "resource_registry.dataset.artifact.generate",
                },
            ),
            disabled_capabilities=frozenset(),
            effective_roles=roles,
            effective_scopes=scopes,
            resolution_path=("system_default",),
        )


class FakeReadFacade:
    def list_datasets(self, context, **kwargs):
        _ = context
        _ = kwargs
        return [{"id": "d-1", "name": "Test Dataset", "slug": "test-dataset"}]


def build_persona(
    name: str,
    *,
    vendor_model: str = "test-openai-model",
    endpoint_url: str = "https://api.test.example.com/v1/chat/completions",
    assigned_function_names: Collection[str] | None = None,
) -> SimpleNamespace:
    vendor = SimpleNamespace(
        supports_function_calling=True,
        endpoint_url=endpoint_url,
        enable_web_search=False,
    )
    model = SimpleNamespace(vendor=vendor, vendor_model=vendor_model)
    ai_engine = SimpleNamespace(ai_model=model, api_key="test-api-key", reasoning_effort=None)
    return SimpleNamespace(
        name=name,
        ai_engine=ai_engine,
        stop_sequences=None,
        builtinfunctionpersonarelationship_set=FakePersonaBifRelationshipManager(
            (
                tuple(RESOURCE_REGISTRY_BIF_MANIFEST)
                if assigned_function_names is None
                else assigned_function_names
            ),
        ),
    )


def build_request(*, include_session_id: bool = True) -> SimpleNamespace:
    groups = SimpleNamespace(filter=lambda **kwargs: SimpleNamespace(exists=lambda: False))
    session = {"id": "session-1"} if include_session_id else {}
    return SimpleNamespace(
        user=SimpleNamespace(username="dr_bif_tester", is_superuser=False, groups=groups),
        session=session,
        get_host=lambda: "localhost",
    )


def configure_client_for_bif_contracts(client, persona) -> None:
    client.persona = persona
    client.ai_engine = persona.ai_engine
    client.context_mgr = FakeContextManager()
    client.bif_execution_admission_service = FakeExecutionAdmissionService()
    client.getPrompt = lambda request, statement: [{"role": "user", "content": statement}]
    if client.__class__.__name__ == "AnthropicClient":
        client.get_embedding_functions = lambda: ([], {"type": "auto"})
    else:
        client.get_embedding_functions = lambda: ([], "auto")
    client.get_agent_delegation_rule_functions = lambda: []


def extract_tool_names(client_key: str, request_json: dict[str, Any]) -> set[str]:
    if client_key == "openai":
        return {
            tool["function"]["name"]
            for tool in request_json.get("tools", [])
            if isinstance(tool, dict) and "function" in tool
        }
    if client_key == "openai_responses":
        return {
            tool["name"]
            for tool in request_json.get("tools", [])
            if isinstance(tool, dict) and tool.get("type") == "function" and "name" in tool
        }
    if client_key == "gemini":
        names: set[str] = set()
        for wrapper in request_json.get("tools", []):
            for declaration in wrapper.get("function_declarations", []):
                if isinstance(declaration, dict) and "name" in declaration:
                    names.add(declaration["name"])
        return names
    if client_key == "anthropic":
        return {
            tool["name"]
            for tool in request_json.get("tools", [])
            if isinstance(tool, dict) and "name" in tool and "input_schema" in tool
        }
    return set()


def extract_registry_actions(
    client_key: str, request_json: Mapping[str, object]
) -> dict[str, set[str]]:
    """Read grouped actions from provider requests for assignment assertions.

    Args:
        client_key: Provider envelope used by the captured request.
        request_json: Model request containing the advertised tools.

    Returns:
        Published Registry groups mapped to their action names.

    Raises:
        AssertionError: If a legacy Registry name or malformed group is advertised.
    """
    tools = request_json.get("tools", [])
    assert isinstance(tools, list)
    if client_key == "gemini":
        tools = [tool for wrapper in tools for tool in wrapper.get("function_declarations", [])]
    schema_key = {"gemini": "parametersJsonSchema", "anthropic": "input_schema"}.get(
        client_key, "parameters"
    )
    groups: dict[str, set[str]] = {}
    for tool in tools:
        definition = tool.get("function", tool)
        name = definition.get("name", "")
        if not name.startswith("resource_registry_"):
            continue
        assert name in {
            "resource_registry_inspect",
            "resource_registry_query",
            "resource_registry_manage_outputs",
            "resource_registry_manage_transformations",
        }
        actions = definition[schema_key]["properties"]["action"]["enum"]
        assert isinstance(actions, list) and actions
        assert all(isinstance(action, str) for action in actions)
        groups[name] = set(actions)
    return groups


def assert_safe_envelope(test_case, payload: dict[str, Any], *, tenant_id: str) -> None:
    if tenant_id:
        test_case.assertTrue(payload["success"], payload)
        test_case.assertIn("datasets", payload["data"])
    else:
        test_case.assertFalse(payload["success"])
        test_case.assertIn(
            payload.get("code"),
            {
                "authorization_error",
                "capability_error",
                "capability_resolution_error",
                "invalid_tenant_id",
            },
        )

    serialized = json.dumps(payload)
    for marker in SENSITIVE_MARKERS:
        test_case.assertNotIn(marker, serialized)


def json_dumps(value: dict[str, str]) -> str:
    return json.dumps(value)


def build_schema_probe_response(client_key: str) -> FakeResponse:
    if client_key == "openai":
        return FakeResponse(
            {
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"total_tokens": 1, "prompt_tokens": 1, "completion_tokens": 0},
            },
        )
    if client_key == "openai_responses":
        return FakeResponse(
            {
                "output": [{"type": "message", "content": [{"text": "ok"}]}],
                "usage": {"total_tokens": 1, "prompt_tokens": 1, "completion_tokens": 0},
            },
        )
    if client_key == "gemini":
        return FakeResponse(
            {
                "responseId": "rid-1",
                "candidates": [{"content": {"parts": [{"text": "ok"}]}}],
                "usageMetadata": {
                    "totalTokenCount": 1,
                    "promptTokenCount": 1,
                    "candidatesTokenCount": 0,
                },
            },
        )
    return FakeResponse(
        {
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "ok"}],
            "usage": {"input_tokens": 1, "output_tokens": 1},
        },
    )


def build_tool_loop_response(
    client_key: str,
    *,
    tenant_id: str,
    first_call: bool,
) -> FakeResponse:
    if first_call:
        if client_key == "openai":
            return FakeResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {
                                            "name": "resource_registry_list_datasets",
                                            "arguments": json_dumps({"tenant_id": tenant_id}),
                                        },
                                    },
                                ],
                            },
                            "finish_reason": "tool_calls",
                        },
                    ],
                    "usage": {
                        "total_tokens": 10,
                        "prompt_tokens": 7,
                        "completion_tokens": 3,
                    },
                },
            )
        if client_key == "openai_responses":
            return FakeResponse(
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_1",
                            "name": "resource_registry_list_datasets",
                            "arguments": json_dumps({"tenant_id": tenant_id}),
                            "status": "completed",
                        },
                    ],
                    "usage": {
                        "total_tokens": 10,
                        "prompt_tokens": 7,
                        "completion_tokens": 3,
                    },
                },
            )
        if client_key == "gemini":
            return FakeResponse(
                {
                    "responseId": "rid-1",
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "functionCall": {
                                            "name": "resource_registry_list_datasets",
                                            "args": {"tenant_id": tenant_id},
                                        },
                                    },
                                ],
                            },
                        },
                    ],
                    "usageMetadata": {
                        "totalTokenCount": 10,
                        "promptTokenCount": 7,
                        "candidatesTokenCount": 3,
                    },
                },
            )
        return FakeResponse(
            {
                "stop_reason": "tool_use",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call_1",
                        "name": "resource_registry_list_datasets",
                        "input": {"tenant_id": tenant_id},
                    },
                ],
                "usage": {"input_tokens": 7, "output_tokens": 3},
            },
        )

    if client_key == "openai":
        return FakeResponse(
            {
                "choices": [{"message": {"content": "done"}, "finish_reason": "stop"}],
                "usage": {"total_tokens": 5, "prompt_tokens": 3, "completion_tokens": 2},
            },
        )
    if client_key == "openai_responses":
        return FakeResponse(
            {
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "done"}],
                    },
                ],
                "usage": {"total_tokens": 5, "prompt_tokens": 3, "completion_tokens": 2},
            },
        )
    if client_key == "gemini":
        return FakeResponse(
            {
                "responseId": "rid-2",
                "candidates": [{"content": {"parts": [{"text": "done"}]}}],
                "usageMetadata": {
                    "totalTokenCount": 5,
                    "promptTokenCount": 3,
                    "candidatesTokenCount": 2,
                },
            },
        )
    return FakeResponse(
        {
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "done"}],
            "usage": {"input_tokens": 3, "output_tokens": 2},
        },
    )


@contextmanager
def isolate_legacy_registry_routing() -> Iterator[None]:
    """Isolate transport fixtures from database-backed namespace admission.

    These transport tests use unsaved identities. Authorization is exercised separately
    with real users and persisted assignments in the admission and namespace suites.

    Yields:
        Control while routing uses the legacy, already-admitted test context.
    """
    with (
        patch(
            "resource_registry.assistant.runtime_adapter.RegistryAuthorizationContextService"
            ".build_context"
        ),
        patch(
            "resource_registry.assistant.namespace_context.AssistantNamespaceContextService.resolve",
            return_value=AssistantNamespaceResolution(status="unavailable"),
        ),
    ):
        yield
