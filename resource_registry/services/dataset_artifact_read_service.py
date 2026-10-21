from __future__ import annotations

from typing import Any

from resource_registry.models import (
    DatasetAnalysisArtifact,
    DatasetChartArtifact,
    DatasetExportPlan,
    DatasetQueryRun,
)
from resource_registry.read_auth.context import ReadAuthorizationContext
from resource_registry.read_auth.exceptions import DataRegistryAuthorizationError
from resource_registry.read_auth.permissions import ReadAuthorizationService
from resource_registry.services.payload_sanitizer import sanitize_payload


class DatasetArtifactReadService:
    """Reads persisted Dataset Intelligence artifacts with tenant isolation."""

    def __init__(self, *, authorization_service: ReadAuthorizationService | None = None) -> None:
        self._authorization_service = authorization_service or ReadAuthorizationService()

    def get_query_run(
        self,
        *,
        context: ReadAuthorizationContext,
        tenant_id: str,
        query_run_id: str,
    ) -> dict[str, Any]:
        artifact = DatasetQueryRun.objects.filter(tenant_id=tenant_id, id=query_run_id).first()
        if artifact is None:
            raise DataRegistryAuthorizationError("Artifact not found.")
        self._authorization_service.assert_can_read_dataset(context, str(artifact.dataset_id))
        return self._sanitize_payload(
            {
                "id": str(artifact.id),
                "tenant_id": str(artifact.tenant_id),
                "dataset_id": str(artifact.dataset_id),
                "version_id": str(artifact.version_id) if artifact.version_id else None,
                "sql_hash": artifact.sql_hash,
                "sql_text": artifact.sql_text,
                "status": artifact.status,
                "row_count": int(artifact.row_count),
                "truncated": bool(artifact.truncated),
                "execution_ms": artifact.execution_ms,
                "error_code": artifact.error_code,
                "created_at": artifact.created_at.isoformat(),
                "metadata": artifact.metadata_json,
            },
        )

    def get_analysis_artifact(
        self,
        *,
        context: ReadAuthorizationContext,
        tenant_id: str,
        analysis_artifact_id: str,
    ) -> dict[str, Any]:
        artifact = DatasetAnalysisArtifact.objects.filter(
            tenant_id=tenant_id,
            id=analysis_artifact_id,
        ).first()
        if artifact is None:
            raise DataRegistryAuthorizationError("Artifact not found.")
        self._authorization_service.assert_can_read_dataset(context, str(artifact.dataset_id))
        return self._sanitize_payload(
            {
                "id": str(artifact.id),
                "tenant_id": str(artifact.tenant_id),
                "dataset_id": str(artifact.dataset_id),
                "version_id": str(artifact.version_id) if artifact.version_id else None,
                "analysis_type": artifact.analysis_type,
                "title": artifact.title,
                "summary": artifact.summary,
                "insights": artifact.insights_json,
                "queries_used": artifact.queries_used_json,
                "status": artifact.status,
                "created_at": artifact.created_at.isoformat(),
                "metadata": artifact.metadata_json,
            },
        )

    def get_chart_artifact(
        self,
        *,
        context: ReadAuthorizationContext,
        tenant_id: str,
        chart_artifact_id: str,
    ) -> dict[str, Any]:
        artifact = DatasetChartArtifact.objects.filter(
            tenant_id=tenant_id,
            id=chart_artifact_id,
        ).first()
        if artifact is None:
            raise DataRegistryAuthorizationError("Artifact not found.")
        self._authorization_service.assert_can_read_dataset(context, str(artifact.dataset_id))
        return self._sanitize_payload(
            {
                "id": str(artifact.id),
                "tenant_id": str(artifact.tenant_id),
                "dataset_id": str(artifact.dataset_id),
                "version_id": str(artifact.version_id) if artifact.version_id else None,
                "chart_type": artifact.chart_type,
                "title": artifact.title,
                "chart_spec": artifact.chart_spec_json,
                "source_query": artifact.source_query_json,
                "status": artifact.status,
                "created_at": artifact.created_at.isoformat(),
                "metadata": artifact.metadata_json,
            },
        )

    def get_export_plan(
        self,
        *,
        context: ReadAuthorizationContext,
        tenant_id: str,
        export_plan_id: str,
    ) -> dict[str, Any]:
        artifact = DatasetExportPlan.objects.filter(tenant_id=tenant_id, id=export_plan_id).first()
        if artifact is None:
            raise DataRegistryAuthorizationError("Artifact not found.")
        self._authorization_service.assert_can_read_dataset(context, str(artifact.dataset_id))
        return self._sanitize_payload(
            {
                "id": str(artifact.id),
                "tenant_id": str(artifact.tenant_id),
                "dataset_id": str(artifact.dataset_id),
                "version_id": str(artifact.version_id) if artifact.version_id else None,
                "artifact_type": artifact.artifact_type,
                "title": artifact.title,
                "export_plan": artifact.export_plan_json,
                "status": artifact.status,
                "created_at": artifact.created_at.isoformat(),
                "metadata": artifact.metadata_json,
            },
        )

    def _sanitize_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return sanitize_payload(payload)
