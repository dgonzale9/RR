"""Registry Namespace management-command adapter tests."""

from __future__ import annotations

import io
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from resource_registry.management.commands.provision_registry_namespace import (
    Command as ProvisionCommand,
)
from resource_registry.services.registry_namespace_exceptions import (
    RegistryNamespaceWorkspaceConflictError,
)


class RegistryNamespaceManagementCommandTests(SimpleTestCase):
    """Verify that commands remain thin provisioning-service adapters."""

    def test_normal_command_delegates_without_accepting_namespace_id(self) -> None:
        """Keep UUID generation out of the normal command contract."""
        result = SimpleNamespace(
            id=str(uuid.uuid4()),
            workspace_id=12,
            organization_id=3,
            created_at="2026-07-18T00:00:00+00:00",
        )
        stdout = io.StringIO()
        parser = ProvisionCommand().create_parser("manage.py", "provision_registry_namespace")
        option_destinations = {action.dest for action in parser._actions}
        self.assertNotIn("namespace_id", option_destinations)
        with patch(
            "resource_registry.management.commands.provision_registry_namespace."
            "RegistryNamespaceProvisioningService.provision_new_namespace",
            return_value=result,
        ) as provision:
            call_command(
                "provision_registry_namespace",
                "--workspace-id",
                "12",
                "--expected-organization-id",
                "3",
                stdout=stdout,
            )
        provision.assert_called_once_with(workspace_id=12, expected_organization_id=3)
        self.assertIn(result.id, stdout.getvalue())

    def test_organization_command_delegates_to_organization_provisioning(self) -> None:
        """Provision an Organization namespace without a synthetic Workspace."""
        result = SimpleNamespace(
            id=str(uuid.uuid4()),
            workspace_id=None,
            organization_id=3,
            created_at="2026-07-18T00:00:00+00:00",
        )
        with patch(
            "resource_registry.management.commands.provision_registry_namespace."
            "RegistryNamespaceProvisioningService.provision_new_organization_namespace",
            return_value=result,
        ) as provision:
            call_command(
                "provision_registry_namespace",
                "--organization-id",
                "3",
                stdout=io.StringIO(),
            )
        provision.assert_called_once_with(organization_id=3)

    def test_verified_command_requires_confirmation_before_service_invocation(self) -> None:
        """Require an explicit trusted migration confirmation."""
        namespace_id = str(uuid.uuid4())
        with (
            patch(
                "resource_registry.management.commands.map_verified_registry_namespace."
                "RegistryNamespaceProvisioningService.provision_verified_existing_namespace",
            ) as provision,
            self.assertRaises(CommandError),
        ):
            call_command(
                "map_verified_registry_namespace",
                "--workspace-id",
                "12",
                "--namespace-id",
                namespace_id,
            )
        provision.assert_not_called()

    def test_verified_command_delegates_exact_mapping(self) -> None:
        """Delegate verified UUID preservation without command-owned business logic."""
        namespace_id = str(uuid.uuid4())
        result = SimpleNamespace(
            id=namespace_id,
            workspace_id=12,
            organization_id=3,
            created_at="2026-07-18T00:00:00+00:00",
        )
        with patch(
            "resource_registry.management.commands.map_verified_registry_namespace."
            "RegistryNamespaceProvisioningService.provision_verified_existing_namespace",
            return_value=result,
        ) as provision:
            call_command(
                "map_verified_registry_namespace",
                "--workspace-id",
                "12",
                "--namespace-id",
                namespace_id,
                "--confirm-verified-mapping",
                "--expected-organization-id",
                "3",
                stdout=io.StringIO(),
            )
        provision.assert_called_once_with(
            workspace_id=12,
            namespace_id=namespace_id,
            expected_organization_id=3,
        )

    def test_domain_failure_is_translated_to_command_error(self) -> None:
        """Keep stable application failures behind the CLI boundary."""
        with (
            patch(
                "resource_registry.management.commands.provision_registry_namespace."
                "RegistryNamespaceProvisioningService.provision_new_namespace",
                side_effect=RegistryNamespaceWorkspaceConflictError("conflict"),
            ),
            self.assertRaises(CommandError),
        ):
            call_command("provision_registry_namespace", "--workspace-id", "12")
