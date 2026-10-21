from __future__ import annotations

import time
from typing import Any

from django.conf import settings

from resource_registry.authorization.worker_execution_authority import WorkerExecutionAuthorityService
from resource_registry.models import DatasetIntelligenceJob, DatasetIntelligenceJobOperation
from resource_registry.read_auth.context import ReadAuthorizationContext
from resource_registry.read_auth.policy import AuthorizedDataRegistryReadFacade
from resource_registry.services.artifact_generation_service import ArtifactGenerationService
from resource_registry.services.async_job_execution_timeout import (
    build_execution_timeout_message,
    has_execution_timeout_elapsed,
    normalize_execution_timeout_seconds,
)
from resource_registry.services.dataset_analysis_service import DatasetAnalysisService
from resource_registry.services.dataset_chart_service import DatasetChartService
from resource_registry.services.dataset_export_plan_service import DatasetExportPlanService
from resource_registry.services.dataset_intelligence_job_service import DatasetIntelligenceJobService
from resource_registry.services.tabular_transformation_execution_service import (
    JobSpecValidationError,
    TabularTransformationExecutionService,
)


class DatasetIntelligenceJobExecutionService:
    """Executes queued dataset intelligence jobs through existing services."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        job_service: DatasetIntelligenceJobService | None = None,
        read_facade: AuthorizedDataRegistryReadFacade | None = None,
        analysis_service: DatasetAnalysisService | None = None,
        chart_service: DatasetChartService | None = None,
        export_plan_service: DatasetExportPlanService | None = None,
        artifact_generation_service: ArtifactGenerationService | None = None,
        transformation_service: TabularTransformationExecutionService | None = None,
        execution_timeout_seconds: float | int | None = None,
    ) -> None:
        self._job_service = job_service or DatasetIntelligenceJobService()
        configured_timeout = (
            execution_timeout_seconds
            if execution_timeout_seconds is not None
            else getattr(settings, "RESOURCE_REGISTRY_INTELLIGENCE_JOB_EXECUTION_TIMEOUT_SECONDS", 0)
        )
        self._execution_timeout_seconds = normalize_execution_timeout_seconds(configured_timeout)
        self._read_facade = read_facade or AuthorizedDataRegistryReadFacade()
        self._analysis_service = analysis_service or DatasetAnalysisService()
        self._chart_service = chart_service or DatasetChartService()
        self._export_plan_service = export_plan_service or DatasetExportPlanService()
        self._artifact_generation_service = (
            artifact_generation_service or ArtifactGenerationService()
        )
        self._transformation_service = (
            transformation_service or TabularTransformationExecutionService()
        )

    def execute_job(  # noqa: PLR0911
        self,
        *,
        context: ReadAuthorizationContext,
        intelligence_job_id: str,
    ) -> DatasetIntelligenceJob:
        job = self._job_service.get_job(
            tenant_id=context.tenant_id,
            intelligence_job_id=intelligence_job_id,
        )
        self._job_service.mark_running(job=job)
        started_at = time.perf_counter()

        try:
            result = self._execute_operation(context=context, job=job)
            elapsed_seconds = time.perf_counter() - started_at
            execution_ms = int(elapsed_seconds * 1000)
            if (
                self._job_service.is_cancellation_requested(
                    tenant_id=context.tenant_id,
                    intelligence_job_id=intelligence_job_id,
                )
                is True
            ):
                current_job = self._job_service.get_job(
                    tenant_id=context.tenant_id,
                    intelligence_job_id=intelligence_job_id,
                )
                return self._job_service.mark_cancelled(job=current_job)
            if result.get("success") is not False and has_execution_timeout_elapsed(
                elapsed_seconds=elapsed_seconds,
                timeout_seconds=self._execution_timeout_seconds,
            ):
                return self._mark_execution_timeout(
                    job=job,
                    execution_ms=execution_ms,
                )
            if result.get("success") is False:
                return self._job_service.mark_failed(
                    job=job,
                    error_code=str(result.get("code") or "intelligence_execution_error"),
                    error_message=str(result.get("error") or "Unable to execute intelligence job."),
                    execution_ms=execution_ms,
                )
            return self._job_service.mark_completed(
                job=job,
                result_payload=result,
                decision_trace=result.get("decision_trace"),
                logical_result=result.get("logical_result"),
                execution_ms=execution_ms,
            )
        except JobSpecValidationError as error:
            execution_ms = int((time.perf_counter() - started_at) * 1000)
            if (
                self._job_service.is_cancellation_requested(
                    tenant_id=context.tenant_id,
                    intelligence_job_id=intelligence_job_id,
                )
                is True
            ):
                current_job = self._job_service.get_job(
                    tenant_id=context.tenant_id,
                    intelligence_job_id=intelligence_job_id,
                )
                return self._job_service.mark_cancelled(job=current_job)
            return self._job_service.mark_failed(
                job=job,
                error_code="invalid_job_spec",
                error_message=str(error),
                execution_ms=execution_ms,
            )
        except Exception:
            elapsed_seconds = time.perf_counter() - started_at
            execution_ms = int(elapsed_seconds * 1000)
            if (
                self._job_service.is_cancellation_requested(
                    tenant_id=context.tenant_id,
                    intelligence_job_id=intelligence_job_id,
                )
                is True
            ):
                current_job = self._job_service.get_job(
                    tenant_id=context.tenant_id,
                    intelligence_job_id=intelligence_job_id,
                )
                return self._job_service.mark_cancelled(job=current_job)
            if has_execution_timeout_elapsed(
                elapsed_seconds=elapsed_seconds,
                timeout_seconds=self._execution_timeout_seconds,
            ):
                return self._mark_execution_timeout(
                    job=job,
                    execution_ms=execution_ms,
                )
            return self._job_service.mark_failed(
                job=job,
                error_code="intelligence_execution_error",
                error_message="Unable to execute intelligence job.",
                execution_ms=execution_ms,
                retryable=True,
            )

    def _mark_execution_timeout(
        self,
        *,
        job: DatasetIntelligenceJob,
        execution_ms: int,
    ) -> DatasetIntelligenceJob:
        return self._job_service.mark_failed(
            job=job,
            error_code="execution_timeout",
            error_message=build_execution_timeout_message(
                job_family="Dataset intelligence job",
                timeout_seconds=self._execution_timeout_seconds,
            ),
            execution_ms=execution_ms,
            retryable=True,
        )

    def _execute_operation(
        self,
        *,
        context: ReadAuthorizationContext,
        job: DatasetIntelligenceJob,
    ) -> dict[str, Any]:
        operation = str(job.operation or "").strip().lower()
        request_payload = (
            job.request_payload_json
            if isinstance(job.request_payload_json, dict)
            else {}
        )

        if operation == DatasetIntelligenceJobOperation.ANALYSIS:
            dataset_id, version_id, schema_json = self._resolve_dataset_inputs(
                context=context,
                job=job,
            )
            return self._analysis_service.analyze(
                context=context,
                dataset_id=dataset_id,
                version_id=version_id,
                analysis_type=str(request_payload.get("analysis_type") or ""),
                schema_json=schema_json,
                goal=str(request_payload.get("goal") or ""),
            )

        if operation == DatasetIntelligenceJobOperation.CHART:
            dataset_id, version_id, schema_json = self._resolve_dataset_inputs(
                context=context,
                job=job,
            )
            return self._chart_service.create_chart(
                context=context,
                dataset_id=dataset_id,
                version_id=version_id,
                chart_type=str(request_payload.get("chart_type") or ""),
                user_request=str(request_payload.get("user_request") or ""),
                schema_json=schema_json,
            )

        if operation == DatasetIntelligenceJobOperation.EXPORT_PLAN:
            dataset_id, version_id, schema_json = self._resolve_dataset_inputs(
                context=context,
                job=job,
            )
            return self._export_plan_service.create_plan(
                dataset_id=dataset_id,
                version_id=version_id,
                artifact_format=str(request_payload.get("format") or ""),
                user_request=str(request_payload.get("user_request") or ""),
                schema_json=schema_json,
            )

        if operation == DatasetIntelligenceJobOperation.ARTIFACT_GENERATION:
            export_plan_id = str(
                job.export_plan_id or request_payload.get("export_plan_id") or "",
            ).strip()
            return self._artifact_generation_service.generate_from_export_plan(
                context=context,
                export_plan_id=export_plan_id,
                created_by=getattr(job, "created_by", None),
            )
        if operation == DatasetIntelligenceJobOperation.TRANSFORMATION:
            job_spec = job.job_spec_json if isinstance(job.job_spec_json, dict) else {}
            return self._transformation_service.execute(
                context=context,
                job_spec=job_spec,
            )

        return {
            "success": False,
            "status": 400,
            "code": "unsupported_intelligence_job_operation",
            "error": "Unsupported intelligence job operation.",
        }

    def _resolve_dataset_inputs(
        self,
        *,
        context: ReadAuthorizationContext,
        job,
    ) -> tuple[str, str | None, list[dict[str, Any]]]:
        dataset_id = str(job.dataset_id or "").strip()
        version_id = str(job.version_id).strip() if job.version_id else None
        schema_summary = self._read_facade.get_schema(context, dataset_id, version_id=version_id)
        schema_json = schema_summary.schema_json if hasattr(schema_summary, "schema_json") else []
        if not isinstance(schema_json, list):
            schema_json = []
        return dataset_id, version_id, schema_json


def execute_dataset_intelligence_job(  # noqa: PLR0913
    *,
    tenant_id: str,
    intelligence_job_id: str,
    actor: str | None = None,
    roles: tuple[str, ...] = (),
    scopes: tuple[str, ...] = (),
    allowed_dataset_ids: frozenset[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute one intelligence job using only its persisted delegated authority."""
    _ = (actor, roles, scopes, allowed_dataset_ids, metadata)
    job_service = DatasetIntelligenceJobService()
    persisted_job = job_service.get_job(
        tenant_id=tenant_id,
        intelligence_job_id=intelligence_job_id,
    )
    execution_authority = WorkerExecutionAuthorityService().build_intelligence_context(
        job=persisted_job
    )
    job = DatasetIntelligenceJobExecutionService(job_service=job_service).execute_job(
        context=execution_authority.to_read_authorization_context(),
        intelligence_job_id=intelligence_job_id,
    )
    return {
        "intelligence_job_id": str(job.id),
        "job_status": str(job.status),
        "operation": str(job.operation),
    }
