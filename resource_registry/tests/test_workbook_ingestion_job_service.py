from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from resource_registry.models import WorkbookIngestionJobStatus
from resource_registry.services.workbook_ingestion_job_service import WorkbookIngestionJobService


class WorkbookIngestionJobServiceTests(SimpleTestCase):
    def test_create_job_persists_queued_job(self) -> None:
        service = WorkbookIngestionJobService()
        fake_created = SimpleNamespace(id="job-1")
        with patch(
            "resource_registry.services.workbook_ingestion_job_service."
            "WorkbookIngestionJob.objects.create",
            return_value=fake_created,
        ) as create_mock, patch.object(
            service,
            "_find_active_job_by_fingerprint",
            return_value=None,
        ), patch("resource_registry.services.workbook_ingestion_job_service.transaction.atomic"):
            result = service.create_job(
                tenant_id="tenant-1",
                command_payload={
                    "tenant_id": "tenant-1",
                    "slug": "sales",
                    "name": "Sales",
                    "path": "/tmp/sales.xlsx",
                    "source_type": "FILE_UPLOAD",
                },
                created_by=SimpleNamespace(pk=1),
            )

        self.assertEqual(result, fake_created)
        kwargs = create_mock.call_args.kwargs
        self.assertEqual(kwargs["tenant_id"], "tenant-1")
        self.assertEqual(kwargs["status"], WorkbookIngestionJobStatus.QUEUED)
        self.assertIn("slug", kwargs["request_payload_json"])
        self.assertEqual(len(kwargs["request_fingerprint"]), 64)
        self.assertTrue(str(kwargs["idempotency_key"]).startswith("workbook_ingestion_job:"))

    def test_create_job_returns_existing_active_job_for_duplicate_request(self) -> None:
        service = WorkbookIngestionJobService()
        existing_job = SimpleNamespace(id="job-1", status=WorkbookIngestionJobStatus.RUNNING)
        with patch.object(
            service,
            "_find_active_job_by_fingerprint",
            return_value=existing_job,
        ), patch(
            "resource_registry.services.workbook_ingestion_job_service."
            "WorkbookIngestionJob.objects.create",
        ) as create_mock, patch(
            "resource_registry.services.workbook_ingestion_job_service.transaction.atomic",
        ):
            result = service.create_job(
                tenant_id="tenant-1",
                command_payload={"tenant_id": "tenant-1", "slug": "sales"},
            )

        self.assertEqual(result, existing_job)
        create_mock.assert_not_called()

    def test_terminal_workbook_jobs_do_not_block_new_creation(self) -> None:
        self.assertSetEqual(
            WorkbookIngestionJobService._active_statuses,
            {WorkbookIngestionJobStatus.QUEUED, WorkbookIngestionJobStatus.RUNNING},
        )

    def test_mark_running_updates_status_and_started_at(self) -> None:
        service = WorkbookIngestionJobService()
        job = SimpleNamespace(
            status=WorkbookIngestionJobStatus.QUEUED,
            started_at=None,
            save=lambda **kwargs: None,
        )
        save_calls: list[dict] = []
        job.save = lambda **kwargs: save_calls.append(kwargs)

        updated = service.mark_running(job=job)

        self.assertEqual(updated.status, WorkbookIngestionJobStatus.RUNNING)
        self.assertIsNotNone(updated.started_at)
        self.assertEqual(save_calls[0]["update_fields"], ["status", "started_at"])

    def test_mark_completed_supports_completed_with_skips(self) -> None:
        service = WorkbookIngestionJobService()
        job = SimpleNamespace(
            status=WorkbookIngestionJobStatus.RUNNING,
            result_payload_json=None,
            error_code="old",
            error_message="old",
            execution_ms=None,
            completed_at=None,
            workbook_group_id=None,
        )
        save_calls: list[dict] = []
        job.save = lambda **kwargs: save_calls.append(kwargs)

        result = service.mark_completed(
            job=job,
            result_payload={"total_processed": 2, "total_skipped": 1},
            workbook_group_id="group-1",
            has_skips=True,
            execution_ms=321,
        )

        self.assertEqual(result.status, WorkbookIngestionJobStatus.COMPLETED_WITH_SKIPS)
        self.assertEqual(result.workbook_group_id, "group-1")
        self.assertEqual(result.execution_ms, 321)
        self.assertIsNone(result.error_code)
        self.assertIn("result_payload_json", save_calls[0]["update_fields"])

    def test_mark_failed_sanitizes_sensitive_error(self) -> None:
        service = WorkbookIngestionJobService()
        job = SimpleNamespace(
            status=WorkbookIngestionJobStatus.RUNNING,
            error_code=None,
            error_message=None,
            execution_ms=None,
            completed_at=None,
            retry_count=0,
        )
        job.save = lambda **kwargs: None

        result = service.mark_failed(
            job=job,
            error_code="workbook_ingestion_error",
            error_message="failed with token=abc and secret payload",
            increment_retry=True,
        )

        self.assertEqual(result.status, WorkbookIngestionJobStatus.FAILED)
        self.assertEqual(result.retry_count, 1)
        self.assertNotIn("token", str(result.error_message).lower())
        self.assertNotIn("secret", str(result.error_message).lower())

    def test_mark_failed_requeues_retryable_failure_before_retry_limit(self) -> None:
        service = WorkbookIngestionJobService(max_retries=1)
        job = SimpleNamespace(
            status=WorkbookIngestionJobStatus.RUNNING,
            error_code=None,
            error_message=None,
            execution_ms=None,
            started_at=object(),
            completed_at=object(),
            retry_count=0,
        )
        save_calls: list[dict] = []
        job.save = lambda **kwargs: save_calls.append(kwargs)

        result = service.mark_failed(
            job=job,
            error_code="workbook_ingestion_error",
            error_message="temporary failure",
            retryable=True,
        )

        self.assertEqual(result.status, WorkbookIngestionJobStatus.QUEUED)
        self.assertEqual(result.retry_count, 1)
        self.assertIsNone(result.started_at)
        self.assertIsNone(result.completed_at)
        self.assertIn("retry_count", save_calls[0]["update_fields"])

    def test_request_cancellation_is_tenant_scoped_and_status_guarded(self) -> None:
        source = inspect.getsource(WorkbookIngestionJobService.request_cancellation)
        mark_completed_source = inspect.getsource(WorkbookIngestionJobService.mark_completed)
        mark_failed_source = inspect.getsource(WorkbookIngestionJobService.mark_failed)

        self.assertIn("transaction.atomic", source)
        self.assertIn("select_for_update", source)
        self.assertIn("tenant_id=tenant_id", source)
        self.assertIn("WorkbookIngestionJobStatus.QUEUED", source)
        self.assertIn("WorkbookIngestionJobStatus.CANCELLED", source)
        self.assertIn("transaction.atomic", mark_completed_source)
        self.assertIn("select_for_update", mark_completed_source)
        self.assertIn("transaction.atomic", mark_failed_source)
        self.assertIn("select_for_update", mark_failed_source)

    def test_mark_cancelled_transitions_running_job_without_retry_increment(self) -> None:
        service = WorkbookIngestionJobService()
        job = SimpleNamespace(
            status=WorkbookIngestionJobStatus.RUNNING,
            cancellation_requested_at=None,
            completed_at=None,
            retry_count=1,
        )
        save_calls: list[dict] = []
        job.save = lambda **kwargs: save_calls.append(kwargs)

        result = service.mark_cancelled(job=job)

        self.assertEqual(result.status, WorkbookIngestionJobStatus.CANCELLED)
        self.assertIsNotNone(result.cancellation_requested_at)
        self.assertIsNotNone(result.completed_at)
        self.assertEqual(result.retry_count, 1)
        self.assertIn("cancellation_requested_at", save_calls[0]["update_fields"])

    def test_mark_failed_cancels_when_cancellation_is_requested_before_retry(self) -> None:
        service = WorkbookIngestionJobService(max_retries=3)
        job = SimpleNamespace(
            status=WorkbookIngestionJobStatus.RUNNING,
            retry_count=0,
            cancellation_requested_at=object(),
            completed_at=None,
            error_code=None,
            error_message=None,
            execution_ms=None,
        )
        job.save = lambda **kwargs: None

        result = service.mark_failed(
            job=job,
            error_code="workbook_ingestion_error",
            error_message="temporary failure",
            retryable=True,
        )

        self.assertEqual(result.status, WorkbookIngestionJobStatus.CANCELLED)
        self.assertEqual(result.retry_count, 0)

    def test_terminal_jobs_are_not_overwritten_by_cancellation_completion_or_failure(self) -> None:
        service = WorkbookIngestionJobService()
        completed_job = SimpleNamespace(status=WorkbookIngestionJobStatus.COMPLETED)
        failed_job = SimpleNamespace(status=WorkbookIngestionJobStatus.FAILED)
        cancelled_job = SimpleNamespace(
            status=WorkbookIngestionJobStatus.CANCELLED,
            cancellation_requested_at=object(),
        )
        completed_job.save = lambda **kwargs: self.fail("completed job should not save")
        failed_job.save = lambda **kwargs: self.fail("failed job should not save")
        cancelled_job.save = lambda **kwargs: self.fail("cancelled job should not save")

        completed_result = service.mark_completed(job=completed_job, result_payload={})
        failed_result = service.mark_failed(
            job=failed_job,
            error_code="workbook_ingestion_error",
            error_message="temporary failure",
            retryable=True,
        )
        cancelled_result = service.mark_cancelled(job=cancelled_job)

        self.assertEqual(completed_result.status, WorkbookIngestionJobStatus.COMPLETED)
        self.assertEqual(failed_result.status, WorkbookIngestionJobStatus.FAILED)
        self.assertEqual(cancelled_result.status, WorkbookIngestionJobStatus.CANCELLED)
