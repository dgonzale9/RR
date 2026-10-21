from __future__ import annotations

from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from django.core.management import call_command
from django.test import SimpleTestCase

from resource_registry.services.workbook_ingestion_job_execution_service import (
    WorkbookIngestionJobExecutionService,
)
from resource_registry.services.workbook_ingestion_job_worker_runner import (
    WorkbookIngestionJobWorkerRunner,
)


class WorkbookIngestionJobWorkerRunnerTests(SimpleTestCase):
    def test_run_once_executes_claimed_jobs(self) -> None:
        claimed_job = SimpleNamespace(
            id=str(uuid4()),
            tenant_id=str(uuid4()),
            status="running",
            error_code=None,
            execution_ms=10,
            workbook_group_id=None,
        )
        job_service = MagicMock()
        job_service.claim_queued_jobs.return_value = [claimed_job]
        execution_service = MagicMock()
        execution_service.execute_job.return_value = claimed_job
        runner = WorkbookIngestionJobWorkerRunner(
            job_service=job_service,
            execution_service=execution_service,
            batch_size=2,
            stale_running_seconds=0,
        )

        claimed = runner.run_once()

        self.assertEqual(claimed, 1)
        execution_service.execute_job.assert_called_once()

    def test_cancellation_requested_before_execution_skips_execution(self) -> None:
        claimed_job = SimpleNamespace(
            id=str(uuid4()),
            tenant_id=str(uuid4()),
            status="running",
            error_code=None,
            execution_ms=0,
            workbook_group_id=None,
        )
        cancelled_job = SimpleNamespace(
            id=claimed_job.id,
            tenant_id=claimed_job.tenant_id,
            status="cancelled",
            error_code=None,
            execution_ms=0,
            workbook_group_id=None,
        )
        job_service = MagicMock()
        job_service.claim_queued_jobs.return_value = [claimed_job]
        job_service.is_cancellation_requested.return_value = True
        job_service.mark_cancelled.return_value = cancelled_job
        execution_service = MagicMock()
        runner = WorkbookIngestionJobWorkerRunner(
            job_service=job_service,
            execution_service=execution_service,
            batch_size=1,
            stale_running_seconds=0,
        )

        _ = runner.run_once()

        execution_service.execute_job.assert_not_called()
        job_service.mark_cancelled.assert_called_once_with(job=claimed_job)

    def test_cancellation_finalizes_linked_staged_detachment(self) -> None:
        job_id = uuid4()
        claimed_job = SimpleNamespace(
            id=str(job_id),
            tenant_id=str(uuid4()),
            status="running",
            error_code=None,
            execution_ms=0,
            workbook_group_id=None,
            registration_submission_id=uuid4(),
        )
        cancelled_job = SimpleNamespace(
            id=str(job_id),
            tenant_id=claimed_job.tenant_id,
            status="cancelled",
            error_code=None,
            execution_ms=0,
            workbook_group_id=None,
            registration_submission_id=claimed_job.registration_submission_id,
        )
        job_service = MagicMock()
        job_service.claim_queued_jobs.return_value = [claimed_job]
        job_service.is_cancellation_requested.return_value = True
        job_service.mark_cancelled.return_value = cancelled_job
        execution_service = MagicMock()
        removal_service = MagicMock()
        runner = WorkbookIngestionJobWorkerRunner(
            job_service=job_service,
            execution_service=execution_service,
            destination_assignment_removal_service=removal_service,
            batch_size=1,
            stale_running_seconds=0,
        )

        _ = runner.run_once()

        removal_service.finalize_detachment_for_ingestion_job.assert_called_once_with(
            ingestion_job_id=job_id
        )

    def test_cancellation_requested_during_worker_failure_wins_over_retry(self) -> None:
        claimed_job = SimpleNamespace(
            id=str(uuid4()),
            tenant_id=str(uuid4()),
            status="running",
            error_code=None,
            execution_ms=0,
            workbook_group_id=None,
        )
        current_job = SimpleNamespace(
            id=claimed_job.id,
            tenant_id=claimed_job.tenant_id,
            status="running",
            error_code=None,
            execution_ms=0,
            workbook_group_id=None,
        )
        cancelled_job = SimpleNamespace(
            id=claimed_job.id,
            tenant_id=claimed_job.tenant_id,
            status="cancelled",
            error_code=None,
            execution_ms=0,
            workbook_group_id=None,
        )
        job_service = MagicMock()
        job_service.claim_queued_jobs.return_value = [claimed_job]
        job_service.get_job.return_value = current_job
        job_service.is_cancellation_requested.side_effect = [False, True, False]
        job_service.mark_cancelled.return_value = cancelled_job
        execution_service = MagicMock()
        execution_service.execute_job.side_effect = RuntimeError("boom")
        runner = WorkbookIngestionJobWorkerRunner(
            job_service=job_service,
            execution_service=execution_service,
            batch_size=1,
            stale_running_seconds=0,
        )

        _ = runner.run_once()

        job_service.mark_cancelled.assert_called_once_with(job=current_job)
        job_service.mark_failed.assert_not_called()

    def test_runner_defaults_to_execution_service_class(self) -> None:
        runner = WorkbookIngestionJobWorkerRunner()
        self.assertIsInstance(runner._execution_service, WorkbookIngestionJobExecutionService)

    def test_runner_passes_execution_timeout_to_default_execution_service(self) -> None:
        job_service = MagicMock()
        with patch(
            "resource_registry.services.workbook_ingestion_job_worker_runner."
            "WorkbookIngestionJobExecutionService",
        ) as execution_service_class:
            runner = WorkbookIngestionJobWorkerRunner(
                job_service=job_service,
                execution_timeout_seconds=7,
            )

        self.assertEqual(runner._execution_service, execution_service_class.return_value)
        execution_service_class.assert_called_once_with(
            job_service=job_service,
            execution_timeout_seconds=7,
        )


class RunWorkbookIngestionJobsCommandTests(SimpleTestCase):
    def test_command_once_executes_runner(self) -> None:
        mocked_runner = MagicMock()
        mocked_runner.run.return_value = SimpleNamespace(claimed_jobs=1, processed_jobs=1)
        stdout = StringIO()
        with patch(
            "resource_registry.management.commands.run_workbook_ingestion_jobs."
            "WorkbookIngestionJobWorkerRunner",
            return_value=mocked_runner,
        ):
            call_command("run_workbook_ingestion_jobs", "--once", stdout=stdout)

        mocked_runner.run.assert_called_once()
        kwargs = mocked_runner.run.call_args.kwargs
        self.assertTrue(kwargs["once"])
        self.assertIn("claimed=1 processed=1", stdout.getvalue())
