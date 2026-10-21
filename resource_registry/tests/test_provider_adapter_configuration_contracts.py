"""Verify ERR-5.3 pure Provider adapter and configuration contracts."""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from resource_registry.provider_contracts import (
    OpaqueSecretReference,
    ProviderAdapterError,
    ProviderAdapterErrorCategory,
    ProviderAdapterRequest,
    ProviderAdapterResult,
    ProviderConfiguration,
    ProviderConfigurationRef,
    ProviderContractValidationError,
    ProviderIdentity,
    ProviderKey,
    ProviderVersion,
)

_CONTRACTS_DIRECTORY = Path(__file__).resolve().parents[1] / "provider_contracts"
_ADAPTERS_PATH = _CONTRACTS_DIRECTORY / "adapters.py"
_CONFIGURATION_PATH = _CONTRACTS_DIRECTORY / "configuration.py"


def _build_configuration_reference() -> ProviderConfigurationRef:
    """Build one exact safe Provider configuration reference."""
    return ProviderConfigurationRef(
        ProviderIdentity(
            ProviderKey("atlas.transformation.duckdb"), ProviderVersion("semver", "1.0.0")
        ),
        "atlas.provider.config.v1",
        "atlas.deployment.primary",
        "revision.20260730",
    )


def test_provider_configuration_is_immutable_and_serializes_only_safe_evidence() -> None:
    """Preserve non-secret settings and opaque secret references deterministically."""
    configuration = ProviderConfiguration(
        _build_configuration_reference(),
        (("dialect", "standard"), ("max_rows", "100")),
        (OpaqueSecretReference("vault.production.primary"),),
    )
    canonical = configuration.to_canonical_json()
    assert (
        canonical
        == ProviderConfiguration(
            configuration.reference,
            tuple(reversed(configuration.settings)),
            configuration.secret_references,
        ).to_canonical_json()
    )
    assert '"secret_references":["vault.production.primary"]' in canonical
    with pytest.raises(FrozenInstanceError):
        configuration.settings = ()  # type: ignore[misc]


@pytest.mark.parametrize(
    "factory",
    (
        lambda: ProviderConfigurationRef(
            _build_configuration_reference().provider_identity,
            "atlas.provider.config.v1",
            "atlas.deployment.primary",
            "",
        ),
        lambda: ProviderConfiguration(
            _build_configuration_reference(), (("api_token", "not_allowed"),)
        ),
        lambda: ProviderConfiguration(
            _build_configuration_reference(), (("endpoint", "https.example.com"),)
        ),
        lambda: ProviderConfiguration(
            _build_configuration_reference(), (("sdk_client", "instance"),)
        ),
        lambda: ProviderConfiguration(
            _build_configuration_reference(), (("dialect", "standard"),) * 2
        ),
    ),
)
def test_provider_configuration_rejects_secret_endpoint_sdk_and_ambiguous_values(
    factory: object,
) -> None:
    """Reject unsafe, unbounded, or duplicate configuration evidence."""
    with pytest.raises(ProviderContractValidationError):
        factory()  # type: ignore[operator]


def test_provider_adapter_envelopes_preserve_references_without_runtime_authority() -> None:
    """Preserve immutable request, result, and normalized error evidence."""
    reference = _build_configuration_reference()
    request = ProviderAdapterRequest(
        reference.provider_identity,
        reference,
        "tabular.transform",
        ("input.artifact.1",),
    )
    result = ProviderAdapterResult(request, ("output.artifact.1",))
    error = ProviderAdapterError(ProviderAdapterErrorCategory.TIMEOUT, "provider_timeout")
    assert '"operation_identifier":"tabular.transform"' in request.to_canonical_json()
    assert result.output_references == ("output.artifact.1",)
    assert error.category is ProviderAdapterErrorCategory.TIMEOUT
    with pytest.raises(FrozenInstanceError):
        request.operation_identifier = "another.operation"  # type: ignore[misc]


@pytest.mark.parametrize(
    "factory",
    (
        lambda: ProviderAdapterRequest(
            ProviderIdentity(
                ProviderKey("atlas.other.provider"), ProviderVersion("semver", "1.0.0")
            ),
            _build_configuration_reference(),
            "tabular.transform",
        ),
        lambda: ProviderAdapterRequest(
            _build_configuration_reference().provider_identity,
            _build_configuration_reference(),
            "sdk.client.invoke",
        ),
        lambda: ProviderAdapterResult(
            ProviderAdapterRequest(
                _build_configuration_reference().provider_identity,
                _build_configuration_reference(),
                "tabular.transform",
            ),
            ("https.example.com",),
        ),
        lambda: ProviderAdapterError(ProviderAdapterErrorCategory.UNKNOWN, "vendor_secret_detail"),
    ),
)
def test_provider_adapter_envelopes_reject_selection_payload_and_sensitive_shapes(
    factory: object,
) -> None:
    """Reject inconsistent identity, SDK-shaped, endpoint, and secret-bearing evidence."""
    with pytest.raises(ProviderContractValidationError):
        factory()  # type: ignore[operator]


def test_provider_adapter_and_configuration_contracts_have_no_runtime_dependencies() -> None:
    """Keep ERR-5.3 contracts independent from runtime, SDK, and sensitive boundaries."""
    imported_modules: set[str] = set()
    for source_path in (_ADAPTERS_PATH, _CONFIGURATION_PATH):
        syntax_tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imported_modules.update(
            (node.module or "").split(".", maxsplit=1)[0]
            for node in ast.walk(syntax_tree)
            if isinstance(node, ast.ImportFrom) and node.level == 0
        )

    assert imported_modules.isdisjoint(
        {
            "BotPlatform",
            "BotServer",
            "MCPServer",
            "celery",
            "django",
            "execution",
            "network",
            "persistence",
            "registry_core",
            "router",
            "secrets",
            "socket",
            "transport",
            "workers",
        }
    )
