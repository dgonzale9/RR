from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from resource_registry.events.audit_service import AuditService
from resource_registry.events.event_publisher import InMemoryEventPublisher
from resource_registry.events.event_types import EventType
from resource_registry.policies.evaluation import PolicyExecutionService
from resource_registry.policies.exceptions import (
    InvalidPolicyConfigurationError,
    PolicyEvaluationError,
    UnsupportedPolicyError,
)
from resource_registry.policies.lifecycle import LifecyclePolicyEvaluator
from resource_registry.policies.models import (
    ArtifactSnapshot,
    DatasetVersionSnapshot,
    LifecycleAction,
    LifecycleActionType,
    RetentionEvaluationResult,
)
from resource_registry.policies.retention import RetentionPolicyEvaluator

TOTAL_FOUR_VERSIONS = 4


def _version(version_id: str, number: int, days_ago: int) -> DatasetVersionSnapshot:
    return DatasetVersionSnapshot(
        dataset_version_id=version_id,
        version_number=number,
        created_at=datetime.now(UTC) - timedelta(days=days_ago),
    )


def test_retention_keep_latest_n_versions() -> None:
    evaluator = RetentionPolicyEvaluator()
    versions = [
        _version("v1", 1, 30),
        _version("v2", 2, 20),
        _version("v3", 3, 10),
        _version("v4", 4, 0),
    ]

    result = evaluator.evaluate(
        tenant_id="tenant-a",
        dataset_id="dataset-1",
        versions=versions,
        max_versions=2,
    )

    assert result.total_versions == TOTAL_FOUR_VERSIONS
    assert result.versions_to_keep == ["v4", "v3"]
    assert result.versions_to_deprecate == ["v2", "v1"]


def test_retention_age_based_evaluation() -> None:
    evaluator = RetentionPolicyEvaluator()
    versions = [
        _version("v1", 1, 120),
        _version("v2", 2, 5),
    ]

    result = evaluator.evaluate(
        tenant_id="tenant-a",
        dataset_id="dataset-1",
        versions=versions,
        max_age_days=90,
    )

    assert result.versions_to_deprecate == ["v1"]


def test_retention_preserves_latest_version() -> None:
    evaluator = RetentionPolicyEvaluator()
    versions = [_version("v1", 1, 100)]

    result = evaluator.evaluate(
        tenant_id="tenant-a",
        dataset_id="dataset-1",
        versions=versions,
        max_age_days=30,
        retain_latest_version=True,
    )

    assert result.versions_to_keep == ["v1"]
    assert result.versions_to_deprecate == []


def test_retention_empty_dataset() -> None:
    evaluator = RetentionPolicyEvaluator()

    result = evaluator.evaluate(
        tenant_id="tenant-a",
        dataset_id="dataset-1",
        versions=[],
        max_versions=2,
    )

    assert result.total_versions == 0
    assert result.actions == []


def test_retention_invalid_configuration() -> None:
    evaluator = RetentionPolicyEvaluator()
    with pytest.raises(InvalidPolicyConfigurationError):
        evaluator.evaluate(
            tenant_id="tenant-a",
            dataset_id="dataset-1",
            versions=[],
            max_versions=0,
        )


def test_lifecycle_deprecate_recommendation() -> None:
    evaluator = LifecyclePolicyEvaluator()
    result = RetentionEvaluationResult(
        tenant_id="tenant-a",
        dataset_id="dataset-1",
        evaluated_at=datetime.now(UTC),
        total_versions=2,
        versions_to_deprecate=["v1"],
    )
    actions = evaluator.evaluate(
        retention_result=result,
        artifacts_by_version={
            "v1": [ArtifactSnapshot(artifact_id="a1", dataset_version_id="v1")],
        },
        action_type=LifecycleActionType.DEPRECATE,
    )
    assert actions[0].action_type == LifecycleActionType.DEPRECATE


def test_lifecycle_delete_reference_recommendation() -> None:
    evaluator = LifecyclePolicyEvaluator()
    result = RetentionEvaluationResult(
        tenant_id="tenant-a",
        dataset_id="dataset-1",
        evaluated_at=datetime.now(UTC),
        total_versions=2,
        versions_to_deprecate=["v1"],
    )
    actions = evaluator.evaluate(
        retention_result=result,
        artifacts_by_version={
            "v1": [ArtifactSnapshot(artifact_id="a1", dataset_version_id="v1")],
        },
        action_type=LifecycleActionType.DELETE_REFERENCE,
    )
    assert actions[0].action_type == LifecycleActionType.DELETE_REFERENCE


def test_lifecycle_delete_physical_recommendation() -> None:
    evaluator = LifecyclePolicyEvaluator()
    result = RetentionEvaluationResult(
        tenant_id="tenant-a",
        dataset_id="dataset-1",
        evaluated_at=datetime.now(UTC),
        total_versions=2,
        versions_to_deprecate=["v1"],
    )
    actions = evaluator.evaluate(
        retention_result=result,
        artifacts_by_version={
            "v1": [ArtifactSnapshot(artifact_id="a1", dataset_version_id="v1")],
        },
        action_type=LifecycleActionType.DELETE_PHYSICAL,
    )
    assert actions[0].action_type == LifecycleActionType.DELETE_PHYSICAL


def test_lifecycle_unsupported_policy() -> None:
    evaluator = LifecyclePolicyEvaluator()
    with pytest.raises(UnsupportedPolicyError):
        evaluator.evaluate(
            retention_result=RetentionEvaluationResult(
                tenant_id="tenant-a",
                dataset_id="dataset-1",
                evaluated_at=datetime.now(UTC),
                total_versions=0,
            ),
            artifacts_by_version={},
            action_type="SUSPEND",
        )


def test_policy_execution_dry_run_performs_no_writes() -> None:
    artifact_lifecycle_service = MagicMock()
    service = PolicyExecutionService(artifact_lifecycle_service=artifact_lifecycle_service)
    dataset = SimpleNamespace(id="dataset-1", tenant_id="tenant-a")
    result = RetentionEvaluationResult(
        tenant_id="tenant-a",
        dataset_id="dataset-1",
        evaluated_at=datetime.now(UTC),
        total_versions=1,
        actions=[
            LifecycleAction(
                action_type=LifecycleActionType.DEPRECATE,
                artifact_id="a1",
                dataset_version_id="v1",
                reason="retention_policy",
            ),
        ],
    )

    executed = service.apply(dataset=dataset, evaluation_result=result, dry_run=True)

    assert executed == []
    artifact_lifecycle_service.deprecate_artifact.assert_not_called()


def test_policy_execution_evaluate_emits_audit_event() -> None:
    publisher = InMemoryEventPublisher()
    audit_service = AuditService(publisher=publisher)
    service = PolicyExecutionService(audit_service=audit_service)
    version = SimpleNamespace(
        id="v1",
        version_number=1,
        created_at=datetime.now(UTC) - timedelta(days=1),
        artifacts=SimpleNamespace(all=lambda: [SimpleNamespace(id="a1", storage_provider="local")]),
    )
    dataset = SimpleNamespace(
        id="dataset-1",
        tenant_id="tenant-a",
        versions=SimpleNamespace(all=lambda: [version]),
    )

    result = service.evaluate(
        dataset=dataset,
        max_versions=1,
        action_type=LifecycleActionType.DEPRECATE,
        dry_run=True,
    )

    assert result.total_versions == 1
    event_types = [event.event_type for event in publisher.list_events()]
    assert EventType.RETENTION_POLICY_EVALUATED in event_types


def test_policy_execution_evaluate_accepts_resolved_policy() -> None:
    service = PolicyExecutionService()
    version = SimpleNamespace(
        id="v1",
        version_number=1,
        created_at=datetime.now(UTC) - timedelta(days=1),
        artifacts=SimpleNamespace(all=lambda: []),
    )
    dataset = SimpleNamespace(
        id="dataset-1",
        tenant_id="tenant-a",
        versions=SimpleNamespace(all=lambda: [version]),
    )
    resolved_policy = SimpleNamespace(
        source="system_default",
        revision=None,
        rules_json={
            "max_versions": 1,
            "retain_latest_version": True,
            "default_action_type": "DEPRECATE",
        },
    )

    result = service.evaluate(
        dataset=dataset,
        resolved_policy=resolved_policy,
        dry_run=True,
    )

    assert result.total_versions == 1
    assert result.versions_to_keep == ["v1"]


def test_policy_execution_apply_executes_actions_and_emits_audit() -> None:
    publisher = InMemoryEventPublisher()
    audit_service = AuditService(publisher=publisher)
    artifact_lifecycle_service = MagicMock()
    service = PolicyExecutionService(
        artifact_lifecycle_service=artifact_lifecycle_service,
        audit_service=audit_service,
    )
    dataset = SimpleNamespace(id="dataset-1", tenant_id="tenant-a")
    artifact = SimpleNamespace(
        id="a1",
        storage_provider="local",
        dataset_version=SimpleNamespace(
            id="v1",
            dataset=SimpleNamespace(tenant_id="tenant-a"),
        ),
    )
    result = RetentionEvaluationResult(
        tenant_id="tenant-a",
        dataset_id="dataset-1",
        evaluated_at=datetime.now(UTC),
        total_versions=1,
        actions=[
            LifecycleAction(
                action_type=LifecycleActionType.DEPRECATE,
                artifact_id="a1",
                dataset_version_id="v1",
                reason="retention_policy",
            ),
        ],
    )
    with (
        patch(
            "resource_registry.policies.evaluation.DatasetArtifact.objects.select_related",
        ) as mocked_select_related,
        patch("resource_registry.policies.evaluation.transaction.atomic", return_value=nullcontext()),
    ):
        mocked_select_related.return_value.get.return_value = artifact
        executed = service.apply(dataset=dataset, evaluation_result=result, dry_run=False)

    assert len(executed) == 1
    artifact_lifecycle_service.deprecate_artifact.assert_called_once()
    event_types = [event.event_type for event in publisher.list_events()]
    assert EventType.ARTIFACT_RETENTION_ACTION_EXECUTED in event_types
    assert EventType.RETENTION_POLICY_APPLIED in event_types


def test_policy_execution_tenant_boundary_preserved() -> None:
    service = PolicyExecutionService(artifact_lifecycle_service=MagicMock())
    dataset = SimpleNamespace(id="dataset-1", tenant_id="tenant-a")
    artifact = SimpleNamespace(
        id="a1",
        storage_provider="local",
        dataset_version=SimpleNamespace(
            id="v1",
            dataset=SimpleNamespace(tenant_id="tenant-b"),
        ),
    )
    result = RetentionEvaluationResult(
        tenant_id="tenant-a",
        dataset_id="dataset-1",
        evaluated_at=datetime.now(UTC),
        total_versions=1,
        actions=[
            LifecycleAction(
                action_type=LifecycleActionType.DEPRECATE,
                artifact_id="a1",
                dataset_version_id="v1",
                reason="retention_policy",
            ),
        ],
    )
    with (
        patch(
            "resource_registry.policies.evaluation.DatasetArtifact.objects.select_related",
        ) as mocked_select_related,
        patch("resource_registry.policies.evaluation.transaction.atomic", return_value=nullcontext()),
    ):
        mocked_select_related.return_value.get.return_value = artifact
        with pytest.raises(PolicyEvaluationError):
            service.apply(dataset=dataset, evaluation_result=result, dry_run=False)


def test_dataset_with_one_version_only() -> None:
    evaluator = RetentionPolicyEvaluator()
    result = evaluator.evaluate(
        tenant_id="tenant-a",
        dataset_id="dataset-1",
        versions=[_version("v1", 1, 1)],
        max_versions=1,
    )

    assert result.versions_to_keep == ["v1"]
    assert result.versions_to_deprecate == []
