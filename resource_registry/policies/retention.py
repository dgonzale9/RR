from __future__ import annotations

from datetime import datetime, timedelta

from resource_registry.policies.exceptions import InvalidPolicyConfigurationError
from resource_registry.policies.models import DatasetVersionSnapshot, RetentionEvaluationResult, now_utc


class RetentionPolicyEvaluator:
    """Evaluates retention policy recommendations without side effects."""

    def evaluate(  # noqa: PLR0913
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        versions: list[DatasetVersionSnapshot],
        max_versions: int | None = None,
        max_age_days: int | None = None,
        retain_latest_version: bool = True,
        evaluated_at: datetime | None = None,
    ) -> RetentionEvaluationResult:
        self._validate_policy(
            max_versions=max_versions,
            max_age_days=max_age_days,
            retain_latest_version=retain_latest_version,
        )
        now = evaluated_at or now_utc()
        ordered = sorted(
            versions,
            key=lambda version: (version.version_number, version.created_at),
            reverse=True,
        )
        if not ordered:
            return RetentionEvaluationResult(
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                evaluated_at=now,
                total_versions=0,
            )

        keep_ids: set[str] = set()
        latest_version_id = ordered[0].dataset_version_id
        if retain_latest_version:
            keep_ids.add(latest_version_id)
        if max_versions is not None:
            keep_ids.update(version.dataset_version_id for version in ordered[:max_versions])

        count_candidates: set[str] = set()
        if max_versions is not None and len(ordered) > max_versions:
            count_candidates = {
                version.dataset_version_id for version in ordered[max_versions:]
            }

        age_candidates: set[str] = set()
        if max_age_days is not None:
            max_age_cutoff = now - timedelta(days=max_age_days)
            age_candidates = {
                version.dataset_version_id
                for version in ordered
                if version.created_at < max_age_cutoff
            }

        deprecate_candidates = (count_candidates | age_candidates) - keep_ids
        keep_list = [
            version.dataset_version_id
            for version in ordered
            if version.dataset_version_id in keep_ids
        ]
        deprecate_list = [
            version.dataset_version_id
            for version in ordered
            if version.dataset_version_id in deprecate_candidates
        ]
        return RetentionEvaluationResult(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            evaluated_at=now,
            total_versions=len(ordered),
            versions_to_keep=keep_list,
            versions_to_deprecate=deprecate_list,
            versions_to_delete_reference=[],
            versions_to_delete_physical=[],
            actions=[],
        )

    def _validate_policy(
        self,
        *,
        max_versions: int | None,
        max_age_days: int | None,
        retain_latest_version: bool,
    ) -> None:
        if max_versions is not None and max_versions <= 0:
            raise InvalidPolicyConfigurationError("max_versions must be greater than 0.")
        if max_age_days is not None and max_age_days < 0:
            raise InvalidPolicyConfigurationError("max_age_days must be zero or greater.")
        if not retain_latest_version and max_versions is None and max_age_days is None:
            raise InvalidPolicyConfigurationError(
                "At least one retention safeguard/rule must be configured.",
            )
