from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import duckdb
import pytest

from resource_registry.services.workbook_consolidation_service import (
    WorkbookConsolidationError,
    WorkbookConsolidationService,
)


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


@pytest.fixture
def sample_sheet_parquets(tmp_path: Path) -> tuple[Path, Path]:
    first = tmp_path / "sheet1.parquet"
    second = tmp_path / "sheet2.parquet"
    with duckdb.connect(database=":memory:") as connection:
        connection.execute(
            """
            CREATE TABLE sheet_one AS
            SELECT 1 AS id, 'CR' AS country, 100.0 AS amount
            UNION ALL SELECT 2, 'US', 200.0
            """,
        )
        connection.execute("COPY sheet_one TO ? (FORMAT PARQUET)", [str(first)])
        connection.execute(
            """
            CREATE TABLE sheet_two AS
            SELECT 10 AS id, 'A' AS segment
            UNION ALL SELECT 11, 'B'
            """,
        )
        connection.execute("COPY sheet_two TO ? (FORMAT PARQUET)", [str(second)])
    return first, second


def test_materialize_consolidated_parquet_builds_union_by_name(
    sample_sheet_parquets: tuple[Path, Path],
) -> None:
    first, second = sample_sheet_parquets
    now = datetime.now(tz=timezone.utc)
    group = SimpleNamespace(id="group-1", tenant_id="tenant-1")
    datasets = _FakeQuerySet(
        [
            SimpleNamespace(id="d-1", slug="sales-summary"),
            SimpleNamespace(id="d-2", slug="sales-detail"),
        ],
    )
    versions = _FakeQuerySet(
        [
            SimpleNamespace(
                id="v-1",
                dataset_id="d-1",
                version_number=1,
                created_at=now,
            ),
            SimpleNamespace(
                id="v-2",
                dataset_id="d-2",
                version_number=1,
                created_at=now,
            ),
        ],
    )
    artifacts = _FakeQuerySet(
        [
            SimpleNamespace(
                id="a-1",
                dataset_version_id="v-1",
                uri=str(first),
                format="PARQUET",
                artifact_role="SNAPSHOT",
                lifecycle_status="ACTIVE",
                metadata_json={"is_canonical": True},
                sheet_name="Summary",
                created_at=now,
            ),
            SimpleNamespace(
                id="a-2",
                dataset_version_id="v-2",
                uri=str(second),
                format="PARQUET",
                artifact_role="SNAPSHOT",
                lifecycle_status="ACTIVE",
                metadata_json={"is_canonical": True},
                sheet_name="Detail",
                created_at=now,
            ),
        ],
    )

    service = WorkbookConsolidationService()
    with (
        patch(
            "resource_registry.services.workbook_consolidation_service."
            "WorkbookIngestionGroup.objects.filter",
        ) as group_filter,
        patch(
            "resource_registry.services.workbook_consolidation_service.Dataset.objects.filter",
            return_value=datasets,
        ),
        patch(
            "resource_registry.services.workbook_consolidation_service.DatasetVersion.objects.filter",
            return_value=versions,
        ),
        patch(
            "resource_registry.services.workbook_consolidation_service.DatasetArtifact.objects.filter",
            return_value=artifacts,
        ),
    ):
        group_filter.return_value.first.return_value = group
        with service.materialize_consolidated_parquet(
            tenant_id="tenant-1",
            workbook_group_id="group-1",
        ) as result:
            assert result.row_count == 4
            assert "__sheet_name" in result.columns
            assert set(result.sheets) == {"Summary", "Detail"}
            assert Path(result.local_path).exists()


def test_materialize_consolidated_parquet_fails_for_unknown_group() -> None:
    service = WorkbookConsolidationService()
    with patch(
        "resource_registry.services.workbook_consolidation_service.WorkbookIngestionGroup.objects.filter",
    ) as group_filter:
        group_filter.return_value.first.return_value = None
        with pytest.raises(WorkbookConsolidationError):
            service.materialize_consolidated_parquet(
                tenant_id="tenant-1",
                workbook_group_id="group-missing",
            )
