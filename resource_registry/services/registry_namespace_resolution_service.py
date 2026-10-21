"""Resolve persisted Workspace and Registry Namespace mappings without authorization."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

from resource_registry.models import RegistryNamespace
from resource_registry.read.exceptions import (
    RegistryNamespaceNotFoundError,
    WorkspaceRegistryNamespaceNotFoundError,
)
from resource_registry.services.registry_namespace_exceptions import (
    RegistryNamespaceOrganizationMismatchError,
    RegistryNamespaceWorkspaceNotFoundError,
)
from Organization.models import Workspace


@dataclass(frozen=True)
class RegistryNamespaceMapping:
    """Represent one persisted Workspace-to-namespace mapping without ORM leakage."""

    workspace_id: int | None
    namespace_id: UUID
    organization_id: int


class RegistryNamespaceResolutionService:
    """Resolve only persisted Registry Namespace ownership mappings."""

    def resolve_namespaces_for_workspaces(
        self,
        workspace_ids: Iterable[int],
    ) -> tuple[RegistryNamespaceMapping, ...]:
        """Resolve persisted namespace mappings for a collection of Workspaces.

        Args:
            workspace_ids: Workspace primary keys to resolve.

        Returns:
            Deterministically ordered persisted mappings. Unmapped Workspaces are omitted.
        """
        normalized_workspace_ids = frozenset(int(workspace_id) for workspace_id in workspace_ids)
        if not normalized_workspace_ids:
            return ()
        rows = (
            RegistryNamespace.objects.filter(workspace_id__in=normalized_workspace_ids)
            .order_by("workspace_id")
            .values_list("workspace_id", "id", "workspace__organization_id")
        )
        return tuple(
            RegistryNamespaceMapping(
                workspace_id=int(workspace_id),
                namespace_id=namespace_id,
                organization_id=int(organization_id),
            )
            for workspace_id, namespace_id, organization_id in rows
        )

    def resolve_organization_namespace(
        self,
        organization_id: int,
    ) -> RegistryNamespaceMapping | None:
        """Resolve one persisted Organization-owned namespace without authorization.

        Args:
            organization_id: Organization primary key.

        Returns:
            The Organization namespace mapping, or None when it is not provisioned.
        """
        row = (
            RegistryNamespace.objects.filter(organization_id=organization_id)
            .values_list("id", "organization_id")
            .first()
        )
        if row is None:
            return None
        namespace_id, resolved_organization_id = row
        return RegistryNamespaceMapping(
            workspace_id=None,
            namespace_id=namespace_id,
            organization_id=int(resolved_organization_id),
        )

    def resolve_namespace_id_for_workspace(
        self,
        workspace: Workspace | int,
        expected_organization_id: int | None = None,
    ) -> UUID:
        """Resolve the canonical namespace UUID for an existing Workspace.

        Args:
            workspace: Existing Workspace or its primary key.
            expected_organization_id: Optional Organization ownership assertion.

        Returns:
            The canonical Registry Namespace UUID.

        Raises:
            RegistryNamespaceWorkspaceNotFoundError: If the Workspace does not exist.
            RegistryNamespaceOrganizationMismatchError: If Organization ownership differs.
            WorkspaceRegistryNamespaceNotFoundError: If the Workspace has no mapping.
        """
        resolved_workspace = self._resolve_workspace(workspace)
        self._assert_expected_organization(
            workspace=resolved_workspace,
            expected_organization_id=expected_organization_id,
        )
        namespace = RegistryNamespace.objects.filter(workspace_id=resolved_workspace.pk).first()
        if namespace is None:
            raise WorkspaceRegistryNamespaceNotFoundError(
                "Workspace has no Registry Namespace mapping.",
            )
        return namespace.id

    def resolve_workspace_for_namespace(
        self,
        namespace_id: UUID | str,
        expected_organization_id: int | None = None,
    ) -> Workspace:
        """Resolve the owning Workspace for a Registry Namespace UUID.

        Args:
            namespace_id: Canonical Registry Namespace UUID.
            expected_organization_id: Optional Organization ownership assertion.

        Returns:
            The owning Workspace.

        Raises:
            RegistryNamespaceNotFoundError: If the UUID is invalid or unknown.
            RegistryNamespaceOrganizationMismatchError: If Organization ownership differs.
        """
        parsed_namespace_id = self._parse_namespace_id(namespace_id)
        namespace = (
            RegistryNamespace.objects.select_related("workspace__organization")
            .filter(pk=parsed_namespace_id)
            .first()
        )
        if namespace is None:
            raise RegistryNamespaceNotFoundError("Registry Namespace was not found.")
        if namespace.workspace_id is None:
            raise RegistryNamespaceNotFoundError("Registry Namespace is not Workspace-owned.")
        self._assert_expected_organization(
            workspace=namespace.workspace,
            expected_organization_id=expected_organization_id,
        )
        return namespace.workspace

    @staticmethod
    def _resolve_workspace(workspace: Workspace | int) -> Workspace:
        if isinstance(workspace, Workspace):
            if workspace.pk is None:
                raise RegistryNamespaceWorkspaceNotFoundError("Workspace was not found.")
            return workspace
        resolved_workspace = (
            Workspace.objects.select_related("organization").filter(pk=workspace).first()
        )
        if resolved_workspace is None:
            raise RegistryNamespaceWorkspaceNotFoundError("Workspace was not found.")
        return resolved_workspace

    @staticmethod
    def _assert_expected_organization(
        *,
        workspace: Workspace,
        expected_organization_id: int | None,
    ) -> None:
        if (
            expected_organization_id is not None
            and workspace.organization_id != expected_organization_id
        ):
            raise RegistryNamespaceOrganizationMismatchError(
                "Workspace does not belong to the expected Organization.",
            )

    @staticmethod
    def _parse_namespace_id(namespace_id: UUID | str) -> UUID:
        try:
            return UUID(str(namespace_id))
        except (AttributeError, TypeError, ValueError) as error:
            raise RegistryNamespaceNotFoundError("Registry Namespace was not found.") from error
