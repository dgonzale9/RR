from __future__ import annotations

from resource_registry.dataset_visibility import build_dataset_visibility_filter
from resource_registry.models import (
    Dataset,
    DatasetArtifact,
    DatasetProfile,
    DatasetSchema,
    DatasetStatus,
    DatasetVersion,
    WorkbookIngestionGroup,
)
from resource_registry.read.dtos import WorkbookContextSummary, WorkbookSheetContextSummary, to_iso
from resource_registry.read.exceptions import DataRegistryReadError


class WorkbookContextReadService:
    """Tenant-scoped read service for workbook-level context."""

    def get_workbook_context(
        self,
        *,
        tenant_id: str,
        workbook_group_id: str,
        allowed_dataset_ids: frozenset[str] | None = None,
    ) -> WorkbookContextSummary:
        """Return workbook context only when its visible Datasets are fully authorized.

        Args:
            tenant_id: Immutable Registry Namespace identifier.
            workbook_group_id: Exact workbook ingestion group identifier.
            allowed_dataset_ids: Optional exact Dataset scope for a non-admin reader.

        Returns:
            The workbook context and its aggregate metadata.

        Raises:
            DataRegistryReadError: If the group is absent, hidden, or only partially authorized.
        """
        group = WorkbookIngestionGroup.objects.filter(
            id=workbook_group_id,
            tenant_id=tenant_id,
        ).first()
        if group is None:
            raise DataRegistryReadError("Workbook ingestion group not found for tenant.")

        archived_dataset_exists = Dataset.objects.filter(
            build_dataset_visibility_filter(tenant_id=tenant_id),
            workbook_group_id=workbook_group_id,
            archived_at__isnull=False,
        ).exists()
        if archived_dataset_exists:
            raise DataRegistryReadError("Workbook ingestion group not found for tenant.")

        dataset_queryset = Dataset.objects.filter(
            build_dataset_visibility_filter(tenant_id=tenant_id),
            workbook_group_id=workbook_group_id,
            archived_at__isnull=True,
            status__in=[DatasetStatus.DRAFT, DatasetStatus.ACTIVE],
        )
        if allowed_dataset_ids is not None:
            if dataset_queryset.exclude(id__in=allowed_dataset_ids).exists():
                raise DataRegistryReadError("Workbook ingestion group not found for tenant.")
            dataset_queryset = dataset_queryset.filter(id__in=allowed_dataset_ids)
        datasets = list(dataset_queryset.order_by("slug", "id"))
        if not datasets:
            raise DataRegistryReadError("Workbook ingestion group not found for tenant.")
        dataset_ids = [dataset.id for dataset in datasets]
        latest_versions_by_dataset = self._latest_versions_by_dataset(dataset_ids)
        latest_version_ids = [version.id for version in latest_versions_by_dataset.values()]
        latest_profiles_by_version = self._latest_profiles_by_version(latest_version_ids)
        latest_schemas_by_version = self._latest_schemas_by_version(latest_version_ids)
        sheet_names_by_version = self._sheet_names_by_version(latest_version_ids)

        sheets: list[WorkbookSheetContextSummary] = []
        for dataset in datasets:
            latest_version = latest_versions_by_dataset.get(dataset.id)
            latest_profile = (
                latest_profiles_by_version.get(latest_version.id) if latest_version else None
            )
            latest_schema = (
                latest_schemas_by_version.get(latest_version.id) if latest_version else None
            )
            row_count = self._as_int(
                (latest_profile.metrics_json or {}).get("row_count") if latest_profile else None,
            )
            column_count = self._as_int(
                (latest_profile.metrics_json or {}).get("column_count")
                if latest_profile
                else None,
            )
            schema_columns = self._schema_column_names(latest_schema)
            sheet_name = (
                sheet_names_by_version.get(latest_version.id, None)
                if latest_version
                else None
            )
            sheets.append(
                WorkbookSheetContextSummary(
                    dataset_id=str(dataset.id),
                    slug=str(dataset.slug),
                    name=str(dataset.name),
                    sheet_name=sheet_name,
                    latest_version_id=(str(latest_version.id) if latest_version else None),
                    row_count=row_count,
                    column_count=column_count,
                    schema_columns=schema_columns,
                    tags=list(dataset.tags or []),
                ),
            )

        return WorkbookContextSummary(
            workbook_group_id=str(group.id),
            tenant_id=str(group.tenant_id),
            status=str(group.status),
            source_file_name=group.source_file_name,
            source_uri=str(group.source_uri),
            source_hash=str(group.source_hash),
            total_sheets=int(group.total_sheets),
            total_created=int(group.total_created),
            total_deduplicated=int(group.total_deduplicated),
            total_skipped=int(group.total_skipped),
            sheets=sheets,
            created_at=to_iso(group.created_at),
            updated_at=to_iso(group.updated_at),
        )

    def _latest_versions_by_dataset(self, dataset_ids: list[str]) -> dict[str, DatasetVersion]:
        if not dataset_ids:
            return {}
        versions = list(
            DatasetVersion.objects.filter(dataset_id__in=dataset_ids).order_by(
                "dataset_id",
                "-version_number",
                "-created_at",
            ),
        )
        latest: dict[str, DatasetVersion] = {}
        for version in versions:
            key = str(version.dataset_id)
            if key not in latest:
                latest[key] = version
        return latest

    def _latest_profiles_by_version(self, version_ids: list[str]) -> dict[str, DatasetProfile]:
        if not version_ids:
            return {}
        profiles = list(
            DatasetProfile.objects.filter(dataset_version_id__in=version_ids).order_by(
                "dataset_version_id",
                "-computed_at",
            ),
        )
        latest: dict[str, DatasetProfile] = {}
        for profile in profiles:
            key = str(profile.dataset_version_id)
            if key not in latest:
                latest[key] = profile
        return latest

    def _latest_schemas_by_version(self, version_ids: list[str]) -> dict[str, DatasetSchema]:
        if not version_ids:
            return {}
        schemas = list(
            DatasetSchema.objects.filter(dataset_version_id__in=version_ids).order_by(
                "dataset_version_id",
                "-created_at",
            ),
        )
        latest: dict[str, DatasetSchema] = {}
        for schema in schemas:
            key = str(schema.dataset_version_id)
            if key not in latest:
                latest[key] = schema
        return latest

    def _sheet_names_by_version(self, version_ids: list[str]) -> dict[str, str]:
        if not version_ids:
            return {}
        artifacts = list(
            DatasetArtifact.objects.filter(
                dataset_version_id__in=version_ids,
                lifecycle_status="ACTIVE",
            ).order_by("dataset_version_id", "id"),
        )
        sheet_names: dict[str, str] = {}
        for artifact in artifacts:
            key = str(artifact.dataset_version_id)
            if key in sheet_names:
                continue
            value = str(artifact.sheet_name or "").strip()
            if value:
                sheet_names[key] = value
        return sheet_names

    def _schema_column_names(self, schema: DatasetSchema | None) -> list[str]:
        if schema is None:
            return []
        columns = []
        for item in list(schema.schema_json or []):
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
                if name:
                    columns.append(name)
        return columns

    def _as_int(self, value) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except Exception:
            return None
