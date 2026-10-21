from __future__ import annotations

from resource_registry.dataset_visibility import build_dataset_visibility_filter
from resource_registry.models import DatasetSchema, DatasetStatus
from resource_registry.read.dtos import DatasetSchemaSummary, sanitize_json, to_iso
from resource_registry.read.exceptions import DatasetVersionNotFoundError


class DatasetSchemaReadService:
    """Tenant-scoped schema metadata read service."""

    def get_schema_for_version(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        version_id: str,
    ) -> DatasetSchemaSummary:
        schema = DatasetSchema.objects.filter(
            build_dataset_visibility_filter(
                tenant_id=tenant_id,
                field_prefix="dataset_version__dataset__",
            ),
            dataset_version__dataset_id=dataset_id,
            dataset_version_id=version_id,
            dataset_version__dataset__archived_at__isnull=True,
            dataset_version__dataset__status__in=[DatasetStatus.DRAFT, DatasetStatus.ACTIVE],
        ).order_by("-created_at", "-id").first()
        if schema is None:
            raise DatasetVersionNotFoundError("Schema not found for dataset version.")
        return self._to_summary(schema)

    def get_latest_schema(self, *, tenant_id: str, dataset_id: str) -> DatasetSchemaSummary:
        schema = DatasetSchema.objects.filter(
            build_dataset_visibility_filter(
                tenant_id=tenant_id,
                field_prefix="dataset_version__dataset__",
            ),
            dataset_version__dataset_id=dataset_id,
            dataset_version__dataset__archived_at__isnull=True,
            dataset_version__dataset__status__in=[DatasetStatus.DRAFT, DatasetStatus.ACTIVE],
        ).order_by("-dataset_version__version_number", "-created_at", "-id").first()
        if schema is None:
            raise DatasetVersionNotFoundError("No schema found for dataset.")
        return self._to_summary(schema)

    def _to_summary(self, schema: DatasetSchema) -> DatasetSchemaSummary:
        sanitized = sanitize_json(list(schema.schema_json or []))
        return DatasetSchemaSummary(
            id=str(schema.id),
            dataset_version_id=str(schema.dataset_version_id),
            tenant_id=str(schema.dataset_version.dataset.tenant_id),
            schema_hash=schema.schema_hash,
            column_count=int(schema.column_count),
            schema_json=sanitized if isinstance(sanitized, list) else [],
            created_at=str(to_iso(schema.created_at)),
        )
