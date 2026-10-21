from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from resource_registry.models import DatasetIntelligenceJobOperation, DatasetIntelligenceJobStatus
from resource_registry.services.dataset_intelligence_job_service import DatasetIntelligenceJobService


class DatasetIntelligenceJobServiceTests(SimpleTestCase):
    def test_create_job_persists_queued_job(self) -> None:
        service = DatasetIntelligenceJobService()
        fake_created = SimpleNamespace(id="ij-1")
        with patch(
            "resource_registry.services.dataset_intelligence_job_service."
            "DatasetIntelligenceJob.objects.create",
            return_value=fake_created,
        ) as create_mock, patch.object(
            service,
            "_find_active_job_by_fingerprint",
            return_value=None,
        ), patch("resource_registry.services.dataset_intelligence_job_service.transaction.atomic"):
            result = service.create_job(
                tenant_id="tenant-1",
                operation=DatasetIntelligenceJobOperation.ANALYSIS,
                request_payload={"dataset_id": "dataset-1", "analysis_type": "summary"},
                dataset_id="dataset-1",
                version_id="version-1",
                created_by=SimpleNamespace(pk=1),
            )

        self.assertEqual(result, fake_created)
        kwargs = create_mock.call_args.kwargs
        self.assertEqual(kwargs["status"], DatasetIntelligenceJobStatus.QUEUED)
        self.assertEqual(kwargs["operation"], DatasetIntelligenceJobOperation.ANALYSIS)
        self.assertEqual(kwargs["dataset_id"], "dataset-1")
        self.assertEqual(kwargs["version_id"], "version-1")
        self.assertEqual(kwargs["job_spec_json"], {})
        self.assertEqual(len(kwargs["request_fingerprint"]), 64)
        self.assertTrue(str(kwargs["idempotency_key"]).startswith("dataset_intelligence_job:"))

    def test_create_job_persists_job_spec_json(self) -> None:
        service = DatasetIntelligenceJobService()
        fake_created = SimpleNamespace(id="ij-2")
        with patch(
            "resource_registry.services.dataset_intelligence_job_service."
            "DatasetIntelligenceJob.objects.create",
            return_value=fake_created,
        ) as create_mock, patch.object(
            service,
            "_find_active_job_by_fingerprint",
            return_value=None,
        ), patch("resource_registry.services.dataset_intelligence_job_service.transaction.atomic"):
            _ = service.create_job(
                tenant_id="tenant-1",
                operation=DatasetIntelligenceJobOperation.TRANSFORMATION,
                request_payload={"job_spec": {"version": "1.0"}},
                job_spec={"job_type": "tabular_transformation", "version": "1.0"},
            )

        kwargs = create_mock.call_args.kwargs
        self.assertEqual(kwargs["operation"], DatasetIntelligenceJobOperation.TRANSFORMATION)
        self.assertEqual(kwargs["job_spec_json"]["version"], "1.0")

    def test_create_job_returns_existing_active_job_for_duplicate_request(self) -> None:
        service = DatasetIntelligenceJobService()
        existing_job = SimpleNamespace(id="ij-1", status=DatasetIntelligenceJobStatus.RUNNING)
        with patch.object(
            service,
            "_find_active_job_by_fingerprint",
            return_value=existing_job,
        ), patch(
            "resource_registry.services.dataset_intelligence_job_service."
            "DatasetIntelligenceJob.objects.create",
        ) as create_mock, patch(
            "resource_registry.services.dataset_intelligence_job_service.transaction.atomic",
        ):
            result = service.create_job(
                tenant_id="tenant-1",
                operation=DatasetIntelligenceJobOperation.ANALYSIS,
                request_payload={"dataset_id": "dataset-1", "analysis_type": "summary"},
                dataset_id="dataset-1",
            )

        self.assertEqual(result, existing_job)
        create_mock.assert_not_called()

    def test_terminal_intelligence_jobs_do_not_block_new_creation(self) -> None:
        self.assertSetEqual(
            DatasetIntelligenceJobService._active_statuses,
            {DatasetIntelligenceJobStatus.QUEUED, DatasetIntelligenceJobStatus.RUNNING},
        )

    def test_create_job_rejects_unsupported_operation(self) -> None:
        service = DatasetIntelligenceJobService()
        with self.assertRaises(ValueError):
            service.create_job(
                tenant_id="tenant-1",
                operation="unknown",
                request_payload={"dataset_id": "dataset-1"},
            )

    def test_mark_running_updates_status_and_started_at(self) -> None:
        service = DatasetIntelligenceJobService()
        job = SimpleNamespace(
            status=DatasetIntelligenceJobStatus.QUEUED,
            started_at=None,
            save=lambda **kwargs: None,
        )
        save_calls: list[dict] = []
        job.save = lambda **kwargs: save_calls.append(kwargs)

        updated = service.mark_running(job=job)

        self.assertEqual(updated.status, DatasetIntelligenceJobStatus.RUNNING)
        self.assertIsNotNone(updated.started_at)
        self.assertEqual(save_calls[0]["update_fields"], ["status", "started_at"])

    def test_claim_queued_jobs_uses_atomic_row_locking_contract(self) -> None:
        source = inspect.getsource(DatasetIntelligenceJobService.claim_queued_jobs)

        self.assertIn("transaction.atomic", source)
        self.assertIn("select_for_update(skip_locked=True)", source)
        self.assertIn("except DatabaseError", source)

    def test_claim_queued_jobs_transitions_only_queued_jobs_to_running(self) -> None:
        source = inspect.getsource(DatasetIntelligenceJobService.claim_queued_jobs)

        self.assertIn("status=DatasetIntelligenceJobStatus.QUEUED", source)
        self.assertIn("status=DatasetIntelligenceJobStatus.RUNNING", source)
        self.assertIn("started_at=claimed_started_at", source)

    def test_claim_queued_jobs_respects_batch_size_and_tenant_scope(self) -> None:
        source = inspect.getsource(DatasetIntelligenceJobService.claim_queued_jobs)
        signature = inspect.signature(DatasetIntelligenceJobService.claim_queued_jobs)

        self.assertIn("batch_size", signature.parameters)
        self.assertIn("tenant_id", signature.parameters)
        self.assertIn("limited_batch_size = max(int(batch_size), 1)", source)
        self.assertIn("queryset = queryset.filter(tenant_id=tenant_id)", source)

    def test_mark_completed_persists_sanitized_payload(self) -> None:
        service = DatasetIntelligenceJobService()
        job = SimpleNamespace(
            status=DatasetIntelligenceJobStatus.RUNNING,
            result_payload_json=None,
            decision_trace_json=None,
            logical_result_json=None,
            error_code="old",
            error_message="old",
            execution_ms=None,
            completed_at=None,
        )
        save_calls: list[dict] = []
        job.save = lambda **kwargs: save_calls.append(kwargs)

        result = service.mark_completed(
            job=job,
            result_payload={"success": True, "token": "secret", "result": {"ok": True}},
            decision_trace={"stage": "normalize"},
            logical_result={"logical_artifacts": [{"name": "records"}]},
            execution_ms=120,
        )

        self.assertEqual(result.status, DatasetIntelligenceJobStatus.COMPLETED)
        self.assertEqual(result.result_payload_json["success"], True)
        self.assertEqual(result.result_payload_json["result"], {"ok": True})
        self.assertEqual(result.decision_trace_json["stage"], "normalize")
        self.assertEqual(result.logical_result_json["logical_artifacts"][0]["name"], "records")
        self.assertIsNone(result.error_code)
        self.assertIsNone(result.error_message)
        self.assertEqual(result.execution_ms, 120)
        self.assertIsNotNone(result.completed_at)
        self.assertIn("result_payload_json", save_calls[0]["update_fields"])
        self.assertIn("decision_trace_json", save_calls[0]["update_fields"])
        self.assertIn("logical_result_json", save_calls[0]["update_fields"])

    def test_mark_failed_persists_sanitized_error(self) -> None:
        service = DatasetIntelligenceJobService()
        job = SimpleNamespace(
            status=DatasetIntelligenceJobStatus.RUNNING,
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
            error_code="job_failed",
            error_message="failed due to token leak",
            execution_ms=300,
        )

        self.assertEqual(result.status, DatasetIntelligenceJobStatus.FAILED)
        self.assertEqual(result.error_code, "job_failed")
        self.assertNotIn("token", str(result.error_message).lower())
        self.assertEqual(result.execution_ms, 300)
        self.assertIsNotNone(result.completed_at)
        self.assertIn("error_code", save_calls[0]["update_fields"])

    def test_mark_failed_requeues_retryable_failure_before_retry_limit(self) -> None:
        service = DatasetIntelligenceJobService(max_retries=1)
        job = SimpleNamespace(
            status=DatasetIntelligenceJobStatus.RUNNING,
            retry_count=0,
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
            error_code="intelligence_execution_error",
            error_message="temporary failure",
            retryable=True,
        )

        self.assertEqual(result.status, DatasetIntelligenceJobStatus.QUEUED)
        self.assertEqual(result.retry_count, 1)
        self.assertIsNone(result.started_at)
        self.assertIsNone(result.completed_at)
        self.assertIn("retry_count", save_calls[0]["update_fields"])

    def test_request_cancellation_is_tenant_scoped_and_status_guarded(self) -> None:
        source = inspect.getsource(DatasetIntelligenceJobService.request_cancellation)
        mark_completed_source = inspect.getsource(DatasetIntelligenceJobService.mark_completed)
        mark_failed_source = inspect.getsource(DatasetIntelligenceJobService.mark_failed)

        self.assertIn("transaction.atomic", source)
        self.assertIn("select_for_update", source)
        self.assertIn("tenant_id=tenant_id", source)
        self.assertIn("DatasetIntelligenceJobStatus.QUEUED", source)
        self.assertIn("DatasetIntelligenceJobStatus.CANCELLED", source)
        self.assertIn("transaction.atomic", mark_completed_source)
        self.assertIn("select_for_update", mark_completed_source)
        self.assertIn("transaction.atomic", mark_failed_source)
        self.assertIn("select_for_update", mark_failed_source)

    def test_mark_cancelled_transitions_running_job_without_retry_increment(self) -> None:
        service = DatasetIntelligenceJobService()
        job = SimpleNamespace(
            status=DatasetIntelligenceJobStatus.RUNNING,
            cancellation_requested_at=None,
            completed_at=None,
            retry_count=1,
        )
        save_calls: list[dict] = []
        job.save = lambda **kwargs: save_calls.append(kwargs)

        result = service.mark_cancelled(job=job)

        self.assertEqual(result.status, DatasetIntelligenceJobStatus.CANCELLED)
        self.assertIsNotNone(result.cancellation_requested_at)
        self.assertIsNotNone(result.completed_at)
        self.assertEqual(result.retry_count, 1)
        self.assertIn("cancellation_requested_at", save_calls[0]["update_fields"])

    def test_mark_failed_cancels_when_cancellation_is_requested_before_retry(self) -> None:
        service = DatasetIntelligenceJobService(max_retries=3)
        job = SimpleNamespace(
            status=DatasetIntelligenceJobStatus.RUNNING,
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
            error_code="intelligence_execution_error",
            error_message="temporary failure",
            retryable=True,
        )

        self.assertEqual(result.status, DatasetIntelligenceJobStatus.CANCELLED)
        self.assertEqual(result.retry_count, 0)

    def test_terminal_jobs_are_not_overwritten_by_cancellation_completion_or_failure(self) -> None:
        service = DatasetIntelligenceJobService()
        completed_job = SimpleNamespace(status=DatasetIntelligenceJobStatus.COMPLETED)
        failed_job = SimpleNamespace(status=DatasetIntelligenceJobStatus.FAILED)
        cancelled_job = SimpleNamespace(
            status=DatasetIntelligenceJobStatus.CANCELLED,
            cancellation_requested_at=object(),
        )
        completed_job.save = lambda **kwargs: self.fail("completed job should not save")
        failed_job.save = lambda **kwargs: self.fail("failed job should not save")
        cancelled_job.save = lambda **kwargs: self.fail("cancelled job should not save")

        completed_result = service.mark_completed(job=completed_job, result_payload={})
        failed_result = service.mark_failed(
            job=failed_job,
            error_code="intelligence_execution_error",
            error_message="temporary failure",
            retryable=True,
        )
        cancelled_result = service.mark_cancelled(job=cancelled_job)

        self.assertEqual(completed_result.status, DatasetIntelligenceJobStatus.COMPLETED)
        self.assertEqual(failed_result.status, DatasetIntelligenceJobStatus.FAILED)
        self.assertEqual(cancelled_result.status, DatasetIntelligenceJobStatus.CANCELLED)

    def test_get_job_is_tenant_scoped(self) -> None:
        service = DatasetIntelligenceJobService()
        fake_job = SimpleNamespace(id="ij-1")
        with patch(
            "resource_registry.services.dataset_intelligence_job_service."
            "DatasetIntelligenceJob.objects.get",
            return_value=fake_job,
        ) as get_mock:
            result = service.get_job(tenant_id="tenant-1", intelligence_job_id="ij-1")

        self.assertEqual(result, fake_job)
        get_mock.assert_called_once_with(id="ij-1", tenant_id="tenant-1")
