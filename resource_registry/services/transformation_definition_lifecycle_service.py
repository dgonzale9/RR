from __future__ import annotations

from datetime import datetime
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from resource_registry.models import (
    TransformationDefinition,
    TransformationDefinitionVersion,
    TransformationDefinitionVersionStatus,
)
from resource_registry.utils.json_checksum import TransformationSpecificationChecksumError


class TransformationDefinitionLifecycleError(Exception):
    """Raise when a transformation definition lifecycle operation fails."""


class TransformationDefinitionNotFoundError(TransformationDefinitionLifecycleError):
    """Raise when a transformation definition cannot be found for a tenant."""


class TransformationDefinitionVersionNotFoundError(TransformationDefinitionLifecycleError):
    """Raise when a transformation definition version cannot be found for a definition."""


class ArchivedTransformationDefinitionError(TransformationDefinitionLifecycleError):
    """Raise when an archived definition rejects a publication operation."""


class InvalidTransformationDefinitionVersionTransitionError(
    TransformationDefinitionLifecycleError,
):
    """Raise when a transformation definition version lifecycle transition is invalid."""


class TransformationSpecificationFinalizationError(TransformationDefinitionLifecycleError):
    """Raise when a transformation specification checksum cannot be finalized."""


class TransformationDefinitionLifecycleService:
    """Manage supported transformation definition version lifecycle transitions."""

    def publish_version(
        self,
        *,
        tenant_id: UUID | str,
        definition_id: UUID | str,
        version_id: UUID | str,
        actor: object | None = None,
    ) -> TransformationDefinitionVersion:
        """Publish a draft transformation definition version for a tenant.

        Args:
            tenant_id: Tenant identifier that owns the transformation definition.
            definition_id: Transformation definition identifier.
            version_id: Transformation definition version identifier.
            actor: Optional caller identity reserved for future audit integration.

        Returns:
            The published transformation definition version.

        Raises:
            TransformationDefinitionNotFoundError: If the definition is outside tenant scope.
            TransformationDefinitionVersionNotFoundError: If the version is outside definition
                scope.
            ArchivedTransformationDefinitionError: If the definition is archived.
            InvalidTransformationDefinitionVersionTransitionError: If the target is not draft.
            TransformationSpecificationFinalizationError: If checksum finalization fails.
        """
        with transaction.atomic():
            definition = self._lock_definition_for_tenant(
                tenant_id=tenant_id,
                definition_id=definition_id,
            )
            target_version = self._lock_version_for_definition(
                definition=definition,
                version_id=version_id,
            )

            if definition.archived_at is not None:
                raise ArchivedTransformationDefinitionError(
                    "Archived transformation definitions cannot publish versions.",
                )

            if target_version.status == TransformationDefinitionVersionStatus.PUBLISHED:
                return target_version

            if target_version.status != TransformationDefinitionVersionStatus.DRAFT:
                raise InvalidTransformationDefinitionVersionTransitionError(
                    "Only draft transformation definition versions can be published.",
                )

            self._finalize_specification_checksum(version=target_version)
            published_at = timezone.now()

            self._deprecate_published_siblings(
                definition=definition,
                excluded_version=target_version,
                updated_at=published_at,
            )

            target_version.status = TransformationDefinitionVersionStatus.PUBLISHED
            target_version.updated_at = published_at
            target_version.save(
                update_fields=[
                    "status",
                    "specification_checksum",
                    "updated_at",
                ],
            )
            return target_version

    def deprecate_version(
        self,
        *,
        tenant_id: UUID | str,
        definition_id: UUID | str,
        version_id: UUID | str,
        actor: object | None = None,
    ) -> TransformationDefinitionVersion:
        """Deprecate a published transformation definition version for a tenant.

        Args:
            tenant_id: Tenant identifier that owns the transformation definition.
            definition_id: Transformation definition identifier.
            version_id: Transformation definition version identifier.
            actor: Optional caller identity reserved for future audit integration.

        Returns:
            The deprecated transformation definition version.

        Raises:
            TransformationDefinitionNotFoundError: If the definition is outside tenant scope.
            TransformationDefinitionVersionNotFoundError: If the version is outside definition
                scope.
            InvalidTransformationDefinitionVersionTransitionError: If the target is not published.
        """
        with transaction.atomic():
            definition = self._lock_definition_for_tenant(
                tenant_id=tenant_id,
                definition_id=definition_id,
            )
            target_version = self._lock_version_for_definition(
                definition=definition,
                version_id=version_id,
            )

            if target_version.status == TransformationDefinitionVersionStatus.DEPRECATED:
                return target_version

            if target_version.status != TransformationDefinitionVersionStatus.PUBLISHED:
                raise InvalidTransformationDefinitionVersionTransitionError(
                    "Only published transformation definition versions can be deprecated.",
                )

            target_version.status = TransformationDefinitionVersionStatus.DEPRECATED
            target_version.save(update_fields=["status", "updated_at"])
            return target_version

    def _lock_definition_for_tenant(
        self,
        *,
        tenant_id: UUID | str,
        definition_id: UUID | str,
    ) -> TransformationDefinition:
        """Lock a tenant-scoped transformation definition for lifecycle mutation."""
        try:
            return TransformationDefinition.objects.select_for_update().get(
                tenant_id=tenant_id,
                id=definition_id,
            )
        except TransformationDefinition.DoesNotExist as error:
            raise TransformationDefinitionNotFoundError(
                "Transformation definition not found.",
            ) from error

    def _lock_version_for_definition(
        self,
        *,
        definition: TransformationDefinition,
        version_id: UUID | str,
    ) -> TransformationDefinitionVersion:
        """Lock a transformation definition version through its parent definition."""
        try:
            return TransformationDefinitionVersion.objects.select_for_update().get(
                definition=definition,
                id=version_id,
            )
        except TransformationDefinitionVersion.DoesNotExist as error:
            raise TransformationDefinitionVersionNotFoundError(
                "Transformation definition version not found.",
            ) from error

    def _finalize_specification_checksum(
        self,
        *,
        version: TransformationDefinitionVersion,
    ) -> None:
        """Refresh a version checksum from the currently persisted specification JSON."""
        try:
            version.refresh_specification_checksum()
        except TransformationSpecificationChecksumError as error:
            raise TransformationSpecificationFinalizationError(
                "Transformation specification checksum finalization failed.",
            ) from error

    def _deprecate_published_siblings(
        self,
        *,
        definition: TransformationDefinition,
        excluded_version: TransformationDefinitionVersion,
        updated_at: datetime,
    ) -> None:
        """Deprecate currently published sibling versions except the target version."""
        TransformationDefinitionVersion.objects.select_for_update().filter(
            definition=definition,
            status=TransformationDefinitionVersionStatus.PUBLISHED,
        ).exclude(pk=excluded_version.pk).update(
            status=TransformationDefinitionVersionStatus.DEPRECATED,
            updated_at=updated_at,
        )
