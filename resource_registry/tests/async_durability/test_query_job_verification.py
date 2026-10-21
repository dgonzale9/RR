from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from resource_registry.models import (
    DatasetQueryJob,
    DatasetQueryJobStatus,
)
from resource_registry.services.dataset_query_job_execution_service import (
    DatasetQueryJobExecutionService,
)
from resource_registry.services.dataset_query_job_service import DatasetQueryJobService
from resource_registry.services.dataset_query_job_worker_runner import DatasetQueryJobWorkerRunner


class QueryJobAsyncDurabilityVerificationTests(SimpleTestCase):
    """Verify current async durability behavior for dataset query jobs."""

    def test_job_creation_lifecycle_persists_queued_status(self) -> None:
        fake_job = SimpleNamespace(id="job-1")

        with patch(
            "resource_registry.services.dataset_query_job_service.DatasetQueryJob.objects.create",
            return_value=fake_job,
        ) as create_mock, patch(
            "resource_registry.services.dataset_query_job_service."
            "DatasetQueryJobService._find_active_job_by_fingerprint",
            return_value=None,
        ), patch("resource_registry.services.dataset_query_job_service.transaction.atomic"):
            created_job = DatasetQueryJobService().create_job(
                tenant_id="tenant-1",
                dataset_id="dataset-1",
                version_id=None,
                sql_text="SELECT 1",
            )

        self.assertEqual(created_job, fake_job)
        self.assertEqual(create_mock.call_args.kwargs["status"], DatasetQueryJobStatus.QUEUED)
        self.assertEqual(create_mock.call_args.kwargs["tenant_id"], "tenant-1")
        self.assertEqual(create_mock.call_args.kwargs["dataset_id"], "dataset-1")

    def test_claiming_atomicity_has_row_lock_source_evidence(self) -> None:
        source = inspect.getsource(DatasetQueryJobService.claim_queued_jobs)

        self.assertIn("transaction.atomic", source)
        self.assertIn("select_for_update", source)
        self.assertIn("skip_locked=True", source)
        self.assertIn("status=DatasetQueryJobStatus.QUEUED", source)

    def test_claimed_jobs_are_filtered_out_of_future_claims_by_source_contract(self) -> None:
        source = inspect.getsource(DatasetQueryJobService.claim_queued_jobs)

        self.assertIn("filter(status=DatasetQueryJobStatus.QUEUED)", source)
        self.assertIn("status=DatasetQueryJobStatus.RUNNING", source)
        self.assertIn("status=DatasetQueryJobStatus.QUEUED", source)

    def test_restart_recovery_requeues_or_fails_stale_running_jobs(self) -> None:
        source = inspect.getsource(DatasetQueryJobService.recover_stale_running_jobs)
        runner_source = inspect.getsource(DatasetQueryJobWorkerRunner._recover_stale_running_jobs)

        self.assertIn("status=DatasetQueryJobStatus.RUNNING", source)
        self.assertIn("DatasetQueryJobStatus.QUEUED", source)
        self.assertIn("DatasetQueryJobStatus.FAILED", source)
        self.assertIn("DatasetQueryJobStatus.CANCELLED", source)
        self.assertIn("retry_count", source)
        self.assertIn("cancellation_requested_at", source)
        self.assertIn("recover_stale_running_jobs", runner_source)

    def test_retry_behavior_tracks_attempts_and_requeues_retryable_failure(self) -> None:
        field_names = {field.name for field in DatasetQueryJob._meta.get_fields()}
        job = SimpleNamespace(
            status=DatasetQueryJobStatus.RUNNING,
            error_code=None,
            error_message=None,
            execution_ms=None,
            started_at=object(),
            completed_at=None,
            retry_count=0,
        )
        job.save = lambda **_kwargs: None

        retried_job = DatasetQueryJobService(max_retries=1).mark_failed(
            job=job,
            error_code="query_execution_error",
            error_message="Unable to execute query job.",
            retryable=True,
        )

        self.assertIn("retry_count", field_names)
        self.assertEqual(retried_job.status, DatasetQueryJobStatus.QUEUED)
        self.assertEqual(retried_job.retry_count, 1)
        self.assertIsNone(retried_job.started_at)
        self.assertIsNone(retried_job.completed_at)

    def test_cancellation_contract_is_exposed_by_query_job_service(self) -> None:
        service_source = inspect.getsource(DatasetQueryJobService)
        runner_source = inspect.getsource(DatasetQueryJobWorkerRunner._execute_claimed_job)
        field_names = {field.name for field in DatasetQueryJob._meta.get_fields()}

        self.assertTrue(hasattr(DatasetQueryJobService, "mark_cancelled"))
        self.assertTrue(hasattr(DatasetQueryJobService, "request_cancellation"))
        self.assertTrue(hasattr(DatasetQueryJobService, "is_cancellation_requested"))
        self.assertIn(DatasetQueryJobStatus.CANCELLED, DatasetQueryJobStatus.values)
        self.assertIn("cancellation_requested_at", field_names)
        self.assertIn("is_cancellation_requested", runner_source)
        self.assertIn("mark_cancelled", runner_source)
        self.assertNotIn("cancellation_requested_at", runner_source)
        self.assertIn("DatasetQueryJobStatus.CANCELLED", service_source)

    def test_timeout_behavior_is_exposed_by_query_execution_service(self) -> None:
        init_signature = inspect.signature(DatasetQueryJobExecutionService.__init__)
        execute_source = inspect.getsource(DatasetQueryJobExecutionService.execute_job)
        field_names = {field.name for field in DatasetQueryJob._meta.get_fields()}

        self.assertIn("execution_timeout_seconds", init_signature.parameters)
        self.assertIn("execution_timeout", execute_source)
        self.assertIn("mark_failed", execute_source)
        self.assertIn("retryable=True", execute_source)
        self.assertNotIn("timeout_seconds", field_names)
        self.assertNotIn("timed_out_at", field_names)
        self.assertIn("time.perf_counter", execute_source)

    def test_job_creation_is_idempotent_for_duplicate_active_payloads(self) -> None:
        source = inspect.getsource(DatasetQueryJobService.create_job)
        constraint_names = {constraint.name for constraint in DatasetQueryJob._meta.constraints}
        field_names = {field.name for field in DatasetQueryJob._meta.get_fields()}

        self.assertIn("DatasetQueryJob.objects.create", source)
        self.assertIn("transaction.atomic", source)
        self.assertIn("IntegrityError", source)
        self.assertTrue(hasattr(DatasetQueryJobService, "_find_active_job_by_fingerprint"))
        self.assertIn("idempotency_key", field_names)
        self.assertIn("request_fingerprint", field_names)
        self.assertIn("dqj_active_fingerprint_uniq", constraint_names)
