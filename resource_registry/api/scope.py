"""Apply Registry-specific request context and map selection failures to REST."""

from __future__ import annotations

from drf_spectacular.utils import OpenApiParameter
from rest_framework.exceptions import APIException
from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.views import APIView

from BotServer.workspace_permissions import WorkspaceContextPermission
from resource_registry.namespace_selection import RegistryScopeSelectionError
from resource_registry.scope_adapters import REGISTRY_SCOPE_HEADER

REGISTRY_SCOPE_PARAMETER = OpenApiParameter(
    name=REGISTRY_SCOPE_HEADER,
    type=str,
    location=OpenApiParameter.HEADER,
    enum=["workspace", "organization", "explicit"],
    required=False,
    description=(
        "Opt into Registry scope selection. Workspace uses X-Workspace (or its default); "
        "organization uses the host Organization; explicit requires an authorized destination. "
        "Without this header, workspace selection is enforced when the rollout policy is strict."
    ),
)


class RegistryApiContextPermission(BasePermission):
    """Resolve Workspace context only for operations that require the active Workspace."""

    def has_permission(self, request: Request, view: APIView) -> bool:
        """Apply the public W/O resolver after authentication where required.

        Args:
            request: Authenticated DRF request.
            view: Registry view declaring whether it operates on private staging.

        Returns:
            True after the required request context has been resolved.

        Raises:
            APIException: If the mode or required Workspace is invalid.
        """
        mode = request.headers.get(REGISTRY_SCOPE_HEADER, "workspace")
        if mode not in {"workspace", "organization", "explicit"}:
            error = APIException(
                {"code": "registry_scope_invalid", "message": "Invalid Registry scope mode."}
            )
            error.status_code = 400
            raise error
        if getattr(view, "registry_private_intake", False) or mode != "workspace":
            return True
        return WorkspaceContextPermission().has_permission(request, view)


def get_registry_scope_http_status(error: RegistryScopeSelectionError) -> int:
    """Translate a domain selection failure to a REST status code.

    Args:
        error: Transport-independent scope failure.

    Returns:
        Forbidden for authorization failures; bad request for invalid selection.
    """
    return 403 if error.is_authorization_failure else 400
