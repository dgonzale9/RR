"""Admit Registry Namespaces for approved synchronous Built-In Functions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast
from uuid import UUID

from django.contrib.auth.models import AnonymousUser, User

from Organization.models import Organization
from resource_registry.assistant.persona_workspace_scope import (
    PERSONA_WORKSPACE_SELECTION_REQUIRED_MESSAGE,
    PersonaWorkspaceScopeError,
    resolve_persona_workspace_scope,
)
from resource_registry.authorization.exceptions import RegistryAuthorizationContextError
from resource_registry.authorization.registry_authorization_context_service import (
    RegistryAuthorizationContextService,
)
from resource_registry.conversation_state import clear_registry_context
from resource_registry.dataset_scope import get_visible_workspace_dataset_ids
from resource_registry.namespace_selection import RegistryScopeSelectionError
from resource_registry.performance.instrumentation import (
    RegistryPerformanceStage,
    instrument_registry_function,
)
from resource_registry.read_auth.exceptions import DataRegistryAuthorizationError
from resource_registry.scope_adapters import (
    get_request_scope_policy,
    select_request_registry_namespace,
    validate_registry_destination,
)

INVALID_TENANT_MESSAGE = "tenant_id must be a valid UUID."
TENANT_SELECTION_CONFLICT_MESSAGE = "Conflicting tenant_id selectors are not allowed."
TENANT_SELECTION_REQUIRED_MESSAGE = "An explicit tenant_id selection is required."
TENANT_NOT_AUTHORIZED_MESSAGE = "The selected tenant_id is not authorized."
WORKSPACE_SELECTION_REQUIRED_MESSAGE = (
    "An explicit Resource Registry workspace selection is required."
)


@dataclass(frozen=True, kw_only=True)
class AdmittedBifNamespace:
    """Carry canonical namespace identity and immutable submission provenance."""

    namespace_id: str
    organization_id: int
    workspace_id: int | None
    actor_id: int
    is_persona_workspace_scoped: bool = False
    enforced_dataset_ids: frozenset[str] | None = None


class ReadOnlyBifNamespaceAdmissionService:
    """Authorize one legacy namespace selector for approved synchronous BIF execution."""

    def __init__(
        self,
        *,
        authorization_context_service: RegistryAuthorizationContextService | None = None,
    ) -> None:
        """Initialize namespace admission with an optional authorization service.

        Args:
            authorization_context_service: Workspace authorization orchestration dependency.
        """
        self._authorization_context_service = (
            authorization_context_service or RegistryAuthorizationContextService()
        )

    def admit_namespace(
        self,
        *,
        request: object,
        requested_namespace_id: object,
    ) -> str:
        """Return the canonical authorized namespace selected for one BIF call.

        Args:
            request: Authenticated invocation request with trusted Organization context.
            requested_namespace_id: Legacy BIF namespace argument used only as a selector.

        Returns:
            Canonical authorized Registry Namespace UUID.

        Raises:
            DataRegistryAuthorizationError: If authentication, selection, or authorization fails.
        """
        return self.admit_namespace_with_provenance(
            request=request,
            requested_namespace_id=requested_namespace_id,
        ).namespace_id

    @instrument_registry_function(RegistryPerformanceStage.NAMESPACE_ADMISSION)
    def admit_namespace_with_provenance(
        self,
        *,
        request: object,
        requested_namespace_id: object,
    ) -> AdmittedBifNamespace:
        """Return canonical namespace identity with immutable owner provenance.

        Args:
            request: Authenticated invocation request with trusted Organization context.
            requested_namespace_id: Legacy BIF namespace argument used only as a selector.

        Returns:
            Immutable canonical namespace, Organization, optional Workspace, and actor identifiers.

        Raises:
            DataRegistryAuthorizationError: If authentication, selection, or authorization fails.
        """
        user = cast(User | AnonymousUser | None, getattr(request, "user", None))
        organization = cast(Organization | None, getattr(request, "organization", None))
        try:
            authorization_context = self._authorization_context_service.build_context(
                user=user,
                organization=organization,
            )
        except RegistryAuthorizationContextError as error:
            clear_registry_context(request)
            raise DataRegistryAuthorizationError(TENANT_NOT_AUTHORIZED_MESSAGE) from error

        try:
            persona_scope = resolve_persona_workspace_scope(
                request=request,
                authorization_context=authorization_context,
            )
        except PersonaWorkspaceScopeError as error:
            clear_registry_context(request)
            if str(error) == PERSONA_WORKSPACE_SELECTION_REQUIRED_MESSAGE:
                raise DataRegistryAuthorizationError(
                    WORKSPACE_SELECTION_REQUIRED_MESSAGE
                ) from error
            raise DataRegistryAuthorizationError(TENANT_NOT_AUTHORIZED_MESSAGE) from error
        if get_request_scope_policy(request, assistant=True).is_enforced:
            namespace_selectors = (
                (requested_namespace_id,)
                if self._has_selector_value(requested_namespace_id)
                else ()
            )
            try:
                selected = select_request_registry_namespace(
                    request=request,
                    namespace_selectors=namespace_selectors,
                    assistant=True,
                    authorization_context=authorization_context,
                )
            except RegistryScopeSelectionError as error:
                clear_registry_context(request)
                raise DataRegistryAuthorizationError(
                    "Select the requested authorized Workspace in the interface, then retry."
                ) from error
            session = getattr(request, "session", None)
            previous_namespace = (
                session.get("resource_registry_tenant_id") if session is not None else None
            )
            if previous_namespace != selected.namespace_id:
                clear_registry_context(request)
            setattr(request, "resource_registry_tenant_id", selected.namespace_id)  # noqa: B010
            if session is not None:
                session["resource_registry_tenant_id"] = selected.namespace_id
                session["resource_registry_workspace_id"] = selected.workspace_id
            return AdmittedBifNamespace(
                namespace_id=selected.namespace_id,
                organization_id=selected.organization_id,
                workspace_id=selected.workspace_id,
                actor_id=authorization_context.authorized_workspaces.user_id,
                is_persona_workspace_scoped=persona_scope is not None,
                enforced_dataset_ids=(
                    self._get_workspace_dataset_ids(
                        namespace_id=selected.namespace_id,
                        workspace_id=persona_scope.workspace_id,
                    )
                    if persona_scope is not None
                    else None
                ),
            )
        if persona_scope is not None:
            validate_registry_destination(
                request=request,
                namespace_id=persona_scope.namespace_id,
                surface="bif",
                assistant=True,
                authorization_context=authorization_context,
            )
            return AdmittedBifNamespace(
                namespace_id=persona_scope.namespace_id,
                organization_id=authorization_context.authorized_workspaces.organization_id,
                workspace_id=persona_scope.workspace_id,
                actor_id=authorization_context.authorized_workspaces.user_id,
                is_persona_workspace_scoped=True,
                enforced_dataset_ids=self._get_workspace_dataset_ids(
                    namespace_id=persona_scope.namespace_id,
                    workspace_id=persona_scope.workspace_id,
                ),
            )

        selectors = self._get_metadata_selection_values(
            request=request,
        )
        resolved_metadata_namespace = self._resolve_metadata_namespace(selectors=selectors)
        requested_selector = self._resolve_requested_namespace_selector(
            requested_namespace_id=requested_namespace_id,
        )

        authorized_namespaces = authorization_context.authorized_namespaces
        selected_namespace = requested_selector
        if selected_namespace is None:
            selected_namespace = self._resolve_workspace_namespace(
                request=request,
                authorized_namespaces=authorized_namespaces,
            )
        if selected_namespace is None:
            selected_namespace = self._resolve_organization_namespace(
                request=request,
                authorized_namespaces=authorized_namespaces,
            )
        if selected_namespace is None:
            selected_namespace = resolved_metadata_namespace
        if selected_namespace is None and not authorized_namespaces.namespace_ids:
            raise DataRegistryAuthorizationError(TENANT_NOT_AUTHORIZED_MESSAGE)
        if selected_namespace is None:
            raise DataRegistryAuthorizationError(TENANT_SELECTION_REQUIRED_MESSAGE)

        if not authorized_namespaces.contains_namespace(selected_namespace):
            raise DataRegistryAuthorizationError(TENANT_NOT_AUTHORIZED_MESSAGE)
        workspace_id = authorized_namespaces.resolve_workspace(selected_namespace)
        validate_registry_destination(
            request=request,
            namespace_id=selected_namespace,
            surface="bif",
            assistant=True,
            authorization_context=authorization_context,
        )
        actor_id = getattr(user, "pk", None)
        organization_id = getattr(organization, "pk", None)
        if not isinstance(actor_id, int) or not isinstance(organization_id, int):
            raise DataRegistryAuthorizationError(TENANT_NOT_AUTHORIZED_MESSAGE)
        return AdmittedBifNamespace(
            namespace_id=selected_namespace,
            organization_id=organization_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
        )

    @staticmethod
    def _get_workspace_dataset_ids(*, namespace_id: str, workspace_id: int) -> frozenset[str]:
        """Return visible active Dataset IDs for one admitted Persona Workspace.

        Args:
            namespace_id: Canonical Registry namespace admitted for the invocation.
            workspace_id: Workspace owner of the admitted namespace.

        Returns:
            Immutable Dataset IDs that the Workspace-scoped Persona may access.
        """
        return get_visible_workspace_dataset_ids(
            namespace_id=namespace_id,
            workspace_id=workspace_id,
        )

    @classmethod
    def _get_metadata_selection_values(
        cls,
        *,
        request: object,
    ) -> tuple[tuple[str, object], ...]:
        values = (
            ("request_resource_registry", getattr(request, "resource_registry_tenant_id", None)),
            (
                "session_resource_registry",
                cls._get_session_value(request=request, key="resource_registry_tenant_id"),
            ),
            (
                "persona_metadata",
                cls._get_persona_metadata_value(request=request, key="resource_registry_tenant_id"),
            ),
        )
        return tuple((source, value) for source, value in values if cls._has_selector_value(value))

    @classmethod
    def _resolve_metadata_namespace(
        cls,
        *,
        selectors: tuple[tuple[str, object], ...],
    ) -> str | None:
        if not selectors:
            return None

        canonical_selectors: list[str] = []
        for _source, value in selectors:
            canonical_selectors.append(cls._canonicalize_selector(value))
        if len(set(canonical_selectors)) > 1:
            raise DataRegistryAuthorizationError(TENANT_SELECTION_CONFLICT_MESSAGE)
        return canonical_selectors[0]

    @staticmethod
    def _resolve_workspace_namespace(
        *, request: object, authorized_namespaces: object
    ) -> str | None:
        workspace = getattr(request, "workspace", None)
        workspace_id = getattr(workspace, "pk", None)
        if not isinstance(workspace_id, int):
            workspace_id = getattr(request, "workspace_id", None)
        if not isinstance(workspace_id, int):
            return None

        for mapping in getattr(authorized_namespaces, "mappings", ()):  # pragma: no branch
            if getattr(mapping, "workspace_id", None) == workspace_id:
                return str(mapping.namespace_id)
        return None

    @staticmethod
    def _resolve_organization_namespace(
        *, request: object, authorized_namespaces: object
    ) -> str | None:
        organization = getattr(request, "organization", None)
        if not isinstance(getattr(organization, "pk", None), int):
            return None

        for mapping in getattr(authorized_namespaces, "mappings", ()):  # pragma: no branch
            if getattr(mapping, "workspace_id", None) is None:
                return str(mapping.namespace_id)
        return None

    @staticmethod
    def _get_session_value(*, request: object, key: str) -> object | None:
        session = getattr(request, "session", None)
        getter = getattr(session, "get", None)
        return getter(key) if callable(getter) else None

    @staticmethod
    def _get_persona_metadata_value(*, request: object, key: str) -> object | None:
        persona = getattr(request, "persona", None)
        metadata = getattr(persona, "metadata", None)
        if not isinstance(metadata, dict):
            return None
        return metadata.get(key)

    @staticmethod
    def _has_selector_value(value: object) -> bool:
        return value is not None and bool(str(value).strip())

    @classmethod
    def _resolve_requested_namespace_selector(
        cls,
        *,
        requested_namespace_id: object,
    ) -> str | None:
        if not cls._has_selector_value(requested_namespace_id):
            return None
        try:
            return cls._canonicalize_selector(requested_namespace_id)
        except DataRegistryAuthorizationError:
            return None

    @staticmethod
    def _canonicalize_selector(value: object) -> str:
        try:
            return str(UUID(str(value).strip()))
        except (AttributeError, TypeError, ValueError) as error:
            raise DataRegistryAuthorizationError(INVALID_TENANT_MESSAGE) from error
