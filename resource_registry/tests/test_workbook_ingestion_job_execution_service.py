from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from resource_registry.application.dataset_registration import (
    RegisterDatasetBatchResult,
    RegisterDatasetResult,
    SkippedWorksheet,
)
from resource_registry.application.exceptions import ValidationError
from resource_registry.services.workbook_ingestion_job_execution_service import (
    WorkbookIngestionJobExecutionService,
)


class WorkbookIngestionJobExecutionServiceTests(SimpleTestCase):
    def setUp(self) -> None:
        """Stub transaction handling because these unit tests do not require a database."""
        transaction_patcher = patch(
            "resource_registry.services.workbook_ingestion_job_execution_service.transaction.atomic"
        )
        transaction_patcher.start()
        self.addCleanup(transaction_patcher.stop)

    def test_build_upload_registration_command_uses_stable_workbook_slug(self) -> None:
        job = SimpleNamespace(
            registration_submission=SimpleNamespace(
                organization_id=1,
                workspace_id=2,
                visibility_scope="WORKSPACE",
            ),
            created_by=None,
        )
        source = SimpleNamespace(
            original_filename="Quarterly Sales.xlsx",
            tenant_id="tenant-1",
            local_path="/tmp/quarterly-sales.xlsx",
            storage_provider="local",
            content_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
        )

        command = WorkbookIngestionJobExecutionService._build_upload_registration_command(
            job=job,
            source=source,
        )

        assert command.slug == "quarterly-sales"
        assert command.ingest_all_sheets is True

    def test_execute_job_marks_completed_with_skips(self) -> None:
        job = SimpleNamespace(
            id="job-1",
            tenant_id="tenant-1",
            request_payload_json={
                "tenant_id": "tenant-1",
                "slug": "sales",
                "name": "Sales",
                "path": "/tmp/sales.xlsx",
                "source_type": "FILE_UPLOAD",
            },
        )
        job_service = MagicMock()
        job_service.get_job.return_value = job
        job_service.mark_running.return_value = job
        job_service.mark_completed.return_value = job
        facade = MagicMock()
        facade.register_workbook.return_value = RegisterDatasetBatchResult(
            created=[
                RegisterDatasetResult(
                    dataset_id="d1",
                    dataset_version_id="v1",
                    schema_id="s1",
                    profile_id="p1",
                    artifact_id="a1",
                    row_count=1,
                    column_count=2,
                    source_format="xlsx",
                    file_size_bytes=100,
                ),
            ],
            deduplicated=[],
            skipped=[SkippedWorksheet(sheet_name="Sheet2", code="empty_dataset", message="empty")],
            total_processed=2,
            total_created=1,
            total_deduplicated=0,
            total_skipped=1,
        )

        service = WorkbookIngestionJobExecutionService(
            job_service=job_service,
            registration_facade=facade,
        )
        result = service.execute_job(tenant_id="tenant-1", ingestion_job_id="job-1")

        self.assertEqual(result, job)
        job_service.mark_completed.assert_called_once()
        self.assertTrue(job_service.mark_completed.call_args.kwargs["has_skips"])
        job_service.mark_failed.assert_not_called()

    def test_execute_job_marks_failed_for_validation_error(self) -> None:
        job = SimpleNamespace(
            id="job-1",
            tenant_id="tenant-1",
            request_payload_json={
                "tenant_id": "tenant-1",
                "slug": "sales",
                "name": "Sales",
                "path": "/tmp/sales.xlsx",
                "source_type": "FILE_UPLOAD",
            },
        )
        job_service = MagicMock()
        job_service.get_job.return_value = job
        job_service.mark_running.return_value = job
        job_service.mark_failed.return_value = job
        facade = MagicMock()
        facade.register_workbook.side_effect = ValidationError(
            "invalid workbook",
            status=400,
            code="workbook_has_no_valid_sheets",
        )

        service = WorkbookIngestionJobExecutionService(
            job_service=job_service,
            registration_facade=facade,
        )
        result = service.execute_job(tenant_id="tenant-1", ingestion_job_id="job-1")

        self.assertEqual(result, job)
        job_service.mark_completed.assert_not_called()
        job_service.mark_failed.assert_called_once()
        self.assertFalse(job_service.mark_failed.call_args.kwargs.get("retryable", False))

    def test_execute_job_does_not_convert_validation_error_to_timeout(self) -> None:
        job = SimpleNamespace(
            id="job-1",
            tenant_id="tenant-1",
            request_payload_json={
                "tenant_id": "tenant-1",
                "slug": "sales",
                "name": "Sales",
                "path": "/tmp/sales.xlsx",
                "source_type": "FILE_UPLOAD",
            },
        )
        job_service = MagicMock()
        job_service.get_job.return_value = job
        job_service.mark_running.return_value = job
        job_service.mark_failed.return_value = job
        facade = MagicMock()
        facade.register_workbook.side_effect = ValidationError(
            "invalid workbook",
            status=400,
            code="workbook_has_no_valid_sheets",
        )
        service = WorkbookIngestionJobExecutionService(
            job_service=job_service,
            registration_facade=facade,
            execution_timeout_seconds=1,
        )

        with patch(
            "resource_registry.services.workbook_ingestion_job_execution_service.time.perf_counter",
            side_effect=[0.0, 2.0],
        ):
            result = service.execute_job(tenant_id="tenant-1", ingestion_job_id="job-1")

        self.assertEqual(result, job)
        kwargs = job_service.mark_failed.call_args.kwargs
        self.assertEqual(kwargs["error_code"], "workbook_has_no_valid_sheets")
        self.assertFalse(kwargs.get("retryable", False))

    def test_execute_job_marks_generic_exception_retryable(self) -> None:
        job = SimpleNamespace(
            id="job-1",
            tenant_id="tenant-1",
            request_payload_json={
                "tenant_id": "tenant-1",
                "slug": "sales",
                "name": "Sales",
                "path": "/tmp/sales.xlsx",
                "source_type": "FILE_UPLOAD",
            },
        )
        job_service = MagicMock()
        job_service.get_job.return_value = job
        job_service.mark_running.return_value = job
        job_service.mark_failed.return_value = job
        facade = MagicMock()
        facade.register_workbook.side_effect = RuntimeError("temporary")
        service = WorkbookIngestionJobExecutionService(
            job_service=job_service,
            registration_facade=facade,
            execution_timeout_seconds=1,
        )

        with patch(
            "resource_registry.services.workbook_ingestion_job_execution_service.time.perf_counter",
            side_effect=[0.0, 2.0],
        ):
            result = service.execute_job(tenant_id="tenant-1", ingestion_job_id="job-1")

        self.assertEqual(result, job)
        job_service.mark_completed.assert_not_called()
        job_service.mark_failed.assert_called_once()
        self.assertTrue(job_service.mark_failed.call_args.kwargs["retryable"])

    def test_execute_job_soft_timeout_after_success_delegates_retryable_failure(self) -> None:
        job = SimpleNamespace(
            id="job-1",
            tenant_id="tenant-1",
            request_payload_json={
                "tenant_id": "tenant-1",
                "slug": "sales",
                "name": "Sales",
                "path": "/tmp/sales.xlsx",
                "source_type": "FILE_UPLOAD",
            },
        )
        job_service = MagicMock()
        job_service.get_job.return_value = job
        job_service.mark_running.return_value = job
        job_service.mark_failed.return_value = job
        facade = MagicMock()
        facade.register_workbook.return_value = RegisterDatasetBatchResult(
            created=[],
            deduplicated=[],
            skipped=[],
            total_processed=0,
            total_created=0,
            total_deduplicated=0,
            total_skipped=0,
        )
        service = WorkbookIngestionJobExecutionService(
            job_service=job_service,
            registration_facade=facade,
            execution_timeout_seconds=1,
        )

        with patch(
            "resource_registry.services.workbook_ingestion_job_execution_service.time.perf_counter",
            side_effect=[0.0, 2.0],
        ):
            result = service.execute_job(tenant_id="tenant-1", ingestion_job_id="job-1")

        self.assertEqual(result, job)
        job_service.mark_completed.assert_not_called()
        kwargs = job_service.mark_failed.call_args.kwargs
        self.assertEqual(kwargs["error_code"], "execution_timeout")
        self.assertTrue(kwargs["retryable"])

    def test_execute_job_disabled_soft_timeout_allows_late_success(self) -> None:
        job = SimpleNamespace(
            id="job-1",
            tenant_id="tenant-1",
            request_payload_json={
                "tenant_id": "tenant-1",
                "slug": "sales",
                "name": "Sales",
                "path": "/tmp/sales.xlsx",
                "source_type": "FILE_UPLOAD",
            },
        )
        job_service = MagicMock()
        job_service.get_job.return_value = job
        job_service.mark_running.return_value = job
        job_service.mark_completed.return_value = job
        facade = MagicMock()
        facade.register_workbook.return_value = RegisterDatasetBatchResult(
            created=[],
            deduplicated=[],
            skipped=[],
            total_processed=0,
            total_created=0,
            total_deduplicated=0,
            total_skipped=0,
        )
        service = WorkbookIngestionJobExecutionService(
            job_service=job_service,
            registration_facade=facade,
            execution_timeout_seconds=0,
        )

        with patch(
            "resource_registry.services.workbook_ingestion_job_execution_service.time.perf_counter",
            side_effect=[0.0, 2.0],
        ):
            result = service.execute_job(tenant_id="tenant-1", ingestion_job_id="job-1")

        self.assertEqual(result, job)
        job_service.mark_completed.assert_called_once()
        job_service.mark_failed.assert_not_called()

    def test_execute_job_marks_cancelled_when_cancellation_is_requested_after_execution(
        self,
    ) -> None:
        job = SimpleNamespace(
            id="job-1",
            tenant_id="tenant-1",
            request_payload_json={
                "tenant_id": "tenant-1",
                "slug": "sales",
                "name": "Sales",
                "path": "/tmp/sales.xlsx",
                "source_type": "FILE_UPLOAD",
            },
        )
        cancelled_job = SimpleNamespace(id="job-1", status="cancelled")
        job_service = MagicMock()
        job_service.get_job.return_value = job
        job_service.mark_running.return_value = job
        job_service.is_cancellation_requested.return_value = True
        job_service.mark_cancelled.return_value = cancelled_job
        facade = MagicMock()
        facade.register_workbook.return_value = RegisterDatasetBatchResult(
            created=[],
            deduplicated=[],
            skipped=[],
            total_processed=0,
            total_created=0,
            total_deduplicated=0,
            total_skipped=0,
        )
        service = WorkbookIngestionJobExecutionService(
            job_service=job_service,
            registration_facade=facade,
        )

        result = service.execute_job(tenant_id="tenant-1", ingestion_job_id="job-1")

        self.assertEqual(result, cancelled_job)
        job_service.mark_cancelled.assert_called_once_with(job=job)
        job_service.mark_completed.assert_not_called()
        job_service.mark_failed.assert_not_called()
