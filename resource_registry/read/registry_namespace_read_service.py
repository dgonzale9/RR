"""Read immutable Workspace-owned Registry Namespace mappings."""

from __future__ import annotations

from uuid import UUID

from resource_registry.models import RegistryNamespace
from resource_registry.read.dtos import RegistryNamespaceSummary
from resource_registry.read.exceptions import (
    RegistryNamespaceNotFoundError,
    WorkspaceRegistryNamespaceNotFoundError,
)
from Organization.models import Workspace


class RegistryNamespaceReadService:
    """Return immutable summaries for persisted Registry Namespace mappings."""

    def get_by_namespace_id(self, namespace_id: UUID | str) -> RegistryNamespaceSummary:
        """Retrieve a Registry Namespace summary by canonical UUID.

        Args:
            namespace_id: Registry Namespace UUID.

        Returns:
            The immutable Registry Namespace summary.

        Raises:
            RegistryNamespaceNotFoundError: If the UUID is invalid or unknown.
        """
        parsed_namespace_id = self._parse_namespace_id(namespace_id)
        namespace = (
            RegistryNamespace.objects.select_related("workspace__organization")
            .filter(pk=parsed_namespace_id)
            .first()
        )
        if namespace is None:
            raise RegistryNamespaceNotFoundError("Registry Namespace was not found.")
        return self._to_summary(namespace)

    def get_by_workspace_id(self, workspace_id: int) -> RegistryNamespaceSummary:
        """Retrieve a Registry Namespace summary by Workspace identifier.

        Args:
            workspace_id: Existing Workspace primary key.

        Returns:
            The immutable Registry Namespace summary.

        Raises:
            WorkspaceRegistryNamespaceNotFoundError: If no mapping exists.
        """
        namespace = (
            RegistryNamespace.objects.select_related("workspace__organization")
            .filter(workspace_id=workspace_id)
            .first()
        )
        if namespace is None:
            raise WorkspaceRegistryNamespaceNotFoundError(
                "Workspace has no Registry Namespace mapping.",
            )
        return self._to_summary(namespace)

    def get_by_workspace(self, workspace: Workspace) -> RegistryNamespaceSummary:
        """Retrieve a Registry Namespace summary for an existing Workspace.

        Args:
            workspace: Existing Workspace instance.

        Returns:
            The immutable Registry Namespace summary.

        Raises:
            WorkspaceRegistryNamespaceNotFoundError: If the Workspace is unsaved or unmapped.
        """
        if workspace.pk is None:
            raise WorkspaceRegistryNamespaceNotFoundError(
                "Workspace has no Registry Namespace mapping.",
            )
        return self.get_by_workspace_id(int(workspace.pk))

    @staticmethod
    def _parse_namespace_id(namespace_id: UUID | str) -> UUID:
        try:
            return UUID(str(namespace_id))
        except (AttributeError, TypeError, ValueError) as error:
            raise RegistryNamespaceNotFoundError("Registry Namespace was not found.") from error

    @staticmethod
    def _to_summary(namespace: RegistryNamespace) -> RegistryNamespaceSummary:
        return RegistryNamespaceSummary(
            id=str(namespace.id),
            workspace_id=int(namespace.workspace_id),
            organization_id=int(namespace.workspace.organization_id),
            created_at=namespace.created_at.isoformat(),
        )
