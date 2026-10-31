"""Provide bounded, authorized dataset representations for REST and Django Admin."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from django.contrib.auth.models import User
from django.db.models import CharField, Func, OuterRef, Q, QuerySet, Subquery
from django.db.models.functions import Cast, JSONObject

from Organization.models import Organization
from resource_registry.management_domain.changes import JsonObject
from resource_registry.management_domain.errors import DatasetManagementError
from resource_registry.management_domain.lifecycle import DatasetLifecycleService
from resource_registry.models import Dataset, DatasetChange, DatasetLineage
from resource_registry.services.dataset_context_builder import DatasetContextBuilder
from resource_registry.storage.factory import DataRegistryStorageFactory


def dataset_payload(dataset: Dataset) -> JsonObject:
    """Serialize one eagerly loaded dataset without exposing storage locations."""
    return {
        "id": str(dataset.id),
        "name": dataset.name,
        "description": dataset.description,
        "tags": dataset.tags,
        "owner_team": dataset.owner_team,
        "status": dataset.status,
        "source_type": dataset.source_type,
        "namespace_id": str(dataset.tenant_id),
        "workspace_id": dataset.workspace_id,
        "visibility_scope": dataset.visibility_scope,
        "responsible_user_id": dataset.responsible_user_id,
        "responsible_user": (
            dataset.responsible_user.get_username() if dataset.responsible_user else None
        ),
        "revision": dataset.revision,
        "source_id": str(dataset.external_source_id) if dataset.external_source_id else None,
        "content_managed_externally": dataset.external_source_id is not None,
        "data_available": dataset.purge_started_at is None,
        "purged_at": dataset.purged_at.isoformat() if dataset.purged_at else None,
        "current_version_id": (
            str(dataset.current_version_id) if dataset.current_version_id else None
        ),
        "current_version_number": (
            dataset.current_version.version_number if dataset.current_version else None
        ),
        "last_successful_update": (
            dataset.current_version.created_at.isoformat() if dataset.current_version else None
        ),
        "requires_review": dataset.current_version_id is None,
        "archived_at": dataset.archived_at.isoformat() if dataset.archived_at else None,
        "archive_reason": dataset.archive_reason,
        "workbook_id": str(dataset.workbook_id) if dataset.workbook else None,
        "workbook_revision": dataset.workbook.revision if dataset.workbook else None,
        "workbook_sheet": dataset.workbook_sheet,
        "pending_update_status": getattr(dataset, "pending_update_status", None),
    }


def change_payload(change: DatasetChange) -> JsonObject:
    """Expose review evidence while keeping private artifact coordinates server-side."""
    return {
        "id": str(change.id),
        "namespace_id": str(change.namespace_id),
        "status": change.status,
        "revision": change.revision,
        "source_id": str(change.external_source_id) if change.external_source_id else None,
        "source_revision": change.external_source.revision if change.external_source else None,
        "source_reference": change.source_reference or None,
        "submitted_by_id": change.submitted_by_id,
        "specification": change.specification,
        "created_at": change.created_at.isoformat(),
        "error_code": change.error_code,
        "error_message": change.error_message,
        "result": change.result,
        "available_sheets": change.prepared.get("available_sheets", []),
        "suggested_sheets": change.prepared.get("suggested_sheets", []),
        "items": [
            {
                key: item.get(key)
                for key in (
                    "dataset_id",
                    "name",
                    "sheet_name",
                    "difference",
                    "unchanged",
                    "historical_version_id",
                )
            }
            for item in change.prepared.get("items", [])
        ],
    }


class DatasetCatalogService:
    """Share authorized listing, detail, previews and recorded dependency reads."""

    def __init__(self, user: User, organization: Organization | None) -> None:
        """Bind reads to the authenticated actor and trusted organization."""
        self.lifecycle = DatasetLifecycleService(user, organization)
        self.authorization = self.lifecycle.authorization

    def queryset(self, *, include_archived: bool = False) -> QuerySet[Dataset]:
        """Eager-load summary relationships and restrict archived data to managers."""
        queryset = self.authorization.datasets(include_archived=include_archived)
        if include_archived:
            managed = [
                namespace.id
                for namespace in self.authorization.namespaces()
                if self.authorization.can_manage_namespace(namespace)
            ]
            queryset = queryset.filter(
                Q(archived_at__isnull=True) & ~Q(status="ARCHIVED") | Q(tenant_id__in=managed)
            )
        pending_changes = (
            DatasetChange.objects.filter(namespace_id=OuterRef("tenant_id"))
            .exclude(
                status__in=["PUBLISHED", "CANCELLED", "NO_CHANGES"],
            )
            .filter(
                Q(
                    specification__contains=JSONObject(
                        dataset_id=Cast(OuterRef("id"), output_field=CharField())
                    )
                )
                | Q(workbook_id=OuterRef("workbook_id"))
                | Q(
                    specification__sheets__contains=Func(
                        JSONObject(dataset_id=Cast(OuterRef("id"), output_field=CharField())),
                        function="jsonb_build_array",
                    )
                )
            )
            .order_by("-created_at")
        )
        return queryset.annotate(
            pending_update_status=Subquery(pending_changes.values("status")[:1])
        ).select_related(
            "current_version", "responsible_user", "workspace", "organization", "workbook"
        )

    def list_datasets(self, parameters: Mapping[str, str]) -> JsonObject:
        """Filter admission before searching, counting and paginating the catalog."""
        queryset = self.queryset(include_archived=parameters.get("include_archived") == "true")
        namespace_id = parameters.get("namespace_id") or parameters.get("tenant_id")
        if namespace_id:
            try:
                namespace = self.authorization.get_namespace(UUID(namespace_id))
            except ValueError as error:
                raise DatasetManagementError(
                    "invalid_namespace_id", "Select a valid destination.", 400
                ) from error
            queryset = queryset.filter(
                Q(tenant_id=namespace.id)
                | Q(visibility_scope="ORGANIZATION", workspace__isnull=True)
            )
        search = parameters.get("q") or parameters.get("search")
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search)
                | Q(description__icontains=search)
                | Q(slug__icontains=search)
            )
        for field in (
            "workspace_id",
            "responsible_user_id",
            "visibility_scope",
            "source_type",
            "status",
        ):
            if parameters.get(field):
                value: str | int = parameters[field]
                if field.endswith("_id"):
                    try:
                        value = int(value)
                    except ValueError as error:
                        raise DatasetManagementError(
                            "invalid_filter", "Select a valid catalog filter.", 400
                        ) from error
                queryset = queryset.filter(**{field: value})
        if parameters.get("tag"):
            queryset = queryset.filter(tags__contains=[parameters["tag"]])
        try:
            limit = min(max(int(parameters.get("limit", "100")), 1), 200)
            offset = max(int(parameters.get("offset", "0")), 0)
            total = queryset.count()
            rows = [
                dataset_payload(dataset)
                for dataset in queryset.order_by("-updated_at", "id")[offset : offset + limit]
            ]
        except ValueError as error:
            raise DatasetManagementError(
                "invalid_filter", "Invalid catalog filter or pagination.", 400
            ) from error
        return {
            "results": rows,
            "count": len(rows),
            "total": total,
            "limit": limit,
            "offset": offset,
            "next_offset": offset + limit if offset + limit < total else None,
            "previous_offset": max(offset - limit, 0) if offset else None,
            **({"namespace_id": namespace_id} if namespace_id else {}),
        }

    def get_detail(self, identifier: UUID | str) -> JsonObject:
        """Return metadata and the schema selected by the current-version pointer."""
        dataset = self.lifecycle.get_dataset(identifier)
        payload = dataset_payload(dataset)
        schema = (
            dataset.current_version.schemas.order_by("-created_at").first()
            if dataset.current_version
            else None
        )
        payload["schema"] = schema.schema_json if schema else []
        return payload

    def get_preview(self, identifier: UUID | str, *, limit: int = 10) -> JsonObject:
        """Read a bounded preview from the available current canonical artifact."""
        dataset = self.lifecycle.get_dataset(identifier)
        if dataset.purge_started_at is not None:
            raise DatasetManagementError(
                "dataset_purging", "Dataset data is no longer available.", 410
            )
        if dataset.current_version is None:
            return {"version_id": None, "rows": []}
        artifact = dataset.current_version.artifacts.filter(
            lifecycle_status="ACTIVE", metadata_json__is_canonical=True
        ).first()
        if artifact is None:
            raise DatasetManagementError(
                "preview_unavailable", "The current version has no available preview."
            )
        provider = DataRegistryStorageFactory().create_provider(artifact.storage_provider)
        try:
            with TemporaryDirectory(prefix="dataset-preview-") as directory:
                path = Path(directory) / "preview.parquet"
                provider.download_to_file(artifact.uri, str(path))
                context = DatasetContextBuilder().build_context(
                    path, preview_limit=min(max(limit, 1), 20)
                )
        except (OSError, ValueError) as error:
            raise DatasetManagementError(
                "preview_unavailable", "The current artifact is unavailable."
            ) from error
        return {"version_id": str(dataset.current_version_id), "rows": context["preview"]}

    def get_usage(self, identifier: UUID | str) -> JsonObject:
        """List visible recorded lineage without claiming a complete dependency inventory."""
        dataset = self.lifecycle.get_dataset(identifier)
        dependencies = DatasetLineage.objects.filter(
            upstream_dataset_version__dataset=dataset,
            downstream_dataset_version__dataset__in=self.queryset(),
        ).select_related("downstream_dataset_version__dataset")[:100]
        return {
            "complete_inventory": False,
            "results": [
                {
                    "dataset_id": str(item.downstream_dataset_version.dataset_id),
                    "name": item.downstream_dataset_version.dataset.name,
                    "version_id": str(item.downstream_dataset_version_id),
                }
                for item in dependencies
            ],
        }
