from __future__ import annotations

from dataclasses import dataclass

import pytest

from resource_registry.bifs.capability_resolution import DataRegistryBifCapabilityResolver
from resource_registry.bifs.exceptions import InvalidCapabilityPolicyError
from resource_registry.events.audit_service import AuditService
from resource_registry.events.event_publisher import InMemoryEventPublisher
from resource_registry.events.event_types import EventType
from resource_registry.policies.models import ResolvedPolicy


@dataclass(frozen=True)
class _DatasetStub:
    id: str
    tenant_id: str


class _FakePolicyResolutionService:
    def __init__(
        self,
        *,
        tenant_policy: ResolvedPolicy | None = None,
        dataset_policy: ResolvedPolicy | None = None,
    ) -> None:
        self._tenant_policy = tenant_policy
        self._dataset_policy = dataset_policy

    def resolve_tenant_policy(self, *, tenant_id: str, policy_key: str) -> ResolvedPolicy | None:
        _ = tenant_id, policy_key
        return self._tenant_policy

    def resolve_dataset_policy(
        self,
        *,
        dataset: _DatasetStub,
        policy_key: str,
    ) -> ResolvedPolicy | None:
        _ = dataset, policy_key
        return self._dataset_policy


def test_system_default_capabilities() -> None:
    resolver = DataRegistryBifCapabilityResolver(
        policy_resolution_service=_FakePolicyResolutionService(),
    )
    resolved = resolver.resolve(tenant_id="tenant-a", roles=("restricted_reader",))
    assert "resource_registry.dataset.list" in resolved.enabled_capabilities
    assert "resource_registry.dataset.exploration.read" in resolved.enabled_capabilities
    assert "resource_registry.dataset.query.read" in resolved.enabled_capabilities
    assert "resource_registry.dataset.query.job.start" in resolved.enabled_capabilities
    assert "resource_registry.dataset.query.job.read" in resolved.enabled_capabilities
    assert "resource_registry.dataset.query.plan" in resolved.enabled_capabilities
    assert "resource_registry.dataset.derived.create" in resolved.disabled_capabilities
    assert "resource_registry.dataset.export.plan" in resolved.enabled_capabilities
    assert "resource_registry.dataset.export_plan.read" in resolved.enabled_capabilities
    assert "resource_registry.dataset.intelligence.job.read" in resolved.enabled_capabilities
    assert "resource_registry.transformation.run.read" in resolved.enabled_capabilities
    assert "resource_registry.transformation.step.read" in resolved.enabled_capabilities
    assert "resource_registry.transformation.output.read" in resolved.enabled_capabilities
    assert "resource_registry.dataset.artifact.generate" in resolved.disabled_capabilities
    assert "resource_registry.dataset.intelligence.job.start" in resolved.disabled_capabilities
    assert "resource_registry.transformation.diagnostics.read" in resolved.disabled_capabilities
    assert "resource_registry.external_jobs.read" in resolved.disabled_capabilities


def test_normal_reader_can_analyze_chart_and_read_saved_results_by_default() -> None:
    """Allow a normal reader to use insight capabilities while retaining write denials."""
    resolver = DataRegistryBifCapabilityResolver(
        policy_resolution_service=_FakePolicyResolutionService(),
    )

    resolved = resolver.resolve(tenant_id="tenant-a", roles=("restricted_reader",))

    assert {
        "resource_registry.dataset.analysis.read",
        "resource_registry.dataset.chart.read",
        "resource_registry.dataset.query_run.read",
        "resource_registry.dataset.analysis_artifact.read",
        "resource_registry.dataset.chart_artifact.read",
    }.issubset(resolved.enabled_capabilities)
    assert {
        "resource_registry.dataset.derived.create",
        "resource_registry.dataset.artifact.generate",
        "resource_registry.external_jobs.read",
        "resource_registry.dataset.intelligence.job.start",
    }.issubset(resolved.disabled_capabilities)


def test_tenant_default_override() -> None:
    tenant_policy = ResolvedPolicy(
        source="tenant_default",
        policy_key="resource_registry_bif_capabilities_default",
        tenant_id="tenant-a",
        dataset_id=None,
        revision=2,
        policy_id="p-1",
        rules_json={
            "enabled_capabilities": ["resource_registry.external_jobs.read"],
            "disabled_capabilities": [],
            "role_overrides": {},
        },
    )
    resolver = DataRegistryBifCapabilityResolver(
        policy_resolution_service=_FakePolicyResolutionService(tenant_policy=tenant_policy),
    )
    resolved = resolver.resolve(tenant_id="tenant-a", roles=("reader",))
    assert "resource_registry.external_jobs.read" in resolved.enabled_capabilities


def test_disabled_capability_wins() -> None:
    tenant_policy = ResolvedPolicy(
        source="tenant_default",
        policy_key="resource_registry_bif_capabilities_default",
        tenant_id="tenant-a",
        dataset_id=None,
        revision=3,
        policy_id="p-2",
        rules_json={
            "enabled_capabilities": ["resource_registry.dataset.profile.read"],
            "disabled_capabilities": ["resource_registry.dataset.profile.read"],
            "role_overrides": {},
        },
    )
    resolver = DataRegistryBifCapabilityResolver(
        policy_resolution_service=_FakePolicyResolutionService(tenant_policy=tenant_policy),
    )
    resolved = resolver.resolve(tenant_id="tenant-a", roles=("reader",))
    assert "resource_registry.dataset.profile.read" not in resolved.enabled_capabilities
    assert "resource_registry.dataset.profile.read" in resolved.disabled_capabilities


def test_role_override_for_reader() -> None:
    resolver = DataRegistryBifCapabilityResolver(
        policy_resolution_service=_FakePolicyResolutionService(),
    )
    resolved = resolver.resolve(tenant_id="tenant-a", roles=("reader",))
    assert "resource_registry.external_jobs.read" in resolved.enabled_capabilities


def test_restricted_reader_external_jobs_disabled_by_default() -> None:
    resolver = DataRegistryBifCapabilityResolver(
        policy_resolution_service=_FakePolicyResolutionService(),
    )
    resolved = resolver.resolve(tenant_id="tenant-a", roles=("restricted_reader",))
    assert "resource_registry.external_jobs.read" in resolved.disabled_capabilities


def test_wildcard_allowed_only_for_admin() -> None:
    tenant_policy = ResolvedPolicy(
        source="tenant_default",
        policy_key="resource_registry_bif_capabilities_default",
        tenant_id="tenant-a",
        dataset_id=None,
        revision=4,
        policy_id="p-3",
        rules_json={
            "enabled_capabilities": [],
            "disabled_capabilities": [],
            "role_overrides": {
                "reader": {
                    "enabled_capabilities": ["*"],
                    "disabled_capabilities": [],
                },
            },
        },
    )
    resolver = DataRegistryBifCapabilityResolver(
        policy_resolution_service=_FakePolicyResolutionService(tenant_policy=tenant_policy),
    )
    with pytest.raises(InvalidCapabilityPolicyError):
        resolver.resolve(tenant_id="tenant-a", roles=("reader",))


def test_unknown_capability_rejected() -> None:
    tenant_policy = ResolvedPolicy(
        source="tenant_default",
        policy_key="resource_registry_bif_capabilities_default",
        tenant_id="tenant-a",
        dataset_id=None,
        revision=5,
        policy_id="p-4",
        rules_json={
            "enabled_capabilities": ["resource_registry.unknown.capability"],
            "disabled_capabilities": [],
            "role_overrides": {},
        },
    )
    resolver = DataRegistryBifCapabilityResolver(
        policy_resolution_service=_FakePolicyResolutionService(tenant_policy=tenant_policy),
    )
    with pytest.raises(InvalidCapabilityPolicyError):
        resolver.resolve(tenant_id="tenant-a", roles=("reader",))


def test_invalid_policy_shape_rejected() -> None:
    tenant_policy = ResolvedPolicy(
        source="tenant_default",
        policy_key="resource_registry_bif_capabilities_default",
        tenant_id="tenant-a",
        dataset_id=None,
        revision=6,
        policy_id="p-5",
        rules_json={
            "enabled_capabilities": "not-a-list",
            "disabled_capabilities": [],
            "role_overrides": {},
        },
    )
    resolver = DataRegistryBifCapabilityResolver(
        policy_resolution_service=_FakePolicyResolutionService(tenant_policy=tenant_policy),
    )
    with pytest.raises(InvalidCapabilityPolicyError):
        resolver.resolve(tenant_id="tenant-a", roles=("reader",))


def test_session_disabled_overrides_persistent_enabled() -> None:
    resolver = DataRegistryBifCapabilityResolver(
        policy_resolution_service=_FakePolicyResolutionService(),
    )
    resolved = resolver.resolve(
        tenant_id="tenant-a",
        roles=("reader",),
        session_disabled_capabilities=frozenset({"resource_registry.dataset.read"}),
    )
    assert "resource_registry.dataset.read" in resolved.disabled_capabilities
    assert "resource_registry.dataset.read" not in resolved.enabled_capabilities


def test_session_enabled_adds_capability() -> None:
    resolver = DataRegistryBifCapabilityResolver(
        policy_resolution_service=_FakePolicyResolutionService(),
    )
    resolved = resolver.resolve(
        tenant_id="tenant-a",
        roles=("reader",),
        session_enabled_capabilities=frozenset({"resource_registry.external_jobs.read"}),
    )
    assert "resource_registry.external_jobs.read" in resolved.enabled_capabilities


def test_dataset_override_merges_when_dataset_provided(monkeypatch) -> None:
    dataset_policy = ResolvedPolicy(
        source="dataset_override",
        policy_key="resource_registry_bif_capabilities_default",
        tenant_id="tenant-a",
        dataset_id="d-1",
        revision=9,
        policy_id="p-9",
        rules_json={
            "enabled_capabilities": [],
            "disabled_capabilities": ["resource_registry.dataset.context.read"],
            "role_overrides": {},
        },
    )
    resolver = DataRegistryBifCapabilityResolver(
        policy_resolution_service=_FakePolicyResolutionService(dataset_policy=dataset_policy),
    )
    monkeypatch.setattr(
        DataRegistryBifCapabilityResolver,
        "_get_tenant_dataset",
        staticmethod(lambda **kwargs: _DatasetStub(id="d-1", tenant_id=kwargs["tenant_id"])),
    )

    resolved = resolver.resolve(
        tenant_id="tenant-a",
        dataset_id="d-1",
        roles=("reader",),
    )
    assert resolved.policy_source == "dataset_override"
    assert "resource_registry.dataset.context.read" in resolved.disabled_capabilities


def test_capability_resolved_event_emitted() -> None:
    publisher = InMemoryEventPublisher()
    resolver = DataRegistryBifCapabilityResolver(
        policy_resolution_service=_FakePolicyResolutionService(),
        audit_service=AuditService(publisher),
    )

    resolver.resolve(tenant_id="tenant-a", actor="alice", roles=("reader",))

    events = publisher.list_events(event_type=EventType.BIF_CAPABILITIES_RESOLVED)
    assert len(events) == 1
    assert events[0].metadata["policy_source"] == "system_default"
