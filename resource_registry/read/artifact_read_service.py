from __future__ import annotations

from resource_registry.dataset_visibility import build_dataset_visibility_filter
from resource_registry.models import ArtifactLifecycleStatus, DatasetArtifact, DatasetStatus
from resource_registry.read.dtos import DatasetArtifactSummary, sanitize_json, sanitize_uri, to_iso
from resource_registry.read.exceptions import ArtifactNotFoundError


class DatasetArtifactReadService:
    """Tenant-scoped artifact metadata read service."""

    MAX_LIMIT = 100

    def list_artifacts(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        version_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[DatasetArtifactSummary]:
        queryset = DatasetArtifact.objects.filter(
            build_dataset_visibility_filter(
                tenant_id=tenant_id,
                field_prefix="dataset_version__dataset__",
            ),
            dataset_version__dataset_id=dataset_id,
            dataset_version__dataset__archived_at__isnull=True,
            dataset_version__dataset__status__in=[DatasetStatus.DRAFT, DatasetStatus.ACTIVE],
            lifecycle_status=ArtifactLifecycleStatus.ACTIVE,
        )
        if version_id:
            queryset = queryset.filter(dataset_version_id=version_id)
        ordered = queryset.order_by("-created_at", "-id")
        artifacts = self._paginate(list(ordered), limit=limit, offset=offset)
        return [self._to_summary(artifact) for artifact in artifacts]

    def get_artifact(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        artifact_id: str,
    ) -> DatasetArtifactSummary:
        artifact = DatasetArtifact.objects.filter(
            build_dataset_visibility_filter(
                tenant_id=tenant_id,
                field_prefix="dataset_version__dataset__",
            ),
            id=artifact_id,
            dataset_version__dataset_id=dataset_id,
            dataset_version__dataset__archived_at__isnull=True,
            dataset_version__dataset__status__in=[DatasetStatus.DRAFT, DatasetStatus.ACTIVE],
            lifecycle_status=ArtifactLifecycleStatus.ACTIVE,
        ).first()
        if artifact is None:
            raise ArtifactNotFoundError("Artifact not found for tenant dataset.")
        return self._to_summary(artifact)

    def _paginate(
        self,
        items: list[DatasetArtifact],
        *,
        limit: int,
        offset: int,
    ) -> list[DatasetArtifact]:
        safe_limit = min(max(limit, 1), self.MAX_LIMIT)
        safe_offset = max(offset, 0)
        return items[safe_offset : safe_offset + safe_limit]

    def _to_summary(self, artifact: DatasetArtifact) -> DatasetArtifactSummary:
        return DatasetArtifactSummary(
            id=str(artifact.id),
            dataset_version_id=str(artifact.dataset_version_id),
            tenant_id=str(artifact.dataset_version.dataset.tenant_id),
            artifact_role=artifact.artifact_role,
            uri=sanitize_uri(str(artifact.uri)),
            format=artifact.format,
            size_bytes=int(artifact.size_bytes),
            checksum=artifact.checksum,
            storage_provider=artifact.storage_provider,
            lifecycle_status=artifact.lifecycle_status,
            created_at=str(to_iso(artifact.created_at)),
            metadata_json=sanitize_json(dict(artifact.metadata_json or {})),
        )
