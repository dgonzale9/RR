from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from django.db import transaction

from resource_registry.events.audit_service import AuditService
from resource_registry.models import Dataset, DatasetArtifact
from resource_registry.policies.exceptions import PolicyEvaluationError
from resource_registry.policies.lifecycle import LifecyclePolicyEvaluator
from resource_registry.policies.models import (
    ArtifactSnapshot,
    DatasetVersionSnapshot,
    LifecycleAction,
    LifecycleActionType,
    RetentionEvaluationResult,
)
from resource_registry.policies.retention import RetentionPolicyEvaluator
from resource_registry.services.registry.artifact_lifecycle_service import ArtifactLifecycleService
from resource_registry.storage.factory import DataRegistryStorageFactory


class PolicyExecutionService:
    """Evaluates and explicitly applies retention lifecycle actions."""

    def __init__(
        self,
        *,
        retention_evaluator: RetentionPolicyEvaluator | None = None,
        lifecycle_evaluator: LifecyclePolicyEvaluator | None = None,
        artifact_lifecycle_service: ArtifactLifecycleService | None = None,
        audit_service: AuditService | None = None,
        storage_factory: DataRegistryStorageFactory | None = None,
    ) -> None:
        self._retention_evaluator = retention_evaluator or RetentionPolicyEvaluator()
        self._lifecycle_evaluator = lifecycle_evaluator or LifecyclePolicyEvaluator()
        self._audit_service = audit_service or AuditService()
        self._artifact_lifecycle_service = artifact_lifecycle_service or ArtifactLifecycleService(
            audit_service=self._audit_service,
        )
        self._storage_factory = storage_factory or DataRegistryStorageFactory()

    def evaluate(  # noqa: PLR0913
        self,
        *,
        dataset: Dataset,
        max_versions: int | None = None,
        max_age_days: int | None = None,
        retain_latest_version: bool = True,
        action_type: str = LifecycleActionType.DEPRECATE,
        resolved_policy=None,
        evaluated_at: datetime | None = None,
        dry_run: bool = True,
    ) -> RetentionEvaluationResult:
        policy_rules = self._extract_policy_rules(resolved_policy=resolved_policy)
        if max_versions is None:
            max_versions = policy_rules.get("max_versions")
        if max_age_days is None:
            max_age_days = policy_rules.get("max_age_days")
        if "retain_latest_version" in policy_rules:
            retain_latest_version = bool(policy_rules["retain_latest_version"])
        if action_type == LifecycleActionType.DEPRECATE and "default_action_type" in policy_rules:
            action_type = str(policy_rules["default_action_type"])

        version_snapshots = self._build_version_snapshots(dataset=dataset)
        artifacts_by_version = self._build_artifact_map(dataset=dataset)
        retention_result = self._retention_evaluator.evaluate(
            tenant_id=str(dataset.tenant_id),
            dataset_id=str(dataset.id),
            versions=version_snapshots,
            max_versions=max_versions,
            max_age_days=max_age_days,
            retain_latest_version=retain_latest_version,
            evaluated_at=evaluated_at,
        )
        actions = self._lifecycle_evaluator.evaluate(
            retention_result=retention_result,
            artifacts_by_version=artifacts_by_version,
            action_type=action_type,
        )
        result = replace(retention_result, actions=actions)
        self._safe_audit(
            lambda: self._audit_service.record_retention_policy_evaluated(
                tenant_id=str(dataset.tenant_id),
                dataset_id=str(dataset.id),
                metadata={
                    "max_versions": max_versions,
                    "max_age_days": max_age_days,
                    "retain_latest_version": retain_latest_version,
                    "policy_source": getattr(resolved_policy, "source", None),
                    "policy_revision": getattr(resolved_policy, "revision", None),
                    "dry_run": dry_run,
                    "recommended_action_count": len(actions),
                },
            ),
        )
        return result

    def apply(
        self,
        *,
        dataset: Dataset,
        evaluation_result: RetentionEvaluationResult,
        dry_run: bool = True,
    ) -> list[LifecycleAction]:
        if dry_run:
            return []

        executed_actions: list[LifecycleAction] = []
        for action in evaluation_result.actions:
            artifact = DatasetArtifact.objects.select_related("dataset_version__dataset").get(
                id=action.artifact_id,
            )
            self._ensure_tenant_boundary(
                dataset=dataset,
                artifact=artifact,
                evaluation_tenant_id=evaluation_result.tenant_id,
            )
            with transaction.atomic():
                self._execute_action(artifact=artifact, action=action)
            executed_actions.append(action)
            dataset_version_id = str(artifact.dataset_version.id)
            artifact_id = str(artifact.id)
            action_type = action.action_type
            reason = action.reason

            def _record_action_audit(
                *,
                dataset_version_id: str = dataset_version_id,
                artifact_id: str = artifact_id,
                action_type: str = action_type,
                reason: str = reason,
            ):
                return self._audit_service.record_artifact_retention_action_executed(
                    tenant_id=str(dataset.tenant_id),
                    dataset_id=str(dataset.id),
                    dataset_version_id=dataset_version_id,
                    metadata={
                        "artifact_id": artifact_id,
                        "action_type": action_type,
                        "reason": reason,
                    },
                )

            self._safe_audit(
                _record_action_audit,
            )

        self._safe_audit(
            lambda: self._audit_service.record_retention_policy_applied(
                tenant_id=str(dataset.tenant_id),
                dataset_id=str(dataset.id),
                metadata={"executed_action_count": len(executed_actions)},
            ),
        )
        return executed_actions

    def _execute_action(self, *, artifact: DatasetArtifact, action: LifecycleAction) -> None:
        try:
            action_type = LifecycleActionType(action.action_type)
        except ValueError as error:
            raise PolicyEvaluationError(f"Unsupported action '{action.action_type}'.") from error
        if action_type == LifecycleActionType.DEPRECATE:
            self._artifact_lifecycle_service.deprecate_artifact(
                artifact=artifact,
                reason=action.reason,
            )
            return
        if action_type == LifecycleActionType.DELETE_REFERENCE:
            self._artifact_lifecycle_service.delete_artifact_reference(artifact=artifact)
            return
        if action_type == LifecycleActionType.DELETE_PHYSICAL:
            provider = self._resolve_storage_provider(artifact=artifact)
            self._artifact_lifecycle_service.delete_physical_artifact(
                artifact=artifact,
                storage_provider=provider,
            )
            return
        raise PolicyEvaluationError(f"Unsupported action '{action.action_type}'.")

    def _resolve_storage_provider(self, *, artifact: DatasetArtifact):
        provider_key = artifact.storage_provider
        if provider_key is None:
            raise PolicyEvaluationError(
                f"Artifact {artifact.id} does not define a storage provider.",
            )
        try:
            return self._storage_factory.create_provider(provider_key)
        except Exception as error:
            raise PolicyEvaluationError(
                f"Failed to resolve storage provider for artifact {artifact.id}.",
            ) from error

    def _build_version_snapshots(self, *, dataset: Dataset) -> list[DatasetVersionSnapshot]:
        versions = dataset.versions.all()
        return [
            DatasetVersionSnapshot(
                dataset_version_id=str(version.id),
                version_number=int(version.version_number),
                created_at=version.created_at,
            )
            for version in versions
        ]

    def _build_artifact_map(self, *, dataset: Dataset) -> dict[str, list[ArtifactSnapshot]]:
        artifacts_by_version: dict[str, list[ArtifactSnapshot]] = {}
        for version in dataset.versions.all():
            version_id = str(version.id)
            artifacts_by_version[version_id] = [
                ArtifactSnapshot(
                    artifact_id=str(artifact.id),
                    dataset_version_id=version_id,
                    storage_provider=artifact.storage_provider,
                )
                for artifact in version.artifacts.all()
            ]
        return artifacts_by_version

    def _ensure_tenant_boundary(
        self,
        *,
        dataset: Dataset,
        artifact: DatasetArtifact,
        evaluation_tenant_id: str,
    ) -> None:
        dataset_tenant_id = str(dataset.tenant_id)
        artifact_tenant_id = str(artifact.dataset_version.dataset.tenant_id)
        if dataset_tenant_id != evaluation_tenant_id or artifact_tenant_id != evaluation_tenant_id:
            raise PolicyEvaluationError("Tenant boundary violation during policy apply.")

    def _safe_audit(self, audit_call) -> None:
        try:
            audit_call()
        except Exception:
            return

    def _extract_policy_rules(self, *, resolved_policy) -> dict[str, object]:
        if resolved_policy is None:
            return {}
        rules_json = getattr(resolved_policy, "rules_json", None)
        if isinstance(rules_json, dict):
            return dict(rules_json)
        return {}
