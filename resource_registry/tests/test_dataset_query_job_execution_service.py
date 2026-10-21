from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from resource_registry.read_auth.context import ReadAuthorizationContext
from resource_registry.services.dataset_query_job_execution_service import (
    DatasetQueryJobExecutionService,
    execute_dataset_query_job,
)


class DatasetQueryJobExecutionServiceTests(SimpleTestCase):
    def test_worker_rebuilds_current_restricted_reader_scope(self) -> None:
        job = SimpleNamespace(dataset_id="dataset-1")
        user = SimpleNamespace(pk=1, username="normal-user")
        delegated_context = ReadAuthorizationContext(
            tenant_id="tenant-1",
            actor="user:1",
            metadata={
                "source": "delegated_job_authority",
                "workspace_id": 2,
                "organization_id": 3,
            },
        )
        with (
            patch(
                "resource_registry.services.dataset_query_job_execution_service.User.objects.get",
                return_value=user,
            ),
            patch(
                "resource_registry.services.dataset_query_job_execution_service."
                "Organization.objects.get",
                return_value=SimpleNamespace(pk=3),
            ),
            patch(
                "resource_registry.services.dataset_query_job_execution_service."
                "RegistryAuthorizationContextService"
            ) as authorization_service_class,
            patch(
                "resource_registry.services.dataset_query_job_execution_service."
                "get_visible_workspace_dataset_ids",
                return_value=frozenset({"dataset-1"}),
            ),
            patch(
                "resource_registry.services.dataset_query_job_execution_service."
                "derive_registry_roles",
                return_value=("restricted_reader",),
            ),
            patch(
                "resource_registry.services.dataset_query_job_execution_service."
                "DataRegistryBifCapabilityResolver"
            ) as resolver_class,
            patch(
                "resource_registry.services.dataset_query_job_execution_service."
                "DataRegistryBifCapabilityPolicy",
                autospec=True,
            ) as policy_class,
            patch(
                "resource_registry.services.dataset_query_job_execution_service."
                "AuthorizedDataRegistryReadFacade"
            ) as facade_class,
        ):
            namespaces = (
                authorization_service_class.return_value.build_context.return_value.authorized_namespaces
            )
            namespaces.contains_namespace.return_value = True
            namespaces.resolve_workspace.return_value = 2
            resolver_class.return_value.resolve.return_value = SimpleNamespace(
                enabled_capabilities=frozenset({"resource_registry.dataset.query.job.start"}),
                disabled_capabilities=frozenset(),
            )

            live_context = DatasetQueryJobExecutionService._resolve_live_authorization_context(
                job=job,
                context=delegated_context,
            )

        self.assertIsNotNone(live_context)
        self.assertEqual(live_context.roles, ("restricted_reader",))
        self.assertEqual(live_context.enforced_dataset_ids, frozenset({"dataset-1"}))
        policy_class.return_value.assert_can_call.assert_called_once()
        facade_class.return_value.get_dataset.assert_called_once()

    def test_revoked_delegated_access_fails_without_running_query(self) -> None:
        job = SimpleNamespace(id="job-1", dataset_id="dataset-1")
        job_service = MagicMock()
        job_service.get_job.return_value = job
        job_service.mark_failed.return_value = job
        executor = MagicMock()
        service = DatasetQueryJobExecutionService(
            query_job_service=job_service,
            query_executor=executor,
        )
        context = ReadAuthorizationContext(
            tenant_id="tenant-1",
            metadata={"source": "delegated_job_authority"},
        )

        with patch.object(service, "_resolve_live_authorization_context", return_value=None):
            result = service.execute_job(context=context, query_job_id="job-1")

        self.assertEqual(result, job)
        self.assertEqual(
            job_service.mark_failed.call_args.kwargs["error_code"],
            "query_authorization_revoked",
        )
        self.assertFalse(job_service.mark_failed.call_args.kwargs["retryable"])
        job_service.mark_running.assert_not_called()
        executor.execute_query.assert_not_called()

    def test_execute_job_marks_running_then_completed(self) -> None:
        job = SimpleNamespace(
            id="job-1",
            dataset_id="dataset-1",
            version_id=None,
            sql_text="SELECT country FROM dataset LIMIT 1",
            status="queued",
        )
        job_service = MagicMock()
        job_service.get_job.return_value = job
        job_service.mark_running.return_value = job
        job_service.mark_completed.return_value = job

        executor = MagicMock()
        executor.execute_query.return_value = {
            "columns": ["country"],
            "rows": [["CR"]],
            "row_count": 1,
            "truncated": False,
        }

        service = DatasetQueryJobExecutionService(
            query_job_service=job_service,
            query_executor=executor,
        )
        context = ReadAuthorizationContext(tenant_id="tenant-1")

        result = service.execute_job(context=context, query_job_id="job-1")

        self.assertEqual(result, job)
        job_service.get_job.assert_called_once_with(tenant_id="tenant-1", query_job_id="job-1")
        job_service.mark_running.assert_called_once_with(job=job)
        job_service.mark_completed.assert_called_once()
        job_service.mark_failed.assert_not_called()

    def test_execute_job_marks_failed_when_executor_returns_error_payload(self) -> None:
        job = SimpleNamespace(
            id="job-1",
            dataset_id="dataset-1",
            version_id=None,
            sql_text="SELECT country FROM dataset LIMIT 1",
            status="queued",
        )
        job_service = MagicMock()
        job_service.get_job.return_value = job
        job_service.mark_running.return_value = job
        job_service.mark_failed.return_value = job

        executor = MagicMock()
        executor.execute_query.return_value = {
            "success": False,
            "status": 400,
            "code": "unsafe_sql",
            "error": "Only safe SELECT statements are allowed.",
        }

        service = DatasetQueryJobExecutionService(
            query_job_service=job_service,
            query_executor=executor,
        )
        context = ReadAuthorizationContext(tenant_id="tenant-1")

        result = service.execute_job(context=context, query_job_id="job-1")

        self.assertEqual(result, job)
        job_service.mark_completed.assert_not_called()
        job_service.mark_failed.assert_called_once()
        self.assertFalse(job_service.mark_failed.call_args.kwargs.get("retryable", False))

    def test_execute_job_maps_query_timeout_payload_to_retryable_execution_timeout(self) -> None:
        job = SimpleNamespace(
            id="job-1",
            dataset_id="dataset-1",
            version_id=None,
            sql_text="SELECT country FROM dataset LIMIT 1",
            status="queued",
        )
        job_service = MagicMock()
        job_service.get_job.return_value = job
        job_service.mark_running.return_value = job
        job_service.mark_failed.return_value = job
        executor = MagicMock()
        executor.execute_query.return_value = {
            "success": False,
            "status": 408,
            "code": "query_timeout",
            "error": "Query execution exceeded timeout.",
        }
        service = DatasetQueryJobExecutionService(
            query_job_service=job_service,
            query_executor=executor,
            execution_timeout_seconds=10,
        )

        result = service.execute_job(
            context=ReadAuthorizationContext(tenant_id="tenant-1"),
            query_job_id="job-1",
        )

        self.assertEqual(result, job)
        kwargs = job_service.mark_failed.call_args.kwargs
        self.assertEqual(kwargs["error_code"], "execution_timeout")
        self.assertIn("10 seconds", kwargs["error_message"])
        self.assertTrue(kwargs["retryable"])

    def test_execute_job_soft_timeout_after_success_delegates_retryable_failure(self) -> None:
        job = SimpleNamespace(
            id="job-1",
            dataset_id="dataset-1",
            version_id=None,
            sql_text="SELECT country FROM dataset LIMIT 1",
            status="queued",
        )
        job_service = MagicMock()
        job_service.get_job.return_value = job
        job_service.mark_running.return_value = job
        job_service.mark_failed.return_value = job
        executor = MagicMock()
        executor.execute_query.return_value = {"columns": [], "rows": [], "row_count": 0}
        service = DatasetQueryJobExecutionService(
            query_job_service=job_service,
            query_executor=executor,
            execution_timeout_seconds=1,
        )

        with patch(
            "resource_registry.services.dataset_query_job_execution_service.time.perf_counter",
            side_effect=[0.0, 2.0],
        ):
            result = service.execute_job(
                context=ReadAuthorizationContext(tenant_id="tenant-1"),
                query_job_id="job-1",
            )

        self.assertEqual(result, job)
        job_service.mark_completed.assert_not_called()
        kwargs = job_service.mark_failed.call_args.kwargs
        self.assertEqual(kwargs["error_code"], "execution_timeout")
        self.assertTrue(kwargs["retryable"])

    def test_execute_job_disabled_soft_timeout_allows_late_success(self) -> None:
        job = SimpleNamespace(
            id="job-1",
            dataset_id="dataset-1",
            version_id=None,
            sql_text="SELECT country FROM dataset LIMIT 1",
            status="queued",
        )
        job_service = MagicMock()
        job_service.get_job.return_value = job
        job_service.mark_running.return_value = job
        job_service.mark_completed.return_value = job
        executor = MagicMock()
        executor.execute_query.return_value = {"columns": [], "rows": [], "row_count": 0}
        service = DatasetQueryJobExecutionService(
            query_job_service=job_service,
            query_executor=executor,
            execution_timeout_seconds=0,
        )

        with patch(
            "resource_registry.services.dataset_query_job_execution_service.time.perf_counter",
            side_effect=[0.0, 2.0],
        ):
            result = service.execute_job(
                context=ReadAuthorizationContext(tenant_id="tenant-1"),
                query_job_id="job-1",
            )

        self.assertEqual(result, job)
        job_service.mark_completed.assert_called_once()
        job_service.mark_failed.assert_not_called()

    def test_execute_job_marks_failed_when_executor_raises(self) -> None:
        job = SimpleNamespace(
            id="job-1",
            dataset_id="dataset-1",
            version_id=None,
            sql_text="SELECT country FROM dataset LIMIT 1",
            status="queued",
        )
        job_service = MagicMock()
        job_service.get_job.return_value = job
        job_service.mark_running.return_value = job
        job_service.mark_failed.return_value = job

        executor = MagicMock()
        executor.execute_query.side_effect = RuntimeError("boom")

        service = DatasetQueryJobExecutionService(
            query_job_service=job_service,
            query_executor=executor,
        )
        context = ReadAuthorizationContext(tenant_id="tenant-1")

        result = service.execute_job(context=context, query_job_id="job-1")

        self.assertEqual(result, job)
        job_service.mark_completed.assert_not_called()
        job_service.mark_failed.assert_called_once()
        self.assertTrue(job_service.mark_failed.call_args.kwargs["retryable"])

    def test_execute_job_marks_cancelled_when_cancellation_is_requested_after_execution(
        self,
    ) -> None:
        job = SimpleNamespace(
            id="job-1",
            dataset_id="dataset-1",
            version_id=None,
            sql_text="SELECT country FROM dataset LIMIT 1",
            status="running",
        )
        cancelled_job = SimpleNamespace(id="job-1", status="cancelled")
        job_service = MagicMock()
        job_service.get_job.return_value = job
        job_service.mark_running.return_value = job
        job_service.is_cancellation_requested.return_value = True
        job_service.mark_cancelled.return_value = cancelled_job
        executor = MagicMock()
        executor.execute_query.return_value = {"columns": [], "rows": [], "row_count": 0}
        service = DatasetQueryJobExecutionService(
            query_job_service=job_service,
            query_executor=executor,
            execution_timeout_seconds=1,
        )

        with patch(
            "resource_registry.services.dataset_query_job_execution_service.time.perf_counter",
            side_effect=[0.0, 2.0],
        ):
            result = service.execute_job(
                context=ReadAuthorizationContext(tenant_id="tenant-1"),
                query_job_id="job-1",
            )

        self.assertEqual(result, cancelled_job)
        job_service.mark_cancelled.assert_called_once_with(job=job)
        job_service.mark_completed.assert_not_called()
        job_service.mark_failed.assert_not_called()

    def test_execute_job_applies_result_row_limit(self) -> None:
        job = SimpleNamespace(
            id="job-1",
            dataset_id="dataset-1",
            version_id=None,
            sql_text="SELECT country FROM dataset LIMIT 1",
            status="queued",
        )
        job_service = MagicMock()
        job_service.get_job.return_value = job
        job_service.mark_running.return_value = job
        job_service.mark_completed.return_value = job

        executor = MagicMock()
        executor.execute_query.return_value = {
            "columns": ["country"],
            "rows": [["CR"], ["US"], ["MX"]],
            "row_count": 3,
            "truncated": False,
        }

        service = DatasetQueryJobExecutionService(
            query_job_service=job_service,
            query_executor=executor,
            max_result_rows=2,
        )
        context = ReadAuthorizationContext(tenant_id="tenant-1")

        _ = service.execute_job(context=context, query_job_id="job-1")

        completed_result = job_service.mark_completed.call_args.kwargs["result"]
        self.assertEqual(completed_result["rows"], [["CR"], ["US"]])
        self.assertEqual(completed_result["row_count"], 2)
        self.assertTrue(completed_result["truncated"])


class ExecuteDatasetQueryJobEntryPointTests(SimpleTestCase):
    def test_execute_dataset_query_job_returns_job_id_and_status(self) -> None:
        fake_job = SimpleNamespace(id="job-1", status="completed")
        persisted_job = SimpleNamespace(id="job-1")
        fake_job_service = MagicMock()
        fake_job_service.get_job.return_value = persisted_job
        authority = MagicMock()
        authority.to_read_authorization_context.return_value = ReadAuthorizationContext(
            tenant_id="tenant-1"
        )
        fake_service = MagicMock()
        fake_service.execute_job.return_value = fake_job

        with (
            patch(
                "resource_registry.services.dataset_query_job_execution_service."
                "DatasetQueryJobService",
                return_value=fake_job_service,
            ),
            patch(
                "resource_registry.services.dataset_query_job_execution_service."
                "WorkerExecutionAuthorityService"
            ) as authority_service,
            patch(
                "resource_registry.services.dataset_query_job_execution_service."
                "DatasetQueryJobExecutionService",
                return_value=fake_service,
            ),
        ):
            authority_service.return_value.build_query_context.return_value = authority
            payload = execute_dataset_query_job(
                tenant_id="tenant-1",
                query_job_id="job-1",
                actor="alice",
                roles=("reader",),
                scopes=("scope-a",),
            )

        self.assertEqual(payload["query_job_id"], "job-1")
        self.assertEqual(payload["job_status"], "completed")
