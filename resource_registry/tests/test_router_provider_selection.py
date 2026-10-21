"""Verify ERR-5.6 pure deterministic Router Provider-selection evidence."""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
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
from resource_registry.execution_contracts import (
    ExecutionRequest,
    ExecutionRequestRef,
    ProviderSelectionPolicyRef,
    ProviderSelectionValidationError,
    RouterProviderCandidate,
    RouterProviderCandidateSet,
    RouterProviderSelectionEvidence,
    build_router_provider_selection_evidence,
)
from resource_registry.provider_contracts import (
    CertifiedProviderCapabilityBinding,
    ProviderCertificationEvidenceRef,
    ProviderCertificationProfileRef,
    ProviderCertificationRecord,
    ProviderHealthObservation,
    ProviderHealthObservationCategory,
    ProviderHealthObservationScope,
    ProviderIdentity,
    ProviderKey,
    ProviderVersion,
)

_EXECUTION_DIRECTORY = Path(__file__).resolve().parents[1] / "execution_contracts"
_PROVIDER_DIRECTORY = Path(__file__).resolve().parents[1] / "provider_contracts"
_SELECTION_PATH = _EXECUTION_DIRECTORY / "provider_selection.py"


def _build_request(version: str = "1.0.0") -> ExecutionRequest:
    """Build one exact execution request for Provider selection evidence."""
    return ExecutionRequest(
        ExecutionRequestRef("request.provider.selection.1"),
        CapabilityContractRef(CapabilityKey("atlas.tabular.transform"), CapabilityVersion(version)),
    )


def _build_candidate(
    implementation_reference: str = "atlas.duckdb.implementation.1",
    observed_at: datetime = datetime(2026, 7, 30, 12, tzinfo=UTC),
    contract_version: str = "1.0.0",
) -> RouterProviderCandidate:
    """Build one exact certified Provider candidate with fresh health evidence."""
    provider_identity = ProviderIdentity(
        ProviderKey("atlas.transformation.duckdb"), ProviderVersion("semver", "1.0.0")
    )
    certification = ProviderCertificationRecord(
        provider_identity,
        "atlas.provider.adapter.v1",
        "atlas.provider.configuration.v1",
        ProviderCertificationProfileRef("atlas.certification.profile.v1"),
        ProviderCertificationEvidenceRef("atlas.certification.evidence.1"),
    )
    binding = CertifiedProviderCapabilityBinding(
        certification,
        CapabilityImplementationConformanceDeclaration(
            CapabilityImplementationRef(implementation_reference),
            CapabilityContractRef(
                CapabilityKey("atlas.tabular.transform"), CapabilityVersion(contract_version)
            ),
            ConformanceEvidenceRef("atlas.conformance.evidence.1"),
        ),
    )
    health = ProviderHealthObservation(
        ProviderHealthObservationScope(
            provider_identity,
            implementation_reference,
            "atlas.deployment.primary",
            "region.us_central",
        ),
        ProviderHealthObservationCategory.READY,
        observed_at,
        observed_at + timedelta(minutes=5),
        "probe_succeeded",
    )
    return RouterProviderCandidate(binding, health)


def test_router_selection_evidence_is_deterministic_and_uses_first_policy_candidate() -> None:
    """Preserve supplied policy order without sorting while producing stable selection evidence."""
    request = _build_request()
    candidates = RouterProviderCandidateSet(
        (_build_candidate(), _build_candidate("atlas.duckdb.implementation.2"))
    )
    policy = ProviderSelectionPolicyRef("atlas.router.policy.v1")
    observed_at = datetime(2026, 7, 30, 12, 1, tzinfo=UTC)
    first = build_router_provider_selection_evidence(request, policy, observed_at, candidates)
    second = build_router_provider_selection_evidence(request, policy, observed_at, candidates)
    assert first.selected_candidate == candidates.candidates[0]
    assert first.checksum == second.checksum


@pytest.mark.parametrize(
    "policy_reference",
    (
        "https.policy.example",
        "credential.reference",
        "sdk.client",
        "secret.reference",
    ),
)
def test_provider_selection_policy_reference_rejects_sensitive_runtime_shapes(
    policy_reference: str,
) -> None:
    """Reject endpoint, credential, SDK, and secret-shaped policy references."""
    with pytest.raises(ProviderSelectionValidationError):
        ProviderSelectionPolicyRef(policy_reference)


@pytest.mark.parametrize(
    "scope_provider_identity,scope_implementation_reference",
    (
        (
            ProviderIdentity(
                ProviderKey("atlas.other.provider"), ProviderVersion("semver", "1.0.0")
            ),
            "atlas.duckdb.implementation.1",
        ),
        (
            ProviderIdentity(
                ProviderKey("atlas.transformation.duckdb"), ProviderVersion("semver", "1.0.0")
            ),
            "atlas.duckdb.implementation.other",
        ),
    ),
)
def test_router_provider_candidate_rejects_mismatched_health_scope(
    scope_provider_identity: ProviderIdentity,
    scope_implementation_reference: str,
) -> None:
    """Reject health evidence whose Provider or implementation differs from certification evidence."""
    candidate = _build_candidate()
    observation = candidate.health_observation
    mismatched_observation = ProviderHealthObservation(
        ProviderHealthObservationScope(
            scope_provider_identity,
            scope_implementation_reference,
            observation.scope.deployment_reference,
            observation.scope.region_reference,
        ),
        observation.category,
        observation.observed_at,
        observation.expires_at,
        observation.reason_code,
    )
    with pytest.raises(ProviderSelectionValidationError, match="mismatched"):
        RouterProviderCandidate(candidate.certification_binding, mismatched_observation)


@pytest.mark.parametrize(
    "factory",
    (
        lambda: ProviderSelectionPolicyRef("latest"),
        lambda: RouterProviderCandidateSet((_build_candidate(), _build_candidate())),
        lambda: build_router_provider_selection_evidence(
            _build_request(),
            ProviderSelectionPolicyRef("atlas.router.policy.v1"),
            datetime(2026, 7, 30, 12, 6, tzinfo=UTC),
            RouterProviderCandidateSet((_build_candidate(),)),
        ),
        lambda: build_router_provider_selection_evidence(
            _build_request("1.0.1"),
            ProviderSelectionPolicyRef("atlas.router.policy.v1"),
            datetime(2026, 7, 30, 12, 1, tzinfo=UTC),
            RouterProviderCandidateSet((_build_candidate(),)),
        ),
    ),
)
def test_router_selection_evidence_rejects_selector_duplicate_stale_and_mismatched_inputs(
    factory: object,
) -> None:
    """Reject non-exact and stale evidence before it can become a selection record."""
    with pytest.raises(ProviderSelectionValidationError):
        factory()  # type: ignore[operator]


def test_router_selection_evidence_rejects_non_first_selected_candidate() -> None:
    """Reject evidence that contradicts the supplied Router-policy candidate order."""
    request = _build_request()
    candidates = RouterProviderCandidateSet(
        (_build_candidate(), _build_candidate("atlas.duckdb.implementation.2"))
    )
    policy = ProviderSelectionPolicyRef("atlas.router.policy.v1")
    observed_at = datetime(2026, 7, 30, 12, 1, tzinfo=UTC)
    checksum = build_router_provider_selection_evidence(
        request, policy, observed_at, candidates
    ).checksum
    with pytest.raises(ProviderSelectionValidationError, match="not first"):
        RouterProviderSelectionEvidence(
            request,
            policy,
            observed_at,
            candidates,
            candidates.candidates[1],
            (),
            checksum,
        )


def test_router_selection_and_provider_contracts_preserve_dependency_direction() -> None:
    """Keep Provider contracts independent from Execution and selection free of runtime imports."""
    selection_tree = ast.parse(_SELECTION_PATH.read_text(encoding="utf-8"))
    selection_imports = {
        (node.module or "").split(".", maxsplit=1)[0]
        for node in ast.walk(selection_tree)
        if isinstance(node, ast.ImportFrom) and node.level == 0
    }
    assert selection_imports.isdisjoint(
        {
            "BotPlatform",
            "BotServer",
            "MCPServer",
            "celery",
            "django",
            "network",
            "persistence",
            "secrets",
            "socket",
            "transport",
            "workers",
        }
    )

    for provider_path in _PROVIDER_DIRECTORY.glob("*.py"):
        provider_tree = ast.parse(provider_path.read_text(encoding="utf-8"))
        assert not any(
            node.module is not None
            and node.module.startswith("resource_registry.execution_contracts")
            for node in ast.walk(provider_tree)
            if isinstance(node, ast.ImportFrom) and node.level == 0
        )
