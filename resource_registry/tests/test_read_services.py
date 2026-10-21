from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import User

from Organization.models import Organization, Workspace
from resource_registry.models import (
    Dataset,
    DatasetProfile,
    DatasetSourceType,
    DatasetStatus,
    DatasetVersion,
    DatasetVersionStatus,
    ProfileType,
    RegistryNamespace,
)
from resource_registry.read.artifact_read_service import DatasetArtifactReadService
from resource_registry.read.dataset_context_read_service import DatasetContextReadService
from resource_registry.read.dataset_read_service import DatasetReadService
from resource_registry.read.exceptions import DataRegistryReadError, DatasetVersionNotFoundError
from resource_registry.read.job_read_service import ExternalDatasetJobReadService
from resource_registry.read.profile_read_service import DatasetProfileReadService
from resource_registry.read.schema_read_service import DatasetSchemaReadService
from resource_registry.read.version_read_service import DatasetVersionReadService
from resource_registry.read.workbook_context_read_service import WorkbookContextReadService
from resource_registry.read_auth.context import ReadAuthorizationContext
from resource_registry.read_auth.policy import AuthorizedDataRegistryReadFacade

PAGINATION_MAX = 100
LATEST_VERSION_NUMBER = 2


def _map_dataset_to_workspace(dataset: Dataset, tenant_id: uuid.UUID) -> None:
    """Map a persisted Dataset to the Workspace represented by its test namespace."""
    suffix = tenant_id.hex
    user = User.objects.create_user(username=f"read-service-{suffix}")
    organization = Organization.objects.create(
        name=f"Read Service {suffix}",
        slug=f"read-service-{suffix}",
        last_modified_by=user,
    )
    workspace = Workspace.objects.create(
        organization=organization,
        name=f"Workspace {suffix}",
        slug=f"workspace-{suffix}",
        last_modified_by=user,
    )
    RegistryNamespace.objects.create(id=tenant_id, workspace=workspace)
    dataset.organization = organization
    dataset.workspace = workspace
    dataset.save(update_fields=["organization", "workspace"])


class FakeQuerySet(list):
    def filter(self, *args: object, **kwargs: object) -> FakeQuerySet:
        """Filter fixture records by equality or an explicit dataset ID set.

        Args:
            args: Positional ORM expressions unused by this test double.
            kwargs: Field equality filters or an id__in frozenset.

        Returns:
            Matching fixture records in their original order.

        Raises:
            AssertionError: If id__in is not supplied as a frozenset.
        """
        _ = args
        result = list(self)
        for key, value in kwargs.items():
            if key == "id__in":
                assert isinstance(value, frozenset)
                result = [item for item in result if item.id in value]
                continue
            result = [item for item in result if _resolve_attr(item, key) == value]
        return FakeQuerySet(result)

    def order_by(self, *fields: str):
        items = list(self)
        for field in reversed(fields):
            descending = field.startswith("-")
            field_name = field[1:] if descending else field
            items.sort(
                key=lambda item: _resolve_attr(item, field_name),
                reverse=descending,
            )
        return FakeQuerySet(items)

    def annotate(self, **kwargs):
        _ = kwargs
        return self

    def first(self):
        return self[0] if self else None

    def exists(self):
        return bool(self)


def _resolve_attr(obj, dotted: str):
    target = obj
    for part in dotted.split("__"):
        target = getattr(target, part)
    return target


def _dataset(*, dataset_id: str, tenant_id: str, slug: str, name: str, status: str = "ACTIVE"):
    now = datetime.now(tz=UTC)
    return SimpleNamespace(
        id=dataset_id,
        tenant_id=tenant_id,
        slug=slug,
        name=name,
        source_type="FILE_UPLOAD",
        status=status,
        description=None,
        owner_team=None,
        tags=[],
        created_at=now,
        updated_at=now,
        archived_at=None,
    )


def _version(*, version_id: str, dataset, version_number: int):
    profile = SimpleNamespace(metrics_json={"row_count": 10, "column_count": 3})
    schema = SimpleNamespace(column_count=3)
    manager_profile = SimpleNamespace(order_by=lambda *args: SimpleNamespace(first=lambda: profile))
    manager_schema = SimpleNamespace(order_by=lambda *args: SimpleNamespace(first=lambda: schema))
    return SimpleNamespace(
        id=version_id,
        dataset=dataset,
        dataset_id=dataset.id,
        version_number=version_number,
        status="READY",
        source_uri="https://store/data.csv?sig=secret",
        content_hash="hash",
        created_at=datetime.now(tz=UTC) + timedelta(minutes=version_number),
        profiles=manager_profile,
        schemas=manager_schema,
    )


def test_dataset_read_service_list_tenant_and_pagination_cap() -> None:
    tenant_id = "tenant-a"
    datasets = FakeQuerySet([
        _dataset(
            dataset_id=f"d-{idx}",
            tenant_id=tenant_id,
            slug=f"s{idx}",
            name=f"N{idx}",
        )
        for idx in range(150)
    ])
    service = DatasetReadService()
    with patch(
        "resource_registry.read.dataset_read_service.Dataset.objects.filter",
        return_value=datasets,
    ):
        results = service.list_datasets(tenant_id=tenant_id, limit=1000, offset=0)
    assert len(results) == PAGINATION_MAX


@pytest.mark.parametrize("search", [None, "Inventory"])
@pytest.mark.parametrize(
    ("roles", "allowed_ids", "enforced_ids", "expected_ids"),
    [
        (("reader",), None, frozenset({"d-3"}), ["d-3"]),
        (("reader",), frozenset({"d-3", "d-4", "d-5"}), None, ["d-3", "d-4", "d-5"]),
        (("reader",), None, frozenset({"d-3", "d-4", "d-5"}), ["d-3", "d-4", "d-5"]),
        (("admin",), frozenset({"d-1"}), frozenset({"d-3", "d-4", "d-5"}), ["d-3", "d-4", "d-5"]),
        (
            ("reader",),
            frozenset({"d-2", "d-3", "d-4"}),
            frozenset({"d-3", "d-4", "d-5"}),
            ["d-3", "d-4"],
        ),
        (("reader",), frozenset(), None, []),
        (("admin",), None, frozenset(), []),
        (("reader",), frozenset({"d-1"}), frozenset({"d-3"}), []),
        (("reader",), None, None, ["d-1", "d-2", "d-3", "d-4", "d-5"]),
        (("admin",), frozenset({"d-5"}), None, ["d-1", "d-2", "d-3", "d-4", "d-5"]),
    ],
)
def test_authorized_dataset_pagination_ignores_inaccessible_records(
    search: str | None,
    roles: tuple[str, ...],
    allowed_ids: frozenset[str] | None,
    enforced_ids: frozenset[str] | None,
    expected_ids: list[str],
) -> None:
    """Paginate authorized inventories without inaccessible records consuming page slots.

    Args:
        search: Optional inventory search term.
        roles: Roles granted to the caller.
        allowed_ids: Optional caller ACL constraint.
        enforced_ids: Optional mandatory Workspace constraint.
        expected_ids: Complete ordered set visible to this caller.
    """
    dataset_ids = {f"d-{index}": str(uuid.UUID(int=index)) for index in range(1, 6)}
    datasets = FakeQuerySet(
        [
            _dataset(
                dataset_id=dataset_ids[f"d-{index}"],
                tenant_id="tenant-a",
                slug=f"inventory-{index}",
                name=f"Inventory {index}",
            )
            for index in range(1, 6)
        ]
    )
    context = ReadAuthorizationContext(
        tenant_id="tenant-a",
        actor="user-1",
        roles=roles,
        allowed_dataset_ids=(
            frozenset(dataset_ids[item] for item in allowed_ids)
            if allowed_ids is not None
            else None
        ),
        enforced_dataset_ids=(
            frozenset(dataset_ids[item] for item in enforced_ids)
            if enforced_ids is not None
            else None
        ),
    )
    facade = AuthorizedDataRegistryReadFacade(dataset_read_service=DatasetReadService())

    with patch(
        "resource_registry.read.dataset_read_service.Dataset.objects.filter",
        return_value=datasets,
    ):
        for offset in (0, 2, 4, 6):
            results = facade.list_datasets(
                context, search=search, status="ACTIVE", limit=2, offset=offset
            )
            assert [result.id for result in results] == [
                dataset_ids[item] for item in expected_ids[offset : offset + 2]
            ]


def test_dataset_read_service_summary_includes_authoritative_sheet_name() -> None:
    tenant_id = "tenant-a"
    dataset = _dataset(
        dataset_id="d-1",
        tenant_id=tenant_id,
        slug="customer-data-customers",
        name="Customer Data — Customers",
    )
    dataset._latest_sheet_name = "Customers"
    service = DatasetReadService()
    with patch(
        "resource_registry.read.dataset_read_service.Dataset.objects.filter",
        return_value=FakeQuerySet([dataset]),
    ):
        results = service.list_datasets(tenant_id=tenant_id, limit=50, offset=0)

    assert results[0].sheet_name == "Customers"


def test_dataset_read_service_search_slug_name_and_tenant_isolation() -> None:
    tenant_id = "tenant-a"
    service = DatasetReadService()
    filtered = FakeQuerySet(
        [
            _dataset(dataset_id="d-1", tenant_id=tenant_id, slug="sales-data", name="Sales Data"),
            _dataset(dataset_id="d-2", tenant_id=tenant_id, slug="ops-data", name="Ops"),
        ],
    )
    root_qs = MagicMock()
    root_qs.filter.return_value = filtered
    with patch(
        "resource_registry.read.dataset_read_service.Dataset.objects.filter",
        return_value=root_qs,
    ):
        results = service.search_datasets(tenant_id=tenant_id, query="sales")
    slugs = {item.slug for item in results}
    assert slugs == {"ops-data", "sales-data"}


@pytest.mark.django_db
def test_dataset_read_service_search_casts_profile_row_count_from_json() -> None:
    """Search datasets without JSONB/integer type errors in relevance ranking."""
    tenant_id = uuid.uuid4()
    dataset = Dataset.objects.create(
        tenant_id=tenant_id,
        slug="financial-audiences",
        name="Financial Audiences",
        source_type=DatasetSourceType.FILE_UPLOAD,
        status=DatasetStatus.ACTIVE,
        tags=["audience"],
    )
    _map_dataset_to_workspace(dataset, tenant_id)
    version = DatasetVersion.objects.create(
        dataset=dataset,
        version_number=1,
        source_uri="local://financial-audiences.parquet",
        content_hash="hash-financial-audiences",
        status=DatasetVersionStatus.READY,
    )
    DatasetProfile.objects.create(
        dataset_version=version,
        profile_type=ProfileType.BASIC,
        profile_hash="profile-hash-financial-audiences",
        metrics_json={"row_count": 250, "column_count": 3},
    )

    results = DatasetReadService().search_datasets(
        tenant_id=str(tenant_id),
        query="financial audiences",
        limit=10,
    )

    assert [result.slug for result in results] == ["financial-audiences"]


@pytest.mark.django_db
def test_dataset_read_service_search_matches_hyphenated_slug_tokens() -> None:
    """Match human search phrases against slug tokens separated by punctuation."""
    tenant_id = uuid.uuid4()
    dataset = Dataset.objects.create(
        tenant_id=tenant_id,
        slug="epsilon-export-raw-online-financial-audiences",
        name="Epsilon Export Raw — Online Financial Audiences",
        source_type=DatasetSourceType.FILE_UPLOAD,
        status=DatasetStatus.ACTIVE,
        tags=[],
    )
    _map_dataset_to_workspace(dataset, tenant_id)
    version = DatasetVersion.objects.create(
        dataset=dataset,
        version_number=1,
        source_uri="local://epsilon-export-raw-online-financial-audiences.parquet",
        content_hash="hash-online-financial-audiences",
        status=DatasetVersionStatus.READY,
    )
    DatasetProfile.objects.create(
        dataset_version=version,
        profile_type=ProfileType.BASIC,
        profile_hash="profile-hash-online-financial-audiences",
        metrics_json={"row_count": 500, "column_count": 12},
    )

    results = DatasetReadService().search_datasets(
        tenant_id=str(tenant_id),
        query="financial audiences",
        limit=10,
    )

    assert [result.slug for result in results] == [
        "epsilon-export-raw-online-financial-audiences",
    ]


@pytest.mark.django_db
def test_dataset_read_service_search_falls_back_to_related_pluralized_tokens() -> None:
    """Return related partial matches for exploratory dataset discovery phrases."""
    tenant_id = uuid.uuid4()
    audience_dataset = Dataset.objects.create(
        tenant_id=tenant_id,
        slug="epsilon-export-raw-online-financial-audiences",
        name="Epsilon Export Raw — Online Financial Audiences",
        source_type=DatasetSourceType.FILE_UPLOAD,
        status=DatasetStatus.ACTIVE,
        tags=[],
    )
    index_dataset = Dataset.objects.create(
        tenant_id=tenant_id,
        slug="epsilon-export-raw-index-report",
        name="Epsilon Export Raw — Index Report",
        source_type=DatasetSourceType.FILE_UPLOAD,
        status=DatasetStatus.ACTIVE,
        tags=[],
    )
    unrelated_dataset = Dataset.objects.create(
        tenant_id=tenant_id,
        slug="epsilon-export-raw-market-trends",
        name="Epsilon Export Raw — Market Trends",
        source_type=DatasetSourceType.FILE_UPLOAD,
        status=DatasetStatus.ACTIVE,
        tags=[],
    )
    _map_dataset_to_workspace(audience_dataset, tenant_id)
    index_dataset.organization = audience_dataset.organization
    index_dataset.workspace = audience_dataset.workspace
    index_dataset.save(update_fields=["organization", "workspace"])
    unrelated_dataset.organization = audience_dataset.organization
    unrelated_dataset.workspace = audience_dataset.workspace
    unrelated_dataset.save(update_fields=["organization", "workspace"])
    row_counts_by_slug = {
        audience_dataset.slug: 500,
        index_dataset.slug: 25_000,
        unrelated_dataset.slug: 1_000,
    }
    for dataset in (audience_dataset, index_dataset, unrelated_dataset):
        version = DatasetVersion.objects.create(
            dataset=dataset,
            version_number=1,
            source_uri=f"local://{dataset.slug}.parquet",
            content_hash=f"hash-{dataset.slug}",
            status=DatasetVersionStatus.READY,
        )
        DatasetProfile.objects.create(
            dataset_version=version,
            profile_type=ProfileType.BASIC,
            profile_hash=f"profile-hash-{dataset.slug}",
            metrics_json={"row_count": row_counts_by_slug[dataset.slug], "column_count": 12},
        )

    results = DatasetReadService().search_datasets(
        tenant_id=str(tenant_id),
        query="audience indexes",
        limit=10,
    )

    slugs = {result.slug for result in results}
    assert results[0].slug == "epsilon-export-raw-index-report"
    assert "epsilon-export-raw-online-financial-audiences" in slugs
    assert "epsilon-export-raw-index-report" in slugs
    assert "epsilon-export-raw-market-trends" not in slugs


def test_version_read_service_latest_and_ordering() -> None:
    dataset = _dataset(dataset_id="d-1", tenant_id="tenant-a", slug="sales", name="Sales")
    versions = FakeQuerySet(
        [
            _version(version_id="v1", dataset=dataset, version_number=1),
            _version(version_id="v2", dataset=dataset, version_number=2),
        ],
    )
    service = DatasetVersionReadService()
    with patch(
        "resource_registry.read.version_read_service.DatasetVersion.objects.filter",
        return_value=versions,
    ):
        listed = service.list_versions(tenant_id="tenant-a", dataset_id="d-1")
        latest = service.get_latest_version(tenant_id="tenant-a", dataset_id="d-1")
    assert listed[0].version_number == LATEST_VERSION_NUMBER
    assert latest.version_number == LATEST_VERSION_NUMBER
    assert "sig=" not in listed[0].source_uri


def test_version_read_service_tenant_isolation_not_found() -> None:
    service = DatasetVersionReadService()
    with patch(
        "resource_registry.read.version_read_service.DatasetVersion.objects.filter",
        return_value=FakeQuerySet([]),
    ):
        with pytest.raises(DatasetVersionNotFoundError):
            service.get_latest_version(tenant_id="tenant-a", dataset_id="d-1")


def test_artifact_read_service_sanitizes_uri_and_metadata() -> None:
    dataset = _dataset(dataset_id="d-1", tenant_id="tenant-a", slug="sales", name="Sales")
    version = SimpleNamespace(id="v1", dataset=dataset)
    artifact = SimpleNamespace(
        id="a1",
        dataset_version=version,
        dataset_version_id="v1",
        artifact_role="PRIMARY",
        uri="https://blob/path/file.parquet?sig=secret&other=1",
        format="PARQUET",
        size_bytes=10,
        checksum="abc",
        storage_provider="azure_blob",
        lifecycle_status="ACTIVE",
        created_at=datetime.now(tz=UTC),
        metadata_json={"preview": [{"id": 1}], "safe": "ok"},
    )
    service = DatasetArtifactReadService()
    with patch(
        "resource_registry.read.artifact_read_service.DatasetArtifact.objects.filter",
        return_value=FakeQuerySet([artifact]),
    ):
        result = service.get_artifact(tenant_id="tenant-a", dataset_id="d-1", artifact_id="a1")
    assert "sig=" not in result.uri
    assert "preview" not in result.metadata_json


def test_schema_and_profile_services_exclude_raw_preview_fields() -> None:
    dataset = _dataset(dataset_id="d-1", tenant_id="tenant-a", slug="sales", name="Sales")
    version = SimpleNamespace(id="v1", dataset=dataset)
    schema = SimpleNamespace(
        id="s1",
        dataset_version=version,
        dataset_version_id="v1",
        schema_hash="h",
        column_count=2,
        schema_json=[{"name": "id", "type": "BIGINT", "preview": [1]}],
        created_at=datetime.now(tz=UTC),
    )
    profile = SimpleNamespace(
        id="p1",
        dataset_version=version,
        dataset_version_id="v1",
        profile_type="BASIC",
        profile_hash="h",
        metrics_json={"row_count": 10, "preview": [{"id": 1}]},
        computed_at=datetime.now(tz=UTC),
    )
    schema_service = DatasetSchemaReadService()
    profile_service = DatasetProfileReadService()
    with (
        patch(
            "resource_registry.read.schema_read_service.DatasetSchema.objects.filter",
            return_value=FakeQuerySet([schema]),
        ),
        patch(
            "resource_registry.read.profile_read_service.DatasetProfile.objects.filter",
            return_value=FakeQuerySet([profile]),
        ),
    ):
        schema_result = schema_service.get_schema_for_version(
            tenant_id="tenant-a",
            dataset_id="d-1",
            version_id="v1",
        )
        profile_result = profile_service.get_profile_for_version(
            tenant_id="tenant-a",
            dataset_id="d-1",
            version_id="v1",
        )
    assert "preview" not in schema_result.schema_json[0]
    assert "preview" not in profile_result.metrics_json


def test_job_read_service_filters_and_ordering() -> None:
    dataset = _dataset(dataset_id="d-1", tenant_id="tenant-a", slug="sales", name="Sales")
    job_old = SimpleNamespace(
        id="j1",
        dataset=dataset,
        dataset_id="d-1",
        job_type="SYNC",
        status="FAILED",
        provider="azure",
        provider_job_ref="r1",
        retry_count=1,
        error_code="ERR",
        requested_at=datetime.now(tz=UTC),
        started_at=None,
        finished_at=None,
        updated_at=datetime.now(tz=UTC),
        output_dataset_version_id=None,
    )
    job_new = SimpleNamespace(
        **{
            **job_old.__dict__,
            "id": "j2",
            "requested_at": datetime.now(tz=UTC) + timedelta(minutes=1),
            "status": "SUCCEEDED",
        },
    )
    service = ExternalDatasetJobReadService()
    with patch(
        "resource_registry.read.job_read_service.ExternalDatasetJob.objects.filter",
        return_value=FakeQuerySet([job_old, job_new]),
    ):
        results = service.list_jobs(tenant_id="tenant-a", status="SUCCEEDED")
    assert results[0].id == "j2"


def test_dataset_context_read_service_returns_safe_context() -> None:
    dataset_service = MagicMock()
    version_service = MagicMock()
    schema_service = MagicMock()
    profile_service = MagicMock()
    artifact_service = MagicMock()
    dataset_service.get_dataset.return_value = SimpleNamespace(id="d1")
    version_service.list_versions.return_value = [SimpleNamespace(id="v1")]
    schema_service.get_schema_for_version.return_value = SimpleNamespace(id="s1")
    profile_service.get_profile_for_version.return_value = SimpleNamespace(id="p1")
    artifact_service.list_artifacts.return_value = [SimpleNamespace(id="a1")]
    resolver = MagicMock()
    resolver.resolve_with_provenance.return_value = SimpleNamespace(
        provenance=SimpleNamespace(
            policy_key="artifact_materialization_default",
            source="SYSTEM_DEFAULT",
            revision=None,
            rules_hash="hash",
            resolution_path=["tenant_default", "system_default"],
        ),
    )
    with patch(
        "resource_registry.read.dataset_context_read_service.Dataset.objects.filter",
    ) as mocked_filter:
        mocked_filter.return_value.first.return_value = SimpleNamespace(id="d1")
        service = DatasetContextReadService(
            dataset_read_service=dataset_service,
            version_read_service=version_service,
            schema_read_service=schema_service,
            profile_read_service=profile_service,
            artifact_read_service=artifact_service,
            materialization_policy_resolver=resolver,
        )
        context = service.get_dataset_context(tenant_id="tenant-a", dataset_id="d1")

    assert context.dataset.id == "d1"
    assert context.latest_version.id == "v1"
    assert context.schema.id == "s1"
    assert context.profile.id == "p1"
    assert context.artifact.id == "a1"
    assert context.policy_provenance is not None


def test_dataset_read_service_list_applies_relevance_ranking_order() -> None:
    service = DatasetReadService()
    queryset = MagicMock()
    annotated = MagicMock()
    queryset.annotate.return_value = annotated
    annotated.annotate.return_value = annotated
    annotated.order_by.return_value = queryset
    queryset.__getitem__.return_value = []
    with patch(
        "resource_registry.read.dataset_read_service.Dataset.objects.filter",
        return_value=queryset,
    ):
        _ = service.list_datasets(
            tenant_id="tenant-a",
            relevance_query="sales",
            limit=10,
            offset=0,
        )
    queryset.annotate.assert_called()
    annotated.order_by.assert_called_once()


def test_workbook_context_read_service_compacts_sheet_context() -> None:
    tenant_id = "tenant-a"
    group = SimpleNamespace(
        id="group-1",
        tenant_id=tenant_id,
        status="COMPLETED",
        source_file_name="sales.xlsx",
        source_uri="azure://datasets/sales.xlsx",
        source_hash="hash-1",
        total_sheets=1,
        total_created=1,
        total_deduplicated=0,
        total_skipped=0,
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
    )
    dataset = SimpleNamespace(
        id="d-1",
        tenant_id=tenant_id,
        slug="sales-summary",
        name="Sales Summary",
        tags=["finance"],
    )
    version = SimpleNamespace(
        id="v-1",
        dataset_id="d-1",
        version_number=1,
        created_at=datetime.now(tz=UTC),
    )
    profile = SimpleNamespace(
        dataset_version_id="v-1",
        metrics_json={"row_count": 100, "column_count": 4},
        computed_at=datetime.now(tz=UTC),
    )
    schema = SimpleNamespace(
        dataset_version_id="v-1",
        schema_json=[{"name": "region"}, {"name": "amount"}],
        created_at=datetime.now(tz=UTC),
    )
    artifact = SimpleNamespace(id="a-1", dataset_version_id="v-1", sheet_name="Summary")

    service = WorkbookContextReadService()
    with (
        patch(
            "resource_registry.read.workbook_context_read_service.WorkbookIngestionGroup.objects.filter",
        ) as group_filter,
        patch(
            "resource_registry.read.workbook_context_read_service.Dataset.objects.filter",
            side_effect=[FakeQuerySet([]), FakeQuerySet([dataset])],
        ),
        patch(
            "resource_registry.read.workbook_context_read_service.DatasetVersion.objects.filter",
            return_value=FakeQuerySet([version]),
        ),
        patch(
            "resource_registry.read.workbook_context_read_service.DatasetProfile.objects.filter",
            return_value=FakeQuerySet([profile]),
        ),
        patch(
            "resource_registry.read.workbook_context_read_service.DatasetSchema.objects.filter",
            return_value=FakeQuerySet([schema]),
        ),
        patch(
            "resource_registry.read.workbook_context_read_service.DatasetArtifact.objects.filter",
            return_value=FakeQuerySet([artifact]),
        ),
    ):
        group_filter.return_value.first.return_value = group
        context = service.get_workbook_context(tenant_id=tenant_id, workbook_group_id="group-1")

    assert context.workbook_group_id == "group-1"
    assert context.total_sheets == 1
    assert len(context.sheets) == 1
    assert context.sheets[0].sheet_name == "Summary"
    assert context.sheets[0].schema_columns == ["region", "amount"]


def test_workbook_context_read_service_hides_group_without_visible_datasets() -> None:
    """Reject workbook context when all destination Datasets are archived or unavailable."""
    group = SimpleNamespace(
        id="group-archived",
        tenant_id="tenant-a",
        status="COMPLETED",
        source_file_name="archived.xlsx",
        source_uri="azure://datasets/archived.xlsx",
        source_hash="archived-hash",
        total_sheets=1,
        total_created=1,
        total_deduplicated=0,
        total_skipped=0,
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
    )
    service = WorkbookContextReadService()

    with (
        patch(
            "resource_registry.read.workbook_context_read_service.WorkbookIngestionGroup.objects.filter",
        ) as group_filter,
        patch(
            "resource_registry.read.workbook_context_read_service.Dataset.objects.filter",
            side_effect=[FakeQuerySet([]), FakeQuerySet([])],
        ),
    ):
        group_filter.return_value.first.return_value = group

        with pytest.raises(DataRegistryReadError, match="Workbook ingestion group not found"):
            service.get_workbook_context(tenant_id="tenant-a", workbook_group_id="group-archived")


def test_workbook_context_read_service_allows_fully_scoped_workbook_context() -> None:
    """Return workbook context when the reader is authorized for every visible Dataset."""
    tenant_id = "tenant-a"
    group = SimpleNamespace(
        id="group-scoped",
        tenant_id=tenant_id,
        status="COMPLETED",
        source_file_name="scoped.xlsx",
        source_uri="azure://datasets/scoped.xlsx",
        source_hash="scoped-hash",
        total_sheets=1,
        total_created=1,
        total_deduplicated=0,
        total_skipped=0,
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
    )
    dataset = SimpleNamespace(
        id="dataset-allowed",
        tenant_id=tenant_id,
        slug="allowed-sheet",
        name="Allowed Sheet",
        tags=[],
    )
    dataset_queryset = MagicMock()
    dataset_queryset.filter.return_value = FakeQuerySet([dataset])
    dataset_queryset.exclude.return_value.exists.return_value = False
    archived_dataset_queryset = MagicMock()
    archived_dataset_queryset.exists.return_value = False
    service = WorkbookContextReadService()

    with (
        patch(
            "resource_registry.read.workbook_context_read_service.WorkbookIngestionGroup.objects.filter",
        ) as group_filter,
        patch(
            "resource_registry.read.workbook_context_read_service.Dataset.objects.filter",
            side_effect=[archived_dataset_queryset, dataset_queryset],
        ),
        patch(
            "resource_registry.read.workbook_context_read_service.DatasetVersion.objects.filter",
            return_value=FakeQuerySet([]),
        ),
        patch(
            "resource_registry.read.workbook_context_read_service.DatasetProfile.objects.filter",
            return_value=FakeQuerySet([]),
        ),
        patch(
            "resource_registry.read.workbook_context_read_service.DatasetSchema.objects.filter",
            return_value=FakeQuerySet([]),
        ),
        patch(
            "resource_registry.read.workbook_context_read_service.DatasetArtifact.objects.filter",
            return_value=FakeQuerySet([]),
        ),
    ):
        group_filter.return_value.first.return_value = group
        context = service.get_workbook_context(
            tenant_id=tenant_id,
            workbook_group_id="group-scoped",
            allowed_dataset_ids=frozenset({"dataset-allowed"}),
        )

    dataset_queryset.filter.assert_called_once_with(id__in=frozenset({"dataset-allowed"}))
    assert [sheet.dataset_id for sheet in context.sheets] == ["dataset-allowed"]


def test_workbook_context_read_service_hides_partially_scoped_workbook_context() -> None:
    """Reject aggregate workbook metadata when any visible Dataset is not authorized."""
    group = SimpleNamespace(id="group-partial", tenant_id="tenant-a")
    dataset_queryset = MagicMock()
    dataset_queryset.exclude.return_value.exists.return_value = True
    archived_dataset_queryset = MagicMock()
    archived_dataset_queryset.exists.return_value = False
    service = WorkbookContextReadService()

    with (
        patch(
            "resource_registry.read.workbook_context_read_service.WorkbookIngestionGroup.objects.filter",
        ) as group_filter,
        patch(
            "resource_registry.read.workbook_context_read_service.Dataset.objects.filter",
            side_effect=[archived_dataset_queryset, dataset_queryset],
        ),
    ):
        group_filter.return_value.first.return_value = group

        with pytest.raises(DataRegistryReadError, match="Workbook ingestion group not found"):
            service.get_workbook_context(
                tenant_id="tenant-a",
                workbook_group_id="group-partial",
                allowed_dataset_ids=frozenset({"dataset-allowed"}),
            )

    dataset_queryset.exclude.assert_called_once_with(id__in=frozenset({"dataset-allowed"}))


def test_workbook_context_read_service_hides_group_with_archived_dataset() -> None:
    """Reject workbook context when any Dataset in the group is archived."""
    group = SimpleNamespace(id="group-mixed", tenant_id="tenant-a")
    archived_dataset_queryset = MagicMock()
    archived_dataset_queryset.exists.return_value = True
    service = WorkbookContextReadService()

    with (
        patch(
            "resource_registry.read.workbook_context_read_service.WorkbookIngestionGroup.objects.filter",
        ) as group_filter,
        patch(
            "resource_registry.read.workbook_context_read_service.Dataset.objects.filter",
            return_value=archived_dataset_queryset,
        ),
    ):
        group_filter.return_value.first.return_value = group

        with pytest.raises(DataRegistryReadError, match="Workbook ingestion group not found"):
            service.get_workbook_context(tenant_id="tenant-a", workbook_group_id="group-mixed")
