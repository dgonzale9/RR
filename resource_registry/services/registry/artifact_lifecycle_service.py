from __future__ import annotations

from collections.abc import Callable
from typing import Any

from django.db.models import QuerySet

from resource_registry.events.audit_service import AuditService
from resource_registry.models import (
    ArtifactLifecycleStatus,
    DatasetArtifact,
    DatasetVersion,
    WorkbookIngestionGroup,
)
from resource_registry.storage.base import StorageProvider


class ArtifactLifecycleBoundaryError(ValueError):
    """Raised when artifact lifecycle operations cross tenant boundaries."""


class ArtifactLifecycleService:
    """Provider-neutral dataset artifact lifecycle manager."""

    def __init__(self, audit_service: AuditService | None = None) -> None:
        self._audit_service = audit_service or AuditService()

    def register_uploaded_artifact(  # noqa: PLR0913
        self,
        *,
        dataset_version: DatasetVersion,
        artifact_role: str,
        uri: str,
        format: str,
        size_bytes: int,
        storage_provider: str | None = None,
        checksum: str | None = None,
        workbook_group: WorkbookIngestionGroup | None = None,
        sheet_name: str | None = None,
        source_artifact: DatasetArtifact | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> DatasetArtifact:
        artifact = DatasetArtifact.objects.create(
            dataset_version=dataset_version,
            artifact_role=artifact_role,
            uri=uri,
            format=format,
            size_bytes=size_bytes,
            storage_provider=storage_provider,
            checksum=checksum,
            lifecycle_status=ArtifactLifecycleStatus.ACTIVE,
            workbook_group=workbook_group,
            sheet_name=sheet_name,
            source_artifact=source_artifact,
            metadata_json=metadata_json or {},
        )
        self._safe_audit(
            lambda: self._audit_service.record_dataset_artifact_registered(
                tenant_id=str(dataset_version.dataset.tenant_id),
                dataset_id=str(dataset_version.dataset.id),
                dataset_version_id=str(dataset_version.id),
                metadata={
                    "artifact_id": str(artifact.id),
                    "storage_provider": artifact.storage_provider,
                    "artifact_role": artifact.artifact_role,
                },
            ),
        )
        return artifact

    def register_uploaded_artifact_idempotently(  # noqa: PLR0913
        self,
        *,
        dataset_version: DatasetVersion,
        artifact_role: str,
        uri: str,
        format: str,
        size_bytes: int,
        storage_provider: str | None = None,
        checksum: str | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> DatasetArtifact:
        """Register or strictly resolve one identical active uploaded artifact."""
        existing = DatasetArtifact.objects.filter(
            dataset_version=dataset_version,
            artifact_role=artifact_role,
            uri=uri,
        ).first()
        expected_metadata = metadata_json or {}
        if existing is not None:
            if (
                existing.format != format
                or existing.size_bytes != size_bytes
                or existing.storage_provider != storage_provider
                or existing.checksum != checksum
                or existing.metadata_json != expected_metadata
                or existing.lifecycle_status != ArtifactLifecycleStatus.ACTIVE
            ):
                raise ValueError("Existing dataset artifact does not match registration intent.")
            return existing
        return self.register_uploaded_artifact(
            dataset_version=dataset_version,
            artifact_role=artifact_role,
            uri=uri,
            format=format,
            size_bytes=size_bytes,
            storage_provider=storage_provider,
            checksum=checksum,
            metadata_json=expected_metadata,
        )

    def deprecate_artifact(
        self,
        *,
        artifact: DatasetArtifact,
        reason: str | None = None,
    ) -> DatasetArtifact:
        artifact.lifecycle_status = ArtifactLifecycleStatus.DEPRECATED
        metadata_json = dict(artifact.metadata_json or {})
        if reason:
            metadata_json["deprecation_reason"] = reason
        artifact.metadata_json = metadata_json
        artifact.save(update_fields=["lifecycle_status", "metadata_json"])
        self._safe_audit(
            lambda: self._audit_service.record_dataset_artifact_deprecated(
                tenant_id=str(artifact.dataset_version.dataset.tenant_id),
                dataset_id=str(artifact.dataset_version.dataset.id),
                dataset_version_id=str(artifact.dataset_version.id),
                metadata={
                    "artifact_id": str(artifact.id),
                    "reason": reason,
                },
            ),
        )
        return artifact

    def restore_artifact(self, *, artifact: DatasetArtifact) -> DatasetArtifact:
        artifact.lifecycle_status = ArtifactLifecycleStatus.ACTIVE
        artifact.save(update_fields=["lifecycle_status"])
        self._safe_audit(
            lambda: self._audit_service.record_dataset_artifact_restored(
                tenant_id=str(artifact.dataset_version.dataset.tenant_id),
                dataset_id=str(artifact.dataset_version.dataset.id),
                dataset_version_id=str(artifact.dataset_version.id),
                metadata={"artifact_id": str(artifact.id)},
            ),
        )
        return artifact

    def delete_artifact_reference(self, *, artifact: DatasetArtifact) -> DatasetArtifact:
        artifact.lifecycle_status = ArtifactLifecycleStatus.DELETED_REFERENCE
        artifact.save(update_fields=["lifecycle_status"])
        self._safe_audit(
            lambda: self._audit_service.record_dataset_artifact_reference_deleted(
                tenant_id=str(artifact.dataset_version.dataset.tenant_id),
                dataset_id=str(artifact.dataset_version.dataset.id),
                dataset_version_id=str(artifact.dataset_version.id),
                metadata={"artifact_id": str(artifact.id)},
            ),
        )
        return artifact

    def delete_physical_artifact(
        self,
        *,
        artifact: DatasetArtifact,
        storage_provider: StorageProvider,
    ) -> DatasetArtifact:
        storage_provider.delete(artifact.uri)
        artifact.lifecycle_status = ArtifactLifecycleStatus.PHYSICAL_DELETED
        artifact.save(update_fields=["lifecycle_status"])
        self._safe_audit(
            lambda: self._audit_service.record_dataset_artifact_physical_deleted(
                tenant_id=str(artifact.dataset_version.dataset.tenant_id),
                dataset_id=str(artifact.dataset_version.dataset.id),
                dataset_version_id=str(artifact.dataset_version.id),
                metadata={
                    "artifact_id": str(artifact.id),
                    "storage_provider": artifact.storage_provider,
                },
            ),
        )
        return artifact

    def cleanup_orphan_upload(  # noqa: PLR0913
        self,
        *,
        storage_provider: StorageProvider,
        uri: str,
        tenant_id: str,
        dataset_id: str | None = None,
        dataset_version_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        storage_provider.delete(uri)
        event_metadata = dict(metadata or {})
        event_metadata["uri"] = uri
        self._safe_audit(
            lambda: self._audit_service.record_dataset_artifact_orphan_cleaned(
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                dataset_version_id=dataset_version_id,
                metadata=event_metadata,
            ),
        )

    def get_artifacts_for_version(
        self,
        *,
        dataset_version: DatasetVersion,
        include_deleted_reference: bool = True,
    ) -> QuerySet[DatasetArtifact]:
        artifacts = DatasetArtifact.objects.filter(dataset_version=dataset_version)
        if not include_deleted_reference:
            artifacts = artifacts.exclude(
                lifecycle_status=ArtifactLifecycleStatus.DELETED_REFERENCE,
            )
        return artifacts.order_by("-created_at")

    def _safe_audit(self, audit_call: Callable[[], object]) -> None:
        try:
            audit_call()
        except Exception:
            return
