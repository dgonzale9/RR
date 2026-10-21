from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import ClassVar

from resource_registry.services.schema_column_intelligence import (
    SchemaColumnIntelligence,
    SchemaColumnIntelligenceItem,
)


@dataclass(frozen=True)
class QueryPlan:
    intent: str
    sql: str
    columns_used: tuple[str, ...]
    result_columns: tuple[str, ...]
    limit: int
    requires_execution: bool = True


QueryPlanningStrategy = Callable[
    [str, tuple[SchemaColumnIntelligenceItem, ...]],
    QueryPlan | None,
]


class QueryIntentDetector:
    _COUNT_PATTERNS = (
        "how many rows",
        "count rows",
        "row count",
        "count of rows",
    )
    _AVG_TOKENS: ClassVar[frozenset[str]] = frozenset({"average", "avg", "mean"})
    _MIN_TOKENS: ClassVar[frozenset[str]] = frozenset({"minimum", "min", "lowest"})
    _MAX_TOKENS: ClassVar[frozenset[str]] = frozenset({"maximum", "max", "highest"})
    _SUM_TOKENS: ClassVar[frozenset[str]] = frozenset({"sum", "total"})
    _TOP_TOKENS: ClassVar[frozenset[str]] = frozenset(
        {"top", "highest", "common", "frequent", "frequency"}
    )
    _PREVIEW_PATTERNS: ClassVar[tuple[str, ...]] = (
        "preview",
        "first row",
        "show rows",
    )

    @staticmethod
    def _is_asset_inventory_lookup_request(normalized_request: str) -> bool:
        """Return whether a request has the closed Creative Assembly inventory shape."""
        has_asset_reference = "asset" in normalized_request
        has_file_reference = "file" in normalized_request
        has_filename_selector = "named" in normalized_request or "filename" in normalized_request
        has_brand_selector = "brand " in normalized_request or bool(
            re.search(r"\b(?:within|in)\s+(?:the\s+)?[^/\s]+/[^\s]+", normalized_request)
        )
        return all(
            (has_asset_reference, has_file_reference, has_filename_selector, has_brand_selector)
        )

    @staticmethod
    def _is_template_inventory_lookup_request(normalized_request: str) -> bool:
        """Return whether a request asks for a Creative Assembly template by brand."""
        return "template" in normalized_request and "brand " in normalized_request

    def detect(self, normalized_request: str, tokens: set[str]) -> str | None:
        intent: str | None = None
        requests_stats = bool(tokens.intersection(self._AVG_TOKENS)) and (
            bool(tokens.intersection(self._MIN_TOKENS))
            or bool(tokens.intersection(self._MAX_TOKENS))
        )
        if self._is_template_inventory_lookup_request(normalized_request):
            intent = "template_inventory_lookup"
        elif self._is_asset_inventory_lookup_request(normalized_request):
            intent = "asset_inventory_lookup"
        elif any(pattern in normalized_request for pattern in self._COUNT_PATTERNS):
            intent = "count_rows"
        elif "where " in normalized_request:
            intent = "simple_filter_preview"
        elif self._is_preview_rows_request(normalized_request):
            intent = "preview_rows"
        elif requests_stats and " by " not in normalized_request:
            intent = "aggregate_metric_stats"
        elif tokens.intersection(self._AVG_TOKENS):
            intent = "aggregate_avg_by_dimension"
        elif tokens.intersection(self._SUM_TOKENS):
            intent = "aggregate_sum_by_dimension"
        elif tokens.intersection(self._TOP_TOKENS):
            intent = "top_values"
        elif "most common" in normalized_request or "most frequent" in normalized_request:
            intent = "top_values"
        elif "highest count" in normalized_request:
            intent = "top_values"
        return intent

    def _is_preview_rows_request(self, normalized_request: str) -> bool:
        if "rows" not in normalized_request:
            return False
        if re.search(r"\bfirst\s+\d+\s+rows?\b", normalized_request) is not None:
            return True
        return any(pattern in normalized_request for pattern in self._PREVIEW_PATTERNS)


class SafeSqlBuilder:
    @staticmethod
    def _quote_identifier(identifier: str) -> str:
        escaped = identifier.replace('"', '""')
        return f'"{escaped}"'

    @staticmethod
    def _quote_string_literal(value: str) -> str:
        return f"'{value.replace(chr(39), chr(39) * 2)}'"

    @staticmethod
    def _metric_alias(metric: str, prefix: str) -> str:
        tokens = re.findall(r"[a-z0-9]+", metric.lower())
        normalized = "_".join(tokens).strip("_")
        if not normalized:
            normalized = "metric"
        return f"{prefix}_{normalized}"

    @staticmethod
    def count_rows() -> str:
        return "SELECT COUNT(*) AS row_count FROM dataset LIMIT 1"

    @staticmethod
    def top_values(dimension: str, limit: int) -> tuple[str, tuple[str, ...]]:
        dimension_identifier = SafeSqlBuilder._quote_identifier(dimension)
        sql = (
            f"SELECT {dimension_identifier}, COUNT(*) AS count "
            f"FROM dataset GROUP BY {dimension_identifier} ORDER BY count DESC LIMIT {limit}"
        )
        return sql, (dimension, "count")

    @staticmethod
    def aggregate_sum(metric: str, dimension: str, limit: int) -> tuple[str, tuple[str, ...]]:
        metric_identifier = SafeSqlBuilder._quote_identifier(metric)
        dimension_identifier = SafeSqlBuilder._quote_identifier(dimension)
        alias = SafeSqlBuilder._metric_alias(metric, "total")
        sql = (
            f"SELECT {dimension_identifier}, SUM({metric_identifier}) AS {alias} "
            f"FROM dataset GROUP BY {dimension_identifier} ORDER BY {alias} DESC LIMIT {limit}"
        )
        return sql, (dimension, alias)

    @staticmethod
    def aggregate_avg(metric: str, dimension: str, limit: int) -> tuple[str, tuple[str, ...]]:
        metric_identifier = SafeSqlBuilder._quote_identifier(metric)
        dimension_identifier = SafeSqlBuilder._quote_identifier(dimension)
        alias = SafeSqlBuilder._metric_alias(metric, "average")
        sql = (
            f"SELECT {dimension_identifier}, AVG({metric_identifier}) AS {alias} "
            f"FROM dataset GROUP BY {dimension_identifier} ORDER BY {alias} DESC LIMIT {limit}"
        )
        return sql, (dimension, alias)

    @staticmethod
    def aggregate_metric_stats(metric: str) -> tuple[str, tuple[str, ...]]:
        metric_identifier = SafeSqlBuilder._quote_identifier(metric)
        numeric_metric = f"TRY_CAST({metric_identifier} AS DOUBLE)"
        min_alias = SafeSqlBuilder._metric_alias(metric, "min")
        max_alias = SafeSqlBuilder._metric_alias(metric, "max")
        avg_alias = SafeSqlBuilder._metric_alias(metric, "average")
        sql = (
            f"SELECT MIN({numeric_metric}) AS {min_alias}, "
            f"MAX({numeric_metric}) AS {max_alias}, "
            f"AVG({numeric_metric}) AS {avg_alias} "
            "FROM dataset LIMIT 1"
        )
        return sql, (min_alias, max_alias, avg_alias)

    @staticmethod
    def filter_preview(
        columns: tuple[str, ...],
        filter_column: str,
        filter_value: str,
        limit: int,
    ) -> str:
        selected = ", ".join(SafeSqlBuilder._quote_identifier(column) for column in columns)
        filter_identifier = SafeSqlBuilder._quote_identifier(filter_column)
        return (
            f"SELECT {selected} FROM dataset "
            f"WHERE {filter_identifier} = {SafeSqlBuilder._quote_string_literal(filter_value)} "
            f"LIMIT {limit}"
        )

    @staticmethod
    def preview_rows(columns: tuple[str, ...], limit: int) -> str:
        selected = ", ".join(SafeSqlBuilder._quote_identifier(column) for column in columns)
        return f"SELECT {selected} FROM dataset LIMIT {limit}"

    @staticmethod
    def asset_inventory_lookup(
        columns: tuple[str, ...],
        brand: str,
        file_names: tuple[str, ...],
        limit: int,
    ) -> str:
        """Build a bounded, case-insensitive Creative Assembly inventory query."""
        selected = ", ".join(SafeSqlBuilder._quote_identifier(column) for column in columns)
        file_name_values = ", ".join(
            f"LOWER({SafeSqlBuilder._quote_string_literal(file_name)})" for file_name in file_names
        )
        return (
            f"SELECT {selected} FROM dataset "
            f"WHERE LOWER(\"brand\") = LOWER({SafeSqlBuilder._quote_string_literal(brand)}) "
            f"AND LOWER(\"file_name\") IN ({file_name_values}) LIMIT {limit}"
        )

    @staticmethod
    def template_inventory_lookup(
        columns: tuple[str, ...],
        brand: str,
        file_names: tuple[str, ...],
        limit: int,
    ) -> str:
        """Build a bounded Creative Assembly template inventory query.

        Args:
            columns: Approved non-sensitive columns to return.
            brand: Brand that owns the requested template.
            file_names: Optional exact template file names.
            limit: Maximum number of returned rows.

        Returns:
            One static-shape SQL query constrained to template asset types.
        """
        selected = ", ".join(SafeSqlBuilder._quote_identifier(column) for column in columns)
        predicates = [
            "(LOWER(\"brand\") = LOWER("
            f"{SafeSqlBuilder._quote_string_literal(brand)}) OR LOWER(\"brand\") = 'global')",
            "LOWER(\"assettype\") = LOWER('template')",
        ]
        if file_names:
            file_name_values = ", ".join(
                f"LOWER({SafeSqlBuilder._quote_string_literal(file_name)})"
                for file_name in file_names
            )
            predicates.append(f"LOWER(\"file_name\") IN ({file_name_values})")
        return f"SELECT {selected} FROM dataset WHERE {' AND '.join(predicates)} LIMIT {limit}"


class CreativeAssemblyInventoryRequestParser:
    """Extract bounded Creative Assembly inventory criteria from a normalized request."""

    _MAX_FILE_NAMES = 10
    _MAX_VALUE_LENGTH = 256
    _BRAND_PATTERN = re.compile(
        r"\bbrand\s+(.+?)(?=\s*,\s*(?:including|with|for)\b|"
        r"\s+(?:including|with|for|to\s+apply)\b|$)"
    )
    _SCOPE_BRAND_PATTERN = re.compile(
        r"\b(?:within|in)\s+(?:the\s+)?(?P<brand>[^/\s]+)/[^\s]+"
    )
    _FILE_NAMES_PATTERN = re.compile(
        r"\b(?:files?\s+named|template\s+named|exact\s+filenames?)\s+(.+?)(?="
        r"\s+(?:exist|exists|are|is|within)\b|$)"
    )
    _QUOTED_VALUE_PATTERN = re.compile(r"'([^']+)'|\"([^\"]+)\"")
    _UNQUOTED_FILE_NAME_PATTERN = re.compile(r"^(?:and\s+)?(?P<file_name>.+\.[a-z0-9]{1,10})$")

    def extract_brand(self, normalized_request: str) -> str | None:
        """Extract one bounded brand value from an inventory request.

        Args:
            normalized_request: Canonically normalized user request.

        Returns:
            A validated brand value, or None when the request has no usable brand.
        """
        match = self._BRAND_PATTERN.search(normalized_request)
        if match is not None:
            return self._validated_value(match.group(1).strip().strip("'\""))
        scope_match = self._SCOPE_BRAND_PATTERN.search(normalized_request)
        if scope_match is None:
            return None
        return self._validated_value(scope_match.group("brand").strip().strip("'\""))

    def extract_file_names(self, normalized_request: str) -> tuple[str, ...]:
        """Extract bounded quoted file names from an inventory request.

        Args:
            normalized_request: Canonically normalized user request.

        Returns:
            Exact requested names, or an empty tuple when none are specified safely.
        """
        match = self._FILE_NAMES_PATTERN.search(normalized_request)
        if match is None:
            return tuple()
        quoted_file_names = tuple(
            validated_value
            for first_value, second_value in self._QUOTED_VALUE_PATTERN.findall(match.group(1))
            if (validated_value := self._validated_value(first_value or second_value)) is not None
        )
        file_names = quoted_file_names or self._extract_unquoted_file_names(match.group(1))
        if not file_names or len(file_names) > self._MAX_FILE_NAMES:
            return tuple()
        return file_names

    def _extract_unquoted_file_names(self, raw_file_names: str) -> tuple[str, ...]:
        """Extract a bounded comma-delimited filename list without accepting free-form text."""
        file_names: list[str] = []
        for raw_value in raw_file_names.split(","):
            normalized_value = raw_value.strip().strip(" ,.")
            if not normalized_value:
                continue
            match = self._UNQUOTED_FILE_NAME_PATTERN.fullmatch(normalized_value)
            if match is None:
                return tuple()
            file_name = self._validated_value(match.group("file_name").strip())
            if file_name is None:
                return tuple()
            file_names.append(file_name)
        return tuple(file_names)

    def _validated_value(self, value: str) -> str | None:
        if not value or len(value) > self._MAX_VALUE_LENGTH:
            return None
        return value


class CreativeAssemblyAssetInventoryLookupStrategy:
    """Build closed, schema-verified inventory lookups for Creative Assembly asset selection."""

    _INTENT = "asset_inventory_lookup"
    _LIMIT = 10
    _REQUIRED_COLUMNS = frozenset({"asset_id", "brand", "file_name"})
    _SAFE_RESULT_COLUMNS = (
        "asset_id",
        "file_name",
        "format",
        "brand",
        "campaign",
        "assettype",
        "instructions",
        "relative_path",
    )

    def __init__(self, *, sql_builder: SafeSqlBuilder) -> None:
        """Initialize the strategy with the shared safe SQL builder.

        Args:
            sql_builder: Builder that escapes literals and quotes identifiers.
        """
        self._sql_builder: SafeSqlBuilder = sql_builder
        self._request_parser: CreativeAssemblyInventoryRequestParser = (
            CreativeAssemblyInventoryRequestParser()
        )

    def plan(
        self,
        normalized_request: str,
        schema_info: tuple[SchemaColumnIntelligenceItem, ...],
    ) -> QueryPlan | None:
        """Build a plan only for a complete, compatible asset-inventory request.

        Args:
            normalized_request: Canonically normalized user request.
            schema_info: Classified columns from the authorized DatasetVersion schema.

        Returns:
            A bounded query plan, or None when the request/schema is not the closed inventory shape.
        """
        columns_by_normalized_name = {
            column.normalized_name: column.name for column in schema_info if not column.is_sensitive
        }
        if not self._REQUIRED_COLUMNS.issubset(columns_by_normalized_name):
            return None
        brand = self._request_parser.extract_brand(normalized_request)
        file_names = self._request_parser.extract_file_names(normalized_request)
        if brand is None or not file_names:
            return None
        selected_columns = tuple(
            columns_by_normalized_name[column]
            for column in self._SAFE_RESULT_COLUMNS
            if column in columns_by_normalized_name
        )
        if not selected_columns:
            return None
        return QueryPlan(
            intent=self._INTENT,
            sql=self._sql_builder.asset_inventory_lookup(
                selected_columns,
                brand,
                file_names,
                self._LIMIT,
            ),
            columns_used=selected_columns,
            result_columns=selected_columns,
            limit=self._LIMIT,
        )


class CreativeAssemblyTemplateInventoryLookupStrategy:
    """Build closed, schema-verified inventory lookups for Creative Assembly templates."""

    _INTENT = "template_inventory_lookup"
    _LIMIT = 10
    _REQUIRED_COLUMNS = frozenset({"asset_id", "brand", "assettype"})
    _SAFE_RESULT_COLUMNS = (
        "asset_id",
        "file_name",
        "format",
        "brand",
        "campaign",
        "assettype",
        "instructions",
        "relative_path",
    )

    def __init__(self, *, sql_builder: SafeSqlBuilder) -> None:
        """Initialize the strategy with the shared safe SQL builder.

        Args:
            sql_builder: Builder that escapes literals and quotes identifiers.
        """
        self._sql_builder: SafeSqlBuilder = sql_builder
        self._request_parser: CreativeAssemblyInventoryRequestParser = (
            CreativeAssemblyInventoryRequestParser()
        )

    def plan(
        self,
        normalized_request: str,
        schema_info: tuple[SchemaColumnIntelligenceItem, ...],
    ) -> QueryPlan | None:
        """Build a plan for a compatible template search.

        Args:
            normalized_request: Canonically normalized user request.
            schema_info: Classified columns from the authorized DatasetVersion schema.

        Returns:
            A bounded query plan, or None when the request/schema cannot identify templates safely.
        """
        columns_by_normalized_name = {
            column.normalized_name: column.name for column in schema_info if not column.is_sensitive
        }
        if not self._REQUIRED_COLUMNS.issubset(columns_by_normalized_name):
            return None
        brand = self._request_parser.extract_brand(normalized_request)
        if brand is None:
            return None
        selected_columns = tuple(
            columns_by_normalized_name[column]
            for column in self._SAFE_RESULT_COLUMNS
            if column in columns_by_normalized_name
        )
        if not selected_columns:
            return None
        return QueryPlan(
            intent=self._INTENT,
            sql=self._sql_builder.template_inventory_lookup(
                selected_columns,
                brand,
                self._request_parser.extract_file_names(normalized_request),
                self._LIMIT,
            ),
            columns_used=selected_columns,
            result_columns=selected_columns,
            limit=self._LIMIT,
        )


class QueryPlanner:
    """Deterministic safe planner for schema-driven dataset query intents."""

    _TOP_VALUES_LIMIT = 10
    _AGG_LIMIT = 10
    _FILTER_LIMIT = 20
    _COUNT_LIMIT = 1
    _MAX_FILTER_COLUMNS = 3
    _MAX_SHORT_CODE_TOKEN_LENGTH = 3
    _DEFAULT_PREVIEW_LIMIT = 10
    _MAX_PREVIEW_LIMIT = 50

    def __init__(
        self,
        *,
        intent_detector: QueryIntentDetector | None = None,
        sql_builder: SafeSqlBuilder | None = None,
        column_intelligence: SchemaColumnIntelligence | None = None,
    ) -> None:
        self._intent_detector: QueryIntentDetector = intent_detector or QueryIntentDetector()
        self._sql_builder: SafeSqlBuilder = sql_builder or SafeSqlBuilder()
        self._column_intelligence: SchemaColumnIntelligence = (
            column_intelligence or SchemaColumnIntelligence()
        )
        self._asset_inventory_lookup_strategy: CreativeAssemblyAssetInventoryLookupStrategy = (
            CreativeAssemblyAssetInventoryLookupStrategy(sql_builder=self._sql_builder)
        )
        self._template_inventory_lookup_strategy: (
            CreativeAssemblyTemplateInventoryLookupStrategy
        ) = (
            CreativeAssemblyTemplateInventoryLookupStrategy(sql_builder=self._sql_builder)
        )
        self._planning_strategies: Mapping[str, QueryPlanningStrategy] = {
            "asset_inventory_lookup": self._asset_inventory_lookup_strategy.plan,
            "template_inventory_lookup": self._template_inventory_lookup_strategy.plan,
            "count_rows": self._plan_count_rows,
            "top_values": self._plan_top_values,
            "aggregate_sum_by_dimension": self._plan_aggregate_sum_by_dimension,
            "aggregate_avg_by_dimension": self._plan_aggregate_average_by_dimension,
            "aggregate_metric_stats": self._plan_aggregate_metric_stats,
            "simple_filter_preview": self._plan_simple_filter_preview,
            "preview_rows": self._plan_preview_rows,
        }

    def plan(
        self,
        *,
        user_request: str,
        schema_columns: tuple[str | Mapping[str, object], ...],
    ) -> QueryPlan | None:
        normalized_request = " ".join((user_request or "").strip().lower().split())
        if not normalized_request:
            return None

        schema_info = self._safe_schema_items(schema_columns)
        if not schema_info:
            return None

        tokens = set(self._tokenize(normalized_request))
        intent = self._intent_detector.detect(normalized_request, tokens)
        strategy = self._planning_strategies.get(intent or "")
        if strategy is None:
            return None
        return strategy(normalized_request, schema_info)

    def _plan_count_rows(
        self,
        _normalized_request: str,
        _schema_info: tuple[SchemaColumnIntelligenceItem, ...],
    ) -> QueryPlan:
        return QueryPlan(
            intent="count_rows",
            sql=self._sql_builder.count_rows(),
            columns_used=tuple(),
            result_columns=("row_count",),
            limit=self._COUNT_LIMIT,
        )

    def _plan_top_values(
        self,
        normalized_request: str,
        schema_info: tuple[SchemaColumnIntelligenceItem, ...],
    ) -> QueryPlan | None:
        dimension = self._column_intelligence.select_dimension(
            columns=schema_info,
            request_text=normalized_request,
        )
        if dimension is None:
            return None
        sql, result_columns = self._sql_builder.top_values(dimension.name, self._TOP_VALUES_LIMIT)
        return QueryPlan(
            intent="top_values",
            sql=sql,
            columns_used=(dimension.name,),
            result_columns=result_columns,
            limit=self._TOP_VALUES_LIMIT,
        )

    def _plan_aggregate_sum_by_dimension(
        self,
        normalized_request: str,
        schema_info: tuple[SchemaColumnIntelligenceItem, ...],
    ) -> QueryPlan | None:
        metric_column = self._resolve_requested_metric_column(normalized_request, schema_info)
        dimension_column = self._resolve_dimension_from_by_clause(normalized_request, schema_info)
        if metric_column is None or dimension_column is None:
            return None
        sql, result_columns = self._sql_builder.aggregate_sum(
            metric_column.name,
            dimension_column.name,
            self._AGG_LIMIT,
        )
        return QueryPlan(
            intent="aggregate_sum_by_dimension",
            sql=sql,
            columns_used=(dimension_column.name, metric_column.name),
            result_columns=result_columns,
            limit=self._AGG_LIMIT,
        )

    def _plan_aggregate_average_by_dimension(
        self,
        normalized_request: str,
        schema_info: tuple[SchemaColumnIntelligenceItem, ...],
    ) -> QueryPlan | None:
        metric_column = self._resolve_requested_metric_column(normalized_request, schema_info)
        dimension_column = self._resolve_dimension_from_by_clause(normalized_request, schema_info)
        if metric_column is None or dimension_column is None:
            return None
        sql, result_columns = self._sql_builder.aggregate_avg(
            metric_column.name,
            dimension_column.name,
            self._AGG_LIMIT,
        )
        return QueryPlan(
            intent="aggregate_avg_by_dimension",
            sql=sql,
            columns_used=(dimension_column.name, metric_column.name),
            result_columns=result_columns,
            limit=self._AGG_LIMIT,
        )

    def _plan_aggregate_metric_stats(
        self,
        normalized_request: str,
        schema_info: tuple[SchemaColumnIntelligenceItem, ...],
    ) -> QueryPlan | None:
        metric_column = self._resolve_requested_metric_column(normalized_request, schema_info)
        if metric_column is None:
            metric_column = self._resolve_requested_column(normalized_request, schema_info)
        if metric_column is None:
            return None
        sql, result_columns = self._sql_builder.aggregate_metric_stats(metric_column.name)
        return QueryPlan(
            intent="aggregate_metric_stats",
            sql=sql,
            columns_used=(metric_column.name,),
            result_columns=result_columns,
            limit=self._COUNT_LIMIT,
        )

    def _plan_simple_filter_preview(
        self,
        normalized_request: str,
        schema_info: tuple[SchemaColumnIntelligenceItem, ...],
    ) -> QueryPlan | None:
        parsed_filter = self._parse_filter(normalized_request, schema_info)
        if parsed_filter is None:
            return None
        filter_column, filter_value = parsed_filter
        selected_columns = self._select_filter_preview_columns(filter_column, schema_info)
        if not selected_columns:
            return None
        return QueryPlan(
            intent="simple_filter_preview",
            sql=self._sql_builder.filter_preview(
                selected_columns,
                filter_column,
                filter_value,
                self._FILTER_LIMIT,
            ),
            columns_used=selected_columns,
            result_columns=selected_columns,
            limit=self._FILTER_LIMIT,
        )

    def _plan_preview_rows(
        self,
        normalized_request: str,
        schema_info: tuple[SchemaColumnIntelligenceItem, ...],
    ) -> QueryPlan | None:
        selected_columns = self._select_preview_columns(schema_info)
        if not selected_columns:
            return None
        preview_limit = self._extract_preview_limit(normalized_request)
        return QueryPlan(
            intent="preview_rows",
            sql=self._sql_builder.preview_rows(selected_columns, preview_limit),
            columns_used=selected_columns,
            result_columns=selected_columns,
            limit=preview_limit,
        )

    def _safe_schema_items(
        self,
        schema_columns: tuple[str | Mapping[str, object], ...],
    ) -> tuple[SchemaColumnIntelligenceItem, ...]:
        """Classify typed schema definitions while tolerating legacy name-only callers."""
        schema_json: list[dict[str, object]] = [
            {"name": column, "type": ""}
            if isinstance(column, str)
            else {key: value for key, value in column.items()}
            for column in schema_columns
        ]
        return self._column_intelligence.classify_schema(schema_json)

    def _resolve_requested_metric_column(
        self,
        normalized_request: str,
        schema_info: tuple[SchemaColumnIntelligenceItem, ...],
    ) -> SchemaColumnIntelligenceItem | None:
        by_clause = re.search(r"\bby\s+([a-z0-9_ ]+)$", normalized_request)
        metric_phrase = normalized_request
        if by_clause:
            metric_phrase = normalized_request[: by_clause.start()]

        matched = self._column_intelligence.best_match_by_phrase(
            phrase=metric_phrase,
            columns=schema_info,
            require_metric_like=True,
        )
        if matched is not None:
            return matched
        return None

    def _resolve_dimension_from_by_clause(
        self,
        normalized_request: str,
        schema_info: tuple[SchemaColumnIntelligenceItem, ...],
    ) -> SchemaColumnIntelligenceItem | None:
        match = re.search(r"\bby\s+([a-z0-9_ ]+)$", normalized_request)
        if match:
            return self._column_intelligence.best_match_by_phrase(
                phrase=match.group(1),
                columns=schema_info,
                require_dimension_like=True,
            )
        return self._column_intelligence.select_dimension(
            columns=schema_info,
            request_text=normalized_request,
        )

    def _resolve_requested_column(
        self,
        normalized_request: str,
        schema_info: tuple[SchemaColumnIntelligenceItem, ...],
    ) -> SchemaColumnIntelligenceItem | None:
        return self._column_intelligence.best_match_by_phrase(
            phrase=normalized_request,
            columns=schema_info,
            require_dimension_like=False,
            require_metric_like=False,
        )

    def _parse_filter(
        self,
        normalized_request: str,
        schema_info: tuple[SchemaColumnIntelligenceItem, ...],
    ) -> tuple[str, str] | None:
        pattern = re.compile(r"\bwhere\s+(.+?)\s+(?:is|equals|=)\s+(.+)$")
        match = pattern.search(normalized_request)
        if not match:
            return None
        column_phrase = match.group(1).strip()
        column_phrase = re.sub(r"^(the|a|an)\s+", "", column_phrase).strip()
        raw_value = match.group(2).strip().strip(".?!")
        raw_value = re.sub(r"^(the|a|an)\s+", "", raw_value).strip()
        if not raw_value:
            return None

        matched = self._column_intelligence.best_match_by_phrase(
            phrase=column_phrase,
            columns=schema_info,
            require_dimension_like=True,
        )
        if matched is None:
            return None
        return matched.name, self._title_case_if_needed(raw_value)

    def _select_filter_preview_columns(
        self,
        filter_column: str,
        schema_info: tuple[SchemaColumnIntelligenceItem, ...],
    ) -> tuple[str, ...]:
        selected: list[str] = []

        id_like = [
            column.name
            for column in schema_info
            if column.is_identifier_like and not column.is_sensitive
        ]
        if id_like:
            selected.append(id_like[0])
        if filter_column not in selected:
            selected.append(filter_column)

        metric = self._column_intelligence.select_metric(columns=schema_info, request_text="")
        if metric is not None and metric.name not in selected:
            selected.append(metric.name)

        dimension = self._column_intelligence.select_dimension(columns=schema_info, request_text="")
        if dimension is not None and dimension.name not in selected:
            selected.append(dimension.name)

        for column in schema_info:
            if len(selected) >= self._MAX_FILTER_COLUMNS:
                break
            if column.name in selected or column.is_sensitive:
                continue
            selected.append(column.name)

        safe_selected = tuple(selected[: self._MAX_FILTER_COLUMNS])
        if filter_column not in safe_selected:
            return tuple()
        return safe_selected

    def _select_preview_columns(
        self,
        schema_info: tuple[SchemaColumnIntelligenceItem, ...],
    ) -> tuple[str, ...]:
        columns = [column.name for column in schema_info if not column.is_sensitive]
        if not columns:
            return tuple()
        return tuple(columns)

    def _extract_preview_limit(self, normalized_request: str) -> int:
        match = re.search(r"\bfirst\s+(\d+)\s+rows?\b", normalized_request)
        if match is None:
            return self._DEFAULT_PREVIEW_LIMIT
        requested = int(match.group(1))
        if requested <= 0:
            return self._DEFAULT_PREVIEW_LIMIT
        return min(requested, self._MAX_PREVIEW_LIMIT)

    def _tokenize(self, value: str) -> tuple[str, ...]:
        tokens: list[str] = []
        seen: set[str] = set()
        for token in re.split(r"[^a-z0-9]+", value.lower()):
            if not token or token in seen:
                continue
            tokens.append(token)
            seen.add(token)
        return tuple(tokens)

    def _title_case_if_needed(self, value: str) -> str:
        words = value.split()
        if not words:
            return value
        if all(
            word.isalpha() and len(word) <= self._MAX_SHORT_CODE_TOKEN_LENGTH for word in words
        ):
            return " ".join(word.upper() for word in words)
        if any(any(char.isupper() for char in word) for word in words):
            return value
        return value.title()
