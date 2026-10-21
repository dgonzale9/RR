"""Map a verified existing Registry Namespace UUID to an existing Workspace."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from resource_registry.services.registry_namespace_exceptions import RegistryNamespaceServiceError
from resource_registry.services.registry_namespace_provisioning_service import (
    RegistryNamespaceProvisioningService,
)


class Command(BaseCommand):
    """Adapt a trusted verified mapping to Registry Namespace provisioning."""

    help = "Map a verified existing Resource Registry namespace UUID to a Workspace."

    def add_arguments(self, parser: CommandParser) -> None:
        """Register verified legacy mapping arguments."""
        parser.add_argument("--workspace-id", type=int, required=True)
        parser.add_argument("--namespace-id", type=str, required=True)
        parser.add_argument("--confirm-verified-mapping", action="store_true")
        parser.add_argument("--expected-organization-id", type=int, default=None)

    def handle(self, *args: Any, **options: Any) -> None:
        """Invoke verified provisioning only after explicit operator confirmation."""
        _ = args
        if not options.get("confirm_verified_mapping"):
            raise CommandError("Verified Registry Namespace mapping confirmation is required.")
        try:
            result = RegistryNamespaceProvisioningService().provision_verified_existing_namespace(
                workspace_id=int(options["workspace_id"]),
                namespace_id=str(options["namespace_id"]),
                expected_organization_id=options.get("expected_organization_id"),
            )
        except RegistryNamespaceServiceError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(
            self.style.SUCCESS(
                "Verified Registry Namespace "
                f"{result.id} mapped to Workspace {result.workspace_id}.",
            ),
        )
