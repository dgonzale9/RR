"""Registry Namespace initial-schema migration contract tests."""

from __future__ import annotations

import importlib

from django.db import connection, migrations, models
from django.db.migrations.executor import MigrationExecutor
from django.test import SimpleTestCase, TransactionTestCase


class RegistryNamespaceMigrationTests(SimpleTestCase):
    """Verify the consolidated initial migration retains namespace ownership rules."""

    def setUp(self) -> None:
        """Load the consolidated Resource Registry initial migration module."""
        self.migration = importlib.import_module(
            "resource_registry.migrations.0001_initial",
        ).Migration

    def test_migration_is_the_initial_schema(self) -> None:
        """Declare the full Resource Registry state as one initial migration."""
        self.assertTrue(self.migration.initial)
        self.assertIn(
            ("organization", "0006_resource_registry_enablement"),
            self.migration.dependencies,
        )

    def test_migration_creates_registry_namespace(self) -> None:
        """Create the durable Registry Namespace aggregate in the initial schema."""
        operation = next(
            item
            for item in self.migration.operations
            if isinstance(item, migrations.CreateModel) and item.name == "RegistryNamespace"
        )
        self.assertEqual(operation.name, "RegistryNamespace")
        self.assertFalse(
            any(isinstance(item, migrations.RunPython) for item in self.migration.operations),
        )

    def test_migration_fields_match_owner_contract(self) -> None:
        """Persist canonical identity with either Workspace or Organization ownership."""
        operation = next(
            item
            for item in self.migration.operations
            if isinstance(item, migrations.CreateModel) and item.name == "RegistryNamespace"
        )
        fields = dict(operation.fields)
        self.assertSetEqual(set(fields), {"id", "created_at", "workspace", "organization"})
        identity = fields["id"]
        self.assertIsInstance(identity, models.UUIDField)
        self.assertTrue(identity.primary_key)
        self.assertIs(identity.default, models.NOT_PROVIDED)
        workspace = fields["workspace"]
        self.assertIsInstance(workspace, models.OneToOneField)
        self.assertEqual(workspace.remote_field.related_name, "resource_registry_namespace")
        self.assertIs(workspace.remote_field.on_delete, models.PROTECT)
        organization = fields["organization"]
        self.assertIsInstance(organization, models.OneToOneField)
        self.assertEqual(
            organization.remote_field.related_name,
            "resource_registry_organization_namespace",
        )
        self.assertIs(organization.remote_field.on_delete, models.PROTECT)


class RegistryNamespaceMigrationApplicationTests(TransactionTestCase):
    """Verify structural reversal and reapplication in the test database."""

    @staticmethod
    def _restore_resource_registry_leaf_migrations() -> None:
        """Restore the Resource Registry schema to its current migration leaf."""
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes("resource_registry"))

    def test_migration_reverses_and_reapplies_structurally(self) -> None:
        """Remove and restore the Registry Namespace table from the initial schema."""
        try:
            executor = MigrationExecutor(connection)
            executor.migrate([("resource_registry", None)])
            self.assertNotIn(
                "resource_registry_registrynamespace", connection.introspection.table_names()
            )

            executor = MigrationExecutor(connection)
            executor.migrate([("resource_registry", "0001_initial")])
            self.assertIn(
                "resource_registry_registrynamespace", connection.introspection.table_names()
            )
        finally:
            self._restore_resource_registry_leaf_migrations()
