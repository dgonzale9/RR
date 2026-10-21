from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from resource_registry.models import (
    DatasetIntelligenceJob,
    DatasetIntelligenceJobOperation,
    DatasetIntelligenceJobStatus,
)
from resource_registry.read_auth.context import ReadAuthorizationContext
from resource_registry.services.dataset_intelligence_job_execution_service import (
    DatasetIntelligenceJobExecutionService,
    execute_dataset_intelligence_job,
)
from resource_registry.services.dataset_intelligence_job_service import DatasetIntelligenceJobService
from resource_registry.services.dataset_intelligence_job_worker_runner import (
    DatasetIntelligenceJobWorkerRunner,
)


class IntelligenceJobAsyncDurabilityVerificationTests(SimpleTestCase):
    """Verify current async durability behavior for dataset intelligence jobs."""

    def test_job_creation_lifecycle_persists_queued_status(self) -> None:
        fake_job = MagicMock()

        with patch(
            "resource_registry.services.dataset_intelligence_job_service."
            "DatasetIntelligenceJob.objects.create",
            return_value=fake_job,
        ) as create_mock, patch(
            "resource_registry.services.dataset_intelligence_job_service."
            "DatasetIntelligenceJobService._find_active_job_by_fingerprint",
            return_value=None,
        ), patch("resource_registry.services.dataset_intelligence_job_service.transaction.atomic"):
            created_job = DatasetIntelligenceJobService().create_job(
                tenant_id="tenant-1",
                operation=DatasetIntelligenceJobOperation.TRANSFORMATION,
                request_payload={"job_spec": {"version": "1.0"}},
                job_spec={"version": "1.0"},
            )

        self.assertEqual(created_job, fake_job)
        self.assertEqual(
            create_mock.call_args.kwargs["status"],
            DatasetIntelligenceJobStatus.QUEUED,
        )
        self.assertEqual(create_mock.call_args.kwargs["tenant_id"], "tenant-1")

    def test_worker_entrypoint_and_claiming_contract_are_exposed(self) -> None:
        entrypoint_source = inspect.getsource(execute_dataset_intelligence_job)
        claim_source = inspect.getsource(DatasetIntelligenceJobService.claim_queued_jobs)
        runner_source = inspect.getsource(DatasetIntelligenceJobWorkerRunner.run_once)

        self.assertIn("DatasetIntelligenceJobExecutionService", entrypoint_source)
        self.assertTrue(hasattr(DatasetIntelligenceJobService, "claim_queued_jobs"))
        self.assertIn("select_for_update(skip_locked=True)", claim_source)
        self.assertIn("DatasetIntelligenceJobStatus.QUEUED", claim_source)
        self.assertIn("DatasetIntelligenceJobStatus.RUNNING", claim_source)
        self.assertIn("claim_queued_jobs", runner_source)
        self.assertTrue(hasattr(DatasetIntelligenceJobService, "mark_cancelled"))
        self.assertTrue(hasattr(DatasetIntelligenceJobService, "request_cancellation"))
        self.assertTrue(hasattr(DatasetIntelligenceJobService, "is_cancellation_requested"))

    def test_execution_failure_marks_terminal_failed_status(self) -> None:
        job = MagicMock()
        job.id = "ij-1"
        job.operation = DatasetIntelligenceJobOperation.TRANSFORMATION
        job.dataset_id = None
        job.version_id = None
        job.export_plan_id = None
        job.request_payload_json = {}
        job.job_spec_json = {"version": "1.0"}
        transformation_service = MagicMock()
        transformation_service.execute.side_effect = RuntimeError("boom")
        job_service = MagicMock()
        job_service.get_job.return_value = job
        job_service.mark_running.return_value = job
        failed_job = MagicMock()
        failed_job.status = DatasetIntelligenceJobStatus.FAILED
        failed_job.error_code = "intelligence_execution_error"
        failed_job.completed_at = object()
        job_service.mark_failed.return_value = failed_job
        execution_service = DatasetIntelligenceJobExecutionService(
            job_service=job_service,
            transformation_service=transformation_service,
        )

        executed_job = execution_service.execute_job(
            context=ReadAuthorizationContext(tenant_id="tenant-1"),
            intelligence_job_id="ij-1",
        )

        self.assertEqual(executed_job.status, DatasetIntelligenceJobStatus.FAILED)
        self.assertEqual(executed_job.error_code, "intelligence_execution_error")
        self.assertIsNotNone(executed_job.completed_at)

    def test_retry_behavior_has_attempt_tracking_field(self) -> None:
        field_names = {field.name for field in DatasetIntelligenceJob._meta.get_fields()}

        self.assertIn("retry_count", field_names)
        self.assertNotIn("attempt_count", field_names)

    def test_cancellation_contract_is_exposed_by_intelligence_job_service(self) -> None:
        service_source = inspect.getsource(DatasetIntelligenceJobService)
        runner_source = inspect.getsource(DatasetIntelligenceJobWorkerRunner._execute_claimed_job)
        recovery_source = inspect.getsource(
            DatasetIntelligenceJobService.recover_stale_running_jobs,
        )
        field_names = {field.name for field in DatasetIntelligenceJob._meta.get_fields()}

        self.assertIn("cancellation_requested_at", field_names)
        self.assertIn("is_cancellation_requested", runner_source)
        self.assertIn("mark_cancelled", runner_source)
        self.assertNotIn("cancellation_requested_at", runner_source)
        self.assertIn("DatasetIntelligenceJobStatus.CANCELLED", service_source)
        self.assertIn("DatasetIntelligenceJobStatus.CANCELLED", recovery_source)
        self.assertIn("cancellation_requested_at", recovery_source)

    def test_timeout_behavior_is_exposed_by_intelligence_execution_service(self) -> None:
        init_signature = inspect.signature(DatasetIntelligenceJobExecutionService.__init__)
        execute_source = inspect.getsource(DatasetIntelligenceJobExecutionService.execute_job)
        field_names = {field.name for field in DatasetIntelligenceJob._meta.get_fields()}

        self.assertIn("execution_timeout_seconds", init_signature.parameters)
        self.assertIn("execution_timeout", execute_source)
        self.assertIn("mark_failed", execute_source)
        self.assertIn("retryable=True", execute_source)
        self.assertNotIn("timeout_seconds", field_names)
        self.assertNotIn("timed_out_at", field_names)
        self.assertIn("time.perf_counter", execute_source)

    def test_job_creation_is_idempotent_for_duplicate_active_payloads(self) -> None:
        source = inspect.getsource(DatasetIntelligenceJobService.create_job)
        constraint_names = {
            constraint.name for constraint in DatasetIntelligenceJob._meta.constraints
        }
        field_names = {field.name for field in DatasetIntelligenceJob._meta.get_fields()}

        self.assertIn("DatasetIntelligenceJob.objects.create", source)
        self.assertIn("transaction.atomic", source)
        self.assertIn("IntegrityError", source)
        self.assertTrue(hasattr(DatasetIntelligenceJobService, "_find_active_job_by_fingerprint"))
        self.assertIn("idempotency_key", field_names)
        self.assertIn("request_fingerprint", field_names)
        self.assertIn("dij_active_fingerprint_uniq", constraint_names)
