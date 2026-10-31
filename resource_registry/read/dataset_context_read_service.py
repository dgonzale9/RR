from __future__ import annotations

from resource_registry.dataset_visibility import build_dataset_visibility_filter
from resource_registry.models import Dataset, DatasetStatus
from resource_registry.read.artifact_read_service import DatasetArtifactReadService
from resource_registry.read.dataset_read_service import DatasetReadService
from resource_registry.read.dtos import DatasetContextSummary
from resource_registry.read.exceptions import DatasetVersionNotFoundError
from resource_registry.read.profile_read_service import DatasetProfileReadService
from resource_registry.read.schema_read_service import DatasetSchemaReadService
from resource_registry.read.version_read_service import DatasetVersionReadService
from resource_registry.security.policies import ArtifactMaterializationPolicyResolver


class DatasetContextReadService:
    """Composes safe dataset context from persisted registry metadata."""

    def __init__(
        self,
        *,
        dataset_read_service: DatasetReadService | None = None,
        version_read_service: DatasetVersionReadService | None = None,
        artifact_read_service: DatasetArtifactReadService | None = None,
        schema_read_service: DatasetSchemaReadService | None = None,
        profile_read_service: DatasetProfileReadService | None = None,
        materialization_policy_resolver: ArtifactMaterializationPolicyResolver | None = None,
    ) -> None:
        self._dataset_read_service = dataset_read_service or DatasetReadService()
        self._version_read_service = version_read_service or DatasetVersionReadService()
        self._artifact_read_service = artifact_read_service or DatasetArtifactReadService()
        self._schema_read_service = schema_read_service or DatasetSchemaReadService()
        self._profile_read_service = profile_read_service or DatasetProfileReadService()
        self._materialization_policy_resolver = (
            materialization_policy_resolver or ArtifactMaterializationPolicyResolver()
        )

    def get_dataset_context(self, *, tenant_id: str, dataset_id: str) -> DatasetContextSummary:
        dataset = self._dataset_read_service.get_dataset(tenant_id=tenant_id, dataset_id=dataset_id)
        latest_version = None
        latest_schema = None
        latest_profile = None
        latest_artifact = None
        policy_provenance = None

        try:
            latest_version = self._version_read_service.get_latest_version(
                tenant_id=tenant_id,
                dataset_id=dataset_id,
            )
        except DatasetVersionNotFoundError:
            latest_version = None
        if latest_version is not None:
            try:
                latest_schema = self._schema_read_service.get_schema_for_version(
                    tenant_id=tenant_id,
                    dataset_id=dataset_id,
                    version_id=latest_version.id,
                )
            except DatasetVersionNotFoundError:
                latest_schema = None
            try:
                latest_profile = self._profile_read_service.get_profile_for_version(
                    tenant_id=tenant_id,
                    dataset_id=dataset_id,
                    version_id=latest_version.id,
                )
            except DatasetVersionNotFoundError:
                latest_profile = None
            artifacts = self._artifact_read_service.list_artifacts(
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                version_id=latest_version.id,
                limit=1,
                offset=0,
            )
            latest_artifact = artifacts[0] if artifacts else None

        dataset_obj = Dataset.objects.filter(
            build_dataset_visibility_filter(tenant_id=tenant_id),
            id=dataset_id,
            archived_at__isnull=True,
            status__in=[DatasetStatus.DRAFT, DatasetStatus.ACTIVE],
        ).first()
        if dataset_obj is not None:
            resolved = self._materialization_policy_resolver.resolve_with_provenance(
                tenant_id=tenant_id,
                dataset_id=dataset_id,
            )
            provenance = resolved.provenance
            policy_provenance = {
                "policy_key": provenance.policy_key,
                "source": str(provenance.source),
                "revision": provenance.revision,
                "rules_hash": provenance.rules_hash,
                "resolution_path": list(provenance.resolution_path),
            }

        return DatasetContextSummary(
            dataset=dataset,
            latest_version=latest_version,
            schema=latest_schema,
            profile=latest_profile,
            artifact=latest_artifact,
            policy_provenance=policy_provenance,
        )
