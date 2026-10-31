from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from resource_registry.models import DatasetQueryJobStatus
from resource_registry.services.dataset_query_job_service import DatasetQueryJobService


class DatasetQueryJobServiceTests(SimpleTestCase):
    def test_create_job_persists_queued_job_with_sql_hash(self) -> None:
        service = DatasetQueryJobService()
        fake_created = SimpleNamespace(id="job-1")
        with (
            patch(
                "resource_registry.services.dataset_query_job_service.DatasetQueryJob.objects.create",
                return_value=fake_created,
            ) as create_mock,
            patch.object(
                service,
                "_find_active_job_by_fingerprint",
                return_value=None,
            ),
            patch("resource_registry.services.dataset_query_job_service.transaction.atomic"),
            patch(
                "resource_registry.services.dataset_query_job_service.pin_job_dataset_version",
                return_value=("version-1", None),
            ),
        ):
            result = service.create_job(
                tenant_id="tenant-1",
                dataset_id="dataset-1",
                version_id="version-1",
                sql_text="SELECT country FROM dataset LIMIT 1",
                created_by=SimpleNamespace(pk=1),
            )

        self.assertEqual(result, fake_created)
        kwargs = create_mock.call_args.kwargs
        self.assertEqual(kwargs["status"], DatasetQueryJobStatus.QUEUED)
        self.assertEqual(kwargs["tenant_id"], "tenant-1")
        self.assertEqual(kwargs["dataset_id"], "dataset-1")
        self.assertEqual(kwargs["version_id"], "version-1")
        self.assertEqual(len(kwargs["sql_hash"]), 64)
        self.assertEqual(len(kwargs["request_fingerprint"]), 64)
        self.assertTrue(str(kwargs["idempotency_key"]).startswith("dataset_query_job:"))

    def test_create_job_returns_existing_active_job_for_duplicate_request(self) -> None:
        service = DatasetQueryJobService()
        existing_job = SimpleNamespace(id="job-1", status=DatasetQueryJobStatus.QUEUED)
        with (
            patch.object(
                service,
                "_find_active_job_by_fingerprint",
                return_value=existing_job,
            ),
            patch(
                "resource_registry.services.dataset_query_job_service.DatasetQueryJob.objects.create",
            ) as create_mock,
            patch(
                "resource_registry.services.dataset_query_job_service.transaction.atomic",
            ),
            patch(
                "resource_registry.services.dataset_query_job_service.pin_job_dataset_version",
                return_value=("version-1", None),
            ),
        ):
            result = service.create_job(
                tenant_id="tenant-1",
                dataset_id="dataset-1",
                version_id=None,
                sql_text="SELECT country FROM dataset LIMIT 1",
            )

        self.assertEqual(result, existing_job)
        create_mock.assert_not_called()

    def test_terminal_query_jobs_do_not_block_new_creation(self) -> None:
        self.assertSetEqual(
            DatasetQueryJobService._active_statuses,
            {DatasetQueryJobStatus.QUEUED, DatasetQueryJobStatus.RUNNING},
        )

    def test_mark_running_updates_status_and_started_at(self) -> None:
        service = DatasetQueryJobService()
        job = SimpleNamespace(
            status=DatasetQueryJobStatus.QUEUED,
            started_at=None,
            save=lambda **kwargs: None,
        )
        save_calls: list[dict] = []
        job.save = lambda **kwargs: save_calls.append(kwargs)

        updated = service.mark_running(job=job)

        self.assertEqual(updated.status, DatasetQueryJobStatus.RUNNING)
        self.assertIsNotNone(updated.started_at)
        self.assertEqual(save_calls[0]["update_fields"], ["status", "started_at"])

    def test_mark_completed_persists_sanitized_result(self) -> None:
        service = DatasetQueryJobService()
        job = SimpleNamespace(
            status=DatasetQueryJobStatus.RUNNING,
            result_columns=None,
            result_rows=None,
            row_count=None,
            truncated=False,
            error_code="old",
            error_message="old",
            execution_ms=None,
            completed_at=None,
        )
        save_calls: list[dict] = []
        job.save = lambda **kwargs: save_calls.append(kwargs)

        result = service.mark_completed(
            job=job,
            result={
                "columns": ["country"],
                "rows": [["CR"]],
                "row_count": 1,
                "truncated": False,
                "token": "secret",
            },
            execution_ms=123,
        )

        self.assertEqual(result.status, DatasetQueryJobStatus.COMPLETED)
        self.assertEqual(result.result_columns, ["country"])
        self.assertEqual(result.result_rows, [["CR"]])
        self.assertEqual(result.row_count, 1)
        self.assertFalse(result.truncated)
        self.assertIsNone(result.error_code)
        self.assertIsNone(result.error_message)
        self.assertEqual(result.execution_ms, 123)
        self.assertIsNotNone(result.completed_at)
        self.assertIn("result_columns", save_calls[0]["update_fields"])

    def test_mark_failed_persists_error_fields(self) -> None:
        service = DatasetQueryJobService()
        job = SimpleNamespace(
            status=DatasetQueryJobStatus.RUNNING,
            error_code=None,
            error_message=None,
            execution_ms=None,
            completed_at=None,
            retry_count=0,
        )
        save_calls: list[dict] = []
        job.save = lambda **kwargs: save_calls.append(kwargs)

        result = service.mark_failed(
            job=job,
            error_code="query_timeout",
            error_message="Query execution exceeded timeout.",
            execution_ms=500,
        )

        self.assertEqual(result.status, DatasetQueryJobStatus.FAILED)
        self.assertEqual(result.error_code, "query_timeout")
        self.assertEqual(result.error_message, "Query execution exceeded timeout.")
        self.assertEqual(result.execution_ms, 500)
        self.assertIsNotNone(result.completed_at)
        self.assertIn("error_code", save_calls[0]["update_fields"])

    def test_mark_failed_requeues_retryable_failure_before_retry_limit(self) -> None:
        service = DatasetQueryJobService(max_retries=2)
        job = SimpleNamespace(
            status=DatasetQueryJobStatus.RUNNING,
            retry_count=1,
            started_at=object(),
            completed_at=object(),
            error_code=None,
            error_message=None,
            execution_ms=None,
        )
        save_calls: list[dict] = []
        job.save = lambda **kwargs: save_calls.append(kwargs)

        result = service.mark_failed(
            job=job,
            error_code="query_execution_error",
            error_message="temporary failure",
            retryable=True,
        )

        self.assertEqual(result.status, DatasetQueryJobStatus.QUEUED)
        self.assertEqual(result.retry_count, 2)
        self.assertIsNone(result.started_at)
        self.assertIsNone(result.completed_at)
        self.assertIn("retry_count", save_calls[0]["update_fields"])

    def test_mark_failed_terminal_after_retry_limit(self) -> None:
        service = DatasetQueryJobService(max_retries=1)
        job = SimpleNamespace(
            status=DatasetQueryJobStatus.RUNNING,
            retry_count=1,
            started_at=object(),
            completed_at=None,
            error_code=None,
            error_message=None,
            execution_ms=None,
        )
        job.save = lambda **kwargs: None

        result = service.mark_failed(
            job=job,
            error_code="query_execution_error",
            error_message="temporary failure",
            retryable=True,
        )

        self.assertEqual(result.status, DatasetQueryJobStatus.FAILED)
        self.assertEqual(result.retry_count, 1)
        self.assertIsNotNone(result.completed_at)

    def test_request_cancellation_is_tenant_scoped_and_status_guarded(self) -> None:
        source = inspect.getsource(DatasetQueryJobService.request_cancellation)
        mark_completed_source = inspect.getsource(DatasetQueryJobService.mark_completed)
        mark_failed_source = inspect.getsource(DatasetQueryJobService.mark_failed)

        self.assertIn("transaction.atomic", source)
        self.assertIn("select_for_update", source)
        self.assertIn("tenant_id=tenant_id", source)
        self.assertIn("DatasetQueryJobStatus.QUEUED", source)
        self.assertIn("DatasetQueryJobStatus.CANCELLED", source)
        self.assertIn("transaction.atomic", mark_completed_source)
        self.assertIn("select_for_update", mark_completed_source)
        self.assertIn("transaction.atomic", mark_failed_source)
        self.assertIn("select_for_update", mark_failed_source)

    def test_mark_cancelled_transitions_running_job_without_retry_increment(self) -> None:
        service = DatasetQueryJobService()
        job = SimpleNamespace(
            status=DatasetQueryJobStatus.RUNNING,
            cancellation_requested_at=None,
            completed_at=None,
            retry_count=2,
        )
        save_calls: list[dict] = []
        job.save = lambda **kwargs: save_calls.append(kwargs)

        result = service.mark_cancelled(job=job)

        self.assertEqual(result.status, DatasetQueryJobStatus.CANCELLED)
        self.assertIsNotNone(result.cancellation_requested_at)
        self.assertIsNotNone(result.completed_at)
        self.assertEqual(result.retry_count, 2)
        self.assertIn("cancellation_requested_at", save_calls[0]["update_fields"])

    def test_repeated_mark_cancelled_preserves_terminal_job(self) -> None:
        service = DatasetQueryJobService()
        first_requested_at = object()
        job = SimpleNamespace(
            status=DatasetQueryJobStatus.CANCELLED,
            cancellation_requested_at=first_requested_at,
            completed_at=object(),
        )
        job.save = lambda **kwargs: self.fail("terminal cancellation should not save")

        result = service.mark_cancelled(job=job)

        self.assertEqual(result.status, DatasetQueryJobStatus.CANCELLED)
        self.assertIs(result.cancellation_requested_at, first_requested_at)

    def test_terminal_jobs_are_not_overwritten_by_completion_or_failure(self) -> None:
        service = DatasetQueryJobService()
        completed_job = SimpleNamespace(status=DatasetQueryJobStatus.COMPLETED)
        failed_job = SimpleNamespace(status=DatasetQueryJobStatus.FAILED)
        completed_job.save = lambda **kwargs: self.fail("completed job should not save")
        failed_job.save = lambda **kwargs: self.fail("failed job should not save")

        completed_result = service.mark_completed(job=completed_job, result={})
        failed_result = service.mark_failed(
            job=failed_job,
            error_code="query_execution_error",
            error_message="temporary failure",
            retryable=True,
        )

        self.assertEqual(completed_result.status, DatasetQueryJobStatus.COMPLETED)
        self.assertEqual(failed_result.status, DatasetQueryJobStatus.FAILED)

    def test_mark_failed_cancels_when_cancellation_is_requested_before_retry(self) -> None:
        service = DatasetQueryJobService(max_retries=3)
        job = SimpleNamespace(
            status=DatasetQueryJobStatus.RUNNING,
            retry_count=1,
            cancellation_requested_at=object(),
            completed_at=None,
            error_code=None,
            error_message=None,
            execution_ms=None,
        )
        job.save = lambda **kwargs: None

        result = service.mark_failed(
            job=job,
            error_code="query_execution_error",
            error_message="temporary failure",
            retryable=True,
        )

        self.assertEqual(result.status, DatasetQueryJobStatus.CANCELLED)
        self.assertEqual(result.retry_count, 1)

    def test_mark_failed_sanitizes_sensitive_error_content(self) -> None:
        service = DatasetQueryJobService()
        job = SimpleNamespace(
            status=DatasetQueryJobStatus.RUNNING,
            error_code=None,
            error_message=None,
            execution_ms=None,
            completed_at=None,
            retry_count=0,
        )
        job.save = lambda **kwargs: None

        result = service.mark_failed(
            job=job,
            error_code="query_execution_error",
            error_message="failed with token=abc and secret payload",
        )

        self.assertNotIn("token", str(result.error_message).lower())
        self.assertNotIn("secret", str(result.error_message).lower())

    def test_get_job_is_tenant_scoped(self) -> None:
        service = DatasetQueryJobService()
        fake_job = SimpleNamespace(id="job-1")
        with patch(
            "resource_registry.services.dataset_query_job_service.DatasetQueryJob.objects.get",
            return_value=fake_job,
        ) as get_mock:
            result = service.get_job(tenant_id="tenant-1", query_job_id="job-1")

        self.assertEqual(result, fake_job)
        get_mock.assert_called_once_with(id="job-1", tenant_id="tenant-1")
