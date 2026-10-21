"""Synchronize Resource Registry namespace identity with durable owner enablement."""

from __future__ import annotations

from typing import cast

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from resource_registry.services.registry_namespace_enablement_service import (
    RegistryNamespaceEnablementService,
)
from Organization.models import Organization, Workspace


def _is_enablement_transition(
    instance: Organization | Workspace,
    *,
    update_fields: frozenset[str] | None,
) -> bool:
    """Return whether the saved owner transitioned from disabled to enabled.

    Args:
        instance: Organization or Workspace about to be persisted.
        update_fields: Explicit model fields included in the pending save, if constrained.

    Returns:
        True only when namespace provisioning is required.
    """
    if update_fields is not None and "resource_registry_enabled" not in update_fields:
        return False
    if not instance.resource_registry_enabled:
        return False
    if instance.pk is None:
        return True
    previous_enabled = type(instance).objects.filter(pk=instance.pk).values_list(
        "resource_registry_enabled",
        flat=True,
    ).first()
    return previous_enabled is False


@receiver(pre_save, sender=Organization)
@receiver(pre_save, sender=Workspace)
def capture_resource_registry_enablement_transition(
    sender: type[Organization] | type[Workspace],
    instance: Organization | Workspace,
    **kwargs: object,
) -> None:
    """Capture whether a durable owner requires namespace provisioning.

    Args:
        sender: Persisted owner model class.
        instance: Owner instance about to be saved.
        **kwargs: Signal metadata.
    """
    _ = sender
    raw_update_fields = cast(frozenset[str] | None, kwargs.get("update_fields"))
    update_fields = (
        frozenset(str(field_name) for field_name in raw_update_fields)
        if raw_update_fields is not None
        else None
    )
    instance._requires_resource_registry_namespace = _is_enablement_transition(  # type: ignore[attr-defined]
        instance,
        update_fields=update_fields,
    )


@receiver(post_save, sender=Organization)
@receiver(post_save, sender=Workspace)
def provision_namespace_after_resource_registry_enablement(
    sender: type[Organization] | type[Workspace],
    instance: Organization | Workspace,
    **kwargs: object,
) -> None:
    """Create namespace identity and restore a disabled flag if provisioning fails.

    Args:
        sender: Persisted owner model class.
        instance: Newly enabled owner instance.
        **kwargs: Signal metadata.

    Raises:
        Exception: Re-raise namespace persistence failures after reverting enablement.
    """
    _ = sender, kwargs
    if not getattr(instance, "_requires_resource_registry_namespace", False):
        return
    if not isinstance(instance.pk, int):
        return
    try:
        service = RegistryNamespaceEnablementService()
        if isinstance(instance, Organization):
            service.ensure_organization_namespace(instance.pk)
        else:
            service.ensure_workspace_namespace(instance.pk)
    except Exception:
        type(instance).objects.filter(pk=instance.pk).update(resource_registry_enabled=False)
        instance.resource_registry_enabled = False
        raise
