from __future__ import annotations

import json
from io import StringIO
from threading import Event
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from django.core.management import call_command
from django.test import SimpleTestCase

from resource_registry.models import DatasetIntelligenceJobOperation, DatasetIntelligenceJobStatus
from resource_registry.read_auth.context import ReadAuthorizationContext
from resource_registry.services.dataset_intelligence_job_execution_service import (
    DatasetIntelligenceJobExecutionService,
)
from resource_registry.services.dataset_intelligence_job_worker_runner import (
    DatasetIntelligenceJobWorkerRunner,
)


class DatasetIntelligenceJobWorkerRunnerTests(SimpleTestCase):
    def setUp(self) -> None:
        """Provide bounded prevalidated authority for legacy runner unit tests."""
        authority_service_class = self.enterContext(
            patch(
                "resource_registry.services.dataset_intelligence_job_worker_runner."
                "WorkerExecutionAuthorityService"
            )
        )
        authority_service_class.return_value.build_intelligence_context.side_effect = (
            self._build_execution_authority
        )

    @staticmethod
    def _build_execution_authority(*, job: object) -> SimpleNamespace:
        tenant_id = str(job.tenant_id)
        job_id = str(job.id)
        dataset_id = getattr(job, "dataset_id", None)
        context = ReadAuthorizationContext(
            tenant_id=tenant_id,
            actor="user:1",
            roles=("reader",),
            allowed_dataset_ids=(frozenset({str(dataset_id)}) if dataset_id else frozenset()),
            metadata={"source": "delegated_job_authority", "job_id": job_id},
        )
        return SimpleNamespace(to_read_authorization_context=lambda: context)

    def test_run_once_claims_and_executes_jobs(self) -> None:
        claimed_job = self._build_job(status=DatasetIntelligenceJobStatus.RUNNING)
        completed_job = self._build_job(
            job_id=claimed_job.id,
            tenant_id=claimed_job.tenant_id,
            status=DatasetIntelligenceJobStatus.COMPLETED,
            execution_ms=12,
        )
        job_service = MagicMock()
        job_service.claim_queued_jobs.return_value = [claimed_job]
        execution_service = MagicMock()
        execution_service.execute_job.return_value = completed_job
        runner = DatasetIntelligenceJobWorkerRunner(
            job_service=job_service,
            execution_service=execution_service,
            batch_size=5,
            stale_running_seconds=0,
        )

        claimed = runner.run_once()

        self.assertEqual(claimed, 1)
        execution_service.execute_job.assert_called_once_with(
            context=execution_service.execute_job.call_args.kwargs["context"],
            intelligence_job_id=str(claimed_job.id),
        )

    def test_failed_execution_marks_job_failed_through_service(self) -> None:
        claimed_job = self._build_job(status=DatasetIntelligenceJobStatus.RUNNING)
        failed_job = self._build_job(
            job_id=claimed_job.id,
            tenant_id=claimed_job.tenant_id,
            status=DatasetIntelligenceJobStatus.FAILED,
            error_code="intelligence_execution_error",
        )
        job_service = MagicMock()
        job_service.claim_queued_jobs.return_value = [claimed_job]
        job_service.get_job.return_value = failed_job
        job_service.mark_failed.return_value = failed_job
        execution_service = MagicMock()
        execution_service.execute_job.side_effect = RuntimeError("boom")
        runner = DatasetIntelligenceJobWorkerRunner(
            job_service=job_service,
            execution_service=execution_service,
            batch_size=1,
            stale_running_seconds=0,
        )

        _ = runner.run_once()

        job_service.mark_failed.assert_called_once()
        kwargs = job_service.mark_failed.call_args.kwargs
        self.assertEqual(kwargs["error_code"], "intelligence_execution_error")
        self.assertEqual(kwargs["error_message"], "Unable to execute intelligence job.")
        self.assertTrue(kwargs["retryable"])

    def test_cancellation_requested_before_execution_skips_execution(self) -> None:
        claimed_job = self._build_job(status=DatasetIntelligenceJobStatus.RUNNING)
        cancelled_job = self._build_job(
            job_id=claimed_job.id,
            tenant_id=claimed_job.tenant_id,
            status=DatasetIntelligenceJobStatus.CANCELLED,
        )
        job_service = MagicMock()
        job_service.claim_queued_jobs.return_value = [claimed_job]
        job_service.is_cancellation_requested.return_value = True
        job_service.mark_cancelled.return_value = cancelled_job
        execution_service = MagicMock()
        runner = DatasetIntelligenceJobWorkerRunner(
            job_service=job_service,
            execution_service=execution_service,
            batch_size=1,
            stale_running_seconds=0,
        )

        _ = runner.run_once()

        execution_service.execute_job.assert_not_called()
        job_service.mark_cancelled.assert_called_once_with(job=claimed_job)

    def test_cancellation_requested_during_worker_failure_wins_over_retry(self) -> None:
        claimed_job = self._build_job(status=DatasetIntelligenceJobStatus.RUNNING)
        current_job = self._build_job(
            job_id=claimed_job.id,
            tenant_id=claimed_job.tenant_id,
            status=DatasetIntelligenceJobStatus.RUNNING,
        )
        cancelled_job = self._build_job(
            job_id=claimed_job.id,
            tenant_id=claimed_job.tenant_id,
            status=DatasetIntelligenceJobStatus.CANCELLED,
        )
        job_service = MagicMock()
        job_service.claim_queued_jobs.return_value = [claimed_job]
        job_service.get_job.return_value = current_job
        job_service.is_cancellation_requested.side_effect = [False, True, False]
        job_service.mark_cancelled.return_value = cancelled_job
        execution_service = MagicMock()
        execution_service.execute_job.side_effect = RuntimeError("boom")
        runner = DatasetIntelligenceJobWorkerRunner(
            job_service=job_service,
            execution_service=execution_service,
            batch_size=1,
            stale_running_seconds=0,
        )

        _ = runner.run_once()

        job_service.mark_cancelled.assert_called_once_with(job=current_job)
        job_service.mark_failed.assert_not_called()

    def test_batch_size_and_tenant_scope_are_forwarded_to_claim_service(self) -> None:
        job_service = MagicMock()
        job_service.claim_queued_jobs.return_value = []
        runner = DatasetIntelligenceJobWorkerRunner(
            job_service=job_service,
            execution_service=MagicMock(),
            batch_size=5,
            stale_running_seconds=0,
        )

        _ = runner.run_once(batch_size=1, tenant_id="tenant-1")

        job_service.claim_queued_jobs.assert_called_once_with(
            batch_size=1,
            tenant_id="tenant-1",
        )

    def test_max_jobs_stops_worker_after_limit(self) -> None:
        job_one = self._build_job(status=DatasetIntelligenceJobStatus.COMPLETED)
        job_two = self._build_job(status=DatasetIntelligenceJobStatus.COMPLETED)
        job_service = MagicMock()
        job_service.claim_queued_jobs.side_effect = [[job_one], [job_two], [job_one]]
        execution_service = MagicMock()
        execution_service.execute_job.side_effect = [job_one, job_two]
        runner = DatasetIntelligenceJobWorkerRunner(
            job_service=job_service,
            execution_service=execution_service,
            batch_size=2,
            stale_running_seconds=0,
        )

        stats = runner.run(max_jobs=2)

        self.assertEqual(stats.claimed_jobs, 2)
        self.assertEqual(stats.processed_jobs, 2)
        self.assertEqual(execution_service.execute_job.call_count, 2)

    def test_worker_leaves_when_no_queued_jobs_in_one_shot_cycle(self) -> None:
        job_service = MagicMock()
        job_service.claim_queued_jobs.return_value = []
        execution_service = MagicMock()
        runner = DatasetIntelligenceJobWorkerRunner(
            job_service=job_service,
            execution_service=execution_service,
            batch_size=10,
            stale_running_seconds=0,
        )

        claimed = runner.run_once()

        self.assertEqual(claimed, 0)
        execution_service.execute_job.assert_not_called()

    def test_one_shot_mode_exits_after_single_empty_cycle(self) -> None:
        job_service = MagicMock()
        job_service.claim_queued_jobs.return_value = []
        runner = DatasetIntelligenceJobWorkerRunner(
            job_service=job_service,
            execution_service=MagicMock(),
            poll_interval_seconds=0.1,
            stale_running_seconds=0,
        )

        stats = runner.run(once=True)

        self.assertEqual(stats.claimed_jobs, 0)
        self.assertEqual(stats.processed_jobs, 0)
        self.assertEqual(job_service.claim_queued_jobs.call_count, 1)

    def test_worker_preserves_tenant_authorization_context(self) -> None:
        claimed_job = self._build_job(status=DatasetIntelligenceJobStatus.RUNNING)
        completed_job = self._build_job(
            job_id=claimed_job.id,
            tenant_id=claimed_job.tenant_id,
            status=DatasetIntelligenceJobStatus.COMPLETED,
        )
        job_service = MagicMock()
        job_service.claim_queued_jobs.return_value = [claimed_job]
        execution_service = MagicMock()
        execution_service.execute_job.return_value = completed_job
        runner = DatasetIntelligenceJobWorkerRunner(
            job_service=job_service,
            execution_service=execution_service,
            batch_size=1,
            stale_running_seconds=0,
        )

        _ = runner.run_once()

        context = execution_service.execute_job.call_args.kwargs["context"]
        self.assertEqual(str(context.tenant_id), str(claimed_job.tenant_id))
        self.assertEqual(context.actor, "user:1")
        self.assertEqual(context.roles, ("reader",))
        self.assertEqual(context.metadata["job_id"], str(claimed_job.id))

    def test_stop_event_stops_worker_before_claiming(self) -> None:
        stop_event = Event()
        stop_event.set()
        job_service = MagicMock()
        runner = DatasetIntelligenceJobWorkerRunner(
            job_service=job_service,
            execution_service=MagicMock(),
            stale_running_seconds=0,
        )

        stats = runner.run(stop_event=stop_event)

        self.assertEqual(stats.claimed_jobs, 0)
        self.assertEqual(stats.processed_jobs, 0)
        job_service.claim_queued_jobs.assert_not_called()

    def test_runner_defaults_to_execution_service_class(self) -> None:
        runner = DatasetIntelligenceJobWorkerRunner()
        self.assertIsInstance(runner._execution_service, DatasetIntelligenceJobExecutionService)

    def test_runner_passes_execution_timeout_to_default_execution_service(self) -> None:
        job_service = MagicMock()
        with patch(
            "resource_registry.services.dataset_intelligence_job_worker_runner."
            "DatasetIntelligenceJobExecutionService",
        ) as execution_service_class:
            runner = DatasetIntelligenceJobWorkerRunner(
                job_service=job_service,
                execution_timeout_seconds=7,
            )

        self.assertEqual(runner._execution_service, execution_service_class.return_value)
        execution_service_class.assert_called_once_with(
            job_service=job_service,
            execution_timeout_seconds=7,
        )

    def test_worker_logs_do_not_include_payloads_or_secrets(self) -> None:
        claimed_job = self._build_job(status=DatasetIntelligenceJobStatus.RUNNING)
        completed_job = self._build_job(
            job_id=claimed_job.id,
            tenant_id=claimed_job.tenant_id,
            status=DatasetIntelligenceJobStatus.COMPLETED,
            execution_ms=9,
        )
        job_service = MagicMock()
        job_service.claim_queued_jobs.return_value = [claimed_job]
        execution_service = MagicMock()
        execution_service.execute_job.return_value = completed_job
        runner = DatasetIntelligenceJobWorkerRunner(
            job_service=job_service,
            execution_service=execution_service,
            batch_size=1,
            stale_running_seconds=0,
        )

        with patch(
            "resource_registry.services.dataset_intelligence_job_worker_runner._logger.info",
        ) as info_mock:
            _ = runner.run_once()

        logged_extra = info_mock.call_args.kwargs["extra"]
        serialized = json.dumps(logged_extra).lower()
        self.assertNotIn("request_payload", serialized)
        self.assertNotIn("token", serialized)
        self.assertNotIn("secret", serialized)

    def _build_job(  # noqa: PLR0913
        self,
        *,
        job_id: str | None = None,
        tenant_id: str | None = None,
        status: str,
        operation: str = DatasetIntelligenceJobOperation.ANALYSIS,
        error_code: str | None = None,
        execution_ms: int = 0,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            id=job_id or str(uuid4()),
            tenant_id=tenant_id or str(uuid4()),
            operation=operation,
            status=status,
            error_code=error_code,
            execution_ms=execution_ms,
        )


class RunDatasetIntelligenceJobsCommandTests(SimpleTestCase):
    def test_command_once_executes_runner(self) -> None:
        mocked_runner = MagicMock()
        mocked_runner.run.return_value = SimpleNamespace(claimed_jobs=1, processed_jobs=1)
        stdout = StringIO()
        with patch(
            "resource_registry.management.commands.run_dataset_intelligence_jobs."
            "DatasetIntelligenceJobWorkerRunner",
            return_value=mocked_runner,
        ):
            call_command("run_dataset_intelligence_jobs", "--once", stdout=stdout)

        mocked_runner.run.assert_called_once()
        kwargs = mocked_runner.run.call_args.kwargs
        self.assertTrue(kwargs["once"])
        self.assertIn("claimed=1 processed=1", stdout.getvalue())

    def test_command_passes_max_jobs_batch_poll_and_tenant_configuration(self) -> None:
        mocked_runner = MagicMock()
        mocked_runner.run.return_value = SimpleNamespace(claimed_jobs=2, processed_jobs=2)
        tenant_id = str(uuid4())
        with patch(
            "resource_registry.management.commands.run_dataset_intelligence_jobs."
            "DatasetIntelligenceJobWorkerRunner",
            return_value=mocked_runner,
        ) as runner_class:
            call_command(
                "run_dataset_intelligence_jobs",
                "--once",
                "--batch-size",
                "4",
                "--max-jobs",
                "2",
                "--tenant-id",
                tenant_id,
                "--poll-interval-seconds",
                "0.2",
            )

        runner_kwargs = runner_class.call_args.kwargs
        self.assertEqual(runner_kwargs["batch_size"], 4)
        self.assertEqual(runner_kwargs["poll_interval_seconds"], 0.2)
        run_kwargs = mocked_runner.run.call_args.kwargs
        self.assertEqual(run_kwargs["max_jobs"], 2)
        self.assertEqual(run_kwargs["tenant_id"], tenant_id)
        self.assertTrue(run_kwargs["once"])
