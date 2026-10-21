from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from resource_registry.read_auth.context import ReadAuthorizationContext
from resource_registry.services.dataset_query_executor import DatasetQueryExecutor
from resource_registry.services.payload_sanitizer import sanitize_payload
from resource_registry.services.schema_column_intelligence import (
    SchemaColumnIntelligence,
    SchemaColumnIntelligenceItem,
)

_MAX_BAR_ROWS = 10
_MAX_PIE_ROWS = 6
_MAX_LINE_ROWS = 24
_MAX_SCATTER_ROWS = 100
_MAX_TABLE_ROWS = 20
_MAX_TABLE_COLUMNS = 5
_MIN_SCATTER_COLUMNS = 2


@dataclass(frozen=True)
class ChartPlan:
    chart_type: str
    title: str
    description: str
    sql: str
    x: str | None
    y: str | None


class DatasetChartService:
    """Deterministic read-only chart specification service."""

    SUPPORTED_CHART_TYPES = frozenset({"bar", "line", "pie", "scatter", "table"})

    def __init__(
        self,
        *,
        query_executor: DatasetQueryExecutor | None = None,
        column_intelligence: SchemaColumnIntelligence | None = None,
    ) -> None:
        self._query_executor = query_executor or DatasetQueryExecutor()
        self._column_intelligence = column_intelligence or SchemaColumnIntelligence()

    def create_chart(  # noqa: PLR0913
        self,
        *,
        context: ReadAuthorizationContext,
        dataset_id: str,
        version_id: str | None,
        user_request: str,
        chart_type: str,
        schema_json: list[dict[str, Any]],
    ) -> dict[str, Any]:
        normalized_chart_type = str(chart_type).strip().lower()
        if normalized_chart_type not in self.SUPPORTED_CHART_TYPES:
            return self._unsupported_chart_request_error()

        request = " ".join(str(user_request or "").strip().lower().split())
        column_infos = self._column_intelligence.classify_schema(schema_json)

        plan = self._build_chart_plan(
            chart_type=normalized_chart_type,
            user_request=request,
            column_infos=column_infos,
        )
        if plan is None:
            return self._unsupported_chart_request_error()

        query_result = self._query_executor.execute_query(
            context=context,
            dataset_id=dataset_id,
            version_id=version_id,
            sql=plan.sql,
        )
        if query_result.get("success") is False:
            return query_result

        rows_as_dicts = self._rows_as_dicts(query_result)
        payload = {
            "success": True,
            "status": 200,
            "dataset_id": dataset_id,
            "version_id": version_id,
            "chart": {
                "chart_type": plan.chart_type,
                "title": plan.title,
                "description": plan.description,
                "source_query": {"sql": plan.sql},
                "x": plan.x,
                "y": plan.y,
                "data": rows_as_dicts,
            },
        }
        return self._sanitize_payload(payload)

    def _build_chart_plan(  # noqa: PLR0911
        self,
        *,
        chart_type: str,
        user_request: str,
        column_infos: tuple[SchemaColumnIntelligenceItem, ...],
    ) -> ChartPlan | None:
        if chart_type == "bar":
            dimension = self._column_intelligence.select_dimension(
                columns=column_infos,
                request_text=user_request,
            )
            metric = self._column_intelligence.select_metric(
                columns=column_infos,
                request_text=user_request,
            )
            if dimension is None or metric is None:
                return None
            metric_alias = f"total_{metric.name}"
            return ChartPlan(
                chart_type="bar",
                title=f"Total {self._label(metric.name)} by {self._label(dimension.name)}",
                description=(
                    f"Aggregated total {self._label(metric.name).lower()} grouped by "
                    f"{self._label(dimension.name).lower()}."
                ),
                sql=(
                    f"SELECT {dimension.name}, SUM({metric.name}) AS {metric_alias} "
                    f"FROM dataset GROUP BY {dimension.name} ORDER BY {metric_alias} DESC "
                    f"LIMIT {_MAX_BAR_ROWS}"
                ),
                x=dimension.name,
                y=metric_alias,
            )

        if chart_type == "pie":
            dimension = self._column_intelligence.select_dimension(
                columns=column_infos,
                request_text=user_request,
            )
            if dimension is None:
                return None
            return ChartPlan(
                chart_type="pie",
                title=f"Distribution by {self._label(dimension.name)}",
                description=f"Category share by {self._label(dimension.name).lower()}.",
                sql=(
                    f"SELECT {dimension.name}, COUNT(*) AS count FROM dataset "
                    f"GROUP BY {dimension.name} ORDER BY count DESC LIMIT {_MAX_PIE_ROWS}"
                ),
                x=dimension.name,
                y="count",
            )

        if chart_type == "line":
            period_column = self._column_intelligence.select_datetime(
                columns=column_infos,
                request_text=user_request,
            )
            metric = self._column_intelligence.select_metric(
                columns=column_infos,
                request_text=user_request,
            )
            if period_column is None or metric is None:
                return None
            metric_alias = f"total_{metric.name}"
            return ChartPlan(
                chart_type="line",
                title=f"{self._label(metric.name)} Trend",
                description=(
                    f"Monthly trend of {self._label(metric.name).lower()} over "
                    f"{self._label(period_column.name).lower()}."
                ),
                sql=(
                    f"SELECT DATE_TRUNC('month', {period_column.name}) AS period, "
                    f"SUM({metric.name}) AS {metric_alias} FROM dataset "
                    f"GROUP BY period ORDER BY period LIMIT {_MAX_LINE_ROWS}"
                ),
                x="period",
                y=metric_alias,
            )

        if chart_type == "scatter":
            first_metric, second_metric = self._resolve_scatter_columns(user_request, column_infos)
            if first_metric is None or second_metric is None:
                return None
            return ChartPlan(
                chart_type="scatter",
                title=f"{self._label(first_metric)} vs {self._label(second_metric)}",
                description=(
                    f"Scatter relation between {self._label(first_metric).lower()} and "
                    f"{self._label(second_metric).lower()}."
                ),
                sql=(
                    f"SELECT {first_metric}, {second_metric} FROM dataset "
                    f"WHERE {first_metric} IS NOT NULL AND {second_metric} IS NOT NULL "
                    f"LIMIT {_MAX_SCATTER_ROWS}"
                ),
                x=first_metric,
                y=second_metric,
            )

        if chart_type == "table":
            selected_columns = self._column_intelligence.select_table_columns(
                columns=column_infos,
                request_text=user_request,
                max_columns=_MAX_TABLE_COLUMNS,
            )
            if not selected_columns:
                return None
            column_list = ", ".join(selected_columns)
            return ChartPlan(
                chart_type="table",
                title="Dataset Table Preview",
                description="Controlled tabular view with selected columns and row limit.",
                sql=(f"SELECT {column_list} FROM dataset LIMIT {_MAX_TABLE_ROWS}"),
                x=None,
                y=None,
            )

        return None

    def _resolve_scatter_columns(
        self,
        user_request: str,
        column_infos: tuple[SchemaColumnIntelligenceItem, ...],
    ) -> tuple[str | None, str | None]:
        numeric_metrics = self._column_intelligence.list_numeric_metrics(column_infos)
        if len(numeric_metrics) < _MIN_SCATTER_COLUMNS:
            return None, None

        first_match = self._column_intelligence.best_match_by_phrase(
            phrase=user_request,
            columns=column_infos,
            require_metric_like=True,
        )
        if first_match is not None:
            for metric_name in numeric_metrics:
                if metric_name != first_match.name:
                    return first_match.name, metric_name
        return numeric_metrics[0], numeric_metrics[1]

    def _rows_as_dicts(self, result: dict[str, Any]) -> list[dict[str, Any]]:
        columns = [str(column) for column in result.get("columns", [])]
        rows = result.get("rows", [])
        output: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, list):
                continue
            output.append(
                {
                    columns[index]: row[index]
                    for index in range(min(len(columns), len(row)))
                },
            )
        return output

    def _unsupported_chart_request_error(self) -> dict[str, Any]:
        return {
            "success": False,
            "status": 400,
            "code": "unsupported_chart_request",
            "error": "The requested chart cannot be safely generated yet.",
        }

    def _label(self, value: str) -> str:
        return value.replace("_", " ").strip().title()

    def _sanitize_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return sanitize_payload(payload)
