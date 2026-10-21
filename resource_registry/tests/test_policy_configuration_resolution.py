from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from resource_registry.events.audit_service import AuditService
from resource_registry.events.event_publisher import InMemoryEventPublisher
from resource_registry.events.event_types import EventType
from resource_registry.policies.configuration import PolicyConfigurationService
from resource_registry.policies.exceptions import InvalidPolicyConfigurationError, PolicyEvaluationError
from resource_registry.policies.provenance import PolicySource
from resource_registry.policies.resolution import PolicyResolutionService

REVISION_THREE = 3
REVISION_TWO = 2
REVISION_FOUR = 4
MAX_VERSIONS_OVERRIDE = 2
MAX_VERSIONS_SYSTEM_DEFAULT = 10


def _policy(*, tenant_id: str = "tenant-a", key: str = "retention_default", revision: int = 1):
    policy = SimpleNamespace(
        id=f"policy-{revision}",
        pk=f"policy-{revision}",
        tenant_id=tenant_id,
        policy_key=key,
        revision=revision,
        policy_type="RETENTION",
        rules_json={"max_versions": 5, "retain_latest_version": True},
        is_tenant_default=True,
        is_active=False,
        description="policy",
        save=MagicMock(),
    )
    return policy


def test_create_policy() -> None:
    service = PolicyConfigurationService()
    created = _policy()
    filter_qs = MagicMock()
    filter_qs.exists.return_value = False
    with (
        patch(
            "resource_registry.policies.configuration.GovernancePolicy.objects.filter",
            return_value=filter_qs,
        ),
        patch(
            "resource_registry.policies.configuration.GovernancePolicy.objects.create",
            return_value=created,
        ),
    ):
        result = service.create_policy(
            tenant_id="tenant-a",
            policy_key="retention_default",
            policy_type="RETENTION",
            rules_json={"max_versions": 5},
            is_active=False,
        )
    assert result == created


def test_create_policy_revision_and_history_available() -> None:
    service = PolicyConfigurationService()
    base = _policy(revision=1)
    latest = _policy(revision=2)
    created = _policy(revision=3)
    filter_qs = MagicMock()
    filter_qs.order_by.return_value.first.return_value = latest
    with (
        patch(
            "resource_registry.policies.configuration.GovernancePolicy.objects.filter",
            return_value=filter_qs,
        ),
        patch(
            "resource_registry.policies.configuration.GovernancePolicy.objects.create",
            return_value=created,
        ) as mocked_create,
    ):
        result = service.create_policy_revision(
            base_policy=base,
            rules_json={"max_versions": 3},
            activate=False,
        )
    assert result.revision == REVISION_THREE
    assert base.revision == 1
    assert mocked_create.call_args.kwargs["revision"] == REVISION_THREE


def test_activate_revision_only_one_active() -> None:
    service = PolicyConfigurationService()
    policy = _policy(revision=3)
    filter_qs = MagicMock()
    active_scope = MagicMock()
    excluded_qs = MagicMock()
    filter_qs.filter.return_value = active_scope
    active_scope.exclude.return_value = excluded_qs
    with patch(
        "resource_registry.policies.configuration.GovernancePolicy.objects.filter",
        return_value=filter_qs,
    ):
        service.activate_policy(policy=policy)
    excluded_qs.update.assert_called_once_with(is_active=False)
    assert policy.is_active is True


def test_deactivate_revision() -> None:
    service = PolicyConfigurationService()
    policy = _policy()
    policy.is_active = True
    service.deactivate_policy(policy=policy)
    assert policy.is_active is False
    policy.save.assert_called_once()


def test_invalid_revision_activation_rejected() -> None:
    service = PolicyConfigurationService()
    policy = _policy()
    policy.pk = None
    with pytest.raises(InvalidPolicyConfigurationError):
        service.activate_policy(policy=policy)


def test_assign_and_remove_dataset_policy() -> None:
    service = PolicyConfigurationService()
    dataset = SimpleNamespace(id="dataset-1", tenant_id="tenant-a")
    policy = _policy()
    assignment = SimpleNamespace(id="assign-1")
    with patch(
        "resource_registry.policies.configuration.DatasetPolicyAssignment.objects.update_or_create",
        return_value=(assignment, True),
    ) as mocked_update:
        result = service.assign_policy_to_dataset(dataset=dataset, policy=policy)
    assert result == assignment
    mocked_update.assert_called_once()

    delete_qs = MagicMock()
    delete_qs.delete.return_value = (1, {})
    with patch(
        "resource_registry.policies.configuration.DatasetPolicyAssignment.objects.filter",
        return_value=delete_qs,
    ):
        removed = service.remove_policy_from_dataset(dataset=dataset, policy=policy)
    assert removed is True


def test_cross_tenant_assignment_rejected() -> None:
    service = PolicyConfigurationService()
    dataset = SimpleNamespace(id="dataset-1", tenant_id="tenant-a")
    policy = _policy(tenant_id="tenant-b")
    with pytest.raises(PolicyEvaluationError):
        service.assign_policy_to_dataset(dataset=dataset, policy=policy)


def test_resolution_dataset_override_wins() -> None:
    publisher = InMemoryEventPublisher()
    service = PolicyResolutionService(audit_service=AuditService(publisher=publisher))
    dataset = SimpleNamespace(id="dataset-1", tenant_id="tenant-a")
    override_policy = _policy(revision=4)
    with (
        patch.object(service, "resolve_dataset_policy", return_value=SimpleNamespace(
            source="dataset_override",
            policy_key="retention_default",
            tenant_id="tenant-a",
            dataset_id="dataset-1",
            revision=4,
            policy_id=str(override_policy.id),
            rules_json={"max_versions": 2},
        )),
        patch.object(service, "resolve_tenant_policy", return_value=_policy(revision=2)),
    ):
        resolved = service.resolve_effective_policy(dataset=dataset, policy_key="retention_default")
    assert resolved.source == "dataset_override"
    assert resolved.rules_json["max_versions"] == MAX_VERSIONS_OVERRIDE
    event_types = [event.event_type for event in publisher.list_events()]
    assert EventType.POLICY_RESOLVED in event_types


def test_resolution_tenant_default_when_override_absent() -> None:
    service = PolicyResolutionService()
    dataset = SimpleNamespace(id="dataset-1", tenant_id="tenant-a")
    tenant_policy = _policy(revision=2)
    with (
        patch.object(service, "resolve_dataset_policy", return_value=None),
        patch.object(service, "resolve_tenant_policy", return_value=SimpleNamespace(
            source="tenant_default",
            policy_key="retention_default",
            tenant_id="tenant-a",
            dataset_id=None,
            revision=2,
            policy_id=str(tenant_policy.id),
            rules_json={"max_versions": 7},
        )),
    ):
        resolved = service.resolve_effective_policy(dataset=dataset, policy_key="retention_default")
    assert resolved.source == "tenant_default"
    assert resolved.revision == REVISION_TWO


def test_resolution_system_default_when_nothing_configured() -> None:
    service = PolicyResolutionService()
    dataset = SimpleNamespace(id="dataset-1", tenant_id="tenant-a")
    with (
        patch.object(service, "resolve_dataset_policy", return_value=None),
        patch.object(service, "resolve_tenant_policy", return_value=None),
    ):
        resolved = service.resolve_effective_policy(dataset=dataset, policy_key="retention_default")
    assert resolved.source == "system_default"
    assert resolved.rules_json["max_versions"] == MAX_VERSIONS_SYSTEM_DEFAULT


def test_resolution_is_deterministic() -> None:
    service = PolicyResolutionService()
    dataset = SimpleNamespace(id="dataset-1", tenant_id="tenant-a")
    with (
        patch.object(service, "resolve_dataset_policy", return_value=None),
        patch.object(service, "resolve_tenant_policy", return_value=None),
    ):
        first = service.resolve_effective_policy(dataset=dataset, policy_key="retention_default")
        second = service.resolve_effective_policy(dataset=dataset, policy_key="retention_default")
    assert first.rules_json == second.rules_json


def test_resolution_with_provenance_system_default() -> None:
    service = PolicyResolutionService()
    dataset = SimpleNamespace(id="dataset-1", tenant_id="tenant-a")
    with (
        patch.object(service, "resolve_dataset_policy", return_value=None),
        patch.object(service, "resolve_tenant_policy", return_value=None),
    ):
        resolved = service.resolve_effective_policy_with_provenance(
            dataset=dataset,
            policy_key="retention_default",
        )
    assert resolved.provenance.source == PolicySource.SYSTEM_DEFAULT
    assert resolved.provenance.revision is None
    assert resolved.provenance.resolution_path == [
        "dataset_override",
        "tenant_default",
        "system_default",
    ]
    assert resolved.provenance.rules_hash


def test_resolution_with_provenance_tenant_default() -> None:
    service = PolicyResolutionService()
    dataset = SimpleNamespace(id="dataset-1", tenant_id="tenant-a")
    with (
        patch.object(service, "resolve_dataset_policy", return_value=None),
        patch.object(service, "resolve_tenant_policy", return_value=SimpleNamespace(
            source="tenant_default",
            policy_key="retention_default",
            tenant_id="tenant-a",
            dataset_id=None,
            revision=2,
            policy_id="policy-2",
            rules_json={"max_versions": 7},
        )),
    ):
        resolved = service.resolve_effective_policy_with_provenance(
            dataset=dataset,
            policy_key="retention_default",
        )
    assert resolved.provenance.source == PolicySource.TENANT_DEFAULT
    assert resolved.provenance.revision == REVISION_TWO


def test_resolution_with_provenance_dataset_override() -> None:
    service = PolicyResolutionService()
    dataset = SimpleNamespace(id="dataset-1", tenant_id="tenant-a")
    with patch.object(service, "resolve_dataset_policy", return_value=SimpleNamespace(
        source="dataset_override",
        policy_key="retention_default",
        tenant_id="tenant-a",
        dataset_id="dataset-1",
        revision=4,
        policy_id="policy-4",
        rules_json={"max_versions": 2},
    )):
        resolved = service.resolve_effective_policy_with_provenance(
            dataset=dataset,
            policy_key="retention_default",
        )
    assert resolved.provenance.source == PolicySource.DATASET_OVERRIDE
    assert resolved.provenance.revision == REVISION_FOUR


def test_backward_compatible_resolution_methods_still_work() -> None:
    service = PolicyResolutionService()
    dataset = SimpleNamespace(id="dataset-1", tenant_id="tenant-a")
    with (
        patch.object(service, "resolve_dataset_policy", return_value=None),
        patch.object(service, "resolve_tenant_policy", return_value=None),
    ):
        legacy = service.resolve_effective_policy(dataset=dataset, policy_key="retention_default")
    assert legacy.source == "system_default"
