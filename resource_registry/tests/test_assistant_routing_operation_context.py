"""Verify trusted operation-local context reuse during assistant routing."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest

from resource_registry.assistant.namespace_context import (
    AssistantNamespaceContextService,
    AssistantNamespaceResolution,
    InvalidAssistantRoutingOperationContextError,
)
from resource_registry.assistant.routing_operation_context import (
    AssistantRoutingOperationContext,
)
from resource_registry.assistant.runtime_adapter import DataRegistryExplorationRoutingAdapter
from resource_registry.authorization.contracts import (
    AuthorizedNamespace,
    AuthorizedNamespaceSet,
    AuthorizedWorkspaceSet,
    RegistryAuthorizationContext,
)
from resource_registry.authorization.registry_authorization_context_service import (
    RegistryAuthorizationContextService,
)

_NAMESPACE_ID = UUID("11111111-1111-1111-1111-111111111111")


class _CountingAuthorizationContextService:
    """Return one fixed authorization context while counting fresh builds."""

    def __init__(self, context: RegistryAuthorizationContext) -> None:
        """Initialize the service with one trusted context.

        Args:
            context: Immutable authorization context returned for every request.
        """
        self.context = context
        self.build_count = 0

    def build_context(self, *, user: object, organization: object) -> RegistryAuthorizationContext:
        """Return the fixed context after recording one trusted build request.

        Args:
            user: Authenticated principal supplied by the request.
            organization: Host-resolved Organization supplied by the request.

        Returns:
            The fixture's immutable authorization context.
        """
        _ = user, organization
        self.build_count += 1
        return self.context


class _ResourceRegistryRelationshipManager:
    """Expose a configured Registry BIF relationship for Persona scope resolution."""

    def filter(self, **_kwargs: object) -> SimpleNamespace:
        """Return an assigned relationship query result.

        Args:
            **_kwargs: Relationship query constraints.

        Returns:
            An object reporting that a matching BIF assignment exists.
        """
        return SimpleNamespace(exists=lambda: True)


class _CountingWorkspaceManager:
    """Record configured Persona Workspace ID retrievals."""

    def __init__(self, workspace_ids: tuple[int, ...]) -> None:
        """Store immutable configured Workspace identifiers.

        Args:
            workspace_ids: Persona-assigned Workspace primary keys.
        """
        self._workspace_ids = workspace_ids
        self.values_list_count = 0

    def values_list(self, field_name: str, *, flat: bool) -> tuple[int, ...]:
        """Return configured Workspace identifiers once per explicit retrieval.

        Args:
            field_name: Requested model field name.
            flat: Whether scalar identifiers are requested.

        Returns:
            Configured Workspace identifiers.
        """
        assert field_name == "pk"
        assert flat is True
        self.values_list_count += 1
        return self._workspace_ids


class _RecordingNamespaceContextService:
    """Capture the adapter's internal context handoff without dispatching a BIF."""

    def __init__(self) -> None:
        """Initialize the captured context and fixed ambiguous resolution."""
        self.created_contexts: list[object] = []

    def _create_routing_operation_context(
        self,
        *,
        request: object,
        authorization_context: RegistryAuthorizationContext,
        persona_workspace_scope: object,
        configured_persona_workspace_ids: frozenset[int] | None,
    ) -> object:
        """Record the Registry-owned handoff inputs.

        Args:
            request: Trusted request carrying principal and Organization identities.
            authorization_context: Fresh Registry authorization context.
            persona_workspace_scope: Persona-derived Workspace scope.
            configured_persona_workspace_ids: Immutable Persona Workspace restriction.

        Returns:
            Opaque test-only operation context.
        """
        context = SimpleNamespace(
            request=request,
            authorization_context=authorization_context,
            persona_workspace_scope=persona_workspace_scope,
            configured_persona_workspace_ids=configured_persona_workspace_ids,
        )
        self.created_contexts.append(context)
        return context

    def _resolve_with_routing_operation_context(
        self,
        *,
        request: object,
        statement: str,
        operation_context: object,
        required_workspace_ids: frozenset[int] | None,
        require_explicit_workspace_selection: bool,
    ) -> AssistantNamespaceResolution:
        """Return a deterministic ambiguity before any shared dispatch boundary.

        Args:
            request: Trusted current request.
            statement: Assistant statement being resolved.
            operation_context: Opaque Registry-owned operation context.
            required_workspace_ids: Persona Workspace restriction.
            require_explicit_workspace_selection: Explicit-selection policy flag.

        Returns:
            An ambiguous resolution with no tool execution.
        """
        _ = request, statement, operation_context, required_workspace_ids
        assert require_explicit_workspace_selection is False
        return AssistantNamespaceResolution(status="multiple")


class _RejectingNamespaceContextService(_RecordingNamespaceContextService):
    """Reject a context after adapter setup to verify its closed failure boundary."""

    def clear_context(self, request: object) -> int:
        """Record no state while satisfying the Registry cleanup contract.

        Args:
            request: Request whose Registry state would be cleared.

        Returns:
            Zero because this in-memory fixture has no persisted state.
        """
        _ = request
        return 0

    def _resolve_with_routing_operation_context(
        self,
        *,
        request: object,
        statement: str,
        operation_context: object,
        required_workspace_ids: frozenset[int] | None,
        require_explicit_workspace_selection: bool,
    ) -> AssistantNamespaceResolution:
        """Reject the internal handoff before any dispatcher call.

        Args:
            request: Trusted current request.
            statement: Assistant statement being resolved.
            operation_context: Opaque Registry-owned operation context.
            required_workspace_ids: Persona Workspace restriction.
            require_explicit_workspace_selection: Explicit-selection policy flag.

        Returns:
            This method never returns because the context is rejected.

        Raises:
            InvalidAssistantRoutingOperationContextError: Always, to simulate failed validation.
        """
        _ = (
            request,
            statement,
            operation_context,
            required_workspace_ids,
            require_explicit_workspace_selection,
        )
        raise InvalidAssistantRoutingOperationContextError("forged fixture context")


class _NoOpContextManager:
    """Provide the context-manager cleanup surface without retaining Registry state."""

    def get_all_registry_entries(self, request: object) -> dict[str, object]:
        """Return no managed Registry entries.

        Args:
            request: Current assistant request.

        Returns:
            Empty Registry context mapping.
        """
        _ = request
        return {}

    def remove_registry_entry(self, request: object, key: str) -> None:
        """Accept cleanup of a nonexistent Registry entry.

        Args:
            request: Current assistant request.
            key: Registry context key to remove.
        """
        _ = request, key


def _authorization_context() -> RegistryAuthorizationContext:
    """Build a fixed immutable authorization fixture.

    Returns:
        Authorization for principal 101 in Organization 202 and Workspace 7.
    """
    return RegistryAuthorizationContext(
        authorized_workspaces=AuthorizedWorkspaceSet(
            organization_id=202,
            user_id=101,
            workspace_ids=frozenset({7}),
        ),
        authorized_namespaces=AuthorizedNamespaceSet(
            mappings=(AuthorizedNamespace(namespace_id=_NAMESPACE_ID, workspace_id=7),),
        ),
    )


def _request(*, principal_id: int = 101, organization_id: int = 202) -> SimpleNamespace:
    """Build a trusted request-shaped fixture with explicit identities.

    Args:
        principal_id: Persisted authenticated principal identifier.
        organization_id: Persisted active Organization identifier.

    Returns:
        A mutable request-shaped fixture.
    """
    return SimpleNamespace(
        user=SimpleNamespace(pk=principal_id, is_authenticated=True, is_active=True),
        organization=SimpleNamespace(pk=organization_id, is_active=True),
        workspace=SimpleNamespace(pk=7, organization_id=organization_id, is_active=True),
        session={},
    )


def test_adapter_builds_authorization_and_configured_workspace_ids_once() -> None:
    """Reuse one trusted setup context before returning Workspace ambiguity guidance."""
    authorization_service = _CountingAuthorizationContextService(_authorization_context())
    namespace_service = _RecordingNamespaceContextService()
    workspace_manager = _CountingWorkspaceManager((7,))
    client = SimpleNamespace(
        persona=SimpleNamespace(
            builtinfunctionpersonarelationship_set=_ResourceRegistryRelationshipManager(),
            workspaces=workspace_manager,
        ),
        context_mgr=SimpleNamespace(),
    )
    adapter = DataRegistryExplorationRoutingAdapter(
        authorization_context_service=cast(
            RegistryAuthorizationContextService, authorization_service
        ),
        namespace_context_service=namespace_service,  # type: ignore[arg-type]
    )
    adapter._has_resource_registry_capability = lambda _client: True  # type: ignore[method-assign, assignment]

    result = adapter.try_route(
        client=client,
        request=_request(),
        statement="List the datasets available to me.",
    )

    assert result is not None
    assert result.request_payload["status"] == "workspace_selection_required"
    assert authorization_service.build_count == 1
    assert workspace_manager.values_list_count == 1
    context = cast(AssistantRoutingOperationContext, namespace_service.created_contexts[0])
    assert context.configured_persona_workspace_ids == frozenset({7})


def test_standalone_namespace_resolution_builds_a_fresh_authorization_context() -> None:
    """Keep the public resolver independent of adapter operation-local state."""
    no_namespace_context = RegistryAuthorizationContext(
        authorized_workspaces=AuthorizedWorkspaceSet(
            organization_id=202,
            user_id=101,
            workspace_ids=frozenset(),
        ),
        authorized_namespaces=AuthorizedNamespaceSet(mappings=()),
    )
    authorization_service = _CountingAuthorizationContextService(no_namespace_context)
    service = AssistantNamespaceContextService(
        authorization_context_service=authorization_service  # type: ignore[arg-type]
    )

    resolution = service.resolve(request=_request(), statement="List the datasets available to me.")

    assert resolution.status == "none"
    assert authorization_service.build_count == 1


def test_adapter_rejects_invalid_internal_context_before_shared_dispatch() -> None:
    """Return the fail-closed denial envelope without invoking a BIF dispatcher."""
    authorization_service = _CountingAuthorizationContextService(_authorization_context())
    namespace_service = _RejectingNamespaceContextService()
    workspace_manager = _CountingWorkspaceManager((7,))
    dispatcher_calls: list[tuple[str, dict[str, object]]] = []

    def record_dispatch(
        _request: object,
        function_name: str,
        function_args: dict[str, object],
    ) -> str:
        """Record an unexpected shared-dispatch invocation.

        Args:
            _request: Current assistant request.
            function_name: Requested BIF name.
            function_args: Requested BIF arguments.

        Returns:
            This function never returns because dispatch is not allowed in this test.
        """
        dispatcher_calls.append((function_name, function_args))
        raise AssertionError("Invalid operation context reached shared dispatch.")

    client = SimpleNamespace(
        persona=SimpleNamespace(
            builtinfunctionpersonarelationship_set=_ResourceRegistryRelationshipManager(),
            workspaces=workspace_manager,
        ),
        context_mgr=_NoOpContextManager(),
        callGenericFunction=record_dispatch,
    )
    adapter = DataRegistryExplorationRoutingAdapter(
        authorization_context_service=cast(
            RegistryAuthorizationContextService, authorization_service
        ),
        namespace_context_service=namespace_service,  # type: ignore[arg-type]
    )
    adapter._has_resource_registry_capability = lambda _client: True  # type: ignore[method-assign, assignment]

    result = adapter.try_route(
        client=client,
        request=_request(),
        statement="List the datasets available to me.",
    )

    assert result is not None
    assert result.request_payload["error_code"] == "invalid_resource_registry_routing_context"
    assert dispatcher_calls == []


@pytest.mark.parametrize(
    ("principal_id", "organization_id"),
    ((303, 202), (101, 404)),
)
def test_internal_context_rejects_principal_and_organization_mismatch(
    principal_id: int,
    organization_id: int,
) -> None:
    """Fail closed before Workspace resolution when request identity changes.

    Args:
        principal_id: Principal identity to use after context construction.
        organization_id: Organization identity to use after context construction.
    """
    service = AssistantNamespaceContextService()
    trusted_request = _request()
    operation_context = service._create_routing_operation_context(
        request=trusted_request,
        authorization_context=_authorization_context(),
        persona_workspace_scope=None,
        configured_persona_workspace_ids=None,
    )

    with pytest.raises(InvalidAssistantRoutingOperationContextError):
        service._resolve_with_routing_operation_context(
            request=_request(principal_id=principal_id, organization_id=organization_id),
            statement="List the datasets available to me.",
            operation_context=operation_context,
            required_workspace_ids=None,
            require_explicit_workspace_selection=False,
        )


def test_internal_context_rejects_a_forged_value_before_workspace_lookup() -> None:
    """Reject arbitrary values rather than accepting caller-controlled authorization snapshots."""
    service = AssistantNamespaceContextService()

    with pytest.raises(InvalidAssistantRoutingOperationContextError):
        service._resolve_with_routing_operation_context(
            request=_request(),
            statement="List the datasets available to me.",
            operation_context=cast(AssistantRoutingOperationContext, SimpleNamespace()),
            required_workspace_ids=None,
            require_explicit_workspace_selection=False,
        )


def test_concurrent_requests_receive_distinct_immutable_operation_contexts() -> None:
    """Keep operation-local contexts isolated across concurrent request setup."""
    service = AssistantNamespaceContextService()

    def create_context(principal_id: int) -> object:
        """Create one operation context with a principal-specific immutable authorization.

        Args:
            principal_id: Authenticated principal assigned to the synthetic request.

        Returns:
            Newly created operation-local context.
        """
        authorization_context = RegistryAuthorizationContext(
            authorized_workspaces=AuthorizedWorkspaceSet(
                organization_id=202,
                user_id=principal_id,
                workspace_ids=frozenset({7}),
            ),
            authorized_namespaces=AuthorizedNamespaceSet(
                mappings=(AuthorizedNamespace(namespace_id=_NAMESPACE_ID, workspace_id=7),),
            ),
        )
        return service._create_routing_operation_context(
            request=_request(principal_id=principal_id),
            authorization_context=authorization_context,
            persona_workspace_scope=None,
            configured_persona_workspace_ids=frozenset({7}),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        contexts = [
            cast(AssistantRoutingOperationContext, context)
            for context in executor.map(create_context, (101, 102))
        ]

    assert contexts[0] is not contexts[1]
    assert {context.principal_id for context in contexts} == {101, 102}
    assert all(
        isinstance(context.configured_persona_workspace_ids, frozenset) for context in contexts
    )
