from __future__ import annotations

from dataclasses import dataclass, field

from resource_registry.models import (
    Dataset,
    DatasetVersion,
    WorkbookIngestionGroup,
)
from resource_registry.services.registry.artifact_lifecycle_service import ArtifactLifecycleService


@dataclass(frozen=True)
class WorkbookRetentionResult:
    workbook_group_id: str
    dry_run: bool
    versions_considered: int
    versions_selected: int
    artifacts_selected: int
    deprecated_artifact_ids: list[str] = field(default_factory=list)


class WorkbookRetentionService:
    """Apply retention rules for workbook ingestion groups."""

    def __init__(
        self,
        *,
        artifact_lifecycle_service: ArtifactLifecycleService | None = None,
    ) -> None:
        self._artifact_lifecycle_service = artifact_lifecycle_service or ArtifactLifecycleService()

    def apply_workbook_retention(
        self,
        *,
        tenant_id: str,
        workbook_group_id: str,
        keep_versions_per_dataset: int = 1,
        dry_run: bool = True,
    ) -> WorkbookRetentionResult:
        group = WorkbookIngestionGroup.objects.filter(
            id=workbook_group_id,
            tenant_id=tenant_id,
        ).first()
        if group is None:
            raise ValueError("Workbook ingestion group not found for tenant.")

        datasets = list(
            Dataset.objects.filter(
                tenant_id=tenant_id,
                workbook_group_id=workbook_group_id,
            ).order_by("slug", "id"),
        )
        dataset_ids = [dataset.id for dataset in datasets]
        versions = list(
            DatasetVersion.objects.filter(dataset_id__in=dataset_ids).order_by(
                "dataset_id",
                "-version_number",
                "-created_at",
            ),
        )
        # Permanent cleanup now owns retention. Old callers retain their result contract
        # but cannot make historical versions unavailable through artifact deprecation.
        return WorkbookRetentionResult(
            workbook_group_id=workbook_group_id,
            dry_run=bool(dry_run),
            versions_considered=len(versions),
            versions_selected=0,
            artifacts_selected=0,
        )
