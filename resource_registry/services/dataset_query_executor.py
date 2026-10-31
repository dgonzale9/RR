from __future__ import annotations

import logging
import re
import shutil
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from hashlib import sha256
from pathlib import Path
from threading import Lock
from typing import Any, Protocol

import duckdb
from django.conf import settings
from sqlglot import exp, parse
from sqlglot.errors import ParseError

from resource_registry.cleanup.reservations import lease_dataset
from resource_registry.read.artifact_read_service import DatasetArtifactReadService
from resource_registry.read.dataset_read_service import DatasetReadService
from resource_registry.read.dtos import DatasetArtifactSummary
from resource_registry.read.exceptions import ArtifactNotFoundError
from resource_registry.read.schema_read_service import DatasetSchemaReadService
from resource_registry.read.version_read_service import DatasetVersionReadService
from resource_registry.read_auth.context import ReadAuthorizationContext
from resource_registry.read_auth.policy import AuthorizedDataRegistryReadFacade
from resource_registry.read_auth.query_columns import get_queryable_schema_column_names
from resource_registry.services.dataset_query_resource_guard import (
    DatasetQueryResourceGuard,
    DatasetQueryResourceLimitError,
)
from resource_registry.services.duckdb_service import (
    DuckDbConnectionProvider,
    InMemoryDuckDbConnectionProvider,
)
from resource_registry.services.materialized_dataset_cache import (
    DatasetCacheMaterializationError,
    DatasetCacheStorageExceededError,
    MaterializedDatasetCache,
)
from resource_registry.storage.factory import DataRegistryStorageFactory
from resource_registry.storage.read_adapters import (
    ArtifactReadAdapter,
    LocalArtifactReadAdapter,
    StorageProviderArtifactReadAdapter,
)

_DEFAULT_TIMEOUT_SECONDS = 3
_MAX_RETURNED_ROWS = 100
_DEFAULT_MAX_CONCURRENT_GLOBAL = 10
_DEFAULT_MAX_CONCURRENT_PER_TENANT = 2
_DEFAULT_MAX_CONCURRENT_PER_USER = 0
_DEFAULT_MAX_ARTIFACT_BYTES_SYNC = 500 * 1024 * 1024
_DEFAULT_TEMP_MIN_FREE_BYTES = 1024 * 1024 * 1024
_DEFAULT_DATASET_CACHE_TTL_SECONDS = 600
_DEFAULT_DATASET_CACHE_MAX_BYTES = 2 * 1024 * 1024 * 1024
_DEFAULT_SQL_VALIDATOR_MODE = "sqlglot"
_UNSAFE_SQL_ERROR = {
    "success": False,
    "status": 400,
    "code": "unsafe_sql",
    "error": (
        "Only safe SELECT statements against the registered dataset table with LIMIT are allowed."
    ),
}
_QUERY_TIMEOUT_ERROR = {
    "success": False,
    "status": 408,
    "code": "query_timeout",
    "error": "Query execution exceeded timeout.",
}
_QUERY_EXECUTION_ERROR = {
    "success": False,
    "status": 400,
    "code": "query_execution_error",
    "error": "Unable to execute query.",
}
_DATASET_TOO_LARGE_FOR_SYNC_QUERY_ERROR = {
    "success": False,
    "status": 413,
    "code": "dataset_too_large_for_sync_query",
    "error": "This dataset is too large for synchronous querying.",
}
_INSUFFICIENT_TEMP_STORAGE_ERROR = {
    "success": False,
    "status": 507,
    "code": "insufficient_temp_storage",
    "error": "Insufficient temporary storage for dataset query execution.",
}
_DATASET_CACHE_MATERIALIZATION_FAILED_ERROR = {
    "success": False,
    "status": 500,
    "code": "dataset_cache_materialization_failed",
    "error": "Unable to prepare dataset for query execution.",
}
_DATASET_CACHE_STORAGE_EXCEEDED_ERROR = {
    "success": False,
    "status": 507,
    "code": "dataset_cache_storage_exceeded",
    "error": "Dataset cache storage limit exceeded.",
}
_FORBIDDEN_KEYWORDS = (
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "create",
    "copy",
    "export",
    "attach",
    "detach",
    "pragma",
    "install",
    "load",
    "call",
    "union",
    "join",
)
_KEYWORD_PATTERN = re.compile(
    r"\b(" + "|".join(_FORBIDDEN_KEYWORDS) + r")\b",
    flags=re.IGNORECASE,
)
_LIMIT_PATTERN = re.compile(r"\blimit\s+(\d+)\b", flags=re.IGNORECASE)
_SELECT_STAR_PATTERN = re.compile(r"(?:^|,)\s*(?:dataset\.)?\*", flags=re.IGNORECASE)
_TABLE_FUNCTION_PATTERN = re.compile(
    r"\b("
    r"read_csv_auto|read_csv|read_parquet|read_json|read_json_auto|"
    r"parquet_scan|csv_auto|glob"
    r")\s*\(",
    flags=re.IGNORECASE,
)
_COLUMN_NOT_FOUND_PATTERN = re.compile(
    r'Referenced column "(?P<column>[^"]+)" not found',
    flags=re.IGNORECASE,
)
_MULTI_SPACE_PATTERN = re.compile(r"\s+")
_SQL_COMMENTS_PATTERN = re.compile(r"(--|/\*|\*/)")
_logger = logging.getLogger("app-logger")
_RESOURCE_GUARD_LOCK = Lock()
_RESOURCE_GUARD_CACHE: dict[tuple[int, int, int], DatasetQueryResourceGuard] = {}


def _get_default_resource_guard() -> DatasetQueryResourceGuard:
    max_global = int(
        getattr(
            settings,
            "RESOURCE_REGISTRY_QUERY_MAX_CONCURRENT_GLOBAL",
            _DEFAULT_MAX_CONCURRENT_GLOBAL,
        ),
    )
    max_per_tenant = int(
        getattr(
            settings,
            "RESOURCE_REGISTRY_QUERY_MAX_CONCURRENT_PER_TENANT",
            _DEFAULT_MAX_CONCURRENT_PER_TENANT,
        ),
    )
    max_per_user = int(
        getattr(
            settings,
            "RESOURCE_REGISTRY_QUERY_MAX_CONCURRENT_PER_USER",
            _DEFAULT_MAX_CONCURRENT_PER_USER,
        ),
    )
    cache_key = (max_global, max_per_tenant, max_per_user)

    with _RESOURCE_GUARD_LOCK:
        guard = _RESOURCE_GUARD_CACHE.get(cache_key)
        if guard is None:
            guard = DatasetQueryResourceGuard(
                max_concurrent_global=max_global,
                max_concurrent_per_tenant=max_per_tenant,
                max_concurrent_per_user=max_per_user,
            )
            _RESOURCE_GUARD_CACHE[cache_key] = guard
    return guard


class QueryTimeoutError(RuntimeError):
    """Raised when query execution exceeds configured timeout."""


class DuckDbQueryExecutionError(RuntimeError):
    """Carry the safe execution phase and original DuckDB exception."""

    def __init__(self, *, phase: str, original_error: duckdb.Error) -> None:
        """Initialize the execution error.

        Args:
            phase: Safe execution phase identifier.
            original_error: Original DuckDB exception retained for internal classification.
        """
        self.phase = phase
        self.original_error = original_error
        super().__init__(f"DuckDB {phase} failed.")


class _SqlValidationStrategy(Protocol):
    def validate(self, sql: str) -> dict[str, Any] | None:
        """Validate SQL and return an unsafe_sql payload when invalid."""


class _FunctionSqlValidationStrategy:
    def __init__(self, validator: Callable[[str], dict[str, Any] | None]) -> None:
        self._validator = validator

    def validate(self, sql: str) -> dict[str, Any] | None:
        return self._validator(sql)


class _ShadowSqlValidationStrategy:
    def __init__(
        self,
        *,
        legacy_validator: Callable[[str], dict[str, Any] | None],
        sqlglot_validator: Callable[[str], dict[str, Any] | None],
        on_mismatch: Callable[..., None],
    ) -> None:
        self._legacy_validator = legacy_validator
        self._sqlglot_validator = sqlglot_validator
        self._on_mismatch = on_mismatch

    def validate(self, sql: str) -> dict[str, Any] | None:
        legacy_result = self._legacy_validator(sql)
        sqlglot_result = self._sqlglot_validator(sql)
        self._on_mismatch(legacy_result, sqlglot_result)
        return legacy_result


class _SafeSqlValidator:
    """Validates SQL safety and column references against dataset schema."""

    def __init__(self, *, max_returned_rows: int, mode: str = _DEFAULT_SQL_VALIDATOR_MODE) -> None:
        self._max_returned_rows = max_returned_rows
        normalized_mode = str(mode or _DEFAULT_SQL_VALIDATOR_MODE).strip().lower()
        if normalized_mode not in {"legacy", "sqlglot", "shadow"}:
            _logger.warning(
                "Unknown SQL validator mode configured. Falling back to default mode.",
                extra={"configured_mode": normalized_mode},
            )
            normalized_mode = _DEFAULT_SQL_VALIDATOR_MODE
        self._sql_validation_strategy = self._build_sql_validation_strategy(normalized_mode)

    def validate_sql(self, sql: str) -> dict[str, Any] | None:
        normalized = sql.strip()
        if not normalized:
            return dict(_UNSAFE_SQL_ERROR)
        if _SQL_COMMENTS_PATTERN.search(normalized):
            return dict(_UNSAFE_SQL_ERROR)

        normalized_no_semicolon = (
            normalized[:-1].strip() if normalized.endswith(";") else normalized
        )
        if ";" in normalized_no_semicolon:
            return dict(_UNSAFE_SQL_ERROR)

        return self._sql_validation_strategy.validate(normalized_no_semicolon)

    def _build_sql_validation_strategy(self, mode: str) -> _SqlValidationStrategy:
        if mode == "legacy":
            return _FunctionSqlValidationStrategy(lambda sql: self._validate_sql_with_legacy(sql))
        if mode == "sqlglot":
            return _FunctionSqlValidationStrategy(lambda sql: self._validate_sql_with_sqlglot(sql))
        return _ShadowSqlValidationStrategy(
            legacy_validator=lambda sql: self._validate_sql_with_legacy(sql),
            sqlglot_validator=lambda sql: self._validate_sql_with_sqlglot(sql),
            on_mismatch=lambda legacy_result, sqlglot_result: self._log_shadow_mismatch_if_needed(
                legacy_result=legacy_result,
                sqlglot_result=sqlglot_result,
            ),
        )

    def _validate_sql_with_legacy(self, normalized_no_semicolon: str) -> dict[str, Any] | None:
        if not normalized_no_semicolon.lower().startswith("select "):
            return dict(_UNSAFE_SQL_ERROR)
        if _KEYWORD_PATTERN.search(self._strip_string_literals(normalized_no_semicolon)):
            return dict(_UNSAFE_SQL_ERROR)
        if _TABLE_FUNCTION_PATTERN.search(normalized_no_semicolon):
            return dict(_UNSAFE_SQL_ERROR)
        if not self._has_only_dataset_sources(normalized_no_semicolon):
            return dict(_UNSAFE_SQL_ERROR)
        if not self._is_from_dataset_only(normalized_no_semicolon):
            return dict(_UNSAFE_SQL_ERROR)

        lowered = normalized_no_semicolon.lower()
        select_index = lowered.find("select") + len("select")
        from_index = self._find_top_level_keyword(lowered, "from")
        if from_index == -1:
            return dict(_UNSAFE_SQL_ERROR)
        select_clause = normalized_no_semicolon[select_index:from_index].strip()
        if _SELECT_STAR_PATTERN.search(select_clause):
            return dict(_UNSAFE_SQL_ERROR)

        top_level_limit = self._extract_top_level_limit(normalized_no_semicolon)
        if top_level_limit is None:
            return dict(_UNSAFE_SQL_ERROR)
        requested_limit = top_level_limit
        if requested_limit > self._max_returned_rows:
            return dict(_UNSAFE_SQL_ERROR)
        return None

    def _validate_sql_with_sqlglot(self, normalized_no_semicolon: str) -> dict[str, Any] | None:
        try:
            parsed_statements = parse(normalized_no_semicolon, read="duckdb")
        except ParseError:
            return dict(_UNSAFE_SQL_ERROR)

        if len(parsed_statements) != 1:
            return dict(_UNSAFE_SQL_ERROR)
        statement = parsed_statements[0]
        if not isinstance(statement, exp.Select):
            return dict(_UNSAFE_SQL_ERROR)
        if statement.args.get("with") is not None:
            return dict(_UNSAFE_SQL_ERROR)
        if any(True for _ in statement.find_all(exp.Join)):
            return dict(_UNSAFE_SQL_ERROR)
        if any(True for _ in statement.find_all(exp.SetOperation)):
            return dict(_UNSAFE_SQL_ERROR)
        if any(True for _ in statement.find_all(exp.Subquery)):
            return dict(_UNSAFE_SQL_ERROR)
        if any(True for _ in statement.find_all(exp.CTE)):
            return dict(_UNSAFE_SQL_ERROR)

        from_clause = statement.args.get("from_")
        if not isinstance(from_clause, exp.From):
            return dict(_UNSAFE_SQL_ERROR)
        if from_clause.expressions:
            return dict(_UNSAFE_SQL_ERROR)
        source = from_clause.this
        if not isinstance(source, exp.Table):
            return dict(_UNSAFE_SQL_ERROR)
        if not isinstance(source.this, exp.Identifier):
            return dict(_UNSAFE_SQL_ERROR)
        if source.name.lower() != "dataset":
            return dict(_UNSAFE_SQL_ERROR)
        referenced_tables = list(statement.find_all(exp.Table))
        if len(referenced_tables) != 1:
            return dict(_UNSAFE_SQL_ERROR)

        for projection in statement.expressions:
            projected = projection.this if isinstance(projection, exp.Alias) else projection
            if isinstance(projected, exp.Star):
                return dict(_UNSAFE_SQL_ERROR)
            if isinstance(projected, exp.Column) and isinstance(projected.this, exp.Star):
                return dict(_UNSAFE_SQL_ERROR)

        top_level_limit = self._extract_limit_from_ast(statement)
        if top_level_limit is None:
            return dict(_UNSAFE_SQL_ERROR)
        if top_level_limit > self._max_returned_rows:
            return dict(_UNSAFE_SQL_ERROR)
        return None

    def _extract_limit_from_ast(self, statement: exp.Select) -> int | None:
        limit_expression = statement.args.get("limit")
        if not isinstance(limit_expression, exp.Limit):
            return None
        candidate = limit_expression.expression
        if candidate is None:
            candidate = limit_expression.this
        if not isinstance(candidate, exp.Literal) or not candidate.is_int:
            return None
        return int(candidate.this)

    def _log_shadow_mismatch_if_needed(
        self,
        *,
        legacy_result: dict[str, Any] | None,
        sqlglot_result: dict[str, Any] | None,
    ) -> None:
        def _signature(payload: dict[str, Any] | None) -> tuple[str, int] | None:
            if payload is None:
                return None
            return str(payload.get("code", "")), int(payload.get("status", 0))

        if _signature(legacy_result) == _signature(sqlglot_result):
            return
        _logger.warning(
            "Dataset SQL validator divergence detected.",
            extra={
                "legacy_signature": _signature(legacy_result),
                "sqlglot_signature": _signature(sqlglot_result),
            },
        )

    def validate_known_columns(self, sql: str, schema_columns: set[str]) -> str | None:
        """Return the first column outside the caller's visible schema.

        Args:
            sql: A previously validated single-dataset SELECT statement.
            schema_columns: Column names authorized for the caller.

        Returns:
            The first unauthorized column, or None when every reference is allowed.
        """
        try:
            statements = parse(sql.strip().rstrip(";"), read="duckdb")
        except ParseError:
            return "<invalid SQL>"
        if len(statements) != 1 or not isinstance(statements[0], exp.Select):
            return "<invalid SQL>"

        statement = statements[0]
        visible_columns = {column.casefold() for column in schema_columns}
        from_clause = statement.args.get("from_")
        source = from_clause.this if isinstance(from_clause, exp.From) else None
        source_names = {"dataset"}
        if isinstance(source, exp.Table):
            source_names.add(source.alias_or_name.casefold())
        projection_aliases = {
            projection.alias.casefold()
            for projection in statement.expressions
            if isinstance(projection, exp.Alias) and projection.alias
        }

        for column in statement.find_all(exp.Column):
            if isinstance(column.this, exp.Star):
                continue
            column_name = column.name
            if column.table and column.table.casefold() not in source_names:
                return column_name
            if column_name.casefold() in visible_columns:
                continue
            if (
                not column.table
                and column_name.casefold() in projection_aliases
                and column.find_ancestor(exp.Order) is not None
            ):
                continue
            return column_name
        return None

    def extract_unknown_column(self, error: str, schema_columns: set[str]) -> str | None:
        match = _COLUMN_NOT_FOUND_PATTERN.search(error)
        if match is None:
            return None
        column = match.group("column")
        lowered_columns = {name.lower() for name in schema_columns}
        if column.lower() not in lowered_columns:
            return column
        return None

    def _extract_select_clause(self, sql: str) -> str:
        lowered = sql.lower()
        select_index = lowered.find("select")
        from_index = self._find_top_level_keyword(lowered, "from")
        if select_index < 0 or from_index < 0:
            return ""
        return sql[select_index + len("select") : from_index]

    def _extract_top_level_limit(self, sql: str) -> int | None:
        lowered = self._normalize_sql(sql)
        limit_index = self._find_top_level_keyword(lowered, "limit")
        if limit_index < 0:
            return None
        limit_segment = lowered[limit_index:]
        match = _LIMIT_PATTERN.match(limit_segment)
        if match is None:
            return None
        return int(match.group(1))

    def _has_only_dataset_sources(self, sql: str) -> bool:
        scrubbed = self._strip_string_literals(sql)
        for match in re.finditer(
            r"\bfrom\s+([A-Za-z_][A-Za-z0-9_\".]*)", scrubbed, flags=re.IGNORECASE
        ):
            source = match.group(1).strip().strip('"').lower()
            if source != "dataset":
                return False
        return True

    def _is_from_dataset_only(self, sql: str) -> bool:
        lowered = self._normalize_sql(sql)
        from_index = self._find_top_level_keyword(lowered, "from")
        if from_index < 0:
            return False
        segment_start = from_index + len("from")
        next_index = self._next_top_level_clause_index(
            lowered,
            segment_start,
            ("where", "group", "having", "order", "limit", "offset", "fetch"),
        )
        from_segment = lowered[segment_start:next_index].strip()
        if not from_segment:
            return False
        if from_segment.startswith("("):
            return False
        if "," in from_segment:
            return False
        first_token = from_segment.split()[0].strip('"')
        return first_token == "dataset"

    def _normalize_sql(self, sql: str) -> str:
        return _MULTI_SPACE_PATTERN.sub(" ", sql.lower()).strip()

    def _strip_string_literals(self, sql: str) -> str:
        output: list[str] = []
        in_single = False
        in_double = False
        index = 0
        while index < len(sql):
            char = sql[index]
            if char == "'" and not in_double:
                in_single = not in_single
                output.append(" ")
                index += 1
                continue
            if char == '"' and not in_single:
                in_double = not in_double
                output.append(" ")
                index += 1
                continue
            output.append(" " if in_single or in_double else char)
            index += 1
        return "".join(output)

    def _find_top_level_keyword(self, sql: str, keyword: str, start: int = 0) -> int:
        target = keyword.lower()
        in_single = False
        in_double = False
        depth = 0
        index = start
        while index < len(sql):
            char = sql[index]
            if char == "'" and not in_double:
                in_single = not in_single
                index += 1
                continue
            if char == '"' and not in_single:
                in_double = not in_double
                index += 1
                continue
            if in_single or in_double:
                index += 1
                continue
            if char == "(":
                depth += 1
                index += 1
                continue
            if char == ")" and depth > 0:
                depth -= 1
                index += 1
                continue
            if depth == 0 and sql.startswith(target, index):
                before = sql[index - 1] if index > 0 else " "
                after_index = index + len(target)
                after = sql[after_index] if after_index < len(sql) else " "
                if not before.isalnum() and before != "_" and not after.isalnum() and after != "_":
                    return index
            index += 1
        return -1

    def _next_top_level_clause_index(
        self,
        sql: str,
        start: int,
        clause_names: tuple[str, ...],
    ) -> int:
        positions = [
            position
            for clause in clause_names
            if (position := self._find_top_level_keyword(sql, clause, start)) >= 0
        ]
        return min(positions) if positions else len(sql)

    def _split_top_level_expressions(self, clause: str) -> list[str]:
        items: list[str] = []
        depth = 0
        current: list[str] = []
        for char in clause:
            if char == "(":
                depth += 1
            elif char == ")" and depth > 0:
                depth -= 1
            if char == "," and depth == 0:
                items.append("".join(current))
                current = []
                continue
            current.append(char)
        if current:
            items.append("".join(current))
        return items


class _DuckDbDatasetRunner:
    """Runs validated SQL against a materialized dataset file in DuckDB."""

    def __init__(
        self,
        *,
        max_returned_rows: int,
        execution_timeout_seconds: float,
        duckdb_connection_provider: DuckDbConnectionProvider | None = None,
    ) -> None:
        self._max_returned_rows = max_returned_rows
        self._execution_timeout_seconds = execution_timeout_seconds
        self._duckdb_connection_provider = (
            duckdb_connection_provider or InMemoryDuckDbConnectionProvider()
        )

    def query_file(
        self,
        *,
        local_path: str,
        sql: str,
        parameters: tuple[Any, ...] = (),
    ) -> dict[str, Any]:
        with ThreadPoolExecutor(max_workers=1) as executor:
            query_job = executor.submit(
                self._execute_query,
                local_path=local_path,
                sql=sql,
                parameters=parameters,
            )
            try:
                return query_job.result(timeout=self._execution_timeout_seconds)
            except FuturesTimeoutError as error:
                query_job.cancel()
                raise QueryTimeoutError("Query execution exceeded timeout.") from error

    def _execute_query(
        self,
        *,
        local_path: str,
        sql: str,
        parameters: tuple[Any, ...],
    ) -> dict[str, Any]:
        with self._duckdb_connection_provider.connect() as connection:
            connection.execute(
                "CREATE TABLE dataset AS SELECT * FROM read_parquet(?)",
                [str(Path(local_path))],
            )
            try:
                cursor = connection.execute(f"EXPLAIN {sql}", list(parameters))
                cursor.fetchall()
            except duckdb.Error as error:
                raise DuckDbQueryExecutionError(phase="explain", original_error=error) from error
            try:
                cursor = connection.execute(sql, list(parameters))
                rows = cursor.fetchall()
                columns = [column[0] for column in cursor.description]
            except duckdb.Error as error:
                raise DuckDbQueryExecutionError(phase="query", original_error=error) from error

        if len(rows) > self._max_returned_rows:
            return dict(_UNSAFE_SQL_ERROR)

        serialized_rows = [list(row) for row in rows]
        return {
            "columns": columns,
            "rows": serialized_rows,
            "row_count": len(serialized_rows),
            "truncated": False,
        }


class DatasetQueryExecutor:
    """Executes safe, read-only SQL over registered dataset artifacts."""

    def __init__(
        self,
        *,
        dataset_read_service: DatasetReadService | None = None,
        version_read_service: DatasetVersionReadService | None = None,
        schema_read_service: DatasetSchemaReadService | None = None,
        artifact_read_service: DatasetArtifactReadService | None = None,
        storage_factory: DataRegistryStorageFactory | None = None,
        adapters: list[ArtifactReadAdapter] | None = None,
        execution_timeout_seconds: float | None = None,
        resource_guard: DatasetQueryResourceGuard | None = None,
        temp_root_directory: str | Path | None = None,
        max_artifact_bytes_sync: int | None = None,
        temp_min_free_bytes: int | None = None,
        max_returned_rows: int | None = None,
        materialized_dataset_cache: MaterializedDatasetCache | None = None,
        duckdb_connection_provider: DuckDbConnectionProvider | None = None,
    ) -> None:
        self._dataset_read_service = dataset_read_service or DatasetReadService()
        self._version_read_service = version_read_service or DatasetVersionReadService()
        self._schema_read_service = schema_read_service or DatasetSchemaReadService()
        self._artifact_read_service = artifact_read_service or DatasetArtifactReadService()
        self._storage_factory = storage_factory or DataRegistryStorageFactory()
        self._adapters = adapters
        self._resource_guard = resource_guard or _get_default_resource_guard()
        configured_timeout = execution_timeout_seconds
        if configured_timeout is None:
            configured_timeout = float(
                getattr(
                    settings, "RESOURCE_REGISTRY_QUERY_TIMEOUT_SECONDS", _DEFAULT_TIMEOUT_SECONDS
                ),
            )
        self._execution_timeout_seconds = max(float(configured_timeout), 0.1)
        configured_max_artifact_bytes = (
            max_artifact_bytes_sync
            if max_artifact_bytes_sync is not None
            else int(
                getattr(
                    settings,
                    "RESOURCE_REGISTRY_QUERY_MAX_ARTIFACT_BYTES_SYNC",
                    _DEFAULT_MAX_ARTIFACT_BYTES_SYNC,
                ),
            )
        )
        self._max_artifact_bytes_sync = max(int(configured_max_artifact_bytes), 1)
        configured_temp_min_free_bytes = (
            temp_min_free_bytes
            if temp_min_free_bytes is not None
            else int(
                getattr(
                    settings,
                    "RESOURCE_REGISTRY_QUERY_TEMP_MIN_FREE_BYTES",
                    _DEFAULT_TEMP_MIN_FREE_BYTES,
                ),
            )
        )
        self._temp_min_free_bytes = max(int(configured_temp_min_free_bytes), 0)
        if temp_root_directory is None:
            configured_temp_root = getattr(
                settings,
                "RESOURCE_REGISTRY_QUERY_TEMP_DIR",
                Path(settings.BASE_DIR) / "resource_registry_tmp" / "query",
            )
        else:
            configured_temp_root = temp_root_directory
        self._temp_root_directory = Path(str(configured_temp_root)).resolve()
        self._temp_root_directory.mkdir(parents=True, exist_ok=True)
        cache_directory = Path(
            getattr(
                settings,
                "RESOURCE_REGISTRY_DATASET_CACHE_DIR",
                Path(settings.BASE_DIR) / "resource_registry_tmp" / "dataset_cache",
            ),
        ).resolve()
        cache_ttl = int(
            getattr(
                settings,
                "RESOURCE_REGISTRY_DATASET_CACHE_TTL_SECONDS",
                _DEFAULT_DATASET_CACHE_TTL_SECONDS,
            ),
        )
        cache_max_bytes = int(
            getattr(
                settings,
                "RESOURCE_REGISTRY_DATASET_CACHE_MAX_BYTES",
                _DEFAULT_DATASET_CACHE_MAX_BYTES,
            ),
        )
        self._dataset_cache = materialized_dataset_cache or MaterializedDatasetCache(
            cache_directory=cache_directory,
            ttl_seconds=cache_ttl,
            max_bytes=cache_max_bytes,
        )
        validator_mode = str(
            getattr(
                settings,
                "RESOURCE_REGISTRY_SQL_VALIDATOR_MODE",
                _DEFAULT_SQL_VALIDATOR_MODE,
            ),
        )
        configured_max_returned_rows = (
            max_returned_rows if max_returned_rows is not None else _MAX_RETURNED_ROWS
        )
        self._max_returned_rows = max(int(configured_max_returned_rows), 1)
        self._sql_validator = _SafeSqlValidator(
            max_returned_rows=self._max_returned_rows,
            mode=validator_mode,
        )
        self._duckdb_runner = _DuckDbDatasetRunner(
            max_returned_rows=self._max_returned_rows,
            execution_timeout_seconds=self._execution_timeout_seconds,
            duckdb_connection_provider=duckdb_connection_provider,
        )

    def execute_query(
        self,
        *,
        context: ReadAuthorizationContext,
        dataset_id: str,
        sql: str,
        version_id: str | None = None,
        parameters: tuple[Any, ...] = (),
    ) -> dict[str, Any]:
        query_started_at = time.perf_counter()
        selected_version_id = version_id
        result_payload: dict[str, Any] | None = None
        timeout_hit = False

        sql_validation = self._sql_validator.validate_sql(sql)
        if sql_validation is not None:
            self._log_query_observability(
                context=context,
                dataset_id=dataset_id,
                version_id=selected_version_id,
                result=sql_validation,
                query_started_at=query_started_at,
                timeout_hit=False,
            )
            return sql_validation

        self._dataset_read_service.get_dataset(
            tenant_id=context.tenant_id,
            dataset_id=dataset_id,
        )

        if version_id:
            version = self._version_read_service.get_version(
                tenant_id=context.tenant_id,
                dataset_id=dataset_id,
                version_id=version_id,
            )
        else:
            version = self._version_read_service.get_latest_version(
                tenant_id=context.tenant_id,
                dataset_id=dataset_id,
            )
        selected_version_id = version.id

        schema = self._schema_read_service.get_schema_for_version(
            tenant_id=context.tenant_id,
            dataset_id=dataset_id,
            version_id=version.id,
        )
        schema_columns = {
            str(column.get("name"))
            for column in schema.schema_json
            if isinstance(column, dict) and column.get("name")
        }

        if "admin" not in context.roles:
            visible_schema = AuthorizedDataRegistryReadFacade().get_schema(
                context,
                dataset_id,
                version_id=str(version.id),
            )
            schema_columns &= set(get_queryable_schema_column_names(visible_schema.schema_json))
        invalid_column = self._sql_validator.validate_known_columns(sql, schema_columns)
        if invalid_column is not None:
            result_payload = self._invalid_column_error(invalid_column)
            self._log_query_observability(
                context=context,
                dataset_id=dataset_id,
                version_id=selected_version_id,
                result=result_payload,
                query_started_at=query_started_at,
                timeout_hit=False,
            )
            return result_payload

        artifact = self._resolve_artifact(
            tenant_id=context.tenant_id,
            dataset_id=dataset_id,
            version_id=version.id,
        )
        if (
            artifact.size_bytes is not None
            and int(artifact.size_bytes) > self._max_artifact_bytes_sync
        ):
            result_payload = dict(_DATASET_TOO_LARGE_FOR_SYNC_QUERY_ERROR)
            self._log_query_observability(
                context=context,
                dataset_id=dataset_id,
                version_id=selected_version_id,
                result=result_payload,
                query_started_at=query_started_at,
                timeout_hit=False,
            )
            return result_payload

        if not self._has_sufficient_temp_storage():
            result_payload = dict(_INSUFFICIENT_TEMP_STORAGE_ERROR)
            self._log_query_observability(
                context=context,
                dataset_id=dataset_id,
                version_id=selected_version_id,
                result=result_payload,
                query_started_at=query_started_at,
                timeout_hit=False,
            )
            return result_payload

        adapter = self._resolve_adapter(artifact=artifact)
        try:

            with (
                lease_dataset(dataset_id),
                self._resource_guard.acquire(
                    tenant_id=context.tenant_id,
                    user_id=context.actor,
                ),
            ):
                try:
                    materialized_artifact = self._dataset_cache.get_or_materialize(
                        tenant_id=context.tenant_id,
                        dataset_id=dataset_id,
                        version_id=version.id,
                        artifact=artifact,
                        adapter=adapter,
                    )
                    result_payload = self._duckdb_runner.query_file(
                        local_path=materialized_artifact.local_path,
                        sql=sql.strip(),
                        parameters=parameters,
                    )
                except DatasetCacheStorageExceededError:
                    result_payload = dict(_DATASET_CACHE_STORAGE_EXCEEDED_ERROR)
                except DatasetCacheMaterializationError:
                    result_payload = dict(_DATASET_CACHE_MATERIALIZATION_FAILED_ERROR)
                except QueryTimeoutError:
                    timeout_hit = True
                    result_payload = dict(_QUERY_TIMEOUT_ERROR)
                except DuckDbQueryExecutionError as error:
                    unknown_column = self._sql_validator.extract_unknown_column(
                        str(error.original_error),
                        schema_columns,
                    )
                    if unknown_column is not None:
                        result_payload = self._invalid_column_error(unknown_column)
                    else:
                        result_payload = dict(_QUERY_EXECUTION_ERROR)
                    self._log_duckdb_execution_failure(
                        context=context,
                        dataset_id=dataset_id,
                        version_id=selected_version_id,
                        sql=sql,
                        error=error,
                    )
                except duckdb.BinderException as error:
                    unknown_column = self._sql_validator.extract_unknown_column(
                        str(error),
                        schema_columns,
                    )
                    if unknown_column is not None:
                        result_payload = self._invalid_column_error(unknown_column)
                    else:
                        result_payload = dict(_QUERY_EXECUTION_ERROR)
                    self._log_duckdb_execution_failure(
                        context=context,
                        dataset_id=dataset_id,
                        version_id=selected_version_id,
                        sql=sql,
                        error=DuckDbQueryExecutionError(phase="query", original_error=error),
                    )
                except duckdb.Error as error:
                    result_payload = dict(_QUERY_EXECUTION_ERROR)
                    self._log_duckdb_execution_failure(
                        context=context,
                        dataset_id=dataset_id,
                        version_id=selected_version_id,
                        sql=sql,
                        error=DuckDbQueryExecutionError(phase="query", original_error=error),
                    )
        except DatasetQueryResourceLimitError as error:
            result_payload = dict(error.error_payload)

        if result_payload is None:
            result_payload = dict(_QUERY_EXECUTION_ERROR)
        if result_payload.get("success") is not False:
            result_payload["version_id"] = selected_version_id

        self._log_query_observability(
            context=context,
            dataset_id=dataset_id,
            version_id=selected_version_id,
            result=result_payload,
            query_started_at=query_started_at,
            timeout_hit=timeout_hit,
        )
        return result_payload

    @staticmethod
    def _log_duckdb_execution_failure(
        *,
        context: ReadAuthorizationContext,
        dataset_id: str,
        version_id: str | None,
        sql: str,
        error: DuckDbQueryExecutionError,
    ) -> None:
        """Log one sanitized DuckDB query failure for operator diagnosis.

        Args:
            context: Authorized tenant and request metadata.
            dataset_id: Dataset selected for the query.
            version_id: Resolved Dataset Version identifier.
            sql: Validated SQL used only to derive a non-reversible fingerprint.
            error: Safe execution phase and original DuckDB exception.
        """
        _logger.warning(
            "Dataset DuckDB query execution failed.",
            extra={
                "tenant_id": context.tenant_id,
                "dataset_id": dataset_id,
                "version_id": version_id,
                "phase": error.phase,
                "exception_type": type(error.original_error).__name__,
                "sql_fingerprint": sha256(sql.encode("utf-8")).hexdigest(),
            },
        )

    def _has_sufficient_temp_storage(self) -> bool:
        if self._temp_min_free_bytes <= 0:
            return True
        disk_usage = shutil.disk_usage(self._temp_root_directory)
        return disk_usage.free >= self._temp_min_free_bytes

    def _log_query_observability(
        self,
        *,
        context: ReadAuthorizationContext,
        dataset_id: str,
        version_id: str | None,
        result: dict[str, Any],
        query_started_at: float,
        timeout_hit: bool,
    ) -> None:
        execution_ms = int((time.perf_counter() - query_started_at) * 1000)
        error_code = result.get("code") if result.get("success") is False else None
        status = int(result.get("status", 200))
        _logger.info(
            "Dataset query execution completed.",
            extra={
                "dataset_id": dataset_id,
                "version_id": version_id,
                "tenant_id": context.tenant_id,
                "query_run_id": (
                    context.metadata.get("query_run_id")
                    if isinstance(context.metadata, dict)
                    else None
                ),
                "execution_ms": execution_ms,
                "row_count": int(result.get("row_count", 0)),
                "truncated": bool(result.get("truncated", False)),
                "error_code": error_code,
                "limit_hit": status in {413, 429, 507},
                "timeout_hit": timeout_hit or error_code == "query_timeout",
            },
        )

    def _resolve_artifact(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        version_id: str,
    ) -> DatasetArtifactSummary:
        artifacts = self._artifact_read_service.list_artifacts(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            version_id=version_id,
            limit=100,
            offset=0,
        )
        if not artifacts:
            raise ArtifactNotFoundError("Artifact not found for tenant dataset.")

        for artifact in artifacts:
            metadata = artifact.metadata_json if isinstance(artifact.metadata_json, dict) else {}
            if (
                artifact.lifecycle_status == "ACTIVE"
                and artifact.format.strip().upper() == "PARQUET"
                and bool(metadata.get("is_canonical"))
            ):
                return artifact
        for artifact in artifacts:
            if artifact.lifecycle_status == "ACTIVE" and artifact.artifact_role == "PRIMARY":
                return artifact
        for artifact in artifacts:
            if artifact.lifecycle_status == "ACTIVE":
                return artifact
        raise ArtifactNotFoundError("No active artifact is available for tenant dataset.")

    def _resolve_adapter(self, *, artifact: DatasetArtifactSummary) -> ArtifactReadAdapter:
        adapters = self._adapters or self._build_default_adapters(
            storage_provider=artifact.storage_provider,
        )
        for adapter in adapters:
            if adapter.can_handle(artifact.uri):
                return adapter
        raise ArtifactNotFoundError("Artifact not found for tenant dataset.")

    def _build_default_adapters(self, *, storage_provider: str | None) -> list[ArtifactReadAdapter]:
        adapters: list[ArtifactReadAdapter] = [
            LocalArtifactReadAdapter(
                storage_provider=self._storage_factory.create_provider("local")
            ),
        ]
        provider_key = (storage_provider or "").strip().lower()
        if provider_key == "azure_blob":
            adapters.append(
                StorageProviderArtifactReadAdapter(
                    storage_provider=self._storage_factory.create_provider("azure_blob"),
                    schemes=("azure://", "https://"),
                ),
            )
        return adapters

    def _invalid_column_error(self, column: str) -> dict[str, Any]:
        return {
            "success": False,
            "status": 400,
            "code": "invalid_column",
            "error": f"Unknown column '{column}'.",
        }
