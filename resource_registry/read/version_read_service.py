from __future__ import annotations

from resource_registry.dataset_visibility import build_dataset_visibility_filter
from resource_registry.models import DatasetStatus, DatasetVersion
from resource_registry.read.dtos import DatasetVersionSummary, sanitize_uri, to_iso
from resource_registry.read.exceptions import DatasetVersionNotFoundError


class DatasetVersionReadService:
    """Tenant-scoped metadata reads for dataset versions."""

    MAX_LIMIT = 100

    def list_versions(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[DatasetVersionSummary]:
        queryset = DatasetVersion.objects.filter(
            build_dataset_visibility_filter(tenant_id=tenant_id, field_prefix="dataset__"),
            dataset_id=dataset_id,
            dataset__archived_at__isnull=True,
            dataset__status__in=[DatasetStatus.DRAFT, DatasetStatus.ACTIVE],
        )
        ordered = queryset.order_by("-version_number", "-created_at", "-id")
        versions = self._paginate(list(ordered), limit=limit, offset=offset)
        return [self._to_summary(version) for version in versions]

    def get_latest_version(self, *, tenant_id: str, dataset_id: str) -> DatasetVersionSummary:
        version = (
            DatasetVersion.objects.filter(
                build_dataset_visibility_filter(tenant_id=tenant_id, field_prefix="dataset__"),
                dataset_id=dataset_id,
                dataset__archived_at__isnull=True,
                dataset__status__in=[DatasetStatus.DRAFT, DatasetStatus.ACTIVE],
            )
            .order_by("-version_number", "-created_at", "-id")
            .first()
        )
        if version is None:
            raise DatasetVersionNotFoundError("No versions found for dataset.")
        return self._to_summary(version)

    def get_version(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        version_id: str,
    ) -> DatasetVersionSummary:
        version = DatasetVersion.objects.filter(
            build_dataset_visibility_filter(tenant_id=tenant_id, field_prefix="dataset__"),
            dataset_id=dataset_id,
            id=version_id,
            dataset__archived_at__isnull=True,
            dataset__status__in=[DatasetStatus.DRAFT, DatasetStatus.ACTIVE],
        ).first()
        if version is None:
            raise DatasetVersionNotFoundError("Version not found for tenant dataset.")
        return self._to_summary(version)

    def _paginate(
        self,
        items: list[DatasetVersion],
        *,
        limit: int,
        offset: int,
    ) -> list[DatasetVersion]:
        safe_limit = min(max(limit, 1), self.MAX_LIMIT)
        safe_offset = max(offset, 0)
        return items[safe_offset : safe_offset + safe_limit]

    def _to_summary(self, version: DatasetVersion) -> DatasetVersionSummary:
        latest_profile = version.profiles.order_by("-computed_at").first()
        latest_schema = version.schemas.order_by("-created_at").first()
        row_count = None
        column_count = None
        if latest_profile is not None:
            row_count = latest_profile.metrics_json.get("row_count")
            column_count = latest_profile.metrics_json.get("column_count")
        if column_count is None and latest_schema is not None:
            column_count = latest_schema.column_count
        return DatasetVersionSummary(
            id=str(version.id),
            dataset_id=str(version.dataset_id),
            tenant_id=str(version.dataset.tenant_id),
            version_number=int(version.version_number),
            status=version.status,
            source_uri=sanitize_uri(str(version.source_uri)),
            row_count=row_count if isinstance(row_count, int) else None,
            column_count=column_count if isinstance(column_count, int) else None,
            created_at=str(to_iso(version.created_at)),
            content_hash=str(version.content_hash),
        )
