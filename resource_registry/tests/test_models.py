from __future__ import annotations

import uuid
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import models
from django.test import SimpleTestCase

from resource_registry.models import (
    Dataset,
    DatasetAnalysisArtifact,
    DatasetArtifact,
    DatasetChartArtifact,
    DatasetExportPlan,
    DatasetGeneratedArtifact,
    DatasetIntelligenceJob,
    DatasetIntelligenceJobOperation,
    DatasetIntelligenceJobStatus,
    DatasetLineage,
    DatasetQueryJob,
    DatasetQueryJobStatus,
    DatasetQueryRun,
    DatasetVersion,
    DatasetVersionStatus,
    ExternalDatasetJob,
    GovernancePolicy,
    TransformationDefinition,
    TransformationDefinitionVersion,
    TransformationDefinitionVersionStatus,
    WorkbookIngestionJob,
    WorkbookIngestionJobStatus,
)
from resource_registry.utils.json_checksum import calculate_transformation_specification_checksum


class DataRegistryModelContractTests(SimpleTestCase):
    def test_dataset_has_tenant_slug_uniqueness(self) -> None:
        constraint_names = {constraint.name for constraint in Dataset._meta.constraints}
        self.assertIn("dataset_tenant_slug_uniq", constraint_names)

    def test_dataset_version_has_expected_uniqueness_constraints(self) -> None:
        constraint_names = {constraint.name for constraint in DatasetVersion._meta.constraints}
        self.assertIn("dataset_version_number_uniq", constraint_names)
        self.assertNotIn("dataset_version_hash_uniq", constraint_names)

    def test_dataset_version_is_immutable_once_ready(self) -> None:
        version = DatasetVersion(pk=1, status=DatasetVersionStatus.READY)
        version._state.adding = False
        with patch.object(
            DatasetVersion.objects,
            "get",
            return_value=DatasetVersion(pk=1, status=DatasetVersionStatus.READY),
        ):
            with self.assertRaises(ValidationError):
                version.save()

    def test_dataset_version_can_update_before_ready(self) -> None:
        version = DatasetVersion(pk=1, status=DatasetVersionStatus.PENDING)
        with (
            patch.object(
                DatasetVersion.objects,
                "get",
                return_value=DatasetVersion(pk=1, status=DatasetVersionStatus.PENDING),
            ),
            patch.object(models.Model, "save", return_value=None) as mocked_save,
        ):
            version.save()
        mocked_save.assert_called_once()

    def test_dataset_artifact_fields_do_not_include_credential_like_columns(self) -> None:
        artifact_field_names = {field.name.lower() for field in DatasetArtifact._meta.get_fields()}
        banned_tokens = {"secret", "password", "token", "credential"}
        has_banned_field = any(
            token in field_name for field_name in artifact_field_names for token in banned_tokens
        )
        self.assertFalse(has_banned_field)

    def test_dataset_artifact_has_lifecycle_status_field(self) -> None:
        artifact_field_names = {field.name for field in DatasetArtifact._meta.get_fields()}
        self.assertIn("lifecycle_status", artifact_field_names)

    def test_dataset_lineage_requires_upstream_reference_constraint(self) -> None:
        constraint_names = {constraint.name for constraint in DatasetLineage._meta.constraints}
        self.assertIn("dataset_lineage_exact_upstream", constraint_names)

    def test_governance_policy_supports_tenant_default_flag(self) -> None:
        policy_field_names = {field.name for field in GovernancePolicy._meta.get_fields()}
        self.assertIn("is_tenant_default", policy_field_names)

    def test_transformation_definition_has_expected_fields_indexes_and_constraints(self) -> None:
        field_names = {field.name for field in TransformationDefinition._meta.get_fields()}
        self.assertIn("tenant_id", field_names)
        self.assertIn("definition_key", field_names)
        self.assertIn("name", field_names)
        self.assertIn("description", field_names)
        self.assertIn("created_by", field_names)
        self.assertIn("created_at", field_names)
        self.assertIn("updated_at", field_names)
        self.assertIn("archived_at", field_names)
        self.assertNotIn("status", field_names)
        self.assertNotIn("specification_json", field_names)

        index_names = {index.name for index in TransformationDefinition._meta.indexes}
        self.assertIn("td_tenant_key_idx", index_names)
        self.assertIn("td_tenant_archived_idx", index_names)
        self.assertIn("td_tenant_recent_idx", index_names)

        constraint_names = {
            constraint.name for constraint in TransformationDefinition._meta.constraints
        }
        self.assertIn("td_tenant_key_uniq", constraint_names)

    def test_transformation_definition_identity_is_tenant_scoped(self) -> None:
        unique_constraint = next(
            constraint
            for constraint in TransformationDefinition._meta.constraints
            if constraint.name == "td_tenant_key_uniq"
        )
        self.assertEqual(tuple(unique_constraint.fields), ("tenant_id", "definition_key"))

    def test_transformation_definition_archived_at_defaults_to_null(self) -> None:
        definition = TransformationDefinition(
            tenant_id=uuid.uuid4(),
            definition_key="audience-workbook",
            name="Audience Workbook",
        )
        self.assertIsNone(definition.archived_at)

    def test_transformation_definition_string_representation_matches_dataset_pattern(self) -> None:
        tenant_id = uuid.uuid4()
        definition = TransformationDefinition(
            tenant_id=tenant_id,
            definition_key="audience-workbook",
            name="Audience Workbook",
        )
        self.assertEqual(str(definition), f"Audience Workbook ({tenant_id})")

    def test_transformation_definition_version_has_expected_fields_indexes_and_constraints(
        self,
    ) -> None:
        field_names = {field.name for field in TransformationDefinitionVersion._meta.get_fields()}
        self.assertIn("definition", field_names)
        self.assertIn("version_number", field_names)
        self.assertIn("status", field_names)
        self.assertIn("specification_json", field_names)
        self.assertIn("created_by", field_names)
        self.assertIn("created_at", field_names)
        self.assertIn("updated_at", field_names)
        self.assertIn("specification_checksum", field_names)
        self.assertNotIn("dataset", field_names)
        self.assertNotIn("dataset_version", field_names)
        self.assertNotIn("job", field_names)
        self.assertNotIn("provider", field_names)

        checksum_field = TransformationDefinitionVersion._meta.get_field("specification_checksum")
        self.assertEqual(checksum_field.max_length, 64)
        self.assertFalse(checksum_field.editable)
        self.assertFalse(checksum_field.unique)
        self.assertFalse(checksum_field.db_index)

        index_names = {index.name for index in TransformationDefinitionVersion._meta.indexes}
        self.assertIn("tdv_definition_version_idx", index_names)
        self.assertIn("tdv_definition_status_idx", index_names)
        self.assertIn("tdv_definition_recent_idx", index_names)
        self.assertFalse(
            any(
                "specification_checksum" in index.fields
                for index in TransformationDefinitionVersion._meta.indexes
            ),
        )

        constraint_names = {
            constraint.name for constraint in TransformationDefinitionVersion._meta.constraints
        }
        self.assertIn("tdv_definition_version_uniq", constraint_names)
        self.assertIn("tdv_spec_checksum_hex_chk", constraint_names)
        checksum_unique_constraints = [
            constraint
            for constraint in TransformationDefinitionVersion._meta.constraints
            if isinstance(constraint, models.UniqueConstraint)
            and "specification_checksum" in constraint.fields
        ]
        self.assertEqual(checksum_unique_constraints, [])

    def test_transformation_definition_version_checksum_constraint_rejects_empty_values(
        self,
    ) -> None:
        checksum_constraint = next(
            constraint
            for constraint in TransformationDefinitionVersion._meta.constraints
            if constraint.name == "tdv_spec_checksum_hex_chk"
        )
        version = TransformationDefinitionVersion(
            definition=TransformationDefinition(
                tenant_id=uuid.uuid4(),
                definition_key="audience-workbook",
                name="Audience Workbook",
            ),
            version_number=1,
            specification_json={"spec_version": "1.0"},
        )

        self.assertEqual(version.specification_checksum, "")
        self.assertEqual(
            checksum_constraint.condition.children,
            [("specification_checksum__regex", "^[0-9a-f]{64}$")],
        )
        self.assertNotRegex(version.specification_checksum, r"^[0-9a-f]{64}$")

    def test_transformation_definition_version_status_choices_are_publication_values(
        self,
    ) -> None:
        status_values = {choice[0] for choice in TransformationDefinitionVersionStatus.choices}
        self.assertSetEqual(status_values, {"DRAFT", "PUBLISHED", "DEPRECATED"})

    def test_transformation_definition_version_defaults_to_draft(self) -> None:
        status_field = TransformationDefinitionVersion._meta.get_field("status")
        self.assertEqual(status_field.default, TransformationDefinitionVersionStatus.DRAFT)

    def test_transformation_definition_version_allows_each_status_value(self) -> None:
        definition = TransformationDefinition(
            tenant_id=uuid.uuid4(),
            definition_key="audience-workbook",
            name="Audience Workbook",
        )
        for status in TransformationDefinitionVersionStatus.values:
            version = TransformationDefinitionVersion(
                definition=definition,
                version_number=1,
                status=status,
                specification_json={"spec_version": "1.0"},
            )
            self.assertEqual(version.status, status)

    def test_transformation_definition_version_number_is_unique_per_definition(self) -> None:
        unique_constraint = next(
            constraint
            for constraint in TransformationDefinitionVersion._meta.constraints
            if constraint.name == "tdv_definition_version_uniq"
        )
        self.assertEqual(tuple(unique_constraint.fields), ("definition", "version_number"))

    def test_transformation_definition_version_relationship_uses_versions_related_name(
        self,
    ) -> None:
        definition_field = TransformationDefinitionVersion._meta.get_field("definition")
        self.assertEqual(definition_field.remote_field.related_name, "versions")
        self.assertEqual(definition_field.remote_field.on_delete, models.CASCADE)

    def test_transformation_definition_version_persists_json_specification(self) -> None:
        specification = {
            "spec_version": "1.0",
            "steps": [{"step_key": "inspect", "provider_key": "core.inspect"}],
        }
        version = TransformationDefinitionVersion(
            definition=TransformationDefinition(
                tenant_id=uuid.uuid4(),
                definition_key="audience-workbook",
                name="Audience Workbook",
            ),
            version_number=1,
            specification_json=specification,
        )
        self.assertEqual(version.specification_json, specification)

    def test_transformation_definition_version_calculates_specification_checksum(self) -> None:
        specification = {"spec_version": "1.0", "steps": [{"step_key": "inspect"}]}
        version = TransformationDefinitionVersion(
            definition=TransformationDefinition(
                tenant_id=uuid.uuid4(),
                definition_key="audience-workbook",
                name="Audience Workbook",
            ),
            version_number=1,
            specification_json=specification,
        )

        self.assertEqual(
            version.calculate_specification_checksum(),
            calculate_transformation_specification_checksum(specification),
        )

    def test_transformation_definition_version_refresh_assigns_specification_checksum(
        self,
    ) -> None:
        specification = {"spec_version": "1.0", "steps": [{"step_key": "inspect"}]}
        version = TransformationDefinitionVersion(
            definition=TransformationDefinition(
                tenant_id=uuid.uuid4(),
                definition_key="audience-workbook",
                name="Audience Workbook",
            ),
            version_number=1,
            specification_json=specification,
        )

        version.refresh_specification_checksum()

        self.assertEqual(
            version.specification_checksum,
            calculate_transformation_specification_checksum(specification),
        )

    def test_transformation_definition_version_refresh_does_not_persist_automatically(
        self,
    ) -> None:
        version = TransformationDefinitionVersion(
            definition=TransformationDefinition(
                tenant_id=uuid.uuid4(),
                definition_key="audience-workbook",
                name="Audience Workbook",
            ),
            version_number=1,
            specification_json={"spec_version": "1.0"},
        )

        with patch.object(TransformationDefinitionVersion, "save") as mocked_save:
            version.refresh_specification_checksum()

        mocked_save.assert_not_called()

    def test_transformation_definition_version_does_not_override_save_for_checksum(
        self,
    ) -> None:
        self.assertNotIn("save", TransformationDefinitionVersion.__dict__)

    def test_transformation_definition_version_string_representation_uses_definition_key(
        self,
    ) -> None:
        definition = TransformationDefinition(
            tenant_id=uuid.uuid4(),
            definition_key="audience-workbook",
            name="Audience Workbook",
        )
        version = TransformationDefinitionVersion(definition=definition, version_number=2)
        self.assertEqual(str(version), "audience-workbook v2")

    def test_external_job_provider_reference_uniqueness_exists(self) -> None:
        constraint_names = {constraint.name for constraint in ExternalDatasetJob._meta.constraints}
        self.assertIn("external_job_provider_ref_uniq", constraint_names)

    def test_dataset_query_run_has_expected_indexes(self) -> None:
        index_names = {index.name for index in DatasetQueryRun._meta.indexes}
        self.assertIn("dqr_tenant_recent_idx", index_names)
        self.assertIn("dqr_dataset_recent_idx", index_names)
        self.assertIn("dqr_version_recent_idx", index_names)
        self.assertIn("dqr_status_recent_idx", index_names)

    def test_dataset_query_job_has_expected_fields_and_indexes(self) -> None:
        field_names = {field.name for field in DatasetQueryJob._meta.get_fields()}
        self.assertIn("sql_hash", field_names)
        self.assertIn("sql_text", field_names)
        self.assertIn("result_columns", field_names)
        self.assertIn("result_rows", field_names)
        self.assertIn("error_code", field_names)
        self.assertIn("error_message", field_names)
        self.assertIn("retry_count", field_names)
        self.assertIn("cancellation_requested_at", field_names)
        self.assertIn("idempotency_key", field_names)
        self.assertIn("request_fingerprint", field_names)
        index_names = {index.name for index in DatasetQueryJob._meta.indexes}
        self.assertIn("dqj_tenant_recent_idx", index_names)
        self.assertIn("dqj_dataset_recent_idx", index_names)
        self.assertIn("dqj_version_recent_idx", index_names)
        self.assertIn("dqj_status_recent_idx", index_names)
        self.assertNotIn("timeout_seconds", field_names)
        self.assertNotIn("timed_out_at", field_names)
        constraint_names = {constraint.name for constraint in DatasetQueryJob._meta.constraints}
        self.assertIn("dqj_active_fingerprint_uniq", constraint_names)

    def test_dataset_query_job_status_choices_are_async_contract_values(self) -> None:
        status_values = {choice[0] for choice in DatasetQueryJobStatus.choices}
        self.assertSetEqual(
            status_values,
            {"queued", "running", "completed", "failed", "cancelled"},
        )

    def test_dataset_intelligence_job_has_expected_fields_and_indexes(self) -> None:
        field_names = {field.name for field in DatasetIntelligenceJob._meta.get_fields()}
        self.assertIn("operation", field_names)
        self.assertIn("request_payload_json", field_names)
        self.assertIn("job_spec_json", field_names)
        self.assertIn("decision_trace_json", field_names)
        self.assertIn("logical_result_json", field_names)
        self.assertIn("result_payload_json", field_names)
        self.assertIn("export_plan", field_names)
        self.assertIn("error_code", field_names)
        self.assertIn("error_message", field_names)
        self.assertIn("retry_count", field_names)
        self.assertIn("cancellation_requested_at", field_names)
        self.assertIn("idempotency_key", field_names)
        self.assertIn("request_fingerprint", field_names)
        index_names = {index.name for index in DatasetIntelligenceJob._meta.indexes}
        self.assertIn("dij_tenant_recent_idx", index_names)
        self.assertIn("dij_operation_recent_idx", index_names)
        self.assertIn("dij_status_recent_idx", index_names)
        self.assertIn("dij_dataset_recent_idx", index_names)
        self.assertIn("dij_version_recent_idx", index_names)
        self.assertIn("dij_export_plan_recent_idx", index_names)
        self.assertNotIn("timeout_seconds", field_names)
        self.assertNotIn("timed_out_at", field_names)
        constraint_names = {
            constraint.name for constraint in DatasetIntelligenceJob._meta.constraints
        }
        self.assertIn("dij_active_fingerprint_uniq", constraint_names)

    def test_dataset_intelligence_job_status_choices_are_async_contract_values(self) -> None:
        status_values = {choice[0] for choice in DatasetIntelligenceJobStatus.choices}
        self.assertSetEqual(
            status_values,
            {"queued", "running", "completed", "failed", "cancelled"},
        )

    def test_cancellation_request_timestamps_default_to_none(self) -> None:
        self.assertIsNone(DatasetQueryJob._meta.get_field("cancellation_requested_at").default)
        self.assertIsNone(
            DatasetIntelligenceJob._meta.get_field("cancellation_requested_at").default,
        )
        self.assertIsNone(
            WorkbookIngestionJob._meta.get_field("cancellation_requested_at").default,
        )

    def test_dataset_intelligence_job_operation_choices_are_supported_values(self) -> None:
        operation_values = {choice[0] for choice in DatasetIntelligenceJobOperation.choices}
        self.assertSetEqual(
            operation_values,
            {"analysis", "chart", "export_plan", "artifact_generation", "transformation"},
        )

    def test_workbook_ingestion_job_has_expected_fields_and_indexes(self) -> None:
        field_names = {field.name for field in WorkbookIngestionJob._meta.get_fields()}
        self.assertIn("request_payload_json", field_names)
        self.assertIn("result_payload_json", field_names)
        self.assertIn("retry_count", field_names)
        self.assertIn("cancellation_requested_at", field_names)
        self.assertIn("idempotency_key", field_names)
        self.assertIn("request_fingerprint", field_names)
        self.assertIn("workbook_group", field_names)
        index_names = {index.name for index in WorkbookIngestionJob._meta.indexes}
        self.assertIn("wbij_tenant_recent_idx", index_names)
        self.assertIn("wbij_status_recent_idx", index_names)
        self.assertIn("wbij_group_recent_idx", index_names)
        self.assertNotIn("timeout_seconds", field_names)
        self.assertNotIn("timed_out_at", field_names)
        constraint_names = {
            constraint.name for constraint in WorkbookIngestionJob._meta.constraints
        }
        self.assertIn("wbij_active_fprint_uniq", constraint_names)

    def test_workbook_ingestion_job_status_choices_are_async_contract_values(self) -> None:
        status_values = {choice[0] for choice in WorkbookIngestionJobStatus.choices}
        self.assertSetEqual(
            status_values,
            {
                "submitted",
                "queued",
                "running",
                "completed",
                "completed_with_skips",
                "failed",
                "cancelled",
            },
        )

    def test_dataset_analysis_artifact_has_json_fields(self) -> None:
        field_names = {field.name for field in DatasetAnalysisArtifact._meta.get_fields()}
        self.assertIn("insights_json", field_names)
        self.assertIn("queries_used_json", field_names)

    def test_dataset_chart_artifact_has_json_fields(self) -> None:
        field_names = {field.name for field in DatasetChartArtifact._meta.get_fields()}
        self.assertIn("chart_spec_json", field_names)
        self.assertIn("source_query_json", field_names)

    def test_dataset_export_plan_has_expected_indexes(self) -> None:
        field_names = {field.name for field in DatasetExportPlan._meta.get_fields()}
        self.assertIn("export_plan_json", field_names)
        index_names = {index.name for index in DatasetExportPlan._meta.indexes}
        self.assertIn("dep_tenant_recent_idx", index_names)
        self.assertIn("dep_dataset_recent_idx", index_names)
        self.assertIn("dep_version_recent_idx", index_names)
        self.assertIn("dep_type_recent_idx", index_names)

    def test_dataset_generated_artifact_has_expected_fields_and_indexes(self) -> None:
        field_names = {field.name for field in DatasetGeneratedArtifact._meta.get_fields()}
        self.assertIn("export_plan", field_names)
        self.assertIn("storage_uri", field_names)
        index_names = {index.name for index in DatasetGeneratedArtifact._meta.indexes}
        self.assertIn("dga_tenant_recent_idx", index_names)
        self.assertIn("dga_dataset_recent_idx", index_names)
        self.assertIn("dga_version_recent_idx", index_names)
        self.assertIn("dga_plan_recent_idx", index_names)
        self.assertIn("dga_type_recent_idx", index_names)
