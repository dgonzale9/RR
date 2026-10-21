from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from resource_registry.services.workbook_retention_service import WorkbookRetentionService


class _FakeQuerySet(list):
    def order_by(self, *fields: str):
        items = list(self)
        for field in reversed(fields):
            descending = field.startswith("-")
            name = field[1:] if descending else field
            items.sort(key=lambda item: getattr(item, name), reverse=descending)
        return _FakeQuerySet(items)

    def first(self):
        return self[0] if self else None


def test_apply_workbook_retention_reports_targets_in_dry_run() -> None:
    now = datetime.now(tz=timezone.utc)
    group = SimpleNamespace(id="group-1", tenant_id="tenant-1")
    datasets = _FakeQuerySet([SimpleNamespace(id="d-1", slug="sales")])
    versions = _FakeQuerySet(
        [
            SimpleNamespace(id="v-2", dataset_id="d-1", version_number=2, created_at=now),
            SimpleNamespace(id="v-1", dataset_id="d-1", version_number=1, created_at=now),
        ],
    )
    old_artifact = SimpleNamespace(
        id="a-old",
        dataset_version_id="v-1",
        artifact_role="SNAPSHOT",
        lifecycle_status="ACTIVE",
        metadata_json={},
        checksum=None,
        uri="local://old",
        sheet_name="Summary",
        created_at=now,
    )
    dup_primary_a = SimpleNamespace(
        id="a-dup-a",
        dataset_version_id="v-2",
        artifact_role="PRIMARY",
        lifecycle_status="ACTIVE",
        metadata_json={"workbook_group_id": "group-1"},
        checksum="same",
        uri="azure://datasets/original.xlsx",
        sheet_name="Summary",
        created_at=now,
    )
    dup_primary_b = SimpleNamespace(
        id="a-dup-b",
        dataset_version_id="v-2",
        artifact_role="PRIMARY",
        lifecycle_status="ACTIVE",
        metadata_json={"workbook_group_id": "group-1"},
        checksum="same",
        uri="azure://datasets/original.xlsx",
        sheet_name="Summary",
        created_at=now,
    )
    old_version_artifacts = _FakeQuerySet([old_artifact])
    primary_artifacts = _FakeQuerySet([dup_primary_a, dup_primary_b])

    service = WorkbookRetentionService(artifact_lifecycle_service=MagicMock())
    with (
        patch(
            "resource_registry.services.workbook_retention_service.WorkbookIngestionGroup.objects.filter",
        ) as group_filter,
        patch(
            "resource_registry.services.workbook_retention_service.Dataset.objects.filter",
            return_value=datasets,
        ),
        patch(
            "resource_registry.services.workbook_retention_service.DatasetVersion.objects.filter",
            return_value=versions,
        ),
        patch(
            "resource_registry.services.workbook_retention_service.DatasetArtifact.objects.filter",
            side_effect=[old_version_artifacts, primary_artifacts],
        ),
    ):
        group_filter.return_value.first.return_value = group
        result = service.apply_workbook_retention(
            tenant_id="tenant-1",
            workbook_group_id="group-1",
            keep_versions_per_dataset=1,
            dry_run=True,
        )

    assert result.dry_run is True
    assert result.versions_selected == 1
    assert result.artifacts_selected == 2
    assert result.deprecated_artifact_ids == []


def test_apply_workbook_retention_executes_deprecation_when_not_dry_run() -> None:
    now = datetime.now(tz=timezone.utc)
    group = SimpleNamespace(id="group-1", tenant_id="tenant-1")
    datasets = _FakeQuerySet([SimpleNamespace(id="d-1", slug="sales")])
    versions = _FakeQuerySet(
        [SimpleNamespace(id="v-1", dataset_id="d-1", version_number=1, created_at=now)],
    )
    artifact = SimpleNamespace(
        id="a-1",
        dataset_version_id="v-1",
        artifact_role="PRIMARY",
        lifecycle_status="ACTIVE",
        metadata_json={"workbook_group_id": "group-1"},
        checksum="h1",
        uri="azure://datasets/original.xlsx",
        sheet_name="Summary",
        created_at=now,
    )
    lifecycle = MagicMock()
    lifecycle.deprecate_artifact.return_value = artifact
    service = WorkbookRetentionService(artifact_lifecycle_service=lifecycle)
    with (
        patch(
            "resource_registry.services.workbook_retention_service.WorkbookIngestionGroup.objects.filter",
        ) as group_filter,
        patch(
            "resource_registry.services.workbook_retention_service.Dataset.objects.filter",
            return_value=datasets,
        ),
        patch(
            "resource_registry.services.workbook_retention_service.DatasetVersion.objects.filter",
            return_value=versions,
        ),
        patch(
            "resource_registry.services.workbook_retention_service.DatasetArtifact.objects.filter",
            side_effect=[_FakeQuerySet([]), _FakeQuerySet([artifact, artifact])],
        ),
    ):
        group_filter.return_value.first.return_value = group
        result = service.apply_workbook_retention(
            tenant_id="tenant-1",
            workbook_group_id="group-1",
            keep_versions_per_dataset=1,
            dry_run=False,
        )

    assert result.artifacts_selected == 1
    assert result.deprecated_artifact_ids == ["a-1"]
    lifecycle.deprecate_artifact.assert_called_once()


def test_apply_workbook_retention_raises_for_missing_group() -> None:
    service = WorkbookRetentionService(artifact_lifecycle_service=MagicMock())
    with patch(
        "resource_registry.services.workbook_retention_service.WorkbookIngestionGroup.objects.filter",
    ) as group_filter:
        group_filter.return_value.first.return_value = None
        with pytest.raises(ValueError):
            service.apply_workbook_retention(
                tenant_id="tenant-1",
                workbook_group_id="group-missing",
            )
