"""Share operational dataset enablement rules between Admin and management commands."""

from django.db import router, transaction
from django.db.models import Q

from Organization.models import Organization
from resource_registry.models import (
    DatasetManagementConfiguration,
    DatasetRegistrationJob,
    RegistryNamespace,
    WorkbookIngestionJob,
)


class DatasetManagementConfigurationError(ValueError):
    """Report an actionable refusal to change dataset management configuration."""


def require_legacy_ingestion_drained(organization_id: int, *, using: str) -> None:
    """Reject enablement while legacy ingestion can still replace dataset content.

    Args:
        organization_id: Organization whose organization and workspace namespaces are checked.
        using: Database alias shared with the caller's transaction.

    Raises:
        DatasetManagementConfigurationError: Legacy registration or workbook jobs are active.
    """
    namespaces = (
        RegistryNamespace.objects.using(using)
        .filter(Q(organization_id=organization_id) | Q(workspace__organization_id=organization_id))
        .values("id")
    )
    active = ["submitted", "queued", "running"]
    if (
        DatasetRegistrationJob.objects.using(using)
        .filter(tenant_id__in=namespaces, status__in=active)
        .exists()
        or WorkbookIngestionJob.objects.using(using)
        .filter(tenant_id__in=namespaces, status__in=active)
        .exists()
    ):
        raise DatasetManagementConfigurationError(
            "Drain legacy registration and workbook jobs before enabling maintenance."
        )


def configure_dataset_management(
    *, organization_id: int, enabled: bool
) -> DatasetManagementConfiguration:
    """Persist enablement atomically without replacing policy or weakening legacy protection.

    Args:
        organization_id: Existing organization authorized by the caller's entry point.
        enabled: Whether to admit dataset maintenance mutations.

    Returns:
        The durable management configuration after applying the requested state.

    Raises:
        DatasetManagementConfigurationError: The organization is missing, Resource Registry
            is disabled, or legacy jobs have not drained.
    """
    alias = router.db_for_write(Organization)
    with transaction.atomic(using=alias):
        organization = (
            Organization.objects.using(alias).select_for_update().filter(pk=organization_id).first()
        )
        if organization is None:
            raise DatasetManagementConfigurationError("Organization not found.")
        if enabled:
            require_legacy_ingestion_drained(organization_id, using=alias)
            if not organization.resource_registry_enabled:
                raise DatasetManagementConfigurationError(
                    "Enable Resource Registry for the organization first."
                )
        configuration, _ = DatasetManagementConfiguration.objects.using(alias).get_or_create(
            organization=organization
        )
        configuration.enabled = enabled
        configuration.legacy_updates_blocked = configuration.legacy_updates_blocked or enabled
        configuration.save(using=alias, update_fields=["enabled", "legacy_updates_blocked"])
        return configuration
