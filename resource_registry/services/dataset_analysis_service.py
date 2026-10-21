from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from resource_registry.read_auth.context import ReadAuthorizationContext
from resource_registry.services.dataset_query_executor import DatasetQueryExecutor
from resource_registry.services.payload_sanitizer import sanitize_payload
from resource_registry.services.schema_column_intelligence import SchemaColumnIntelligence

_SUMMARY_COUNT_QUERY = "SELECT COUNT(*) AS row_count FROM dataset LIMIT 1"
_PREVIEW_LIMIT = 10
_TOP_CATEGORIES_LIMIT = 10
_DISTRIBUTION_LIMIT = 1
_CAMPAIGN_LIMIT = 10


@dataclass(frozen=True)
class AnalysisQuery:
    name: str
    sql: str


class DatasetAnalysisService:
    """Deterministic read-only dataset analysis built on safe query execution."""

    SUPPORTED_ANALYSIS_TYPES = frozenset(
        {"summary", "preview", "top_categories", "distribution", "campaign_opportunities"},
    )

    def __init__(
        self,
        *,
        query_executor: DatasetQueryExecutor | None = None,
        column_intelligence: SchemaColumnIntelligence | None = None,
    ) -> None:
        self._query_executor = query_executor or DatasetQueryExecutor()
        self._column_intelligence = column_intelligence or SchemaColumnIntelligence()

    def analyze(  # noqa: PLR0911, PLR0912, PLR0913, PLR0915
        self,
        *,
        context: ReadAuthorizationContext,
        dataset_id: str,
        version_id: str | None,
        analysis_type: str,
        schema_json: list[dict[str, Any]],
        goal: str | None,
    ) -> dict[str, Any]:
        normalized_type = str(analysis_type).strip().lower()
        if normalized_type not in self.SUPPORTED_ANALYSIS_TYPES:
            return {
                "success": False,
                "status": 400,
                "code": "unsupported_analysis_type",
                "error": "The requested analysis_type is not supported yet.",
            }

        column_infos = self._column_intelligence.classify_schema(schema_json)
        numeric_columns = self._column_intelligence.list_numeric_metrics(column_infos)
        categorical_columns = self._column_intelligence.list_categorical_dimensions(column_infos)
        queries_used: list[AnalysisQuery] = []

        if normalized_type == "summary":
            count_result = self._run_query(
                context=context,
                dataset_id=dataset_id,
                version_id=version_id,
                query_name="row_count",
                sql=_SUMMARY_COUNT_QUERY,
                queries_used=queries_used,
            )
            if count_result.get("success") is False:
                return count_result
            row_count = self._extract_first_scalar(count_result)
            payload = {
                "success": True,
                "status": 200,
                "dataset_id": dataset_id,
                "version_id": version_id,
                "analysis_type": normalized_type,
                "summary": {
                    "row_count": row_count,
                    "column_count": len(schema_json),
                    "detected_numeric_columns": list(numeric_columns),
                    "detected_categorical_columns": list(categorical_columns),
                    "basic_null_count_metadata_available": False,
                },
                "insights": [],
                "queries_used": [query.__dict__ for query in queries_used],
            }
            return self._sanitize_payload(payload)

        if normalized_type == "preview":
            preview_columns = self._extract_preview_columns(schema_json)
            if not preview_columns:
                return self._unsupported_analysis_type_error()
            preview_sql = (
                "SELECT "
                + ", ".join(self._quote_identifier(column) for column in preview_columns)
                + f" FROM dataset LIMIT {_PREVIEW_LIMIT}"
            )
            preview_result = self._run_query(
                context=context,
                dataset_id=dataset_id,
                version_id=version_id,
                query_name="sample_rows",
                sql=preview_sql,
                queries_used=queries_used,
            )
            if preview_result.get("success") is False:
                return preview_result
            sample_rows = self._rows_as_dicts(preview_result)
            payload = {
                "success": True,
                "status": 200,
                "dataset_id": dataset_id,
                "version_id": version_id,
                "analysis_type": normalized_type,
                "summary": {
                    "sample_row_count": len(sample_rows),
                    "sample_limit": _PREVIEW_LIMIT,
                    "returned_columns": len(preview_result.get("columns", [])),
                    "truncated": bool(preview_result.get("truncated", False)),
                },
                "rows": sample_rows,
                "insights": [],
                "queries_used": [query.__dict__ for query in queries_used],
            }
            return self._sanitize_payload(payload)

        if normalized_type == "top_categories":
            dimension_info = self._column_intelligence.select_dimension(
                columns=column_infos,
                request_text=str(goal or ""),
                goal_text=str(goal or ""),
            )
            if dimension_info is None:
                return self._unsupported_analysis_type_error()
            dimension = dimension_info.name
            quoted_dimension = self._quote_identifier(dimension)
            sql = (
                f"SELECT {quoted_dimension}, COUNT(*) AS count FROM dataset "
                f"GROUP BY {quoted_dimension} ORDER BY count DESC LIMIT {_TOP_CATEGORIES_LIMIT}"
            )
            top_result = self._run_query(
                context=context,
                dataset_id=dataset_id,
                version_id=version_id,
                query_name=f"top_{dimension}",
                sql=sql,
                queries_used=queries_used,
            )
            if top_result.get("success") is False:
                return top_result
            top_rows = self._rows_as_dicts(top_result)
            payload = {
                "success": True,
                "status": 200,
                "dataset_id": dataset_id,
                "version_id": version_id,
                "analysis_type": normalized_type,
                "summary": f"Top {dimension} categories by row count.",
                "insights": [
                    {
                        "title": f"Top {dimension} segments",
                        "evidence": top_rows,
                        "recommended_action": (
                            f"Prioritize campaigns for leading {dimension} segments."
                        ),
                        "confidence": "medium",
                    },
                ],
                "queries_used": [query.__dict__ for query in queries_used],
            }
            return self._sanitize_payload(payload)

        if normalized_type == "distribution":
            metric_info = self._column_intelligence.select_metric(
                columns=column_infos,
                request_text=str(goal or ""),
                goal_text=str(goal or ""),
            )
            if metric_info is None:
                return self._unsupported_analysis_type_error()
            metric = metric_info.name
            quoted_metric = self._quote_identifier(metric)
            metric_alias = self._safe_alias(f"avg_{metric}")
            min_alias = self._safe_alias(f"min_{metric}")
            max_alias = self._safe_alias(f"max_{metric}")
            sql = (
                f"SELECT MIN({quoted_metric}) AS {min_alias}, "
                f"MAX({quoted_metric}) AS {max_alias}, "
                f"AVG({quoted_metric}) AS {metric_alias} "
                f"FROM dataset LIMIT {_DISTRIBUTION_LIMIT}"
            )
            distribution_result = self._run_query(
                context=context,
                dataset_id=dataset_id,
                version_id=version_id,
                query_name=f"distribution_{metric}",
                sql=sql,
                queries_used=queries_used,
            )
            if distribution_result.get("success") is False:
                return distribution_result
            evidence_rows = self._rows_as_dicts(distribution_result)
            payload = {
                "success": True,
                "status": 200,
                "dataset_id": dataset_id,
                "version_id": version_id,
                "analysis_type": normalized_type,
                "summary": f"Distribution profile for {metric}.",
                "insights": [
                    {
                        "title": f"{metric} range and average",
                        "evidence": evidence_rows,
                        "recommended_action": (
                            f"Use {metric} thresholds to segment campaign audiences."
                        ),
                        "confidence": "medium",
                    },
                ],
                "queries_used": [query.__dict__ for query in queries_used],
            }
            return self._sanitize_payload(payload)

        dimension_info = self._column_intelligence.select_dimension(
            columns=column_infos,
            request_text=str(goal or ""),
            goal_text=str(goal or ""),
        )
        if dimension_info is None:
            return self._unsupported_analysis_type_error()

        dimension = dimension_info.name
        quoted_dimension = self._quote_identifier(dimension)
        segment_sql = (
            f"SELECT {quoted_dimension}, COUNT(*) AS count FROM dataset "
            f"GROUP BY {quoted_dimension} ORDER BY count DESC LIMIT {_CAMPAIGN_LIMIT}"
        )
        segment_result = self._run_query(
            context=context,
            dataset_id=dataset_id,
            version_id=version_id,
            query_name=f"count_by_{dimension}",
            sql=segment_sql,
            queries_used=queries_used,
        )
        if segment_result.get("success") is False:
            return segment_result
        segment_rows = self._rows_as_dicts(segment_result)

        insights: list[dict[str, Any]] = []
        if segment_rows:
            leader = segment_rows[0]
            insights.append(
                {
                    "title": f"Top {dimension} segment by volume",
                    "evidence": (
                        f"{leader.get(dimension)} ranks first with count "
                        f"{leader.get('count')}."
                    ),
                    "recommended_action": (
                        f"Prioritize targeted campaigns for {leader.get(dimension)}."
                    ),
                    "confidence": "medium",
                },
            )

        metric_info = self._column_intelligence.select_metric(
            columns=column_infos,
            request_text=str(goal or ""),
            goal_text=str(goal or ""),
        )
        if metric_info is not None:
            metric = metric_info.name
            quoted_metric = self._quote_identifier(metric)
            metric_alias = self._safe_alias(f"total_{metric}")
            spend_sql = (
                f"SELECT {quoted_dimension}, SUM({quoted_metric}) AS {metric_alias} "
                f"FROM dataset GROUP BY {quoted_dimension} ORDER BY {metric_alias} DESC "
                f"LIMIT {_CAMPAIGN_LIMIT}"
            )
            spend_result = self._run_query(
                context=context,
                dataset_id=dataset_id,
                version_id=version_id,
                query_name=f"{metric}_by_{dimension}",
                sql=spend_sql,
                queries_used=queries_used,
            )
            if spend_result.get("success") is False:
                return spend_result
            spend_rows = self._rows_as_dicts(spend_result)
            if spend_rows:
                top_spend = spend_rows[0]
                insights.append(
                    {
                        "title": f"High-value {dimension} segment",
                        "evidence": (
                            f"{top_spend.get(dimension)} leads with "
                            f"{metric_alias}={top_spend.get(metric_alias)}."
                        ),
                        "recommended_action": (
                            f"Create upsell and retention campaigns for {top_spend.get(dimension)}."
                        ),
                        "confidence": "medium",
                    },
                )

        payload = {
            "success": True,
            "status": 200,
            "dataset_id": dataset_id,
            "version_id": version_id,
            "analysis_type": normalized_type,
            "summary": (
                "The dataset contains segments suitable for targeted campaign actions."
                if insights
                else "The dataset supports campaign segmentation analysis."
            ),
            "goal": str(goal or "").strip() or None,
            "insights": insights,
            "queries_used": [query.__dict__ for query in queries_used],
        }
        return self._sanitize_payload(payload)

    def _unsupported_analysis_type_error(self) -> dict[str, Any]:
        return {
            "success": False,
            "status": 400,
            "code": "unsupported_analysis_type",
            "error": "The requested analysis_type is not supported yet.",
        }

    def _run_query(  # noqa: PLR0913
        self,
        *,
        context: ReadAuthorizationContext,
        dataset_id: str,
        version_id: str | None,
        query_name: str,
        sql: str,
        queries_used: list[AnalysisQuery],
    ) -> dict[str, Any]:
        queries_used.append(AnalysisQuery(name=query_name, sql=sql))
        return self._query_executor.execute_query(
            context=context,
            dataset_id=dataset_id,
            version_id=version_id,
            sql=sql,
        )

    def _extract_first_scalar(self, result: dict[str, Any]) -> int:
        rows = result.get("rows", [])
        if not rows or not isinstance(rows[0], list):
            return 0
        first_row = rows[0]
        if not first_row:
            return 0
        raw = first_row[0]
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 0

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

    def _sanitize_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return sanitize_payload(payload)

    def _extract_preview_columns(self, schema_json: list[dict[str, Any]]) -> list[str]:
        columns: list[str] = []
        seen: set[str] = set()
        for entry in schema_json:
            if not isinstance(entry, dict):
                continue
            raw_name = str(entry.get("name") or "").strip()
            if not raw_name:
                continue
            lowered = raw_name.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            columns.append(raw_name)
        return columns

    def _quote_identifier(self, identifier: str) -> str:
        escaped = identifier.replace('"', '""')
        return f'"{escaped}"'

    def _safe_alias(self, raw: str) -> str:
        normalized = "".join(char if char.isalnum() else "_" for char in raw).strip("_")
        return normalized.lower() or "value"
