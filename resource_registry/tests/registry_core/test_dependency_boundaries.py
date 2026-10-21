"""Verify the ERR-0.1-S1 Registry Core package boundary."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

from resource_registry import registry_core
from resource_registry.registry_core import (
    REGISTRY_CORE_CONTRACT_VERSION,
    OwnerDescriptor,
    OwnershipClassification,
    RegistryCoreValidationError,
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_CORE_PACKAGE_ROOT = _REPOSITORY_ROOT / "resource_registry" / "registry_core"
_EXPECTED_CORE_SOURCE_FILENAMES = frozenset(
    {
        "__init__.py",
        "errors.py",
        "identity.py",
        "metadata.py",
        "ownership.py",
        "projection_envelopes.py",
        "references.py",
        "version.py",
        "versioning.py",
    }
)
_PERMITTED_IMPORT_TIME_CALL_NAMES = frozenset({"dataclass"})
_PROHIBITED_IMPORT_ROOTS = frozenset(
    {
        "BotPlatform",
        "BotServer",
        "CloudServices",
        "Django",
        "Embeddings",
        "MCPServer",
        "Organization",
        "resource_registry.api",
        "resource_registry.application",
        "resource_registry.authorization",
        "resource_registry.bifs",
        "resource_registry.events",
        "resource_registry.management",
        "resource_registry.migrations",
        "resource_registry.models",
        "resource_registry.persistence",
        "resource_registry.policies",
        "resource_registry.providers",
        "resource_registry.read",
        "resource_registry.read_auth",
        "resource_registry.services",
        "resource_registry.storage",
        "resource_registry.transformations",
        "resource_registry.workers",
        "django",
    }
)
_RUNTIME_PACKAGE_PATHS = (
    "BotPlatform",
    "BotServer",
    "CloudServices",
    "Embeddings",
    "MCPServer",
    "Organization",
    "resource_registry",
    "rfi_mgmt",
)
_AUTHORIZED_REGISTRY_CORE_CONSUMERS = frozenset(
    {
        "resource_registry/authorization/content_authorization_reference_resolver.py",
        "resource_registry/authorized_discovery_module.py",
        "resource_registry/capability_catalog_projection/contracts.py",
        "resource_registry/catalog_module.py",
        "resource_registry/dataset_event_contracts.py",
        "resource_registry/event_contracts/contracts.py",
        "resource_registry/execution_contracts/admission.py",
        "resource_registry/execution_contracts/contracts.py",
        "resource_registry/execution_contracts/lifecycle.py",
        "resource_registry/execution_contracts/output_registration.py",
        "resource_registry/identity_type_module.py",
        "resource_registry/metadata_schema/admission.py",
        "resource_registry/metadata_schema/declarations.py",
        "resource_registry/metadata_schema/definitions.py",
        "resource_registry/metadata_schema/payload_measurement.py",
        "resource_registry/metadata_schema/persistence.py",
        "resource_registry/metadata_schema/publications.py",
        "resource_registry/namespace_module.py",
        "resource_registry/owner_reference_module.py",
        "resource_registry/projection_ingestion_module.py",
        "resource_registry/rebuild_comparison_module.py",
        "resource_registry/registry_native_resource_module.py",
        "resource_registry/registry_native/versions.py",
        "resource_registry/relationship_module.py",
        "resource_registry/resource_capability_support/contracts.py",
        "resource_registry/search_document_module.py",
        "resource_registry/search_provider_module.py",
        "resource_registry/services/registry/dataset_registry_service.py",
    }
)


def _iter_core_source_paths() -> Iterator[Path]:
    """Yield Python sources that make up the Registry Core package.

    Yields:
        Paths to Registry Core Python source files.
    """
    yield from sorted(_CORE_PACKAGE_ROOT.glob("*.py"))


def _get_imported_module_names(source_path: Path) -> set[str]:
    """Parse import targets from a Python source file.

    Args:
        source_path: Python file whose imports are inspected.

    Returns:
        Fully qualified imported module names, excluding package-relative imports.
    """
    source_tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    imported_module_names: set[str] = set()
    for node in ast.walk(source_tree):
        if isinstance(node, ast.Import):
            imported_module_names.update(imported.name for imported in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module is not None:
            imported_module_names.add(node.module)
    return imported_module_names


def _get_import_time_call_names(source_path: Path) -> set[str]:
    """Parse calls evaluated while a module is imported.

    Args:
        source_path: Python file whose module-level statements are inspected.

    Returns:
        Names invoked while the module is imported.
    """
    source_tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    called_names: set[str] = set()
    for node in source_tree.body:
        if isinstance(node, ast.ClassDef):
            nodes_to_inspect = (*node.decorator_list, *node.bases, *node.keywords)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            nodes_to_inspect = (*node.decorator_list,)
        else:
            nodes_to_inspect = (node,)
        for inspected_node in nodes_to_inspect:
            for descendant in ast.walk(inspected_node):
                if isinstance(descendant, ast.Call):
                    called_names.add(ast.unparse(descendant.func))
    return called_names


def _imports_registry_core(source_path: Path) -> bool:
    """Determine whether a production module imports Registry Core.

    Args:
        source_path: Python source file to inspect.

    Returns:
        Whether the source file imports the Registry Core package or one of its members.
    """
    source_tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    for node in ast.walk(source_tree):
        if isinstance(node, ast.Import):
            if any(
                imported.name.startswith("resource_registry.registry_core")
                for imported in node.names
            ):
                return True
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module is not None:
                if node.module.startswith("resource_registry.registry_core"):
                    return True
                if node.module == "resource_registry" and any(
                    imported.name == "registry_core" for imported in node.names
                ):
                    return True
            elif (
                source_path.is_relative_to(_REPOSITORY_ROOT / "resource_registry")
                and node.level == 1
                and (
                    node.module == "registry_core"
                    or (
                        node.module is None
                        and any(imported.name == "registry_core" for imported in node.names)
                    )
                )
            ):
                return True
    return False


def _get_runtime_registry_core_consumers() -> list[str]:
    """Find production Python modules that adopt Registry Core.

    Returns:
        Repository-relative paths to unexpected runtime consumers.
    """
    runtime_consumers: list[str] = []
    for runtime_package_path in _RUNTIME_PACKAGE_PATHS:
        package_path = _REPOSITORY_ROOT / runtime_package_path
        if not package_path.exists():
            continue
        for source_path in package_path.rglob("*.py"):
            relative_path = source_path.relative_to(_REPOSITORY_ROOT).as_posix()
            if relative_path.startswith("resource_registry/tests/"):
                continue
            if relative_path.startswith("resource_registry/registry_core/"):
                continue
            if _imports_registry_core(source_path):
                runtime_consumers.append(relative_path)
    return sorted(runtime_consumers)


def test_registry_core_exports_only_its_authorized_s2_boundary() -> None:
    """Expose completed Epic 0.1 and authorized ERR-0.2-S1/S2 contracts only."""
    assert registry_core.__all__ == (
        "REGISTRY_CORE_CONTRACT_VERSION",
        "OwnerDescriptor",
        "OwnershipClassification",
        "OpaqueReference",
        "ProjectionEnvelope",
        "ProjectionTombstoneState",
        "RegistryCoreValidationError",
        "NamespaceKey",
        "ResourceTypeKey",
        "ResourceKey",
        "ResourceVersionKey",
        "ArtifactKey",
        "MetadataNamespaceKey",
        "MetadataSchemaKey",
        "MetadataClassification",
        "MetadataScope",
        "MetadataProvenance",
        "MetadataEnvelope",
    )
    assert REGISTRY_CORE_CONTRACT_VERSION == "0.1"
    assert isinstance(REGISTRY_CORE_CONTRACT_VERSION, str)
    assert OwnerDescriptor.__module__ == "resource_registry.registry_core.ownership"
    assert OwnershipClassification.__module__ == "resource_registry.registry_core.ownership"


def test_registry_core_error_is_deterministic_and_has_no_domain_semantics() -> None:
    """Keep the Core validation error a stable pure ValueError base."""
    validation_error = RegistryCoreValidationError("invalid contract value")

    assert isinstance(validation_error, ValueError)
    assert str(validation_error) == "invalid contract value"
    assert validation_error.args == ("invalid contract value",)


def test_registry_core_source_imports_only_standard_library_or_relative_modules() -> None:
    """Reject framework and existing-runtime dependencies from Registry Core sources."""
    nonstandard_imports: dict[str, set[str]] = {}
    for source_path in _iter_core_source_paths():
        imported_module_names = _get_imported_module_names(source_path)
        matching_imports = {
            module_name
            for module_name in imported_module_names
            if module_name.partition(".")[0] not in sys.stdlib_module_names
        }
        if matching_imports:
            nonstandard_imports[source_path.name] = matching_imports
    assert nonstandard_imports == {}

    prohibited_imports = {
        source_path.name: {
            module_name
            for module_name in _get_imported_module_names(source_path)
            if any(
                module_name == prohibited_root or module_name.startswith(f"{prohibited_root}.")
                for prohibited_root in _PROHIBITED_IMPORT_ROOTS
            )
        }
        for source_path in _iter_core_source_paths()
    }
    assert prohibited_imports == {
        source_path.name: set() for source_path in _iter_core_source_paths()
    }


def test_registry_core_source_has_no_prohibited_import_time_side_effects() -> None:
    """Reject calls that could perform I/O or runtime setup at import time."""
    import_time_calls: dict[str, set[str]] = {}
    for source_path in _iter_core_source_paths():
        called_names = _get_import_time_call_names(source_path)
        prohibited_called_names = called_names - _PERMITTED_IMPORT_TIME_CALL_NAMES
        if prohibited_called_names:
            import_time_calls[source_path.name] = prohibited_called_names
    assert import_time_calls == {}


def test_registry_core_imports_in_a_clean_python_process() -> None:
    """Import Registry Core without Django settings or infrastructure configuration."""
    import_environment = os.environ.copy()
    import_environment.pop("DJANGO_SETTINGS_MODULE", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import resource_registry.registry_core as core; "
            "assert core.REGISTRY_CORE_CONTRACT_VERSION == '0.1'; "
            "assert core.OwnerDescriptor('registry-core', "
            "core.OwnershipClassification.REGISTRY_NATIVE).bounded_context_identifier "
            "== 'registry-core'",
        ],
        cwd=_REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        env=import_environment,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_existing_runtime_modules_do_not_adopt_registry_core() -> None:
    """Allow only reviewed Phase 0/1 consumers to depend inward on Registry Core."""
    assert _get_runtime_registry_core_consumers() == sorted(_AUTHORIZED_REGISTRY_CORE_CONSUMERS)


def test_registry_core_contains_only_authorized_source_modules() -> None:
    """Keep Registry Core free of persistence and unauthorized later-story modules."""
    assert {
        source_path.name for source_path in _iter_core_source_paths()
    } == _EXPECTED_CORE_SOURCE_FILENAMES
    assert not list((_REPOSITORY_ROOT / "resource_registry" / "migrations").glob("*registry_core*"))
