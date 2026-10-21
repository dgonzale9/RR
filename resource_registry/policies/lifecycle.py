from __future__ import annotations

from resource_registry.policies.exceptions import UnsupportedPolicyError
from resource_registry.policies.models import (
    ArtifactSnapshot,
    LifecycleAction,
    LifecycleActionType,
    RetentionEvaluationResult,
)


class LifecyclePolicyEvaluator:
    """Builds artifact-level lifecycle action recommendations."""

    def evaluate(
        self,
        *,
        retention_result: RetentionEvaluationResult,
        artifacts_by_version: dict[str, list[ArtifactSnapshot]],
        action_type: str = LifecycleActionType.DEPRECATE,
        reason: str = "retention_policy",
    ) -> list[LifecycleAction]:
        normalized_action = self._normalize_action_type(action_type)
        target_versions = self._target_versions(
            retention_result=retention_result,
            action_type=normalized_action,
        )
        actions: list[LifecycleAction] = []
        for version_id in target_versions:
            for artifact in artifacts_by_version.get(version_id, []):
                actions.append(
                    LifecycleAction(
                        action_type=normalized_action.value,
                        artifact_id=artifact.artifact_id,
                        dataset_version_id=artifact.dataset_version_id,
                        reason=reason,
                        metadata={"storage_provider": artifact.storage_provider},
                    ),
                )
        return actions

    def _normalize_action_type(self, action_type: str) -> LifecycleActionType:
        value = str(action_type).strip().upper()
        try:
            return LifecycleActionType(value)
        except ValueError as error:
            raise UnsupportedPolicyError(
                f"Unsupported lifecycle action '{action_type}'.",
            ) from error

    def _target_versions(
        self,
        *,
        retention_result: RetentionEvaluationResult,
        action_type: LifecycleActionType,
    ) -> list[str]:
        if action_type == LifecycleActionType.DEPRECATE:
            return list(retention_result.versions_to_deprecate)
        if action_type == LifecycleActionType.DELETE_REFERENCE:
            if retention_result.versions_to_delete_reference:
                return list(retention_result.versions_to_delete_reference)
            return list(retention_result.versions_to_deprecate)
        if action_type == LifecycleActionType.DELETE_PHYSICAL:
            if retention_result.versions_to_delete_physical:
                return list(retention_result.versions_to_delete_physical)
            return list(retention_result.versions_to_deprecate)
        raise UnsupportedPolicyError(f"Unsupported lifecycle action '{action_type.value}'.")
