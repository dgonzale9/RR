"""Verify that Registry discovery distinguishes restored versions from newer history."""

from __future__ import annotations

from dataclasses import asdict
from uuid import uuid4

import pytest
from django.contrib.auth.models import User
from django.db import connection
from django.test.utils import CaptureQueriesContext

from Organization.models import Organization, Workspace
from resource_registry.assistant.prompt_result_compactor import (
    ResourceRegistryPromptResultCompactor,
)
from resource_registry.bifs.grouped_tools import (
    FunctionToolDefinition,
    build_grouped_registry_tools,
)
from resource_registry.models import Dataset, DatasetSchema, DatasetVersion, RegistryNamespace
from resource_registry.read.dataset_read_service import DatasetReadService
from resource_registry.read.exceptions import DatasetNotFoundError, DatasetVersionNotFoundError
from resource_registry.read.schema_read_service import DatasetSchemaReadService
from resource_registry.read.version_read_service import DatasetVersionReadService


@pytest.fixture
def restored_dataset(db: None) -> tuple[Dataset, DatasetVersion, DatasetVersion]:
    """Create a restored first version alongside a newer READY historical version."""
    user = User.objects.create_user(username="version-discovery")
    organization = Organization.objects.create(
        name="Version discovery", slug="version-discovery", last_modified_by=user
    )
    workspace = Workspace.objects.create(
        organization=organization,
        name="Version discovery",
        slug="version-discovery",
        last_modified_by=user,
    )
    namespace, _ = RegistryNamespace.objects.get_or_create(
        workspace=workspace, defaults={"id": uuid4()}
    )
    dataset = Dataset.objects.create(
        tenant_id=namespace.id,
        organization=organization,
        workspace=workspace,
        slug="sales",
        name="Sales",
        source_type="FILE_UPLOAD",
        status="ACTIVE",
    )
    versions = []
    for number in (1, 2):
        version = DatasetVersion.objects.create(
            dataset=dataset,
            version_number=number,
            status="READY",
            source_uri=f"local://sales-v{number}.csv",
            content_hash=f"hash-{number}",
        )
        DatasetSchema.objects.create(
            dataset_version=version,
            schema_hash=f"schema-{number}",
            column_count=1,
            schema_json=[{"name": f"version_{number}", "type": "VARCHAR"}],
        )
        versions.append(version)
    dataset.current_version = versions[0]
    dataset.save(update_fields=["current_version"])
    return dataset, versions[0], versions[1]


def test_discovery_exposes_restored_current_version(
    restored_dataset: tuple[Dataset, DatasetVersion, DatasetVersion],
) -> None:
    """Expose the current pointer and mark history without changing newest-first pagination."""
    dataset, current, historical = restored_dataset
    scope = {"tenant_id": str(dataset.tenant_id), "dataset_id": str(dataset.id)}
    reader = DatasetReadService()
    listed = reader.list_datasets(tenant_id=scope["tenant_id"])
    assert asdict(listed[0])["current_version_id"] == str(current.id)
    assert asdict(reader.get_dataset(**scope))["current_version_id"] == str(current.id)
    assert reader.search_datasets(tenant_id=scope["tenant_id"], query="Sales")[
        0
    ].current_version_id == str(current.id)

    versions = DatasetVersionReadService()
    first_page = versions.list_versions(**scope, limit=1)
    second_page = versions.list_versions(**scope, limit=1, offset=1)
    assert first_page[0].id == str(historical.id)
    assert asdict(first_page[0])["is_current"] is False
    assert second_page[0].id == str(current.id)
    assert asdict(second_page[0])["is_current"] is True
    assert versions.get_latest_version(**scope).is_current is True
    assert versions.get_version(**scope, version_id=str(historical.id)).is_current is False


def test_default_schema_uses_restored_version_and_explicit_history_remains_readable(
    restored_dataset: tuple[Dataset, DatasetVersion, DatasetVersion],
) -> None:
    """Respect the current pointer while preserving explicit historical reads."""
    dataset, current, historical = restored_dataset
    scope = {"tenant_id": str(dataset.tenant_id), "dataset_id": str(dataset.id)}
    reader = DatasetSchemaReadService()
    assert reader.get_latest_schema(**scope).dataset_version_id == str(current.id)
    assert reader.get_schema_for_version(
        **scope, version_id=str(historical.id)
    ).dataset_version_id == str(historical.id)


def test_discovery_preserves_namespace_isolation(
    restored_dataset: tuple[Dataset, DatasetVersion, DatasetVersion],
) -> None:
    """Keep version selection metadata behind existing namespace visibility checks."""
    dataset, current, _ = restored_dataset
    foreign_scope = {"tenant_id": str(uuid4()), "dataset_id": str(dataset.id)}
    assert DatasetReadService().list_datasets(tenant_id=foreign_scope["tenant_id"]) == []
    with pytest.raises(DatasetNotFoundError):
        DatasetReadService().get_dataset(**foreign_scope)
    assert DatasetVersionReadService().list_versions(**foreign_scope) == []
    with pytest.raises(DatasetVersionNotFoundError):
        DatasetVersionReadService().get_version(**foreign_scope, version_id=str(current.id))


def test_discovery_query_count_does_not_grow_with_collection_size(
    restored_dataset: tuple[Dataset, DatasetVersion, DatasetVersion],
) -> None:
    """Read current-version metadata without per-record database requests."""
    dataset, _, _ = restored_dataset
    scope = {"tenant_id": str(dataset.tenant_id), "dataset_id": str(dataset.id)}
    with CaptureQueriesContext(connection) as small_versions:
        DatasetVersionReadService().list_versions(**scope)
    with CaptureQueriesContext(connection) as small_datasets:
        DatasetReadService().list_datasets(tenant_id=scope["tenant_id"])
    for number in range(3, 9):
        version = DatasetVersion.objects.create(
            dataset=dataset,
            version_number=number,
            status="READY",
            source_uri=f"local://history-{number}.csv",
            content_hash=f"history-{number}",
        )
        DatasetSchema.objects.create(
            dataset_version=version,
            schema_hash=f"history-{number}",
            column_count=1,
            schema_json=[{"name": "sale", "type": "VARCHAR"}],
        )
        Dataset.objects.create(
            tenant_id=dataset.tenant_id,
            organization=dataset.organization,
            workspace=dataset.workspace,
            slug=f"sales-{number}",
            name=f"Sales {number}",
            source_type="FILE_UPLOAD",
            status="ACTIVE",
        )
    with CaptureQueriesContext(connection) as large_versions:
        result = DatasetVersionReadService().list_versions(**scope)
    with CaptureQueriesContext(connection) as large_datasets:
        DatasetReadService().list_datasets(tenant_id=scope["tenant_id"])
    assert len(result) == 8
    assert len(large_versions) <= len(small_versions)
    assert len(large_datasets) <= len(small_datasets)


def test_prompt_replay_preserves_current_and_resolved_version_ids() -> None:
    """Keep version provenance available after tool results are compacted."""
    compactor = ResourceRegistryPromptResultCompactor()
    listing = compactor.compact(
        tool_name="resource_registry_list_datasets",
        raw_result={
            "success": True,
            "data": {
                "datasets": [
                    {"id": "dataset", "name": "Sales", "current_version_id": "restored-v1"},
                ]
            },
        },
    )
    assert listing["data"]["datasets"][0]["current_version_id"] == "restored-v1"
    schema = compactor.compact(
        tool_name="resource_registry_get_dataset_schema",
        raw_result={
            "success": True,
            "data": {
                "schema": {
                    "dataset_version_id": "restored-v1",
                    "column_count": 1,
                    "schema_json": [{"name": "sale", "type": "VARCHAR"}],
                }
            },
        },
    )
    assert schema["data"]["schema"]["dataset_version_id"] == "restored-v1"


@pytest.mark.parametrize("operation", ["list_dataset_versions", "query_dataset", "create_chart"])
def test_model_tools_explain_current_version_defaults(operation: str) -> None:
    """Publish current-versus-historical selection guidance to every consuming tool group."""
    tool = FunctionToolDefinition(
        name=f"resource_registry_{operation}",
        description="Registry operation.",
        parameters={"type": "object", "properties": {"version_id": {"type": "string"}}},
    )
    published = build_grouped_registry_tools([tool])[0]
    assert "current_version_id" in published.description
    assert "is_current" in published.description
    assert "historical" in published.description
    assert "explicitly" in published.description
