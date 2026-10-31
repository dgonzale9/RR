"""Define immutable, trusted setup state for one assistant routing operation."""

from __future__ import annotations

from dataclasses import dataclass, field

from resource_registry.assistant.persona_workspace_scope import PersonaWorkspaceScope
from resource_registry.authorization.contracts import RegistryAuthorizationContext


@dataclass(frozen=True)
class AssistantRoutingOperationContext:
    """Carry Registry-owned pre-dispatch state for exactly one adapter invocation.

    This context is deliberately not accepted from any request, session, model, or public
    resolver entry point. The namespace service verifies its private provenance token and the
    request identities before it may reuse the authorization snapshot.

    Args:
        authorization_context: Fresh immutable Registry authorization for the operation.
        persona_workspace_scope: Resolved forced Persona Workspace scope, when configured.
        configured_persona_workspace_ids: Immutable configured Persona Workspace identifiers.
        principal_id: Trusted authenticated principal primary key.
        organization_id: Trusted host-resolved Organization primary key.
        _provenance: Opaque namespace-service token that proves internal construction.
    """

    authorization_context: RegistryAuthorizationContext
    persona_workspace_scope: PersonaWorkspaceScope | None
    configured_persona_workspace_ids: frozenset[int] | None
    principal_id: int
    organization_id: int
    _provenance: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        """Normalize nested identifiers and reject inconsistent trusted identities.

        Raises:
            ValueError: If the context identities are missing or disagree with authorization.
        """
        if not isinstance(self.principal_id, int) or isinstance(self.principal_id, bool):
            raise ValueError("Routing operation context requires a persisted principal.")
        if not isinstance(self.organization_id, int) or isinstance(self.organization_id, bool):
            raise ValueError("Routing operation context requires a persisted Organization.")
        if (
            self.authorization_context.authorized_workspaces.user_id != self.principal_id
            or self.authorization_context.authorized_workspaces.organization_id
            != self.organization_id
        ):
            raise ValueError("Routing operation context identities do not match authorization.")
        configured_workspace_ids = self.configured_persona_workspace_ids
        if configured_workspace_ids is not None:
            configured_workspace_ids = frozenset(int(value) for value in configured_workspace_ids)
            object.__setattr__(
                self,
                "configured_persona_workspace_ids",
                configured_workspace_ids,
            )
        if (
            self.persona_workspace_scope is not None
            and configured_workspace_ids is not None
            and self.persona_workspace_scope.workspace_id not in configured_workspace_ids
        ):
            raise ValueError("Persona Workspace scope is outside configured Workspace identifiers.")
