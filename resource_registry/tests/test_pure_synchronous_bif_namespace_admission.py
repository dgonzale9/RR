"""Runtime Adoption Phase 3A pure synchronous BIF boundary tests."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from BotServer.builtin_functions import resource_registry_functions
from resource_registry.read_auth.exceptions import DataRegistryAuthorizationError
from resource_registry.bifs.read_only_namespace_admission import AdmittedBifNamespace

_ROOT = Path(__file__).resolve().parents[2]
_DATASET_BIF_MODULE = _ROOT / "BotServer/builtin_functions/resource_registry_functions.py"
_TRANSFORMATION_HANDLER_MODULE = _ROOT / "resource_registry/bifs/transformation/handlers.py"
_TRANSFORMATION_CONTEXT_MODULE = _ROOT / "resource_registry/bifs/transformation/context.py"

_PHASE_3A_DATASET_BIFS = frozenset(
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
_PHASE_3A_TRANSFORMATION_BIFS = frozenset(
    {
        "resource_registry_get_transformation_run",
        "resource_registry_list_transformation_runs",
        "resource_registry_list_transformation_steps",
        "resource_registry_list_transformation_outputs",
        "resource_registry_get_transformation_diagnostics",
    }
)
_VALID_RESOURCE_ID = "11111111-1111-1111-1111-111111111111"
_HTTP_FORBIDDEN = 403
_PHASE_3A_DATASET_INVOCATIONS: tuple[tuple[str, dict[str, object]], ...] = (
    ("resource_registry_list_external_jobs", {}),
    (
        "resource_registry_get_intelligence_job_status",
        {"intelligence_job_id": _VALID_RESOURCE_ID},
    ),
    (
        "resource_registry_get_intelligence_job_result",
        {"intelligence_job_id": _VALID_RESOURCE_ID},
    ),
    ("resource_registry_get_query_job_status", {"query_job_id": _VALID_RESOURCE_ID}),
    ("resource_registry_get_query_job_result", {"query_job_id": _VALID_RESOURCE_ID}),
    (
        "resource_registry_plan_query",
        {"dataset_id": _VALID_RESOURCE_ID, "user_request": "Count rows"},
    ),
    ("resource_registry_get_query_run", {"query_run_id": _VALID_RESOURCE_ID}),
    (
        "resource_registry_get_analysis_artifact",
        {"analysis_artifact_id": _VALID_RESOURCE_ID},
    ),
    (
        "resource_registry_get_chart_artifact",
        {"chart_artifact_id": _VALID_RESOURCE_ID},
    ),
    ("resource_registry_get_export_plan", {"export_plan_id": _VALID_RESOURCE_ID}),
)


class _DenyingNamespaceAdmissionService:
    """Deny admission while recording that the runtime boundary was reached."""

    def __init__(self) -> None:
        self.calls: list[tuple[object, object]] = []

    def admit_namespace(
        self,
        *,
        request: object,
        requested_namespace_id: object,
    ) -> str:
        """Reject the selector before any downstream resource lookup."""
        self.calls.append((request, requested_namespace_id))
        raise DataRegistryAuthorizationError("The selected tenant_id is not authorized.")

    def admit_namespace_with_provenance(
        self,
        *,
        request: object,
        requested_namespace_id: object,
    ) -> AdmittedBifNamespace:
        """Reject namespace admission before a BIF can perform a downstream lookup."""
        self.admit_namespace(request=request, requested_namespace_id=requested_namespace_id)
        raise AssertionError("Namespace denial must interrupt BIF execution.")


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


def _method_by_name(path: Path, class_name: str, method_name: str) -> ast.FunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    matching_class = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    return next(
        node
        for node in matching_class.body
        if isinstance(node, ast.FunctionDef) and node.name == method_name
    )


def test_every_phase_3a_dataset_bif_uses_canonical_namespace_admission() -> None:
    functions = _module_functions(_DATASET_BIF_MODULE)

    assert all(
        _calls_name(functions[function_name], "_resolve_read_only_tenant_id")
        for function_name in _PHASE_3A_DATASET_BIFS
    )


@pytest.mark.parametrize(("function_name", "function_arguments"), _PHASE_3A_DATASET_INVOCATIONS)
def test_every_phase_3a_dataset_bif_denies_before_downstream_lookup(
    monkeypatch: pytest.MonkeyPatch,
    function_name: str,
    function_arguments: dict[str, object],
) -> None:
    request = SimpleNamespace(user=SimpleNamespace(username="phase-3a-reviewer"), session={})
    admission_service = _DenyingNamespaceAdmissionService()
    monkeypatch.setattr(
        resource_registry_functions,
        "_get_read_only_namespace_admission_service",
        lambda: admission_service,
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


def test_every_phase_3a_transformation_bif_uses_the_canonical_read_context_path() -> None:
    functions = _module_functions(_TRANSFORMATION_HANDLER_MODULE)

    assert all(
        _calls_name(functions[function_name], "_invoke_read")
        for function_name in _PHASE_3A_TRANSFORMATION_BIFS
    )
    build_read_context = _method_by_name(
        _TRANSFORMATION_CONTEXT_MODULE,
        "TransformationBifTrustedContextBuilder",
        "build_read_context",
    )
    assert _calls_name(build_read_context, "_admit_read_namespace")


def test_transformation_submission_remains_outside_phase_3a() -> None:
    transformation_functions = _module_functions(_TRANSFORMATION_HANDLER_MODULE)
    assert not _calls_name(transformation_functions["resource_registry_start_transformation_run"], "_invoke_read")


def test_transformation_submission_remains_outside_phase_3a_read_boundary() -> None:
    build_execution_context = _method_by_name(
        _TRANSFORMATION_CONTEXT_MODULE,
        "TransformationBifTrustedContextBuilder",
        "build_execution_context",
    )

    assert _calls_name(build_execution_context, "_admit_submission_namespace")
    assert not _calls_name(build_execution_context, "_admit_read_namespace")
