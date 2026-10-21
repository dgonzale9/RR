from __future__ import annotations

from resource_registry.models import TransformationDefinition
from resource_registry.read.dtos import (
    TransformationDefinitionDetails,
    TransformationDefinitionSummary,
    to_iso,
)
from resource_registry.read.exceptions import TransformationDefinitionNotFoundError


class TransformationDefinitionReadService:
    """Tenant-scoped read service for transformation definition metadata."""

    MAX_LIMIT = 100

    def list_definitions(
        self,
        *,
        tenant_id: str,
        include_archived: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TransformationDefinitionSummary]:
        """List transformation definitions for a tenant.

        Args:
            tenant_id: Tenant identifier that owns the definitions.
            include_archived: Whether logically archived definitions are included.
            limit: Maximum number of definitions to return before repository cap.
            offset: Number of definitions to skip before returning results.

        Returns:
            Tenant-scoped transformation definition summaries.
        """
        queryset = TransformationDefinition.objects.filter(tenant_id=tenant_id)
        if not include_archived:
            queryset = queryset.filter(archived_at__isnull=True)
        ordered = queryset.order_by("definition_key", "id")
        definitions = self._paginate(list(ordered), limit=limit, offset=offset)
        return [self._to_summary(definition) for definition in definitions]

    def get_definition_by_id(
        self,
        *,
        tenant_id: str,
        definition_id: str,
        include_archived: bool = True,
    ) -> TransformationDefinitionDetails:
        """Fetch a transformation definition by tenant-scoped UUID.

        Args:
            tenant_id: Tenant identifier that owns the definition.
            definition_id: Transformation definition UUID.
            include_archived: Whether logically archived definitions are addressable.

        Returns:
            Transformation definition details.

        Raises:
            TransformationDefinitionNotFoundError: If no matching definition is visible.
        """
        definition = self._get_definition(
            tenant_id=tenant_id,
            include_archived=include_archived,
            id=definition_id,
        )
        return self._to_details(definition)

    def get_definition_by_key(
        self,
        *,
        tenant_id: str,
        definition_key: str,
        include_archived: bool = True,
    ) -> TransformationDefinitionDetails:
        """Fetch a transformation definition by tenant-scoped functional key.

        Args:
            tenant_id: Tenant identifier that owns the definition.
            definition_key: Tenant-scoped transformation definition key.
            include_archived: Whether logically archived definitions are addressable.

        Returns:
            Transformation definition details.

        Raises:
            TransformationDefinitionNotFoundError: If no matching definition is visible.
        """
        definition = self._get_definition(
            tenant_id=tenant_id,
            include_archived=include_archived,
            definition_key=definition_key,
        )
        return self._to_details(definition)

    def _get_definition(
        self,
        *,
        tenant_id: str,
        include_archived: bool,
        **filters: str,
    ) -> TransformationDefinition:
        """Fetch a visible transformation definition model for internal service composition."""
        queryset = TransformationDefinition.objects.filter(tenant_id=tenant_id, **filters)
        if not include_archived:
            queryset = queryset.filter(archived_at__isnull=True)
        definition = queryset.first()
        if definition is None:
            raise TransformationDefinitionNotFoundError(
                "Transformation definition not found for tenant.",
            )
        return definition

    def _paginate(
        self,
        items: list[TransformationDefinition],
        *,
        limit: int,
        offset: int,
    ) -> list[TransformationDefinition]:
        """Return a bounded slice following repository read-service pagination."""
        safe_limit = min(max(limit, 1), self.MAX_LIMIT)
        safe_offset = max(offset, 0)
        return items[safe_offset : safe_offset + safe_limit]

    def _to_summary(
        self,
        definition: TransformationDefinition,
    ) -> TransformationDefinitionSummary:
        """Map a transformation definition model to a lightweight read DTO."""
        return TransformationDefinitionSummary(
            id=str(definition.id),
            tenant_id=str(definition.tenant_id),
            definition_key=definition.definition_key,
            name=definition.name,
            archived_at=to_iso(definition.archived_at),
            created_at=str(to_iso(definition.created_at)),
            updated_at=str(to_iso(definition.updated_at)),
        )

    def _to_details(
        self,
        definition: TransformationDefinition,
    ) -> TransformationDefinitionDetails:
        """Map a transformation definition model to a detailed read DTO."""
        return TransformationDefinitionDetails(
            id=str(definition.id),
            tenant_id=str(definition.tenant_id),
            definition_key=definition.definition_key,
            name=definition.name,
            description=definition.description,
            created_by_id=str(definition.created_by_id) if definition.created_by_id else None,
            archived_at=to_iso(definition.archived_at),
            created_at=str(to_iso(definition.created_at)),
            updated_at=str(to_iso(definition.updated_at)),
        )
