"""Build fail-closed Dataset visibility filters from an admitted namespace."""

from __future__ import annotations

from uuid import UUID

from django.db.models import Q, Subquery
from django.db.models.functions import Coalesce

from resource_registry.models import DatasetVisibilityScope, RegistryNamespace


def build_dataset_visibility_filter(*, tenant_id: str, field_prefix: str = "") -> Q:
    """Build the Dataset visibility filter for one Registry namespace.

    Args:
        tenant_id: Registry namespace selected by the trusted authorization boundary.
        field_prefix: ORM relation prefix preceding Dataset fields.

    Returns:
        A filter that admits only mapped Workspace datasets and Organization-global datasets.
    """
    try:
        namespace_id = UUID(str(tenant_id))
    except (TypeError, ValueError, AttributeError):
        return Q(pk__in=())
    namespace = RegistryNamespace.objects.filter(id=namespace_id)
    workspace_id = Subquery(namespace.values("workspace_id")[:1])
    organization_id = Subquery(
        namespace.annotate(
            resolved_organization_id=Coalesce("organization_id", "workspace__organization_id"),
        ).values("resolved_organization_id")[:1]
    )

    def field(name: str) -> str:
        return f"{field_prefix}{name}"

    return Q(
        **{
            field("tenant_id"): namespace_id,
            field("organization_id"): organization_id,
            field("workspace_id"): workspace_id,
            field("visibility_scope"): DatasetVisibilityScope.WORKSPACE,
        },
    ) | Q(
        **{
            field("organization_id"): organization_id,
            field("workspace_id__isnull"): True,
            field("visibility_scope"): DatasetVisibilityScope.ORGANIZATION,
        },
    )
