from __future__ import annotations

import importlib

from django.test import SimpleTestCase

from resource_registry.models import (
    TransformationRun,
    TransformationRunOutput,
    TransformationRunStepAttempt,
)


class TransformationRunModelContractTests(SimpleTestCase):
    def test_story_six_uses_attempt_token_without_a_second_claim_token(self) -> None:
        field_names = {field.name for field in TransformationRun._meta.get_fields()}

        self.assertNotIn("claim_token", field_names)
        self.assertIn("claim_owner", field_names)
        self.assertIn("claimed_at", field_names)
        self.assertIn("heartbeat_at", field_names)
        self.assertIn("lease_expires_at", field_names)
        self.assertIn("current_attempt_token", field_names)
        self.assertIn("execution_plan_snapshot_json", field_names)
        self.assertNotIn("execution_plan_json", field_names)

    def test_required_constraints_and_indexes_are_declared(self) -> None:
        run_constraints = {constraint.name for constraint in TransformationRun._meta.constraints}
        step_constraints = {
            constraint.name for constraint in TransformationRunStepAttempt._meta.constraints
        }
        output_constraints = {
            constraint.name for constraint in TransformationRunOutput._meta.constraints
        }

        self.assertIn("tr_active_fingerprint_uniq", run_constraints)
        self.assertIn("tr_tenant_idempotency_uniq", run_constraints)
        self.assertIn("tr_attempt_token_state_chk", run_constraints)
        self.assertIn("tr_claim_state_chk", run_constraints)
        self.assertIn("tr_claim_timestamps_chk", run_constraints)
        self.assertIn("tr_output_pending_attempt_chk", run_constraints)
        self.assertIn("trsa_run_attempt_index_uniq", step_constraints)
        self.assertIn("trsa_run_attempt_step_uniq", step_constraints)
        self.assertIn("trsa_succeeded_checkpoint_chk", step_constraints)
        self.assertIn("tro_run_output_index_uniq", output_constraints)
        self.assertIn("tro_run_logical_name_uniq", output_constraints)
        self.assertIn("tro_checksum_pair_chk", output_constraints)
        self.assertIn("tro_policy_bundle_chk", output_constraints)
        self.assertIn("tro_destination_uniq", output_constraints)
        self.assertIn("tro_registered_shape_chk", output_constraints)
        self.assertIn("tro_cleanup_shape_chk", output_constraints)

    def test_initial_migration_creates_exact_run_models(self) -> None:
        """Persist the complete Transformation Run model set in the initial schema."""
        migration = importlib.import_module("resource_registry.migrations.0001_initial").Migration
        created_models = {
            operation.name
            for operation in migration.operations
            if operation.__class__.__name__ == "CreateModel"
        }

        self.assertTrue(migration.initial)
        self.assertTrue(
            {
                "TransformationRun",
                "TransformationRunStepAttempt",
                "TransformationRunOutput",
            }.issubset(created_models),
        )
