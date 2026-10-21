from __future__ import annotations

from dataclasses import dataclass, field

from resource_registry.models import Dataset, DatasetArtifact, DatasetVersion, WorkbookIngestionGroup
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
        keep_limit = max(int(keep_versions_per_dataset), 1)
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
        old_version_ids: set[str] = set()
        versions_seen: dict[str, int] = {}
        for version in versions:
            dataset_key = str(version.dataset_id)
            versions_seen[dataset_key] = versions_seen.get(dataset_key, 0) + 1
            if versions_seen[dataset_key] > keep_limit:
                old_version_ids.add(str(version.id))

        active_artifacts = list(
            DatasetArtifact.objects.filter(
                lifecycle_status="ACTIVE",
                dataset_version_id__in=old_version_ids,
            ).order_by("-created_at"),
        )
        duplicate_source_artifacts = self._find_duplicate_source_artifacts(
            workbook_group_id=workbook_group_id,
        )
        selected_artifacts = self._dedupe_artifact_candidates(
            [*active_artifacts, *duplicate_source_artifacts],
        )
        deprecated_artifact_ids: list[str] = []
        if not dry_run:
            for artifact in selected_artifacts:
                updated = self._artifact_lifecycle_service.deprecate_artifact(
                    artifact=artifact,
                    reason="workbook_retention_policy",
                )
                deprecated_artifact_ids.append(str(updated.id))

        return WorkbookRetentionResult(
            workbook_group_id=workbook_group_id,
            dry_run=bool(dry_run),
            versions_considered=len(versions),
            versions_selected=len(old_version_ids),
            artifacts_selected=len(selected_artifacts),
            deprecated_artifact_ids=deprecated_artifact_ids,
        )

    def _find_duplicate_source_artifacts(self, *, workbook_group_id: str) -> list[DatasetArtifact]:
        sources = list(
            DatasetArtifact.objects.filter(
                workbook_group_id=workbook_group_id,
                lifecycle_status="ACTIVE",
                artifact_role="PRIMARY",
            ).order_by("-created_at"),
        )
        retained_keys: set[str] = set()
        duplicates: list[DatasetArtifact] = []
        for artifact in sources:
            metadata = artifact.metadata_json if isinstance(artifact.metadata_json, dict) else {}
            key = "|".join(
                [
                    str(metadata.get("workbook_group_id") or workbook_group_id),
                    str(artifact.checksum or ""),
                    str(artifact.uri or ""),
                    str(artifact.sheet_name or ""),
                ],
            )
            if key in retained_keys:
                duplicates.append(artifact)
                continue
            retained_keys.add(key)
        return duplicates

    def _dedupe_artifact_candidates(
        self,
        artifacts: list[DatasetArtifact],
    ) -> list[DatasetArtifact]:
        seen: set[str] = set()
        selected: list[DatasetArtifact] = []
        for artifact in artifacts:
            key = str(artifact.id)
            if key in seen:
                continue
            seen.add(key)
            selected.append(artifact)
        return selected
