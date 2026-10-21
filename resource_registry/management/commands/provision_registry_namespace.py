"""Provision a server-generated Registry Namespace for a Workspace or Organization."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from resource_registry.services.registry_namespace_exceptions import RegistryNamespaceServiceError
from resource_registry.services.registry_namespace_provisioning_service import (
    RegistryNamespaceProvisioningService,
)


class Command(BaseCommand):
    """Adapt trusted command-line input to Registry Namespace provisioning."""

    help = "Provision a server-generated Resource Registry namespace for a Workspace or Organization."

    def add_arguments(self, parser: CommandParser) -> None:
        """Register normal provisioning arguments."""
        owner_group = parser.add_mutually_exclusive_group(required=True)
        owner_group.add_argument("--workspace-id", type=int)
        owner_group.add_argument("--organization-id", type=int)
        parser.add_argument("--expected-organization-id", type=int, default=None)

    def handle(self, *args: Any, **options: Any) -> None:
        """Invoke the provisioning application service and report a sanitized result."""
        _ = args
        try:
            if options.get("workspace_id") is not None:
                result = RegistryNamespaceProvisioningService().provision_new_namespace(
                    workspace_id=int(options["workspace_id"]),
                    expected_organization_id=options.get("expected_organization_id"),
                )
            else:
                result = RegistryNamespaceProvisioningService().provision_new_organization_namespace(
                    organization_id=int(options["organization_id"]),
                )
        except RegistryNamespaceServiceError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(
            self.style.SUCCESS(
                "Registry Namespace "
                f"{result.id} provisioned for "
                f"{'Workspace ' + str(result.workspace_id) if result.workspace_id else 'Organization ' + str(result.organization_id)}.",
            ),
        )
