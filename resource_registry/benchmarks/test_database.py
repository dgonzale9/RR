"""Explicit pytest-only database benchmarks using an isolated test database."""

from __future__ import annotations

import json
import logging
import os
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from django.contrib.auth.models import Permission, User
from django.core.cache import cache
from django.db import close_old_connections, connections
from django.test import override_settings

from BotServer.ai_engine_client import AIEngineClient
from BotServer.bif_execution_admission import (
    BuiltInFunctionExecutionAdmissionService,
    BuiltInFunctionExecutionChannel,
)
from BotServer.models import (
    AiEngine,
    AiModel,
    AiVendor,
    BuiltInFunction,
    BuiltInFunctionPersonaRelationship,
    FunctionParameter,
    Persona,
)
from Organization.models import Organization, Workspace
from resource_registry.application.read import (
    TransformationDiagnosticsReadService,
    TransformationOutputReadService,
    TransformationRunReadService,
    TransformationStepReadService,
)
from resource_registry.application.read.authorization import TransformationReadAuthorizationService
from resource_registry.application.services import TransformationExecutionApplicationService
from resource_registry.assistant.dataset_selection_context import (
    get_registry_selection_prompt_context,
    get_verified_registry_dataset_ids,
)
from resource_registry.assistant.dataset_selection_store import (
    RegistryConversationSelectionScope,
    VerifiedDatasetSelection,
    save_selections,
)
from resource_registry.assistant.persona_workspace_scope import (
    PERSONA_WORKSPACE_SELECTION_REQUEST_ATTRIBUTE,
    PERSONA_WORKSPACE_SELECTION_SESSION_KEY,
)
from resource_registry.assistant.runtime_adapter import DataRegistryExplorationRoutingAdapter
from resource_registry.benchmarks.epic4_catalog import (
    EPIC4_ARTIFACT_SET,
    EPIC4_CATALOG_SCENARIO_BOUNDARIES,
    EPIC4_DATABASE_SCENARIO_NAMES,
    measure_memory_usage,
)
from resource_registry.benchmarks.epic5_selection import (
    EPIC5_ARTIFACT_SET,
    EPIC5_CANDIDATE_ARTIFACT_SET,
    EPIC5_DATABASE_SCENARIO_NAMES,
    EPIC5_SELECTION_SCALE_COUNTS,
    EPIC5_SELECTION_SCENARIO_BOUNDARIES,
)
from resource_registry.benchmarks.epic6_grouped_result import (
    EPIC6_CANONICAL_SCENARIO_NAMES,
    EPIC6_GROUPED_RESULT_ARTIFACT_SET,
    EPIC6_SCENARIO_BOUNDARIES,
    EPIC6_TRIAL_COUNT,
    build_epic6_not_measured_canonical_results,
)
from resource_registry.benchmarks.epic6_grouped_result_memory import (
    EPIC6_GROUPED_RESULT_MEMORY_ARTIFACT_SET,
    EPIC6_MEMORY_CANONICAL_SCENARIO_NAMES,
    EPIC6_MEMORY_TRACING_CONFIGURATION,
    EPIC6_MEMORY_TRIAL_COUNT,
    measure_epic6_memory_operation,
)
from resource_registry.benchmarks.grouped_execution import (
    CANONICAL_GROUPED_EXECUTION_SCENARIO_NAMES,
    GROUPED_EXECUTION_SCENARIO_BOUNDARIES,
    REQUIRED_GROUPED_EXECUTION_SCENARIO_NAMES,
)
from resource_registry.benchmarks.runner import (
    DATABASE_ADAPTER_ROUTING_SCENARIO_NAMES,
    get_benchmark_artifact_paths,
    get_benchmark_artifact_set,
    persist_baseline,
    run_concurrent_database_scenario,
    run_scenario,
    temporary_performance_configuration,
)
from resource_registry.benchmarks.scenarios import BenchmarkScenario
from resource_registry.bifs.grouped_publication import (
    build_registry_operation_definition,
    prepare_registry_tools,
)
from resource_registry.bifs.grouped_runtime import execute_grouped_registry_call
from resource_registry.bifs.grouped_tools import (
    FunctionToolDefinition,
    is_valid_grouped_registry_arguments,
    project_registry_tool_result,
)
from resource_registry.bifs.manifest import RESOURCE_REGISTRY_BIF_MANIFEST
from resource_registry.bifs.transformation.context import TransformationBifTrustedContextBuilder
from resource_registry.bifs.transformation.errors import TransformationBifErrorMapper
from resource_registry.bifs.transformation.handlers import TransformationBifApplicationServices
from resource_registry.models import (
    Dataset,
    DatasetSourceType,
    DatasetStatus,
    DatasetVersion,
    DatasetVersionStatus,
    DatasetVisibilityScope,
)
from resource_registry.services.registry_namespace_provisioning_service import (
    RegistryNamespaceProvisioningService,
)
from resource_registry.tests.workspace_permission_helpers import add_workspace_user

_BENCHMARK_OUTPUT, _BENCHMARK_REPORT = get_benchmark_artifact_paths()
_PRE_E21_BENCHMARK_OUTPUT = Path(__file__).resolve().parent / "baseline_results.json"
_SAFE_LOCAL_HOSTS = frozenset({"", "localhost", "127.0.0.1", "::1"})
_ADAPTER_COMPARISON_METRICS = (
    "p50_ms",
    "p95_ms",
    "p99_ms",
    "sql_query_count",
    "sql_duration_ms",
    "authorization_context_build_count_per_operation",
    "error_count",
)


def validate_isolated_database_target(
    *, database_name: str, database_host: str, read_database_host: str
) -> None:
    """Reject a benchmark target that is not the sanctioned disposable database.

    Args:
        database_name: Configured primary database name.
        database_host: Configured primary database host.
        read_database_host: Configured read-replica host.

    Raises:
        RuntimeError: If the target could reach a non-local or non-isolated database.
    """
    if (
        database_host not in _SAFE_LOCAL_HOSTS
        or bool(read_database_host)
        or database_name != "RegistryE14Isolated"
    ):
        raise RuntimeError(
            "Database benchmark requires the named isolated database on a local host."
        )


if os.environ.get("RESOURCE_REGISTRY_BENCHMARK_DATABASE") == "1":
    validate_isolated_database_target(
        database_name=os.environ.get("DB_DATABASE", ""),
        database_host=os.environ.get("DB_HOST", ""),
        read_database_host=os.environ.get("DB_READ_HOST", ""),
    )


@dataclass(frozen=True)
class _DatabaseFixture:
    """Hold only isolated pytest database objects for the measured workload."""

    user: User
    organization: Organization
    workspace: Workspace
    persona: Persona
    namespace_id: uuid.UUID
    datasets: tuple[Dataset, ...]
    request: SimpleNamespace
    scope: RegistryConversationSelectionScope


class _BenchmarkSession(dict[str, object]):
    """Provide an isolated dict-like session with an existing session key."""

    def __init__(self, namespace_id: str | None) -> None:
        """Initialize a test-local session with explicit Workspace selection.

        Args:
            namespace_id: Selected, authorized benchmark namespace, if any.
        """
        super().__init__()
        if namespace_id is not None:
            self.update(
                {
                    "resource_registry_tenant_id": namespace_id,
                    PERSONA_WORKSPACE_SELECTION_SESSION_KEY: namespace_id,
                    "resource_registry_roles": ["restricted_reader"],
                }
            )
        self.session_key = "isolated-benchmark-session"


class _AvailableTransformationGovernance:
    """Provide deterministic tenant governance for read-only isolated benchmark calls."""

    def is_available(self, *, tenant_id: str) -> bool:
        """Allow the already-admitted disposable tenant without contacting external services.

        Args:
            tenant_id: Canonical tenant ID admitted by the live BIF namespace service.

        Returns:
            Whether the benchmark's local governance dependency is available.
        """
        return bool(tenant_id)


def _build_transformation_bif_services() -> TransformationBifApplicationServices:
    """Build real read-only transformation dependencies for canonical benchmark dispatch.

    Returns:
        Handler dependencies whose invoked path is the real run-list read service.
    """
    authorization = TransformationReadAuthorizationService(
        governance=_AvailableTransformationGovernance()
    )
    return TransformationBifApplicationServices(
        execution=cast(TransformationExecutionApplicationService, object()),
        runs=TransformationRunReadService(authorization=authorization),
        steps=cast(TransformationStepReadService, object()),
        outputs=cast(TransformationOutputReadService, object()),
        diagnostics=cast(TransformationDiagnosticsReadService, object()),
        context_builder=TransformationBifTrustedContextBuilder(),
        error_mapper=TransformationBifErrorMapper(),
    )


def _create_database_fixture(
    *,
    fixture_index: int = 0,
    dataset_count: int = 50,
) -> _DatabaseFixture:
    """Create a real member, Persona assignment, namespace, and bounded datasets.

    Args:
        fixture_index: Stable isolated-fixture suffix for independent benchmark trials.
        dataset_count: Number of active workspace-visible datasets to persist.

    Returns:
        Isolated test-database fixtures; no external service is contacted.
    """
    fixture_suffix = f"-{fixture_index}" if fixture_index else ""
    user = User.objects.create_user(username=f"registry-benchmark-member{fixture_suffix}")
    organization = Organization.objects.create(
        name=f"Registry Benchmark Organization{fixture_suffix}",
        slug=f"registry-benchmark-organization{fixture_suffix}",
        last_modified_by=user,
    )
    Organization.objects.filter(pk=organization.pk).update(resource_registry_enabled=True)
    organization.refresh_from_db()
    workspace = Workspace.objects.create(
        organization=organization,
        name=f"Registry Benchmark Workspace{fixture_suffix}",
        slug=f"registry-benchmark-workspace{fixture_suffix}",
        last_modified_by=user,
    )
    Workspace.objects.filter(pk=workspace.pk).update(resource_registry_enabled=True)
    workspace.refresh_from_db()
    add_workspace_user(workspace=workspace, user=user)
    namespace_id = uuid.UUID(int=801 + fixture_index)
    RegistryNamespaceProvisioningService().provision_verified_existing_namespace(
        workspace_id=workspace.pk, namespace_id=namespace_id
    )
    vendor = AiVendor.objects.create(
        vendor_name=f"registry-benchmark-vendor{fixture_suffix}",
        endpoint_url="https://example.invalid",
        implementation_file="unused",
        implementation_class="Unused",
        last_modified_by=user,
    )
    model = AiModel.objects.create(
        vendor_model=f"registry-benchmark-model{fixture_suffix}",
        vendor=vendor,
        last_modified_by=user,
    )
    engine = AiEngine.objects.create(
        name=f"registry-benchmark-engine{fixture_suffix}",
        ai_model=model,
        api_key="not-a-real-key",
        last_modified_by=user,
    )
    persona = Persona.objects.create(
        name=f"registry-benchmark-persona{fixture_suffix}",
        display_description="Isolated benchmark Persona",
        ai_engine=engine,
        temperature=0,
        frequency_penalty=0,
        presence_penalty=0,
        last_modified_by=user,
    )
    persona.workspaces.add(workspace)
    transformation_read_permission = Permission.objects.get(
        content_type__app_label="resource_registry",
        codename="view_transformationrun",
    )
    user.user_permissions.add(transformation_read_permission)
    if hasattr(user, "_perm_cache"):
        delattr(user, "_perm_cache")
    action_parameters = {
        "list_datasets": (("tenant_id", "string", True), ("limit", "integer", False)),
        "get_dataset": (("tenant_id", "string", True), ("dataset_id", "string", True)),
        "get_dataset_schema": (
            ("tenant_id", "string", True),
            ("dataset_id", "string", True),
            ("version_id", "string", False),
        ),
        "query_dataset": (("tenant_id", "string", True),),
        "list_transformation_runs": (
            ("tenant_id", "string", True),
            ("limit", "integer", False),
            ("offset", "integer", False),
            ("status", "string", False),
            ("definition_id", "string", False),
            ("definition_version_id", "string", False),
            ("created_after", "string", False),
            ("created_before", "string", False),
            ("correlation_id", "string", False),
        ),
        "start_transformation_run": (
            ("tenant_id", "string", False),
            ("definition_id", "string", True),
            ("definition_version_id", "string", True),
            ("parameters", "object", True),
            ("input_bindings", "array", True),
            ("idempotency_key", "string", True),
            ("correlation_id", "string", False),
        ),
    }
    for action, parameters in action_parameters.items():
        function, _ = BuiltInFunction.objects.get_or_create(
            name=f"resource_registry_{action}",
            defaults={
                "description": "Isolated Registry benchmark operation.",
                "last_modified_by": user,
            },
        )
        BuiltInFunctionPersonaRelationship.objects.create(
            persona=persona, builtin_function=function, last_modified_by=user
        )
        for parameter_name, parameter_type, parameter_required in parameters:
            FunctionParameter.objects.get_or_create(
                name=parameter_name,
                function=function,
                defaults={
                    "type": parameter_type,
                    "required": parameter_required,
                    "description": "Isolated Registry benchmark parameter.",
                },
            )
    BuiltInFunction.objects.get_or_create(
        name="resource_registry_explore_dataset",
        defaults={
            "description": "Unassigned Registry benchmark operation.",
            "last_modified_by": user,
        },
    )
    datasets = tuple(
        Dataset.objects.create(
            id=uuid.UUID(int=1_000 + fixture_index * 100 + index),
            tenant_id=namespace_id,
            organization=organization,
            workspace=workspace,
            visibility_scope=DatasetVisibilityScope.WORKSPACE,
            slug=f"benchmark-dataset-{index}",
            name=f"Benchmark dataset {index}",
            source_type=DatasetSourceType.FILE_UPLOAD,
            status=DatasetStatus.ACTIVE,
        )
        for index in range(dataset_count)
    )
    request = SimpleNamespace(
        user=user,
        organization=organization,
        workspace=workspace,
        persona=persona,
        session=_BenchmarkSession(str(namespace_id)),
        parent_conversation_id="isolated-benchmark-conversation",
        resource_registry_tenant_id=str(namespace_id),
        META={},
    )
    request.transformation_bif_services = _build_transformation_bif_services()
    setattr(request, PERSONA_WORKSPACE_SELECTION_REQUEST_ATTRIBUTE, str(namespace_id))
    scope = RegistryConversationSelectionScope(
        user_id=user.pk,
        organization_id=organization.pk,
        root_persona_id=persona.pk,
        conversation_id=request.parent_conversation_id,
        browser_session_key=request.session.session_key,
    )
    return _DatabaseFixture(
        user, organization, workspace, persona, namespace_id, datasets, request, scope
    )


class _AdapterBenchmarkContextManager:
    """Provide only the Registry conversation facade required by the live adapter."""

    def __init__(self) -> None:
        """Initialize an empty Registry-owned conversation entry mapping."""
        self._registry_entries: dict[str, object] = {}

    def get_all_registry_entries(self, request: object) -> dict[str, object]:
        """Return the current Registry-owned entries without external state.

        Args:
            request: Current assistant request.

        Returns:
            Copy of the local Registry entry mapping.
        """
        _ = request
        return dict(self._registry_entries)

    def remove_registry_entry(self, request: object, key: str) -> None:
        """Remove one Registry-owned entry when the adapter invalidates it.

        Args:
            request: Current assistant request.
            key: Registry entry key to remove.
        """
        _ = request
        self._registry_entries.pop(key, None)

    def update_current_context_entry(
        self,
        request: object,
        *,
        function: dict[str, object],
    ) -> None:
        """Accept deterministic tool-result context without retaining result payloads.

        Args:
            request: Current assistant request.
            function: Compact tool-result envelope emitted by the live adapter.
        """
        _ = request, function


class _SharedDispatcherBoundaryStub:
    """Stub only the shared BotServer dispatcher boundary after adapter routing completes."""

    def __init__(self, responses: Mapping[str, Mapping[str, object]]) -> None:
        """Initialize deterministic provider-free terminal tool responses.

        Args:
            responses: JSON-compatible BIF payloads indexed by canonical function name.
        """
        self._responses = responses
        self.call_count = 0
        self.function_names: list[str] = []

    def call_generic_function(
        self,
        request: object,
        function_name: str,
        function_args: dict[str, object],
    ) -> str:
        """Return a configured response at the shared dispatcher boundary.

        The adapter's preceding Registry authorization, Persona scope, Workspace resolution,
        and routing policy execute unchanged. This method replaces the shared dispatcher and
        does not enter canonical BIF admission, BIF handlers, providers, or external services.

        Args:
            request: Current assistant request.
            function_name: Canonical BIF selected by the live adapter.
            function_args: Canonical arguments produced by the live adapter.

        Returns:
            JSON-encoded terminal result configured for the selected BIF.

        Raises:
            AssertionError: If the live adapter selects an unexpected BIF.
        """
        _ = request, function_args
        if function_name not in self._responses:
            raise AssertionError("Unexpected BIF reached the shared dispatcher boundary.")
        self.call_count += 1
        self.function_names.append(function_name)
        return json.dumps(self._responses[function_name])


class _AdapterBenchmarkClient:
    """Expose a real Persona with only the shared dispatcher edge substituted."""

    def __init__(
        self,
        *,
        persona: Persona,
        dispatcher_stub: _SharedDispatcherBoundaryStub,
    ) -> None:
        """Initialize the adapter client facade.

        Args:
            persona: Persisted Persona whose Registry capability and Workspace scope are live.
            dispatcher_stub: Explicit replacement for the shared dispatch edge.
        """
        self.persona = persona
        self.context_mgr = _AdapterBenchmarkContextManager()
        self._dispatcher_stub = dispatcher_stub

    def callGenericFunction(
        self,
        request: object,
        function_name: str,
        function_args: dict[str, object],
    ) -> str:
        """Delegate exactly the shared dispatcher boundary to the explicit stub.

        Args:
            request: Current assistant request.
            function_name: Canonical BIF selected by the live adapter.
            function_args: Canonical arguments produced by the live adapter.

        Returns:
            JSON-encoded configured terminal response.
        """
        return self._dispatcher_stub.call_generic_function(request, function_name, function_args)


def _build_adapter_request(
    fixture: _DatabaseFixture,
    *,
    user: User | None = None,
    workspace: Workspace | None = None,
    selected_namespace_id: uuid.UUID | None = None,
) -> SimpleNamespace:
    """Build one request that exposes only trusted fixture context to the live adapter.

    Args:
        fixture: Isolated persisted Registry fixture.
        user: Optional requester; defaults to the authorized fixture member.
        workspace: Optional trusted Workspace request context.
        selected_namespace_id: Optional explicit conversation Workspace selection.

    Returns:
        Provider-free request facade with a local session mapping.
    """
    namespace_id = str(selected_namespace_id) if selected_namespace_id is not None else None
    request = SimpleNamespace(
        user=user or fixture.user,
        organization=fixture.organization,
        workspace=workspace,
        session=_BenchmarkSession(namespace_id),
        parent_conversation_id="isolated-benchmark-adapter-conversation",
        META={},
    )
    if namespace_id is not None:
        request.resource_registry_tenant_id = namespace_id
        setattr(request, PERSONA_WORKSPACE_SELECTION_REQUEST_ATTRIBUTE, namespace_id)
    return request


def _add_second_authorized_adapter_workspace(
    fixture: _DatabaseFixture,
    *,
    fixture_index: int,
) -> Workspace:
    """Add a second live authorized Persona Workspace for ambiguity measurement.

    Args:
        fixture: Current persisted Registry fixture.
        fixture_index: Stable suffix used to keep independent trial rows unique.

    Returns:
        Enabled Workspace with a provisioned Registry namespace and real user membership.
    """
    workspace = Workspace.objects.create(
        organization=fixture.organization,
        name=f"Registry Benchmark Second Workspace {fixture_index}",
        slug=f"registry-benchmark-second-workspace-{fixture_index}",
        last_modified_by=fixture.user,
    )
    Workspace.objects.filter(pk=workspace.pk).update(resource_registry_enabled=True)
    workspace.refresh_from_db()
    add_workspace_user(workspace=workspace, user=fixture.user)
    RegistryNamespaceProvisioningService().provision_verified_existing_namespace(
        workspace_id=workspace.pk,
        namespace_id=uuid.UUID(int=900 + fixture_index),
    )
    fixture.persona.workspaces.add(workspace)
    return workspace


def _classify_adapter_tool_calls(
    result: object,
    *,
    expected_tool_names: tuple[str, ...],
) -> str:
    """Classify a live adapter result by its fixed tool-call sequence.

    Args:
        result: Result returned by the live adapter.
        expected_tool_names: Fixed canonical BIF sequence expected before the dispatcher stub.

    Returns:
        Success only when the live adapter reached the configured shared boundary sequence.
    """
    tool_calls = getattr(result, "tool_calls", ())
    observed_names = tuple(getattr(tool_call, "name", None) for tool_call in tool_calls)
    return "success" if observed_names == expected_tool_names else "failed"


def _classify_workspace_ambiguity(result: object) -> str:
    """Classify a real Workspace-selection response that must not reach dispatch.

    Args:
        result: Result returned by the live adapter.

    Returns:
        Success only for the bounded deterministic Workspace-selection response.
    """
    payload = getattr(result, "request_payload", None)
    tool_calls = getattr(result, "tool_calls", ())
    return (
        "success"
        if isinstance(payload, dict)
        and payload.get("status") == "workspace_selection_required"
        and tool_calls == ()
        else "failed"
    )


def _classify_revoked_before_bif(result: object) -> str:
    """Classify a live authorization denial before the shared dispatcher boundary.

    Args:
        result: Result returned by the live adapter.

    Returns:
        Rejected only when the live adapter denies the unassigned requester before BIF dispatch.
    """
    payload = getattr(result, "request_payload", None)
    tool_calls = getattr(result, "tool_calls", ())
    return (
        "rejected"
        if isinstance(payload, dict)
        and payload.get("error_code") == "resource_registry_persona_workspace_unavailable"
        and tool_calls == ()
        else "failed"
    )


def _build_adapter_benchmark_scenarios(
    fixture: _DatabaseFixture,
    *,
    fixture_index: int,
) -> tuple[tuple[BenchmarkScenario, ...], Mapping[str, _SharedDispatcherBoundaryStub]]:
    """Build isolated live-adapter scenarios with an explicit shared-dispatch stub.

    Args:
        fixture: Persisted fixture for one independent database-backed benchmark trial.
        fixture_index: Stable unique trial suffix.

    Returns:
        Fixed adapter benchmark scenarios and their observable dispatcher stubs.
    """
    _add_second_authorized_adapter_workspace(fixture, fixture_index=fixture_index)
    adapter = DataRegistryExplorationRoutingAdapter()
    listing_stub = _SharedDispatcherBoundaryStub(
        {"resource_registry_list_datasets": {"success": True, "data": {"datasets": []}}}
    )
    schema_stub = _SharedDispatcherBoundaryStub(
        {
            "resource_registry_get_dataset_schema": {
                "success": True,
                "data": {
                    "schema": {
                        "schema_json": [{"name": "category", "type": "string"}],
                    }
                },
            },
            "resource_registry_explore_dataset": {
                "success": True,
                "operation": "distinct_values",
                "column": "category",
                "values": [{"value": "benchmark"}],
                "row_count": 1,
                "limit": 5,
                "truncated": False,
            },
        }
    )
    ambiguous_stub = _SharedDispatcherBoundaryStub({})
    revoked_stub = _SharedDispatcherBoundaryStub({})
    listing_client = _AdapterBenchmarkClient(
        persona=fixture.persona,
        dispatcher_stub=listing_stub,
    )
    schema_client = _AdapterBenchmarkClient(
        persona=fixture.persona,
        dispatcher_stub=schema_stub,
    )
    ambiguous_client = _AdapterBenchmarkClient(
        persona=fixture.persona,
        dispatcher_stub=ambiguous_stub,
    )
    revoked_client = _AdapterBenchmarkClient(
        persona=fixture.persona,
        dispatcher_stub=revoked_stub,
    )
    listing_request = _build_adapter_request(
        fixture,
        workspace=fixture.workspace,
        selected_namespace_id=fixture.namespace_id,
    )
    schema_request = _build_adapter_request(
        fixture,
        workspace=fixture.workspace,
        selected_namespace_id=fixture.namespace_id,
    )
    schema_request.session[f"resource_registry_active_dataset_id:{fixture.namespace_id}"] = str(
        fixture.datasets[0].pk
    )
    ambiguous_request = _build_adapter_request(fixture)
    revoked_user = User.objects.create_user(
        username=f"registry-benchmark-revoked-member-{fixture_index}"
    )
    revoked_request = _build_adapter_request(fixture, user=revoked_user)
    scenarios = (
        BenchmarkScenario(
            "routing.adapter_listing_authorized",
            "routing",
            "isolated_database",
            lambda: adapter.try_route(
                client=listing_client,
                request=listing_request,
                statement="List the datasets available to me.",
            ),
            lambda result: _classify_adapter_tool_calls(
                result,
                expected_tool_names=("resource_registry_list_datasets",),
            ),
            "shared_dispatch_stubbed_before_bif",
        ),
        BenchmarkScenario(
            "routing.adapter_schema_authorized",
            "routing",
            "isolated_database",
            lambda: adapter.try_route(
                client=schema_client,
                request=schema_request,
                statement="What unique values exist in category?",
            ),
            lambda result: _classify_adapter_tool_calls(
                result,
                expected_tool_names=(
                    "resource_registry_get_dataset_schema",
                    "resource_registry_explore_dataset",
                ),
            ),
            "shared_dispatch_stubbed_before_bif",
        ),
        BenchmarkScenario(
            "routing.adapter_workspace_ambiguous",
            "routing",
            "isolated_database",
            lambda: adapter.try_route(
                client=ambiguous_client,
                request=ambiguous_request,
                statement="List the datasets available to me.",
            ),
            _classify_workspace_ambiguity,
            "registry_pre_dispatch_only",
        ),
        BenchmarkScenario(
            "routing.adapter_revoked_before_bif",
            "routing",
            "isolated_database",
            lambda: adapter.try_route(
                client=revoked_client,
                request=revoked_request,
                statement="List the datasets available to me.",
            ),
            _classify_revoked_before_bif,
            "registry_pre_dispatch_only",
        ),
    )
    stubs = {
        "routing.adapter_listing_authorized": listing_stub,
        "routing.adapter_schema_authorized": schema_stub,
        "routing.adapter_workspace_ambiguous": ambiguous_stub,
        "routing.adapter_revoked_before_bif": revoked_stub,
    }
    return scenarios, stubs


def _record_adapter_execution_boundary(
    result: dict[str, object],
    *,
    dispatcher_call_count: int,
) -> None:
    """Annotate whether the benchmark reached the explicit dispatcher substitute.

    Args:
        result: Sanitized scenario result to annotate in place.
        dispatcher_call_count: Calls observed at the shared-dispatch substitute.
    """
    result["shared_dispatch_stubbed"] = dispatcher_call_count > 0
    result["execution_boundary"] = (
        "shared_dispatch_stubbed_before_bif"
        if dispatcher_call_count > 0
        else "registry_pre_dispatch_only"
    )


def _adapter_comparison_values(result: Mapping[str, object]) -> dict[str, float | None]:
    """Extract comparable measured adapter metrics without summing nested stages.

    Args:
        result: One sanitized adapter benchmark result.

    Returns:
        Fixed primary wall, root SQL, authorization-build, and error metrics.
    """
    latency = result.get("latency")
    latency_values = latency if isinstance(latency, Mapping) else {}
    stages = result.get("stages")
    stage_values = stages if isinstance(stages, Mapping) else {}
    deterministic_routing = stage_values.get("deterministic_routing")
    routing_values = deterministic_routing if isinstance(deterministic_routing, Mapping) else {}
    raw_values: dict[str, object] = {
        "p50_ms": latency_values.get("p50_ms"),
        "p95_ms": latency_values.get("p95_ms"),
        "p99_ms": latency_values.get("p99_ms"),
        "sql_query_count": routing_values.get("mean_db_query_count"),
        "sql_duration_ms": routing_values.get("mean_db_duration_ms"),
        "authorization_context_build_count_per_operation": result.get(
            "authorization_context_build_count_per_operation"
        ),
        "error_count": result.get("error_count"),
    }
    return {
        name: (
            float(value)
            if isinstance(value, (int, float)) and not isinstance(value, bool)
            else None
        )
        for name, value in raw_values.items()
    }


def _build_pre_optimization_adapter_comparison(
    candidate_results: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Compare measured E2.1 adapter results to the accepted pre-optimization baseline.

    Args:
        candidate_results: Primary first-trial E2.1 adapter measurements.

    Returns:
        Sanitized before/after records with absolute and percentage differences.

    Raises:
        pytest.fail: If the accepted pre-E2.1 baseline is unavailable or incomplete.
    """
    if not _PRE_E21_BENCHMARK_OUTPUT.is_file():
        pytest.fail("Accepted pre-E2.1 adapter baseline artifact is required for comparison.")
    baseline = json.loads(_PRE_E21_BENCHMARK_OUTPUT.read_text())
    raw_baseline_results = baseline.get("results")
    if not isinstance(raw_baseline_results, list):
        pytest.fail("Accepted pre-E2.1 adapter baseline artifact has invalid results.")
    baseline_by_name = {
        str(result.get("name")): result
        for result in raw_baseline_results
        if isinstance(result, Mapping)
        and str(result.get("name")) in DATABASE_ADAPTER_ROUTING_SCENARIO_NAMES
    }
    candidate_by_name = {
        str(result.get("name")): result
        for result in candidate_results
        if str(result.get("name")) in DATABASE_ADAPTER_ROUTING_SCENARIO_NAMES
    }
    if (
        tuple(baseline_by_name) != DATABASE_ADAPTER_ROUTING_SCENARIO_NAMES
        or tuple(candidate_by_name) != DATABASE_ADAPTER_ROUTING_SCENARIO_NAMES
    ):
        pytest.fail("Adapter baseline comparison requires all four registered scenarios.")
    comparisons: list[dict[str, object]] = []
    for scenario_name in DATABASE_ADAPTER_ROUTING_SCENARIO_NAMES:
        before = _adapter_comparison_values(baseline_by_name[scenario_name])
        after = _adapter_comparison_values(candidate_by_name[scenario_name])
        absolute_difference: dict[str, float | None] = {}
        percentage_difference: dict[str, float | None] = {}
        for metric_name in _ADAPTER_COMPARISON_METRICS:
            before_value = before[metric_name]
            after_value = after[metric_name]
            difference = (
                after_value - before_value
                if after_value is not None and before_value is not None
                else None
            )
            absolute_difference[metric_name] = difference
            percentage_difference[metric_name] = (
                (difference / before_value) * 100
                if difference is not None and before_value is not None and before_value != 0
                else None
            )
        comparisons.append(
            {
                "name": scenario_name,
                "before": before,
                "after": after,
                "absolute_difference": absolute_difference,
                "percentage_difference": percentage_difference,
            }
        )
    return comparisons


def _run_adapter_benchmark_trials(
    *,
    warmups: int,
    iterations: int,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Run three fixture-isolated live-adapter trials with the fixed E1.4 methodology.

    Args:
        warmups: Excluded warmup calls per scenario and trial.
        iterations: Measured calls per scenario and trial.

    Returns:
        Primary first-trial results and all trial records for consistency reporting.
    """
    primary_results: list[dict[str, object]] = []
    trials: list[dict[str, object]] = []
    for trial_number in range(1, 4):
        fixture = _create_database_fixture(fixture_index=trial_number)
        scenarios, dispatcher_stubs = _build_adapter_benchmark_scenarios(
            fixture,
            fixture_index=trial_number,
        )
        trial_results: list[dict[str, object]] = []
        for scenario in scenarios:
            result = run_scenario(scenario, warmups=warmups, iterations=iterations)
            _record_adapter_execution_boundary(
                result,
                dispatcher_call_count=dispatcher_stubs[scenario.name].call_count,
            )
            trial_results.append(result)
        if trial_number == 1:
            primary_results = trial_results
        trials.append({"trial": trial_number, "results": trial_results})
    return primary_results, trials


def _save_selected_datasets(fixture: _DatabaseFixture, datasets: tuple[Dataset, ...]) -> None:
    """Prepare isolated selection cache entries before timing any operation.

    Args:
        fixture: Current test-database fixture.
        datasets: Dataset records selected for the measured case.
    """
    save_selections(
        fixture.scope,
        [
            VerifiedDatasetSelection(
                namespace_id=str(fixture.namespace_id),
                workspace_id=fixture.workspace.pk,
                dataset_id=str(dataset.pk),
                version_id=None,
            )
            for dataset in datasets
        ],
    )


def _classify_nonempty_context(value: object) -> str:
    """Classify a real authorized prompt-context result.

    Args:
        value: Actual Registry prompt-context return value.

    Returns:
        Success only for a nonempty context.
    """
    return "success" if isinstance(value, str) and bool(value) else "failed"


def _classify_empty_context(value: object) -> str:
    """Classify a real empty or withheld prompt-context result.

    Args:
        value: Actual Registry prompt-context return value.

    Returns:
        Success for the expected empty context; failed otherwise.
    """
    return "success" if value == "" else "failed"


def _classify_rejected_grouped_result(value: object) -> str:
    """Classify a controlled grouped admission or validation denial.

    Args:
        value: Actual grouped BIF response.

    Returns:
        Rejected for a 400/403 response; failed otherwise.
    """
    try:
        payload = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return "failed"
    return (
        "rejected"
        if isinstance(payload, dict) and payload.get("status") in {400, 403}
        else "failed"
    )


class _CanonicalGroupedDispatchClient(AIEngineClient):
    """Count calls that enter the unchanged shared canonical dispatcher."""

    def __init__(self, persona: Persona) -> None:
        """Initialize the real client with a disposable benchmark Persona.

        Args:
            persona: Persisted Persona assigned only read-only benchmark operations.
        """
        super().__init__(persona)  # type: ignore[no-untyped-call]  # BotServer client lacks annotations.
        self.canonical_dispatch_call_count = 0

    def callGenericFunction(
        self,
        request: object,
        function_name: str,
        function_args: object,
        *,
        execution_channel: BuiltInFunctionExecutionChannel = BuiltInFunctionExecutionChannel.LLM_TOOL,
    ) -> object:
        """Count then invoke the inherited production dispatcher without substitution.

        Args:
            request: Independent isolated benchmark request.
            function_name: Canonical Registry BIF chosen by the group bridge.
            function_args: Effective arguments after Registry selector resolution.
            execution_channel: Original trusted BIF execution channel.

        Returns:
            Result from the existing shared dispatcher and Registry handler path.
        """
        self.canonical_dispatch_call_count += 1
        return super().callGenericFunction(
            request,
            function_name,
            function_args,
            execution_channel=execution_channel,
        )


class _PreDispatchGroupedClient:
    """Terminate only after live Registry pre-dispatch validation for safe benchmark cases."""

    def __init__(self, persona: Persona, *, response: str | None = None) -> None:
        """Initialize a clear non-canonical dispatcher substitute.

        Args:
            persona: Persisted Persona used by the live pre-dispatch admission service.
            response: Fixed JSON result returned after all pre-dispatch checks pass.
        """
        self.persona = persona
        self.appLogger = logging.Logger("resource_registry.benchmarks.pre_dispatch")
        self.bif_execution_admission_service = BuiltInFunctionExecutionAdmissionService()
        self.response = response or json.dumps({"success": True, "data": {}})
        self.call_count = 0

    def callGenericFunction(
        self,
        request: object,
        function_name: str,
        function_args: object,
        *,
        execution_channel: BuiltInFunctionExecutionChannel,
    ) -> str:
        """Return only the configured terminal response after the shared boundary.

        Args:
            request: Current benchmark request.
            function_name: Canonical BIF selected by the real Registry bridge.
            function_args: Validated effective BIF arguments.
            execution_channel: Original trusted BIF execution channel.

        Returns:
            Configured JSON response without invoking the shared dispatcher.
        """
        _ = request, function_name, function_args, execution_channel
        self.call_count += 1
        return self.response


def _decode_grouped_result(value: object) -> dict[str, object] | None:
    """Parse one grouped response for fixed benchmark assertions.

    Args:
        value: Runtime result returned by a grouped invocation.

    Returns:
        JSON object response, or ``None`` when it is not a response object.
    """
    try:
        payload = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _classify_grouped_success(value: object) -> str:
    """Classify a canonical read response without retaining its business payload.

    Args:
        value: Grouped runtime return value.

    Returns:
        Success only for the normal Registry success envelope.
    """
    payload = _decode_grouped_result(value)
    return "success" if payload is not None and payload.get("success") is True else "failed"


def _classify_grouped_bad_request(value: object) -> str:
    """Classify exact fail-closed grouped 400 behavior.

    Args:
        value: Grouped runtime return value.

    Returns:
        Rejected only for the unchanged 400 contract.
    """
    payload = _decode_grouped_result(value)
    return "rejected" if payload is not None and payload.get("status") == 400 else "failed"


def _classify_grouped_forbidden(value: object) -> str:
    """Classify exact fail-closed grouped 403 behavior.

    Args:
        value: Grouped runtime return value.

    Returns:
        Rejected only for the unchanged 403 contract.
    """
    payload = _decode_grouped_result(value)
    return "rejected" if payload is not None and payload.get("status") == 403 else "failed"


def _project_synthetic_registry_result(payload: str) -> str:
    """Project one bounded synthetic Registry response for a dedicated benchmark scenario.

    Args:
        payload: JSON or non-JSON representative Registry operation response.

    Returns:
        Result-projected response with the production passthrough behavior intact.
    """
    return project_registry_tool_result(payload)


def _classify_projection_result(value: object) -> str:
    """Classify a completed projection without interpreting its business payload.

    Args:
        value: Projected synthetic response.

    Returns:
        Success because the projection scenario validates content separately from timing.
    """
    _ = value
    return "success"


def _run_grouped_call(
    client: object,
    request: object,
    *,
    group_name: str,
    action: str,
    arguments: dict[str, object],
    as_json: bool = False,
) -> object:
    """Invoke one grouped action through the public Registry runtime boundary.

    Args:
        client: Canonical or explicitly pre-dispatch client for the declared scenario.
        request: Current isolated benchmark request.
        group_name: Grouped Registry tool name.
        action: Selected action inside that group.
        arguments: Candidate action arguments.
        as_json: Whether to submit the normal JSON-string transport representation.

    Returns:
        Grouped runtime response.
    """
    envelope: dict[str, object] = {"action": action, "arguments": arguments}
    return execute_grouped_registry_call(
        client,
        request,
        group_name,
        json.dumps(envelope) if as_json else envelope,
        execution_channel=BuiltInFunctionExecutionChannel.LLM_TOOL,
    )


def _operation_definition_for_action(
    fixture: _DatabaseFixture,
    action: str,
) -> FunctionToolDefinition:
    """Build the selected action definition from real persisted BIF metadata.

    Args:
        fixture: Current isolated database fixture.
        action: Registry action without the canonical name prefix.

    Returns:
        Real operation definition used by grouped schema validation.
    """
    _ = fixture
    function = BuiltInFunction.objects.get(name=f"resource_registry_{action}")
    parameters = tuple(FunctionParameter.objects.filter(function=function).order_by("pk"))
    return build_registry_operation_definition(function, parameters)


def _build_independent_grouped_request(
    fixture: _DatabaseFixture,
    *,
    request_index: int,
) -> SimpleNamespace:
    """Build an independent request and session for one concurrent canonical call.

    Args:
        fixture: Shared persisted fixture, never a shared request/session object.
        request_index: Stable worker-local session and conversation suffix.

    Returns:
        Fresh mutable request state with read-only transformation dependencies.
    """
    session = _BenchmarkSession(str(fixture.namespace_id))
    session.session_key = f"isolated-grouped-concurrency-{request_index}"
    request = SimpleNamespace(
        user=fixture.user,
        organization=fixture.organization,
        workspace=fixture.workspace,
        persona=fixture.persona,
        session=session,
        parent_conversation_id=f"isolated-grouped-concurrency-{request_index}",
        resource_registry_tenant_id=str(fixture.namespace_id),
        META={},
        transformation_bif_services=_build_transformation_bif_services(),
    )
    setattr(request, PERSONA_WORKSPACE_SELECTION_REQUEST_ATTRIBUTE, str(fixture.namespace_id))
    return request


def _build_grouped_execution_scenarios(
    fixture: _DatabaseFixture,
    *,
    fixture_index: int,
) -> tuple[BenchmarkScenario, ...]:
    """Build the full RR-03.1 workload set around real grouped execution boundaries.

    Args:
        fixture: Stable isolated database fixture for one independent trial.
        fixture_index: Trial number used only to create independent concurrency request identities.

    Returns:
        Fixed RR-03.1 serial scenarios, each with a fail-closed boundary label.
    """
    canonical_client = _CanonicalGroupedDispatchClient(fixture.persona)
    pre_dispatch_client = _PreDispatchGroupedClient(fixture.persona)
    tenant_id = str(fixture.namespace_id)
    dataset_id = str(fixture.datasets[0].pk)
    _save_selected_datasets(fixture, (fixture.datasets[0],))
    simple_definition = _operation_definition_for_action(fixture, "list_datasets")
    transformation_definition = _operation_definition_for_action(
        fixture, "start_transformation_run"
    )
    pending_version = DatasetVersion.objects.create(
        dataset=fixture.datasets[1],
        version_number=fixture_index,
        source_uri=f"benchmark://pending-{fixture_index}",
        content_hash=f"isolated-grouped-pending-{fixture_index}",
        status=DatasetVersionStatus.PENDING,
    )
    transformation_arguments = {
        "tenant_id": tenant_id,
        "definition_id": str(uuid.UUID(int=3000 + fixture_index)),
        "definition_version_id": str(uuid.UUID(int=4000 + fixture_index)),
        "parameters": {"mode": "benchmark"},
        "input_bindings": [
            {
                "input_name": "source",
                "dataset_id": dataset_id,
                "dataset_version_id": str(uuid.UUID(int=5000 + fixture_index)),
            }
        ],
        "idempotency_key": f"rr031-{fixture_index}",
    }

    def successful_list_datasets() -> object:
        return _run_grouped_call(
            canonical_client,
            fixture.request,
            group_name="resource_registry_inspect",
            action="list_datasets",
            arguments={"tenant_id": tenant_id, "limit": 5},
        )

    def contextual_dataset_read() -> object:
        _save_selected_datasets(fixture, (fixture.datasets[0],))
        return _run_grouped_call(
            canonical_client,
            fixture.request,
            group_name="resource_registry_inspect",
            action="get_dataset",
            arguments={"dataset_id": "this dataset"},
        )

    def transformation_read() -> object:
        return _run_grouped_call(
            canonical_client,
            fixture.request,
            group_name="resource_registry_manage_transformations",
            action="list_transformation_runs",
            arguments={"tenant_id": tenant_id, "limit": 5},
        )

    def two_successful_calls() -> object:
        call_count_before = canonical_client.canonical_dispatch_call_count
        first = successful_list_datasets()
        second = successful_list_datasets()
        return {
            "first": _decode_grouped_result(first),
            "second": _decode_grouped_result(second),
            "canonical_call_count": canonical_client.canonical_dispatch_call_count
            - call_count_before,
        }

    def permission_revocation_between_calls() -> object:
        call_count_before = canonical_client.canonical_dispatch_call_count
        first = successful_list_datasets()
        function = BuiltInFunction.objects.get(name="resource_registry_list_datasets")
        BuiltInFunctionPersonaRelationship.objects.filter(
            persona=fixture.persona,
            builtin_function=function,
        ).delete()
        try:
            second = successful_list_datasets()
        finally:
            BuiltInFunctionPersonaRelationship.objects.create(
                persona=fixture.persona,
                builtin_function=function,
                last_modified_by=fixture.user,
            )
        return {
            "first": _decode_grouped_result(first),
            "second": _decode_grouped_result(second),
            "canonical_call_count": canonical_client.canonical_dispatch_call_count
            - call_count_before,
        }

    def capability_change_between_calls() -> object:
        call_count_before = canonical_client.canonical_dispatch_call_count
        first = successful_list_datasets()
        disabled_key = f"resource_registry_disabled_capabilities:{tenant_id}"
        fixture.request.session[disabled_key] = ["resource_registry.dataset.list"]
        try:
            second = successful_list_datasets()
        finally:
            fixture.request.session.pop(disabled_key, None)
        return {
            "first": _decode_grouped_result(first),
            "second": _decode_grouped_result(second),
            "canonical_call_count": canonical_client.canonical_dispatch_call_count
            - call_count_before,
        }

    def visibility_change_between_calls() -> object:
        _save_selected_datasets(fixture, (fixture.datasets[0],))
        call_count_before = canonical_client.canonical_dispatch_call_count
        first = contextual_dataset_read()
        Dataset.objects.filter(pk=fixture.datasets[0].pk).update(status=DatasetStatus.ARCHIVED)
        try:
            second = contextual_dataset_read()
        finally:
            Dataset.objects.filter(pk=fixture.datasets[0].pk).update(status=DatasetStatus.ACTIVE)
        return {
            "first": _decode_grouped_result(first),
            "second": _decode_grouped_result(second),
            "canonical_call_count": canonical_client.canonical_dispatch_call_count
            - call_count_before,
        }

    def stale_selection() -> object:
        save_selections(
            fixture.scope,
            [
                VerifiedDatasetSelection(
                    namespace_id=tenant_id,
                    workspace_id=fixture.workspace.pk,
                    dataset_id=str(fixture.datasets[1].pk),
                    version_id=str(pending_version.pk),
                )
            ],
        )
        call_count_before = canonical_client.canonical_dispatch_call_count
        result = _run_grouped_call(
            canonical_client,
            fixture.request,
            group_name="resource_registry_inspect",
            action="get_dataset_schema",
            arguments={"dataset_id": "this dataset"},
        )
        return {
            "result": _decode_grouped_result(result),
            "canonical_call_count": canonical_client.canonical_dispatch_call_count
            - call_count_before,
        }

    def mixed_success_and_denial() -> object:
        call_count_before = canonical_client.canonical_dispatch_call_count
        first = successful_list_datasets()
        second = _run_grouped_call(
            canonical_client,
            fixture.request,
            group_name="resource_registry_inspect",
            action="explore_dataset",
            arguments={"tenant_id": tenant_id},
        )
        return {
            "first": _decode_grouped_result(first),
            "second": _decode_grouped_result(second),
            "canonical_call_count": canonical_client.canonical_dispatch_call_count
            - call_count_before,
        }

    def classify_two_successes(value: object) -> str:
        if not isinstance(value, Mapping):
            return "failed"
        first = value.get("first")
        second = value.get("second")
        return (
            "success"
            if (
                isinstance(first, Mapping)
                and isinstance(second, Mapping)
                and first.get("success") is True
                and second.get("success") is True
                and value.get("canonical_call_count") == 2
            )
            else "failed"
        )

    def classify_success_then_forbidden(value: object) -> str:
        if not isinstance(value, Mapping):
            return "failed"
        first = value.get("first")
        second = value.get("second")
        return (
            "rejected"
            if (
                isinstance(first, Mapping)
                and isinstance(second, Mapping)
                and first.get("success") is True
                and second.get("status") == 403
                and value.get("canonical_call_count") == 1
            )
            else "failed"
        )

    def classify_visibility_change(value: object) -> str:
        if not isinstance(value, Mapping):
            return "failed"
        first = value.get("first")
        second = value.get("second")
        return (
            "rejected"
            if (
                isinstance(first, Mapping)
                and isinstance(second, Mapping)
                and first.get("success") is True
                and second.get("status") == 400
                and value.get("canonical_call_count") == 1
            )
            else "failed"
        )

    def classify_capability_change(value: object) -> str:
        if not isinstance(value, Mapping):
            return "failed"
        first = value.get("first")
        second = value.get("second")
        return (
            "rejected"
            if (
                isinstance(first, Mapping)
                and isinstance(second, Mapping)
                and first.get("success") is True
                and second.get("status") == 403
                and value.get("canonical_call_count") == 2
            )
            else "failed"
        )

    def classify_stale_selection(value: object) -> str:
        if not isinstance(value, Mapping):
            return "failed"
        result = value.get("result")
        return (
            "rejected"
            if (
                isinstance(result, Mapping)
                and result.get("status") == 400
                and value.get("canonical_call_count") == 0
            )
            else "failed"
        )

    def classify_mixed_calls(value: object) -> str:
        if not isinstance(value, Mapping):
            return "failed"
        first = value.get("first")
        second = value.get("second")
        return (
            "rejected"
            if (
                isinstance(first, Mapping)
                and isinstance(second, Mapping)
                and first.get("success") is True
                and second.get("status") == 403
                and value.get("canonical_call_count") == 1
            )
            else "failed"
        )

    projection_payloads = {
        "grouped.projection.small_unchanged": json.dumps({"success": True, "data": {}}),
        "grouped.projection.large_unchanged": json.dumps(
            {"success": True, "data": {"items": [{"id": index} for index in range(200)]}}
        ),
        "grouped.projection.suggested_bif": json.dumps(
            {"suggested_bif": "resource_registry_get_dataset"}
        ),
        "grouped.projection.recommended_bif": json.dumps(
            {"recommended_bif": "resource_registry_start_query_job"}
        ),
        "grouped.projection.hint": json.dumps(
            {"hint": "Use resource_registry_get_dataset_schema next."}
        ),
        "grouped.projection.error": json.dumps(
            {"error": "Use resource_registry_get_dataset before retrying."}
        ),
        "grouped.projection.multiple_bif_names": json.dumps(
            {
                "hint": "Use resource_registry_get_dataset then resource_registry_get_dataset_schema.",
                "error": "Do not call resource_registry_start_query_job yet.",
            }
        ),
        "grouped.projection.malformed_json": "{not json",
        "grouped.projection.non_object_json": json.dumps(["not", "an", "object"]),
        "grouped.projection.business_data_unchanged": json.dumps(
            {
                "suggested_bif": "resource_registry_get_dataset",
                "data": {"literal": "resource_registry_get_dataset"},
            }
        ),
    }
    projection_scenarios = tuple(
        BenchmarkScenario(
            name,
            "projection",
            "synthetic",
            partial(_project_synthetic_registry_result, payload),
            _classify_projection_result,
            "synthetic_projection_only",
        )
        for name, payload in projection_payloads.items()
    )
    return (
        BenchmarkScenario(
            "grouped.execution.list_datasets",
            "execution",
            "isolated_database",
            successful_list_datasets,
            _classify_grouped_success,
            "canonical_dispatch_included",
        ),
        BenchmarkScenario(
            "grouped.execution.dataset_context",
            "execution",
            "isolated_database",
            contextual_dataset_read,
            _classify_grouped_success,
            "canonical_dispatch_included",
        ),
        BenchmarkScenario(
            "grouped.execution.transformation_list",
            "execution",
            "isolated_database",
            transformation_read,
            _classify_grouped_success,
            "canonical_dispatch_included",
        ),
        BenchmarkScenario(
            "grouped.selection.smallest_group_parsed",
            "action_selection",
            "isolated_database",
            lambda: _run_grouped_call(
                pre_dispatch_client,
                fixture.request,
                group_name="resource_registry_manage_transformations",
                action="list_transformation_runs",
                arguments={"tenant_id": tenant_id},
            ),
            _classify_grouped_success,
            "registry_pre_dispatch_only",
        ),
        BenchmarkScenario(
            "grouped.selection.largest_group_json",
            "action_selection",
            "isolated_database",
            lambda: _run_grouped_call(
                pre_dispatch_client,
                fixture.request,
                group_name="resource_registry_inspect",
                action="list_datasets",
                arguments={"tenant_id": tenant_id, "limit": 5},
                as_json=True,
            ),
            _classify_grouped_success,
            "registry_pre_dispatch_only",
        ),
        BenchmarkScenario(
            "grouped.selection.unknown_action",
            "action_selection",
            "isolated_database",
            lambda: _run_grouped_call(
                pre_dispatch_client,
                fixture.request,
                group_name="resource_registry_inspect",
                action="unknown_action",
                arguments={},
            ),
            _classify_grouped_bad_request,
            "registry_pre_dispatch_only",
        ),
        BenchmarkScenario(
            "grouped.selection.wrong_group",
            "action_selection",
            "isolated_database",
            lambda: _run_grouped_call(
                pre_dispatch_client,
                fixture.request,
                group_name="resource_registry_inspect",
                action="list_transformation_runs",
                arguments={},
            ),
            _classify_grouped_bad_request,
            "registry_pre_dispatch_only",
        ),
        BenchmarkScenario(
            "grouped.validation.simple_valid",
            "schema_validation",
            "isolated_database",
            lambda: is_valid_grouped_registry_arguments(
                simple_definition, {"tenant_id": tenant_id, "limit": 5}
            ),
            lambda value: "success" if value is True else "failed",
            "registry_pre_dispatch_only",
        ),
        BenchmarkScenario(
            "grouped.validation.transformation_complex_valid",
            "schema_validation",
            "isolated_database",
            lambda: is_valid_grouped_registry_arguments(
                transformation_definition, transformation_arguments
            ),
            lambda value: "success" if value is True else "failed",
            "registry_pre_dispatch_only",
        ),
        BenchmarkScenario(
            "grouped.validation.missing_required",
            "schema_validation",
            "isolated_database",
            lambda: is_valid_grouped_registry_arguments(
                transformation_definition,
                {
                    key: value
                    for key, value in transformation_arguments.items()
                    if key != "idempotency_key"
                },
            ),
            lambda value: "rejected" if value is False else "failed",
            "registry_pre_dispatch_only",
        ),
        BenchmarkScenario(
            "grouped.validation.wrong_type",
            "schema_validation",
            "isolated_database",
            lambda: is_valid_grouped_registry_arguments(
                simple_definition, {"tenant_id": tenant_id, "limit": "five"}
            ),
            lambda value: "rejected" if value is False else "failed",
            "registry_pre_dispatch_only",
        ),
        BenchmarkScenario(
            "grouped.validation.unexpected_property",
            "schema_validation",
            "isolated_database",
            lambda: is_valid_grouped_registry_arguments(
                simple_definition, {"tenant_id": tenant_id, "unexpected": True}
            ),
            lambda value: "rejected" if value is False else "failed",
            "registry_pre_dispatch_only",
        ),
        BenchmarkScenario(
            "grouped.validation.invalid_nested",
            "schema_validation",
            "isolated_database",
            lambda: is_valid_grouped_registry_arguments(
                transformation_definition,
                {
                    **transformation_arguments,
                    "input_bindings": [{"input_name": "source", "dataset_id": dataset_id}],
                },
            ),
            lambda value: "rejected" if value is False else "failed",
            "registry_pre_dispatch_only",
        ),
        *projection_scenarios,
        BenchmarkScenario(
            "grouped.freshness.two_successes",
            "authorization_freshness",
            "isolated_database",
            two_successful_calls,
            classify_two_successes,
            "canonical_dispatch_included",
        ),
        BenchmarkScenario(
            "grouped.freshness.permission_revoked",
            "authorization_freshness",
            "isolated_database",
            permission_revocation_between_calls,
            classify_success_then_forbidden,
            "canonical_dispatch_included",
        ),
        BenchmarkScenario(
            "grouped.freshness.capability_changed",
            "authorization_freshness",
            "isolated_database",
            capability_change_between_calls,
            classify_capability_change,
            "canonical_dispatch_included",
        ),
        BenchmarkScenario(
            "grouped.freshness.visibility_changed",
            "authorization_freshness",
            "isolated_database",
            visibility_change_between_calls,
            classify_visibility_change,
            "canonical_dispatch_included",
        ),
        BenchmarkScenario(
            "grouped.freshness.stale_selection",
            "authorization_freshness",
            "isolated_database",
            stale_selection,
            classify_stale_selection,
            "canonical_dispatch_included",
        ),
        BenchmarkScenario(
            "grouped.freshness.mixed_success_denied",
            "authorization_freshness",
            "isolated_database",
            mixed_success_and_denial,
            classify_mixed_calls,
            "canonical_dispatch_included",
        ),
    )


def _build_grouped_concurrency_scenario(
    fixture: _DatabaseFixture,
    *,
    worker_count: int,
) -> BenchmarkScenario:
    """Build a canonical list workload that creates request state inside each worker.

    Args:
        fixture: Persisted isolated data shared only as read-only records.
        worker_count: Declared maximum parallel request count.

    Returns:
        One canonical-dispatch concurrency benchmark scenario.
    """
    tenant_id = str(fixture.namespace_id)

    def execute_independent_call() -> object:
        request_index = uuid.uuid4().int
        close_old_connections()
        try:
            request = _build_independent_grouped_request(
                fixture,
                request_index=request_index,
            )
            client = _CanonicalGroupedDispatchClient(fixture.persona)
            return _run_grouped_call(
                client,
                request,
                group_name="resource_registry_inspect",
                action="list_datasets",
                arguments={"tenant_id": tenant_id, "limit": 5},
            )
        finally:
            close_old_connections()

    return BenchmarkScenario(
        f"grouped.concurrency.{worker_count}",
        "concurrency",
        "isolated_database_threaded",
        execute_independent_call,
        _classify_grouped_success,
        "canonical_dispatch_included",
    )


def _run_grouped_execution_benchmark_trials(
    *,
    warmups: int,
    iterations: int,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Run three fixture-isolated RR-03.1 trials with canonical dispatch enabled.

    Args:
        warmups: Excluded warmup invocations per serial scenario and concurrency level.
        iterations: Measured invocations per serial scenario and concurrency level.

    Returns:
        First-trial results for the artifact table and all three trial records.
    """
    primary_results: list[dict[str, object]] = []
    trials: list[dict[str, object]] = []
    for trial_number in range(1, 4):
        fixture = _create_database_fixture(fixture_index=100 + trial_number)
        serial_scenarios = _build_grouped_execution_scenarios(
            fixture,
            fixture_index=trial_number,
        )
        if {scenario.name for scenario in serial_scenarios} | {
            f"grouped.concurrency.{workers}" for workers in (1, 10, 25)
        } != set(REQUIRED_GROUPED_EXECUTION_SCENARIO_NAMES):
            pytest.fail("RR-03.1 benchmark scenarios do not match the registered inventory.")
        trial_results = [
            run_scenario(scenario, warmups=warmups, iterations=iterations)
            for scenario in serial_scenarios
        ]
        for worker_count in (1, 10, 25):
            concurrency_scenario = _build_grouped_concurrency_scenario(
                fixture,
                worker_count=worker_count,
            )
            trial_results.append(
                run_concurrent_database_scenario(
                    concurrency_scenario,
                    workers=worker_count,
                    warmups=warmups,
                    iterations=iterations,
                )
            )
        if trial_number == 1:
            primary_results = trial_results
        trials.append({"trial": trial_number, "results": trial_results})
    return primary_results, trials


def _classify_epic6_dataset_list(value: object, *, expected_records: int) -> str:
    """Classify one canonical RR-09.1 dataset-list response without retaining records.

    Args:
        value: Grouped runtime response from the inherited canonical dispatcher.
        expected_records: Number of persisted fixture datasets expected in the response.

    Returns:
        ``success`` only when the real grouped response reports the expected bounded page.
    """
    payload = _decode_grouped_result(value)
    if not isinstance(payload, Mapping) or payload.get("success") is not True:
        return "failed"
    data = payload.get("data")
    if not isinstance(data, Mapping):
        return "failed"
    datasets = data.get("datasets")
    pagination = data.get("pagination")
    return (
        "success"
        if (
            isinstance(datasets, list)
            and len(datasets) == expected_records
            and isinstance(pagination, Mapping)
            and pagination.get("returned") == expected_records
        )
        else "failed"
    )


def _observe_epic6_dataset_list(value: object) -> Mapping[str, object]:
    """Extract the actual returned dataset cardinality from one canonical response.

    Args:
        value: Grouped runtime response from the inherited canonical dispatcher.

    Returns:
        Bounded result metadata containing the actual returned dataset count.

    Raises:
        ValueError: If the canonical response lacks a dataset list.
    """
    payload = _decode_grouped_result(value)
    data = payload.get("data") if isinstance(payload, Mapping) else None
    datasets = data.get("datasets") if isinstance(data, Mapping) else None
    if not isinstance(datasets, list):
        raise ValueError("RR-09.1 canonical response does not contain a dataset list.")
    return {"returned_record_count": len(datasets)}


def _execute_epic6_dataset_list(
    canonical_client: _CanonicalGroupedDispatchClient,
    fixture: _DatabaseFixture,
    *,
    tenant_id: str,
    record_count: int,
) -> object:
    """Invoke one bounded canonical dataset-list call for RR-09.1.

    Args:
        canonical_client: Test-only counter delegating to the inherited shared dispatcher.
        fixture: Persisted isolated request context and active datasets.
        tenant_id: Verified Registry namespace identifier.
        record_count: Requested bounded list limit.

    Returns:
        Original grouped runtime response after canonical dispatch.
    """
    return _run_grouped_call(
        canonical_client,
        fixture.request,
        group_name="resource_registry_inspect",
        action="list_datasets",
        arguments={"tenant_id": tenant_id, "limit": record_count},
    )


def _build_epic6_canonical_scenarios(
    fixture: _DatabaseFixture,
) -> tuple[tuple[BenchmarkScenario, ...], _CanonicalGroupedDispatchClient]:
    """Build only fixture-supported RR-09.1 canonical result workloads.

    Args:
        fixture: One isolated fixture containing 100 active visible datasets.

    Returns:
        Canonical dispatcher-inclusive dataset-list scenarios at 1, 50, and 100 records,
        followed by their inherited-dispatch counter.
    """
    canonical_client = _CanonicalGroupedDispatchClient(fixture.persona)
    tenant_id = str(fixture.namespace_id)
    scenarios: list[BenchmarkScenario] = []
    for record_count in (1, 50, 100):
        scenario_name = f"grouped_result.canonical.dataset_list.records_{record_count:03d}"
        scenarios.append(
            BenchmarkScenario(
                scenario_name,
                "canonical_execution",
                "isolated_database",
                partial(
                    _execute_epic6_dataset_list,
                    canonical_client,
                    fixture,
                    tenant_id=tenant_id,
                    record_count=record_count,
                ),
                partial(
                    _classify_epic6_dataset_list,
                    expected_records=record_count,
                ),
                EPIC6_SCENARIO_BOUNDARIES[scenario_name],
                capture_database_sql=True,
                metadata={
                    "record_count": record_count,
                    "projection_changes_result": False,
                    "measurement_boundary": "canonical_dispatch_included",
                },
                observe_result=_observe_epic6_dataset_list,
            )
        )
    return tuple(scenarios), canonical_client


def _allocate_epic6_fixture_indexes() -> tuple[int, ...]:
    """Reserve an unused deterministic group of isolated RR-09.1 fixture indices.

    The isolated pytest database may be retained between explicitly requested benchmark runs.
    Fixture allocation happens before timing and avoids replacing or deleting data from a prior
    guarded run.

    Returns:
        Consecutive fixture indices, one for each required independent canonical trial.
    """
    first_fixture_index = 6_001
    while True:
        candidate_indexes = tuple(
            first_fixture_index + offset for offset in range(EPIC6_TRIAL_COUNT)
        )
        candidate_usernames = tuple(
            f"registry-benchmark-member-{fixture_index}" for fixture_index in candidate_indexes
        )
        if not User.objects.filter(username__in=candidate_usernames).exists():
            return candidate_indexes
        first_fixture_index += EPIC6_TRIAL_COUNT


def _run_epic6_canonical_benchmark_trials(
    *,
    warmups: int,
    iterations: int,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Run guarded RR-09.1 canonical dataset-list trials with explicit fixture gaps.

    Args:
        warmups: Excluded warmup calls per supported canonical scenario.
        iterations: Measured calls per supported canonical scenario.

    Returns:
        First-trial result records and three independent trial records.
    """
    primary_results: list[dict[str, object]] = []
    trials: list[dict[str, object]] = []
    supported_names = {
        "grouped_result.canonical.dataset_list.records_001",
        "grouped_result.canonical.dataset_list.records_050",
        "grouped_result.canonical.dataset_list.records_100",
    }
    fixture_indexes = _allocate_epic6_fixture_indexes()
    for trial_number, fixture_index in enumerate(fixture_indexes, start=1):
        fixture = _create_database_fixture(
            fixture_index=fixture_index,
            dataset_count=100,
        )
        scenarios, canonical_client = _build_epic6_canonical_scenarios(fixture)
        if {scenario.name for scenario in scenarios} != supported_names:
            pytest.fail(
                "RR-09.1 supported canonical scenarios do not match the registered inventory."
            )
        trial_results: list[dict[str, object]] = []
        for scenario in scenarios:
            dispatch_call_count_before = canonical_client.canonical_dispatch_call_count
            result = run_scenario(scenario, warmups=warmups, iterations=iterations)
            dispatch_call_count = (
                canonical_client.canonical_dispatch_call_count - dispatch_call_count_before
            )
            raw_scenario_metadata = result.get("scenario_metadata")
            if not isinstance(raw_scenario_metadata, dict):
                pytest.fail("RR-09.1 canonical result does not retain scenario metadata.")
            raw_scenario_metadata["canonical_dispatch_call_count"] = dispatch_call_count
            if dispatch_call_count != warmups + iterations:
                pytest.fail(
                    "RR-09.1 canonical scenario did not exercise every grouped dispatch: "
                    f"{scenario.name} observed {dispatch_call_count}, expected "
                    f"{warmups + iterations}."
                )
            trial_results.append(result)
        trial_results.extend(build_epic6_not_measured_canonical_results()[3:])
        if {str(result["name"]) for result in trial_results} != set(EPIC6_CANONICAL_SCENARIO_NAMES):
            pytest.fail("RR-09.1 canonical scenario records do not match the registered inventory.")
        if trial_number == 1:
            primary_results = trial_results
        trials.append({"trial": trial_number, "results": trial_results})
    return primary_results, trials


def _run_epic6_canonical_memory_trials() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Profile guarded RR-09.2 canonical dataset-list calls without timing or SQL capture.

    Each fixture is created before ``tracemalloc`` starts. The measured callable performs the
    real grouped dispatch, authorization, and PostgreSQL work; its allocation observations are
    consequently full-boundary evidence and not an attribution to result projection.

    Returns:
        Aggregated primary scenario records and their three independent trial records.

    Raises:
        pytest.fail: If the existing supported canonical scenario inventory changes.
    """
    supported_names = set(EPIC6_MEMORY_CANONICAL_SCENARIO_NAMES)
    trial_results_by_name: dict[str, list[dict[str, object]]] = {
        name: [] for name in EPIC6_MEMORY_CANONICAL_SCENARIO_NAMES
    }
    trial_records: list[dict[str, object]] = []
    fixture_indexes = _allocate_epic6_fixture_indexes()
    for trial_number, fixture_index in enumerate(fixture_indexes, start=1):
        fixture = _create_database_fixture(
            fixture_index=fixture_index,
            dataset_count=100,
        )
        scenarios, canonical_client = _build_epic6_canonical_scenarios(fixture)
        if {scenario.name for scenario in scenarios} != supported_names:
            pytest.fail(
                "RR-09.2 supported canonical scenarios do not match the registered inventory."
            )
        trial_results: list[dict[str, object]] = []
        for scenario in scenarios:
            raw_scenario_metadata = scenario.metadata
            record_count = raw_scenario_metadata.get("record_count")
            if not isinstance(record_count, int):
                pytest.fail("RR-09.2 canonical scenario does not retain its record count.")
            stabilization_dispatch_count_before = canonical_client.canonical_dispatch_call_count
            stabilization_result = scenario.execute()
            if scenario.classify_result(
                stabilization_result
            ) != "success" or scenario.observe_result_metadata(stabilization_result) != {
                "returned_record_count": record_count
            }:
                pytest.fail(
                    "RR-09.2 canonical stabilization result violates the existing contract."
                )
            del stabilization_result
            stabilization_dispatch_count = (
                canonical_client.canonical_dispatch_call_count - stabilization_dispatch_count_before
            )
            if stabilization_dispatch_count != 1:
                pytest.fail(
                    "RR-09.2 canonical stabilization did not exercise one grouped dispatch: "
                    f"{scenario.name} observed {stabilization_dispatch_count}."
                )

            def is_expected_canonical_memory_result(
                result: object,
                evaluated_scenario: BenchmarkScenario = scenario,
                expected_record_count: int = record_count,
            ) -> bool:
                """Verify canonical memory output parity without retaining the response.

                Args:
                    result: Original grouped response from the real canonical dispatcher.

                Returns:
                    Whether the response preserves the expected bounded dataset count.
                """
                return evaluated_scenario.classify_result(
                    result
                ) == "success" and evaluated_scenario.observe_result_metadata(result) == {
                    "returned_record_count": expected_record_count
                }

            dispatch_call_count_before = canonical_client.canonical_dispatch_call_count
            trial = measure_epic6_memory_operation(
                execute=scenario.execute,
                is_expected_result=is_expected_canonical_memory_result,
            )
            dispatch_call_count = (
                canonical_client.canonical_dispatch_call_count - dispatch_call_count_before
            )
            trial["trial"] = trial_number
            trial["canonical_dispatch_call_count"] = dispatch_call_count
            trial["stabilization_operation_count"] = stabilization_dispatch_count
            if dispatch_call_count != 1:
                pytest.fail(
                    "RR-09.2 canonical memory scenario did not exercise one grouped dispatch: "
                    f"{scenario.name} observed {dispatch_call_count}."
                )
            trial_results.append(
                {
                    "name": scenario.name,
                    "record_count": record_count,
                    "trial": trial,
                }
            )
            trial_results_by_name[scenario.name].append(trial)
        trial_records.append({"trial": trial_number, "results": trial_results})

    primary_results: list[dict[str, object]] = []
    for name in EPIC6_MEMORY_CANONICAL_SCENARIO_NAMES:
        trials = trial_results_by_name[name]
        record_count = int(name.rsplit("_", maxsplit=1)[-1])
        output_payload_bytes = trials[0].get("output_payload_bytes") if trials else None
        error_count = sum(1 for trial in trials if trial.get("status") != "measured")
        primary_results.append(
            {
                "name": name,
                "category": "canonical_execution_memory",
                "kind": "isolated_postgresql_tracemalloc",
                "execution_boundary": "canonical_dispatch_included",
                "status": "measured" if error_count == 0 else "failed",
                "reason": None if error_count == 0 else "one_or_more_trials_not_measured_or_failed",
                "trial_count": len(trials),
                "error_count": error_count,
                "input_payload_bytes": None,
                "output_payload_bytes": output_payload_bytes,
                "record_count": record_count,
                "projection_changes_result": False,
                "tracing_configuration": dict(EPIC6_MEMORY_TRACING_CONFIGURATION),
                "trials": trials,
                "observed_result_metadata": {"returned_record_count": record_count},
                "scenario_metadata": {
                    "measurement_boundary": "canonical_dispatch_included",
                    "full_execution_boundary": True,
                    "input_payload_bytes_unavailable_reason": (
                        "Canonical dispatch receives structured request arguments rather than a "
                        "serialized input result string."
                    ),
                    "canonical_dispatch_call_count_per_trial": [
                        trial.get("canonical_dispatch_call_count") for trial in trials
                    ],
                    "stabilization_operation_count_per_trial": [
                        trial.get("stabilization_operation_count") for trial in trials
                    ],
                },
            }
        )
    return primary_results, trial_records


def _replace_catalog_assignments(
    fixture: _DatabaseFixture,
    action_names: tuple[str, ...],
) -> None:
    """Replace one disposable Persona's Registry assignments and parameter metadata.

    Args:
        fixture: Isolated persisted Persona whose relationships may be changed.
        action_names: Canonical Registry BIF names to assign to that Persona.
    """
    BuiltInFunctionPersonaRelationship.objects.filter(
        persona=fixture.persona,
        builtin_function__name__in=RESOURCE_REGISTRY_BIF_MANIFEST,
    ).delete()
    for function_name in action_names:
        function, _ = BuiltInFunction.objects.get_or_create(
            name=function_name,
            defaults={
                "description": "Isolated RR-04.1 Registry benchmark operation.",
                "last_modified_by": fixture.user,
            },
        )
        FunctionParameter.objects.filter(function=function).delete()
        FunctionParameter.objects.create(
            name="tenant_id",
            function=function,
            type="string",
            required=True,
            description="Isolated RR-04.1 tenant selector.",
        )
        if function_name == "resource_registry_list_datasets":
            FunctionParameter.objects.create(
                name="limit",
                function=function,
                type="integer",
                required=False,
                description="Isolated RR-04.1 list limit.",
            )
        BuiltInFunctionPersonaRelationship.objects.create(
            persona=fixture.persona,
            builtin_function=function,
            last_modified_by=fixture.user,
        )


def _publish_current_catalog(fixture: _DatabaseFixture) -> list[dict[str, object]]:
    """Publish the current Persona catalog through the live database-backed path.

    Args:
        fixture: Isolated Persona and request-context fixture.

    Returns:
        Fresh provider-envelope tools derived from a new relationship queryset.
    """
    return _publish_catalog_for_persona(fixture.persona)


def _publish_catalog_for_persona(persona: Persona) -> list[dict[str, object]]:
    """Publish one persisted Persona catalog through the live database-backed path.

    Args:
        persona: Persisted Persona whose current Registry assignments must be published.

    Returns:
        Fresh provider-envelope tools derived from a new relationship queryset.
    """
    relationships = BuiltInFunctionPersonaRelationship.objects.filter(persona=persona)
    _remaining, tools = prepare_registry_tools(
        relationships,
        persona=persona,
        provider="openai",
        statement="List available datasets.",
    )
    return tools


def _publish_catalog_for_persona_id(persona_id: int) -> list[dict[str, object]]:
    """Reload and publish one Persona catalog using the current worker connection.

    Args:
        persona_id: Persisted Persona primary key owned by the isolated benchmark fixture.

    Returns:
        Fresh provider-envelope tools built with a worker-local model instance.
    """
    return _publish_catalog_for_persona(Persona.objects.get(pk=persona_id))


def _extract_published_catalog_actions(tools: object) -> frozenset[str]:
    """Extract only action identifiers from provider-neutral publication output.

    Args:
        tools: Provider tool envelopes returned by the real publication function.

    Returns:
        Published Registry action names without retaining schema inputs.
    """
    if not isinstance(tools, list):
        return frozenset()
    actions: set[str] = set()
    for tool in tools:
        if not isinstance(tool, Mapping):
            continue
        function = tool.get("function")
        function_values = function if isinstance(function, Mapping) else {}
        parameters = function_values.get("parameters")
        parameter_values = parameters if isinstance(parameters, Mapping) else {}
        properties = parameter_values.get("properties")
        property_values = properties if isinstance(properties, Mapping) else {}
        action = property_values.get("action")
        action_values = action if isinstance(action, Mapping) else {}
        enum = action_values.get("enum")
        if isinstance(enum, list):
            actions.update(value for value in enum if isinstance(value, str))
    return frozenset(actions)


def _published_catalog_action_enums(tools: object) -> tuple[list[object], ...]:
    """Return mutable action-enum lists from one provider envelope without copying them.

    Args:
        tools: Provider tool envelopes returned by the real publication function.

    Returns:
        The nested action-enum lists whose object identities are relevant to caller isolation.
    """
    if not isinstance(tools, list):
        return ()
    action_enums: list[list[object]] = []
    for tool in tools:
        if not isinstance(tool, Mapping):
            continue
        function = tool.get("function")
        function_values = function if isinstance(function, Mapping) else {}
        parameters = function_values.get("parameters")
        parameter_values = parameters if isinstance(parameters, Mapping) else {}
        properties = parameter_values.get("properties")
        property_values = properties if isinstance(properties, Mapping) else {}
        action = property_values.get("action")
        action_values = action if isinstance(action, Mapping) else {}
        action_enum = action_values.get("enum")
        if isinstance(action_enum, list):
            action_enums.append(action_enum)
    return tuple(action_enums)


def _catalog_action_names() -> tuple[str, ...]:
    """Return the complete current Registry action inventory in deterministic order.

    Returns:
        All manifest-backed canonical function names.
    """
    return tuple(sorted(RESOURCE_REGISTRY_BIF_MANIFEST))


def _catalog_publication_matches(expected_actions: frozenset[str]) -> Callable[[object], str]:
    """Build a bounded classifier for current-assignment publication output.

    Args:
        expected_actions: Exact unprefixed actions that must appear in current publication.

    Returns:
        Scenario classifier accepting only the expected current catalog.
    """

    def classify_catalog(tools: object) -> str:
        """Classify one provider-envelope catalog without accepting stale actions.

        Args:
            tools: Publication output to inspect.

        Returns:
            ``success`` only when action inventory exactly matches current assignment metadata.
        """
        return (
            "success" if _extract_published_catalog_actions(tools) == expected_actions else "failed"
        )

    return classify_catalog


def _catalog_schema_reflects_current_parameters(
    fixture: _DatabaseFixture,
    *,
    candidate_arguments: dict[str, object],
    expected_valid: bool,
) -> dict[str, object]:
    """Build and validate a selected action schema from current persisted metadata.

    Args:
        fixture: Isolated fixture containing the list-datasets BIF assignment.
        candidate_arguments: Candidate selected-action arguments to validate.
        expected_valid: Expected result under the current parameter rows.

    Returns:
        Current catalog actions and selected-action validation evidence.
    """
    tools = _publish_current_catalog(fixture)
    definition = _operation_definition_for_action(fixture, "list_datasets")
    return {
        "actions": _extract_published_catalog_actions(tools),
        "valid": is_valid_grouped_registry_arguments(definition, candidate_arguments),
        "expected_valid": expected_valid,
    }


def _classify_current_parameter_schema(value: object) -> str:
    """Classify selected-action parameter freshness evidence.

    Args:
        value: Current catalog and validator evidence produced by the benchmark operation.

    Returns:
        Success only when fresh publication and schema validation agree.
    """
    if not isinstance(value, Mapping):
        return "failed"
    actions = value.get("actions")
    return (
        "success"
        if (
            isinstance(actions, frozenset)
            and "list_datasets" in actions
            and value.get("valid") == value.get("expected_valid")
        )
        else "failed"
    )


def _measure_epic4_publication(
    fixture: _DatabaseFixture,
    *,
    name: str,
    actions: tuple[str, ...],
    warmups: int,
    iterations: int,
) -> dict[str, object]:
    """Measure one stable current-assignment publication state after untimed setup.

    Args:
        fixture: Isolated persisted fixture for this trial.
        name: Registered RR-04.1 scenario identifier.
        actions: Exact canonical assignments to publish.
        warmups: Excluded publication warmups.
        iterations: Measured publication iterations.

    Returns:
        One actual publication benchmark record.
    """
    _replace_catalog_assignments(fixture, actions)
    expected_actions = frozenset(action.removeprefix("resource_registry_") for action in actions)
    assert _extract_published_catalog_actions(_publish_current_catalog(fixture)) == expected_actions
    return _measure_epic4_current_publication(
        fixture,
        name=name,
        expected_actions=expected_actions,
        warmups=warmups,
        iterations=iterations,
    )


def _measure_epic4_current_publication(
    fixture: _DatabaseFixture,
    *,
    name: str,
    expected_actions: frozenset[str],
    warmups: int,
    iterations: int,
) -> dict[str, object]:
    """Measure publication after a separately verified current-state change.

    Args:
        fixture: Isolated persisted fixture for this trial.
        name: Registered RR-04.1 scenario identifier.
        expected_actions: Exact current action set expected in every publication.
        warmups: Excluded publication warmups.
        iterations: Measured publication iterations.

    Returns:
        One actual publication benchmark record without mutation time.
    """
    return run_scenario(
        BenchmarkScenario(
            name,
            "publication",
            "isolated_database",
            lambda: _publish_current_catalog(fixture),
            _catalog_publication_matches(expected_actions),
            EPIC4_CATALOG_SCENARIO_BOUNDARIES[name],
        ),
        warmups=warmups,
        iterations=iterations,
    )


def _measure_epic4_parameter_freshness(
    fixture: _DatabaseFixture,
    *,
    name: str,
    candidate_arguments: dict[str, object],
    expected_valid: bool,
    warmups: int,
    iterations: int,
) -> dict[str, object]:
    """Measure a stable selected-action schema after untimed parameter mutation.

    Args:
        fixture: Isolated fixture containing the list-datasets action.
        name: Registered RR-04.1 scenario identifier.
        candidate_arguments: Arguments validated against current parameter metadata.
        expected_valid: Expected outcome from current parameter metadata.
        warmups: Excluded freshness warmups.
        iterations: Measured freshness iterations.

    Returns:
        One real publication-plus-selected-schema benchmark record.
    """
    return run_scenario(
        BenchmarkScenario(
            name,
            "freshness",
            "isolated_database",
            lambda: _catalog_schema_reflects_current_parameters(
                fixture,
                candidate_arguments=candidate_arguments,
                expected_valid=expected_valid,
            ),
            _classify_current_parameter_schema,
            EPIC4_CATALOG_SCENARIO_BOUNDARIES[name],
        ),
        warmups=warmups,
        iterations=iterations,
    )


def _build_epic4_publication_concurrency_scenario(
    fixture: _DatabaseFixture,
    *,
    worker_count: int,
) -> BenchmarkScenario:
    """Build an independent-thread current-catalog publication workload.

    Args:
        fixture: Persisted fixture shared only through database records.
        worker_count: Declared bounded worker count.

    Returns:
        Publication scenario with fresh thread-local database connection handling.
    """
    expected_actions = frozenset(
        action.removeprefix("resource_registry_") for action in _catalog_action_names()
    )

    def publish_with_independent_connection() -> list[dict[str, object]]:
        """Publish the same Persona from a fresh worker-local database connection.

        Returns:
            Current provider envelopes derived from the shared read-only persisted fixture.
        """
        close_old_connections()
        try:
            return _publish_catalog_for_persona_id(fixture.persona.pk)
        finally:
            close_old_connections()

    name = f"catalog.concurrent.publication.{worker_count}"
    return BenchmarkScenario(
        name,
        "concurrency",
        "isolated_database_threaded",
        publish_with_independent_connection,
        _catalog_publication_matches(expected_actions),
        EPIC4_CATALOG_SCENARIO_BOUNDARIES[name],
    )


def _build_epic4_multi_context_publication_concurrency_scenario(
    first_fixture: _DatabaseFixture,
    second_fixture: _DatabaseFixture,
    *,
    first_expected_actions: frozenset[str],
    second_expected_actions: frozenset[str],
    worker_count: int,
) -> BenchmarkScenario:
    """Build concurrent publication evidence for two isolated Persona and Organization contexts.

    Args:
        first_fixture: First persisted Persona, Organization, Workspace, and dataset context.
        second_fixture: Second persisted Persona, Organization, Workspace, and dataset context.
        first_expected_actions: Exact unprefixed actions assigned to the first Persona.
        second_expected_actions: Exact unprefixed actions assigned to the second Persona.
        worker_count: Declared bounded worker count.

    Returns:
        A scenario that publishes both current catalogs with fresh worker-local connections.

    Raises:
        ValueError: If the two contexts do not establish distinct action and organization boundaries.
    """
    if (
        first_fixture.organization.pk == second_fixture.organization.pk
        or first_fixture.persona.pk == second_fixture.persona.pk
        or first_fixture.namespace_id == second_fixture.namespace_id
        or not first_expected_actions
        or not second_expected_actions
        or first_expected_actions & second_expected_actions
    ):
        raise ValueError("Multi-context publication requires distinct Persona boundaries.")

    def publish_both_contexts_with_independent_connections() -> dict[str, object]:
        """Publish both contexts and test caller-owned nested schema mutation isolation.

        Returns:
            Sanitized action, metadata, ordering, and nested-schema isolation evidence.
        """
        close_old_connections()
        try:
            first_catalog = _publish_catalog_for_persona_id(first_fixture.persona.pk)
            second_catalog = _publish_catalog_for_persona_id(second_fixture.persona.pk)
            first_actions = _extract_published_catalog_actions(first_catalog)
            second_actions = _extract_published_catalog_actions(second_catalog)
            serialized_first_catalog = json.dumps(first_catalog, sort_keys=True)
            serialized_second_catalog = json.dumps(second_catalog, sort_keys=True)
            first_action_enums = _published_catalog_action_enums(first_catalog)
            second_action_enums = _published_catalog_action_enums(second_catalog)
            has_independent_nested_schemas = (
                bool(first_action_enums)
                and bool(second_action_enums)
                and all(
                    first_action_enum is not second_action_enum
                    for first_action_enum in first_action_enums
                    for second_action_enum in second_action_enums
                )
            )
            first_action_enums[0].append("caller_owned_mutation")
            republished_first_catalog = _publish_catalog_for_persona_id(first_fixture.persona.pk)
            republished_second_catalog = _publish_catalog_for_persona_id(second_fixture.persona.pk)
            serialized_republished_first = json.dumps(republished_first_catalog, sort_keys=True)
            serialized_republished_second = json.dumps(republished_second_catalog, sort_keys=True)
            organization_identifiers = (
                first_fixture.organization.name,
                second_fixture.organization.name,
                str(first_fixture.namespace_id),
                str(second_fixture.namespace_id),
            )
            return {
                "first_actions": first_actions,
                "second_actions": second_actions,
                "action_sets_are_disjoint": not bool(first_actions & second_actions),
                "organizations_are_distinct": (
                    first_fixture.organization.pk != second_fixture.organization.pk
                ),
                "datasets_are_organization_scoped": (
                    all(
                        dataset.organization_id == first_fixture.organization.pk
                        for dataset in first_fixture.datasets
                    )
                    and all(
                        dataset.organization_id == second_fixture.organization.pk
                        for dataset in second_fixture.datasets
                    )
                ),
                "organization_metadata_isolated": all(
                    identifier not in serialized_first_catalog
                    and identifier not in serialized_second_catalog
                    for identifier in organization_identifiers
                ),
                "nested_schemas_are_independent": has_independent_nested_schemas,
                "caller_mutation_is_isolated": (
                    "caller_owned_mutation" not in serialized_second_catalog
                    and "caller_owned_mutation" not in serialized_republished_first
                    and "caller_owned_mutation" not in serialized_republished_second
                ),
                "output_contract_order_is_preserved": (
                    serialized_first_catalog == serialized_republished_first
                    and serialized_second_catalog == serialized_republished_second
                ),
                "independent_persona_records": (
                    first_fixture.persona.pk != second_fixture.persona.pk
                ),
            }
        finally:
            close_old_connections()

    def classify_multi_context_publication(value: object) -> str:
        """Classify only fully isolated concurrent publications as successful.

        Args:
            value: Sanitized evidence returned from two worker-local publications.

        Returns:
            ``success`` when both Personas retain their exact current catalogs and isolation.
        """
        if not isinstance(value, Mapping):
            return "failed"
        return (
            "success"
            if (
                value.get("first_actions") == first_expected_actions
                and value.get("second_actions") == second_expected_actions
                and all(
                    value.get(name) is True
                    for name in (
                        "action_sets_are_disjoint",
                        "organizations_are_distinct",
                        "datasets_are_organization_scoped",
                        "organization_metadata_isolated",
                        "nested_schemas_are_independent",
                        "caller_mutation_is_isolated",
                        "output_contract_order_is_preserved",
                        "independent_persona_records",
                    )
                )
            )
            else "failed"
        )

    name = f"catalog.concurrent.multi_context.{worker_count}"
    return BenchmarkScenario(
        name,
        "concurrency",
        "isolated_database_threaded",
        publish_both_contexts_with_independent_connections,
        classify_multi_context_publication,
        EPIC4_CATALOG_SCENARIO_BOUNDARIES[name],
    )


def _measure_epic4_removed_assignment_execution(
    fixture: _DatabaseFixture,
    *,
    warmups: int,
    iterations: int,
) -> dict[str, object]:
    """Measure fresh canonical admission after a Persona assignment is removed.

    Args:
        fixture: Isolated fixture containing the assigned list-datasets operation.
        warmups: Excluded canonical-call warmups.
        iterations: Measured canonical-call iterations.

    Returns:
        Canonical-dispatch security freshness evidence.
    """
    _replace_catalog_assignments(fixture, ("resource_registry_list_datasets",))
    client = _CanonicalGroupedDispatchClient(fixture.persona)
    tenant_id = str(fixture.namespace_id)

    def execute_before_and_after_removal() -> dict[str, object]:
        """Call through canonical dispatch before and after deleting the assignment.

        Returns:
            Sanitized success/denial envelopes and canonical-dispatch count.
        """
        count_before = client.canonical_dispatch_call_count
        first = _run_grouped_call(
            client,
            fixture.request,
            group_name="resource_registry_inspect",
            action="list_datasets",
            arguments={"tenant_id": tenant_id, "limit": 5},
        )
        relationship = BuiltInFunctionPersonaRelationship.objects.get(
            persona=fixture.persona,
            builtin_function__name="resource_registry_list_datasets",
        )
        relationship.delete()
        try:
            second = _run_grouped_call(
                client,
                fixture.request,
                group_name="resource_registry_inspect",
                action="list_datasets",
                arguments={"tenant_id": tenant_id, "limit": 5},
            )
        finally:
            BuiltInFunctionPersonaRelationship.objects.create(
                persona=fixture.persona,
                builtin_function_id=relationship.builtin_function_id,
                last_modified_by=fixture.user,
            )
        return {
            "first": _decode_grouped_result(first),
            "second": _decode_grouped_result(second),
            "canonical_calls": client.canonical_dispatch_call_count - count_before,
        }

    def classify_removed_assignment(value: object) -> str:
        """Require a successful first call and pre-dispatch denial after revocation.

        Args:
            value: Sanitized canonical-call evidence.

        Returns:
            Rejected when revoked access is denied before an unauthorized handler call.
        """
        if not isinstance(value, Mapping):
            return "failed"
        first = value.get("first")
        second = value.get("second")
        return (
            "rejected"
            if (
                isinstance(first, Mapping)
                and first.get("success") is True
                and isinstance(second, Mapping)
                and second.get("status") == 403
                and value.get("canonical_calls") == 1
            )
            else "failed"
        )

    return run_scenario(
        BenchmarkScenario(
            "catalog.security.removed_assignment_execution",
            "security",
            "isolated_database",
            execute_before_and_after_removal,
            classify_removed_assignment,
            EPIC4_CATALOG_SCENARIO_BOUNDARIES["catalog.security.removed_assignment_execution"],
        ),
        warmups=warmups,
        iterations=iterations,
    )


def _canonical_list_datasets_call(
    client: _CanonicalGroupedDispatchClient,
    fixture: _DatabaseFixture,
) -> object:
    """Invoke the current list-datasets action through the canonical grouped path.

    Args:
        client: Client that delegates to the unchanged shared canonical dispatcher.
        fixture: Isolated fixture carrying the selected Persona and namespace.

    Returns:
        Original grouped action response after canonical dispatch.
    """
    return _run_grouped_call(
        client,
        fixture.request,
        group_name="resource_registry_inspect",
        action="list_datasets",
        arguments={"tenant_id": str(fixture.namespace_id), "limit": 5},
    )


def _classify_epic4_canonical_security_transition(
    value: object,
    *,
    expected_status: int,
    expected_canonical_calls: int,
) -> str:
    """Classify a fresh canonical security transition without retaining domain data.

    Args:
        value: Sanitized before-and-after canonical execution evidence.
        expected_status: Existing grouped error status for the denied second call.
        expected_canonical_calls: Dispatcher entries expected across the two calls.

    Returns:
        ``rejected`` only when the stale authorization path cannot return domain data.
    """
    if not isinstance(value, Mapping):
        return "failed"
    first = value.get("first")
    second = value.get("second")
    return (
        "rejected"
        if (
            isinstance(first, Mapping)
            and first.get("success") is True
            and isinstance(second, Mapping)
            and second.get("success") is False
            and second.get("status") == expected_status
            and not second.get("data")
            and value.get("canonical_calls") == expected_canonical_calls
            and value.get("domain_state_unchanged") is True
        )
        else "failed"
    )


def _measure_epic4_capability_revoked_execution(
    fixture: _DatabaseFixture,
    *,
    warmups: int,
    iterations: int,
) -> dict[str, object]:
    """Measure canonical denial after a current request capability is revoked.

    Args:
        fixture: Isolated fixture containing the list-datasets action.
        warmups: Excluded canonical-call warmups.
        iterations: Measured canonical-call iterations.

    Returns:
        Canonical capability-revocation evidence with expected denial outcomes.
    """
    _replace_catalog_assignments(fixture, ("resource_registry_list_datasets",))
    client = _CanonicalGroupedDispatchClient(fixture.persona)
    disabled_key = f"resource_registry_disabled_capabilities:{fixture.namespace_id}"

    def execute_before_and_after_capability_revocation() -> dict[str, object]:
        """Execute an authorized call then revoke its capability for the same request.

        Returns:
            Sanitized success, denial, dispatch, and domain-state evidence.
        """
        count_before = client.canonical_dispatch_call_count
        dataset_count_before = Dataset.objects.filter(tenant_id=fixture.namespace_id).count()
        first = _canonical_list_datasets_call(client, fixture)
        fixture.request.session[disabled_key] = ["resource_registry.dataset.list"]
        try:
            second = _canonical_list_datasets_call(client, fixture)
        finally:
            fixture.request.session.pop(disabled_key, None)
        dataset_count_after = Dataset.objects.filter(tenant_id=fixture.namespace_id).count()
        return {
            "first": _decode_grouped_result(first),
            "second": _decode_grouped_result(second),
            "canonical_calls": client.canonical_dispatch_call_count - count_before,
            "domain_state_unchanged": dataset_count_before == dataset_count_after,
        }

    return run_scenario(
        BenchmarkScenario(
            "catalog.security.capability_revoked_execution",
            "security",
            "isolated_database",
            execute_before_and_after_capability_revocation,
            partial(
                _classify_epic4_canonical_security_transition,
                expected_status=403,
                expected_canonical_calls=2,
            ),
            EPIC4_CATALOG_SCENARIO_BOUNDARIES["catalog.security.capability_revoked_execution"],
        ),
        warmups=warmups,
        iterations=iterations,
    )


def _measure_epic4_workspace_disabled_execution(
    fixture: _DatabaseFixture,
    *,
    warmups: int,
    iterations: int,
) -> dict[str, object]:
    """Measure canonical denial after the current Workspace is disabled.

    Args:
        fixture: Isolated fixture carrying an enabled Workspace and list-datasets action.
        warmups: Excluded canonical-call warmups.
        iterations: Measured canonical-call iterations.

    Returns:
        Canonical Workspace-disablement evidence with expected denial outcomes.
    """
    _replace_catalog_assignments(fixture, ("resource_registry_list_datasets",))
    client = _CanonicalGroupedDispatchClient(fixture.persona)

    def execute_before_and_after_workspace_disablement() -> dict[str, object]:
        """Execute an authorized call then disable the selected Workspace.

        Returns:
            Sanitized success, denial, dispatch, and domain-state evidence.
        """
        count_before = client.canonical_dispatch_call_count
        dataset_count_before = Dataset.objects.filter(tenant_id=fixture.namespace_id).count()
        first = _canonical_list_datasets_call(client, fixture)
        Workspace.objects.filter(pk=fixture.workspace.pk).update(resource_registry_enabled=False)
        try:
            second = _canonical_list_datasets_call(client, fixture)
        finally:
            Workspace.objects.filter(pk=fixture.workspace.pk).update(resource_registry_enabled=True)
        dataset_count_after = Dataset.objects.filter(tenant_id=fixture.namespace_id).count()
        return {
            "first": _decode_grouped_result(first),
            "second": _decode_grouped_result(second),
            "canonical_calls": client.canonical_dispatch_call_count - count_before,
            "domain_state_unchanged": dataset_count_before == dataset_count_after,
        }

    return run_scenario(
        BenchmarkScenario(
            "catalog.security.workspace_disabled_execution",
            "security",
            "isolated_database",
            execute_before_and_after_workspace_disablement,
            partial(
                _classify_epic4_canonical_security_transition,
                expected_status=403,
                expected_canonical_calls=2,
            ),
            EPIC4_CATALOG_SCENARIO_BOUNDARIES["catalog.security.workspace_disabled_execution"],
        ),
        warmups=warmups,
        iterations=iterations,
    )


def _measure_epic4_dataset_visibility_revoked_execution(
    fixture: _DatabaseFixture,
    *,
    warmups: int,
    iterations: int,
) -> dict[str, object]:
    """Measure canonical stale-selection denial after Dataset visibility is revoked.

    Args:
        fixture: Isolated fixture carrying the selected Dataset and get-dataset action.
        warmups: Excluded canonical-call warmups.
        iterations: Measured canonical-call iterations.

    Returns:
        Canonical Dataset-visibility revocation evidence with expected denial outcomes.
    """
    _replace_catalog_assignments(fixture, ("resource_registry_get_dataset",))
    get_dataset_function = BuiltInFunction.objects.get(name="resource_registry_get_dataset")
    FunctionParameter.objects.create(
        name="dataset_id",
        function=get_dataset_function,
        type="string",
        required=True,
        description="Disposable RR-04.1 contextual dataset selector.",
    )
    client = _CanonicalGroupedDispatchClient(fixture.persona)
    selected_dataset = fixture.datasets[0]

    def execute_before_and_after_dataset_visibility_revocation() -> dict[str, object]:
        """Execute a contextual read then archive its selected Dataset before retrying.

        Returns:
            Sanitized success, denial, dispatch, and domain-state evidence.
        """
        _save_selected_datasets(fixture, (selected_dataset,))
        count_before = client.canonical_dispatch_call_count
        dataset_count_before = Dataset.objects.filter(tenant_id=fixture.namespace_id).count()
        first = _run_grouped_call(
            client,
            fixture.request,
            group_name="resource_registry_inspect",
            action="get_dataset",
            arguments={"dataset_id": "this dataset"},
        )
        Dataset.objects.filter(pk=selected_dataset.pk).update(status=DatasetStatus.ARCHIVED)
        try:
            second = _run_grouped_call(
                client,
                fixture.request,
                group_name="resource_registry_inspect",
                action="get_dataset",
                arguments={"dataset_id": "this dataset"},
            )
        finally:
            Dataset.objects.filter(pk=selected_dataset.pk).update(status=DatasetStatus.ACTIVE)
        dataset_count_after = Dataset.objects.filter(tenant_id=fixture.namespace_id).count()
        return {
            "first": _decode_grouped_result(first),
            "second": _decode_grouped_result(second),
            "canonical_calls": client.canonical_dispatch_call_count - count_before,
            "domain_state_unchanged": dataset_count_before == dataset_count_after,
        }

    return run_scenario(
        BenchmarkScenario(
            "catalog.security.dataset_visibility_revoked_execution",
            "security",
            "isolated_database",
            execute_before_and_after_dataset_visibility_revocation,
            partial(
                _classify_epic4_canonical_security_transition,
                expected_status=400,
                expected_canonical_calls=1,
            ),
            EPIC4_CATALOG_SCENARIO_BOUNDARIES[
                "catalog.security.dataset_visibility_revoked_execution"
            ],
        ),
        warmups=warmups,
        iterations=iterations,
    )


def _run_epic4_catalog_benchmark_trials(
    *,
    warmups: int,
    iterations: int,
) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[str, object]]:
    """Run three isolated RR-04.1 database trials with untimed metadata setup.

    Args:
        warmups: Excluded calls in each serial and concurrency scenario.
        iterations: Measured calls in each serial and concurrency scenario.

    Returns:
        First-trial results, all trial records, and separate catalog allocation evidence.
    """
    all_actions = _catalog_action_names()
    primary_results: list[dict[str, object]] = []
    trials: list[dict[str, object]] = []
    memory_result: dict[str, object] | None = None
    fixture_index_base = 1_000 + (uuid.uuid4().int % 8_997)
    while User.objects.filter(
        username__in=tuple(
            f"registry-benchmark-member-{fixture_index_base + fixture_offset}"
            for fixture_offset in (*range(1, 4), *range(101, 104))
        )
    ).exists():
        fixture_index_base = 1_000 + (uuid.uuid4().int % 8_997)
    for trial_number in range(1, 4):
        fixture = _create_database_fixture(fixture_index=fixture_index_base + trial_number)
        partial_actions = all_actions[:10]
        results = [
            _measure_epic4_publication(
                fixture,
                name="catalog.publication.one",
                actions=("resource_registry_list_datasets",),
                warmups=warmups,
                iterations=iterations,
            ),
            _measure_epic4_publication(
                fixture,
                name="catalog.publication.partial",
                actions=partial_actions,
                warmups=warmups,
                iterations=iterations,
            ),
            _measure_epic4_publication(
                fixture,
                name="catalog.publication.full",
                actions=all_actions,
                warmups=warmups,
                iterations=iterations,
            ),
            _measure_epic4_publication(
                fixture,
                name="catalog.publication.repeated",
                actions=all_actions,
                warmups=warmups,
                iterations=iterations,
            ),
        ]
        _replace_catalog_assignments(fixture, partial_actions)
        removed_name = partial_actions[0]
        BuiltInFunctionPersonaRelationship.objects.filter(
            persona=fixture.persona,
            builtin_function__name=removed_name,
        ).delete()
        expected_removed = frozenset(
            action.removeprefix("resource_registry_")
            for action in partial_actions
            if action != removed_name
        )
        assert (
            _extract_published_catalog_actions(_publish_current_catalog(fixture))
            == expected_removed
        )
        results.append(
            _measure_epic4_current_publication(
                fixture,
                name="catalog.freshness.assignment_remove",
                expected_actions=expected_removed,
                warmups=warmups,
                iterations=iterations,
            )
        )
        results.append(
            _measure_epic4_publication(
                fixture,
                name="catalog.freshness.assignment_add",
                actions=("resource_registry_list_datasets", "resource_registry_get_dataset"),
                warmups=warmups,
                iterations=iterations,
            )
        )
        results.append(
            _measure_epic4_publication(
                fixture,
                name="catalog.freshness.assignment_replace",
                actions=partial_actions[5:],
                warmups=warmups,
                iterations=iterations,
            )
        )
        _replace_catalog_assignments(fixture, ("resource_registry_list_datasets",))
        list_function = BuiltInFunction.objects.get(name="resource_registry_list_datasets")
        limit_parameter = FunctionParameter.objects.get(function=list_function, name="limit")
        limit_parameter.required = True
        limit_parameter.save(update_fields=["required"])
        results.append(
            _measure_epic4_parameter_freshness(
                fixture,
                name="catalog.freshness.parameter_required",
                candidate_arguments={"tenant_id": str(fixture.namespace_id)},
                expected_valid=False,
                warmups=warmups,
                iterations=iterations,
            )
        )
        limit_parameter.type = "string"
        limit_parameter.save(update_fields=["type"])
        results.append(
            _measure_epic4_parameter_freshness(
                fixture,
                name="catalog.freshness.parameter_type",
                candidate_arguments={"tenant_id": str(fixture.namespace_id), "limit": 5},
                expected_valid=False,
                warmups=warmups,
                iterations=iterations,
            )
        )
        FunctionParameter.objects.create(
            name="benchmark_filter",
            function=list_function,
            type="string",
            required=False,
            description="Disposable RR-04.1 added parameter.",
        )
        added_properties = _operation_definition_for_action(
            fixture, "list_datasets"
        ).parameters.get("properties")
        assert isinstance(added_properties, Mapping) and "benchmark_filter" in added_properties
        FunctionParameter.objects.filter(function=list_function, name="benchmark_filter").delete()
        results.append(
            _measure_epic4_parameter_freshness(
                fixture,
                name="catalog.freshness.parameter_add_remove",
                candidate_arguments={"tenant_id": str(fixture.namespace_id), "limit": "five"},
                expected_valid=True,
                warmups=warmups,
                iterations=iterations,
            )
        )
        _replace_catalog_assignments(fixture, all_actions)
        if memory_result is None:
            memory_result = measure_memory_usage(
                name="catalog.memory.catalog_construction",
                operation=partial(_publish_current_catalog, fixture),
                iterations=30,
            )
        for worker_count in (1, 10, 25):
            results.append(
                run_concurrent_database_scenario(
                    _build_epic4_publication_concurrency_scenario(
                        fixture,
                        worker_count=worker_count,
                    ),
                    workers=worker_count,
                    warmups=warmups,
                    iterations=iterations,
                )
            )
        second_fixture = _create_database_fixture(
            fixture_index=fixture_index_base + 100 + trial_number
        )
        first_multi_context_actions = all_actions[:10]
        second_multi_context_actions = all_actions[10:]
        _replace_catalog_assignments(fixture, first_multi_context_actions)
        _replace_catalog_assignments(second_fixture, second_multi_context_actions)
        first_expected_actions = frozenset(
            action.removeprefix("resource_registry_") for action in first_multi_context_actions
        )
        second_expected_actions = frozenset(
            action.removeprefix("resource_registry_") for action in second_multi_context_actions
        )
        for worker_count in (1, 10, 25):
            results.append(
                run_concurrent_database_scenario(
                    _build_epic4_multi_context_publication_concurrency_scenario(
                        fixture,
                        second_fixture,
                        first_expected_actions=first_expected_actions,
                        second_expected_actions=second_expected_actions,
                        worker_count=worker_count,
                    ),
                    workers=worker_count,
                    warmups=warmups,
                    iterations=iterations,
                )
            )
        results.append(
            _measure_epic4_removed_assignment_execution(
                fixture,
                warmups=warmups,
                iterations=iterations,
            )
        )
        results.extend(
            (
                _measure_epic4_capability_revoked_execution(
                    fixture,
                    warmups=warmups,
                    iterations=iterations,
                ),
                _measure_epic4_workspace_disabled_execution(
                    fixture,
                    warmups=warmups,
                    iterations=iterations,
                ),
                _measure_epic4_dataset_visibility_revoked_execution(
                    fixture,
                    warmups=warmups,
                    iterations=iterations,
                ),
            )
        )
        if trial_number == 1:
            primary_results = results
        trials.append({"trial": trial_number, "results": results})
    if memory_result is None:
        pytest.fail("RR-04.1 catalog allocation measurement did not execute.")
    return primary_results, trials, memory_result


def _build_epic5_ready_versions(
    fixture: _DatabaseFixture,
    *,
    trial_number: int,
) -> tuple[DatasetVersion, ...]:
    """Create one READY Version for every RR-05.1 fixture Dataset before timing.

    Args:
        fixture: Isolated database fixture whose datasets are stable for one trial.
        trial_number: Independent trial number used only in non-sensitive fixture values.

    Returns:
        Exact READY versions in the same order as ``fixture.datasets``.
    """
    return tuple(
        DatasetVersion.objects.create(
            dataset=dataset,
            version_number=trial_number,
            source_uri=f"benchmark://epic5/{trial_number}/{dataset.pk}",
            content_hash=f"epic5-ready-{trial_number}-{dataset.pk}",
            status=DatasetVersionStatus.READY,
        )
        for dataset in fixture.datasets
    )


def _build_epic5_selections(
    fixture: _DatabaseFixture,
    datasets: tuple[Dataset, ...],
    versions: tuple[DatasetVersion, ...],
    *,
    version_pinned: bool,
) -> list[VerifiedDatasetSelection]:
    """Build ordered cached selections for one fixed RR-05.1 workload.

    Args:
        fixture: Isolated fixture supplying the admitted namespace and Workspace.
        datasets: Ordered datasets included in the workload.
        versions: READY versions aligned with ``fixture.datasets``.
        version_pinned: Whether each selection carries its exact READY version identifier.

    Returns:
        Serializable selections in their measured cache ordering.
    """
    version_by_dataset_id = {str(version.dataset_id): version for version in versions}
    return [
        VerifiedDatasetSelection(
            namespace_id=str(fixture.namespace_id),
            workspace_id=fixture.workspace.pk,
            dataset_id=str(dataset.pk),
            version_id=(str(version_by_dataset_id[str(dataset.pk)].pk) if version_pinned else None),
        )
        for dataset in datasets
    ]


def _classify_epic5_prompt_context(
    value: object,
    *,
    expected_dataset_ids: tuple[str, ...],
    expected_version_ids: tuple[str, ...] = (),
    omitted_dataset_ids: tuple[str, ...] = (),
) -> str:
    """Classify an ordered prompt without retaining business payloads in artifacts.

    Args:
        value: Actual prompt-context result.
        expected_dataset_ids: Dataset IDs that must be present in first-seen cache order.
        expected_version_ids: READY version IDs that must be rendered when selections are pinned.
        omitted_dataset_ids: Dataset IDs that must be withheld after failed revalidation.

    Returns:
        ``success`` only when visible and omitted references match the fixed contract.
    """
    if not isinstance(value, str):
        return "failed"
    dataset_positions = [value.find(dataset_id) for dataset_id in expected_dataset_ids]
    if any(position < 0 for position in dataset_positions):
        return "failed"
    if dataset_positions != sorted(dataset_positions):
        return "failed"
    if any(version_id not in value for version_id in expected_version_ids):
        return "failed"
    if any(dataset_id in value for dataset_id in omitted_dataset_ids):
        return "failed"
    return "success"


def _classify_epic5_dataset_ids(
    value: object,
    *,
    expected_dataset_ids: tuple[str, ...],
) -> str:
    """Classify verified comparison discovery by exact ordered Dataset IDs.

    Args:
        value: Actual comparison-ID discovery result.
        expected_dataset_ids: Visible IDs in required first-seen order.

    Returns:
        ``success`` when the complete ordered tuple is preserved.
    """
    return "success" if value == expected_dataset_ids else "failed"


def _classify_epic5_canonical_admission_freshness(value: object) -> str:
    """Classify a cached-selection state change that must not disclose stale data.

    Args:
        value: Sanitized pair of grouped call results and dispatch count.

    Returns:
        ``rejected`` when the stale cached selection is blocked before domain data returns.
    """
    if not isinstance(value, Mapping):
        return "failed"
    first = value.get("first")
    second = value.get("second")
    return (
        "rejected"
        if (
            isinstance(first, Mapping)
            and first.get("success") is True
            and isinstance(second, Mapping)
            and second.get("status") == 400
            and not second.get("data")
            and value.get("canonical_call_count") == 1
        )
        else "failed"
    )


def _build_epic5_candidate_comparison(
    candidate_results: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Compare candidate records with frozen RR-05.1 equivalents without altering history.

    Args:
        candidate_results: First-trial RR-05.2 records matching the RR-05.1 scenario inventory.

    Returns:
        Sanitized before/after metric rows for the RR-05.2 report.

    Raises:
        pytest.Failed: If the historical artifact lacks an equivalent measured scenario.
    """
    baseline_path = Path(__file__).resolve().parent / "epic5_selection_baseline_results.json"
    baseline_payload = json.loads(baseline_path.read_text())
    raw_baseline_results = baseline_payload.get("results")
    if not isinstance(raw_baseline_results, list):
        pytest.fail("RR-05.1 baseline artifact has no comparable result list.")
    baseline_by_name = {
        str(result.get("name")): result
        for result in raw_baseline_results
        if isinstance(result, Mapping)
    }

    def extract_metrics(result: Mapping[str, object]) -> dict[str, int | float | None]:
        """Extract comparable scalar metrics from one sanitized benchmark record."""
        latency = result.get("latency")
        latency_values = latency if isinstance(latency, Mapping) else {}
        database_sql = result.get("database_sql")
        database_sql_values = database_sql if isinstance(database_sql, Mapping) else {}
        payload = result.get("result_payload_bytes")
        payload_values = payload if isinstance(payload, Mapping) else {}
        return {
            "p50_ms": _as_metric_number(latency_values.get("p50_ms")),
            "p95_ms": _as_metric_number(latency_values.get("p95_ms")),
            "p99_ms": _as_metric_number(latency_values.get("p99_ms")),
            "sql_query_count": _as_metric_number(database_sql_values.get("mean_query_count")),
            "sql_duration_ms": _as_metric_number(database_sql_values.get("mean_duration_ms")),
            "payload_bytes": _as_metric_number(payload_values.get("mean")),
            "error_count": _as_metric_number(result.get("error_count")),
        }

    comparisons: list[dict[str, object]] = []
    for candidate in candidate_results:
        scenario_name = str(candidate["name"])
        baseline = baseline_by_name.get(scenario_name)
        if not isinstance(baseline, Mapping):
            pytest.fail(f"RR-05.1 baseline lacks equivalent scenario: {scenario_name}.")
        comparisons.append(
            {
                "name": scenario_name,
                "before": extract_metrics(baseline),
                "after": extract_metrics(candidate),
            }
        )
    return comparisons


def _as_metric_number(value: object) -> int | float | None:
    """Return a sanitized scalar metric without treating booleans as measurements."""
    return value if isinstance(value, int | float) and not isinstance(value, bool) else None


def _run_epic5_selection_benchmark_trials(
    *,
    warmups: int,
    iterations: int,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Run three isolated RR-05.1 selection trials through existing production paths.

    Args:
        warmups: Excluded invocations per serial selection scenario.
        iterations: Measured invocations per serial selection scenario.

    Returns:
        First-trial records for the artifact table and all three independent trial records.
    """
    fixture_index_base = 1_000 + (uuid.uuid4().int % 8_997)
    while User.objects.filter(
        username__in=tuple(
            f"registry-benchmark-member-{fixture_index_base + trial_number}"
            for trial_number in range(1, 4)
        )
    ).exists():
        fixture_index_base = 1_000 + (uuid.uuid4().int % 8_997)
    primary_results: list[dict[str, object]] = []
    trials: list[dict[str, object]] = []
    for trial_number in range(1, 4):
        fixture = _create_database_fixture(fixture_index=fixture_index_base + trial_number)
        ready_versions = _build_epic5_ready_versions(fixture, trial_number=trial_number)
        adapter = DataRegistryExplorationRoutingAdapter()
        results: list[dict[str, object]] = []

        def measure_with_selections(
            scenario: BenchmarkScenario,
            selections_to_save: list[VerifiedDatasetSelection],
            benchmark_fixture: _DatabaseFixture = fixture,
            trial_results: list[dict[str, object]] = results,
        ) -> None:
            """Persist untimed selections then measure one unchanged production operation.

            Args:
                scenario: Fixed benchmark workload with a declared execution boundary.
                selections_to_save: Cache entries installed before warmups and timed iterations.
            """
            save_selections(benchmark_fixture.scope, selections_to_save)
            trial_results.append(run_scenario(scenario, warmups=warmups, iterations=iterations))

        for count in EPIC5_SELECTION_SCALE_COUNTS:
            selected_datasets = fixture.datasets[:count]
            expected_dataset_ids = tuple(str(dataset.pk) for dataset in selected_datasets)
            versionless_selections = _build_epic5_selections(
                fixture,
                selected_datasets,
                ready_versions,
                version_pinned=False,
            )
            measure_with_selections(
                BenchmarkScenario(
                    f"selection.prompt_context.versionless.{count}",
                    "selection",
                    "isolated_database",
                    partial(
                        get_registry_selection_prompt_context, fixture.request, fixture.persona
                    ),
                    partial(
                        _classify_epic5_prompt_context,
                        expected_dataset_ids=expected_dataset_ids,
                    ),
                    EPIC5_SELECTION_SCENARIO_BOUNDARIES[
                        f"selection.prompt_context.versionless.{count}"
                    ],
                    capture_database_sql=True,
                ),
                versionless_selections,
            )
            pinned_selections = _build_epic5_selections(
                fixture,
                selected_datasets,
                ready_versions,
                version_pinned=True,
            )
            expected_version_ids = tuple(str(version.pk) for version in ready_versions[:count])
            measure_with_selections(
                BenchmarkScenario(
                    f"selection.prompt_context.version_pinned.{count}",
                    "selection",
                    "isolated_database",
                    partial(
                        get_registry_selection_prompt_context, fixture.request, fixture.persona
                    ),
                    partial(
                        _classify_epic5_prompt_context,
                        expected_dataset_ids=expected_dataset_ids,
                        expected_version_ids=expected_version_ids,
                    ),
                    EPIC5_SELECTION_SCENARIO_BOUNDARIES[
                        f"selection.prompt_context.version_pinned.{count}"
                    ],
                    capture_database_sql=True,
                ),
                pinned_selections,
            )
            measure_with_selections(
                BenchmarkScenario(
                    f"selection.comparison_ids.{count}",
                    "selection",
                    "isolated_database",
                    partial(
                        adapter._comparison_dataset_ids,
                        request=fixture.request,
                        statement="",
                        tenant_id=str(fixture.namespace_id),
                    ),
                    partial(
                        _classify_epic5_dataset_ids,
                        expected_dataset_ids=expected_dataset_ids,
                    ),
                    EPIC5_SELECTION_SCENARIO_BOUNDARIES[f"selection.comparison_ids.{count}"],
                    capture_database_sql=True,
                ),
                versionless_selections,
            )
            measure_with_selections(
                BenchmarkScenario(
                    f"selection.version_revalidation.{count}",
                    "selection",
                    "isolated_database",
                    partial(
                        get_verified_registry_dataset_ids,
                        fixture.request,
                        str(fixture.namespace_id),
                    ),
                    partial(
                        _classify_epic5_dataset_ids,
                        expected_dataset_ids=expected_dataset_ids,
                    ),
                    EPIC5_SELECTION_SCENARIO_BOUNDARIES[f"selection.version_revalidation.{count}"],
                    capture_database_sql=True,
                ),
                pinned_selections,
            )

        valid_dataset = fixture.datasets[0]
        invalid_dataset = fixture.datasets[1]
        pending_version = DatasetVersion.objects.create(
            dataset=invalid_dataset,
            version_number=trial_number + 100,
            source_uri=f"benchmark://epic5/pending/{trial_number}",
            content_hash=f"epic5-pending-{trial_number}-{invalid_dataset.pk}",
            status=DatasetVersionStatus.PENDING,
        )
        mixed_selections = [
            VerifiedDatasetSelection(
                namespace_id=str(fixture.namespace_id),
                workspace_id=fixture.workspace.pk,
                dataset_id=str(valid_dataset.pk),
                version_id=None,
            ),
            VerifiedDatasetSelection(
                namespace_id=str(fixture.namespace_id),
                workspace_id=fixture.workspace.pk,
                dataset_id=str(invalid_dataset.pk),
                version_id=str(pending_version.pk),
            ),
        ]
        valid_dataset_ids = (str(valid_dataset.pk),)
        measure_with_selections(
            BenchmarkScenario(
                "selection.mixed_validity.prompt_context",
                "selection",
                "isolated_database",
                partial(get_registry_selection_prompt_context, fixture.request, fixture.persona),
                partial(
                    _classify_epic5_prompt_context,
                    expected_dataset_ids=valid_dataset_ids,
                    omitted_dataset_ids=(str(invalid_dataset.pk),),
                ),
                EPIC5_SELECTION_SCENARIO_BOUNDARIES["selection.mixed_validity.prompt_context"],
                capture_database_sql=True,
            ),
            mixed_selections,
        )
        measure_with_selections(
            BenchmarkScenario(
                "selection.mixed_validity.comparison_ids",
                "selection",
                "isolated_database",
                partial(
                    adapter._comparison_dataset_ids,
                    request=fixture.request,
                    statement="",
                    tenant_id=str(fixture.namespace_id),
                ),
                partial(
                    _classify_epic5_dataset_ids,
                    expected_dataset_ids=valid_dataset_ids,
                ),
                EPIC5_SELECTION_SCENARIO_BOUNDARIES["selection.mixed_validity.comparison_ids"],
                capture_database_sql=True,
            ),
            mixed_selections,
        )

        canonical_client = _CanonicalGroupedDispatchClient(fixture.persona)
        canonical_selections = _build_epic5_selections(
            fixture,
            (valid_dataset,),
            ready_versions,
            version_pinned=False,
        )
        save_selections(fixture.scope, canonical_selections)

        def execute_cached_selection_then_archive(
            fixed_client: _CanonicalGroupedDispatchClient = canonical_client,
            fixed_fixture: _DatabaseFixture = fixture,
            cached_dataset: Dataset = valid_dataset,
        ) -> dict[str, object]:
            """Execute an admitted contextual read, then archive its cached Dataset.

            Returns:
                Sanitized first/second grouped outcomes and real dispatcher-entry count.
            """
            call_count_before = fixed_client.canonical_dispatch_call_count
            first = _run_grouped_call(
                fixed_client,
                fixed_fixture.request,
                group_name="resource_registry_inspect",
                action="get_dataset",
                arguments={"dataset_id": "this dataset"},
            )
            Dataset.objects.filter(pk=cached_dataset.pk).update(status=DatasetStatus.ARCHIVED)
            try:
                second = _run_grouped_call(
                    fixed_client,
                    fixed_fixture.request,
                    group_name="resource_registry_inspect",
                    action="get_dataset",
                    arguments={"dataset_id": "this dataset"},
                )
            finally:
                Dataset.objects.filter(pk=cached_dataset.pk).update(status=DatasetStatus.ACTIVE)
            return {
                "first": _decode_grouped_result(first),
                "second": _decode_grouped_result(second),
                "canonical_call_count": fixed_client.canonical_dispatch_call_count
                - call_count_before,
            }

        results.append(
            run_scenario(
                BenchmarkScenario(
                    "selection.canonical_admission_freshness",
                    "selection",
                    "isolated_database",
                    execute_cached_selection_then_archive,
                    _classify_epic5_canonical_admission_freshness,
                    EPIC5_SELECTION_SCENARIO_BOUNDARIES["selection.canonical_admission_freshness"],
                    capture_database_sql=True,
                ),
                warmups=warmups,
                iterations=iterations,
            )
        )
        if {str(result["name"]) for result in results} != set(EPIC5_DATABASE_SCENARIO_NAMES):
            pytest.fail("RR-05.1 benchmark scenarios do not match the registered inventory.")
        if trial_number == 1:
            primary_results = results
        trials.append({"trial": trial_number, "results": results})
    return primary_results, trials


def _create_epic4_remediation_fixture_pair() -> tuple[_DatabaseFixture, _DatabaseFixture]:
    """Create two collision-safe fixture contexts for focused RR-04.1 remediation tests.

    Returns:
        Two persisted fixtures with different Users, Personas, Organizations, and Workspaces.
    """
    fixture_index = 1_000 + (uuid.uuid4().int % 8_997)
    while User.objects.filter(
        username__in=(
            f"registry-benchmark-member-{fixture_index}",
            f"registry-benchmark-member-{fixture_index + 1}",
        )
    ).exists():
        fixture_index = 1_000 + (uuid.uuid4().int % 8_997)
    return (
        _create_database_fixture(fixture_index=fixture_index),
        _create_database_fixture(fixture_index=fixture_index + 1),
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(
    os.environ.get("RESOURCE_REGISTRY_BENCHMARK_DATABASE") != "1",
    reason="Run explicitly with RESOURCE_REGISTRY_BENCHMARK_DATABASE=1 in an isolated test DB.",
)
def test_epic4_multi_persona_and_organization_publication_isolation() -> None:
    """Verify concurrent real publication keeps two Persona and Organization catalogs isolated."""
    first_fixture, second_fixture = _create_epic4_remediation_fixture_pair()
    all_actions = _catalog_action_names()
    first_actions = all_actions[:10]
    second_actions = all_actions[10:]
    _replace_catalog_assignments(first_fixture, first_actions)
    _replace_catalog_assignments(second_fixture, second_actions)
    result = run_concurrent_database_scenario(
        _build_epic4_multi_context_publication_concurrency_scenario(
            first_fixture,
            second_fixture,
            first_expected_actions=frozenset(
                action.removeprefix("resource_registry_") for action in first_actions
            ),
            second_expected_actions=frozenset(
                action.removeprefix("resource_registry_") for action in second_actions
            ),
            worker_count=1,
        ),
        workers=1,
        warmups=0,
        iterations=1,
    )

    assert result["status"] == "measured"
    assert result["outcomes"] == {"success": 1}
    assert result["error_count"] == 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(
    os.environ.get("RESOURCE_REGISTRY_BENCHMARK_DATABASE") != "1",
    reason="Run explicitly with RESOURCE_REGISTRY_BENCHMARK_DATABASE=1 in an isolated test DB.",
)
def test_epic4_canonical_assignment_removal_freshness() -> None:
    """Verify canonical grouped execution denies a removed Persona assignment."""
    fixture, _unused_fixture = _create_epic4_remediation_fixture_pair()
    result = _measure_epic4_removed_assignment_execution(fixture, warmups=0, iterations=1)

    assert result["status"] == "measured"
    assert result["outcomes"] == {"rejected": 1}
    assert result["error_count"] == 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(
    os.environ.get("RESOURCE_REGISTRY_BENCHMARK_DATABASE") != "1",
    reason="Run explicitly with RESOURCE_REGISTRY_BENCHMARK_DATABASE=1 in an isolated test DB.",
)
def test_epic4_canonical_capability_revocation_freshness() -> None:
    """Verify canonical grouped execution denies a newly revoked list capability."""
    fixture, _unused_fixture = _create_epic4_remediation_fixture_pair()
    result = _measure_epic4_capability_revoked_execution(fixture, warmups=0, iterations=1)

    assert result["status"] == "measured"
    assert result["outcomes"] == {"rejected": 1}
    assert result["error_count"] == 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(
    os.environ.get("RESOURCE_REGISTRY_BENCHMARK_DATABASE") != "1",
    reason="Run explicitly with RESOURCE_REGISTRY_BENCHMARK_DATABASE=1 in an isolated test DB.",
)
def test_epic4_canonical_workspace_disablement_freshness() -> None:
    """Verify canonical grouped execution denies a newly disabled Workspace."""
    fixture, _unused_fixture = _create_epic4_remediation_fixture_pair()
    result = _measure_epic4_workspace_disabled_execution(fixture, warmups=0, iterations=1)

    assert result["status"] == "measured"
    assert result["outcomes"] == {"rejected": 1}
    assert result["error_count"] == 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(
    os.environ.get("RESOURCE_REGISTRY_BENCHMARK_DATABASE") != "1",
    reason="Run explicitly with RESOURCE_REGISTRY_BENCHMARK_DATABASE=1 in an isolated test DB.",
)
def test_epic4_canonical_dataset_visibility_revocation_freshness() -> None:
    """Verify canonical grouped execution rejects an archived selected Dataset."""
    fixture, _unused_fixture = _create_epic4_remediation_fixture_pair()
    with override_settings(
        CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    ):
        cache.clear()
        result = _measure_epic4_dataset_visibility_revoked_execution(
            fixture,
            warmups=0,
            iterations=1,
        )
        cache.clear()

    assert result["status"] == "measured"
    assert result["outcomes"] == {"rejected": 1}
    assert result["error_count"] == 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(
    os.environ.get("RESOURCE_REGISTRY_BENCHMARK_DATABASE") != "1",
    reason="Run explicitly with RESOURCE_REGISTRY_BENCHMARK_DATABASE=1 in an isolated test DB.",
)
def test_record_isolated_database_baseline() -> None:
    """Append real authorization and Persona publication measurements to the baseline."""
    if get_benchmark_artifact_set() in {
        "epic3_grouped_baseline",
        EPIC4_ARTIFACT_SET,
        EPIC5_ARTIFACT_SET,
    }:
        pytest.skip("Dedicated Epic 3–5 artifact sets use their own database workloads.")
    if not _BENCHMARK_OUTPUT.is_file():
        pytest.fail("Run `python -m resource_registry.benchmarks` before this database benchmark.")
    baseline = json.loads(_BENCHMARK_OUTPUT.read_text())
    metadata = baseline["metadata"]
    original_results = baseline["results"]
    warmups = int(metadata["warmups"])
    iterations = int(metadata["iterations"])
    measured_results: list[dict[str, object]] = []
    with override_settings(
        CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    ):
        cache.clear()
        fixture = _create_database_fixture()
        with temporary_performance_configuration(
            enabled=True, sql_enabled=True, payload_enabled=True
        ):
            for count in (0, 1, 10, 50):
                _save_selected_datasets(fixture, fixture.datasets[:count])
                measured_results.append(
                    run_scenario(
                        BenchmarkScenario(
                            f"selection.{count}",
                            "selection",
                            "isolated_database",
                            lambda: get_registry_selection_prompt_context(
                                fixture.request, fixture.persona
                            ),
                            _classify_empty_context if count == 0 else _classify_nonempty_context,
                            "registry_pre_dispatch_only",
                        ),
                        warmups=warmups,
                        iterations=iterations,
                    )
                )
            _save_selected_datasets(fixture, fixture.datasets[:1])
            scoped_allowed_ids = f"resource_registry_allowed_dataset_ids:{fixture.namespace_id}"
            fixture.request.session[scoped_allowed_ids] = []
            measured_results.append(
                run_scenario(
                    BenchmarkScenario(
                        "selection.revoked",
                        "selection",
                        "isolated_database",
                        lambda: get_registry_selection_prompt_context(
                            fixture.request, fixture.persona
                        ),
                        _classify_empty_context,
                        "registry_pre_dispatch_only",
                    ),
                    warmups=warmups,
                    iterations=iterations,
                )
            )
            del fixture.request.session[scoped_allowed_ids]
            dispatch_client = SimpleNamespace(
                persona=fixture.persona,
                bif_execution_admission_service=BuiltInFunctionExecutionAdmissionService(),
                appLogger=logging.Logger(
                    "resource_registry.benchmarks.null", level=logging.CRITICAL
                ),
            )
            measured_results.append(
                run_scenario(
                    BenchmarkScenario(
                        "execution.admission_rejection",
                        "execution",
                        "isolated_database",
                        lambda: execute_grouped_registry_call(
                            dispatch_client,
                            fixture.request,
                            "resource_registry_inspect",
                            {"action": "explore_dataset", "arguments": {}},
                            execution_channel=BuiltInFunctionExecutionChannel.LLM_TOOL,
                        ),
                        _classify_rejected_grouped_result,
                        "registry_pre_dispatch_only",
                    ),
                    warmups=warmups,
                    iterations=iterations,
                )
            )
            measured_results.append(
                run_scenario(
                    BenchmarkScenario(
                        "execution.validation_rejection",
                        "execution",
                        "isolated_database",
                        lambda: execute_grouped_registry_call(
                            dispatch_client,
                            fixture.request,
                            "resource_registry_inspect",
                            {
                                "action": "list_datasets",
                                "arguments": {
                                    "tenant_id": str(fixture.namespace_id),
                                    "limit": "invalid",
                                },
                            },
                            execution_channel=BuiltInFunctionExecutionChannel.LLM_TOOL,
                        ),
                        _classify_rejected_grouped_result,
                        "registry_pre_dispatch_only",
                    ),
                    warmups=warmups,
                    iterations=iterations,
                )
            )
            pending_version = DatasetVersion.objects.create(
                dataset=fixture.datasets[0],
                version_number=1,
                source_uri="benchmark://not-fetched",
                content_hash="isolated-benchmark-pending",
                status=DatasetVersionStatus.PENDING,
            )
            save_selections(
                fixture.scope,
                [
                    VerifiedDatasetSelection(
                        namespace_id=str(fixture.namespace_id),
                        workspace_id=fixture.workspace.pk,
                        dataset_id=str(fixture.datasets[0].pk),
                        version_id=str(pending_version.pk),
                    )
                ],
            )
            measured_results.append(
                run_scenario(
                    BenchmarkScenario(
                        "selection.stale",
                        "selection",
                        "isolated_database",
                        lambda: get_registry_selection_prompt_context(
                            fixture.request, fixture.persona
                        ),
                        _classify_empty_context,
                        "registry_pre_dispatch_only",
                    ),
                    warmups=warmups,
                    iterations=iterations,
                )
            )
            relationships = BuiltInFunctionPersonaRelationship.objects.filter(
                persona=fixture.persona
            )
            measured_results.append(
                run_scenario(
                    BenchmarkScenario(
                        "publication.persona_queryset",
                        "publication",
                        "isolated_database",
                        lambda: prepare_registry_tools(
                            relationships,
                            persona=fixture.persona,
                            provider="openai",
                            statement="List datasets",
                        ),
                        execution_boundary="registry_pre_dispatch_only",
                    ),
                    warmups=warmups,
                    iterations=iterations,
                )
            )
            adapter_primary_results, adapter_trials = _run_adapter_benchmark_trials(
                warmups=warmups,
                iterations=iterations,
            )
            measured_results.extend(adapter_primary_results)
        cache.clear()
    replacement_names = {result["name"] for result in measured_results}
    results = [
        result for result in original_results if result["name"] not in replacement_names
    ] + measured_results
    metadata["database_engine"] = connections["default"].vendor
    metadata["adapter_routing_trial_count"] = len(adapter_trials)
    metadata["adapter_routing_trial_method"] = (
        "Three sequential trials with separate persisted fixtures in one isolated pytest process."
    )
    metadata["adapter_routing_trials"] = adapter_trials
    metadata["pre_optimization_adapter_comparison"] = _build_pre_optimization_adapter_comparison(
        adapter_primary_results
    )
    persist_baseline(metadata, results)
    assert all(result["status"] == "measured" for result in measured_results), [
        (result["name"], result["status"], result["outcomes"]) for result in measured_results
    ]
    adapter_results = {
        str(result["name"]): result
        for result in measured_results
        if result["name"] in DATABASE_ADAPTER_ROUTING_SCENARIO_NAMES
    }
    assert tuple(adapter_results) == DATABASE_ADAPTER_ROUTING_SCENARIO_NAMES
    assert {
        name: result["authorization_context_build_count_per_operation"]
        for name, result in adapter_results.items()
    } == {
        "routing.adapter_listing_authorized": 1.0,
        "routing.adapter_schema_authorized": 1.0,
        "routing.adapter_workspace_ambiguous": 1.0,
        "routing.adapter_revoked_before_bif": 1.0,
    }
    for adapter_result in adapter_results.values():
        stages = adapter_result["stages"]
        assert isinstance(stages, dict)
        assert "namespace_admission" not in stages
    for trial in adapter_trials:
        trial_results = trial["results"]
        assert isinstance(trial_results, list)
        assert len(trial_results) == len(DATABASE_ADAPTER_ROUTING_SCENARIO_NAMES)


def _record_epic5_selection_artifacts(*, expected_artifact_set: str, story_label: str) -> None:
    """Replace one RR-05 artifact's placeholders with isolated PostgreSQL trials.

    Args:
        expected_artifact_set: Dedicated artifact target selected for this benchmark execution.
        story_label: Human-readable RR label used in failure messages.
    """
    if get_benchmark_artifact_set() != expected_artifact_set:
        pytest.skip(
            f"{story_label} artifacts are selected only by the dedicated artifact-set environment."
        )
    if not _BENCHMARK_OUTPUT.is_file():
        pytest.fail(
            f"Run the {story_label} benchmark runner before the isolated database benchmark."
        )
    if connections["default"].vendor != "postgresql":
        pytest.fail(f"{story_label} acceptance requires guarded isolated PostgreSQL measurements.")
    baseline = json.loads(_BENCHMARK_OUTPUT.read_text())
    metadata = baseline["metadata"]
    original_results = baseline["results"]
    warmups = int(metadata["warmups"])
    iterations = int(metadata["iterations"])
    with override_settings(
        DEBUG=False,
        CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
    ):
        cache.clear()
        with temporary_performance_configuration(
            enabled=True,
            sql_enabled=True,
            payload_enabled=True,
        ):
            primary_results, trials = _run_epic5_selection_benchmark_trials(
                warmups=warmups,
                iterations=iterations,
            )
        cache.clear()
    assert len(trials) == 3
    assert {str(result["name"]) for result in primary_results} == set(EPIC5_DATABASE_SCENARIO_NAMES)
    assert all(
        result["status"] == "measured"
        for trial in trials
        for result in cast(list[dict[str, object]], trial["results"])
    )
    assert all(
        result["warmups"] == 5 and result["iterations"] == 120
        for trial in trials
        for result in cast(list[dict[str, object]], trial["results"])
    )
    assert all(
        isinstance(database_sql := result.get("database_sql"), Mapping)
        and database_sql.get("measurement_scope") == "whole_operation"
        for trial in trials
        for result in cast(list[dict[str, object]], trial["results"])
    )
    assert {
        str(result["name"]): str(result["execution_boundary"]) for result in primary_results
    } == dict(EPIC5_SELECTION_SCENARIO_BOUNDARIES)
    replacement_names = set(EPIC5_DATABASE_SCENARIO_NAMES)
    results = [
        result for result in original_results if result["name"] not in replacement_names
    ] + primary_results
    metadata["database_engine"] = connections["default"].vendor
    metadata["sql_debug"] = False
    metadata["epic5_selection_trial_count"] = len(trials)
    metadata["epic5_selection_trial_method"] = (
        "Three sequential independent isolated PostgreSQL fixtures; each serial scenario uses "
        "five excluded warmups and 120 measured invocations with DEBUG=False."
    )
    metadata["epic5_selection_trials"] = trials
    if expected_artifact_set == EPIC5_CANDIDATE_ARTIFACT_SET:
        metadata["epic5_selection_baseline_comparison"] = _build_epic5_candidate_comparison(
            primary_results
        )
    metadata["selection_sql_attribution"] = {
        "selection_prompt_context": (
            "Existing inclusive Registry stage events are shown only for prompt-context scenarios "
            "and are never summed with nested stages."
        ),
        "whole_operation": (
            "Django execute_wrapper records exact operation-level SQL count and wrapper duration "
            "for every scenario; it cannot attribute statements to authorization, namespace "
            "admission, visibility, version validation, or context assembly."
        ),
    }
    persist_baseline(metadata, results)


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(
    os.environ.get("RESOURCE_REGISTRY_BENCHMARK_DATABASE") != "1",
    reason="Run explicitly with RESOURCE_REGISTRY_BENCHMARK_DATABASE=1 in an isolated test DB.",
)
def test_record_epic5_selection_baseline() -> None:
    """Replace RR-05.1 placeholders with real isolated PostgreSQL selection trials."""
    _record_epic5_selection_artifacts(
        expected_artifact_set=EPIC5_ARTIFACT_SET,
        story_label="RR-05.1",
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(
    os.environ.get("RESOURCE_REGISTRY_BENCHMARK_DATABASE") != "1",
    reason="Run explicitly with RESOURCE_REGISTRY_BENCHMARK_DATABASE=1 in an isolated test DB.",
)
def test_record_epic5_selection_candidate() -> None:
    """Record RR-05.2 revalidation measurements in a separate candidate artifact set."""
    _record_epic5_selection_artifacts(
        expected_artifact_set=EPIC5_CANDIDATE_ARTIFACT_SET,
        story_label="RR-05.2",
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(
    os.environ.get("RESOURCE_REGISTRY_BENCHMARK_DATABASE") != "1",
    reason="Run explicitly with RESOURCE_REGISTRY_BENCHMARK_DATABASE=1 in an isolated test DB.",
)
def test_record_epic3_grouped_execution_baseline() -> None:
    """Replace RR-03.1 placeholders with three isolated canonical grouped-execution trials."""
    if get_benchmark_artifact_set() != "epic3_grouped_baseline":
        pytest.skip(
            "RR-03.1 artifacts are selected only by the dedicated artifact-set environment."
        )
    if not _BENCHMARK_OUTPUT.is_file():
        pytest.fail("Run the RR-03.1 benchmark runner before the isolated database benchmark.")
    baseline = json.loads(_BENCHMARK_OUTPUT.read_text())
    metadata = baseline["metadata"]
    original_results = baseline["results"]
    warmups = int(metadata["warmups"])
    iterations = int(metadata["iterations"])
    with override_settings(
        DEBUG=False,
        CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
    ):
        cache.clear()
        with temporary_performance_configuration(
            enabled=True,
            sql_enabled=True,
            payload_enabled=True,
        ):
            primary_results, trials = _run_grouped_execution_benchmark_trials(
                warmups=warmups,
                iterations=iterations,
            )
        cache.clear()
    assert len(trials) == 3
    assert all(
        len(cast(list[dict[str, object]], trial["results"]))
        == len(REQUIRED_GROUPED_EXECUTION_SCENARIO_NAMES)
        for trial in trials
    )
    assert all(
        result["status"] == "measured"
        for trial in trials
        for result in cast(list[dict[str, object]], trial["results"])
    )
    assert {result["name"] for result in primary_results} == set(
        REQUIRED_GROUPED_EXECUTION_SCENARIO_NAMES
    )
    unexpected_result = next(
        (result for result in primary_results if result["status"] != "measured"),
        None,
    )
    assert unexpected_result is None, (
        (
            str(unexpected_result["name"]),
            str(unexpected_result["outcomes"]),
        )
        if unexpected_result is not None
        else ""
    )
    assert {
        result["name"]: result["execution_boundary"] for result in primary_results
    } == GROUPED_EXECUTION_SCENARIO_BOUNDARIES
    canonical_results = {
        str(result["name"]): result
        for result in primary_results
        if result["name"] in CANONICAL_GROUPED_EXECUTION_SCENARIO_NAMES
    }
    assert set(canonical_results) == set(CANONICAL_GROUPED_EXECUTION_SCENARIO_NAMES)
    for result in canonical_results.values():
        assert result["execution_boundary"] == "canonical_dispatch_included"
        assert result["result_payload_bytes"] is not None
    list_result = canonical_results["grouped.execution.list_datasets"]
    list_stages = list_result["stages"]
    assert isinstance(list_stages, dict)
    assert "grouped_dispatch_bridge" in list_stages
    assert "grouped_call_total" in list_stages
    total_stage = list_stages["grouped_call_total"]
    assert isinstance(total_stage, dict)
    assert total_stage.get("mean_db_query_count") is not None
    replacement_names = set(REQUIRED_GROUPED_EXECUTION_SCENARIO_NAMES)
    results = [
        result for result in original_results if result["name"] not in replacement_names
    ] + primary_results
    metadata["database_engine"] = connections["default"].vendor
    metadata["grouped_execution_trial_count"] = len(trials)
    metadata["grouped_execution_trial_method"] = (
        "Three sequential trials with separate persisted fixtures; each canonical invocation uses "
        "the unmodified shared AIEngineClient dispatcher."
    )
    metadata["grouped_execution_trials"] = trials
    metadata["canonical_dispatch_evidence"] = (
        "Canonical scenarios are counted by a test-only AIEngineClient subclass that delegates "
        "to super().callGenericFunction(); shared dispatch admission, parameter checks, and "
        "Registry handlers remain active."
    )
    metadata["sql_debug"] = False
    persist_baseline(metadata, results)


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(
    os.environ.get("RESOURCE_REGISTRY_BENCHMARK_DATABASE") != "1",
    reason="Run explicitly with RESOURCE_REGISTRY_BENCHMARK_DATABASE=1 in an isolated test DB.",
)
def test_record_epic6_grouped_result_baseline() -> None:
    """Replace RR-09.1 canonical placeholders with guarded PostgreSQL measurements."""
    if get_benchmark_artifact_set() != EPIC6_GROUPED_RESULT_ARTIFACT_SET:
        pytest.skip("RR-09.1 artifacts are selected only by the dedicated artifact set.")
    if not _BENCHMARK_OUTPUT.is_file():
        pytest.fail("Run the RR-09.1 benchmark runner before the isolated database benchmark.")
    if connections["default"].vendor != "postgresql":
        pytest.fail("RR-09.1 canonical evidence requires guarded isolated PostgreSQL.")
    validate_isolated_database_target(
        database_name=os.environ.get("DB_DATABASE", ""),
        database_host=os.environ.get("DB_HOST", ""),
        read_database_host=os.environ.get("DB_READ_HOST", ""),
    )
    if "replica" in connections.databases:
        pytest.fail("RR-09.1 canonical evidence must not use a read-replica alias.")
    baseline = json.loads(_BENCHMARK_OUTPUT.read_text())
    metadata = baseline["metadata"]
    original_results = baseline["results"]
    warmups = int(metadata["warmups"])
    iterations = int(metadata["iterations"])
    with override_settings(
        DEBUG=False,
        CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
    ):
        cache.clear()
        with temporary_performance_configuration(
            enabled=True,
            sql_enabled=True,
            payload_enabled=True,
        ):
            primary_results, trials = _run_epic6_canonical_benchmark_trials(
                warmups=warmups,
                iterations=iterations,
            )
        cache.clear()
    assert len(trials) == 3
    assert all(
        {str(result["name"]) for result in cast(list[dict[str, object]], trial["results"])}
        == set(EPIC6_CANONICAL_SCENARIO_NAMES)
        for trial in trials
    )
    supported_results = [
        result
        for result in primary_results
        if str(result["name"]).startswith("grouped_result.canonical.dataset_list.")
    ]
    assert len(supported_results) == 3
    for result in supported_results:
        assert result["status"] == "measured"
        assert result["error_count"] == 0
        assert isinstance(result.get("database_sql"), Mapping)
        raw_scenario_metadata = result.get("scenario_metadata")
        assert isinstance(raw_scenario_metadata, Mapping)
        assert result.get("observed_result_metadata") == {
            "returned_record_count": raw_scenario_metadata["record_count"]
        }
    replacement_names = set(EPIC6_CANONICAL_SCENARIO_NAMES)
    results = [
        result for result in original_results if str(result["name"]) not in replacement_names
    ] + primary_results
    metadata["database_engine"] = connections["default"].vendor
    metadata["sql_debug"] = False
    metadata["epic6_canonical_execution_status"] = "measured_guarded_isolated_postgresql"
    metadata["epic6_canonical_trial_count"] = len(trials)
    metadata["epic6_canonical_trial_method"] = (
        "Three sequential isolated PostgreSQL fixtures with 100 active visible datasets. "
        "Each supported canonical list call uses five excluded warmups and 120 measured "
        "invocations with DEBUG=False. Each dataset-list scenario records inherited canonical "
        "dispatcher calls; unsupported canonical response types remain explicitly not_measured."
    )
    metadata["epic6_database_guard"] = {
        "validated": True,
        "benchmark_opt_in": True,
        "configured_database_name": os.environ.get("DB_DATABASE", ""),
        "configured_database_host": os.environ.get("DB_HOST", ""),
        "read_database_host": os.environ.get("DB_READ_HOST", ""),
        "django_database_alias": connections["default"].alias,
    }
    metadata["epic6_canonical_trials"] = trials
    metadata["epic6_sql_attribution"] = (
        "Whole-operation SQL uses a benchmark-local Django execute_wrapper. Inclusive Registry "
        "stage timings are reported separately and are never subtracted or summed."
    )
    persist_baseline(metadata, results)


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(
    os.environ.get("RESOURCE_REGISTRY_BENCHMARK_DATABASE") != "1",
    reason="Run explicitly with RESOURCE_REGISTRY_BENCHMARK_DATABASE=1 in an isolated test DB.",
)
def test_record_epic6_grouped_result_memory() -> None:
    """Replace RR-09.2 canonical placeholders with guarded PostgreSQL memory evidence."""
    if get_benchmark_artifact_set() != EPIC6_GROUPED_RESULT_MEMORY_ARTIFACT_SET:
        pytest.skip("RR-09.2 artifacts are selected only by the dedicated artifact set.")
    if not _BENCHMARK_OUTPUT.is_file():
        pytest.fail("Run the RR-09.2 benchmark runner before the isolated database benchmark.")
    if connections["default"].vendor != "postgresql":
        pytest.fail("RR-09.2 canonical evidence requires guarded isolated PostgreSQL.")
    validate_isolated_database_target(
        database_name=os.environ.get("DB_DATABASE", ""),
        database_host=os.environ.get("DB_HOST", ""),
        read_database_host=os.environ.get("DB_READ_HOST", ""),
    )
    if "replica" in connections.databases:
        pytest.fail("RR-09.2 canonical evidence must not use a read-replica alias.")
    baseline = json.loads(_BENCHMARK_OUTPUT.read_text())
    metadata = baseline["metadata"]
    original_results = baseline["results"]
    with override_settings(
        DEBUG=False,
        CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
    ):
        cache.clear()
        with temporary_performance_configuration(
            enabled=False,
            sql_enabled=False,
            payload_enabled=False,
        ):
            primary_results, trials = _run_epic6_canonical_memory_trials()
        cache.clear()
    assert len(trials) == EPIC6_MEMORY_TRIAL_COUNT
    assert all(
        {str(result["name"]) for result in cast(list[dict[str, object]], trial["results"])}
        == set(EPIC6_MEMORY_CANONICAL_SCENARIO_NAMES)
        for trial in trials
    )
    assert {str(result["name"]) for result in primary_results} == set(
        EPIC6_MEMORY_CANONICAL_SCENARIO_NAMES
    )
    for result in primary_results:
        assert result["status"] == "measured"
        assert result["error_count"] == 0
        assert result["input_payload_bytes"] is None
        assert isinstance(result["output_payload_bytes"], int)
        assert result["output_payload_bytes"] > 0
        assert len(cast(list[dict[str, object]], result["trials"])) == EPIC6_MEMORY_TRIAL_COUNT
        assert all(
            trial["output_parity"] == "exact" and trial["canonical_dispatch_call_count"] == 1
            for trial in cast(list[dict[str, object]], result["trials"])
        )
    replacement_names = set(EPIC6_MEMORY_CANONICAL_SCENARIO_NAMES)
    results = [
        result for result in original_results if str(result["name"]) not in replacement_names
    ] + primary_results
    metadata["database_engine"] = connections["default"].vendor
    metadata["sql_debug"] = False
    metadata["epic6_memory_canonical_execution_status"] = "measured_guarded_isolated_postgresql"
    metadata["epic6_memory_canonical_trial_count"] = len(trials)
    metadata["epic6_memory_canonical_trial_method"] = (
        "Three independent isolated PostgreSQL fixtures with 100 active visible datasets. Each "
        "dataset-list call is traced once per fixture with DEBUG=False, performance instrumentation "
        "disabled, and no SQL or latency capture. The boundary includes canonical dispatch, "
        "authorization, database access, and result projection."
    )
    metadata["epic6_memory_database_guard"] = {
        "validated": True,
        "benchmark_opt_in": True,
        "configured_database_name": os.environ.get("DB_DATABASE", ""),
        "configured_database_host": os.environ.get("DB_HOST", ""),
        "read_database_host": os.environ.get("DB_READ_HOST", ""),
        "django_database_alias": connections["default"].alias,
    }
    metadata["epic6_memory_canonical_trials"] = trials
    metadata["epic6_memory_canonical_attribution"] = (
        "Traced current and peak values cover the complete guarded canonical dispatch boundary. "
        "They are not attributed to Registry projection or any individual request component."
    )
    persist_baseline(metadata, results)


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(
    os.environ.get("RESOURCE_REGISTRY_BENCHMARK_DATABASE") != "1",
    reason="Run explicitly with RESOURCE_REGISTRY_BENCHMARK_DATABASE=1 in an isolated test DB.",
)
def test_record_epic4_catalog_baseline() -> None:
    """Replace RR-04.1 placeholders with real isolated catalog and freshness measurements."""
    if get_benchmark_artifact_set() != EPIC4_ARTIFACT_SET:
        pytest.skip(
            "RR-04.1 artifacts are selected only by the dedicated artifact-set environment."
        )
    if not _BENCHMARK_OUTPUT.is_file():
        pytest.fail("Run the RR-04.1 benchmark runner before the database benchmark.")
    baseline = json.loads(_BENCHMARK_OUTPUT.read_text())
    metadata = baseline["metadata"]
    original_results = baseline["results"]
    warmups = int(metadata["warmups"])
    iterations = int(metadata["iterations"])
    with override_settings(
        DEBUG=False,
        CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
    ):
        cache.clear()
        with temporary_performance_configuration(
            enabled=True,
            sql_enabled=True,
            payload_enabled=True,
        ):
            primary_results, trials, memory_result = _run_epic4_catalog_benchmark_trials(
                warmups=warmups,
                iterations=iterations,
            )
        cache.clear()
    assert len(trials) == 3
    assert {str(result["name"]) for result in primary_results} == set(EPIC4_DATABASE_SCENARIO_NAMES)
    assert all(
        result["status"] == "measured"
        for trial in trials
        for result in cast(list[dict[str, object]], trial["results"])
    )
    assert memory_result["status"] == "measured"
    replacement_names = set(EPIC4_DATABASE_SCENARIO_NAMES) | {"catalog.memory.catalog_construction"}
    results = (
        [result for result in original_results if result["name"] not in replacement_names]
        + primary_results
        + [memory_result]
    )
    metadata["database_engine"] = connections["default"].vendor
    metadata["sql_debug"] = False
    metadata["epic4_catalog_trial_count"] = len(trials)
    metadata["epic4_catalog_trial_method"] = (
        "Three sequential isolated PostgreSQL trials preserve the historical single-Persona "
        "series and add separate multi-Persona, multi-Organization publication at 1, 10, and "
        "25 workers. Assignment and FunctionParameter changes occur before, never inside, "
        "measured publication calls."
    )
    metadata["epic4_catalog_trials"] = trials
    metadata["canonical_admission_evidence"] = (
        "Assignment removal, capability revocation, Workspace disablement, and Dataset visibility "
        "revocation are measured through a test-only AIEngineClient subclass that delegates to the "
        "unchanged shared canonical dispatcher. Each denied path records its expected status and "
        "no returned domain data; unexpected errors remain separate error_count values."
    )
    persist_baseline(metadata, results)


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(
    os.environ.get("RESOURCE_REGISTRY_BENCHMARK_DATABASE") != "1",
    reason="Run explicitly with RESOURCE_REGISTRY_BENCHMARK_DATABASE=1 in an isolated test DB.",
)
def test_epic3_uses_the_real_canonical_dispatcher_without_internal_retries() -> None:
    """Prove canonical delegation and one-attempt retry ownership for a grouped call."""
    if get_benchmark_artifact_set() != "epic3_grouped_baseline":
        pytest.skip("RR-03.1 verification is selected only for the dedicated artifact set.")
    with override_settings(
        CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    ):
        fixture = _create_database_fixture(fixture_index=150)
        canonical_client = _CanonicalGroupedDispatchClient(fixture.persona)
        canonical_result = _run_grouped_call(
            canonical_client,
            fixture.request,
            group_name="resource_registry_inspect",
            action="list_datasets",
            arguments={"tenant_id": str(fixture.namespace_id), "limit": 1},
        )
        assert _classify_grouped_success(canonical_result) == "success"
        assert canonical_client.canonical_dispatch_call_count == 1
        pre_dispatch_client = _PreDispatchGroupedClient(
            fixture.persona,
            response=json.dumps({"success": False, "status": 500, "code": "benchmark_failure"}),
        )
        terminal_failure = _run_grouped_call(
            pre_dispatch_client,
            fixture.request,
            group_name="resource_registry_inspect",
            action="list_datasets",
            arguments={"tenant_id": str(fixture.namespace_id), "limit": 1},
        )
        assert _decode_grouped_result(terminal_failure) == {
            "success": False,
            "status": 500,
            "code": "benchmark_failure",
        }
        assert pre_dispatch_client.call_count == 1
