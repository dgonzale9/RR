"""Resolve trusted Resource Registry namespace context for assistant requests."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

from django.db.models import QuerySet

from Organization.models import Workspace
from resource_registry.assistant.persona_workspace_scope import (
    PERSONA_WORKSPACE_SELECTION_SESSION_KEY,
)
from resource_registry.authorization.exceptions import (
    RegistryAuthorizationContextError,
    RegistryAuthorizationOrganizationDisabledError,
)
from resource_registry.authorization.registry_authorization_context_service import (
    RegistryAuthorizationContextService,
)
from resource_registry.conversation_state import (
    REGISTRY_DATASET_CONTEXT_KEYS,
    clear_registry_context,
    clear_registry_session_keys,
)
from resource_registry.namespace_selection import RegistryScopeSelectionError
from resource_registry.scope_adapters import (
    get_request_scope_policy,
    select_request_registry_namespace,
    validate_registry_destination,
)

NamespaceResolutionStatus = Literal[
    "selected", "multiple", "none", "unavailable", "workspace_change_required"
]


@dataclass(frozen=True)
class AssistantWorkspaceChoice:
    """Describe one authorized Workspace or Organization Registry Namespace."""

    workspace_id: int | None
    workspace_name: str
    workspace_slug: str
    namespace_id: str


@dataclass(frozen=True)
class AssistantNamespaceResolution:
    """Carry the canonical namespace resolution result for one assistant request."""

    status: NamespaceResolutionStatus
    selected: AssistantWorkspaceChoice | None = None
    choices: tuple[AssistantWorkspaceChoice, ...] = ()
    selection_changed: bool = False


class AssistantNamespaceContextService:
    """Resolve assistant namespace selection through canonical owner authorization."""

    def __init__(
        self,
        *,
        authorization_context_service: RegistryAuthorizationContextService | None = None,
    ) -> None:
        """Initialize the resolver with an optional authorization service.

        Args:
            authorization_context_service: Canonical Registry authorization context builder.
        """
        self._authorization_context_service = (
            authorization_context_service or RegistryAuthorizationContextService()
        )

    def resolve(
        self,
        *,
        request: Any,
        statement: str,
        required_workspace_ids: frozenset[int] | None = None,
        require_explicit_workspace_selection: bool = False,
    ) -> AssistantNamespaceResolution:
        """Resolve and persist an authorized namespace selection for the chat request.

        Args:
            request: Authenticated request with a host-resolved Organization.
            statement: Current user message, which may name an authorized Workspace.
            required_workspace_ids: Optional server-derived Workspace restriction.
            require_explicit_workspace_selection: Ignore persisted and UI workspace hints.

        Returns:
            A selected, ambiguous, empty, or unavailable namespace result.
        """
        user = getattr(request, "user", None)
        organization = getattr(request, "organization", None)
        if not getattr(user, "is_authenticated", False) or organization is None:
            return AssistantNamespaceResolution(status="unavailable")

        try:
            authorization_context = self._authorization_context_service.build_context(
                user=user,
                organization=organization,
            )
        except RegistryAuthorizationOrganizationDisabledError:
            return self._clear_context_after_organization_disable(request)
        except (
            AttributeError,
            RegistryAuthorizationContextError,
            TypeError,
            ValueError,
        ):
            self.clear_context(request)
            return AssistantNamespaceResolution(status="unavailable", selection_changed=True)

        mappings = authorization_context.authorized_namespaces.mappings
        if not mappings:
            return self._clear_stale_namespace_context_and_return_no_namespace(request)

        namespace_by_workspace_id = {
            mapping.workspace_id: str(mapping.namespace_id)
            for mapping in mappings
            if mapping.workspace_id is not None
        }
        organization_namespace_id = next(
            (str(mapping.namespace_id) for mapping in mappings if mapping.workspace_id is None),
            None,
        )
        workspaces: QuerySet[Workspace, Workspace] = Workspace.objects.filter(
            pk__in=namespace_by_workspace_id,
            organization=organization,
            is_active=True,
        ).order_by("name", "pk")
        if get_request_scope_policy(request, assistant=True).is_enforced:
            try:
                selected_scope = select_request_registry_namespace(
                    request=request,
                    assistant=True,
                    authorization_context=authorization_context,
                )
            except RegistryScopeSelectionError:
                self.clear_context(request)
                return AssistantNamespaceResolution(
                    status="workspace_change_required", selection_changed=True
                )
            if (
                required_workspace_ids is not None
                and selected_scope.workspace_id not in required_workspace_ids
            ):
                self.clear_context(request)
                return AssistantNamespaceResolution(
                    status="workspace_change_required", selection_changed=True
                )
            all_choices = tuple(
                AssistantWorkspaceChoice(
                    workspace_id=int(workspace.pk),
                    workspace_name=workspace.name,
                    workspace_slug=workspace.slug,
                    namespace_id=namespace_by_workspace_id[int(workspace.pk)],
                )
                for workspace in workspaces
            )
            selected = next(
                (
                    choice
                    for choice in all_choices
                    if choice.namespace_id == selected_scope.namespace_id
                ),
                None,
            )
            if selected is None:
                self.clear_context(request)
                return AssistantNamespaceResolution(
                    status="workspace_change_required", selection_changed=True
                )
            selection_changed = self._has_changed_persisted_selection(
                request=request, selected=selected
            )
            if selection_changed:
                self._clear_dataset_context(request)
            if any(
                choice.workspace_id != selected.workspace_id
                and self._choice_from_statement(statement=statement, choices=(choice,)) is not None
                for choice in all_choices
            ):
                return AssistantNamespaceResolution(
                    status="workspace_change_required",
                    selection_changed=selection_changed,
                )
            self._persist_selection(
                request=request, selected=selected, is_explicit_workspace_selection=True
            )
            return AssistantNamespaceResolution(
                status="selected",
                selected=selected,
                choices=(selected,),
                selection_changed=selection_changed,
            )
        choices = tuple(
            AssistantWorkspaceChoice(
                workspace_id=int(workspace.pk),
                workspace_name=workspace.name,
                workspace_slug=workspace.slug,
                namespace_id=namespace_by_workspace_id[int(workspace.pk)],
            )
            for workspace in workspaces
            if required_workspace_ids is None or int(workspace.pk) in required_workspace_ids
        )
        if not choices and organization_namespace_id is not None and required_workspace_ids is None:
            choices = (
                AssistantWorkspaceChoice(
                    workspace_id=None,
                    workspace_name=organization.name,
                    workspace_slug=organization.slug,
                    namespace_id=organization_namespace_id,
                ),
            )
        if not choices:
            return self._clear_stale_namespace_context_and_return_no_namespace(request)

        selected = self._select_authorized_choice(
            request=request,
            statement=statement,
            choices=choices,
            require_explicit_workspace_selection=require_explicit_workspace_selection,
        )
        if selected is not None:
            validate_registry_destination(
                request=request,
                namespace_id=selected.namespace_id,
                workspace_id=selected.workspace_id,
                surface="assistant",
                assistant=True,
                authorization_context=authorization_context,
            )
            selection_changed = self._has_changed_persisted_selection(
                request=request,
                selected=selected,
            )
            if selection_changed:
                self._clear_dataset_context(request)
            self._persist_selection(
                request=request,
                selected=selected,
                is_explicit_workspace_selection=(
                    require_explicit_workspace_selection and selected.workspace_id is not None
                ),
            )
            return AssistantNamespaceResolution(
                status="selected",
                selected=selected,
                choices=choices,
                selection_changed=selection_changed,
            )
        if len(choices) == 1:
            selected = choices[0]
            validate_registry_destination(
                request=request,
                namespace_id=selected.namespace_id,
                workspace_id=selected.workspace_id,
                surface="assistant",
                assistant=True,
                authorization_context=authorization_context,
            )
            selection_changed = self._has_changed_persisted_selection(
                request=request,
                selected=selected,
            )
            if selection_changed:
                self._clear_dataset_context(request)
            self._persist_selection(request=request, selected=selected)
            return AssistantNamespaceResolution(
                status="selected",
                selected=selected,
                choices=choices,
                selection_changed=selection_changed,
            )
        return AssistantNamespaceResolution(status="multiple", choices=choices)

    def clear_context(self, request: object) -> int:
        """Clear assistant namespace and dataset selection state from one session.

        Args:
            request: Request carrying the Resource Registry assistant session.

        Returns:
            Number of session keys removed.
        """
        return clear_registry_context(request)

    def _clear_context_after_organization_disable(
        self,
        request: Any,
    ) -> AssistantNamespaceResolution:
        """Clear all Registry context after Organization access is disabled.

        Args:
            request: Request carrying optional Registry session and request state.

        Returns:
            A no-namespace result that requests runtime Registry context cleanup.
        """
        self.clear_context(request)
        return AssistantNamespaceResolution(status="none", selection_changed=True)

    def _clear_stale_namespace_context_and_return_no_namespace(
        self,
        request: Any,
    ) -> AssistantNamespaceResolution:
        """Clear stale Registry context without discarding a pending selection intent.

        Args:
            request: Request carrying optional persisted Registry selection state.

        Returns:
            A no-namespace result that signals a prior selection was invalidated.
        """
        had_selection = self._has_persisted_namespace_selection(request)
        if had_selection:
            self.clear_context(request)
        return AssistantNamespaceResolution(status="none", selection_changed=had_selection)

    @staticmethod
    def _has_persisted_namespace_selection(request: Any) -> bool:
        """Return whether the request carries a Registry namespace selection to invalidate.

        Args:
            request: Request carrying optional request and session selection state.

        Returns:
            True when a dedicated namespace or Workspace selection is present.
        """
        return bool(
            getattr(request, "resource_registry_tenant_id", None)
            or AssistantNamespaceContextService._session_value(
                request,
                "resource_registry_tenant_id",
            )
            or AssistantNamespaceContextService._session_value(
                request,
                "resource_registry_workspace_id",
            )
        )

    def _select_authorized_choice(
        self,
        *,
        request: Any,
        statement: str,
        choices: tuple[AssistantWorkspaceChoice, ...],
        require_explicit_workspace_selection: bool = False,
    ) -> AssistantWorkspaceChoice | None:
        selected_from_statement = self._choice_from_statement(
            statement=statement,
            choices=choices,
        )
        if selected_from_statement is not None:
            return selected_from_statement
        if require_explicit_workspace_selection:
            return self._choice_from_explicit_workspace_selection(request=request, choices=choices)

        selected_from_workspace_context = self._choice_from_workspace_values(
            values=(getattr(getattr(request, "workspace", None), "pk", None),),
            choices=choices,
        )
        if selected_from_workspace_context is not None:
            return selected_from_workspace_context

        for namespace_hint in self._ordered_nonempty_values(
            getattr(request, "resource_registry_tenant_id", None),
            self._session_value(request, "resource_registry_tenant_id"),
        ):
            selected_by_namespace = self._unique_choice(
                choice for choice in choices if choice.namespace_id == namespace_hint
            )
            if selected_by_namespace is not None:
                return selected_by_namespace

        return self._choice_from_workspace_values(
            values=(self._session_value(request, "resource_registry_workspace_id"),),
            choices=choices,
        )

    @staticmethod
    def _choice_from_explicit_workspace_selection(
        *,
        request: Any,
        choices: tuple[AssistantWorkspaceChoice, ...],
    ) -> AssistantWorkspaceChoice | None:
        """Return only a server-recorded explicit Workspace selection.

        Args:
            request: Request carrying conversation-scoped Registry state.
            choices: Authorized Workspace choices for the active Persona.

        Returns:
            The explicitly selected choice, or None when this conversation has none.
        """
        session = getattr(request, "session", None)
        explicit_namespace = getattr(
            request, "resource_registry_persona_workspace_namespace_id", None
        )
        if explicit_namespace is None and session is not None:
            explicit_namespace = session.get(PERSONA_WORKSPACE_SELECTION_SESSION_KEY)
        return AssistantNamespaceContextService._unique_choice(
            choice
            for choice in choices
            if choice.namespace_id == str(explicit_namespace or "").strip()
        )

    def _choice_from_statement(
        self,
        *,
        statement: str,
        choices: tuple[AssistantWorkspaceChoice, ...],
    ) -> AssistantWorkspaceChoice | None:
        normalized_statement = self._normalize_text(statement)
        return self._unique_choice(
            choice
            for choice in choices
            if any(
                re.search(
                    r"(?<!\w)" + re.escape(self._normalize_text(value)) + r"(?!\w)",
                    normalized_statement,
                )
                for value in (choice.workspace_slug, choice.workspace_name)
                if value
            )
        )

    def _choice_from_workspace_values(
        self,
        *,
        values: tuple[object | None, ...],
        choices: tuple[AssistantWorkspaceChoice, ...],
    ) -> AssistantWorkspaceChoice | None:
        workspace_hints = self._nonempty_values(*values)
        if not workspace_hints:
            return None
        return self._unique_choice(
            choice
            for choice in choices
            if workspace_hints
            & {
                str(choice.workspace_id),
                choice.workspace_slug,
                choice.workspace_name,
            }
        )

    def _has_changed_persisted_selection(
        self,
        *,
        request: Any,
        selected: AssistantWorkspaceChoice,
    ) -> bool:
        persisted_values = self._nonempty_values(
            getattr(request, "resource_registry_tenant_id", None),
            self._session_value(request, "resource_registry_tenant_id"),
            self._session_value(request, "resource_registry_workspace_id"),
        )
        selected_values = {selected.namespace_id}
        if selected.workspace_id is not None:
            selected_values.add(str(selected.workspace_id))
        session = getattr(request, "session", None)
        keys = getattr(session, "keys", None)
        has_unattributed_state = (
            not persisted_values
            and callable(keys)
            and any(
                key == base or key.startswith(f"{base}:")
                for key in keys()
                if isinstance(key, str)
                for base in REGISTRY_DATASET_CONTEXT_KEYS
                - {"resource_registry_pending_workspace_intent"}
            )
        )
        return bool(has_unattributed_state) or (
            bool(persisted_values) and not persisted_values.issubset(selected_values)
        )

    def _clear_dataset_context(self, request: object) -> int:
        return clear_registry_session_keys(
            request=request,
            base_keys=REGISTRY_DATASET_CONTEXT_KEYS,
        )

    @staticmethod
    def _persist_selection(
        *,
        request: Any,
        selected: AssistantWorkspaceChoice,
        is_explicit_workspace_selection: bool = False,
    ) -> None:
        request.resource_registry_tenant_id = selected.namespace_id
        if is_explicit_workspace_selection:
            request.resource_registry_persona_workspace_namespace_id = selected.namespace_id
        session = getattr(request, "session", None)
        if session is not None:
            session["resource_registry_tenant_id"] = selected.namespace_id
            if selected.workspace_id is None:
                session.pop("resource_registry_workspace_id", None)
            else:
                session["resource_registry_workspace_id"] = selected.workspace_id
            if is_explicit_workspace_selection:
                session[PERSONA_WORKSPACE_SELECTION_SESSION_KEY] = selected.namespace_id

    @staticmethod
    def _session_value(request: Any, key: str) -> object | None:
        session = getattr(request, "session", None)
        return session.get(key) if session is not None else None

    @staticmethod
    def _nonempty_values(*values: object) -> set[str]:
        return {str(value).strip() for value in values if value is not None and str(value).strip()}

    @staticmethod
    def _ordered_nonempty_values(*values: object) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                str(value).strip() for value in values if value is not None and str(value).strip()
            )
        )

    @staticmethod
    def _unique_choice(
        choices: Iterable[AssistantWorkspaceChoice],
    ) -> AssistantWorkspaceChoice | None:
        matches = tuple(choices)
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _normalize_text(value: str) -> str:
        return re.sub(r"\s+", " ", str(value or "").strip().casefold())
