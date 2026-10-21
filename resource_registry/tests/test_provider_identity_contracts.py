"""Verify ERR-5.1 pure Provider identity and descriptor contracts."""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from resource_registry.provider_contracts import (
    ProviderContractValidationError,
    ProviderDescriptor,
    ProviderFunctionalClassification,
    ProviderIdentity,
    ProviderKey,
    ProviderLifecycleStatus,
    ProviderOriginClassification,
    ProviderVersion,
    build_provider_registry_manifest,
    freeze_provider_registry_manifest,
    resolve_registered_provider,
)

_CONTRACTS_PATH = Path(__file__).resolve().parents[1] / "provider_contracts" / "contracts.py"
_REGISTRY_PATH = Path(__file__).resolve().parents[1] / "provider_contracts" / "registry.py"


def _build_descriptor() -> ProviderDescriptor:
    """Build one valid immutable safe Provider descriptor."""
    return ProviderDescriptor(
        ProviderIdentity(
            ProviderKey("atlas.transformation.duckdb"), ProviderVersion("semver", "1.0.0")
        ),
        (ProviderFunctionalClassification.TRANSFORMATION,),
        ProviderOriginClassification.INTERNAL,
        ProviderLifecycleStatus.CERTIFIED,
        ("tabular.transform",),
        "atlas.provider.contract.v1",
        "atlas.provider.config.v1",
        "atlas.provider.provenance.v1",
        ("reference_only_inputs",),
    )


def test_provider_descriptor_preserves_exact_safe_immutable_identity() -> None:
    """Preserve canonical safe descriptor facts without runtime behavior."""
    descriptor = _build_descriptor()
    assert '"key":"atlas.transformation.duckdb"' in descriptor.to_canonical_json()
    with pytest.raises(FrozenInstanceError):
        descriptor.identity = ProviderIdentity(
            ProviderKey("atlas.other.provider"), ProviderVersion("semver", "1.0.0")
        )  # type: ignore[misc]


@pytest.mark.parametrize(
    "factory",
    (
        lambda: ProviderKey("Atlas.Provider"),
        lambda: ProviderVersion("semver", "latest"),
        lambda: ProviderVersion("semver", "1.0"),
        lambda: ProviderDescriptor(
            ProviderIdentity(
                ProviderKey("atlas.provider.test"), ProviderVersion("semver", "1.0.0")
            ),
            (),
            ProviderOriginClassification.INTERNAL,
            ProviderLifecycleStatus.DRAFT,
        ),
    ),
)
def test_provider_contracts_reject_ambiguous_or_incomplete_values(factory: object) -> None:
    """Reject selectors, aliases, malformed versions, and empty classifications."""
    with pytest.raises(ProviderContractValidationError):
        factory()  # type: ignore[operator]


def test_provider_contracts_support_explicit_external_version_schemes() -> None:
    """Preserve external exact version evidence without treating it as semantic versioning."""
    assert ProviderVersion("external_api", "2026-07-30").value == "2026-07-30"


def test_provider_contracts_have_no_runtime_or_sensitive_dependencies() -> None:
    """Keep Provider identity contracts independent from runtime and sensitive boundaries."""
    syntax_tree = ast.parse(_CONTRACTS_PATH.read_text(encoding="utf-8"))
    imported_modules = {
        (node.module or "").split(".", maxsplit=1)[0]
        for node in ast.walk(syntax_tree)
        if isinstance(node, ast.ImportFrom) and node.level == 0
    }

    assert imported_modules.isdisjoint(
        {
            "django",
            "BotPlatform",
            "BotServer",
            "MCPServer",
            "adapters",
            "celery",
            "configuration",
            "execution",
            "providers",
            "secrets",
            "workers",
        }
    )


def test_provider_registry_builds_deterministically_freezes_and_resolves_exactly() -> None:
    """Build a pure manifest that resolves only exact frozen Provider identities."""
    first = _build_descriptor()
    second = ProviderDescriptor(
        ProviderIdentity(ProviderKey("atlas.storage.local"), ProviderVersion("semver", "1.0.0")),
        (ProviderFunctionalClassification.STORAGE,),
        ProviderOriginClassification.INTERNAL,
        ProviderLifecycleStatus.CERTIFIED,
    )
    built = build_provider_registry_manifest((first, second))
    reordered = build_provider_registry_manifest((second, first))
    assert built.status.value == "built"
    assert built.manifest is not None
    assert built.manifest.checksum == reordered.manifest.checksum  # type: ignore[union-attr]
    frozen = freeze_provider_registry_manifest(built.manifest)
    assert resolve_registered_provider(frozen, first.identity) == first
    with pytest.raises(ProviderContractValidationError, match="unknown provider registry identity"):
        resolve_registered_provider(
            frozen,
            ProviderIdentity(
                ProviderKey("atlas.unknown.provider"), ProviderVersion("semver", "1.0.0")
            ),
        )


def test_provider_registry_rejects_duplicate_or_empty_manifests() -> None:
    """Reject ambiguous identities and empty manifests before a registry can freeze."""
    descriptor = _build_descriptor()
    assert build_provider_registry_manifest(()).status.value == "rejected"
    assert build_provider_registry_manifest((descriptor, descriptor)).status.value == "rejected"


def test_provider_registry_has_no_runtime_or_sensitive_dependencies() -> None:
    """Keep Provider Registry contracts independent from runtime composition boundaries."""
    syntax_tree = ast.parse(_REGISTRY_PATH.read_text(encoding="utf-8"))
    imported_modules = {
        (node.module or "").split(".", maxsplit=1)[0]
        for node in ast.walk(syntax_tree)
        if isinstance(node, ast.ImportFrom) and node.level == 0
    }

    assert imported_modules.isdisjoint(
        {
            "django",
            "BotPlatform",
            "BotServer",
            "MCPServer",
            "celery",
            "configuration",
            "execution",
            "network",
            "persistence",
            "providers",
            "registry_core",
            "router",
            "secrets",
            "socket",
            "workers",
        }
    )
