from __future__ import annotations

from typing import Any

from resource_registry.models import WorkbookIngestionGroup, WorkbookIngestionStatus


class WorkbookIngestionGroupService:
    """Manage workbook-level grouping metadata for multi-sheet ingestion."""

    def get_or_create_group(  # noqa: PLR0913
        self,
        *,
        tenant_id: str,
        source_uri: str,
        source_hash: str,
        source_file_name: str | None = None,
        source_storage_provider: str | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> WorkbookIngestionGroup:
        group, created = WorkbookIngestionGroup.objects.get_or_create(
            tenant_id=tenant_id,
            source_hash=source_hash,
            defaults={
                "source_uri": source_uri,
                "source_file_name": source_file_name,
                "source_storage_provider": source_storage_provider,
                "status": WorkbookIngestionStatus.STARTED,
                "metadata_json": metadata_json or {},
            },
        )
        if not created:
            updates: list[str] = []
            if source_uri and group.source_uri != source_uri:
                group.source_uri = source_uri
                updates.append("source_uri")
            if source_file_name and group.source_file_name != source_file_name:
                group.source_file_name = source_file_name
                updates.append("source_file_name")
            if source_storage_provider and group.source_storage_provider != source_storage_provider:
                group.source_storage_provider = source_storage_provider
                updates.append("source_storage_provider")
            if updates:
                group.save(update_fields=updates)
        return group

    def mark_completed(
        self,
        *,
        group: WorkbookIngestionGroup,
        total_sheets: int,
        total_created: int,
        total_deduplicated: int,
        total_skipped: int,
    ) -> WorkbookIngestionGroup:
        group.total_sheets = max(int(total_sheets), 0)
        group.total_created = max(int(total_created), 0)
        group.total_deduplicated = max(int(total_deduplicated), 0)
        group.total_skipped = max(int(total_skipped), 0)
        group.status = (
            WorkbookIngestionStatus.COMPLETED_WITH_SKIPS
            if group.total_skipped > 0
            else WorkbookIngestionStatus.COMPLETED
        )
        group.save(
            update_fields=[
                "total_sheets",
                "total_created",
                "total_deduplicated",
                "total_skipped",
                "status",
                "updated_at",
            ],
        )
        return group

    def mark_failed(self, *, group: WorkbookIngestionGroup) -> WorkbookIngestionGroup:
        group.status = WorkbookIngestionStatus.FAILED
        group.save(update_fields=["status", "updated_at"])
        return group
