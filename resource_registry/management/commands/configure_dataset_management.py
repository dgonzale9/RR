"""Enable an organization only after legacy ingestion writers have drained."""

from django.core.management.base import BaseCommand, CommandError, CommandParser

from resource_registry.management_domain.configuration import (
    DatasetManagementConfigurationError,
    configure_dataset_management,
)


class Command(BaseCommand):
    """Control the dataset maintenance pilot without changing staff or SSO."""

    help = "Enable or disable dataset maintenance mutations for one organization."

    def add_arguments(self, parser: CommandParser) -> None:
        """Require an explicit organization and mutation mode."""
        parser.add_argument("--organization-id", type=int, required=True)
        parser.add_argument("--mode", choices=["enable", "disable"], required=True)

    def handle(self, *args: object, **options: object) -> None:
        """Persist the pilot flag after checking for legacy jobs that can still write."""
        enabled = options["mode"] == "enable"
        try:
            configuration = configure_dataset_management(
                organization_id=int(str(options["organization_id"])), enabled=enabled
            )
        except DatasetManagementConfigurationError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(
            f"Dataset maintenance mutations {'enabled' if enabled else 'disabled'} for organization {configuration.organization_id}."
        )
