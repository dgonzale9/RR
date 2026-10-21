from __future__ import annotations

from pathlib import Path

_BUILTIN_ROOT = Path(__file__).parents[1] / "transformations" / "providers" / "builtin"


def test_builtin_providers_do_not_own_runtime_or_infrastructure() -> None:
    forbidden = (
        "django.",
        "resource_registry.models",
        "DataRegistryStorageFactory",
        "TransformationProviderRegistry(",
        "importlib",
        "tabular_transformation_execution_service",
    )

    for source_file in _BUILTIN_ROOT.rglob("*.py"):
        source = source_file.read_text(encoding="utf-8")
        for value in forbidden:
            assert value not in source


def test_production_catalog_uses_explicit_imports_only() -> None:
    source = (_BUILTIN_ROOT / "catalog.py").read_text(encoding="utf-8")

    assert "LocalDuckDbTabularProvider" in source
    assert "importlib" not in source
    assert "entry_points" not in source
