"""List host-authorized namespaces and datasets with compatible row counts."""

from drf_spectacular.utils import extend_schema
from rest_framework.request import Request
from rest_framework.response import Response

from resource_registry.api.management.views import DatasetManagementView
from resource_registry.management_domain.authorization import DatasetManagementAuthorization
from resource_registry.management_domain.catalog import DatasetCatalogService


class NamespaceCatalogView(DatasetManagementView):
    """List only host-authorized Registry destinations."""

    @extend_schema(responses={200: dict})
    def get(self, request: Request) -> Response:
        """Return admitted destinations for selectors."""
        authority = DatasetManagementAuthorization(self.actor(request), self.organization(request))
        rows = []
        for namespace in authority.namespaces().order_by("created_at")[:200]:
            owner = namespace.workspace if namespace.workspace_id else namespace.organization
            if owner is None:
                continue
            rows.append(
                {
                    "id": str(namespace.id),
                    "name": owner.name,
                    "slug": owner.slug,
                    "workspace_id": namespace.workspace_id,
                    "organization_id": namespace.organization_id,
                    "owner_type": "workspace" if namespace.workspace_id else "organization",
                }
            )
        return Response({"results": rows, "count": len(rows)})


class DatasetCatalogView(DatasetManagementView):
    """Filter admission before search, count and pagination."""

    @extend_schema(responses={200: dict})
    def get(self, request: Request) -> Response:
        """Return one catalog page while count keeps its existing page-size meaning."""
        return Response(
            DatasetCatalogService(self.actor(request), self.organization(request)).list_datasets(
                request.query_params
            )
        )
