from __future__ import annotations

import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from resource_registry.models import (
    Dataset,
    DatasetAnalysisArtifact,
    DatasetChartArtifact,
    DatasetExportPlan,
    DatasetQueryRun,
    DatasetSourceType,
    DatasetVersion,
    DatasetVersionStatus,
)
from resource_registry.services.dataset_intelligence_persistence_service import (
    DatasetIntelligencePersistenceService,
)

User = get_user_model()


class DatasetIntelligencePersistenceServiceTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(username="persist_user", password="unused")
        self.tenant_id = uuid.uuid4()
        self.dataset = Dataset.objects.create(
            tenant_id=self.tenant_id,
            slug="sales-dataset",
            name="Sales Dataset",
            source_type=DatasetSourceType.FILE_UPLOAD,
            created_by=self.user,
        )
        self.version = DatasetVersion.objects.create(
            dataset=self.dataset,
            version_number=1,
            source_uri="local://sales.csv",
            content_hash="abc123",
            status=DatasetVersionStatus.READY,
        )
        self.service = DatasetIntelligencePersistenceService()

    def test_persist_query_run_creates_record(self) -> None:
        record = self.service.persist_query_run(
            tenant_id=str(self.tenant_id),
            dataset_id=str(self.dataset.id),
            version_id=str(self.version.id),
            sql="SELECT country FROM dataset LIMIT 1",
            result={"columns": ["country"], "row_count": 1, "truncated": False},
            created_by=self.user,
        )
        self.assertEqual(DatasetQueryRun.objects.count(), 1)
        self.assertEqual(record.dataset_id, str(self.dataset.id))
        self.assertEqual(record.version_id, str(self.version.id))
        self.assertEqual(record.row_count, 1)
        self.assertNotEqual(record.sql_hash, "")

    def test_persist_analysis_artifact_creates_record(self) -> None:
        record = self.service.persist_analysis_artifact(
            tenant_id=str(self.tenant_id),
            dataset_id=str(self.dataset.id),
            version_id=str(self.version.id),
            result={
                "analysis_type": "summary",
                "summary": "Summary text",
                "insights": [{"title": "Top segment"}],
                "queries_used": [{"name": "q1", "sql": "SELECT x FROM dataset LIMIT 1"}],
                "status": 200,
            },
            created_by=self.user,
        )
        self.assertEqual(DatasetAnalysisArtifact.objects.count(), 1)
        self.assertEqual(record.analysis_type, "summary")
        self.assertEqual(record.insights_json[0]["title"], "Top segment")

    def test_persist_chart_artifact_creates_record(self) -> None:
        record = self.service.persist_chart_artifact(
            tenant_id=str(self.tenant_id),
            dataset_id=str(self.dataset.id),
            version_id=str(self.version.id),
            result={
                "status": 200,
                "chart": {
                    "chart_type": "bar",
                    "title": "Spend by Country",
                    "source_query": {"sql": "SELECT country, total_spend FROM dataset LIMIT 10"},
                },
            },
            created_by=self.user,
        )
        self.assertEqual(DatasetChartArtifact.objects.count(), 1)
        self.assertEqual(record.chart_type, "bar")
        self.assertIn("source_query", record.chart_spec_json)

    def test_persist_export_plan_creates_record(self) -> None:
        record = self.service.persist_export_plan(
            tenant_id=str(self.tenant_id),
            dataset_id=str(self.dataset.id),
            version_id=str(self.version.id),
            result={
                "status": 200,
                "artifact_type": "xlsx",
                "title": "Dataset Export",
                "export_plan": {
                    "filename": "dataset_export.xlsx",
                    "security": {
                        "raw_rows_allowed": False,
                        "max_rows_per_query": 100,
                        "secrets_allowed": False,
                    },
                },
            },
            created_by=self.user,
        )
        self.assertEqual(DatasetExportPlan.objects.count(), 1)
        self.assertEqual(record.artifact_type, "xlsx")
        self.assertEqual(record.export_plan_json["filename"], "dataset_export.xlsx")

    def test_persisted_payload_redacts_sensitive_markers(self) -> None:
        record = self.service.persist_query_run(
            tenant_id=str(self.tenant_id),
            dataset_id=str(self.dataset.id),
            version_id=str(self.version.id),
            sql="SELECT country FROM dataset LIMIT 1",
            result={
                "columns": ["country"],
                "row_count": 1,
                "truncated": False,
                "raw_rows": [["CR"]],
                "credentials": "abc",
                "message": "https://blob/path/file.csv?sig=super-secret",
            },
            created_by=self.user,
        )
        metadata_text = str(record.metadata_json)
        self.assertNotIn("credentials", metadata_text)
        self.assertNotIn("sig=", metadata_text)
        self.assertNotIn("raw_rows", metadata_text)
