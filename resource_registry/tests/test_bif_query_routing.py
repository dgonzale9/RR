from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.test import SimpleTestCase

from BotServer.builtin_functions.resource_registry_functions import (
    resource_registry_plan_query,
    resource_registry_query_dataset,
    resource_registry_start_query_job,
)
from resource_registry.read_auth.exceptions import DataRegistryAuthorizationError
from resource_registry.services.dataset_query_routing_policy import DatasetQueryRoutingDecision
from resource_registry.services.query_planner import QueryPlan


class DataRegistryQueryRoutingBifTests(SimpleTestCase):
    def setUp(self) -> None:
        """Isolate query-routing behavior from the separately tested namespace boundary."""
        super().setUp()
        self._tenant_resolution_patcher = patch(
            "BotServer.builtin_functions.resource_registry_functions._resolve_read_only_tenant_id",
            side_effect=lambda _request, _context_manager, tenant_id: str(tenant_id),
        )
        self._tenant_resolution_patcher.start()

    def tearDown(self) -> None:
        """Restore the canonical namespace-admission boundary."""
        self._tenant_resolution_patcher.stop()
        super().tearDown()

    def _request(self, tenant_id: str) -> SimpleNamespace:
        return SimpleNamespace(user=None, session={}, tenant_id=tenant_id)

    def test_small_dataset_query_runs_synchronously(self) -> None:
        tenant_id = str(uuid4())
        dataset_id = str(uuid4())
        request = self._request(tenant_id)
        with (
            patch("BotServer.builtin_functions.resource_registry_functions._assert_capability"),
            patch(
                "BotServer.builtin_functions.resource_registry_functions._get_read_facade",
            ) as read_facade_mock,
            patch(
                "BotServer.builtin_functions.resource_registry_functions._validate_sql_for_query_job",
                return_value=None,
            ),
            patch(
                "BotServer.builtin_functions.resource_registry_functions._get_query_routing_policy",
            ) as routing_policy_mock,
            patch(
                "BotServer.builtin_functions.resource_registry_functions._get_query_executor",
            ) as query_executor_mock,
            patch("BotServer.builtin_functions.resource_registry_functions._safe_persist"),
        ):
            read_facade_mock.return_value.get_dataset.return_value = SimpleNamespace(id=dataset_id)
            routing_policy_mock.return_value.evaluate.return_value = DatasetQueryRoutingDecision(
                mode="sync_allowed",
                reason="configured_policy",
                artifact_size_bytes=1024,
                threshold_bytes=10_000,
            )
            query_executor_mock.return_value.execute_query.return_value = {
                "columns": ["country"],
                "rows": [["CR"]],
                "row_count": 1,
                "truncated": False,
            }
            payload = json.loads(
                resource_registry_query_dataset(
                    request,
                    None,
                    tenant_id,
                    dataset_id,
                    "SELECT country FROM dataset LIMIT 1",
                ),
            )

        self.assertTrue(payload["success"])
        self.assertEqual(payload["status"], 200)
        self.assertEqual(payload["row_count"], 1)

    def test_plan_query_preserves_typed_schema_for_numeric_metrics(self) -> None:
        tenant_id = str(uuid4())
        dataset_id = str(uuid4())
        request = self._request(tenant_id)
        typed_schema = [
            {"name": "attribute_path", "type": "VARCHAR"},
            {"name": "index", "type": "DOUBLE"},
        ]
        query_plan = QueryPlan(
            intent="aggregate_avg_by_dimension",
            sql='SELECT "attribute_path", AVG("index") AS average_index FROM dataset LIMIT 10',
            columns_used=("attribute_path", "index"),
            result_columns=("attribute_path", "average_index"),
            limit=10,
        )
        with (
            patch("BotServer.builtin_functions.resource_registry_functions._assert_capability"),
            patch(
                "BotServer.builtin_functions.resource_registry_functions._get_read_facade",
            ) as read_facade_mock,
            patch(
                "BotServer.builtin_functions.resource_registry_functions._get_query_planner",
            ) as query_planner_mock,
        ):
            read_facade_mock.return_value.get_schema.return_value = {"schema_json": typed_schema}
            query_planner_mock.return_value.plan.return_value = query_plan

            payload = json.loads(
                resource_registry_plan_query(
                    request,
                    None,
                    tenant_id,
                    dataset_id,
                    "Show average index by attribute path",
                ),
            )

        self.assertTrue(payload["success"])
        self.assertEqual(payload["query_plan"]["intent"], "aggregate_avg_by_dimension")
        query_planner_mock.return_value.plan.assert_called_once_with(
            user_request="Show average index by attribute path",
            schema_columns=tuple(typed_schema),
        )

    def test_large_dataset_returns_async_query_required(self) -> None:
        tenant_id = str(uuid4())
        dataset_id = str(uuid4())
        request = self._request(tenant_id)
        with (
            patch("BotServer.builtin_functions.resource_registry_functions._assert_capability"),
            patch(
                "BotServer.builtin_functions.resource_registry_functions._get_read_facade",
            ) as read_facade_mock,
            patch(
                "BotServer.builtin_functions.resource_registry_functions._validate_sql_for_query_job",
                return_value=None,
            ),
            patch(
                "BotServer.builtin_functions.resource_registry_functions._get_query_routing_policy",
            ) as routing_policy_mock,
            patch(
                "BotServer.builtin_functions.resource_registry_functions._get_query_executor",
            ) as query_executor_mock,
        ):
            read_facade_mock.return_value.get_dataset.return_value = SimpleNamespace(id=dataset_id)
            routing_policy_mock.return_value.evaluate.return_value = DatasetQueryRoutingDecision(
                mode="async_required",
                reason="artifact_size",
                artifact_size_bytes=500_000_000,
                threshold_bytes=100_000_000,
            )
            payload = json.loads(
                resource_registry_query_dataset(
                    request,
                    None,
                    tenant_id,
                    dataset_id,
                    "SELECT country FROM dataset LIMIT 1",
                ),
            )

        self.assertFalse(payload["success"])
        self.assertEqual(payload["status"], 409)
        self.assertEqual(payload["code"], "async_query_required")
        self.assertEqual(payload["recommended_bif"], "resource_registry_start_query_job")
        query_executor_mock.return_value.execute_query.assert_not_called()

    def test_invalid_dataset_id_returns_400_before_routing(self) -> None:
        tenant_id = str(uuid4())
        request = self._request(tenant_id)
        with (
            patch(
                "BotServer.builtin_functions.resource_registry_functions._resolve_read_only_tenant_id",
                return_value=tenant_id,
            ),
            patch("BotServer.builtin_functions.resource_registry_functions._assert_capability"),
            patch(
                "BotServer.builtin_functions.resource_registry_functions._get_query_routing_policy",
            ) as routing_policy_mock,
        ):
            payload = json.loads(
                resource_registry_query_dataset(
                    request,
                    None,
                    tenant_id,
                    "not-a-uuid",
                    "SELECT country FROM dataset LIMIT 1",
                ),
            )

        self.assertFalse(payload["success"])
        self.assertEqual(payload["status"], 400)
        self.assertEqual(payload["code"], "invalid_dataset_id")
        routing_policy_mock.assert_not_called()

    def test_unsafe_sql_returns_400_before_routing(self) -> None:
        tenant_id = str(uuid4())
        dataset_id = str(uuid4())
        request = self._request(tenant_id)
        with (
            patch("BotServer.builtin_functions.resource_registry_functions._assert_capability"),
            patch(
                "BotServer.builtin_functions.resource_registry_functions._get_read_facade",
            ) as read_facade_mock,
            patch(
                "BotServer.builtin_functions.resource_registry_functions._validate_sql_for_query_job",
                return_value={
                    "success": False,
                    "status": 400,
                    "code": "unsafe_sql",
                    "error": "Only safe SELECT statements are allowed.",
                },
            ),
            patch(
                "BotServer.builtin_functions.resource_registry_functions._get_query_routing_policy",
            ) as routing_policy_mock,
        ):
            read_facade_mock.return_value.get_dataset.return_value = SimpleNamespace(id=dataset_id)
            payload = json.loads(
                resource_registry_query_dataset(
                    request,
                    None,
                    tenant_id,
                    dataset_id,
                    "SELECT * FROM dataset",
                ),
            )

        self.assertFalse(payload["success"])
        self.assertEqual(payload["status"], 400)
        self.assertEqual(payload["code"], "unsafe_sql")
        routing_policy_mock.assert_not_called()

    def test_unauthorized_dataset_fails_before_routing(self) -> None:
        tenant_id = str(uuid4())
        dataset_id = str(uuid4())
        request = self._request(tenant_id)
        with (
            patch("BotServer.builtin_functions.resource_registry_functions._assert_capability"),
            patch(
                "BotServer.builtin_functions.resource_registry_functions._get_read_facade",
            ) as read_facade_mock,
            patch(
                "BotServer.builtin_functions.resource_registry_functions._get_query_routing_policy",
            ) as routing_policy_mock,
        ):
            read_facade_mock.return_value.get_dataset.side_effect = DataRegistryAuthorizationError(
                "Dataset access denied.",
            )
            payload = json.loads(
                resource_registry_query_dataset(
                    request,
                    None,
                    tenant_id,
                    dataset_id,
                    "SELECT country FROM dataset LIMIT 1",
                ),
            )

        self.assertFalse(payload["success"])
        self.assertEqual(payload["status"], 403)
        self.assertEqual(payload["code"], "authorization_error")
        routing_policy_mock.assert_not_called()

    def test_start_query_job_still_creates_queued_job_for_large_dataset(self) -> None:
        tenant_id = str(uuid4())
        dataset_id = str(uuid4())
        query_job_id = str(uuid4())
        request = self._request(tenant_id)
        with (
            patch(
                "BotServer.builtin_functions.resource_registry_functions."
                "_admit_async_submission_namespace",
                return_value=SimpleNamespace(
                    namespace_id=tenant_id,
                    organization_id=1,
                    workspace_id=1,
                    actor_id=1,
                ),
            ),
            patch("BotServer.builtin_functions.resource_registry_functions._assert_capability"),
            patch(
                "BotServer.builtin_functions.resource_registry_functions._get_read_facade",
            ) as read_facade_mock,
            patch(
                "BotServer.builtin_functions.resource_registry_functions._validate_sql_for_query_job",
                return_value=None,
            ),
            patch(
                "BotServer.builtin_functions.resource_registry_functions._get_query_job_service",
            ) as query_job_service_mock,
        ):
            read_facade_mock.return_value.get_dataset.return_value = SimpleNamespace(id=dataset_id)
            query_job_service_mock.return_value.create_job.return_value = SimpleNamespace(
                id=query_job_id,
                status="queued",
            )
            payload = json.loads(
                resource_registry_start_query_job(
                    request,
                    None,
                    tenant_id,
                    dataset_id,
                    "SELECT country FROM dataset LIMIT 1",
                ),
            )

        self.assertTrue(payload["success"])
        self.assertEqual(payload["status"], 202)
        self.assertEqual(payload["job_status"], "queued")

    def test_no_secret_or_local_path_exposure_in_async_required_response(self) -> None:
        tenant_id = str(uuid4())
        dataset_id = str(uuid4())
        request = self._request(tenant_id)
        with (
            patch("BotServer.builtin_functions.resource_registry_functions._assert_capability"),
            patch(
                "BotServer.builtin_functions.resource_registry_functions._get_read_facade",
            ) as read_facade_mock,
            patch(
                "BotServer.builtin_functions.resource_registry_functions._validate_sql_for_query_job",
                return_value=None,
            ),
            patch(
                "BotServer.builtin_functions.resource_registry_functions._get_query_routing_policy",
            ) as routing_policy_mock,
        ):
            read_facade_mock.return_value.get_dataset.return_value = SimpleNamespace(id=dataset_id)
            routing_policy_mock.return_value.evaluate.return_value = DatasetQueryRoutingDecision(
                mode="async_required",
                reason="artifact_size",
                artifact_size_bytes=500_000_000,
                threshold_bytes=100_000_000,
            )
            raw = resource_registry_query_dataset(
                request,
                None,
                tenant_id,
                dataset_id,
                "SELECT country FROM dataset LIMIT 1",
            )

        lowered = raw.lower()
        self.assertNotIn("token", lowered)
        self.assertNotIn("secret", lowered)
        self.assertNotIn("/tmp/", lowered)
