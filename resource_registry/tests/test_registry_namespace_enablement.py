"""Lifecycle tests for automatic Registry Namespace provisioning."""

from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth.models import Permission, User
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from Organization.models import Organization, Workspace
from resource_registry.models import RegistryNamespace
from resource_registry.services.registry_namespace_exceptions import (
    RegistryNamespacePersistenceConflictError,
)


def _ensure_owner_object_permissions() -> None:
    """Create permissions required by the secureable-model signal in an isolated test DB."""
    for model in (Organization, Workspace):
        content_type = ContentType.objects.get_for_model(model)
        for action in ("view", "change", "delete"):
            Permission.objects.get_or_create(
                content_type=content_type,
                codename=f"{action}_{model._meta.model_name}",
                defaults={"name": f"Can {action} {model._meta.verbose_name}"},
            )


class RegistryNamespaceEnablementTests(TestCase):
    """Verify enablement creates namespace identity without endpoint participation."""

    def setUp(self) -> None:
        """Create one disabled Organization and Workspace owner pair."""
        _ensure_owner_object_permissions()
        self.user = User.objects.create_user(username="namespace-enablement-user")
        self.organization = Organization.objects.create(
            name="Namespace Enablement Organization",
            slug="namespace-enablement-organization",
            last_modified_by=self.user,
        )
        self.workspace = Workspace.objects.create(
            organization=self.organization,
            name="Namespace Enablement Workspace",
            slug="namespace-enablement-workspace",
            last_modified_by=self.user,
        )

    def test_new_owners_are_disabled_without_namespace(self) -> None:
        """Keep new owners unavailable until an explicit enablement transition."""
        self.assertFalse(self.organization.resource_registry_enabled)
        self.assertFalse(self.workspace.resource_registry_enabled)
        self.assertFalse(
            RegistryNamespace.objects.filter(organization=self.organization).exists()
        )
        self.assertFalse(RegistryNamespace.objects.filter(workspace=self.workspace).exists())

    def test_enabling_organization_creates_one_organization_namespace(self) -> None:
        """Provision one immutable Organization namespace on enablement."""
        self.organization.resource_registry_enabled = True
        self.organization.save(update_fields=["resource_registry_enabled"])
        namespace = RegistryNamespace.objects.get(organization=self.organization)

        self.organization.save(update_fields=["resource_registry_enabled"])

        self.assertIsNone(namespace.workspace_id)
        self.assertEqual(
            RegistryNamespace.objects.filter(organization=self.organization).count(),
            1,
        )

    def test_enabling_workspace_creates_one_workspace_namespace(self) -> None:
        """Provision one immutable Workspace namespace on enablement."""
        self.workspace.resource_registry_enabled = True
        self.workspace.save(update_fields=["resource_registry_enabled"])
        namespace = RegistryNamespace.objects.get(workspace=self.workspace)

        self.workspace.save(update_fields=["resource_registry_enabled"])

        self.assertIsNone(namespace.organization_id)
        self.assertEqual(
            RegistryNamespace.objects.filter(workspace=self.workspace).count(),
            1,
        )

    def test_non_enablement_partial_save_does_not_provision_a_namespace(self) -> None:
        """Ignore an in-memory flag when the save does not persist that field."""
        self.organization.resource_registry_enabled = True
        self.organization.name = "Renamed Namespace Enablement Organization"

        self.organization.save(update_fields=["name"])

        self.organization.refresh_from_db()
        self.assertFalse(self.organization.resource_registry_enabled)
        self.assertFalse(RegistryNamespace.objects.filter(organization=self.organization).exists())

    def test_disabling_owner_preserves_existing_namespace(self) -> None:
        """Retain namespace identity when a Workspace is disabled."""
        self.workspace.resource_registry_enabled = True
        self.workspace.save(update_fields=["resource_registry_enabled"])
        namespace_id = RegistryNamespace.objects.get(workspace=self.workspace).id

        self.workspace.resource_registry_enabled = False
        self.workspace.save(update_fields=["resource_registry_enabled"])

        self.assertFalse(self.workspace.resource_registry_enabled)
        self.assertTrue(RegistryNamespace.objects.filter(pk=namespace_id).exists())

    def test_failed_enablement_reverts_the_organization_flag(self) -> None:
        """Restore the disabled state when namespace provisioning fails."""
        self.organization.resource_registry_enabled = True
        with (
            patch(
                "resource_registry.signals.RegistryNamespaceEnablementService."
                "ensure_organization_namespace",
                side_effect=RegistryNamespacePersistenceConflictError("conflict"),
            ),
            self.assertRaises(RegistryNamespacePersistenceConflictError),
        ):
            self.organization.save(update_fields=["resource_registry_enabled"])

        self.organization.refresh_from_db()
        self.assertFalse(self.organization.resource_registry_enabled)
        self.assertFalse(RegistryNamespace.objects.filter(organization=self.organization).exists())
