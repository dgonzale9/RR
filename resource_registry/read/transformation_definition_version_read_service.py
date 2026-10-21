from __future__ import annotations

from resource_registry.models import (
    TransformationDefinition,
    TransformationDefinitionVersion,
    TransformationDefinitionVersionStatus,
)
from resource_registry.read.dtos import (
    TransformationDefinitionVersionDetails,
    TransformationDefinitionVersionSummary,
    to_iso,
)
from resource_registry.read.exceptions import (
    InvalidTransformationDefinitionVersionStatusFilterError,
    TransformationDefinitionNotFoundError,
    TransformationDefinitionVersionNotFoundError,
)


class TransformationDefinitionVersionReadService:
    """Tenant-scoped read service for transformation definition version metadata."""

    MAX_LIMIT = 100

    def list_versions(  # noqa: PLR0913
        self,
        *,
        tenant_id: str,
        definition_id: str,
        status: str | None = None,
        include_archived_definition: bool = True,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TransformationDefinitionVersionSummary]:
        """List transformation definition versions through a tenant-scoped parent definition.

        Args:
            tenant_id: Tenant identifier that owns the parent definition.
            definition_id: Parent transformation definition UUID.
            status: Optional single lifecycle status filter.
            include_archived_definition: Whether archived parent definitions are addressable.
            limit: Maximum number of versions to return before repository cap.
            offset: Number of versions to skip before returning results.

        Returns:
            Tenant-scoped transformation definition version summaries.

        Raises:
            TransformationDefinitionNotFoundError: If no matching parent definition is visible.
            InvalidTransformationDefinitionVersionStatusFilterError: If status is unsupported.
        """
        definition = self._get_definition(
            tenant_id=tenant_id,
            definition_id=definition_id,
            include_archived_definition=include_archived_definition,
        )
        queryset = TransformationDefinitionVersion.objects.filter(definition=definition)
        if status is not None:
            self._validate_status_filter(status=status)
            queryset = queryset.filter(status=status)
        ordered = queryset.order_by("-version_number", "-created_at", "-id")
        versions = self._paginate(list(ordered), limit=limit, offset=offset)
        return [self._to_summary(version) for version in versions]

    def get_version_by_id(
        self,
        *,
        tenant_id: str,
        definition_id: str,
        version_id: str,
        include_archived_definition: bool = True,
    ) -> TransformationDefinitionVersionDetails:
        """Fetch a transformation definition version by UUID through its parent definition.

        Args:
            tenant_id: Tenant identifier that owns the parent definition.
            definition_id: Parent transformation definition UUID.
            version_id: Transformation definition version UUID.
            include_archived_definition: Whether archived parent definitions are addressable.

        Returns:
            Transformation definition version details.

        Raises:
            TransformationDefinitionNotFoundError: If no matching parent definition is visible.
            TransformationDefinitionVersionNotFoundError: If no matching version is visible.
        """
        definition = self._get_definition(
            tenant_id=tenant_id,
            definition_id=definition_id,
            include_archived_definition=include_archived_definition,
        )
        version = self._get_version(definition=definition, id=version_id)
        return self._to_details(version)

    def get_version_by_number(
        self,
        *,
        tenant_id: str,
        definition_id: str,
        version_number: int,
        include_archived_definition: bool = True,
    ) -> TransformationDefinitionVersionDetails:
        """Fetch a transformation definition version by number through its parent definition.

        Args:
            tenant_id: Tenant identifier that owns the parent definition.
            definition_id: Parent transformation definition UUID.
            version_number: Transformation definition version number.
            include_archived_definition: Whether archived parent definitions are addressable.

        Returns:
            Transformation definition version details.

        Raises:
            TransformationDefinitionNotFoundError: If no matching parent definition is visible.
            TransformationDefinitionVersionNotFoundError: If no matching version is visible.
        """
        definition = self._get_definition(
            tenant_id=tenant_id,
            definition_id=definition_id,
            include_archived_definition=include_archived_definition,
        )
        version = self._get_version(definition=definition, version_number=version_number)
        return self._to_details(version)

    def _get_definition(
        self,
        *,
        tenant_id: str,
        definition_id: str,
        include_archived_definition: bool,
    ) -> TransformationDefinition:
        """Fetch the tenant-scoped parent definition before resolving child versions."""
        queryset = TransformationDefinition.objects.filter(
            tenant_id=tenant_id,
            id=definition_id,
        )
        if not include_archived_definition:
            queryset = queryset.filter(archived_at__isnull=True)
        definition = queryset.first()
        if definition is None:
            raise TransformationDefinitionNotFoundError(
                "Transformation definition not found for tenant.",
            )
        return definition

    def _get_version(
        self,
        *,
        definition: TransformationDefinition,
        **filters: str | int,
    ) -> TransformationDefinitionVersion:
        """Fetch a version through its tenant-scoped parent definition."""
        version = TransformationDefinitionVersion.objects.filter(
            definition=definition,
            **filters,
        ).first()
        if version is None:
            raise TransformationDefinitionVersionNotFoundError(
                "Transformation definition version not found for tenant definition.",
            )
        return version

    def _validate_status_filter(self, *, status: str) -> None:
        """Validate a single lifecycle status filter."""
        if status not in TransformationDefinitionVersionStatus.values:
            raise InvalidTransformationDefinitionVersionStatusFilterError(
                "Unsupported transformation definition version status filter.",
            )

    def _paginate(
        self,
        items: list[TransformationDefinitionVersion],
        *,
        limit: int,
        offset: int,
    ) -> list[TransformationDefinitionVersion]:
        """Return a bounded slice following repository read-service pagination."""
        safe_limit = min(max(limit, 1), self.MAX_LIMIT)
        safe_offset = max(offset, 0)
        return items[safe_offset : safe_offset + safe_limit]

    def _to_summary(
        self,
        version: TransformationDefinitionVersion,
    ) -> TransformationDefinitionVersionSummary:
        """Map a transformation definition version model to a lightweight read DTO."""
        return TransformationDefinitionVersionSummary(
            id=str(version.id),
            definition_id=str(version.definition_id),
            version_number=int(version.version_number),
            status=version.status,
            specification_checksum=version.specification_checksum,
            created_at=str(to_iso(version.created_at)),
            updated_at=str(to_iso(version.updated_at)),
        )

    def _to_details(
        self,
        version: TransformationDefinitionVersion,
    ) -> TransformationDefinitionVersionDetails:
        """Map a transformation definition version model to a detailed read DTO."""
        return TransformationDefinitionVersionDetails(
            id=str(version.id),
            definition_id=str(version.definition_id),
            version_number=int(version.version_number),
            status=version.status,
            specification_checksum=version.specification_checksum,
            specification_json=version.specification_json,
            created_by_id=str(version.created_by_id) if version.created_by_id else None,
            created_at=str(to_iso(version.created_at)),
            updated_at=str(to_iso(version.updated_at)),
        )
