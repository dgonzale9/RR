"""Create Registry Namespace identity when a Resource Registry owner is enabled."""

from __future__ import annotations

import uuid

from django.db import IntegrityError, transaction

from resource_registry.models import RegistryNamespace
from resource_registry.services.registry_namespace_exceptions import (
    RegistryNamespacePersistenceConflictError,
)
from Organization.models import Organization, Workspace


class RegistryNamespaceEnablementService:
    """Ensure immutable namespace identity for enabled Organization and Workspace owners."""

    def ensure_organization_namespace(self, organization_id: int) -> RegistryNamespace:
        """Create or return the namespace owned by one enabled Organization.

        Args:
            organization_id: Persisted Organization primary key.

        Returns:
            The immutable Organization-owned namespace.

        Raises:
            RegistryNamespacePersistenceConflictError: If namespace persistence cannot complete.
        """
        try:
            with transaction.atomic():
                organization = Organization.objects.select_for_update().get(pk=organization_id)
                namespace, _created = RegistryNamespace.objects.get_or_create(
                    organization=organization,
                    defaults={"id": uuid.uuid4()},
                )
        except (IntegrityError, Organization.DoesNotExist) as error:
            raise RegistryNamespacePersistenceConflictError(
                "Registry Namespace could not be created for the enabled Organization.",
            ) from error
        return namespace

    def ensure_workspace_namespace(self, workspace_id: int) -> RegistryNamespace:
        """Create or return the namespace owned by one enabled Workspace.

        Args:
            workspace_id: Persisted Workspace primary key.

        Returns:
            The immutable Workspace-owned namespace.

        Raises:
            RegistryNamespacePersistenceConflictError: If namespace persistence cannot complete.
        """
        try:
            with transaction.atomic():
                workspace = (
                    Workspace.objects.select_for_update()
                    .select_related("organization")
                    .get(pk=workspace_id)
                )
                namespace, _created = RegistryNamespace.objects.get_or_create(
                    workspace=workspace,
                    defaults={"id": uuid.uuid4()},
                )
        except (IntegrityError, Workspace.DoesNotExist) as error:
            raise RegistryNamespacePersistenceConflictError(
                "Registry Namespace could not be created for the enabled Workspace.",
            ) from error
        return namespace
