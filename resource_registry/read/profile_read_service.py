from __future__ import annotations

from resource_registry.dataset_visibility import build_dataset_visibility_filter
from resource_registry.models import DatasetProfile, DatasetStatus
from resource_registry.read.current_version import current_version_filter
from resource_registry.read.dtos import DatasetProfileSummary, sanitize_json, to_iso
from resource_registry.read.exceptions import DatasetVersionNotFoundError


class DatasetProfileReadService:
    """Tenant-scoped profile metadata read service."""

    def get_profile_for_version(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        version_id: str,
    ) -> DatasetProfileSummary:
        profile = (
            DatasetProfile.objects.filter(
                build_dataset_visibility_filter(
                    tenant_id=tenant_id,
                    field_prefix="dataset_version__dataset__",
                ),
                dataset_version__dataset_id=dataset_id,
                dataset_version__status="READY",
                dataset_version_id=version_id,
                dataset_version__dataset__archived_at__isnull=True,
                dataset_version__dataset__status__in=[DatasetStatus.DRAFT, DatasetStatus.ACTIVE],
            )
            .order_by("-computed_at", "-id")
            .first()
        )
        if profile is None:
            raise DatasetVersionNotFoundError("Profile not found for dataset version.")
        return self._to_summary(profile)

    def get_latest_profile(self, *, tenant_id: str, dataset_id: str) -> DatasetProfileSummary:
        profile = (
            DatasetProfile.objects.filter(
                current_version_filter(version_prefix="dataset_version__"),
                build_dataset_visibility_filter(
                    tenant_id=tenant_id,
                    field_prefix="dataset_version__dataset__",
                ),
                dataset_version__dataset_id=dataset_id,
                dataset_version__status="READY",
                dataset_version__dataset__archived_at__isnull=True,
                dataset_version__dataset__status__in=[DatasetStatus.DRAFT, DatasetStatus.ACTIVE],
            )
            .order_by("-dataset_version__version_number", "-computed_at", "-id")
            .first()
        )
        if profile is None:
            raise DatasetVersionNotFoundError("No profile found for dataset.")
        return self._to_summary(profile)

    def _to_summary(self, profile: DatasetProfile) -> DatasetProfileSummary:
        sanitized_metrics = sanitize_json(dict(profile.metrics_json or {}))
        return DatasetProfileSummary(
            id=str(profile.id),
            dataset_version_id=str(profile.dataset_version_id),
            tenant_id=str(profile.dataset_version.dataset.tenant_id),
            profile_type=profile.profile_type,
            profile_hash=profile.profile_hash,
            metrics_json=sanitized_metrics if isinstance(sanitized_metrics, dict) else {},
            computed_at=str(to_iso(profile.computed_at)),
        )
