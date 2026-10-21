"""Verify D6 dependency and retention boundaries without source-text false positives."""

from __future__ import annotations

import ast
import dataclasses
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from resource_registry.authorization import tenancy_evidence
from resource_registry.authorization.tenancy_evidence import (
    RegistryEvidenceConsumer,
    RegistryOwnedPrincipalContext,
    RegistryOwnerKind,
    RegistryPrincipalKind,
    RegistryTenancyEvidenceSession,
    RegistryTenancyFacts,
)
from resource_registry.authorization.tenancy_evidence_port import D6EvidenceRejected

_ROOT = Path(__file__).resolve().parents[2]
_D6_PRODUCTION_PATHS = (
    "resource_registry/authorization/tenancy_evidence.py",
    "resource_registry/authorization/tenancy_evidence_issuer.py",
    "resource_registry/authorization/tenancy_evidence_port.py",
)
_D6_TEST_PATHS = (
    "resource_registry/tests/test_tenancy_evidence.py",
    "resource_registry/tests/test_tenancy_evidence_dependency_boundaries.py",
)
_CLEANUP_AUTHORITY_SYMBOL = "_SessionCleanupAuthority"


def _d6_trees() -> dict[str, ast.Module]:
    """Return parsed syntax trees for the explicit current D6 production modules."""
    return {
        path: ast.parse((_ROOT / path).read_text(encoding="utf-8")) for path in _D6_PRODUCTION_PATHS
    }


def _imported_modules(tree: ast.Module) -> set[str]:
    """Return absolute modules imported by one syntax tree."""
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            modules.add(node.module)
    return modules


def _used_names(tree: ast.Module) -> set[str]:
    """Return executable identifiers without inspecting comments, docstrings, or literals."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
    return names


def _called_names(tree: ast.Module) -> set[str]:
    """Return names invoked by calls in executable syntax only."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                names.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                names.add(node.func.attr)
    return names


def _assert_no_retained_orm_value(value: object, visited: set[int] | None = None) -> None:
    """Assert that a session-visible graph retains no Django ORM or lazy ORM value."""
    if visited is None:
        visited = set()
    if id(value) in visited or isinstance(value, (str, bytes, int, float, bool, type(None))):
        return
    visited.add(id(value))
    assert not type(value).__module__.startswith("django.db.models")
    if isinstance(value, Mapping):
        for key, item in value.items():
            _assert_no_retained_orm_value(key, visited)
            _assert_no_retained_orm_value(item, visited)
    elif dataclasses.is_dataclass(value):
        for field in dataclasses.fields(value):
            _assert_no_retained_orm_value(getattr(value, field.name), visited)
    else:
        for slot in getattr(type(value), "__slots__", ()):
            if hasattr(value, slot):
                _assert_no_retained_orm_value(getattr(value, slot), visited)


class _Clock:
    """Provide fixed clock values for retention inspection."""

    def utc_now(self) -> datetime:
        """Return one valid UTC observation."""
        return datetime(2026, 8, 19, tzinfo=UTC)

    def monotonic_now(self) -> float:
        """Return one finite monotonic observation."""
        return 1.0


class _FactsSource:
    """Return immutable facts after any transient external lookup has completed."""

    def resolve_current_tenancy(
        self, principal_context: RegistryOwnedPrincipalContext
    ) -> RegistryTenancyFacts:
        """Return immutable authoritative facts without retaining the principal object."""
        return RegistryTenancyFacts(
            principal_id=41,
            principal_kind=RegistryPrincipalKind.DJANGO_USER,
            organization_id=7,
            workspace_id=13,
            namespace_id=UUID("11111111-1111-4111-8111-111111111111"),
            owner_kind=RegistryOwnerKind.WORKSPACE,
            owner_id=13,
        )


def test_d6_b01_resource_registry_imports_no_creative_assembly_module() -> None:
    """D6-B01: explicit D6 modules import no Creative Assembly module."""
    for path, tree in _d6_trees().items():
        assert not any(
            module.startswith("creative_assembly") for module in _imported_modules(tree)
        ), path


def test_d6_b02_d6_retains_no_django_orm_objects() -> None:
    """D6-B02: D6 retained state contains no Django ORM or lazy ORM object."""
    session = RegistryTenancyEvidenceSession.open(
        consumer=RegistryEvidenceConsumer.D4,
        trusted_principal_context=RegistryOwnedPrincipalContext(41, 7, workspace_id=13),
        tenancy_facts_source=_FactsSource(),
        clock=_Clock(),
    )
    proof = session.issue()
    _assert_no_retained_orm_value(session)
    session.consume(proof)
    _assert_no_retained_orm_value(session)


def test_d6_b03_no_http_api_or_bif_surface() -> None:
    """D6-B03: D6 declares no transport or BIF surface."""
    forbidden = {
        "HttpRequest",
        "HttpResponse",
        "APIView",
        "urlpatterns",
        "Serializer",
        "api_view",
        "bifs",
    }
    for tree in _d6_trees().values():
        assert forbidden.isdisjoint(_used_names(tree))
        assert not any(module.startswith("rest_framework") for module in _imported_modules(tree))


def test_d6_b04_no_persistence_model_or_migration() -> None:
    """D6-B04: explicit current D6 paths declare no model or persistence calls."""
    forbidden = {"Model", "db_table", "atomic", "save"}
    for path, tree in _d6_trees().items():
        assert "migrations" not in Path(path).parts
        assert forbidden.isdisjoint(_used_names(tree) | _called_names(tree))


def test_d6_b05_no_queue_worker_or_background_execution() -> None:
    """D6-B05: D6 imports and calls no queue or background execution surface."""
    forbidden = {"celery", "asyncio", "ThreadPoolExecutor", "shared_task", "ServiceBus"}
    for tree in _d6_trees().values():
        assert forbidden.isdisjoint(
            _used_names(tree) | _called_names(tree) | _imported_modules(tree)
        )


def test_d6_b06_no_serialization_or_cross_process_representation() -> None:
    """D6-B06: D6 imports no serializer and explicitly rejects reduction hooks."""
    for tree in _d6_trees().values():
        assert {"pickle", "json"}.isdisjoint(_imported_modules(tree))
    assert any("__reduce__" in _used_names(tree) for tree in _d6_trees().values())


def test_d6_b07_no_locator_or_provider_dependencies() -> None:
    """D6-B07: D6 imports no locator or provider module."""
    for tree in _d6_trees().values():
        assert not any(
            "locator" in module.lower() or "provider" in module.lower()
            for module in _imported_modules(tree)
        )


def test_d6_b08_no_e17_e18_or_d7_dependency_or_call() -> None:
    """D6-B08: D6 has no deferred E17, E18, or D7 executable reference."""
    forbidden = {
        "ContentAuthorizationRequest",
        "ContentAuthorizationDecision",
        "ContentAuthorizationReferenceResolver",
    }
    for tree in _d6_trees().values():
        assert forbidden.isdisjoint(_used_names(tree) | _called_names(tree))
        assert not any(
            "content_retrieval_admission" in module for module in _imported_modules(tree)
        )


def test_d6_b09_no_d5_content_bindings_inside_d6() -> None:
    """D6-B09: executable D6 declarations bind no excluded D5 content fields."""
    forbidden = {
        "asset",
        "snapshot",
        "audience",
        "invocation",
        "attempt",
        "integrity",
        "media_type",
        "credential",
        "payload_bytes",
    }
    for tree in _d6_trees().values():
        assert forbidden.isdisjoint(_used_names(tree))


def test_d6_b10_cleanup_authority_never_escapes_boundary_visible_structures() -> None:
    """D6-B10: cleanup authority remains absent from public exports and representations."""
    session = RegistryTenancyEvidenceSession.open(
        consumer=RegistryEvidenceConsumer.D4,
        trusted_principal_context=RegistryOwnedPrincipalContext(41, 7, workspace_id=13),
        tenancy_facts_source=_FactsSource(),
        clock=_Clock(),
    )
    assert _CLEANUP_AUTHORITY_SYMBOL not in repr(session)
    assert _CLEANUP_AUTHORITY_SYMBOL not in repr(session._cleanup_authority)
    with pytest.raises(D6EvidenceRejected):
        session._finalize_owned_evidence(authority=object())
    assert _CLEANUP_AUTHORITY_SYMBOL not in getattr(tenancy_evidence, "__all__", ())


def test_only_the_current_five_d6_modules_define_the_d6_scope() -> None:
    """The D6 scope names exactly the three implementation modules and two evidence tests."""
    assert set(_D6_PRODUCTION_PATHS) | set(_D6_TEST_PATHS) == {
        "resource_registry/authorization/tenancy_evidence.py",
        "resource_registry/authorization/tenancy_evidence_issuer.py",
        "resource_registry/authorization/tenancy_evidence_port.py",
        "resource_registry/tests/test_tenancy_evidence.py",
        "resource_registry/tests/test_tenancy_evidence_dependency_boundaries.py",
    }


def test_explicit_current_d6_module_paths_exist() -> None:
    """The explicitly authorized current D6 module paths are present in this checkout."""
    for path in _D6_PRODUCTION_PATHS + _D6_TEST_PATHS:
        assert (_ROOT / path).is_file()


def test_ast_boundary_checks_ignore_comments_docstrings_and_literals() -> None:
    """Boundary analysis is insulated from forbidden-looking non-executable text."""
    tree = ast.parse('"""models.Model QuerySet objects.get"""\nvalue = "asset provider"')
    assert {"Model", "QuerySet", "get", "asset", "provider"}.isdisjoint(_used_names(tree))
