from __future__ import annotations

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse

from resource_registry.api.transformation.permissions import CORRELATION_HEADER, TENANT_HEADER
from resource_registry.api.transformation.serializers import ApplicationErrorSerializer

TENANT_PARAMETER = OpenApiParameter(
    name=TENANT_HEADER,
    type=str,
    location=OpenApiParameter.HEADER,
    required=False,
    description=(
        "Namespace UUID assertion. In workspace or organization scope it must match the selected "
        "owner; explicit scope requires a destination selector. Legacy rollout keeps the previous "
        "authorized namespace selection behavior."
    ),
)
CORRELATION_PARAMETER = OpenApiParameter(
    name=CORRELATION_HEADER,
    type=str,
    location=OpenApiParameter.HEADER,
    required=False,
    description="Optional read-request correlation identifier; generated when omitted.",
)


def application_error_responses(*status_codes: int) -> dict[int, OpenApiResponse]:
    """Build OpenAPI responses that all reference the stable application error schema."""
    return {
        status_code: OpenApiResponse(
            response=ApplicationErrorSerializer,
            description="Stable sanitized transformation application error.",
        )
        for status_code in status_codes
    }
