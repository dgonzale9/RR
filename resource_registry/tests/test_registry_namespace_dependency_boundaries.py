"""Static dependency boundaries for the Story 1 Registry Namespace foundation."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_ALLOWED_PYTHON_PATHS = (
    "resource_registry/exceptions.py",
    "resource_registry/models.py",
    "resource_registry/migrations/0001_initial.py",
    "resource_registry/dataset_visibility.py",
    "resource_registry/read/dtos.py",
    "resource_registry/read/exceptions.py",
    "resource_registry/read/registry_namespace_read_service.py",
    "resource_registry/services/registry_namespace_exceptions.py",
    "resource_registry/services/registry_namespace_resolution_service.py",
    "resource_registry/services/registry/dataset_registry_service.py",
    "resource_registry/services/registry_namespace_provisioning_service.py",
    "resource_registry/services/registry_namespace_enablement_service.py",
    "resource_registry/signals.py",
    "resource_registry/management/commands/provision_registry_namespace.py",
    "resource_registry/management/commands/map_verified_registry_namespace.py",
    "resource_registry/authorization/__init__.py",
    "resource_registry/authorization/registry_authorization_context_service.py",
    "resource_registry/authorization/worker_execution_authority.py",
    "resource_registry/authorization/tenancy_evidence_issuer.py",
    "resource_registry/authorization/tenancy_evidence_port.py",
    "resource_registry/admin.py",
    "resource_registry/intake/services.py",
)
_FRAMEWORK_INDEPENDENT_NAMESPACE_PATHS = tuple(
    relative_path
    for relative_path in _ALLOWED_PYTHON_PATHS
    if not relative_path.startswith("resource_registry/authorization/")
    and relative_path not in {"resource_registry/admin.py", "resource_registry/intake/services.py"}
)


def test_existing_runtime_modules_do_not_adopt_registry_namespace() -> None:
    """Allow Registry Namespace references only in approved architecture components."""
    unexpected_references: list[str] = []
    for path in (_ROOT / "resource_registry").rglob("*.py"):
        relative_path = path.relative_to(_ROOT).as_posix()
        if relative_path.startswith("resource_registry/tests/"):
            continue
        if relative_path in _ALLOWED_PYTHON_PATHS:
            continue
        if "RegistryNamespace" in path.read_text(encoding="utf-8"):
            unexpected_references.append(relative_path)
    assert unexpected_references == []


def test_namespace_foundation_does_not_import_persona_user_or_group_authorization() -> None:
    """Keep Story 1 independent from runtime and Workspace membership authorization."""
    prohibited_tokens = (
        "BotServer",
        "Persona",
        "OrganizationMembership",
        "django.contrib.auth.models import Group",
        "django.contrib.auth.models import User",
    )
    for relative_path in _FRAMEWORK_INDEPENDENT_NAMESPACE_PATHS:
        source = (_ROOT / relative_path).read_text(encoding="utf-8")
        for token in prohibited_tokens:
            assert token not in source, f"{relative_path} imports prohibited dependency {token}"
