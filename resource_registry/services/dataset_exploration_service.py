from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from resource_registry.read_auth.context import ReadAuthorizationContext
from resource_registry.read_auth.query_columns import get_queryable_schema_column_names
from resource_registry.services.dataset_query_executor import DatasetQueryExecutor
from resource_registry.services.payload_sanitizer import sanitize_payload
from resource_registry.services.schema_column_intelligence import (
    SchemaColumnIntelligence,
    SchemaColumnIntelligenceItem,
)

_DEFAULT_LIMIT = 20
_MAX_LIMIT = 100
_PROFILE_TOP_VALUES_LIMIT = 10
_PROFILE_SINGLE_ROW_LIMIT = 1
_TOP_VALUE_ROW_WIDTH = 2

SearchMode = Literal["contains", "exact", "prefix"]


@dataclass(frozen=True)
class DistinctValuesRequest:
    context: ReadAuthorizationContext
    dataset_id: str
    version_id: str | None
    schema_json: tuple[dict[str, Any], ...]
    column: str
    limit: int = _DEFAULT_LIMIT


@dataclass(frozen=True)
class TopValuesRequest:
    context: ReadAuthorizationContext
    dataset_id: str
    version_id: str | None
    schema_json: tuple[dict[str, Any], ...]
    column: str
    limit: int = _DEFAULT_LIMIT


@dataclass(frozen=True)
class SampleValuesRequest:
    context: ReadAuthorizationContext
    dataset_id: str
    version_id: str | None
    schema_json: tuple[dict[str, Any], ...]
    columns: tuple[str, ...]
    limit: int = _DEFAULT_LIMIT


@dataclass(frozen=True)
class SearchValuesRequest:
    context: ReadAuthorizationContext
    dataset_id: str
    version_id: str | None
    schema_json: tuple[dict[str, Any], ...]
    columns: tuple[str, ...]
    terms: tuple[str, ...]
    mode: SearchMode = "contains"
    limit: int = _DEFAULT_LIMIT


@dataclass(frozen=True)
class ColumnProfileRequest:
    context: ReadAuthorizationContext
    dataset_id: str
    version_id: str | None
    schema_json: tuple[dict[str, Any], ...]
    column: str


@dataclass(frozen=True)
class ExplorationValue:
    value: object


@dataclass(frozen=True)
class ExplorationTopValue:
    value: object
    count: int


@dataclass(frozen=True)
class ExplorationCell:
    column: str
    value: object


@dataclass(frozen=True)
class ExplorationSampleRow:
    cells: tuple[ExplorationCell, ...]


@dataclass(frozen=True)
class ExplorationSearchMatch:
    column: str
    value: object


@dataclass(frozen=True)
class ExplorationStatistic:
    name: str
    value: object


@dataclass(frozen=True)
class DistinctValuesResponse:
    success: bool
    status: int
    operation: str
    dataset_id: str
    version_id: str | None
    column: str
    values: tuple[ExplorationValue, ...]
    limit: int
    truncated: bool


@dataclass(frozen=True)
class TopValuesResponse:
    success: bool
    status: int
    operation: str
    dataset_id: str
    version_id: str | None
    column: str
    values: tuple[ExplorationTopValue, ...]
    limit: int
    truncated: bool


@dataclass(frozen=True)
class SampleValuesResponse:
    success: bool
    status: int
    operation: str
    dataset_id: str
    version_id: str | None
    columns: tuple[str, ...]
    rows: tuple[ExplorationSampleRow, ...]
    limit: int
    truncated: bool


@dataclass(frozen=True)
class SearchValuesResponse:
    success: bool
    status: int
    operation: str
    dataset_id: str
    version_id: str | None
    columns: tuple[str, ...]
    matches: tuple[ExplorationSearchMatch, ...]
    limit: int
    truncated: bool


@dataclass(frozen=True)
class ColumnProfileResponse:
    success: bool
    status: int
    operation: str
    dataset_id: str
    version_id: str | None
    column: str
    data_type: str
    profile_kind: str
    statistics: tuple[ExplorationStatistic, ...]
    top_values: tuple[ExplorationTopValue, ...]
    truncated: bool


@dataclass(frozen=True)
class DatasetExplorationErrorResponse:
    success: bool
    status: int
    code: str
    error: str


DatasetExplorationResponse = (
    DistinctValuesResponse
    | TopValuesResponse
    | SampleValuesResponse
    | SearchValuesResponse
    | ColumnProfileResponse
    | DatasetExplorationErrorResponse
)


class DatasetExplorationGovernancePolicy(Protocol):
    """Validate whether a resolved schema column may be explored."""

    def can_explore_column(
        self,
        *,
        context: ReadAuthorizationContext,
        column: SchemaColumnIntelligenceItem,
        operation: str,
    ) -> bool:
        """Return whether the caller can explore the resolved column."""


class DefaultDatasetExplorationGovernancePolicy:
    """Allow exploration for non-sensitive resolved columns."""

    def can_explore_column(
        self,
        *,
        context: ReadAuthorizationContext,
        column: SchemaColumnIntelligenceItem,
        operation: str,
    ) -> bool:
        """Return whether the resolved column is allowed by the default policy."""
        _ = context, operation
        return not column.is_sensitive


class DatasetExplorationService:
    """Build deterministic dataset exploration queries and delegate execution."""

    def __init__(
        self,
        *,
        query_executor: DatasetQueryExecutor | None = None,
        column_intelligence: SchemaColumnIntelligence | None = None,
        governance_policy: DatasetExplorationGovernancePolicy | None = None,
    ) -> None:
        self._query_executor = query_executor or DatasetQueryExecutor()
        self._column_intelligence = column_intelligence or SchemaColumnIntelligence()
        self._governance_policy = governance_policy or DefaultDatasetExplorationGovernancePolicy()

    def get_distinct_values(
        self,
        request: DistinctValuesRequest,
    ) -> DistinctValuesResponse | DatasetExplorationErrorResponse:
        """Return bounded distinct values for one resolved column."""
        column = self._resolve_single_column(
            context=request.context,
            schema_json=request.schema_json,
            requested_column=request.column,
            operation="distinct_values",
        )
        if isinstance(column, DatasetExplorationErrorResponse):
            return column

        limit = self._normalize_limit(request.limit)
        quoted_column = self._quote_identifier(column.name)
        sql = (
            f"SELECT {quoted_column} AS value FROM dataset "
            f"GROUP BY {quoted_column} ORDER BY value ASC LIMIT {limit}"
        )
        result = self._execute_query(
            context=request.context,
            dataset_id=request.dataset_id,
            version_id=request.version_id,
            sql=sql,
        )
        if isinstance(result, DatasetExplorationErrorResponse):
            return result

        return DistinctValuesResponse(
            success=True,
            status=200,
            operation="distinct_values",
            dataset_id=request.dataset_id,
            version_id=request.version_id,
            column=column.name,
            values=tuple(
                ExplorationValue(value=self._sanitize_value(row[0]))
                for row in result.get("rows", [])
                if isinstance(row, list) and row
            ),
            limit=limit,
            truncated=bool(result.get("truncated", False)),
        )

    def get_top_values(
        self,
        request: TopValuesRequest,
    ) -> TopValuesResponse | DatasetExplorationErrorResponse:
        """Return most frequent values for one resolved column."""
        column = self._resolve_single_column(
            context=request.context,
            schema_json=request.schema_json,
            requested_column=request.column,
            operation="top_values",
        )
        if isinstance(column, DatasetExplorationErrorResponse):
            return column

        limit = self._normalize_limit(request.limit)
        quoted_column = self._quote_identifier(column.name)
        sql = (
            f"SELECT {quoted_column} AS value, COUNT(*) AS count FROM dataset "
            f"GROUP BY {quoted_column} ORDER BY count DESC, value ASC LIMIT {limit}"
        )
        result = self._execute_query(
            context=request.context,
            dataset_id=request.dataset_id,
            version_id=request.version_id,
            sql=sql,
        )
        if isinstance(result, DatasetExplorationErrorResponse):
            return result

        return TopValuesResponse(
            success=True,
            status=200,
            operation="top_values",
            dataset_id=request.dataset_id,
            version_id=request.version_id,
            column=column.name,
            values=tuple(
                ExplorationTopValue(value=self._sanitize_value(row[0]), count=self._to_int(row[1]))
                for row in result.get("rows", [])
                if isinstance(row, list) and len(row) >= _TOP_VALUE_ROW_WIDTH
            ),
            limit=limit,
            truncated=bool(result.get("truncated", False)),
        )

    def get_sample_values(
        self,
        request: SampleValuesRequest,
    ) -> SampleValuesResponse | DatasetExplorationErrorResponse:
        """Return bounded sample rows for resolved columns."""
        columns = self._resolve_many_columns(
            context=request.context,
            schema_json=request.schema_json,
            requested_columns=request.columns,
            operation="sample_values",
        )
        if isinstance(columns, DatasetExplorationErrorResponse):
            return columns

        limit = self._normalize_limit(request.limit)
        selected_columns = tuple(column.name for column in columns)
        select_clause = ", ".join(self._quote_identifier(column) for column in selected_columns)
        sql = f"SELECT {select_clause} FROM dataset LIMIT {limit}"
        result = self._execute_query(
            context=request.context,
            dataset_id=request.dataset_id,
            version_id=request.version_id,
            sql=sql,
        )
        if isinstance(result, DatasetExplorationErrorResponse):
            return result

        return SampleValuesResponse(
            success=True,
            status=200,
            operation="sample_values",
            dataset_id=request.dataset_id,
            version_id=request.version_id,
            columns=selected_columns,
            rows=self._build_sample_rows(result, selected_columns),
            limit=limit,
            truncated=bool(result.get("truncated", False)),
        )

    def search_values(
        self,
        request: SearchValuesRequest,
    ) -> SearchValuesResponse | DatasetExplorationErrorResponse:
        """Search textual values in resolved columns using parameterized terms."""
        columns = self._resolve_many_columns(
            context=request.context,
            schema_json=request.schema_json,
            requested_columns=request.columns,
            operation="search_values",
        )
        if isinstance(columns, DatasetExplorationErrorResponse):
            return columns

        if request.mode not in {"contains", "exact", "prefix"}:
            return self._error(
                code="unsupported_search_mode",
                error="The requested search mode is not supported.",
            )
        terms = self._normalize_terms(request.terms)
        if not terms:
            return self._error(
                code="missing_search_terms",
                error="At least one non-empty search term is required.",
            )

        limit = self._normalize_limit(request.limit)
        matches: list[ExplorationSearchMatch] = []
        truncated = False
        for column in columns:
            if not self._is_textual(column):
                return self._error(
                    code="unsupported_column_type",
                    error="Search values is supported only for textual columns.",
                )
            remaining = limit - len(matches)
            if remaining <= 0:
                truncated = True
                break
            result = self._search_single_column(
                request=request,
                column=column,
                terms=terms,
                limit=remaining,
            )
            if isinstance(result, DatasetExplorationErrorResponse):
                return result
            for row in result.get("rows", []):
                if not isinstance(row, list) or not row:
                    continue
                matches.append(
                    ExplorationSearchMatch(
                        column=column.name,
                        value=self._sanitize_value(row[0]),
                    ),
                )
            truncated = truncated or bool(result.get("truncated", False))

        return SearchValuesResponse(
            success=True,
            status=200,
            operation="search_values",
            dataset_id=request.dataset_id,
            version_id=request.version_id,
            columns=tuple(column.name for column in columns),
            matches=tuple(matches[:limit]),
            limit=limit,
            truncated=truncated or len(matches) > limit,
        )

    def profile_column(
        self,
        request: ColumnProfileRequest,
    ) -> ColumnProfileResponse | DatasetExplorationErrorResponse:
        """Return a deterministic profile for one resolved column."""
        column = self._resolve_single_column(
            context=request.context,
            schema_json=request.schema_json,
            requested_column=request.column,
            operation="column_profile",
        )
        if isinstance(column, DatasetExplorationErrorResponse):
            return column

        profile_kind = self._profile_kind(column)
        profile_result = self._execute_query(
            context=request.context,
            dataset_id=request.dataset_id,
            version_id=request.version_id,
            sql=self._build_profile_sql(column, profile_kind),
        )
        if isinstance(profile_result, DatasetExplorationErrorResponse):
            return profile_result

        top_result = self.get_top_values(
            TopValuesRequest(
                context=request.context,
                dataset_id=request.dataset_id,
                version_id=request.version_id,
                schema_json=request.schema_json,
                column=column.name,
                limit=_PROFILE_TOP_VALUES_LIMIT,
            ),
        )
        if isinstance(top_result, DatasetExplorationErrorResponse):
            return top_result

        return ColumnProfileResponse(
            success=True,
            status=200,
            operation="column_profile",
            dataset_id=request.dataset_id,
            version_id=request.version_id,
            column=column.name,
            data_type=column.data_type,
            profile_kind=profile_kind,
            statistics=self._build_statistics(profile_result),
            top_values=top_result.values,
            truncated=top_result.truncated,
        )

    def _search_single_column(
        self,
        *,
        request: SearchValuesRequest,
        column: SchemaColumnIntelligenceItem,
        terms: tuple[str, ...],
        limit: int,
    ) -> dict[str, Any] | DatasetExplorationErrorResponse:
        quoted_column = self._quote_identifier(column.name)
        predicate_fragments = []
        parameters: list[Any] = []
        for term in terms:
            operator = self._operator(request.mode)
            escape_clause = " ESCAPE '\\'" if operator == "LIKE" else ""
            predicate_fragments.append(
                f"LOWER(CAST({quoted_column} AS VARCHAR)) {operator} ?{escape_clause}",
            )
            parameters.append(self._search_parameter(term, request.mode))
        where_clause = " OR ".join(predicate_fragments)
        sql = (
            f"SELECT {quoted_column} AS value FROM dataset "
            f"WHERE {where_clause} GROUP BY {quoted_column} ORDER BY value ASC LIMIT {limit}"
        )
        return self._execute_query(
            context=request.context,
            dataset_id=request.dataset_id,
            version_id=request.version_id,
            sql=sql,
            parameters=tuple(parameters),
        )

    def _execute_query(
        self,
        *,
        context: ReadAuthorizationContext,
        dataset_id: str,
        version_id: str | None,
        sql: str,
        parameters: tuple[Any, ...] = (),
    ) -> dict[str, Any] | DatasetExplorationErrorResponse:
        result = self._query_executor.execute_query(
            context=context,
            dataset_id=dataset_id,
            version_id=version_id,
            sql=sql,
            parameters=parameters,
        )
        if result.get("success") is False:
            return DatasetExplorationErrorResponse(
                success=False,
                status=int(result.get("status", 400)),
                code=str(result.get("code", "dataset_exploration_query_failed")),
                error=str(result.get("error", "Dataset exploration query failed.")),
            )
        return result

    def _resolve_single_column(
        self,
        *,
        context: ReadAuthorizationContext,
        schema_json: tuple[dict[str, Any], ...],
        requested_column: str,
        operation: str,
    ) -> SchemaColumnIntelligenceItem | DatasetExplorationErrorResponse:
        resolved = self._resolve_many_columns(
            context=context,
            schema_json=schema_json,
            requested_columns=(requested_column,),
            operation=operation,
        )
        if isinstance(resolved, DatasetExplorationErrorResponse):
            return resolved
        return resolved[0]

    def _resolve_many_columns(  # noqa: PLR0911
        self,
        *,
        context: ReadAuthorizationContext,
        schema_json: tuple[dict[str, Any], ...],
        requested_columns: tuple[str, ...],
        operation: str,
    ) -> tuple[SchemaColumnIntelligenceItem, ...] | DatasetExplorationErrorResponse:
        if not requested_columns:
            return self._error(
                code="missing_exploration_column",
                error="At least one exploration column is required.",
            )
        queryable_names = get_queryable_schema_column_names(schema_json)
        columns = self._column_intelligence.classify_schema(
            [column for column in schema_json if column.get("name") in queryable_names],
        )
        by_normalized_name: dict[str, list[SchemaColumnIntelligenceItem]] = {}
        for column in columns:
            by_normalized_name.setdefault(column.name.strip().lower(), []).append(column)

        resolved: list[SchemaColumnIntelligenceItem] = []
        seen: set[str] = set()
        for raw_column in requested_columns:
            normalized = str(raw_column or "").strip().lower()
            if not normalized:
                return self._error(
                    code="missing_exploration_column",
                    error="Exploration column names must be non-empty.",
                )
            if normalized in seen:
                continue
            seen.add(normalized)
            matches = by_normalized_name.get(normalized, [])
            if not matches:
                return self._error(
                    code="unknown_exploration_column",
                    error="The requested exploration column does not exist in the schema.",
                )
            if len(matches) > 1:
                return self._error(
                    code="ambiguous_exploration_column",
                    error="The requested exploration column is ambiguous in the schema.",
                )
            column = matches[0]
            if not self._is_supported_column_type(column):
                return self._error(
                    code="unsupported_column_type",
                    error="The requested exploration column type is not supported.",
                )
            if not self._governance_policy.can_explore_column(
                context=context,
                column=column,
                operation=operation,
            ):
                return self._error(
                    code="exploration_column_not_allowed",
                    error="The requested exploration column is not allowed by policy.",
                )
            resolved.append(column)

        return tuple(resolved)

    def _build_sample_rows(
        self,
        result: dict[str, Any],
        selected_columns: tuple[str, ...],
    ) -> tuple[ExplorationSampleRow, ...]:
        rows: list[ExplorationSampleRow] = []
        for raw_row in result.get("rows", []):
            if not isinstance(raw_row, list):
                continue
            cells = tuple(
                ExplorationCell(
                    column=selected_columns[index],
                    value=self._sanitize_value(raw_row[index]),
                )
                for index in range(min(len(selected_columns), len(raw_row)))
            )
            rows.append(ExplorationSampleRow(cells=cells))
        return tuple(rows)

    def _build_profile_sql(self, column: SchemaColumnIntelligenceItem, profile_kind: str) -> str:
        quoted_column = self._quote_identifier(column.name)
        null_count = f"SUM(CASE WHEN {quoted_column} IS NULL THEN 1 ELSE 0 END) AS null_count"
        if profile_kind == "numeric":
            return (
                f"SELECT COUNT(*) AS row_count, {null_count}, "
                f"MIN(TRY_CAST({quoted_column} AS DOUBLE)) AS min_value, "
                f"MAX(TRY_CAST({quoted_column} AS DOUBLE)) AS max_value, "
                f"AVG(TRY_CAST({quoted_column} AS DOUBLE)) AS average_value "
                f"FROM dataset LIMIT {_PROFILE_SINGLE_ROW_LIMIT}"
            )
        if profile_kind == "temporal":
            return (
                f"SELECT COUNT(*) AS row_count, {null_count}, "
                f"MIN({quoted_column}) AS min_value, MAX({quoted_column}) AS max_value "
                f"FROM dataset LIMIT {_PROFILE_SINGLE_ROW_LIMIT}"
            )
        if profile_kind == "boolean":
            return (
                f"SELECT COUNT(*) AS row_count, {null_count}, "
                f"COUNT(DISTINCT {quoted_column}) AS distinct_count "
                f"FROM dataset LIMIT {_PROFILE_SINGLE_ROW_LIMIT}"
            )
        return (
            f"SELECT COUNT(*) AS row_count, {null_count}, "
            f"COUNT(DISTINCT {quoted_column}) AS distinct_count, "
            f"AVG(LENGTH(CAST({quoted_column} AS VARCHAR))) AS average_length "
            f"FROM dataset LIMIT {_PROFILE_SINGLE_ROW_LIMIT}"
        )

    def _build_statistics(self, result: dict[str, Any]) -> tuple[ExplorationStatistic, ...]:
        columns = [str(column) for column in result.get("columns", [])]
        rows = result.get("rows", [])
        if not rows or not isinstance(rows[0], list):
            return tuple()
        first_row = rows[0]
        return tuple(
            ExplorationStatistic(name=columns[index], value=self._sanitize_value(first_row[index]))
            for index in range(min(len(columns), len(first_row)))
        )

    def _normalize_terms(self, terms: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        seen: set[str] = set()
        for term in terms:
            clean = str(term or "").strip().lower()
            if not clean or clean in seen:
                continue
            seen.add(clean)
            normalized.append(clean)
        return tuple(normalized)

    def _sanitize_value(self, value: object) -> object:
        payload = sanitize_payload({"value": value})
        return payload.get("value")

    def _normalize_limit(self, raw_limit: int) -> int:
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            limit = _DEFAULT_LIMIT
        return min(max(limit, 1), _MAX_LIMIT)

    def _search_parameter(self, term: str, mode: SearchMode) -> str:
        if mode == "exact":
            return term
        if mode == "prefix":
            return f"{self._escape_like(term)}%"
        return f"%{self._escape_like(term)}%"

    def _operator(self, mode: SearchMode) -> str:
        if mode == "exact":
            return "="
        return "LIKE"

    def _escape_like(self, value: str) -> str:
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    def _profile_kind(self, column: SchemaColumnIntelligenceItem) -> str:
        if column.is_numeric:
            return "numeric"
        if column.is_datetime:
            return "temporal"
        if column.is_boolean:
            return "boolean"
        return "text"

    def _is_textual(self, column: SchemaColumnIntelligenceItem) -> bool:
        return not column.is_numeric and not column.is_datetime and not column.is_boolean

    def _is_supported_column_type(self, column: SchemaColumnIntelligenceItem) -> bool:
        _ = column
        return True

    def _quote_identifier(self, identifier: str) -> str:
        escaped = identifier.replace('"', '""')
        return f'"{escaped}"'

    def _to_int(self, value: object) -> int:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float | str):
            return int(value)
        return 0

    def _error(
        self,
        *,
        code: str,
        error: str,
        status: int = 400,
    ) -> DatasetExplorationErrorResponse:
        return DatasetExplorationErrorResponse(
            success=False,
            status=status,
            code=code,
            error=error,
        )
