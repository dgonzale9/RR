from __future__ import annotations

import time
from collections import namedtuple
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import duckdb
import pytest
from django.test import override_settings
from openpyxl import Workbook

import resource_registry.services.dataset_query_executor as dataset_query_executor_module
from resource_registry.read.dtos import (
    DatasetArtifactSummary,
    DatasetSchemaSummary,
    DatasetVersionSummary,
)
from resource_registry.read.exceptions import ArtifactNotFoundError, DatasetNotFoundError
from resource_registry.read_auth.context import ReadAuthorizationContext
from resource_registry.read_auth.query_columns import get_queryable_schema_column_names
from resource_registry.services.dataset_query_executor import (
    DatasetQueryExecutor,
    DuckDbQueryExecutionError,
    QueryTimeoutError,
)
from resource_registry.services.dataset_query_resource_guard import DatasetQueryResourceGuard
from resource_registry.services.materialized_dataset_cache import MaterializedDatasetCache
from resource_registry.storage.read_adapters import ArtifactReadAdapter, TemporaryArtifact

EXPECTED_TWO_ROWS = 2
EXPECTED_STATUS_TOO_MANY_REQUESTS = 429


def _build_executor(  # noqa: PLR0913
    *,
    artifact_path: Path,
    artifact_format: str = "PARQUET",
    execution_timeout_seconds: float = 1.0,
    max_artifact_bytes_sync: int | None = None,
    temp_min_free_bytes: int = 0,
    temp_root_directory: Path | None = None,
    resource_guard: DatasetQueryResourceGuard | None = None,
    metadata_json: dict[str, object] | None = None,
    artifacts: list[DatasetArtifactSummary] | None = None,
    adapters: list[ArtifactReadAdapter] | None = None,
    materialized_dataset_cache: MaterializedDatasetCache | None = None,
) -> tuple[DatasetQueryExecutor, MagicMock, MagicMock]:
    dataset_read_service = MagicMock()
    dataset_read_service.get_dataset.return_value = SimpleNamespace(id="dataset-1")

    version_read_service = MagicMock()
    version_read_service.get_latest_version.return_value = DatasetVersionSummary(
        id="version-latest",
        dataset_id="dataset-1",
        tenant_id="tenant-1",
        version_number=1,
        status="READY",
        source_uri=str(artifact_path),
        row_count=2,
        column_count=3,
        created_at=datetime.utcnow().isoformat(),
    )
    version_read_service.get_version.return_value = DatasetVersionSummary(
        id="version-specific",
        dataset_id="dataset-1",
        tenant_id="tenant-1",
        version_number=3,
        status="READY",
        source_uri=str(artifact_path),
        row_count=2,
        column_count=3,
        created_at=datetime.utcnow().isoformat(),
    )

    schema_read_service = MagicMock()
    schema_read_service.get_schema_for_version.return_value = DatasetSchemaSummary(
        id="schema-1",
        dataset_version_id="version-latest",
        tenant_id="tenant-1",
        schema_hash="hash",
        column_count=3,
        schema_json=[
            {"name": "placement", "type": "VARCHAR"},
            {"name": "spend", "type": "DOUBLE"},
            {"name": "impressions", "type": "BIGINT"},
        ],
        created_at=datetime.utcnow().isoformat(),
    )

    artifact_read_service = MagicMock()
    artifact_read_service.list_artifacts.return_value = artifacts or [
        DatasetArtifactSummary(
            id="artifact-1",
            dataset_version_id="version-latest",
            tenant_id="tenant-1",
            artifact_role="PRIMARY",
            uri=str(artifact_path),
            format=artifact_format,
            size_bytes=artifact_path.stat().st_size,
            checksum=None,
            storage_provider="local",
            lifecycle_status="ACTIVE",
            created_at=datetime.utcnow().isoformat(),
            metadata_json=metadata_json or {"is_canonical": True},
        ),
    ]

    executor = DatasetQueryExecutor(
        dataset_read_service=dataset_read_service,
        version_read_service=version_read_service,
        schema_read_service=schema_read_service,
        artifact_read_service=artifact_read_service,
        execution_timeout_seconds=execution_timeout_seconds,
        max_artifact_bytes_sync=max_artifact_bytes_sync,
        temp_min_free_bytes=temp_min_free_bytes,
        temp_root_directory=temp_root_directory,
        resource_guard=resource_guard,
        adapters=adapters,
        materialized_dataset_cache=materialized_dataset_cache,
    )
    return executor, dataset_read_service, version_read_service


@pytest.fixture
def sample_dataset_path(tmp_path: Path) -> Path:
    file_path = tmp_path / "sample.parquet"
    with duckdb.connect(database=":memory:") as connection:
        connection.execute(
            """
            CREATE TABLE sample_data AS
            SELECT 'A' AS placement, 100.0 AS spend, 1000 AS impressions
            UNION ALL SELECT 'B', 50.0, 500
            """
        )
        connection.execute("COPY sample_data TO ? (FORMAT PARQUET)", [str(file_path)])
    return file_path


@pytest.fixture
def searchable_dataset_path(tmp_path: Path) -> Path:
    file_path = tmp_path / "searchable.parquet"
    with duckdb.connect(database=":memory:") as connection:
        connection.execute(
            "CREATE TABLE searchable_data AS "
            "SELECT 'Online_Financial\\Audiences%' AS attribute_name"
        )
        connection.execute("COPY searchable_data TO ? (FORMAT PARQUET)", [str(file_path)])
    return file_path


@pytest.fixture
def sample_xlsx_and_canonical_paths(tmp_path: Path) -> tuple[Path, Path]:
    xlsx_path = tmp_path / "sample.xlsx"
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Sheet1"
    worksheet.append(["placement", "spend", "impressions"])
    worksheet.append(["A", 100.0, 1000])
    worksheet.append(["B", 50.0, 500])
    workbook.save(xlsx_path)
    workbook.close()

    canonical_parquet_path = tmp_path / "sample-canonical.parquet"
    with duckdb.connect(database=":memory:") as connection:
        connection.execute(
            """
            CREATE TABLE sample_data AS
            SELECT 'A' AS placement, 100.0 AS spend, 1000 AS impressions
            UNION ALL SELECT 'B', 50.0, 500
            """
        )
        connection.execute("COPY sample_data TO ? (FORMAT PARQUET)", [str(canonical_parquet_path)])
    return xlsx_path, canonical_parquet_path


@pytest.fixture
def context() -> ReadAuthorizationContext:
    return ReadAuthorizationContext(tenant_id="tenant-1", roles=("admin",))


class _CountingLocalAdapter:
    def __init__(self, source_path: Path) -> None:
        self.source_path = source_path
        self.materialize_calls = 0

    def can_handle(self, uri: str) -> bool:
        return uri == str(self.source_path)

    def materialize_to_temp(
        self,
        uri: str,
        *,
        temp_directory: str | Path | None = None,
    ) -> TemporaryArtifact:
        _ = uri
        self.materialize_calls += 1
        target_directory = (
            Path(temp_directory) if temp_directory is not None else self.source_path.parent
        )
        target_directory.mkdir(parents=True, exist_ok=True)
        target_path = target_directory / f"materialized-{self.materialize_calls}.parquet"
        target_path.write_bytes(self.source_path.read_bytes())
        return TemporaryArtifact(local_path=str(target_path), source_uri=str(self.source_path))


def test_safe_select_execution_returns_rows(
    sample_dataset_path: Path,
    context: ReadAuthorizationContext,
) -> None:
    executor, _, _ = _build_executor(artifact_path=sample_dataset_path)

    result = executor.execute_query(
        context=context,
        dataset_id="dataset-1",
        sql="SELECT placement, spend FROM dataset LIMIT 2",
    )

    assert result["columns"] == ["placement", "spend"]
    assert result["row_count"] == EXPECTED_TWO_ROWS
    assert result["truncated"] is False
    assert result["version_id"] == "version-latest"


def test_dataset_query_executor_uses_cached_canonical_artifact(
    sample_dataset_path: Path,
    context: ReadAuthorizationContext,
    tmp_path: Path,
) -> None:
    adapter = _CountingLocalAdapter(sample_dataset_path)
    cache = MaterializedDatasetCache(
        cache_directory=tmp_path / "dataset-cache",
        ttl_seconds=600,
        max_bytes=1024 * 1024,
    )
    executor, _, _ = _build_executor(
        artifact_path=sample_dataset_path,
        adapters=[adapter],
        materialized_dataset_cache=cache,
    )

    first_result = executor.execute_query(
        context=context,
        dataset_id="dataset-1",
        sql="SELECT placement FROM dataset LIMIT 1",
    )
    second_result = executor.execute_query(
        context=context,
        dataset_id="dataset-1",
        sql="SELECT placement FROM dataset LIMIT 1",
    )

    assert first_result["row_count"] == 1
    assert second_result["row_count"] == 1
    assert adapter.materialize_calls == 1


def test_dataset_cache_materialization_failure_returns_stable_error(
    sample_dataset_path: Path,
    context: ReadAuthorizationContext,
    tmp_path: Path,
) -> None:
    class _FailingAdapter:
        def can_handle(self, uri: str) -> bool:
            return uri == str(sample_dataset_path)

        def materialize_to_temp(
            self,
            uri: str,
            *,
            temp_directory: str | Path | None = None,
        ) -> TemporaryArtifact:
            _ = uri, temp_directory
            raise RuntimeError("boom")

    cache = MaterializedDatasetCache(
        cache_directory=tmp_path / "dataset-cache-failure",
        ttl_seconds=600,
        max_bytes=1024 * 1024,
    )
    executor, _, _ = _build_executor(
        artifact_path=sample_dataset_path,
        adapters=[_FailingAdapter()],
        materialized_dataset_cache=cache,
    )

    result = executor.execute_query(
        context=context,
        dataset_id="dataset-1",
        sql="SELECT placement FROM dataset LIMIT 1",
    )

    assert result == {
        "success": False,
        "status": 500,
        "code": "dataset_cache_materialization_failed",
        "error": "Unable to prepare dataset for query execution.",
    }


def test_dataset_cache_storage_exceeded_returns_507(
    sample_dataset_path: Path,
    context: ReadAuthorizationContext,
    tmp_path: Path,
) -> None:
    adapter = _CountingLocalAdapter(sample_dataset_path)
    cache = MaterializedDatasetCache(
        cache_directory=tmp_path / "dataset-cache-small",
        ttl_seconds=600,
        max_bytes=1,
    )
    executor, _, _ = _build_executor(
        artifact_path=sample_dataset_path,
        adapters=[adapter],
        materialized_dataset_cache=cache,
    )

    result = executor.execute_query(
        context=context,
        dataset_id="dataset-1",
        sql="SELECT placement FROM dataset LIMIT 1",
    )

    assert result == {
        "success": False,
        "status": 507,
        "code": "dataset_cache_storage_exceeded",
        "error": "Dataset cache storage limit exceeded.",
    }


def test_query_executor_reads_canonical_parquet_artifact_when_source_is_excel(
    sample_xlsx_and_canonical_paths: tuple[Path, Path],
    context: ReadAuthorizationContext,
) -> None:
    source_xlsx_path, canonical_parquet_path = sample_xlsx_and_canonical_paths
    executor, _, _ = _build_executor(
        artifact_path=canonical_parquet_path,
        artifacts=[
            DatasetArtifactSummary(
                id="artifact-source",
                dataset_version_id="version-latest",
                tenant_id="tenant-1",
                artifact_role="PRIMARY",
                uri=str(source_xlsx_path),
                format="XLSX",
                size_bytes=source_xlsx_path.stat().st_size,
                checksum=None,
                storage_provider="local",
                lifecycle_status="ACTIVE",
                created_at=datetime.utcnow().isoformat(),
                metadata_json={"is_canonical": False},
            ),
            DatasetArtifactSummary(
                id="artifact-canonical",
                dataset_version_id="version-latest",
                tenant_id="tenant-1",
                artifact_role="SNAPSHOT",
                uri=str(canonical_parquet_path),
                format="PARQUET",
                size_bytes=canonical_parquet_path.stat().st_size,
                checksum=None,
                storage_provider="local",
                lifecycle_status="ACTIVE",
                created_at=datetime.utcnow().isoformat(),
                metadata_json={"is_canonical": True},
            ),
        ],
    )

    result = executor.execute_query(
        context=context,
        dataset_id="dataset-1",
        sql="SELECT placement, spend FROM dataset LIMIT 2",
    )

    assert result["columns"] == ["placement", "spend"]
    assert result["row_count"] == EXPECTED_TWO_ROWS


def test_limit_required(sample_dataset_path: Path, context: ReadAuthorizationContext) -> None:
    executor, _, _ = _build_executor(artifact_path=sample_dataset_path)

    result = executor.execute_query(
        context=context,
        dataset_id="dataset-1",
        sql="SELECT placement FROM dataset",
    )

    assert result == {
        "success": False,
        "status": 400,
        "code": "unsafe_sql",
        "error": (
            "Only safe SELECT statements against the registered dataset table with LIMIT "
            "are allowed."
        ),
    }


def test_select_star_rejected(sample_dataset_path: Path, context: ReadAuthorizationContext) -> None:
    executor, _, _ = _build_executor(artifact_path=sample_dataset_path)

    result = executor.execute_query(
        context=context,
        dataset_id="dataset-1",
        sql="SELECT * FROM dataset LIMIT 10",
    )

    assert result["code"] == "unsafe_sql"


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO dataset VALUES ('A', 1, 1) LIMIT 1",
        "UPDATE dataset SET spend = 0 LIMIT 1",
        "DELETE FROM dataset LIMIT 1",
        "DROP TABLE dataset LIMIT 1",
        "COPY dataset TO '/tmp/x.csv' LIMIT 1",
        "ATTACH 'db.duckdb' LIMIT 1",
    ],
)
def test_forbidden_statements_rejected(
    sample_dataset_path: Path,
    context: ReadAuthorizationContext,
    sql: str,
) -> None:
    executor, _, _ = _build_executor(artifact_path=sample_dataset_path)

    result = executor.execute_query(context=context, dataset_id="dataset-1", sql=sql)

    assert result["code"] == "unsafe_sql"


@pytest.mark.parametrize("validator_mode", ("sqlglot", "legacy"))
@pytest.mark.parametrize("literal", ("Update", "Union", "Delete"))
def test_safe_literals_containing_forbidden_keywords_are_allowed(
    sample_dataset_path: Path,
    context: ReadAuthorizationContext,
    validator_mode: str,
    literal: str,
) -> None:
    with override_settings(RESOURCE_REGISTRY_SQL_VALIDATOR_MODE=validator_mode):
        executor, _, _ = _build_executor(artifact_path=sample_dataset_path)
        result = executor.execute_query(
            context=context,
            dataset_id="dataset-1",
            sql=f"SELECT placement FROM dataset WHERE placement = '{literal}' LIMIT 1",
        )

    assert result["row_count"] == 0


def test_unknown_column_rejected(
    sample_dataset_path: Path,
    context: ReadAuthorizationContext,
) -> None:
    executor, _, _ = _build_executor(artifact_path=sample_dataset_path)

    result = executor.execute_query(
        context=context,
        dataset_id="dataset-1",
        sql="SELECT unknown_col FROM dataset LIMIT 10",
    )

    assert result == {
        "success": False,
        "status": 400,
        "code": "invalid_column",
        "error": "Unknown column 'unknown_col'.",
    }


@pytest.mark.parametrize(
    "sql",
    (
        "SELECT LOWER(hidden_column) FROM dataset LIMIT 1",
        "SELECT placement FROM dataset WHERE hidden_column = 'x' LIMIT 1",
        "SELECT placement FROM dataset ORDER BY hidden_column LIMIT 1",
        "SELECT COUNT(*) FROM dataset GROUP BY hidden_column LIMIT 1",
        "SELECT COUNT(*) FROM dataset HAVING SUM(hidden_column) > 0 LIMIT 1",
    ),
)
def test_sql_validator_rejects_hidden_columns_in_every_clause(sql: str) -> None:
    validator = dataset_query_executor_module._SafeSqlValidator(max_returned_rows=100)
    assert validator.validate_sql(sql) is None
    assert validator.validate_known_columns(sql, {"placement"}) == "hidden_column"


def test_masked_schema_columns_are_not_queryable() -> None:
    assert get_queryable_schema_column_names(
        (
            {"name": "placement", "type": "VARCHAR"},
            {"name": "secret", "type": "REDACTED"},
        ),
    ) == frozenset({"placement"})


def test_non_admin_query_uses_filtered_schema_for_expression_columns(
    sample_dataset_path: Path,
) -> None:
    executor, _, _ = _build_executor(artifact_path=sample_dataset_path)
    filtered_schema = SimpleNamespace(
        schema_json=[
            {"name": "placement", "type": "VARCHAR"},
            {"name": "spend", "type": "REDACTED"},
        ],
    )
    with patch(
        "resource_registry.services.dataset_query_executor.AuthorizedDataRegistryReadFacade"
    ) as facade_class:
        facade_class.return_value.get_schema.return_value = filtered_schema
        result = executor.execute_query(
            context=ReadAuthorizationContext(tenant_id="tenant-1", roles=("restricted_reader",)),
            dataset_id="dataset-1",
            sql="SELECT LOWER(CAST(spend AS VARCHAR)) FROM dataset LIMIT 1",
        )

    assert result["code"] == "invalid_column"
    facade_class.return_value.get_schema.assert_called_once()


def test_row_limit_enforcement_rejects_over_max(
    sample_dataset_path: Path,
    context: ReadAuthorizationContext,
) -> None:
    executor, _, _ = _build_executor(artifact_path=sample_dataset_path)

    result = executor.execute_query(
        context=context,
        dataset_id="dataset-1",
        sql="SELECT placement FROM dataset LIMIT 1000",
    )

    assert result["code"] == "unsafe_sql"


def test_timeout_handling(sample_dataset_path: Path, context: ReadAuthorizationContext) -> None:
    executor, _, _ = _build_executor(
        artifact_path=sample_dataset_path,
        execution_timeout_seconds=0.05,
    )
    original_method = executor._duckdb_runner._execute_query

    def _slow_query(**kwargs):
        _ = kwargs

        time.sleep(0.3)
        return original_method(
            local_path=str(sample_dataset_path),
            sql="SELECT placement FROM dataset LIMIT 1",
        )

    executor._duckdb_runner._execute_query = _slow_query  # type: ignore[method-assign]

    result = executor.execute_query(
        context=context,
        dataset_id="dataset-1",
        sql="SELECT placement FROM dataset LIMIT 1",
    )

    assert result["code"] == "query_timeout"


def test_global_concurrency_limit_exceeded_returns_429(sample_dataset_path: Path) -> None:
    guard = DatasetQueryResourceGuard(max_concurrent_global=1, max_concurrent_per_tenant=2)
    executor, _, _ = _build_executor(
        artifact_path=sample_dataset_path,
        resource_guard=guard,
    )
    context_one = ReadAuthorizationContext(tenant_id="tenant-1", roles=("admin",))
    context_two = ReadAuthorizationContext(tenant_id="tenant-2", roles=("admin",))
    started = Event()
    release = Event()
    thread_results: list[dict[str, object]] = []

    def _slow_query(**_kwargs):
        started.set()
        release.wait(timeout=1.0)
        return {
            "columns": ["placement"],
            "rows": [["A"]],
            "row_count": 1,
            "truncated": False,
        }

    executor._duckdb_runner.query_file = _slow_query  # type: ignore[method-assign]

    def _run_first() -> None:
        thread_results.append(
            executor.execute_query(
                context=context_one,
                dataset_id="dataset-1",
                sql="SELECT placement FROM dataset LIMIT 1",
            ),
        )

    first_thread = Thread(target=_run_first)
    first_thread.start()
    assert started.wait(timeout=0.5)

    limited_result = executor.execute_query(
        context=context_two,
        dataset_id="dataset-1",
        sql="SELECT placement FROM dataset LIMIT 1",
    )
    assert limited_result == {
        "success": False,
        "status": 429,
        "code": "query_concurrency_limit_exceeded",
        "error": "Too many dataset queries are running. Please retry later.",
    }

    release.set()
    first_thread.join(timeout=1.0)
    assert thread_results and thread_results[0]["row_count"] == 1


def test_per_tenant_concurrency_limit_exceeded_returns_429(sample_dataset_path: Path) -> None:
    guard = DatasetQueryResourceGuard(max_concurrent_global=3, max_concurrent_per_tenant=1)
    executor, _, _ = _build_executor(
        artifact_path=sample_dataset_path,
        resource_guard=guard,
    )
    context = ReadAuthorizationContext(tenant_id="tenant-1", roles=("admin",))
    started = Event()
    release = Event()

    def _slow_query(**_kwargs):
        started.set()
        release.wait(timeout=1.0)
        return {
            "columns": ["placement"],
            "rows": [["A"]],
            "row_count": 1,
            "truncated": False,
        }

    executor._duckdb_runner.query_file = _slow_query  # type: ignore[method-assign]

    first_thread = Thread(
        target=lambda: executor.execute_query(
            context=context,
            dataset_id="dataset-1",
            sql="SELECT placement FROM dataset LIMIT 1",
        ),
    )
    first_thread.start()
    assert started.wait(timeout=0.5)

    limited_result = executor.execute_query(
        context=context,
        dataset_id="dataset-1",
        sql="SELECT placement FROM dataset LIMIT 1",
    )
    assert limited_result["status"] == EXPECTED_STATUS_TOO_MANY_REQUESTS
    assert limited_result["code"] == "query_concurrency_limit_exceeded"

    release.set()
    first_thread.join(timeout=1.0)


def test_concurrency_slot_released_after_success(
    sample_dataset_path: Path,
    context: ReadAuthorizationContext,
) -> None:
    guard = DatasetQueryResourceGuard(max_concurrent_global=1, max_concurrent_per_tenant=1)
    executor, _, _ = _build_executor(artifact_path=sample_dataset_path, resource_guard=guard)

    result_one = executor.execute_query(
        context=context,
        dataset_id="dataset-1",
        sql="SELECT placement FROM dataset LIMIT 1",
    )
    result_two = executor.execute_query(
        context=context,
        dataset_id="dataset-1",
        sql="SELECT placement FROM dataset LIMIT 1",
    )

    assert result_one["row_count"] == 1
    assert result_two["row_count"] == 1


def test_concurrency_slot_released_after_failed_query(
    sample_dataset_path: Path,
    context: ReadAuthorizationContext,
) -> None:
    guard = DatasetQueryResourceGuard(max_concurrent_global=1, max_concurrent_per_tenant=1)
    executor, _, _ = _build_executor(artifact_path=sample_dataset_path, resource_guard=guard)
    executor._duckdb_runner.query_file = MagicMock(side_effect=duckdb.Error("boom"))  # type: ignore[method-assign]

    failed = executor.execute_query(
        context=context,
        dataset_id="dataset-1",
        sql="SELECT placement FROM dataset LIMIT 1",
    )
    executor._duckdb_runner.query_file = MagicMock(  # type: ignore[method-assign]
        return_value={
            "columns": ["placement"],
            "rows": [["A"]],
            "row_count": 1,
            "truncated": False,
        }
    )
    succeeded = executor.execute_query(
        context=context,
        dataset_id="dataset-1",
        sql="SELECT placement FROM dataset LIMIT 1",
    )

    assert failed["code"] == "query_execution_error"
    assert succeeded["row_count"] == 1


def test_concurrency_slot_released_after_timeout(
    sample_dataset_path: Path,
    context: ReadAuthorizationContext,
) -> None:
    guard = DatasetQueryResourceGuard(max_concurrent_global=1, max_concurrent_per_tenant=1)
    executor, _, _ = _build_executor(artifact_path=sample_dataset_path, resource_guard=guard)
    executor._duckdb_runner.query_file = MagicMock(side_effect=QueryTimeoutError("timeout"))  # type: ignore[method-assign]

    timed_out = executor.execute_query(
        context=context,
        dataset_id="dataset-1",
        sql="SELECT placement FROM dataset LIMIT 1",
    )
    executor._duckdb_runner.query_file = MagicMock(  # type: ignore[method-assign]
        return_value={
            "columns": ["placement"],
            "rows": [["A"]],
            "row_count": 1,
            "truncated": False,
        }
    )
    succeeded = executor.execute_query(
        context=context,
        dataset_id="dataset-1",
        sql="SELECT placement FROM dataset LIMIT 1",
    )

    assert timed_out["code"] == "query_timeout"
    assert succeeded["row_count"] == 1


def test_artifact_too_large_returns_413_before_execution(
    sample_dataset_path: Path,
    context: ReadAuthorizationContext,
) -> None:
    executor, _, _ = _build_executor(
        artifact_path=sample_dataset_path,
        max_artifact_bytes_sync=1,
    )
    query_spy = MagicMock(
        return_value={
            "columns": ["placement"],
            "rows": [["A"]],
            "row_count": 1,
            "truncated": False,
        },
    )
    executor._duckdb_runner.query_file = query_spy  # type: ignore[method-assign]

    result = executor.execute_query(
        context=context,
        dataset_id="dataset-1",
        sql="SELECT placement FROM dataset LIMIT 1",
    )

    assert result == {
        "success": False,
        "status": 413,
        "code": "dataset_too_large_for_sync_query",
        "error": "This dataset is too large for synchronous querying.",
    }
    query_spy.assert_not_called()


def test_temp_cleanup_happens_after_success(
    sample_dataset_path: Path,
    context: ReadAuthorizationContext,
) -> None:
    guard = DatasetQueryResourceGuard(max_concurrent_global=2, max_concurrent_per_tenant=2)
    temp_root = sample_dataset_path.parent / "query-temp-success"
    executor, _, _ = _build_executor(
        artifact_path=sample_dataset_path,
        resource_guard=guard,
        temp_root_directory=temp_root,
    )

    result = executor.execute_query(
        context=context,
        dataset_id="dataset-1",
        sql="SELECT placement FROM dataset LIMIT 1",
    )

    assert result["row_count"] == 1
    assert temp_root.exists()
    assert not any(temp_root.iterdir())


def test_temp_cleanup_happens_after_failed_query(
    sample_dataset_path: Path,
    context: ReadAuthorizationContext,
) -> None:
    temp_root = sample_dataset_path.parent / "query-temp-failure"
    executor, _, _ = _build_executor(
        artifact_path=sample_dataset_path,
        temp_root_directory=temp_root,
    )
    executor._duckdb_runner.query_file = MagicMock(side_effect=duckdb.Error("boom"))  # type: ignore[method-assign]

    result = executor.execute_query(
        context=context,
        dataset_id="dataset-1",
        sql="SELECT placement FROM dataset LIMIT 1",
    )

    assert result["code"] == "query_execution_error"
    assert temp_root.exists()
    assert not any(temp_root.iterdir())


def test_temp_cleanup_after_unsafe_sql_rejection_does_not_materialize(
    sample_dataset_path: Path,
    context: ReadAuthorizationContext,
) -> None:
    executor, _, _ = _build_executor(artifact_path=sample_dataset_path)
    resolve_artifact_spy = MagicMock(side_effect=AssertionError("Should not resolve artifact."))
    executor._resolve_artifact = resolve_artifact_spy  # type: ignore[method-assign]

    result = executor.execute_query(
        context=context,
        dataset_id="dataset-1",
        sql="SELECT placement FROM dataset",
    )

    assert result["code"] == "unsafe_sql"
    resolve_artifact_spy.assert_not_called()


def test_insufficient_temp_storage_returns_507(
    sample_dataset_path: Path,
    context: ReadAuthorizationContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, _, _ = _build_executor(
        artifact_path=sample_dataset_path,
        temp_min_free_bytes=1024,
    )
    monkeypatch.setattr(
        "resource_registry.services.dataset_query_executor.shutil.disk_usage",
        lambda _path: namedtuple("usage", "total used free")(10_000, 9_500, 500),
    )
    query_spy = MagicMock(
        return_value={
            "columns": ["placement"],
            "rows": [["A"]],
            "row_count": 1,
            "truncated": False,
        },
    )
    executor._duckdb_runner.query_file = query_spy  # type: ignore[method-assign]

    result = executor.execute_query(
        context=context,
        dataset_id="dataset-1",
        sql="SELECT placement FROM dataset LIMIT 1",
    )

    assert result == {
        "success": False,
        "status": 507,
        "code": "insufficient_temp_storage",
        "error": "Insufficient temporary storage for dataset query execution.",
    }
    query_spy.assert_not_called()


def test_empty_result_set(sample_dataset_path: Path, context: ReadAuthorizationContext) -> None:
    executor, _, _ = _build_executor(artifact_path=sample_dataset_path)

    result = executor.execute_query(
        context=context,
        dataset_id="dataset-1",
        sql="SELECT placement FROM dataset WHERE spend > 9999 LIMIT 10",
    )

    assert result["rows"] == []
    assert result["row_count"] == 0
    assert result["truncated"] is False


def test_dataset_not_found_raises_existing_error(
    sample_dataset_path: Path,
    context: ReadAuthorizationContext,
) -> None:
    executor, dataset_read_service, _ = _build_executor(artifact_path=sample_dataset_path)
    dataset_read_service.get_dataset.side_effect = DatasetNotFoundError(
        "Dataset not found for tenant.",
    )

    with pytest.raises(DatasetNotFoundError):
        executor.execute_query(
            context=context,
            dataset_id="missing",
            sql="SELECT placement FROM dataset LIMIT 1",
        )


def test_version_selection_uses_explicit_version(
    sample_dataset_path: Path,
    context: ReadAuthorizationContext,
) -> None:
    executor, _, version_read_service = _build_executor(artifact_path=sample_dataset_path)

    result = executor.execute_query(
        context=context,
        dataset_id="dataset-1",
        version_id="version-specific",
        sql="SELECT placement FROM dataset LIMIT 1",
    )

    assert result["row_count"] == 1
    version_read_service.get_version.assert_called_once_with(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        version_id="version-specific",
    )
    version_read_service.get_latest_version.assert_not_called()


def test_artifact_not_found(sample_dataset_path: Path, context: ReadAuthorizationContext) -> None:
    executor, _, _ = _build_executor(artifact_path=sample_dataset_path)
    executor._artifact_read_service.list_artifacts.return_value = []

    with pytest.raises(ArtifactNotFoundError):
        executor.execute_query(
            context=context,
            dataset_id="dataset-1",
            sql="SELECT placement FROM dataset LIMIT 1",
        )


def test_no_secrets_exposed_in_error_contract(
    sample_dataset_path: Path,
    context: ReadAuthorizationContext,
) -> None:
    executor, _, _ = _build_executor(artifact_path=sample_dataset_path)

    result = executor.execute_query(
        context=context,
        dataset_id="dataset-1",
        sql="SELECT placement FROM dataset; COPY dataset TO 'https://x?sig=secret' LIMIT 1",
    )

    serialized = str(result).lower()
    assert result["code"] == "unsafe_sql"
    assert "raw_rows" not in serialized
    assert "credentials" not in serialized
    assert "sig=" not in serialized
    assert "token" not in serialized
    assert "password" not in serialized
    assert "secret" not in serialized
    assert "connection string" not in serialized


def test_no_temp_path_exposed_in_execution_error(
    sample_dataset_path: Path,
    context: ReadAuthorizationContext,
    tmp_path: Path,
) -> None:
    cache_directory = tmp_path / "cache-hidden-path"
    cache = MaterializedDatasetCache(
        cache_directory=cache_directory,
        ttl_seconds=600,
        max_bytes=1024 * 1024,
    )
    executor, _, _ = _build_executor(
        artifact_path=sample_dataset_path,
        materialized_dataset_cache=cache,
    )
    executor._duckdb_runner.query_file = MagicMock(side_effect=duckdb.Error("boom"))  # type: ignore[method-assign]

    result = executor.execute_query(
        context=context,
        dataset_id="dataset-1",
        sql="SELECT placement FROM dataset LIMIT 1",
    )

    assert result["code"] == "query_execution_error"
    serialized = str(result).lower()
    assert str(sample_dataset_path.parent).lower() not in serialized
    assert str(cache_directory).lower() not in serialized


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM read_csv_auto('/etc/passwd') LIMIT 1",
        "SELECT placement FROM read_parquet('x.parquet') LIMIT 1",
        "SELECT placement FROM another_table LIMIT 1",
        "SELECT dataset.placement FROM dataset JOIN another_table ON 1=1 LIMIT 1",
        "SELECT placement FROM dataset UNION SELECT placement FROM dataset LIMIT 1",
        "SELECT placement FROM dataset LIMIT 1; SELECT 1",
        "SELECT placement FROM dataset -- bypass\nLIMIT 1",
        (
            "SELECT placement FROM dataset WHERE spend > "
            "(SELECT AVG(spend) FROM another_table LIMIT 1) LIMIT 1"
        ),
        "SELECT placement FROM (SELECT placement FROM dataset LIMIT 1) x LIMIT 1",
        "SELECT placement FROM dataset WHERE placement IN (SELECT placement FROM dataset LIMIT 1)",
    ],
)
def test_sql_hardening_rejects_escape_attempts(
    sample_dataset_path: Path,
    context: ReadAuthorizationContext,
    sql: str,
) -> None:
    executor, _, _ = _build_executor(artifact_path=sample_dataset_path)
    result = executor.execute_query(context=context, dataset_id="dataset-1", sql=sql)
    assert result["code"] == "unsafe_sql"


def test_inner_limit_without_top_level_limit_rejected(
    sample_dataset_path: Path,
    context: ReadAuthorizationContext,
) -> None:
    executor, _, _ = _build_executor(artifact_path=sample_dataset_path)
    sql = "SELECT placement FROM (SELECT placement FROM dataset LIMIT 1) q"
    result = executor.execute_query(context=context, dataset_id="dataset-1", sql=sql)
    assert result["code"] == "unsafe_sql"


def test_valid_dataset_select_with_top_level_limit_passes(
    sample_dataset_path: Path,
    context: ReadAuthorizationContext,
) -> None:
    executor, _, _ = _build_executor(artifact_path=sample_dataset_path)
    result = executor.execute_query(
        context=context,
        dataset_id="dataset-1",
        sql="SELECT placement, spend FROM dataset ORDER BY spend DESC LIMIT 2",
    )
    assert result["row_count"] == EXPECTED_TWO_ROWS


def test_duckdb_contains_search_escapes_percent_underscore_and_backslash(
    searchable_dataset_path: Path,
) -> None:
    runner = dataset_query_executor_module._DuckDbDatasetRunner(
        max_returned_rows=10,
        execution_timeout_seconds=1.0,
    )
    sql = (
        'SELECT "attribute_name" FROM dataset '
        "WHERE LOWER(CAST(\"attribute_name\" AS VARCHAR)) LIKE ? ESCAPE '\\' LIMIT 10"
    )

    result = runner.query_file(
        local_path=str(searchable_dataset_path),
        sql=sql,
        parameters=("%online\\_financial\\\\audiences\\%%",),
    )

    assert result["rows"] == [["Online_Financial\\Audiences%"]]


def test_duckdb_execution_failure_logs_sanitized_diagnostics(
    sample_dataset_path: Path,
    context: ReadAuthorizationContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, _, _ = _build_executor(artifact_path=sample_dataset_path)
    logger_warning = MagicMock()
    monkeypatch.setattr(dataset_query_executor_module._logger, "warning", logger_warning)
    executor._duckdb_runner.query_file = MagicMock(  # type: ignore[method-assign]
        side_effect=DuckDbQueryExecutionError(
            phase="explain",
            original_error=duckdb.ParserException("parser failure for secret-literal"),
        )
    )
    sql = "SELECT placement FROM dataset WHERE placement = 'secret-literal' LIMIT 1"

    result = executor.execute_query(
        context=context,
        dataset_id="dataset-1",
        sql=sql,
    )

    assert result == {
        "success": False,
        "status": 400,
        "code": "query_execution_error",
        "error": "Unable to execute query.",
    }
    logger_warning.assert_called_once()
    extra = logger_warning.call_args.kwargs["extra"]
    assert extra == {
        "tenant_id": "tenant-1",
        "dataset_id": "dataset-1",
        "version_id": "version-latest",
        "phase": "explain",
        "exception_type": "ParserException",
        "sql_fingerprint": sha256(sql.encode("utf-8")).hexdigest(),
    }
    assert "secret-literal" not in str(logger_warning.call_args)


@override_settings(RESOURCE_REGISTRY_SQL_VALIDATOR_MODE="sqlglot")
def test_sqlglot_mode_allows_dataset_alias_and_quoted_identifier(
    sample_dataset_path: Path,
    context: ReadAuthorizationContext,
) -> None:
    executor, _, _ = _build_executor(artifact_path=sample_dataset_path)
    result = executor.execute_query(
        context=context,
        dataset_id="dataset-1",
        sql='SELECT d."placement" FROM dataset AS d ORDER BY d."placement" LIMIT 2',
    )
    assert result["row_count"] == EXPECTED_TWO_ROWS


@override_settings(RESOURCE_REGISTRY_SQL_VALIDATOR_MODE="sqlglot")
def test_sqlglot_mode_allows_nested_expression_with_limit(
    sample_dataset_path: Path,
    context: ReadAuthorizationContext,
) -> None:
    executor, _, _ = _build_executor(artifact_path=sample_dataset_path)
    result = executor.execute_query(
        context=context,
        dataset_id="dataset-1",
        sql="SELECT COALESCE(placement, 'unknown') AS normalized_placement FROM dataset LIMIT 2",
    )
    assert result["row_count"] == EXPECTED_TWO_ROWS


@override_settings(RESOURCE_REGISTRY_SQL_VALIDATOR_MODE="sqlglot")
def test_sqlglot_mode_rejects_join_escape_attempt(
    sample_dataset_path: Path,
    context: ReadAuthorizationContext,
) -> None:
    executor, _, _ = _build_executor(artifact_path=sample_dataset_path)
    result = executor.execute_query(
        context=context,
        dataset_id="dataset-1",
        sql="SELECT d.placement FROM dataset AS d JOIN another_table a ON 1 = 1 LIMIT 1",
    )
    assert result["code"] == "unsafe_sql"


@override_settings(RESOURCE_REGISTRY_SQL_VALIDATOR_MODE="shadow")
def test_shadow_mode_uses_legacy_result_and_logs_divergence(
    sample_dataset_path: Path,
    context: ReadAuthorizationContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, _, _ = _build_executor(artifact_path=sample_dataset_path)

    def _always_reject(_: str) -> dict[str, object]:
        return {
            "success": False,
            "status": 400,
            "code": "unsafe_sql",
            "error": "forced shadow mismatch",
        }

    warning_mock = MagicMock()
    monkeypatch.setattr(dataset_query_executor_module._logger, "warning", warning_mock)
    monkeypatch.setattr(executor._sql_validator, "_validate_sql_with_sqlglot", _always_reject)
    result = executor.execute_query(
        context=context,
        dataset_id="dataset-1",
        sql="SELECT placement FROM dataset LIMIT 1",
    )

    assert result["row_count"] == 1
    warning_mock.assert_called_once()
