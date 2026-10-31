from __future__ import annotations

import hashlib
from typing import Any

from resource_registry.models import (
    DatasetAnalysisArtifact,
    DatasetChartArtifact,
    DatasetExportPlan,
    DatasetIntelligenceStatus,
    DatasetQueryRun,
)
from resource_registry.read.version_read_service import DatasetVersionReadService
from resource_registry.services.payload_sanitizer import sanitize_payload


class DatasetIntelligencePersistenceService:
    """Persists sanitized Dataset Intelligence artifacts for auditability."""

    def persist_query_run(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        version_id: str | None,
        sql: str,
        result: dict[str, Any],
        created_by: Any = None,
    ) -> DatasetQueryRun:
        sanitized_result = self._sanitize_payload(result)
        return DatasetQueryRun.objects.create(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            version_id=version_id,
            sql_hash=self._hash_sql(sql),
            sql_text=str(sql or "").strip(),
            status=DatasetIntelligenceStatus.SUCCEEDED,
            row_count=int(sanitized_result.get("row_count", 0) or 0),
            truncated=bool(sanitized_result.get("truncated", False)),
            error_code=None,
            created_by=self._resolve_created_by(created_by),
            metadata_json={
                "columns": [str(column) for column in sanitized_result.get("columns", [])],
            },
        )

    def persist_analysis_artifact(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        version_id: str | None,
        result: dict[str, Any],
        created_by: Any = None,
    ) -> DatasetAnalysisArtifact:
        payload = self._sanitize_payload(result)
        analysis_type = str(payload.get("analysis_type") or "analysis")
        title = f"Dataset analysis: {analysis_type}"
        return DatasetAnalysisArtifact.objects.create(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            version_id=version_id,
            analysis_type=analysis_type,
            title=title,
            summary=self._to_text(payload.get("summary")),
            insights_json=(
                payload.get("insights") if isinstance(payload.get("insights"), list) else []
            ),
            queries_used_json=(
                payload.get("queries_used") if isinstance(payload.get("queries_used"), list) else []
            ),
            status=DatasetIntelligenceStatus.SUCCEEDED,
            created_by=self._resolve_created_by(created_by),
            metadata_json={"status": int(payload.get("status", 200) or 200)},
        )

    def persist_chart_artifact(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        version_id: str | None,
        result: dict[str, Any],
        created_by: Any = None,
    ) -> DatasetChartArtifact:
        payload = self._sanitize_payload(result)
        chart = payload.get("chart") if isinstance(payload.get("chart"), dict) else {}
        return DatasetChartArtifact.objects.create(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            version_id=version_id,
            chart_type=str(chart.get("chart_type") or "unknown"),
            title=str(chart.get("title") or "Dataset Chart"),
            chart_spec_json=chart,
            source_query_json=(
                chart.get("source_query") if isinstance(chart.get("source_query"), dict) else {}
            ),
            status=DatasetIntelligenceStatus.SUCCEEDED,
            created_by=self._resolve_created_by(created_by),
            metadata_json={"status": int(payload.get("status", 200) or 200)},
        )

    def persist_export_plan(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        version_id: str | None,
        result: dict[str, Any],
        created_by: Any = None,
    ) -> DatasetExportPlan:
        """Persist an export plan pinned to its selected published dataset version.

        Args:
            tenant_id: Authorized namespace identifier.
            dataset_id: Dataset selected for export.
            version_id: Explicit version, or None to use the current version.
            result: Planner result to sanitize and persist.
            created_by: Authenticated actor, when available.

        Returns:
            The immutable, version-bound export plan.

        Raises:
            DatasetVersionNotFoundError: If no current published version is available.
        """
        if version_id is None:
            version_id = (
                DatasetVersionReadService()
                .get_latest_version(tenant_id=tenant_id, dataset_id=dataset_id)
                .id
            )
        payload = self._sanitize_payload(result)
        export_plan = (
            payload.get("export_plan") if isinstance(payload.get("export_plan"), dict) else {}
        )
        return DatasetExportPlan.objects.create(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            version_id=version_id,
            artifact_type=str(payload.get("artifact_type") or "unknown"),
            title=str(payload.get("title") or "Dataset Export"),
            export_plan_json=export_plan,
            status=DatasetIntelligenceStatus.SUCCEEDED,
            created_by=self._resolve_created_by(created_by),
            metadata_json={"status": int(payload.get("status", 200) or 200)},
        )

    def _hash_sql(self, sql: str) -> str:
        normalized = str(sql or "").strip().encode("utf-8")
        return hashlib.sha256(normalized).hexdigest()

    def _resolve_created_by(self, candidate: Any):
        if candidate is None:
            return None
        user_id = getattr(candidate, "pk", None)
        if not user_id:
            return None
        return candidate

    def _to_text(self, value: Any) -> str:
        if isinstance(value, str):
            return value
        if value is None:
            return ""
        return str(value)

    def _sanitize_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return sanitize_payload(payload)
