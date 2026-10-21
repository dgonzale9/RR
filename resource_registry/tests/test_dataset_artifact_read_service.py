from __future__ import annotations

import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from resource_registry.models import (
    Dataset,
    DatasetAnalysisArtifact,
    DatasetChartArtifact,
    DatasetExportPlan,
    DatasetIntelligenceStatus,
    DatasetQueryRun,
    DatasetSourceType,
    DatasetVersion,
    DatasetVersionStatus,
)
from resource_registry.read_auth.context import ReadAuthorizationContext
from resource_registry.read_auth.exceptions import DataRegistryAuthorizationError
from resource_registry.services.dataset_artifact_read_service import DatasetArtifactReadService

User = get_user_model()


class DatasetArtifactReadServiceTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(username="artifact_reader", password="unused")
        self.tenant_id = uuid.uuid4()
        self.other_tenant_id = uuid.uuid4()

        self.dataset = Dataset.objects.create(
            tenant_id=self.tenant_id,
            slug="sales",
            name="Sales",
            source_type=DatasetSourceType.FILE_UPLOAD,
            created_by=self.user,
        )
        self.version = DatasetVersion.objects.create(
            dataset=self.dataset,
            version_number=1,
            source_uri="local://sales.csv",
            content_hash="hash-1",
            status=DatasetVersionStatus.READY,
        )

        self.query_run = DatasetQueryRun.objects.create(
            tenant_id=self.tenant_id,
            dataset=self.dataset,
            version=self.version,
            sql_hash="abc",
            sql_text="SELECT country FROM dataset LIMIT 1",
            status=DatasetIntelligenceStatus.SUCCEEDED,
            row_count=1,
            truncated=False,
            metadata_json={"columns": ["country"], "secret": "remove"},
            created_by=self.user,
        )
        self.analysis_artifact = DatasetAnalysisArtifact.objects.create(
            tenant_id=self.tenant_id,
            dataset=self.dataset,
            version=self.version,
            analysis_type="summary",
            title="Dataset analysis: summary",
            summary="ok",
            insights_json=[{"title": "insight", "token": "remove"}],
            queries_used_json=[{"sql": "SELECT x FROM dataset LIMIT 1"}],
            status=DatasetIntelligenceStatus.SUCCEEDED,
            metadata_json={"signed url": "remove"},
            created_by=self.user,
        )
        self.chart_artifact = DatasetChartArtifact.objects.create(
            tenant_id=self.tenant_id,
            dataset=self.dataset,
            version=self.version,
            chart_type="bar",
            title="Spend by Country",
            chart_spec_json={"x": "country", "password": "remove"},
            source_query_json={"sql": "SELECT country, total_spend FROM dataset LIMIT 10"},
            status=DatasetIntelligenceStatus.SUCCEEDED,
            metadata_json={},
            created_by=self.user,
        )
        self.export_plan = DatasetExportPlan.objects.create(
            tenant_id=self.tenant_id,
            dataset=self.dataset,
            version=self.version,
            artifact_type="xlsx",
            title="Dataset Export",
            export_plan_json={
                "filename": "dataset_export.xlsx",
                "source": "https://blob/path/file.csv?sig=abc",
            },
            status=DatasetIntelligenceStatus.SUCCEEDED,
            metadata_json={"credentials": "remove"},
            created_by=self.user,
        )
        self.service = DatasetArtifactReadService()
        self.context = ReadAuthorizationContext(
            tenant_id=str(self.tenant_id),
            roles=("reader",),
            allowed_dataset_ids=frozenset({str(self.dataset.id)}),
            metadata={"requested_tenant_id": str(self.tenant_id)},
        )

    def test_get_query_run_returns_record(self) -> None:
        payload = self.service.get_query_run(
            context=self.context,
            tenant_id=str(self.tenant_id),
            query_run_id=str(self.query_run.id),
        )
        self.assertEqual(payload["id"], str(self.query_run.id))
        self.assertEqual(payload["row_count"], 1)

    def test_get_analysis_artifact_returns_record(self) -> None:
        payload = self.service.get_analysis_artifact(
            context=self.context,
            tenant_id=str(self.tenant_id),
            analysis_artifact_id=str(self.analysis_artifact.id),
        )
        self.assertEqual(payload["id"], str(self.analysis_artifact.id))
        self.assertEqual(payload["analysis_type"], "summary")

    def test_get_chart_artifact_returns_record(self) -> None:
        payload = self.service.get_chart_artifact(
            context=self.context,
            tenant_id=str(self.tenant_id),
            chart_artifact_id=str(self.chart_artifact.id),
        )
        self.assertEqual(payload["id"], str(self.chart_artifact.id))
        self.assertEqual(payload["chart_type"], "bar")

    def test_get_export_plan_returns_record(self) -> None:
        payload = self.service.get_export_plan(
            context=self.context,
            tenant_id=str(self.tenant_id),
            export_plan_id=str(self.export_plan.id),
        )
        self.assertEqual(payload["id"], str(self.export_plan.id))
        self.assertEqual(payload["artifact_type"], "xlsx")

    def test_tenant_isolation_enforced(self) -> None:
        with self.assertRaises(DataRegistryAuthorizationError):
                self.service.get_query_run(
                    context=self.context,
                    tenant_id=str(self.other_tenant_id),
                    query_run_id=str(self.query_run.id),
                )

    def test_not_found_raises_authorization_error(self) -> None:
        with self.assertRaises(DataRegistryAuthorizationError):
                self.service.get_export_plan(
                    context=self.context,
                    tenant_id=str(self.tenant_id),
                    export_plan_id=str(uuid.uuid4()),
                )

    def test_payload_is_sanitized(self) -> None:
        query_payload = self.service.get_query_run(
            context=self.context,
            tenant_id=str(self.tenant_id),
            query_run_id=str(self.query_run.id),
        )
        export_payload = self.service.get_export_plan(
            context=self.context,
            tenant_id=str(self.tenant_id),
            export_plan_id=str(self.export_plan.id),
        )
        self.assertNotIn("secret", str(query_payload))
        self.assertNotIn("credentials", str(export_payload))
        self.assertNotIn("sig=", str(export_payload))

    def test_dataset_scope_denied_when_dataset_not_allowed(self) -> None:
        denied_context = ReadAuthorizationContext(
            tenant_id=str(self.tenant_id),
            roles=("reader",),
            allowed_dataset_ids=frozenset({str(uuid.uuid4())}),
            metadata={"requested_tenant_id": str(self.tenant_id)},
        )
        with self.assertRaises(DataRegistryAuthorizationError):
            self.service.get_query_run(
                context=denied_context,
                tenant_id=str(self.tenant_id),
                query_run_id=str(self.query_run.id),
            )
