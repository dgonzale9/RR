"""Provision immutable Workspace- and Organization-owned Registry Namespaces."""

from __future__ import annotations

import uuid
from uuid import UUID

from django.db import IntegrityError, transaction

from resource_registry.models import RegistryNamespace
from resource_registry.read.dtos import RegistryNamespaceSummary
from resource_registry.services.registry_namespace_exceptions import (
    RegistryNamespaceIdentifierConflictError,
    RegistryNamespaceInvalidIdentifierError,
    RegistryNamespaceOrganizationConflictError,
    RegistryNamespaceOrganizationMismatchError,
    RegistryNamespaceOrganizationNotFoundError,
    RegistryNamespacePersistenceConflictError,
    RegistryNamespaceWorkspaceConflictError,
    RegistryNamespaceWorkspaceNotFoundError,
)
from Organization.models import Organization, Workspace


class RegistryNamespaceProvisioningService:
    """Own the two approved Registry Namespace aggregate creation use cases."""

    def provision_new_namespace(
        self,
        workspace_id: int,
        expected_organization_id: int | None = None,
    ) -> RegistryNamespaceSummary:
        """Provision a server-generated Registry Namespace for one Workspace.

        Args:
            workspace_id: Existing Workspace primary key.
            expected_organization_id: Optional Organization ownership assertion.

        Returns:
            The created immutable Registry Namespace summary.

        Raises:
            RegistryNamespaceWorkspaceNotFoundError: If the Workspace does not exist.
            RegistryNamespaceOrganizationMismatchError: If Organization ownership differs.
            RegistryNamespaceWorkspaceConflictError: If the Workspace is already provisioned.
            RegistryNamespaceIdentifierConflictError: If the generated UUID conflicts.
            RegistryNamespacePersistenceConflictError: For another persistence conflict.
        """
        return self._provision_namespace(
            workspace_id=workspace_id,
            namespace_id=None,
            expected_organization_id=expected_organization_id,
        )

    def provision_verified_existing_namespace(
        self,
        workspace_id: int,
        namespace_id: UUID | str,
        expected_organization_id: int | None = None,
    ) -> RegistryNamespaceSummary:
        """Provision one explicitly verified existing Registry Namespace UUID.

        Args:
            workspace_id: Existing Workspace primary key.
            namespace_id: Verified legacy Registry Namespace UUID.
            expected_organization_id: Optional Organization ownership assertion.

        Returns:
            The created immutable Registry Namespace summary.

        Raises:
            RegistryNamespaceInvalidIdentifierError: If the UUID is malformed.
            RegistryNamespaceWorkspaceNotFoundError: If the Workspace does not exist.
            RegistryNamespaceOrganizationMismatchError: If Organization ownership differs.
            RegistryNamespaceWorkspaceConflictError: If the Workspace is already provisioned.
            RegistryNamespaceIdentifierConflictError: If the UUID is already mapped.
            RegistryNamespacePersistenceConflictError: For another persistence conflict.
        """
        parsed_namespace_id = self._parse_namespace_id(namespace_id)
        return self._provision_namespace(
            workspace_id=workspace_id,
            namespace_id=parsed_namespace_id,
            expected_organization_id=expected_organization_id,
        )

    def provision_new_organization_namespace(
        self,
        organization_id: int,
    ) -> RegistryNamespaceSummary:
        """Provision a server-generated Registry Namespace for one Organization.

        Args:
            organization_id: Existing Organization primary key.

        Returns:
            The created immutable Organization-owned Registry Namespace summary.

        Raises:
            RegistryNamespaceOrganizationNotFoundError: If the Organization does not exist.
            RegistryNamespaceOrganizationConflictError: If it is already provisioned.
            RegistryNamespacePersistenceConflictError: For another persistence conflict.
        """
        try:
            with transaction.atomic():
                organization = (
                    Organization.objects.select_for_update().filter(pk=organization_id).first()
                )
                if organization is None:
                    raise RegistryNamespaceOrganizationNotFoundError("Organization was not found.")
                if RegistryNamespace.objects.filter(organization_id=organization.pk).exists():
                    raise RegistryNamespaceOrganizationConflictError(
                        "Organization already owns a Registry Namespace.",
                    )
                namespace = RegistryNamespace(id=uuid.uuid4(), organization=organization)
                namespace.save(force_insert=True)
        except IntegrityError as error:
            if RegistryNamespace.objects.filter(organization_id=organization_id).exists():
                raise RegistryNamespaceOrganizationConflictError(
                    "Organization already owns a Registry Namespace.",
                ) from error
            raise RegistryNamespacePersistenceConflictError(
                "Registry Namespace could not be provisioned because of a persistence conflict.",
            ) from error
        return self._to_summary(namespace)

    def _provision_namespace(
        self,
        *,
        workspace_id: int,
        namespace_id: UUID | None,
        expected_organization_id: int | None,
    ) -> RegistryNamespaceSummary:
        selected_namespace_id = namespace_id
        try:
            with transaction.atomic():
                workspace = (
                    Workspace.objects.select_for_update()
                    .select_related("organization")
                    .filter(pk=workspace_id)
                    .first()
                )
                if workspace is None:
                    raise RegistryNamespaceWorkspaceNotFoundError("Workspace was not found.")
                self._assert_expected_organization(
                    workspace=workspace,
                    expected_organization_id=expected_organization_id,
                )
                if RegistryNamespace.objects.filter(workspace_id=workspace.pk).exists():
                    raise RegistryNamespaceWorkspaceConflictError(
                        "Workspace already owns a Registry Namespace.",
                    )
                if selected_namespace_id is None:
                    selected_namespace_id = uuid.uuid4()
                if RegistryNamespace.objects.filter(pk=selected_namespace_id).exists():
                    raise RegistryNamespaceIdentifierConflictError(
                        "Registry Namespace UUID is already mapped.",
                    )
                namespace = RegistryNamespace(
                    id=selected_namespace_id,
                    workspace=workspace,
                )
                namespace.save(force_insert=True)
        except IntegrityError as error:
            self._raise_translated_integrity_error(
                workspace_id=workspace_id,
                namespace_id=selected_namespace_id,
                error=error,
            )
        return self._to_summary(namespace)

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
            raise RegistryNamespaceInvalidIdentifierError(
                "Registry Namespace UUID must be valid.",
            ) from error

    @staticmethod
    def _raise_translated_integrity_error(
        *,
        workspace_id: int,
        namespace_id: UUID | None,
        error: IntegrityError,
    ) -> None:
        if RegistryNamespace.objects.filter(workspace_id=workspace_id).exists():
            raise RegistryNamespaceWorkspaceConflictError(
                "Workspace already owns a Registry Namespace.",
            ) from error
        if namespace_id is not None and RegistryNamespace.objects.filter(pk=namespace_id).exists():
            raise RegistryNamespaceIdentifierConflictError(
                "Registry Namespace UUID is already mapped.",
            ) from error
        raise RegistryNamespacePersistenceConflictError(
            "Registry Namespace could not be provisioned because of a persistence conflict.",
        ) from error

    @staticmethod
    def _to_summary(namespace: RegistryNamespace) -> RegistryNamespaceSummary:
        return RegistryNamespaceSummary(
            id=str(namespace.id),
            workspace_id=(
                int(namespace.workspace_id) if namespace.workspace_id is not None else None
            ),
            organization_id=(
                int(namespace.workspace.organization_id)
                if namespace.workspace_id is not None
                else int(namespace.organization_id)
            ),
            created_at=namespace.created_at.isoformat(),
        )
