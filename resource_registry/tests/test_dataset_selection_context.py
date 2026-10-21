"""Verify conversation-scoped dataset bindings across direct and delegated Registry calls."""

from __future__ import annotations

import json
from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.core.cache import cache
from django.test import override_settings

from BotServer import builtin_functions
from BotServer.ai_engine_client import AIEngineClient
from BotServer.builtin_functions import resource_registry_functions
from BotServer.models import FunctionParameter
from resource_registry.assistant import dataset_selection_context as selections
from resource_registry.assistant.dataset_selection_store import load_selections
from resource_registry.assistant.persona_capabilities import DataRegistryPersonaCapabilityService
from resource_registry.assistant.runtime_adapter import DataRegistryExplorationRoutingAdapter
from resource_registry.bifs.read_only_namespace_admission import AdmittedBifNamespace
from resource_registry.read_auth.context import ReadAuthorizationContext

NAMESPACE_A = "11111111-1111-1111-1111-111111111111"
NAMESPACE_B = "99999999-9999-9999-9999-999999999999"
DATASET_A = "22222222-2222-2222-2222-222222222222"
DATASET_B = "33333333-3333-3333-3333-333333333333"
VERSION_A = "44444444-4444-4444-4444-444444444444"
VERSION_B = "55555555-5555-5555-5555-555555555555"
PARAMETERS = frozenset({"tenant_id", "dataset_id", "version_id"})


@pytest.fixture(autouse=True)
def isolate_selection_cache() -> Iterator[None]:
    """Use an in-memory JSON-compatible cache for each isolated scenario."""
    with override_settings(
        CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    ):
        cache.clear()
        yield
        cache.clear()


@pytest.fixture
def admit_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    """Admit the Workspace currently attached to a fake authenticated request."""

    def admit(request: SimpleNamespace, selector: object) -> str:
        workspace = getattr(request, "workspace")
        namespace_id = str(getattr(workspace, "namespace_id"))
        admission = AdmittedBifNamespace(
            namespace_id=namespace_id,
            organization_id=request.organization.pk,
            workspace_id=workspace.pk,
            actor_id=request.user.pk,
        )
        setattr(request, "resource_registry_admitted_namespace", admission)
        return namespace_id

    monkeypatch.setattr(selections, "_admit_current_namespace", admit)


def build_request(
    *,
    user_id: int = 1,
    organization_id: int = 2,
    persona_id: int = 3,
    conversation_id: str = "conversation-a",
    workspace_id: int = 4,
    namespace_id: str = NAMESPACE_A,
    session_key: str = "browser-session",
) -> SimpleNamespace:
    """Build an authenticated request with a trusted root conversation identity."""
    return SimpleNamespace(
        user=SimpleNamespace(pk=user_id),
        organization=SimpleNamespace(pk=organization_id),
        persona=SimpleNamespace(pk=persona_id),
        workspace=SimpleNamespace(pk=workspace_id, namespace_id=namespace_id),
        parent_conversation_id=conversation_id,
        session=SimpleNamespace(session_key=session_key),
        META={},
    )


def remember_dataset(
    request: SimpleNamespace, dataset_id: str, version_id: str | None = None
) -> None:
    """Save one successful dataset-specific Registry operation."""
    arguments = {"tenant_id": request.workspace.namespace_id, "dataset_id": dataset_id}
    if version_id is not None:
        arguments["version_id"] = version_id
    selections._admit_current_namespace(request, arguments["tenant_id"])
    selections.remember_verified_registry_tool_result(
        request=request,
        function_name="resource_registry_get_dataset",
        function_args=arguments,
        result=json.dumps({"success": True, "data": {"dataset": {"id": dataset_id}}}),
    )


def resolve(request: SimpleNamespace, arguments: dict[str, object]) -> dict[str, object]:
    """Resolve one Registry query with its declared dataset and version selectors."""
    return selections.resolve_registry_selection_tool_arguments(
        request=request,
        function_name="resource_registry_query_dataset",
        function_args=arguments,
        parameter_names=PARAMETERS,
    )


def test_direct_agent_preserves_two_datasets_and_versions_across_turns(
    admit_workspace: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Require exact selectors with two references and preserve either exact version."""
    monkeypatch.setattr(selections, "_validate_reused_selection", Mock())
    request = build_request()
    remember_dataset(request, DATASET_A, VERSION_A)
    remember_dataset(request, DATASET_B, VERSION_B)
    next_turn = build_request()

    with pytest.raises(selections.RegistryDatasetSelectionRequired) as error:
        resolve(next_turn, {"tenant_id": ""})
    assert error.value.code == "dataset_selection_required"
    assert resolve(next_turn, {"tenant_id": "", "dataset_id": DATASET_A})["version_id"] == VERSION_A
    assert resolve(next_turn, {"tenant_id": "", "dataset_id": DATASET_B})["version_id"] == VERSION_B
    assert (
        resolve(
            next_turn,
            {"tenant_id": NAMESPACE_A, "dataset_id": DATASET_B, "version_id": VERSION_A},
        )["version_id"]
        == VERSION_A
    )


def test_contextual_dataset_reference_requires_one_verified_dataset(
    admit_workspace: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject ambiguous or unverified aliases before legacy session resolution."""
    monkeypatch.setattr(selections, "_validate_reused_selection", Mock())
    request = build_request()
    remember_dataset(request, DATASET_A, VERSION_A)
    remember_dataset(request, DATASET_B, VERSION_B)

    for reference in ("selected dataset", "dataset actual", ""):
        with pytest.raises(selections.RegistryDatasetSelectionRequired) as error:
            resolve(build_request(), {"dataset_id": reference})
        assert error.value.code == "dataset_selection_required"

    single_request = build_request(conversation_id="single-dataset")
    remember_dataset(single_request, DATASET_A, VERSION_A)
    resolved = resolve(single_request, {"dataset_id": "selected dataset"})
    assert resolved["dataset_id"] == DATASET_A
    assert resolved["version_id"] == VERSION_A

    with pytest.raises(selections.RegistryDatasetSelectionRequired) as error:
        resolve(build_request(conversation_id="unverified"), {"dataset_id": "selected dataset"})
    assert error.value.code == "dataset_selection_required"
    assert resolve(single_request, {"dataset_id": "invalid-id"})["dataset_id"] == "invalid-id"


def test_nested_delegates_and_execute_agent_share_root_scope(
    admit_workspace: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Carry both exact bindings through isolated sessions and a second delegation."""
    monkeypatch.setattr(selections, "_validate_reused_selection", Mock())
    root = build_request()
    remember_dataset(root, DATASET_A, VERSION_A)
    delegate = build_request(persona_id=5, session_key="delegate-session", conversation_id="new")
    selections.inherit_registry_conversation_selection_scope(
        parent_request=root, child_request=delegate, parent_persona_id=3
    )
    remember_dataset(delegate, DATASET_B, VERSION_B)
    nested = build_request(persona_id=6, session_key="nested-session", conversation_id="newer")
    selections.inherit_registry_conversation_selection_scope(
        parent_request=delegate, child_request=nested, parent_persona_id=5
    )
    execute_agent = build_request(persona_id=7, session_key="execute-session")
    selections.inherit_registry_conversation_selection_scope(
        parent_request=nested, child_request=execute_agent, parent_persona_id=6
    )

    assert resolve(nested, {"dataset_id": DATASET_A})["version_id"] == VERSION_A
    assert resolve(execute_agent, {"dataset_id": DATASET_B})["version_id"] == VERSION_B
    execute_scope = selections._get_selection_scope(execute_agent)
    assert execute_scope is not None
    assert execute_scope.conversation_id == root.parent_conversation_id


def test_registry_scope_inheritance_preserves_non_registry_delegate_context() -> None:
    """Leave BotServer conversation scoping unchanged when propagating Registry state."""
    parent = build_request()
    del parent.parent_conversation_id
    delegate = build_request(persona_id=5, session_key="delegate-session")
    del delegate.parent_conversation_id

    selections.inherit_registry_conversation_selection_scope(
        parent_request=parent, child_request=delegate, parent_persona_id=3
    )

    assert not hasattr(delegate, "parent_conversation_id")
    delegate_scope = selections._get_selection_scope(delegate)
    assert delegate_scope is not None
    assert delegate_scope.conversation_id == parent.session.session_key


def test_scope_isolation_workspace_revalidation_and_reset(
    admit_workspace: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep selections within the actor and conversation, including Workspace return."""
    validator = Mock()
    monkeypatch.setattr(selections, "_validate_reused_selection", validator)
    root = build_request()
    remember_dataset(root, DATASET_A, VERSION_A)
    unrelated_requests = (
        build_request(user_id=8),
        build_request(organization_id=8),
        build_request(persona_id=8),
        build_request(conversation_id="another"),
        build_request(session_key="another-session"),
        build_request(workspace_id=8, namespace_id=NAMESPACE_A),
        build_request(workspace_id=8, namespace_id=NAMESPACE_B),
    )
    for unrelated in unrelated_requests:
        assert "dataset_id" not in resolve(unrelated, {})

    other_workspace = build_request(workspace_id=8, namespace_id=NAMESPACE_B)
    remember_dataset(other_workspace, DATASET_B, VERSION_B)
    assert resolve(build_request(), {})["dataset_id"] == DATASET_A
    assert resolve(other_workspace, {})["dataset_id"] == DATASET_B
    assert validator.call_count >= 2

    selections.clear_registry_conversation_selections(root, 3)
    assert "dataset_id" not in resolve(build_request(), {})
    assert "dataset_id" not in resolve(other_workspace, {})


def test_reused_version_blocks_on_revocation_or_not_ready(
    admit_workspace: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return precise failures instead of substituting another version."""
    request = build_request()
    remember_dataset(request, DATASET_A, VERSION_A)
    facade = Mock()
    facade.get_dataset.return_value = SimpleNamespace(status="ACTIVE")
    facade.get_version.return_value = SimpleNamespace(status="PENDING")
    monkeypatch.setattr(selections, "AuthorizedDataRegistryReadFacade", lambda: facade)
    monkeypatch.setattr(
        "BotServer.builtin_functions.resource_registry_functions._build_authorization_context",
        lambda request, namespace_id: ReadAuthorizationContext(tenant_id=namespace_id),
    )

    with pytest.raises(selections.RegistryDatasetSelectionRequired) as error:
        resolve(request, {})
    assert error.value.code == "dataset_version_not_ready"
    with pytest.raises(selections.RegistryDatasetSelectionRequired) as error:
        resolve(request, {"dataset_id": DATASET_A, "version_id": VERSION_A})
    assert error.value.code == "dataset_version_not_ready"
    facade.get_dataset.side_effect = PermissionError("revoked")
    with pytest.raises(selections.RegistryDatasetSelectionRequired) as error:
        resolve(request, {})
    assert error.value.code == "dataset_selection_unavailable"


def test_dataset_listing_and_failed_operation_do_not_create_selection(
    admit_workspace: None,
) -> None:
    """Only a successful dataset-specific operation contributes to the cache."""
    request = build_request()
    selections._admit_current_namespace(request, NAMESPACE_A)
    for function_name, result in (
        ("resource_registry_list_datasets", {"success": True}),
        ("resource_registry_get_dataset", {"success": False}),
    ):
        selections.remember_verified_registry_tool_result(
            request=request,
            function_name=function_name,
            function_args={"dataset_id": DATASET_A},
            result=result,
        )
    assert "dataset_id" not in resolve(request, {})


def test_prompt_and_comparison_hide_revoked_selection(
    admit_workspace: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Expose only currently authorized bindings to both model and comparison routing."""
    request = build_request()
    remember_dataset(request, DATASET_A, VERSION_A)
    remember_dataset(request, DATASET_B, VERSION_B)
    facade = Mock()

    def get_authorized_dataset(
        authorization: ReadAuthorizationContext, dataset_id: str
    ) -> SimpleNamespace:
        """Allow only the first verified dataset in the current authorization context."""
        if dataset_id != DATASET_A:
            raise PermissionError("revoked")
        return SimpleNamespace(status="ACTIVE")

    facade.get_dataset.side_effect = get_authorized_dataset
    facade.get_version.return_value = SimpleNamespace(status="READY")
    monkeypatch.setattr(selections, "AuthorizedDataRegistryReadFacade", lambda: facade)
    monkeypatch.setattr(
        "BotServer.builtin_functions.resource_registry_functions._build_authorization_context",
        lambda request, namespace_id: ReadAuthorizationContext(tenant_id=namespace_id),
    )
    monkeypatch.setattr(
        DataRegistryPersonaCapabilityService,
        "has_resource_registry_capability",
        lambda service, persona: True,
    )

    prompt = selections.get_registry_selection_prompt_context(request, request.persona)
    assert DATASET_A in prompt and VERSION_A in prompt
    assert DATASET_B not in prompt and VERSION_B not in prompt
    assert selections.get_verified_registry_dataset_ids(request, NAMESPACE_A) == (DATASET_A,)


@pytest.mark.parametrize(
    ("has_delegation_rule", "has_execute_agent", "should_show_reference"),
    [(True, False, True), (False, True, True), (False, False, False)],
)
def test_orchestrator_receives_authorized_registry_references(
    admit_workspace: None,
    monkeypatch: pytest.MonkeyPatch,
    has_delegation_rule: bool,
    has_execute_agent: bool,
    should_show_reference: bool,
) -> None:
    """Show verified IDs to delegating agents without direct Registry functions."""
    request = build_request()
    remember_dataset(request, DATASET_A, VERSION_A)

    def filter_assigned_functions(**criteria: object) -> Mock:
        """Expose the executeAgent function only in its matching assignment query."""
        is_execute_agent_query = criteria.get("builtin_function__name__in") == (
            "executeAgent",
            "executeAgentAsync",
        )
        return Mock(exists=Mock(return_value=has_execute_agent and is_execute_agent_query))

    request.persona.builtinfunctionpersonarelationship_set = Mock(
        filter=Mock(side_effect=filter_assigned_functions)
    )
    request.persona.orchestrator = Mock()
    request.persona.orchestrator.filter.return_value.exists.return_value = has_delegation_rule
    monkeypatch.setattr(
        DataRegistryPersonaCapabilityService,
        "has_resource_registry_capability",
        lambda service, persona: False,
    )
    facade = Mock()
    facade.get_dataset.return_value = SimpleNamespace(status="ACTIVE")
    facade.get_version.return_value = SimpleNamespace(status="READY")
    monkeypatch.setattr(selections, "AuthorizedDataRegistryReadFacade", lambda: facade)
    monkeypatch.setattr(
        "BotServer.builtin_functions.resource_registry_functions._build_authorization_context",
        lambda request, namespace_id: ReadAuthorizationContext(tenant_id=namespace_id),
    )

    prompt = selections.get_registry_selection_prompt_context(request, request.persona)

    assert (DATASET_A in prompt and VERSION_A in prompt) is should_show_reference


@pytest.mark.parametrize("grouped", [False, True])
def test_common_bif_dispatch_records_and_reuses_selection(
    grouped: bool,
    admit_workspace: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the shared BotServer dispatch hooks without a database connection."""
    request = build_request()
    client = object.__new__(AIEngineClient)
    client.persona = request.persona
    client.persona.name = "Selection test persona"
    client.context_mgr = Mock()
    function = SimpleNamespace(
        name="resource_registry_get_dataset",
        implementation="",
        display_name="Dataset",
        description="Read dataset",
    )
    client.bif_execution_admission_service = Mock(
        admit=Mock(return_value=SimpleNamespace(function=function, delegation_rule_id=None))
    )
    parameter_manager = Mock()
    parameter_manager.filter.return_value = [
        SimpleNamespace(name="tenant_id", required=True, type="string", description="Namespace"),
        SimpleNamespace(name="dataset_id", required=True, type="string", description="Dataset"),
    ]
    monkeypatch.setattr(FunctionParameter, "objects", parameter_manager)
    response = json.dumps({"success": True, "data": {"dataset": {"id": DATASET_A}}})
    tool = Mock(return_value=response)
    monkeypatch.setattr(resource_registry_functions, "resource_registry_get_dataset", tool)
    monkeypatch.setattr(selections, "_validate_reused_selection", Mock())

    def call_dataset(incoming_request: SimpleNamespace, arguments: dict[str, object]) -> object:
        """Dispatch through either transport while retaining the same conversation scope."""
        return client.callGenericFunction(
            incoming_request,
            "resource_registry_inspect" if grouped else "resource_registry_get_dataset",
            {"action": "get_dataset", "arguments": arguments} if grouped else arguments,
        )

    assert (
        call_dataset(
            request,
            {"tenant_id": NAMESPACE_A, "dataset_id": DATASET_A},
        )
        == response
    )
    next_turn = build_request()
    assert call_dataset(next_turn, {"tenant_id": NAMESPACE_A}) == response
    assert tool.call_args.kwargs["dataset_id"] == DATASET_A
    remember_dataset(next_turn, DATASET_B, VERSION_B)
    ambiguous_result = call_dataset(
        next_turn,
        {"tenant_id": NAMESPACE_A, "dataset_id": "selected dataset"},
    )
    assert json.loads(ambiguous_result)["code"] == "dataset_selection_required"
    assert tool.call_count == 2


def test_non_registry_bif_dispatch_skips_registry_selection_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep ordinary agent tool calls independent of Registry selection queries."""
    request = build_request()
    client = object.__new__(AIEngineClient)
    client.persona = request.persona
    client.persona.name = "Selection test persona"
    client.context_mgr = Mock()
    client.appLogger = Mock()
    function = SimpleNamespace(
        name="plain_echo", implementation="", display_name="Echo", description="Echo"
    )
    client.bif_execution_admission_service = Mock(
        admit=Mock(return_value=SimpleNamespace(function=function, delegation_rule_id=None))
    )
    parameter_manager = Mock()
    parameter_manager.filter.return_value = []
    monkeypatch.setattr(FunctionParameter, "objects", parameter_manager)
    tool = Mock(return_value="plain-result")
    monkeypatch.setattr(builtin_functions, "plain_echo", tool, raising=False)
    monkeypatch.setattr(
        "BotServer.ai_engine_client.resolve_registry_selection_tool_arguments",
        Mock(side_effect=AssertionError("Registry selection must not run")),
    )
    monkeypatch.setattr(
        "BotServer.ai_engine_client.remember_verified_registry_tool_result",
        Mock(side_effect=AssertionError("Registry selection must not run")),
    )

    assert client.callGenericFunction(request, "plain_echo", {}) == "plain-result"
    parameter_manager.filter.assert_called_once_with(function=function, required=True)
    tool.assert_called_once_with(request, client.context_mgr)


def test_schema_result_pins_exact_version_and_discards_versionless_entry(
    admit_workspace: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Capture the version returned by a specific read even when it was omitted."""
    monkeypatch.setattr(selections, "_validate_reused_selection", Mock())
    request = build_request()
    remember_dataset(request, DATASET_A)
    selections.remember_verified_registry_tool_result(
        request=request,
        function_name="resource_registry_get_dataset_schema",
        function_args={"tenant_id": NAMESPACE_A, "dataset_id": DATASET_A},
        result={
            "success": True,
            "data": {"schema": {"dataset_id": DATASET_A, "dataset_version_id": VERSION_A}},
        },
    )
    assert resolve(build_request(), {"dataset_id": DATASET_A})["version_id"] == VERSION_A
    scope = selections._get_selection_scope(request)
    assert scope is not None
    assert len(load_selections(scope)) == 1


def test_query_result_pins_executed_version_across_turns(
    admit_workspace: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reuse the version returned by a query instead of a later latest version."""
    original_validator = selections._validate_reused_selection
    validator = Mock()
    monkeypatch.setattr(selections, "_validate_reused_selection", validator)
    request = build_request()
    selections.remember_verified_registry_tool_result(
        request=request,
        function_name="resource_registry_query_dataset",
        function_args={"tenant_id": NAMESPACE_A, "dataset_id": DATASET_A},
        result={"success": True, "dataset_id": DATASET_A, "version_id": VERSION_A},
    )

    next_turn = build_request()
    resolved = resolve(next_turn, {"dataset_id": DATASET_A})
    assert resolved["version_id"] == VERSION_A
    assert validator.call_args.kwargs["check_version"] is True

    facade = Mock()
    facade.get_dataset.return_value = SimpleNamespace(status="ACTIVE")
    facade.get_version.return_value = SimpleNamespace(status="PENDING")
    monkeypatch.setattr(selections, "AuthorizedDataRegistryReadFacade", lambda: facade)
    monkeypatch.setattr(selections, "_validate_reused_selection", original_validator)
    monkeypatch.setattr(
        "BotServer.builtin_functions.resource_registry_functions._build_authorization_context",
        lambda request, namespace_id: ReadAuthorizationContext(tenant_id=namespace_id),
    )
    with pytest.raises(selections.RegistryDatasetSelectionRequired) as error:
        resolve(next_turn, {"dataset_id": DATASET_A})
    assert error.value.code == "dataset_version_not_ready"


def test_comparison_uses_verified_selections_instead_of_legacy_listing(
    admit_workspace: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep a mere listing from becoming an implicit multi-dataset selection."""
    monkeypatch.setattr(selections, "_validate_reused_selection", Mock())
    request = build_request()
    request.session.resource_registry_selected_dataset_ids = [DATASET_B]
    remember_dataset(request, DATASET_A, VERSION_A)

    identifiers = DataRegistryExplorationRoutingAdapter()._comparison_dataset_ids(
        request=request,
        statement="Compare my verified datasets.",
        tenant_id=NAMESPACE_A,
    )

    assert identifiers == (DATASET_A,)
