from __future__ import annotations

import ast
from pathlib import Path


def test_operational_modules_do_not_depend_on_forbidden_layers() -> None:
    operational_root = Path("resource_registry/transformations/operational")
    forbidden_prefixes = (
        "resource_registry.api",
        "resource_registry.serializers",
        "resource_registry.storage",
        "BotServer.built_in_functions",
    )

    for source_path in operational_root.glob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imported_modules = _imported_modules(tree)
        assert not any(module.startswith(forbidden_prefixes) for module in imported_modules), (
            source_path
        )


def _imported_modules(tree: ast.AST) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    return imported
