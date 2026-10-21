"""Verify ERR-5.4 pure Provider certification and binding contracts."""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from resource_registry.capability_definitions import (
    CapabilityContractRef,
    CapabilityKey,
    CapabilityVersion,
)
from resource_registry.capability_implementation_bindings import (
    CapabilityImplementationConformanceDeclaration,
    CapabilityImplementationRef,
    ConformanceEvidenceRef,
)
from resource_registry.provider_contracts import (
    CertifiedProviderCapabilityBinding,
    CertifiedProviderCapabilityBindingSet,
    ProviderCertificationBindingValidationError,
    ProviderCertificationEvidenceRef,
    ProviderCertificationProfileRef,
    ProviderCertificationRecord,
    ProviderIdentity,
    ProviderKey,
    ProviderVersion,
)

_CONTRACTS_DIRECTORY = Path(__file__).resolve().parents[1] / "provider_contracts"
_BINDINGS_PATH = _CONTRACTS_DIRECTORY / "bindings.py"
_CERTIFICATION_PATH = _CONTRACTS_DIRECTORY / "certification.py"


def _build_certification_record(
    evidence_value: str = "evidence.20260730",
    profile_value: str = "atlas.certification.profile.v1",
) -> ProviderCertificationRecord:
    """Build one exact immutable Provider certification record."""
    return ProviderCertificationRecord(
        ProviderIdentity(
            ProviderKey("atlas.transformation.duckdb"), ProviderVersion("semver", "1.0.0")
        ),
        "atlas.provider.adapter.v1",
        "atlas.provider.configuration.v1",
        ProviderCertificationProfileRef(profile_value),
        ProviderCertificationEvidenceRef(evidence_value),
    )


def _build_conformance_declaration(
    version: str = "1.0.0",
) -> CapabilityImplementationConformanceDeclaration:
    """Build one existing exact ERR-3.3 Capability conformance declaration."""
    return CapabilityImplementationConformanceDeclaration(
        CapabilityImplementationRef("atlas.duckdb.implementation.1"),
        CapabilityContractRef(CapabilityKey("atlas.tabular.transform"), CapabilityVersion(version)),
        ConformanceEvidenceRef("atlas.conformance.evidence.1"),
    )


def test_provider_certification_record_is_immutable_exact_evidence() -> None:
    """Preserve exact Provider certification facts without establishing live certification."""
    record = _build_certification_record()
    assert record.evidence_reference.value == "evidence.20260730"
    with pytest.raises(FrozenInstanceError):
        record.adapter_contract_reference = "atlas.provider.adapter.v2"  # type: ignore[misc]


@pytest.mark.parametrize(
    "factory",
    (
        lambda: ProviderCertificationProfileRef("latest"),
        lambda: ProviderCertificationEvidenceRef("https.evidence.example"),
        lambda: ProviderCertificationRecord(
            _build_certification_record().provider_identity,
            "sdk.client",
            "atlas.provider.configuration.v1",
            ProviderCertificationProfileRef("atlas.certification.profile.v1"),
            ProviderCertificationEvidenceRef("evidence.20260730"),
        ),
        lambda: ProviderCertificationRecord(
            _build_certification_record().provider_identity,
            "atlas.provider.adapter.v1",
            "tenant.policy",
            ProviderCertificationProfileRef("atlas.certification.profile.v1"),
            ProviderCertificationEvidenceRef("evidence.20260730"),
        ),
    ),
)
def test_provider_certification_record_rejects_selectors_and_runtime_shaped_values(
    factory: object,
) -> None:
    """Reject selectors, endpoints, SDK values, and policy-shaped configuration facts."""
    with pytest.raises(ProviderCertificationBindingValidationError):
        factory()  # type: ignore[operator]


def test_certified_provider_binding_preserves_distinct_exact_evidence() -> None:
    """Keep distinct exact certification evidence and Capability contract versions separate."""
    first = CertifiedProviderCapabilityBinding(
        _build_certification_record(), _build_conformance_declaration()
    )
    second = CertifiedProviderCapabilityBinding(
        _build_certification_record("evidence.20260731"), _build_conformance_declaration("1.0.1")
    )
    bindings = CertifiedProviderCapabilityBindingSet((first, second))
    assert bindings.bindings == (first, second)


def test_certified_provider_binding_set_rejects_duplicate_exact_associations() -> None:
    """Reject duplicate certification and conformance evidence associations fail closed."""
    binding = CertifiedProviderCapabilityBinding(
        _build_certification_record(), _build_conformance_declaration()
    )
    with pytest.raises(ProviderCertificationBindingValidationError, match="duplicate"):
        CertifiedProviderCapabilityBindingSet((binding, binding))


def test_certified_provider_binding_set_rejects_reused_evidence_for_same_conformance() -> None:
    """Reject reused exact evidence even when another certification profile is claimed."""
    declaration = _build_conformance_declaration()
    first = CertifiedProviderCapabilityBinding(_build_certification_record(), declaration)
    second = CertifiedProviderCapabilityBinding(
        _build_certification_record(profile_value="atlas.certification.profile.v2"), declaration
    )
    with pytest.raises(ProviderCertificationBindingValidationError, match="duplicate"):
        CertifiedProviderCapabilityBindingSet((first, second))


def test_certification_and_binding_contracts_have_no_runtime_dependencies() -> None:
    """Keep ERR-5.4 contracts independent from framework, SDK, and runtime boundaries."""
    imported_modules: set[str] = set()
    for source_path in (_CERTIFICATION_PATH, _BINDINGS_PATH):
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
