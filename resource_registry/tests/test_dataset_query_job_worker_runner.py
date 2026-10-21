from __future__ import annotations

import json
from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from django.core.management import call_command
from django.test import SimpleTestCase

from BotServer.builtin_functions.resource_registry_functions import (
    resource_registry_get_query_job_result,
    resource_registry_get_query_job_status,
)
from resource_registry.read_auth.context import ReadAuthorizationContext
from resource_registry.services.dataset_query_job_execution_service import (
    DatasetQueryJobExecutionService,
)
from resource_registry.services.dataset_query_job_worker_runner import DatasetQueryJobWorkerRunner


class DatasetQueryJobWorkerRunnerTests(SimpleTestCase):
    def setUp(self) -> None:
        """Provide bounded prevalidated authority for legacy runner unit tests."""
        authority_service_class = self.enterContext(
            patch(
                "resource_registry.services.dataset_query_job_worker_runner."
                "WorkerExecutionAuthorityService"
            )
        )
        authority_service_class.return_value.build_query_context.side_effect = (
            self._build_execution_authority
        )

    @staticmethod
    def _build_execution_authority(*, job: object) -> SimpleNamespace:
        tenant_id = str(job.tenant_id)
        job_id = str(job.id)
        context = ReadAuthorizationContext(
            tenant_id=tenant_id,
            actor="user:1",
            roles=("reader",),
            allowed_dataset_ids=frozenset({str(job.dataset_id)}),
            metadata={"source": "delegated_job_authority", "job_id": job_id},
        )
        return SimpleNamespace(to_read_authorization_context=lambda: context)

    def test_run_once_executes_claimed_jobs(self) -> None:
        claimed_job = SimpleNamespace(
            id=str(uuid4()),
            tenant_id=str(uuid4()),
            dataset_id=str(uuid4()),
            status="running",
            error_code=None,
            execution_ms=12,
        )
        query_job_service = MagicMock()
        query_job_service.claim_queued_jobs.return_value = [claimed_job]
        execution_service = MagicMock()
        execution_service.execute_job.return_value = claimed_job
        runner = DatasetQueryJobWorkerRunner(
            query_job_service=query_job_service,
            execution_service=execution_service,
            batch_size=5,
            stale_running_seconds=0,
        )

        claimed = runner.run_once()

        self.assertEqual(claimed, 1)
        execution_service.execute_job.assert_called_once()

    def test_failed_execution_marks_job_failed(self) -> None:
        claimed_job = SimpleNamespace(
            id=str(uuid4()),
            tenant_id=str(uuid4()),
            dataset_id=str(uuid4()),
        )
        failed_job = SimpleNamespace(
            id=claimed_job.id,
            tenant_id=claimed_job.tenant_id,
            dataset_id=claimed_job.dataset_id,
            status="failed",
            error_code="query_execution_error",
            execution_ms=0,
        )
        query_job_service = MagicMock()
        query_job_service.claim_queued_jobs.return_value = [claimed_job]
        query_job_service.get_job.return_value = failed_job
        query_job_service.mark_failed.return_value = failed_job
        execution_service = MagicMock()
        execution_service.execute_job.side_effect = RuntimeError("boom")
        runner = DatasetQueryJobWorkerRunner(
            query_job_service=query_job_service,
            execution_service=execution_service,
            batch_size=1,
            stale_running_seconds=0,
        )

        _ = runner.run_once()

        query_job_service.mark_failed.assert_called_once()
        kwargs = query_job_service.mark_failed.call_args.kwargs
        self.assertEqual(kwargs["error_code"], "query_execution_error")
        self.assertTrue(kwargs["retryable"])

    def test_cancellation_requested_before_execution_skips_execution(self) -> None:
        claimed_job = SimpleNamespace(
            id=str(uuid4()),
            tenant_id=str(uuid4()),
            dataset_id=str(uuid4()),
            status="running",
            error_code=None,
            execution_ms=0,
        )
        cancelled_job = SimpleNamespace(
            id=claimed_job.id,
            tenant_id=claimed_job.tenant_id,
            dataset_id=claimed_job.dataset_id,
            status="cancelled",
            error_code=None,
            execution_ms=0,
        )
        query_job_service = MagicMock()
        query_job_service.claim_queued_jobs.return_value = [claimed_job]
        query_job_service.is_cancellation_requested.return_value = True
        query_job_service.mark_cancelled.return_value = cancelled_job
        execution_service = MagicMock()
        runner = DatasetQueryJobWorkerRunner(
            query_job_service=query_job_service,
            execution_service=execution_service,
            batch_size=1,
            stale_running_seconds=0,
        )

        _ = runner.run_once()

        execution_service.execute_job.assert_not_called()
        query_job_service.mark_cancelled.assert_called_once_with(job=claimed_job)

    def test_cancellation_requested_during_worker_failure_wins_over_retry(self) -> None:
        claimed_job = SimpleNamespace(
            id=str(uuid4()),
            tenant_id=str(uuid4()),
            dataset_id=str(uuid4()),
        )
        current_job = SimpleNamespace(
            id=claimed_job.id,
            tenant_id=claimed_job.tenant_id,
            dataset_id=claimed_job.dataset_id,
            status="running",
            error_code=None,
            execution_ms=0,
        )
        cancelled_job = SimpleNamespace(
            id=claimed_job.id,
            tenant_id=claimed_job.tenant_id,
            dataset_id=claimed_job.dataset_id,
            status="cancelled",
            error_code=None,
            execution_ms=0,
        )
        query_job_service = MagicMock()
        query_job_service.claim_queued_jobs.return_value = [claimed_job]
        query_job_service.get_job.return_value = current_job
        query_job_service.is_cancellation_requested.side_effect = [False, True, False]
        query_job_service.mark_cancelled.return_value = cancelled_job
        execution_service = MagicMock()
        execution_service.execute_job.side_effect = RuntimeError("boom")
        runner = DatasetQueryJobWorkerRunner(
            query_job_service=query_job_service,
            execution_service=execution_service,
            batch_size=1,
            stale_running_seconds=0,
        )

        _ = runner.run_once()

        query_job_service.mark_cancelled.assert_called_once_with(job=current_job)
        query_job_service.mark_failed.assert_not_called()

    def test_batch_size_limits_number_of_claimed_jobs(self) -> None:
        query_job_service = MagicMock()
        query_job_service.claim_queued_jobs.return_value = []
        runner = DatasetQueryJobWorkerRunner(
            query_job_service=query_job_service,
            execution_service=MagicMock(),
            batch_size=5,
            stale_running_seconds=0,
        )

        _ = runner.run_once(batch_size=1)

        query_job_service.claim_queued_jobs.assert_called_once_with(batch_size=1, tenant_id=None)

    def test_max_jobs_stops_worker_after_limit(self) -> None:
        job_one = SimpleNamespace(
            id=str(uuid4()),
            tenant_id=str(uuid4()),
            dataset_id=str(uuid4()),
            status="completed",
            error_code=None,
            execution_ms=3,
        )
        job_two = SimpleNamespace(
            id=str(uuid4()),
            tenant_id=str(uuid4()),
            dataset_id=str(uuid4()),
            status="completed",
            error_code=None,
            execution_ms=4,
        )
        query_job_service = MagicMock()
        query_job_service.claim_queued_jobs.side_effect = [[job_one], [job_two], [job_one]]
        execution_service = MagicMock()
        execution_service.execute_job.side_effect = [job_one, job_two]
        runner = DatasetQueryJobWorkerRunner(
            query_job_service=query_job_service,
            execution_service=execution_service,
            batch_size=2,
            stale_running_seconds=0,
        )

        stats = runner.run(max_jobs=2)

        self.assertEqual(stats.processed_jobs, 2)
        self.assertEqual(execution_service.execute_job.call_count, 2)

    def test_worker_leaves_when_no_queued_jobs(self) -> None:
        query_job_service = MagicMock()
        query_job_service.claim_queued_jobs.return_value = []
        execution_service = MagicMock()
        runner = DatasetQueryJobWorkerRunner(
            query_job_service=query_job_service,
            execution_service=execution_service,
            batch_size=10,
            stale_running_seconds=0,
        )

        claimed = runner.run_once()

        self.assertEqual(claimed, 0)
        execution_service.execute_job.assert_not_called()

    def test_worker_preserves_tenant_authorization_context(self) -> None:
        claimed_job = SimpleNamespace(
            id=str(uuid4()),
            tenant_id=str(uuid4()),
            dataset_id=str(uuid4()),
            status="completed",
            error_code=None,
            execution_ms=10,
        )
        query_job_service = MagicMock()
        query_job_service.claim_queued_jobs.return_value = [claimed_job]
        execution_service = MagicMock()
        execution_service.execute_job.return_value = claimed_job
        runner = DatasetQueryJobWorkerRunner(
            query_job_service=query_job_service,
            execution_service=execution_service,
            batch_size=1,
            stale_running_seconds=0,
        )

        _ = runner.run_once()

        context = execution_service.execute_job.call_args.kwargs["context"]
        self.assertEqual(str(context.tenant_id), str(claimed_job.tenant_id))
        self.assertEqual(context.roles, ("reader",))

    def test_runner_defaults_to_execution_service_class(self) -> None:
        runner = DatasetQueryJobWorkerRunner()
        self.assertIsInstance(runner._execution_service, DatasetQueryJobExecutionService)

    def test_runner_passes_execution_timeout_to_default_execution_service(self) -> None:
        query_job_service = MagicMock()
        with patch(
            "resource_registry.services.dataset_query_job_worker_runner."
            "DatasetQueryJobExecutionService",
        ) as execution_service_class:
            runner = DatasetQueryJobWorkerRunner(
                query_job_service=query_job_service,
                execution_timeout_seconds=7,
            )

        self.assertEqual(runner._execution_service, execution_service_class.return_value)
        execution_service_class.assert_called_once_with(
            query_job_service=query_job_service,
            execution_timeout_seconds=7,
        )

    def test_worker_logs_do_not_include_rows_or_secrets(self) -> None:
        claimed_job = SimpleNamespace(
            id=str(uuid4()),
            tenant_id=str(uuid4()),
            dataset_id=str(uuid4()),
            status="completed",
            error_code=None,
            execution_ms=9,
        )
        query_job_service = MagicMock()
        query_job_service.claim_queued_jobs.return_value = [claimed_job]
        execution_service = MagicMock()
        execution_service.execute_job.return_value = claimed_job
        runner = DatasetQueryJobWorkerRunner(
            query_job_service=query_job_service,
            execution_service=execution_service,
            batch_size=1,
            stale_running_seconds=0,
        )

        with patch(
            "resource_registry.services.dataset_query_job_worker_runner._logger.info",
        ) as info_mock:
            _ = runner.run_once()

        logged_extra = info_mock.call_args.kwargs["extra"]
        serialized = json.dumps(logged_extra).lower()
        self.assertNotIn("rows", serialized)
        self.assertNotIn("token", serialized)
        self.assertNotIn("secret", serialized)

    def test_get_query_job_status_and_result_payloads(self) -> None:
        tenant_id = str(uuid4())
        dataset_id = str(uuid4())
        query_job_id = str(uuid4())
        completed_job = SimpleNamespace(
            id=query_job_id,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            status="completed",
            created_at=SimpleNamespace(isoformat=lambda: "2026-01-01T00:00:00"),
            started_at=SimpleNamespace(isoformat=lambda: "2026-01-01T00:00:01"),
            completed_at=SimpleNamespace(isoformat=lambda: "2026-01-01T00:00:02"),
            error_code=None,
            result_columns=["country"],
            result_rows=[["CR"]],
            row_count=1,
            truncated=False,
        )
        request = SimpleNamespace(user=None, session={}, tenant_id=tenant_id)

        with (
            patch("BotServer.builtin_functions.resource_registry_functions._assert_capability"),
            patch(
                "BotServer.builtin_functions.resource_registry_functions._derive_roles",
                return_value=("admin",),
            ),
            patch(
                "BotServer.builtin_functions.resource_registry_functions._resolve_read_only_tenant_id",
                return_value=tenant_id,
            ),
            patch(
                "BotServer.builtin_functions.resource_registry_functions._get_read_facade",
            ) as read_facade_mock,
            patch(
                "BotServer.builtin_functions.resource_registry_functions._get_query_job_service",
            ) as query_job_service_mock,
        ):
            read_facade_mock.return_value.get_dataset.return_value = SimpleNamespace(id=dataset_id)
            query_job_service_mock.return_value.get_job.return_value = completed_job
            status_payload = json.loads(
                resource_registry_get_query_job_status(request, None, tenant_id, query_job_id),
            )
            result_payload = json.loads(
                resource_registry_get_query_job_result(request, None, tenant_id, query_job_id),
            )

        self.assertEqual(status_payload["status"], 200)
        self.assertEqual(status_payload["job_status"], "completed")
        self.assertEqual(result_payload["status"], 200)
        self.assertEqual(result_payload["job_status"], "completed")
        self.assertEqual(result_payload["row_count"], 1)
        self.assertEqual(result_payload["rows"], [["CR"]])

    def test_normal_user_cannot_read_another_users_query_job(self) -> None:
        tenant_id = str(uuid4())
        query_job_id = str(uuid4())
        query_job = SimpleNamespace(dataset_id=uuid4(), created_by_id=2)
        request = SimpleNamespace(user=SimpleNamespace(pk=1), session={})
        with (
            patch(
                "BotServer.builtin_functions.resource_registry_functions._resolve_read_only_tenant_id",
                return_value=tenant_id,
            ),
            patch(
                "BotServer.builtin_functions.resource_registry_functions._derive_roles",
                return_value=("restricted_reader",),
            ),
            patch(
                "BotServer.builtin_functions.resource_registry_functions._get_query_job_service",
            ) as query_job_service_mock,
        ):
            query_job_service_mock.return_value.get_job.return_value = query_job
            status_payload = json.loads(
                resource_registry_get_query_job_status(request, None, tenant_id, query_job_id),
            )
            result_payload = json.loads(
                resource_registry_get_query_job_result(request, None, tenant_id, query_job_id),
            )

        self.assertEqual(status_payload["status"], 403)
        self.assertEqual(result_payload["status"], 403)
        self.assertEqual(status_payload["error"], "Query job not found.")

    def test_completed_result_is_denied_after_schema_access_is_revoked(self) -> None:
        tenant_id = str(uuid4())
        query_job_id = str(uuid4())
        dataset_id = uuid4()
        query_job = SimpleNamespace(
            dataset_id=dataset_id,
            version_id=None,
            created_by_id=1,
            sql_text="SELECT sensitive FROM dataset LIMIT 1",
        )
        request = SimpleNamespace(user=SimpleNamespace(pk=1), session={})
        with (
            patch(
                "BotServer.builtin_functions.resource_registry_functions._resolve_read_only_tenant_id",
                return_value=tenant_id,
            ),
            patch(
                "BotServer.builtin_functions.resource_registry_functions._derive_roles",
                return_value=("restricted_reader",),
            ),
            patch("BotServer.builtin_functions.resource_registry_functions._assert_capability"),
            patch(
                "BotServer.builtin_functions.resource_registry_functions._get_query_job_service",
            ) as query_job_service_class,
            patch(
                "BotServer.builtin_functions.resource_registry_functions._get_read_facade",
            ),
            patch(
                "BotServer.builtin_functions.resource_registry_functions._validate_sql_for_query_job",
                return_value={"code": "invalid_column"},
            ),
        ):
            query_job_service_class.return_value.get_job.return_value = query_job
            payload = json.loads(
                resource_registry_get_query_job_result(request, None, tenant_id, query_job_id),
            )

        self.assertEqual(payload["status"], 403)
        self.assertEqual(payload["error"], "Query job result is no longer authorized.")


class RunDatasetQueryJobsCommandTests(SimpleTestCase):
    def test_command_once_executes_runner(self) -> None:
        mocked_runner = MagicMock()
        mocked_runner.run.return_value = SimpleNamespace(claimed_jobs=1, processed_jobs=1)
        stdout = StringIO()
        with patch(
            "resource_registry.management.commands.run_dataset_query_jobs.DatasetQueryJobWorkerRunner",
            return_value=mocked_runner,
        ):
            call_command("run_dataset_query_jobs", "--once", stdout=stdout)

        mocked_runner.run.assert_called_once()
        kwargs = mocked_runner.run.call_args.kwargs
        self.assertTrue(kwargs["once"])
        self.assertIn("claimed=1 processed=1", stdout.getvalue())

    def test_command_passes_max_jobs_and_batch_configuration(self) -> None:
        mocked_runner = MagicMock()
        mocked_runner.run.return_value = SimpleNamespace(claimed_jobs=2, processed_jobs=2)
        with patch(
            "resource_registry.management.commands.run_dataset_query_jobs.DatasetQueryJobWorkerRunner",
            return_value=mocked_runner,
        ):
            call_command(
                "run_dataset_query_jobs",
                "--once",
                "--batch-size",
                "4",
                "--max-jobs",
                "2",
                "--tenant-id",
                str(uuid4()),
                "--poll-interval-seconds",
                "0.2",
            )

        kwargs = mocked_runner.run.call_args.kwargs
        self.assertEqual(kwargs["max_jobs"], 2)
        self.assertTrue(kwargs["once"])
