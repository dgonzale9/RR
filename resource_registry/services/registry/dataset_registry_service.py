from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max

from Organization.models import Workspace
from resource_registry.dataset_event_contracts import (
    DATASET_EVENT_SOURCE_REGISTRY,
)
from resource_registry.event_contracts import EventEnvelope, EventSubject, EventType, EventVersion
from resource_registry.event_contracts.contracts import JsonValue
from resource_registry.models import (
    Dataset,
    DatasetArtifact,
    DatasetProfile,
    DatasetSchema,
    DatasetVersion,
    DatasetVersionStatus,
    DatasetVisibilityScope,
    OutboxEntry,
    ProfileType,
    WorkbookIngestionGroup,
)
from resource_registry.registry_core import (
    NamespaceKey,
    ResourceKey,
    ResourceTypeKey,
    ResourceVersionKey,
)
from resource_registry.services.registry_namespace_resolution_service import (
    RegistryNamespaceResolutionService,
)


class DatasetRegistryService:
    """Orchestrates dataset registry domain entities."""

    def create_dataset(  # noqa: PLR0913
        self,
        *,
        tenant_id: str,
        slug: str,
        name: str,
        source_type: str,
        organization_id: int | None = None,
        workspace_id: int | None = None,
        visibility_scope: str = "WORKSPACE",
        created_by: Any | None = None,
        description: str | None = None,
        owner_team: str | None = None,
        tags: list[str] | None = None,
        workbook_group: WorkbookIngestionGroup | None = None,
    ) -> Dataset:
        self._validate_dataset_visibility(
            organization_id=organization_id,
            workspace_id=workspace_id,
            visibility_scope=visibility_scope,
        )
        return Dataset.objects.create(
            tenant_id=tenant_id,
            slug=slug,
            name=name,
            source_type=source_type,
            organization_id=organization_id,
            workspace_id=workspace_id,
            visibility_scope=visibility_scope,
            created_by=created_by,
            description=description,
            owner_team=owner_team,
            tags=tags or [],
            workbook_group=workbook_group,
        )

    @staticmethod
    def _validate_dataset_visibility(
        *,
        organization_id: int | None,
        workspace_id: int | None,
        visibility_scope: str,
    ) -> None:
        """Validate Dataset ownership facts supplied by a trusted registration boundary.

        Args:
            organization_id: Durable Organization owner, when supplied.
            workspace_id: Durable Workspace owner for Workspace-scoped data.
            visibility_scope: Requested Dataset visibility scope.

        Raises:
            ValidationError: If supplied ownership facts cannot form a valid Dataset scope.
        """
        if organization_id is None and workspace_id is None:
            return
        if visibility_scope not in DatasetVisibilityScope.values:
            raise ValidationError("Dataset visibility scope is invalid.")
        if organization_id is None:
            raise ValidationError(
                "Dataset organization is required when workspace ownership is supplied.",
            )
        if visibility_scope == DatasetVisibilityScope.ORGANIZATION:
            if workspace_id is not None:
                raise ValidationError(
                    "Organization-global datasets cannot retain a workspace owner.",
                )
            return
        if workspace_id is None:
            raise ValidationError("Workspace-scoped datasets require a workspace owner.")
        if not Workspace.objects.filter(pk=workspace_id, organization_id=organization_id).exists():
            raise ValidationError("Dataset workspace must belong to its organization.")

    def create_dataset_version(  # noqa: PLR0913
        self,
        *,
        dataset: Dataset,
        version_number: int,
        source_uri: str,
        content_hash: str,
        status: str = DatasetVersionStatus.PENDING,
        source_etag: str | None = None,
        notes: str | None = None,
    ) -> DatasetVersion:
        return DatasetVersion.objects.create(
            dataset=dataset,
            version_number=version_number,
            source_uri=source_uri,
            content_hash=content_hash,
            status=status,
            source_etag=source_etag,
            notes=notes,
        )

    @transaction.atomic
    def create_version_from_context(  # noqa: PLR0913
        self,
        *,
        dataset: Dataset,
        version_number: int,
        content_hash: str,
        context: dict[str, Any],
        status: str = DatasetVersionStatus.READY,
        profile_type: str = ProfileType.BASIC,
    ) -> DatasetVersion:
        persistence_result = self.create_version_bundle_from_context(
            dataset=dataset,
            version_number=version_number,
            content_hash=content_hash,
            context=context,
            status=status,
            profile_type=profile_type,
        )
        return persistence_result.dataset_version

    @transaction.atomic
    def create_version_bundle_from_context(  # noqa: PLR0913
        self,
        *,
        dataset: Dataset,
        version_number: int,
        content_hash: str,
        context: dict[str, Any],
        status: str = DatasetVersionStatus.READY,
        profile_type: str = ProfileType.BASIC,
    ) -> VersionContextPersistenceResult:
        source = context.get("source", {})
        summary = context.get("summary", {})
        schema = context.get("schema", [])
        safety = context.get("safety", {})
        schema_quality = context.get("schema_quality", {})
        schema_detection = source.get("schema_detection", {}) if isinstance(source, dict) else {}

        dataset_version = self.create_dataset_version(
            dataset=dataset,
            version_number=version_number,
            source_uri=str(source.get("path", "")),
            content_hash=content_hash,
            status=status,
        )

        schema_hash = self._stable_hash({"schema": schema})
        dataset_schema = self.attach_schema(
            dataset_version=dataset_version,
            schema_hash=schema_hash,
            schema_json=schema,
            column_count=int(summary.get("column_count", len(schema))),
            inference_engine=self._resolve_inference_engine(schema_detection=schema_detection),
            inference_version=self._resolve_inference_version(schema_detection=schema_detection),
        )

        # Persist only aggregate profile metadata; never preview rows.
        profile_hash = self._stable_hash({"summary": summary, "safety": safety})
        dataset_profile = self.attach_profile(
            dataset_version=dataset_version,
            profile_type=profile_type,
            profile_hash=profile_hash,
            metrics_json={
                "row_count": summary.get("row_count"),
                "column_count": summary.get("column_count"),
                "preview_limit": safety.get("preview_limit"),
                "preview_limited": safety.get("preview_limited"),
                "schema_quality": (schema_quality if isinstance(schema_quality, dict) else {}),
            },
        )

        if status == DatasetVersionStatus.READY and bool(
            getattr(settings, "RESOURCE_REGISTRY_OUTBOX_ENABLED", False)
        ):
            self._persist_ready_version_outbox_entry(
                dataset=dataset,
                dataset_version=dataset_version,
                dataset_schema=dataset_schema,
                dataset_profile=dataset_profile,
            )

        return VersionContextPersistenceResult(
            dataset_version=dataset_version,
            dataset_schema=dataset_schema,
            dataset_profile=dataset_profile,
        )

    def _persist_ready_version_outbox_entry(
        self,
        *,
        dataset: Dataset,
        dataset_version: DatasetVersion,
        dataset_schema: DatasetSchema,
        dataset_profile: DatasetProfile,
    ) -> None:
        """Persist a pending immutable Integration Event for one READY version bundle."""
        locked_dataset = Dataset.objects.select_for_update().get(pk=dataset.pk)
        namespace = self._resolve_dataset_namespace(locked_dataset)
        sequence = (
            OutboxEntry.objects.filter(
                dataset=locked_dataset, namespace_id=namespace.value
            ).aggregate(last_sequence=Max("sequence"))["last_sequence"]
            or 0
        ) + 1
        resource = ResourceKey(
            NamespaceKey(namespace.value),
            ResourceTypeKey("atlas.data.dataset"),
            str(locked_dataset.id),
        )
        subject = EventSubject(resource, ResourceVersionKey(resource, str(dataset_version.id)))
        payload: dict[str, JsonValue] = {
            "dataset_version_id": str(dataset_version.id),
            "version_number": dataset_version.version_number,
            "schema_hash": dataset_schema.schema_hash,
            "profile_hash": dataset_profile.profile_hash,
        }
        schema = DATASET_EVENT_SOURCE_REGISTRY.resolve(
            EventType("atlas.dataset.version_published"), EventVersion(1)
        )
        event_id = f"evt-{uuid.uuid4()}"
        envelope = EventEnvelope(
            event_id=event_id,
            schema=schema,
            source=schema.source,
            subject=subject,
            namespace=namespace,
            classification=schema.classification,
            occurred_at=dataset_version.created_at,
            recorded_at=dataset_version.created_at,
            correlation_id=None,
            causation_id=None,
            payload=payload,
        )
        canonical_event = json.dumps(
            {
                "event_id": event_id,
                "event_type": envelope.schema.event_type.value,
                "event_version": envelope.schema.version.value,
                "namespace": str(namespace.value),
                "payload": payload,
                "sequence": sequence,
                "checksum": envelope.checksum,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        OutboxEntry.objects.create(
            event_id=event_id,
            dataset=locked_dataset,
            namespace_id=namespace.value,
            sequence=sequence,
            event_type=envelope.schema.event_type.value,
            event_version=envelope.schema.version.value,
            source="dataset.registry.application",
            subject={"dataset_id": str(locked_dataset.id), "version_id": str(dataset_version.id)},
            payload=payload,
            canonical_event=canonical_event,
            checksum=envelope.checksum,
        )

    @staticmethod
    def _resolve_dataset_namespace(dataset: Dataset) -> NamespaceKey:
        """Resolve the persisted owner Namespace or reject incomplete ownership."""
        if dataset.organization_id is None:
            raise ValidationError(
                "Dataset Organization ownership is required for event publication."
            )
        resolver = RegistryNamespaceResolutionService()
        if dataset.visibility_scope == DatasetVisibilityScope.WORKSPACE:
            if dataset.workspace_id is None:
                raise ValidationError("Workspace-scoped Dataset requires Workspace ownership.")
            return NamespaceKey(
                resolver.resolve_namespace_id_for_workspace(
                    dataset.workspace_id,
                    expected_organization_id=dataset.organization_id,
                )
            )
        if dataset.visibility_scope == DatasetVisibilityScope.ORGANIZATION:
            mapping = resolver.resolve_organization_namespace(dataset.organization_id)
            if mapping is None:
                raise ValidationError("Organization has no Registry Namespace mapping.")
            return NamespaceKey(mapping.namespace_id)
        raise ValidationError("Dataset visibility scope is invalid.")

    def attach_artifact(  # noqa: PLR0913
        self,
        *,
        dataset_version: DatasetVersion,
        artifact_role: str,
        uri: str,
        format: str,
        size_bytes: int,
        compression: str | None = None,
        checksum: str | None = None,
        storage_provider: str | None = None,
        workbook_group: WorkbookIngestionGroup | None = None,
        sheet_name: str | None = None,
        source_artifact: DatasetArtifact | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> DatasetArtifact:
        return DatasetArtifact.objects.create(
            dataset_version=dataset_version,
            artifact_role=artifact_role,
            uri=uri,
            format=format,
            size_bytes=size_bytes,
            compression=compression,
            checksum=checksum,
            storage_provider=storage_provider,
            workbook_group=workbook_group,
            sheet_name=sheet_name,
            source_artifact=source_artifact,
            metadata_json=metadata_json or {},
        )

    def attach_schema(  # noqa: PLR0913
        self,
        *,
        dataset_version: DatasetVersion,
        schema_hash: str,
        schema_json: list[dict[str, Any]],
        column_count: int,
        inference_engine: str | None = None,
        inference_version: str | None = None,
    ) -> DatasetSchema:
        return DatasetSchema.objects.create(
            dataset_version=dataset_version,
            schema_hash=schema_hash,
            schema_json=schema_json,
            column_count=column_count,
            inference_engine=inference_engine,
            inference_version=inference_version,
        )

    def attach_profile(  # noqa: PLR0913
        self,
        *,
        dataset_version: DatasetVersion,
        profile_type: str,
        profile_hash: str,
        metrics_json: dict[str, Any],
        sample_size: int | None = None,
        runtime_ms: int | None = None,
        profiler_version: str | None = None,
    ) -> DatasetProfile:
        return DatasetProfile.objects.create(
            dataset_version=dataset_version,
            profile_type=profile_type,
            profile_hash=profile_hash,
            metrics_json=metrics_json,
            sample_size=sample_size,
            runtime_ms=runtime_ms,
            profiler_version=profiler_version,
        )

    def _stable_hash(self, payload: dict[str, Any]) -> str:
        canonical_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    def _resolve_inference_engine(self, *, schema_detection: dict[str, Any]) -> str:
        strategy_name = str(schema_detection.get("strategy") or "").strip()
        if strategy_name:
            return f"dataset_context_builder:{strategy_name}"
        return "dataset_context_builder"

    def _resolve_inference_version(self, *, schema_detection: dict[str, Any]) -> str | None:
        normalization_version = str(schema_detection.get("normalization_version") or "").strip()
        return normalization_version or None


@dataclass(frozen=True)
class VersionContextPersistenceResult:
    dataset_version: DatasetVersion
    dataset_schema: DatasetSchema
    dataset_profile: DatasetProfile
