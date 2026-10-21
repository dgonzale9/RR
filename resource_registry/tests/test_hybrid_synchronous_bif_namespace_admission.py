"""Runtime Adoption Phase 3B hybrid synchronous BIF boundary tests."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from BotServer.builtin_functions import resource_registry_functions
from resource_registry.bifs.manifest import RESOURCE_REGISTRY_BIF_MANIFEST
from resource_registry.bifs.read_only_namespace_admission import AdmittedBifNamespace
from resource_registry.read_auth.exceptions import DataRegistryAuthorizationError

_ROOT = Path(__file__).resolve().parents[2]
_DATASET_BIF_MODULE = _ROOT / "BotServer/builtin_functions/resource_registry_functions.py"
_TRANSFORMATION_CONTEXT_MODULE = _ROOT / "resource_registry/bifs/transformation/context.py"
_BIF_CATALOG = _ROOT / "resource_registry/docs/07-built-in-functions.md"
_EXPORT_PLAN_SERVICE = _ROOT / "resource_registry/services/dataset_export_plan_service.py"
_VALID_RESOURCE_ID = "11111111-1111-1111-1111-111111111111"
_CANONICAL_NAMESPACE_ID = "22222222-2222-2222-2222-222222222222"
_HTTP_FORBIDDEN = 403

_PHASE_2_BIFS = frozenset(
    {
        "resource_registry_list_datasets",
        "resource_registry_get_dataset",
        "resource_registry_get_dataset_context",
        "resource_registry_get_dataset_schema",
        "resource_registry_get_dataset_profile",
        "resource_registry_list_dataset_versions",
        "resource_registry_explore_dataset",
    }
)
_PHASE_3A_BIFS = frozenset(
    {
        "resource_registry_list_external_jobs",
        "resource_registry_get_intelligence_job_status",
        "resource_registry_get_intelligence_job_result",
        "resource_registry_get_query_job_status",
        "resource_registry_get_query_job_result",
        "resource_registry_plan_query",
        "resource_registry_get_query_run",
        "resource_registry_get_analysis_artifact",
        "resource_registry_get_chart_artifact",
        "resource_registry_get_export_plan",
    }
)
_PHASE_3B_BIFS = frozenset(
    {
        "resource_registry_create_filtered_dataset",
        "resource_registry_query_dataset",
        "resource_registry_analyze_dataset",
        "resource_registry_create_chart",
        "resource_registry_plan_export",
        "resource_registry_generate_artifact",
    }
)
_ASYNC_SUBMISSION_BIFS = frozenset(
    {
        "resource_registry_start_query_job",
        "resource_registry_start_intelligence_job",
    }
)
_PHASE_3B_INVOCATIONS: tuple[tuple[str, dict[str, object]], ...] = (
    (
        "resource_registry_create_filtered_dataset",
        {
            "dataset_id": _VALID_RESOURCE_ID,
            "output_name": "Authorized derivative",
            "filter_column": "spend",
            "operator": ">",
            "threshold": 10,
        },
    ),
    (
        "resource_registry_query_dataset",
        {"dataset_id": _VALID_RESOURCE_ID, "sql": "SELECT * FROM dataset LIMIT 1"},
    ),
    (
        "resource_registry_analyze_dataset",
        {"dataset_id": _VALID_RESOURCE_ID, "analysis_type": "summary"},
    ),
    (
        "resource_registry_create_chart",
        {
            "dataset_id": _VALID_RESOURCE_ID,
            "user_request": "Chart spend",
            "chart_type": "bar",
        },
    ),
    (
        "resource_registry_plan_export",
        {
            "dataset_id": _VALID_RESOURCE_ID,
            "format": "xlsx",
            "user_request": "Export spend",
        },
    ),
    ("resource_registry_generate_artifact", {"export_plan_id": _VALID_RESOURCE_ID}),
)
_SIDE_EFFECT_DEPENDENCY_GETTERS = (
    "_get_read_facade",
    "_get_derived_dataset_query_executor",
    "_get_dataset_registration_facade",
    "_get_query_executor",
    "_get_query_routing_policy",
    "_get_persistence_service",
    "_get_analysis_service",
    "_get_chart_service",
    "_get_export_plan_service",
    "_get_artifact_generation_service",
    "_derived_dataset_file_path",
    "_write_filtered_dataset_csv",
)


class _DenyingNamespaceAdmissionService:
    """Deny namespace admission and record invocation count."""

    def __init__(self) -> None:
        self.calls: list[tuple[object, object]] = []

    def admit_namespace(
        self,
        *,
        request: object,
        requested_namespace_id: object,
    ) -> str:
        """Reject before any domain lookup, execution, or persistence."""
        self.calls.append((request, requested_namespace_id))
        raise DataRegistryAuthorizationError("The selected tenant_id is not authorized.")

    def admit_namespace_with_provenance(
        self,
        *,
        request: object,
        requested_namespace_id: object,
    ) -> AdmittedBifNamespace:
        """Reject namespace admission before any side effect can occur."""
        self.admit_namespace(request=request, requested_namespace_id=requested_namespace_id)
        raise AssertionError("Namespace denial must interrupt BIF execution.")


class _AllowingNamespaceAdmissionService:
    """Return a canonical namespace distinct from the caller's selector."""

    def __init__(self) -> None:
        self.calls: list[tuple[object, object]] = []

    def admit_namespace(
        self,
        *,
        request: object,
        requested_namespace_id: object,
    ) -> str:
        """Record one admission and return the canonical authorized namespace."""
        self.calls.append((request, requested_namespace_id))
        return _CANONICAL_NAMESPACE_ID

    def admit_namespace_with_provenance(
        self,
        *,
        request: object,
        requested_namespace_id: object,
    ) -> AdmittedBifNamespace:
        """Return canonical namespace identity and immutable BIF provenance."""
        return AdmittedBifNamespace(
            namespace_id=self.admit_namespace(
                request=request,
                requested_namespace_id=requested_namespace_id,
            ),
            organization_id=1,
            workspace_id=None,
            actor_id=1,
        )


def _module_functions(path: Path) -> dict[str, ast.FunctionDef]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}


def _calls_name(function: ast.FunctionDef, name: str) -> bool:
    return any(
        (
            (isinstance(call.func, ast.Name) and call.func.id == name)
            or (isinstance(call.func, ast.Attribute) and call.func.attr == name)
        )
        for call in ast.walk(function)
        if isinstance(call, ast.Call)
    )


def _call_count(function: ast.FunctionDef, name: str) -> int:
    return sum(
        1
        for call in ast.walk(function)
        if isinstance(call, ast.Call)
        and (
            (isinstance(call.func, ast.Name) and call.func.id == name)
            or (isinstance(call.func, ast.Attribute) and call.func.attr == name)
        )
    )


def _assigns_canonical_tenant_from_admission(function: ast.FunctionDef) -> bool:
    return any(
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "tenant_id_str"
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "_resolve_read_only_tenant_id"
        for node in ast.walk(function)
    )


def test_exact_phase_3b_allowlist_uses_canonical_bif_admission() -> None:
    functions = _module_functions(_DATASET_BIF_MODULE)

    assert _PHASE_2_BIFS.isdisjoint(_PHASE_3B_BIFS)
    assert _PHASE_3A_BIFS.isdisjoint(_PHASE_3B_BIFS)
    for function_name in _PHASE_3B_BIFS:
        function = functions[function_name]
        assert _call_count(function, "_resolve_read_only_tenant_id") == 1
        assert not _calls_name(function, "_resolve_tenant_id")
        assert _assigns_canonical_tenant_from_admission(function)


def test_async_submission_bifs_use_only_the_phase_4_submission_boundary() -> None:
    functions = _module_functions(_DATASET_BIF_MODULE)
    transformation_context = _TRANSFORMATION_CONTEXT_MODULE.read_text(encoding="utf-8")

    assert all(
        not _calls_name(functions[function_name], "_resolve_read_only_tenant_id")
        for function_name in _ASYNC_SUBMISSION_BIFS
    )
    assert all(
        _calls_name(functions[function_name], "_admit_async_submission_namespace")
        for function_name in _ASYNC_SUBMISSION_BIFS
    )
    assert "build_execution_context" in transformation_context
    assert "namespace_admission = self._admit_submission_namespace(" in transformation_context


@pytest.mark.parametrize(("function_name", "function_arguments"), _PHASE_3B_INVOCATIONS)
def test_admission_denial_prevents_every_phase_3b_side_effect(
    monkeypatch: pytest.MonkeyPatch,
    function_name: str,
    function_arguments: dict[str, object],
) -> None:
    request = SimpleNamespace(user=SimpleNamespace(username="phase-3b-reviewer"), session={})
    admission_service = _DenyingNamespaceAdmissionService()

    def fail_on_side_effect_dependency(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("A Phase 3B side-effect dependency was reached before admission.")

    monkeypatch.setattr(
        resource_registry_functions,
        "_get_read_only_namespace_admission_service",
        lambda: admission_service,
    )
    for dependency_name in _SIDE_EFFECT_DEPENDENCY_GETTERS:
        monkeypatch.setattr(
            resource_registry_functions,
            dependency_name,
            fail_on_side_effect_dependency,
        )

    function = getattr(resource_registry_functions, function_name)
    response = json.loads(
        function(
            request,
            None,
            tenant_id=_VALID_RESOURCE_ID,
            **function_arguments,
        )
    )

    assert response["success"] is False
    assert response["status"] == _HTTP_FORBIDDEN
    assert response["code"] == "authorization_error"
    assert admission_service.calls == [(request, _VALID_RESOURCE_ID)]


@pytest.mark.parametrize(("function_name", "function_arguments"), _PHASE_3B_INVOCATIONS)
def test_canonical_namespace_is_forwarded_once_by_every_phase_3b_bif(  # noqa: PLR0915
    monkeypatch: pytest.MonkeyPatch,
    function_name: str,
    function_arguments: dict[str, object],
) -> None:
    request = SimpleNamespace(user=SimpleNamespace(username="phase-3b-reviewer"), session={})
    admission_service = _AllowingNamespaceAdmissionService()
    capability_tenants: list[str] = []
    authorization_contexts: list[SimpleNamespace] = []

    def record_capability(
        request_value: object,
        tenant_id: str,
        called_function_name: str,
        dataset_id: str | None = None,
    ) -> None:
        _ = request_value, called_function_name, dataset_id
        capability_tenants.append(tenant_id)

    def build_authorization_context(
        request_value: object,
        tenant_id: str,
    ) -> SimpleNamespace:
        _ = request_value
        context = SimpleNamespace(tenant_id=tenant_id, actor="phase-3b-reviewer")
        authorization_contexts.append(context)
        return context

    facade = Mock()
    facade.get_dataset.return_value = {"id": _VALID_RESOURCE_ID}
    facade.get_schema.return_value = {"schema_json": [{"name": "spend", "type": "DOUBLE"}]}
    query_executor = Mock()
    query_executor.execute_query.return_value = {
        "success": True,
        "status": 200,
        "columns": ["spend"],
        "rows": [[12]],
        "row_count": 1,
        "truncated": False,
    }
    persistence_service = Mock()
    persistence_service.persist_export_plan.return_value = SimpleNamespace(
        id=_VALID_RESOURCE_ID,
    )
    analysis_service = Mock()
    analysis_service.analyze.return_value = {
        "success": True,
        "status": 200,
        "analysis_type": "summary",
    }
    chart_service = Mock()
    chart_service.create_chart.return_value = {
        "success": True,
        "status": 200,
        "chart": {"chart_type": "bar", "title": "Spend"},
    }
    export_plan_service = Mock()
    export_plan_service.create_plan.return_value = {
        "success": True,
        "status": 200,
        "artifact_type": "xlsx",
        "export_plan": {"filename": "spend.xlsx"},
    }
    artifact_generation_service = Mock()
    artifact_generation_service.generate_from_export_plan.return_value = {
        "success": True,
        "status": 200,
        "artifact": {"artifact_id": _VALID_RESOURCE_ID},
    }
    registration_facade = Mock()
    registration_facade.register.return_value = SimpleNamespace(
        dataset_id=_VALID_RESOURCE_ID,
        dataset_version_id=_VALID_RESOURCE_ID,
        artifact_id=_VALID_RESOURCE_ID,
    )
    routing_policy = Mock()
    routing_policy.evaluate.return_value = None
    filtered_csv_writer = Mock()

    monkeypatch.setattr(
        resource_registry_functions,
        "_get_read_only_namespace_admission_service",
        lambda: admission_service,
    )
    monkeypatch.setattr(resource_registry_functions, "_assert_capability", record_capability)
    monkeypatch.setattr(
        resource_registry_functions,
        "_build_authorization_context",
        build_authorization_context,
    )
    monkeypatch.setattr(resource_registry_functions, "_get_read_facade", lambda: facade)
    monkeypatch.setattr(resource_registry_functions, "_get_query_executor", lambda: query_executor)
    monkeypatch.setattr(
        resource_registry_functions,
        "_get_derived_dataset_query_executor",
        lambda: query_executor,
    )
    monkeypatch.setattr(
        resource_registry_functions,
        "_get_query_routing_policy",
        lambda: routing_policy,
    )
    monkeypatch.setattr(
        resource_registry_functions,
        "_validate_sql_for_query_job",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        resource_registry_functions,
        "_get_persistence_service",
        lambda: persistence_service,
    )
    monkeypatch.setattr(
        resource_registry_functions,
        "_get_analysis_service",
        lambda: analysis_service,
    )
    monkeypatch.setattr(resource_registry_functions, "_get_chart_service", lambda: chart_service)
    monkeypatch.setattr(
        resource_registry_functions,
        "_get_export_plan_service",
        lambda: export_plan_service,
    )
    monkeypatch.setattr(
        resource_registry_functions,
        "_get_artifact_generation_service",
        lambda: artifact_generation_service,
    )
    monkeypatch.setattr(
        resource_registry_functions,
        "_get_dataset_registration_facade",
        lambda: registration_facade,
    )
    monkeypatch.setattr(
        resource_registry_functions,
        "_derived_dataset_file_path",
        lambda **_kwargs: Path("/tmp/phase-3b-filtered.csv"),
    )
    monkeypatch.setattr(resource_registry_functions, "_write_filtered_dataset_csv", filtered_csv_writer)

    function = getattr(resource_registry_functions, function_name)
    response = json.loads(
        function(
            request,
            None,
            tenant_id=_VALID_RESOURCE_ID,
            **function_arguments,
        )
    )

    assert response["success"] is True
    assert admission_service.calls == [(request, _VALID_RESOURCE_ID)]
    assert capability_tenants == [_CANONICAL_NAMESPACE_ID]
    assert authorization_contexts
    assert {context.tenant_id for context in authorization_contexts} == {
        _CANONICAL_NAMESPACE_ID,
    }

    if function_name == "resource_registry_create_filtered_dataset":
        registration_command = registration_facade.register.call_args.args[0]
        assert registration_command.tenant_id == _CANONICAL_NAMESPACE_ID
        assert query_executor.execute_query.call_args.kwargs["context"].tenant_id == (
            _CANONICAL_NAMESPACE_ID
        )
        filtered_csv_writer.assert_called_once()
    elif function_name == "resource_registry_query_dataset":
        assert query_executor.execute_query.call_args.kwargs["context"].tenant_id == (
            _CANONICAL_NAMESPACE_ID
        )
        assert persistence_service.persist_query_run.call_args.kwargs["tenant_id"] == (
            _CANONICAL_NAMESPACE_ID
        )
    elif function_name == "resource_registry_analyze_dataset":
        assert analysis_service.analyze.call_args.kwargs["context"].tenant_id == (
            _CANONICAL_NAMESPACE_ID
        )
        assert persistence_service.persist_analysis_artifact.call_args.kwargs["tenant_id"] == (
            _CANONICAL_NAMESPACE_ID
        )
    elif function_name == "resource_registry_create_chart":
        assert chart_service.create_chart.call_args.kwargs["context"].tenant_id == (
            _CANONICAL_NAMESPACE_ID
        )
        assert persistence_service.persist_chart_artifact.call_args.kwargs["tenant_id"] == (
            _CANONICAL_NAMESPACE_ID
        )
    elif function_name == "resource_registry_plan_export":
        assert persistence_service.persist_export_plan.call_args.kwargs["tenant_id"] == (
            _CANONICAL_NAMESPACE_ID
        )
    else:
        artifact_context = artifact_generation_service.generate_from_export_plan.call_args.kwargs[
            "context"
        ]
        assert artifact_context.tenant_id == _CANONICAL_NAMESPACE_ID


def test_phase_3b_manifest_entries_are_not_read_only() -> None:
    assert {
        function_name
        for function_name in _PHASE_3B_BIFS
        if RESOURCE_REGISTRY_BIF_MANIFEST[function_name].is_read_only
    } == set()


def test_phase_3b_documentation_does_not_restore_legacy_tenant_authority() -> None:
    catalog = _BIF_CATALOG.read_text(encoding="utf-8")
    admission_section = catalog.split("### Registry Namespace Admission", maxsplit=1)[1].split(
        "## Built-in Function Catalog",
        maxsplit=1,
    )[0]
    assert "BIF arguments and REST\nscope headers cannot switch it" in admission_section
    assert "Conflicting metadata selectors are rejected" in admission_section
    assert "BIF namespace argument cannot broaden access" in admission_section
    assert '`session["tenant_id"]` are not selectors in this path' in admission_section
    assert "read-only export planning service" not in _EXPORT_PLAN_SERVICE.read_text(
        encoding="utf-8",
    )
