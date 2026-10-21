from __future__ import annotations

from collections.abc import Collection
from typing import cast
from uuid import UUID, uuid4

from rest_framework.request import Request

from resource_registry.application.contracts import AuthorizationError, ValidationError
from resource_registry.application.read import TransformationReadContext
from resource_registry.application.services import TransformationExecutionCallerContext
from resource_registry.authorization.exceptions import RegistryAuthorizationContextError
from resource_registry.authorization.registry_authorization_context_service import (
    RegistryAuthorizationContextService,
)
from resource_registry.namespace_selection import RegistryScopeSelectionError
from resource_registry.scope_adapters import (
    get_request_scope_policy,
    select_request_registry_namespace,
)

TENANT_HEADER = "X-Tenant-ID"
CORRELATION_HEADER = "X-Correlation-ID"
_MAXIMUM_CORRELATION_ID_LENGTH = 128


def build_execution_caller_context(
    *,
    request: Request,
    tenant_id: str | None,
    correlation_id: str,
) -> TransformationExecutionCallerContext:
    """Build a trusted application execution context from authenticated request state."""
    resolved_tenant_id = resolve_tenant_id(request=request, requested_tenant_id=tenant_id)
    return TransformationExecutionCallerContext(
        tenant_id=resolved_tenant_id,
        actor=_get_actor(request=request),
        principal_kind="USER",
        permissions=_get_permissions(request=request),
        created_by_id=_get_user_identifier(request=request),
    )


def build_read_context(*, request: Request) -> TransformationReadContext:
    """Build a tenant-scoped application read context from authenticated request state."""
    correlation_id = get_correlation_id(request=request)
    return TransformationReadContext(
        tenant_id=resolve_tenant_id(request=request),
        actor=_get_actor(request=request),
        permissions=_get_permissions(request=request),
        correlation_id=correlation_id,
    )


def resolve_tenant_id(*, request: Request, requested_tenant_id: str | None = None) -> str:
    """Admit a namespace under the rollout policy without relaxing authorization.

    Args:
        request: Authenticated request carrying trusted host and Workspace context.
        requested_tenant_id: Optional body selector.

    Returns:
        Canonical authorized namespace UUID.

    Raises:
        AuthorizationError: If destination access is denied.
        ValidationError: If destination selectors conflict or are invalid.
    """
    try:
        return get_request_scope_policy(request).resolve_selection(
            select_current=lambda: select_request_registry_namespace(
                request=request,
                namespace_selectors=(requested_tenant_id, getattr(request, "tenant_id", None)),
            ).namespace_id,
            select_legacy=lambda: _resolve_legacy_tenant_id(
                request=request,
                requested_tenant_id=requested_tenant_id,
            ),
            surface="transformation",
        )
    except RegistryScopeSelectionError as error:
        error_class = AuthorizationError if error.is_authorization_failure else ValidationError
        raise error_class(
            code=error.code,
            public_message=str(error),
            correlation_id=get_correlation_id(request=request),
        ) from error


def _resolve_legacy_tenant_id(*, request: Request, requested_tenant_id: str | None = None) -> str:
    """Preserve authorized legacy selection during staged rollout."""
    correlation_id = get_correlation_id(request=request)
    try:
        registry_authorization = RegistryAuthorizationContextService().build_context(
            user=request.user,
            organization=getattr(request, "organization", None),
        )
    except RegistryAuthorizationContextError as error:
        raise AuthorizationError(
            code="transformation_tenant_not_authorized",
            public_message="The transformation tenant is not authorized.",
            correlation_id=correlation_id,
        ) from error

    candidates = (
        getattr(request, "tenant_id", None),
        _get_session_value(request=request, key="resource_registry_tenant_id"),
        _get_session_value(request=request, key="tenant_id"),
        request.headers.get(TENANT_HEADER),
    )
    selection_hint = next((str(value).strip() for value in candidates if value), None)
    if (
        requested_tenant_id is not None
        and selection_hint is not None
        and requested_tenant_id != selection_hint
    ):
        raise AuthorizationError(
            code="transformation_tenant_not_authorized",
            public_message="The transformation tenant is not authorized.",
            correlation_id=correlation_id,
        )
    selected_namespace = selection_hint or requested_tenant_id
    authorized_namespaces = registry_authorization.authorized_namespaces
    if selected_namespace is None:
        if len(authorized_namespaces.namespace_ids) == 1:
            return str(next(iter(authorized_namespaces.namespace_ids)))
        if len(authorized_namespaces.namespace_ids) > 1:
            raise ValidationError(
                code="transformation_tenant_selection_required",
                public_message="An explicit transformation tenant selection is required.",
                correlation_id=correlation_id,
            )
        raise AuthorizationError(
            code="transformation_tenant_not_authorized",
            public_message="The transformation tenant is not authorized.",
            correlation_id=correlation_id,
        )
    if not _is_canonical_uuid(selected_namespace):
        raise ValidationError(
            code="invalid_transformation_tenant",
            public_message="A valid transformation tenant is required.",
            correlation_id=correlation_id,
        )
    canonical_namespace = str(UUID(selected_namespace))
    if not authorized_namespaces.contains_namespace(canonical_namespace):
        raise AuthorizationError(
            code="transformation_tenant_not_authorized",
            public_message="The transformation tenant is not authorized.",
            correlation_id=correlation_id,
        )
    return canonical_namespace


def get_correlation_id(*, request: Request) -> str:
    """Return the caller correlation header or create a transport correlation identifier."""
    supplied = request.headers.get(CORRELATION_HEADER)
    if supplied is None:
        return str(uuid4())
    value = supplied.strip()
    if not value or len(value) > _MAXIMUM_CORRELATION_ID_LENGTH:
        raise ValidationError(
            code="invalid_transformation_correlation_id",
            public_message="The transformation correlation ID is invalid.",
        )
    return value


def _get_actor(*, request: Request) -> str:
    user = request.user
    username = getattr(user, "get_username", lambda: "")()
    actor = str(username or getattr(user, "pk", "")).strip()
    if not actor:
        raise AuthorizationError(
            code="transformation_execution_not_authorized",
            public_message="The transformation request is not authorized.",
            correlation_id=get_correlation_id(request=request),
        )
    return actor


def _get_permissions(*, request: Request) -> frozenset[str]:
    getter = getattr(request.user, "get_all_permissions", None)
    if not callable(getter):
        return frozenset()
    permissions = cast(Collection[object], getter())
    return frozenset(str(permission) for permission in permissions)


def _get_user_identifier(*, request: Request) -> int | None:
    identifier = getattr(request.user, "pk", None)
    return identifier if isinstance(identifier, int) else None


def _get_session_value(*, request: Request, key: str) -> object | None:
    session = getattr(request, "session", None)
    return session.get(key) if session is not None else None


def _is_canonical_uuid(value: str) -> bool:
    try:
        return str(UUID(value)) == value.lower()
    except (AttributeError, ValueError):
        return False
