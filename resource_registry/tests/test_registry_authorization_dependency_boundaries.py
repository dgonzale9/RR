"""Dependency boundaries for Registry authorization infrastructure."""

from __future__ import annotations

import ast
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_AUTHORIZATION_PATHS = (
    "resource_registry/authorization/__init__.py",
    "resource_registry/authorization/contracts.py",
    "resource_registry/authorization/exceptions.py",
    "resource_registry/authorization/registry_authorization_context_service.py",
)
_INFRASTRUCTURE_PATHS = frozenset(
    {
        *_AUTHORIZATION_PATHS,
        "resource_registry/services/registry_namespace_resolution_service.py",
    },
)
_ALLOWED_AUTHORIZATION_SYMBOLS_BY_PATH = {
    "resource_registry/namespace_selection.py": frozenset({"RegistryAuthorizationContext"}),
    "resource_registry/scope_adapters.py": frozenset(
        {"RegistryAuthorizationContextService", "RegistryAuthorizationContext"}
    ),
    "resource_registry/api/transformation/permissions.py": frozenset(
        {"RegistryAuthorizationContextService"}
    ),
    "resource_registry/assistant/namespace_context.py": frozenset(
        {"RegistryAuthorizationContextService"}
    ),
    "resource_registry/bifs/read_only_namespace_admission.py": frozenset(
        {"RegistryAuthorizationContextService"}
    ),
    "resource_registry/execution_contracts/admission.py": frozenset(
        {"RegistryAuthorizationContext"}
    ),
    "resource_registry/execution_contracts/engine_worker.py": frozenset(
        {"RegistryAuthorizationContext"}
    ),
    "resource_registry/execution_contracts/lifecycle.py": frozenset(
        {"RegistryAuthorizationContext"}
    ),
    "resource_registry/execution_contracts/output_registration.py": frozenset(
        {"RegistryAuthorizationContext"}
    ),
    "resource_registry/execution_contracts/shadow.py": frozenset({"RegistryAuthorizationContext"}),
    "resource_registry/uploads/services.py": frozenset({"RegistryAuthorizationContextService"}),
    "Embeddings/file_proxy_api.py": frozenset({"RegistryAuthorizationContextService"}),
    "BotServer/creative_assembly_images/access.py": frozenset(
        {"RegistryAuthorizationContextService"}
    ),
    "resource_registry/assistant/persona_workspace_scope.py": frozenset(
        {"RegistryAuthorizationContext"}
    ),
    "resource_registry/assistant/runtime_adapter.py": frozenset(
        {"RegistryAuthorizationContextService"}
    ),
}
_FORBIDDEN_AUTHORIZATION_TOKENS = (
    "Persona",
    "Dataset",
    "request.session",
    "request.headers",
    "resource_registry_tenant_id",
    "django.contrib.auth.models import Group",
)


def test_runtime_modules_do_not_adopt_registry_authorization_infrastructure() -> None:
    """Limit authorization infrastructure consumers to approved runtime boundaries."""
    protected_symbols = (
        "RegistryAuthorizationContextService",
        "RegistryAuthorizationContext",
        "AuthorizedWorkspaceSet",
        "AuthorizedNamespaceSet",
        "AuthorizedNamespace",
        "RegistryNamespaceMapping",
        "resolve_namespaces_for_workspaces",
    )
    unexpected_references: list[str] = []
    for path in _ROOT.rglob("*.py"):
        relative_path = path.relative_to(_ROOT).as_posix()
        relative_parts = path.relative_to(_ROOT).parts
        if any(part.startswith(".") for part in relative_parts):
            continue
        if "tests" in relative_parts or "migrations" in relative_parts:
            continue
        if "__pycache__" in relative_parts:
            continue
        if relative_path in _INFRASTRUCTURE_PATHS:
            continue
        source = path.read_text(encoding="utf-8")
        allowed_symbols = _ALLOWED_AUTHORIZATION_SYMBOLS_BY_PATH.get(
            relative_path,
            frozenset(),
        )
        for symbol in protected_symbols:
            if re.search(rf"\b{re.escape(symbol)}\b", source) and symbol not in allowed_symbols:
                unexpected_references.append(f"{relative_path}:{symbol}")
    assert unexpected_references == []


def test_registry_authorization_has_no_runtime_or_role_dependencies() -> None:
    """Exclude Persona, Dataset, transport, session, and Group dependencies."""
    for relative_path in _AUTHORIZATION_PATHS:
        source = (_ROOT / relative_path).read_text(encoding="utf-8")
        for token in _FORBIDDEN_AUTHORIZATION_TOKENS:
            assert token not in source, f"{relative_path} contains prohibited dependency {token}"


def test_workspace_api_preserves_dev_workspace_authorization() -> None:
    """Require the Workspace API to import and call the public W/O authorization helpers."""
    source = (_ROOT / "Organization/workspace_api.py").read_text(encoding="utf-8")
    syntax_tree = ast.parse(source)
    required_helpers = {
        "get_workspaces_for_user",
        "user_can_access_workspace",
        "can_user_manage_workspace",
        "user_can_manage_organization",
    }
    imported_helpers = {
        alias.name
        for node in ast.walk(syntax_tree)
        if isinstance(node, ast.ImportFrom) and node.module == "Organization.organization_acl"
        for alias in node.names
    }
    called_helpers = {
        node.func.id
        for node in ast.walk(syntax_tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert required_helpers <= imported_helpers
    assert required_helpers <= called_helpers
    assert ".filter(users=" not in source
    assert "request.user.workspaces" not in source
