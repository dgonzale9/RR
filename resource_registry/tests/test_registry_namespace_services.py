"""Registry Namespace read, resolution, and provisioning service tests."""

from __future__ import annotations

import inspect
import uuid
from dataclasses import FrozenInstanceError
from unittest.mock import patch

from django.db import IntegrityError
from django.test import TestCase

from resource_registry.models import RegistryNamespace
from resource_registry.read.exceptions import (
    RegistryNamespaceNotFoundError,
    WorkspaceRegistryNamespaceNotFoundError,
)
from resource_registry.read.registry_namespace_read_service import RegistryNamespaceReadService
from resource_registry.services.registry_namespace_exceptions import (
    RegistryNamespaceIdentifierConflictError,
    RegistryNamespaceInvalidIdentifierError,
    RegistryNamespaceOrganizationMismatchError,
    RegistryNamespacePersistenceConflictError,
    RegistryNamespaceWorkspaceConflictError,
    RegistryNamespaceWorkspaceNotFoundError,
)
from resource_registry.services.registry_namespace_provisioning_service import (
    RegistryNamespaceProvisioningService,
)
from resource_registry.services.registry_namespace_resolution_service import (
    RegistryNamespaceResolutionService,
)
from resource_registry.tests.registry_namespace_test_helpers import create_workspace


class RegistryNamespaceServiceTests(TestCase):
    """Verify namespace application and read-service behavior."""

    def setUp(self) -> None:
        """Create two independent Workspace owners and services."""
        self.workspace = create_workspace(suffix="service-one")
        self.other_workspace = create_workspace(suffix="service-two")
        self.provisioning = RegistryNamespaceProvisioningService()
        self.read = RegistryNamespaceReadService()
        self.resolution = RegistryNamespaceResolutionService()

    def test_normal_provisioning_generates_uuid_inside_service(self) -> None:
        """Generate identity internally and return an immutable DTO."""
        generated_id = uuid.uuid4()
        with patch(
            "resource_registry.services.registry_namespace_provisioning_service.uuid.uuid4",
            return_value=generated_id,
        ):
            result = self.provisioning.provision_new_namespace(
                self.workspace.id,
                expected_organization_id=self.workspace.organization_id,
            )
        self.assertEqual(result.id, str(generated_id))
        self.assertEqual(result.workspace_id, self.workspace.id)
        with self.assertRaises(FrozenInstanceError):
            result.id = str(uuid.uuid4())  # type: ignore[misc]
        parameters = inspect.signature(self.provisioning.provision_new_namespace).parameters
        self.assertNotIn("namespace_id", parameters)

    def test_provisioning_service_exposes_only_approved_lifecycle_operations(self) -> None:
        """Exclude update, reassignment, rotation, replacement, and deletion operations."""
        public_operations = {
            name
            for name, member in RegistryNamespaceProvisioningService.__dict__.items()
            if not name.startswith("_") and callable(member)
        }
        self.assertSetEqual(
            public_operations,
            {
                "provision_new_namespace",
                "provision_new_organization_namespace",
                "provision_verified_existing_namespace",
            },
        )

    def test_verified_provisioning_preserves_exact_uuid(self) -> None:
        """Preserve a trusted migration identity without generating another UUID."""
        verified_id = uuid.uuid4()
        result = self.provisioning.provision_verified_existing_namespace(
            self.workspace.id,
            verified_id,
        )
        self.assertEqual(result.id, str(verified_id))
        self.assertTrue(RegistryNamespace.objects.filter(pk=verified_id).exists())

    def test_verified_provisioning_preserves_zero_uuid(self) -> None:
        """Preserve every valid UUID value without replacing false-like identity."""
        verified_id = uuid.UUID(int=0)
        with patch(
            "resource_registry.services.registry_namespace_provisioning_service.uuid.uuid4",
        ) as generate_namespace_id:
            result = self.provisioning.provision_verified_existing_namespace(
                self.workspace.id,
                verified_id,
            )
        generate_namespace_id.assert_not_called()
        self.assertEqual(result.id, str(verified_id))

    def test_verified_provisioning_rejects_invalid_uuid(self) -> None:
        """Reject malformed legacy mapping identity."""
        with self.assertRaises(RegistryNamespaceInvalidIdentifierError):
            self.provisioning.provision_verified_existing_namespace(
                self.workspace.id,
                "not-a-uuid",
            )

    def test_provisioning_rejects_unknown_workspace_and_organization_mismatch(self) -> None:
        """Fail explicitly for invalid ownership context."""
        with self.assertRaises(RegistryNamespaceWorkspaceNotFoundError):
            self.provisioning.provision_new_namespace(999_999)
        with self.assertRaises(RegistryNamespaceOrganizationMismatchError):
            self.provisioning.provision_new_namespace(
                self.workspace.id,
                expected_organization_id=self.other_workspace.organization_id,
            )
        self.assertFalse(RegistryNamespace.objects.filter(workspace=self.workspace).exists())

    def test_duplicate_workspace_and_uuid_provisioning_are_conflicts(self) -> None:
        """Return explicit non-idempotent conflict outcomes."""
        existing = self.provisioning.provision_new_namespace(self.workspace.id)
        with self.assertRaises(RegistryNamespaceWorkspaceConflictError):
            self.provisioning.provision_new_namespace(self.workspace.id)
        with self.assertRaises(RegistryNamespaceIdentifierConflictError):
            self.provisioning.provision_verified_existing_namespace(
                self.other_workspace.id,
                existing.id,
            )

    def test_organization_provisioning_creates_one_immutable_owner_mapping(self) -> None:
        """Create an Organization-owned namespace without requiring a Workspace."""
        result = self.provisioning.provision_new_organization_namespace(
            self.workspace.organization_id,
        )
        namespace = RegistryNamespace.objects.get(pk=result.id)
        self.assertIsNone(namespace.workspace_id)
        self.assertEqual(namespace.organization_id, self.workspace.organization_id)

    def test_integrity_failure_rolls_back_and_is_translated_after_atomic_block(self) -> None:
        """Translate an unclassified persistence conflict without partial durable state."""
        with (
            patch.object(RegistryNamespace, "save", side_effect=IntegrityError("conflict")),
            self.assertRaises(RegistryNamespacePersistenceConflictError),
        ):
            self.provisioning.provision_new_namespace(self.workspace.id)
        self.assertFalse(RegistryNamespace.objects.filter(workspace=self.workspace).exists())

    def test_read_service_reads_by_namespace_and_workspace(self) -> None:
        """Return deterministic immutable summaries for every approved lookup."""
        provisioned = self.provisioning.provision_new_namespace(self.workspace.id)
        by_id = self.read.get_by_namespace_id(provisioned.id)
        by_workspace_id = self.read.get_by_workspace_id(self.workspace.id)
        by_workspace = self.read.get_by_workspace(self.workspace)
        self.assertEqual(by_id, by_workspace_id)
        self.assertEqual(by_id, by_workspace)
        self.assertEqual(by_id.organization_id, self.workspace.organization_id)

    def test_read_service_translates_unknown_and_missing_mappings(self) -> None:
        """Do not leak persistence-specific missing relation failures."""
        with self.assertRaises(RegistryNamespaceNotFoundError):
            self.read.get_by_namespace_id(uuid.uuid4())
        with self.assertRaises(RegistryNamespaceNotFoundError):
            self.read.get_by_namespace_id("invalid")
        with self.assertRaises(WorkspaceRegistryNamespaceNotFoundError):
            self.read.get_by_workspace(self.workspace)

    def test_resolution_is_bidirectional_and_organization_aware(self) -> None:
        """Map Workspace and namespace without performing user authorization."""
        provisioned = self.provisioning.provision_new_namespace(self.workspace.id)
        namespace_id = self.resolution.resolve_namespace_id_for_workspace(
            self.workspace,
            expected_organization_id=self.workspace.organization_id,
        )
        resolved_workspace = self.resolution.resolve_workspace_for_namespace(
            provisioned.id,
            expected_organization_id=self.workspace.organization_id,
        )
        self.assertEqual(str(namespace_id), provisioned.id)
        self.assertEqual(resolved_workspace.id, self.workspace.id)
        with self.assertRaises(RegistryNamespaceOrganizationMismatchError):
            self.resolution.resolve_workspace_for_namespace(
                provisioned.id,
                expected_organization_id=self.other_workspace.organization_id,
            )

    def test_resolution_translates_missing_workspace_and_namespace(self) -> None:
        """Fail explicitly for every missing mapping direction."""
        with self.assertRaises(RegistryNamespaceWorkspaceNotFoundError):
            self.resolution.resolve_namespace_id_for_workspace(999_999)
        with self.assertRaises(WorkspaceRegistryNamespaceNotFoundError):
            self.resolution.resolve_namespace_id_for_workspace(self.workspace)
        with self.assertRaises(RegistryNamespaceNotFoundError):
            self.resolution.resolve_workspace_for_namespace(uuid.uuid4())
