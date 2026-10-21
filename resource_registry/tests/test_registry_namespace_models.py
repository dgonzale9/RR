"""Database-backed Registry Namespace aggregate contract tests."""

from __future__ import annotations

import uuid

from django.db import models
from django.db.models.deletion import ProtectedError
from django.test import TestCase

from resource_registry.exceptions import (
    RegistryNamespaceDeletionNotAllowedError,
    RegistryNamespaceImmutableFieldError,
)
from resource_registry.models import RegistryNamespace
from resource_registry.tests.registry_namespace_test_helpers import create_workspace


class RegistryNamespaceModelTests(TestCase):
    """Verify Registry Namespace schema and aggregate immutability."""

    def setUp(self) -> None:
        """Create two independent Workspace owners."""
        self.workspace = create_workspace(suffix="model-one")
        self.other_workspace = create_workspace(suffix="model-two")
        self.namespace = RegistryNamespace(
            id=uuid.uuid4(),
            workspace=self.workspace,
        )
        self.namespace.save(force_insert=True)

    def test_model_contains_only_approved_domain_fields(self) -> None:
        """Keep the aggregate schema minimal while supporting one explicit owner kind."""
        field_names = {field.name for field in RegistryNamespace._meta.get_fields()}
        self.assertSetEqual(
            field_names,
            {
                "id",
                "workspace",
                "organization",
                "created_at",
                "staged_resource_publications",
            },
        )

    def test_uuid_is_primary_key_without_default(self) -> None:
        """Use the canonical UUID as identity without model-generated values."""
        identity_field = RegistryNamespace._meta.get_field("id")
        self.assertTrue(identity_field.primary_key)
        self.assertFalse(identity_field.editable)
        self.assertIs(identity_field.default, models.NOT_PROVIDED)

    def test_workspace_relation_is_explicit_one_to_one_with_protection(self) -> None:
        """Expose the approved reverse accessor and deletion protection."""
        workspace_field = RegistryNamespace._meta.get_field("workspace")
        self.assertTrue(workspace_field.one_to_one)
        self.assertEqual(workspace_field.remote_field.related_name, "resource_registry_namespace")
        self.assertIs(workspace_field.remote_field.on_delete, models.PROTECT)
        self.assertTrue(workspace_field.null)
        self.assertEqual(self.workspace.resource_registry_namespace, self.namespace)
        with self.assertRaises(ProtectedError):
            self.workspace.delete()

    def test_persisted_uuid_cannot_change(self) -> None:
        """Reject aggregate identity rotation and preserve durable state."""
        original_id = self.namespace.id
        replacement_id = uuid.uuid4()
        self.namespace.id = replacement_id
        with self.assertRaises(RegistryNamespaceImmutableFieldError):
            self.namespace.save()
        self.assertTrue(RegistryNamespace.objects.filter(pk=original_id).exists())
        self.assertFalse(RegistryNamespace.objects.filter(pk=replacement_id).exists())

    def test_persisted_workspace_and_workspace_id_cannot_change(self) -> None:
        """Reject ownership reassignment through either relation representation."""
        self.namespace.workspace = self.other_workspace
        with self.assertRaises(RegistryNamespaceImmutableFieldError):
            self.namespace.save()
        reloaded = RegistryNamespace.objects.get(pk=self.namespace.id)
        self.assertEqual(reloaded.workspace_id, self.workspace.id)

        reloaded.workspace_id = self.other_workspace.id
        with self.assertRaises(RegistryNamespaceImmutableFieldError):
            reloaded.save()
        reloaded.refresh_from_db()
        self.assertEqual(reloaded.workspace_id, self.workspace.id)

    def test_unchanged_namespace_can_be_persisted(self) -> None:
        """Allow persistence when aggregate identity and ownership are unchanged."""
        self.namespace.save()
        self.namespace.refresh_from_db()
        self.assertEqual(self.namespace.workspace_id, self.workspace.id)

    def test_ordinary_delete_paths_are_rejected(self) -> None:
        """Prevent application deletion through instance and QuerySet operations."""
        with self.assertRaises(RegistryNamespaceDeletionNotAllowedError):
            self.namespace.delete()
        with self.assertRaises(RegistryNamespaceDeletionNotAllowedError):
            RegistryNamespace.objects.filter(pk=self.namespace.pk).delete()

    def test_bulk_identity_and_ownership_mutation_is_rejected(self) -> None:
        """Prevent common QuerySet and bulk-update invariant bypasses."""
        with self.assertRaises(RegistryNamespaceImmutableFieldError):
            RegistryNamespace.objects.filter(pk=self.namespace.pk).update(
                workspace_id=self.other_workspace.id,
            )
        self.namespace.workspace_id = self.other_workspace.id
        with self.assertRaises(RegistryNamespaceImmutableFieldError):
            RegistryNamespace.objects.bulk_update([self.namespace], ["workspace"])

        replacement = RegistryNamespace(
            id=self.namespace.id,
            workspace=self.other_workspace,
        )
        with self.assertRaises(RegistryNamespaceImmutableFieldError):
            RegistryNamespace.objects.bulk_create(
                [replacement],
                update_conflicts=True,
                update_fields=["workspace"],
                unique_fields=["id"],
            )
        self.namespace.refresh_from_db()
        self.assertEqual(self.namespace.workspace_id, self.workspace.id)
