from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from resource_registry.models import DatasetIntelligenceJobOperation
from resource_registry.read_auth.context import ReadAuthorizationContext
from resource_registry.services.dataset_intelligence_job_execution_service import (
    DatasetIntelligenceJobExecutionService,
    execute_dataset_intelligence_job,
)
from resource_registry.services.tabular_transformation_execution_service import JobSpecValidationError


class DatasetIntelligenceJobExecutionServiceTests(SimpleTestCase):
    def test_execute_job_analysis_marks_completed(self) -> None:
        job = SimpleNamespace(
            id="ij-1",
            operation=DatasetIntelligenceJobOperation.ANALYSIS,
            dataset_id="dataset-1",
            version_id=None,
            export_plan_id=None,
            request_payload_json={"analysis_type": "summary", "goal": "find segments"},
            created_by=None,
        )
        job_service = MagicMock()
        job_service.get_job.return_value = job
        job_service.mark_running.return_value = job
        job_service.mark_completed.return_value = job

        read_facade = MagicMock()
        read_facade.get_schema.return_value = SimpleNamespace(schema_json=[{"name": "country"}])
        analysis_service = MagicMock()
        analysis_service.analyze.return_value = {"success": True, "status": 200, "result": {}}

        service = DatasetIntelligenceJobExecutionService(
            job_service=job_service,
            read_facade=read_facade,
            analysis_service=analysis_service,
        )

        result = service.execute_job(
            context=ReadAuthorizationContext(tenant_id="tenant-1"),
            intelligence_job_id="ij-1",
        )

        self.assertEqual(result, job)
        job_service.mark_completed.assert_called_once()
        job_service.mark_failed.assert_not_called()

    def test_execute_job_chart_marks_failed_on_error_payload(self) -> None:
        job = SimpleNamespace(
            id="ij-1",
            operation=DatasetIntelligenceJobOperation.CHART,
            dataset_id="dataset-1",
            version_id=None,
            export_plan_id=None,
            request_payload_json={"chart_type": "bar", "user_request": "chart"},
            created_by=None,
        )
        job_service = MagicMock()
        job_service.get_job.return_value = job
        job_service.mark_running.return_value = job
        job_service.mark_failed.return_value = job

        read_facade = MagicMock()
        read_facade.get_schema.return_value = SimpleNamespace(schema_json=[{"name": "country"}])
        chart_service = MagicMock()
        chart_service.create_chart.return_value = {
            "success": False,
            "status": 400,
            "code": "unsupported_chart_request",
            "error": "unsupported",
        }

        service = DatasetIntelligenceJobExecutionService(
            job_service=job_service,
            read_facade=read_facade,
            chart_service=chart_service,
        )

        result = service.execute_job(
            context=ReadAuthorizationContext(tenant_id="tenant-1"),
            intelligence_job_id="ij-1",
        )

        self.assertEqual(result, job)
        job_service.mark_completed.assert_not_called()
        job_service.mark_failed.assert_called_once()
        self.assertFalse(job_service.mark_failed.call_args.kwargs.get("retryable", False))

    def test_execute_job_does_not_convert_error_payload_to_timeout(self) -> None:
        job = SimpleNamespace(
            id="ij-1",
            operation=DatasetIntelligenceJobOperation.CHART,
            dataset_id="dataset-1",
            version_id=None,
            export_plan_id=None,
            request_payload_json={"chart_type": "bar", "user_request": "chart"},
            created_by=None,
        )
        job_service = MagicMock()
        job_service.get_job.return_value = job
        job_service.mark_running.return_value = job
        job_service.mark_failed.return_value = job
        read_facade = MagicMock()
        read_facade.get_schema.return_value = SimpleNamespace(schema_json=[{"name": "country"}])
        chart_service = MagicMock()
        chart_service.create_chart.return_value = {
            "success": False,
            "status": 400,
            "code": "unsupported_chart_request",
            "error": "unsupported",
        }
        service = DatasetIntelligenceJobExecutionService(
            job_service=job_service,
            read_facade=read_facade,
            chart_service=chart_service,
            execution_timeout_seconds=1,
        )

        with patch(
            "resource_registry.services.dataset_intelligence_job_execution_service.time.perf_counter",
            side_effect=[0.0, 2.0],
        ):
            result = service.execute_job(
                context=ReadAuthorizationContext(tenant_id="tenant-1"),
                intelligence_job_id="ij-1",
            )

        self.assertEqual(result, job)
        kwargs = job_service.mark_failed.call_args.kwargs
        self.assertEqual(kwargs["error_code"], "unsupported_chart_request")
        self.assertFalse(kwargs.get("retryable", False))

    def test_execute_job_soft_timeout_after_success_delegates_retryable_failure(self) -> None:
        job = SimpleNamespace(
            id="ij-1",
            operation=DatasetIntelligenceJobOperation.ANALYSIS,
            dataset_id="dataset-1",
            version_id=None,
            export_plan_id=None,
            request_payload_json={"analysis_type": "summary", "goal": "find segments"},
            created_by=None,
        )
        job_service = MagicMock()
        job_service.get_job.return_value = job
        job_service.mark_running.return_value = job
        job_service.mark_failed.return_value = job
        read_facade = MagicMock()
        read_facade.get_schema.return_value = SimpleNamespace(schema_json=[{"name": "country"}])
        analysis_service = MagicMock()
        analysis_service.analyze.return_value = {"success": True, "status": 200, "result": {}}
        service = DatasetIntelligenceJobExecutionService(
            job_service=job_service,
            read_facade=read_facade,
            analysis_service=analysis_service,
            execution_timeout_seconds=1,
        )

        with patch(
            "resource_registry.services.dataset_intelligence_job_execution_service.time.perf_counter",
            side_effect=[0.0, 2.0],
        ):
            result = service.execute_job(
                context=ReadAuthorizationContext(tenant_id="tenant-1"),
                intelligence_job_id="ij-1",
            )

        self.assertEqual(result, job)
        job_service.mark_completed.assert_not_called()
        kwargs = job_service.mark_failed.call_args.kwargs
        self.assertEqual(kwargs["error_code"], "execution_timeout")
        self.assertTrue(kwargs["retryable"])

    def test_execute_job_disabled_soft_timeout_allows_late_success(self) -> None:
        job = SimpleNamespace(
            id="ij-1",
            operation=DatasetIntelligenceJobOperation.ANALYSIS,
            dataset_id="dataset-1",
            version_id=None,
            export_plan_id=None,
            request_payload_json={"analysis_type": "summary", "goal": "find segments"},
            created_by=None,
        )
        job_service = MagicMock()
        job_service.get_job.return_value = job
        job_service.mark_running.return_value = job
        job_service.mark_completed.return_value = job
        read_facade = MagicMock()
        read_facade.get_schema.return_value = SimpleNamespace(schema_json=[{"name": "country"}])
        analysis_service = MagicMock()
        analysis_service.analyze.return_value = {"success": True, "status": 200, "result": {}}
        service = DatasetIntelligenceJobExecutionService(
            job_service=job_service,
            read_facade=read_facade,
            analysis_service=analysis_service,
            execution_timeout_seconds=0,
        )

        with patch(
            "resource_registry.services.dataset_intelligence_job_execution_service.time.perf_counter",
            side_effect=[0.0, 2.0],
        ):
            result = service.execute_job(
                context=ReadAuthorizationContext(tenant_id="tenant-1"),
                intelligence_job_id="ij-1",
            )

        self.assertEqual(result, job)
        job_service.mark_completed.assert_called_once()
        job_service.mark_failed.assert_not_called()

    def test_execute_job_marks_cancelled_when_cancellation_is_requested_after_execution(
        self,
    ) -> None:
        job = SimpleNamespace(
            id="ij-1",
            operation=DatasetIntelligenceJobOperation.ANALYSIS,
            dataset_id="dataset-1",
            version_id=None,
            export_plan_id=None,
            request_payload_json={"analysis_type": "summary", "goal": "find segments"},
            created_by=None,
        )
        cancelled_job = SimpleNamespace(id="ij-1", status="cancelled")
        job_service = MagicMock()
        job_service.get_job.return_value = job
        job_service.mark_running.return_value = job
        job_service.is_cancellation_requested.return_value = True
        job_service.mark_cancelled.return_value = cancelled_job
        read_facade = MagicMock()
        read_facade.get_schema.return_value = SimpleNamespace(schema_json=[{"name": "country"}])
        analysis_service = MagicMock()
        analysis_service.analyze.return_value = {"success": True, "status": 200, "result": {}}
        service = DatasetIntelligenceJobExecutionService(
            job_service=job_service,
            read_facade=read_facade,
            analysis_service=analysis_service,
            execution_timeout_seconds=1,
        )

        with patch(
            "resource_registry.services.dataset_intelligence_job_execution_service.time.perf_counter",
            side_effect=[0.0, 2.0],
        ):
            result = service.execute_job(
                context=ReadAuthorizationContext(tenant_id="tenant-1"),
                intelligence_job_id="ij-1",
            )

        self.assertEqual(result, cancelled_job)
        job_service.mark_cancelled.assert_called_once_with(job=job)
        job_service.mark_completed.assert_not_called()
        job_service.mark_failed.assert_not_called()

    def test_execute_job_export_plan_routes_to_export_plan_service(self) -> None:
        job = SimpleNamespace(
            id="ij-1",
            operation=DatasetIntelligenceJobOperation.EXPORT_PLAN,
            dataset_id="dataset-1",
            version_id="version-1",
            export_plan_id=None,
            request_payload_json={"format": "xlsx", "user_request": "export"},
            created_by=None,
        )
        job_service = MagicMock()
        job_service.get_job.return_value = job
        job_service.mark_running.return_value = job
        job_service.mark_completed.return_value = job

        read_facade = MagicMock()
        read_facade.get_schema.return_value = SimpleNamespace(schema_json=[{"name": "country"}])
        export_plan_service = MagicMock()
        export_plan_service.create_plan.return_value = {
            "success": True,
            "status": 200,
            "artifact_type": "xlsx",
        }

        service = DatasetIntelligenceJobExecutionService(
            job_service=job_service,
            read_facade=read_facade,
            export_plan_service=export_plan_service,
        )

        _ = service.execute_job(
            context=ReadAuthorizationContext(tenant_id="tenant-1"),
            intelligence_job_id="ij-1",
        )

        export_plan_service.create_plan.assert_called_once()
        job_service.mark_completed.assert_called_once()

    def test_execute_job_artifact_generation_routes_to_generation_service(self) -> None:
        job = SimpleNamespace(
            id="ij-1",
            operation=DatasetIntelligenceJobOperation.ARTIFACT_GENERATION,
            dataset_id=None,
            version_id=None,
            export_plan_id="plan-1",
            request_payload_json={"export_plan_id": "plan-1"},
            created_by=None,
        )
        job_service = MagicMock()
        job_service.get_job.return_value = job
        job_service.mark_running.return_value = job
        job_service.mark_completed.return_value = job

        generation_service = MagicMock()
        generation_service.generate_from_export_plan.return_value = {
            "success": True,
            "status": 200,
            "artifact": {"artifact_id": "a-1"},
        }

        service = DatasetIntelligenceJobExecutionService(
            job_service=job_service,
            artifact_generation_service=generation_service,
        )

        _ = service.execute_job(
            context=ReadAuthorizationContext(tenant_id="tenant-1"),
            intelligence_job_id="ij-1",
        )

        generation_service.generate_from_export_plan.assert_called_once()
        job_service.mark_completed.assert_called_once()

    def test_execute_job_transformation_routes_to_transformation_service(self) -> None:
        job = SimpleNamespace(
            id="ij-1",
            operation=DatasetIntelligenceJobOperation.TRANSFORMATION,
            dataset_id="dataset-1",
            version_id=None,
            export_plan_id=None,
            request_payload_json={"job_spec": {"version": "1.0"}},
            job_spec_json={"job_type": "tabular_transformation", "version": "1.0"},
            created_by=None,
        )
        job_service = MagicMock()
        job_service.get_job.return_value = job
        job_service.mark_running.return_value = job
        job_service.mark_completed.return_value = job

        transformation_service = MagicMock()
        transformation_service.execute.return_value = {
            "success": True,
            "status": 200,
            "logical_result": {"logical_artifacts": [{"name": "records"}]},
            "decision_trace": [{"stage": "normalize"}],
        }

        service = DatasetIntelligenceJobExecutionService(
            job_service=job_service,
            transformation_service=transformation_service,
        )

        _ = service.execute_job(
            context=ReadAuthorizationContext(tenant_id="tenant-1"),
            intelligence_job_id="ij-1",
        )

        transformation_service.execute.assert_called_once()
        job_service.mark_completed.assert_called_once()

    def test_execute_job_marks_failed_when_exception_raised(self) -> None:
        job = SimpleNamespace(
            id="ij-1",
            operation=DatasetIntelligenceJobOperation.ANALYSIS,
            dataset_id="dataset-1",
            version_id=None,
            export_plan_id=None,
            request_payload_json={"analysis_type": "summary"},
            created_by=None,
        )
        job_service = MagicMock()
        job_service.get_job.return_value = job
        job_service.mark_running.return_value = job
        job_service.mark_failed.return_value = job

        read_facade = MagicMock()
        read_facade.get_schema.side_effect = RuntimeError("boom")

        service = DatasetIntelligenceJobExecutionService(
            job_service=job_service,
            read_facade=read_facade,
        )

        _ = service.execute_job(
            context=ReadAuthorizationContext(tenant_id="tenant-1"),
            intelligence_job_id="ij-1",
        )

        job_service.mark_completed.assert_not_called()
        job_service.mark_failed.assert_called_once()
        self.assertTrue(job_service.mark_failed.call_args.kwargs["retryable"])

    def test_execute_job_marks_failed_when_job_spec_is_invalid(self) -> None:
        job = SimpleNamespace(
            id="ij-1",
            operation=DatasetIntelligenceJobOperation.TRANSFORMATION,
            dataset_id=None,
            version_id=None,
            export_plan_id=None,
            request_payload_json={},
            job_spec_json={},
            created_by=None,
        )
        job_service = MagicMock()
        job_service.get_job.return_value = job
        job_service.mark_running.return_value = job
        job_service.mark_failed.return_value = job

        transformation_service = MagicMock()
        transformation_service.execute.side_effect = JobSpecValidationError("invalid spec")

        service = DatasetIntelligenceJobExecutionService(
            job_service=job_service,
            transformation_service=transformation_service,
        )

        _ = service.execute_job(
            context=ReadAuthorizationContext(tenant_id="tenant-1"),
            intelligence_job_id="ij-1",
        )

        job_service.mark_failed.assert_called_once()
        self.assertFalse(job_service.mark_failed.call_args.kwargs.get("retryable", False))


class ExecuteDatasetIntelligenceJobEntryPointTests(SimpleTestCase):
    def test_execute_dataset_intelligence_job_returns_stable_response(self) -> None:
        fake_job = SimpleNamespace(
            id="ij-1",
            status="completed",
            operation=DatasetIntelligenceJobOperation.ANALYSIS,
        )
        persisted_job = SimpleNamespace(id="ij-1")
        fake_job_service = MagicMock()
        fake_job_service.get_job.return_value = persisted_job
        authority = MagicMock()
        authority.to_read_authorization_context.return_value = ReadAuthorizationContext(
            tenant_id="tenant-1"
        )
        fake_service = MagicMock()
        fake_service.execute_job.return_value = fake_job

        with (
            patch(
                "resource_registry.services.dataset_intelligence_job_execution_service."
                "DatasetIntelligenceJobService",
                return_value=fake_job_service,
            ),
            patch(
                "resource_registry.services.dataset_intelligence_job_execution_service."
                "WorkerExecutionAuthorityService"
            ) as authority_service,
            patch(
                "resource_registry.services.dataset_intelligence_job_execution_service."
                "DatasetIntelligenceJobExecutionService",
                return_value=fake_service,
            ),
        ):
            authority_service.return_value.build_intelligence_context.return_value = authority
            payload = execute_dataset_intelligence_job(
                tenant_id="tenant-1",
                intelligence_job_id="ij-1",
                actor="alice",
                roles=("reader",),
            )

        self.assertEqual(payload["intelligence_job_id"], "ij-1")
        self.assertEqual(payload["job_status"], "completed")
        self.assertEqual(payload["operation"], DatasetIntelligenceJobOperation.ANALYSIS)
