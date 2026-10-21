from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from resource_registry.services.payload_sanitizer import sanitize_payload
from resource_registry.services.schema_column_intelligence import (
    SchemaColumnIntelligence,
    SchemaColumnIntelligenceItem,
)

_MAX_ROWS = 100
_DEFAULT_LIMIT = 20
_MIN_PREVIEW_COLUMNS = 2


@dataclass(frozen=True)
class ExportDataRequirement:
    name: str
    sql: str
    columns: tuple[str, ...]
    max_rows: int


class DatasetExportPlanService:
    """Build deterministic export-plan payloads without SQL execution or persistence."""

    SUPPORTED_FORMATS = frozenset({"csv", "xlsx", "pdf", "pptx"})

    def __init__(self, *, column_intelligence: SchemaColumnIntelligence | None = None) -> None:
        self._column_intelligence = column_intelligence or SchemaColumnIntelligence()

    def create_plan(
        self,
        *,
        dataset_id: str,
        version_id: str | None,
        artifact_format: str,
        user_request: str,
        schema_json: list[dict[str, Any]],
    ) -> dict[str, Any]:
        normalized_format = str(artifact_format).strip().lower()
        if normalized_format not in self.SUPPORTED_FORMATS:
            return {
                "success": False,
                "status": 400,
                "code": "unsupported_export_format",
                "error": "The requested export format is not supported.",
            }

        request = " ".join(str(user_request or "").strip().lower().split())
        column_infos = self._column_intelligence.classify_schema(schema_json)

        data_requirements = self._build_data_requirements(
            user_request=request,
            column_infos=column_infos,
        )
        if not data_requirements:
            return {
                "success": False,
                "status": 400,
                "code": "unsupported_export_request",
                "error": "The requested export cannot be safely planned yet.",
            }

        export_plan = self._build_export_plan(
            artifact_format=normalized_format,
            data_requirements=data_requirements,
        )

        payload = {
            "success": True,
            "status": 200,
            "dataset_id": dataset_id,
            "version_id": version_id,
            "artifact_type": normalized_format,
            "title": "Dataset Export",
            "export_plan": export_plan,
        }
        return self._sanitize_payload(payload)

    def _build_data_requirements(
        self,
        *,
        user_request: str,
        column_infos: tuple[SchemaColumnIntelligenceItem, ...],
    ) -> list[ExportDataRequirement]:
        requirements: list[ExportDataRequirement] = []
        dimension = self._column_intelligence.select_dimension(
            columns=column_infos,
            request_text=user_request,
        )
        metric = self._column_intelligence.select_metric(
            columns=column_infos,
            request_text=user_request,
        )

        if dimension is not None and metric is not None:
            metric_alias = f"total_{metric.name}"
            requirements.append(
                ExportDataRequirement(
                    name="metric_by_dimension",
                    sql=(
                        f"SELECT {dimension.name}, SUM({metric.name}) AS {metric_alias} "
                        f"FROM dataset GROUP BY {dimension.name} "
                        f"ORDER BY {metric_alias} DESC LIMIT {_DEFAULT_LIMIT}"
                    ),
                    columns=(dimension.name, metric_alias),
                    max_rows=_DEFAULT_LIMIT,
                ),
            )

        time_column = self._column_intelligence.select_datetime(
            columns=column_infos,
            request_text=user_request,
        )
        if time_column is not None and metric is not None:
            metric_alias = f"total_{metric.name}"
            requirements.append(
                ExportDataRequirement(
                    name="metric_trend",
                    sql=(
                        f"SELECT DATE_TRUNC('month', {time_column.name}) AS period, "
                        f"SUM({metric.name}) AS {metric_alias} FROM dataset "
                        f"GROUP BY period ORDER BY period LIMIT {_DEFAULT_LIMIT}"
                    ),
                    columns=("period", metric_alias),
                    max_rows=_DEFAULT_LIMIT,
                ),
            )

        safe_columns = self._column_intelligence.select_table_columns(
            columns=column_infos,
            request_text=user_request,
            max_columns=5,
        )
        if len(safe_columns) >= _MIN_PREVIEW_COLUMNS:
            requirements.append(
                ExportDataRequirement(
                    name="dataset_preview",
                    sql=(f"SELECT {', '.join(safe_columns)} FROM dataset LIMIT {_DEFAULT_LIMIT}"),
                    columns=safe_columns,
                    max_rows=_DEFAULT_LIMIT,
                ),
            )

        deduped: list[ExportDataRequirement] = []
        seen = set()
        for item in requirements:
            if item.name in seen:
                continue
            seen.add(item.name)
            deduped.append(item)
        return deduped

    def _build_export_plan(
        self,
        *,
        artifact_format: str,
        data_requirements: list[ExportDataRequirement],
    ) -> dict[str, Any]:
        serialized_requirements = [
            {
                "name": requirement.name,
                "sql": requirement.sql,
                "columns": list(requirement.columns),
                "max_rows": requirement.max_rows,
            }
            for requirement in data_requirements
        ]

        base_plan = {
            "filename": f"dataset_export.{artifact_format}",
            "data_requirements": serialized_requirements,
            "security": {
                "raw_rows_allowed": False,
                "max_rows_per_query": _MAX_ROWS,
                "secrets_allowed": False,
            },
        }

        primary_source = data_requirements[0].name
        primary_columns = data_requirements[0].columns
        primary_x = primary_columns[0]
        primary_y = primary_columns[1] if len(primary_columns) > 1 else primary_columns[0]

        if artifact_format == "csv":
            base_plan["data_requirements"] = serialized_requirements[:1]
            return base_plan

        if artifact_format == "xlsx":
            base_plan["sheets"] = [
                {
                    "name": "Summary",
                    "type": "table",
                    "source": primary_source,
                },
            ]
            base_plan["visuals"] = [
                {
                    "id": "summary_chart",
                    "type": "bar",
                    "title": "Summary",
                    "source": primary_source,
                    "x": primary_x,
                    "y": primary_y,
                },
            ]
            return base_plan

        if artifact_format == "pdf":
            base_plan["sections"] = [
                {
                    "id": "section_summary",
                    "title": "Summary",
                    "source": primary_source,
                },
            ]
            base_plan["visuals"] = [
                {
                    "id": "summary_chart",
                    "type": "bar",
                    "title": "Summary",
                    "source": primary_source,
                    "x": primary_x,
                    "y": primary_y,
                },
            ]
            base_plan["narrative_placeholders"] = [
                "Key findings summary",
                "Recommended actions",
            ]
            return base_plan

        base_plan["slides"] = [
            {
                "id": "slide_summary",
                "title": "Summary",
                "source": primary_source,
            },
        ]
        base_plan["visuals"] = [
            {
                "id": "summary_chart",
                "type": "bar",
                "title": "Summary",
                "source": primary_source,
                "x": primary_x,
                "y": primary_y,
            },
        ]
        base_plan["narrative_placeholders"] = [
            "Executive summary",
            "Next steps",
        ]
        return base_plan

    def _sanitize_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Sanitize export payloads while retaining explicit false security guarantees.

        Args:
            payload: Unsanitized deterministic export-plan result.

        Returns:
            A sanitized payload that preserves only the safe ``False`` value for
            ``security.secrets_allowed``.
        """
        sanitized = sanitize_payload(payload)
        if not isinstance(sanitized, dict):
            return sanitized
        export_plan = payload.get("export_plan")
        sanitized_export_plan = sanitized.get("export_plan")
        if not isinstance(export_plan, dict) or not isinstance(sanitized_export_plan, dict):
            return sanitized
        security = export_plan.get("security")
        sanitized_security = sanitized_export_plan.get("security")
        if (
            isinstance(security, dict)
            and isinstance(sanitized_security, dict)
            and security.get("secrets_allowed") is False
        ):
            sanitized_security["secrets_allowed"] = False
        return sanitized
