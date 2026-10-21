from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from resource_registry.models import WorkbookIngestionJob, WorkbookIngestionJobStatus
from resource_registry.services.workbook_ingestion_job_execution_service import (
    WorkbookIngestionJobExecutionService,
)
from resource_registry.services.workbook_ingestion_job_service import WorkbookIngestionJobService
from resource_registry.services.workbook_ingestion_job_worker_runner import (
    WorkbookIngestionJobWorkerRunner,
)


class WorkbookIngestionJobAsyncDurabilityVerificationTests(SimpleTestCase):
    """Verify current async durability behavior for workbook ingestion jobs."""

    def test_job_creation_lifecycle_persists_queued_status(self) -> None:
        fake_job = MagicMock()

        with patch(
            "resource_registry.services.workbook_ingestion_job_service.WorkbookIngestionJob.objects.create",
            return_value=fake_job,
        ) as create_mock, patch(
            "resource_registry.services.workbook_ingestion_job_service."
            "WorkbookIngestionJobService._find_active_job_by_fingerprint",
            return_value=None,
        ), patch("resource_registry.services.workbook_ingestion_job_service.transaction.atomic"):
            created_job = WorkbookIngestionJobService().create_job(
                tenant_id="tenant-1",
                command_payload={"slug": "durability"},
            )

        self.assertEqual(created_job, fake_job)
        self.assertEqual(create_mock.call_args.kwargs["status"], WorkbookIngestionJobStatus.QUEUED)
        self.assertEqual(create_mock.call_args.kwargs["tenant_id"], "tenant-1")

    def test_claiming_atomicity_has_row_lock_source_evidence(self) -> None:
        source = inspect.getsource(WorkbookIngestionJobService.claim_queued_jobs)

        self.assertIn("transaction.atomic", source)
        self.assertIn("select_for_update", source)
        self.assertIn("skip_locked=True", source)
        self.assertIn("status=WorkbookIngestionJobStatus.QUEUED", source)

    def test_claimed_jobs_are_filtered_out_of_future_claims_by_source_contract(self) -> None:
        source = inspect.getsource(WorkbookIngestionJobService.claim_queued_jobs)

        self.assertIn("status=WorkbookIngestionJobStatus.QUEUED", source)
        self.assertIn("status=WorkbookIngestionJobStatus.RUNNING", source)

    def test_restart_recovery_requeues_or_fails_stale_running_jobs(self) -> None:
        source = inspect.getsource(WorkbookIngestionJobService.recover_stale_running_jobs)
        runner_source = inspect.getsource(
            WorkbookIngestionJobWorkerRunner._recover_stale_running_jobs,
        )

        self.assertIn("status=WorkbookIngestionJobStatus.RUNNING", source)
        self.assertIn("WorkbookIngestionJobStatus.QUEUED", source)
        self.assertIn("WorkbookIngestionJobStatus.FAILED", source)
        self.assertIn("WorkbookIngestionJobStatus.CANCELLED", source)
        self.assertIn("retry_count", source)
        self.assertIn("cancellation_requested_at", source)
        self.assertIn("recover_stale_running_jobs", runner_source)

    def test_runner_failure_marks_retryable_failure_through_service(self) -> None:
        claimed_job = MagicMock()
        claimed_job.id = "job-1"
        claimed_job.tenant_id = "tenant-1"
        claimed_job.status = WorkbookIngestionJobStatus.RUNNING
        claimed_job.retry_count = 0
        claimed_job.workbook_group_id = None
        claimed_job.registration_submission_id = None
        execution_service = MagicMock()
        execution_service.execute_job.side_effect = RuntimeError("boom")
        job_service = MagicMock()
        job_service.get_job.return_value = claimed_job
        job_service.mark_failed.return_value = claimed_job
        runner = WorkbookIngestionJobWorkerRunner(
            job_service=job_service,
            execution_service=execution_service,
            stale_running_seconds=0,
        )

        runner._execute_claimed_job(job=claimed_job)

        kwargs = job_service.mark_failed.call_args.kwargs
        self.assertTrue(kwargs["retryable"])

    def test_cancellation_contract_is_exposed_by_ingestion_job_service(self) -> None:
        service_source = inspect.getsource(WorkbookIngestionJobService)
        runner_source = inspect.getsource(WorkbookIngestionJobWorkerRunner._execute_claimed_job)
        field_names = {field.name for field in WorkbookIngestionJob._meta.get_fields()}

        self.assertTrue(hasattr(WorkbookIngestionJobService, "mark_cancelled"))
        self.assertTrue(hasattr(WorkbookIngestionJobService, "request_cancellation"))
        self.assertTrue(hasattr(WorkbookIngestionJobService, "is_cancellation_requested"))
        self.assertIn(WorkbookIngestionJobStatus.CANCELLED, WorkbookIngestionJobStatus.values)
        self.assertIn("cancellation_requested_at", field_names)
        self.assertIn("is_cancellation_requested", runner_source)
        self.assertIn("mark_cancelled", runner_source)
        self.assertNotIn("cancellation_requested_at", runner_source)
        self.assertIn("WorkbookIngestionJobStatus.CANCELLED", service_source)

    def test_timeout_behavior_is_exposed_by_ingestion_execution_service(self) -> None:
        init_signature = inspect.signature(WorkbookIngestionJobExecutionService.__init__)
        execute_source = inspect.getsource(WorkbookIngestionJobExecutionService.execute_job)
        timeout_source = inspect.getsource(WorkbookIngestionJobExecutionService._mark_execution_timeout)
        field_names = {field.name for field in WorkbookIngestionJob._meta.get_fields()}

        self.assertIn("execution_timeout_seconds", init_signature.parameters)
        self.assertIn("execution_timeout", execute_source)
        self.assertIn("mark_failed", execute_source)
        self.assertIn("retryable=True", timeout_source)
        self.assertNotIn("timeout_seconds", field_names)
        self.assertNotIn("timed_out_at", field_names)
        self.assertIn("time.perf_counter", execute_source)

    def test_job_creation_is_idempotent_for_duplicate_active_payloads(self) -> None:
        source = inspect.getsource(WorkbookIngestionJobService.create_job)
        constraint_names = {
            constraint.name for constraint in WorkbookIngestionJob._meta.constraints
        }
        field_names = {field.name for field in WorkbookIngestionJob._meta.get_fields()}

        self.assertIn("WorkbookIngestionJob.objects.create", source)
        self.assertIn("transaction.atomic", source)
        self.assertIn("IntegrityError", source)
        self.assertTrue(hasattr(WorkbookIngestionJobService, "_find_active_job_by_fingerprint"))
        self.assertIn("idempotency_key", field_names)
        self.assertIn("request_fingerprint", field_names)
        self.assertIn("wbij_active_fprint_uniq", constraint_names)
