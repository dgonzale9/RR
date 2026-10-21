"""Define pure Namespace Module declarations, resolution, and admission."""

from __future__ import annotations

from dataclasses import dataclass

from .identity_type_module import ResourceTypeRegistry
from .registry_core import NamespaceKey, OwnerDescriptor, RegistryCoreValidationError, ResourceKey

__all__ = ("NamespaceDeclaration", "NamespaceRegistry")


@dataclass(frozen=True, slots=True)
class NamespaceDeclaration:
    """Declare one immutable Workspace-to-namespace mapping.

    Args:
        namespace: Canonical Registry namespace owned by exactly one Workspace.
        workspace_id: Positive upstream Workspace identifier that owns the namespace.

    Raises:
        RegistryCoreValidationError: If the namespace or Workspace identifier is invalid.
    """

    namespace: NamespaceKey
    workspace_id: int

    def __post_init__(self) -> None:
        """Validate the declaration fields in public order.

        Raises:
            RegistryCoreValidationError: If the namespace or Workspace identifier is invalid.
        """
        if not isinstance(self.namespace, NamespaceKey):
            raise RegistryCoreValidationError("invalid namespace declaration key")
        if type(self.workspace_id) is not int or self.workspace_id <= 0:
            raise RegistryCoreValidationError("invalid namespace declaration workspace")


@dataclass(frozen=True, slots=True)
class NamespaceRegistry:
    """Resolve and admit only explicitly declared namespaces without mutable state.

    Args:
        declarations: Exact immutable namespace declarations without namespace or Workspace reuse.

    Raises:
        RegistryCoreValidationError: If declarations are malformed or collide.
    """

    declarations: tuple[NamespaceDeclaration, ...]

    def __post_init__(self) -> None:
        """Validate namespace and Workspace uniqueness in declaration order.

        Raises:
            RegistryCoreValidationError: If declarations are malformed or collide.
        """
        if type(self.declarations) is not tuple:
            raise RegistryCoreValidationError("invalid namespace registry declarations")

        namespace_keys: set[NamespaceKey] = set()
        workspace_ids: set[int] = set()
        for declaration in self.declarations:
            if not isinstance(declaration, NamespaceDeclaration):
                raise RegistryCoreValidationError("invalid namespace registry declaration")
            if declaration.namespace in namespace_keys:
                raise RegistryCoreValidationError("duplicate namespace declaration")
            if declaration.workspace_id in workspace_ids:
                raise RegistryCoreValidationError("duplicate namespace workspace")
            namespace_keys.add(declaration.namespace)
            workspace_ids.add(declaration.workspace_id)

    def resolve_namespace_for_workspace(self, workspace_id: int) -> NamespaceKey:
        """Resolve the canonical namespace owned by an exact Workspace.

        Args:
            workspace_id: Positive upstream Workspace identifier to resolve.

        Returns:
            The Workspace's declared immutable namespace key.

        Raises:
            RegistryCoreValidationError: If the Workspace identifier is invalid or unknown.
        """
        if type(workspace_id) is not int or workspace_id <= 0:
            raise RegistryCoreValidationError("invalid namespace workspace")
        for declaration in self.declarations:
            if declaration.workspace_id == workspace_id:
                return declaration.namespace
        raise RegistryCoreValidationError("unknown namespace workspace")

    def resolve_workspace_for_namespace(self, namespace: NamespaceKey) -> int:
        """Resolve the upstream Workspace that owns an exact namespace.

        Args:
            namespace: Exact namespace key to resolve.

        Returns:
            The declared owning Workspace identifier.

        Raises:
            RegistryCoreValidationError: If the namespace is invalid or unknown.
        """
        if not isinstance(namespace, NamespaceKey):
            raise RegistryCoreValidationError("invalid namespace resolution key")
        for declaration in self.declarations:
            if declaration.namespace == namespace:
                return declaration.workspace_id
        raise RegistryCoreValidationError("unknown namespace")

    def admit_resource_key(
        self,
        resource_key: ResourceKey,
        resource_type_registry: ResourceTypeRegistry,
    ) -> OwnerDescriptor:
        """Admit a resource key only when its namespace and type are explicitly declared.

        Args:
            resource_key: Exact resource key whose namespace and type require admission.
            resource_type_registry: Completed Identity/Type Module registry for type resolution.

        Returns:
            The declared owner descriptor for the admitted resource type.

        Raises:
            RegistryCoreValidationError: If the key, registry, namespace, or type is invalid.
        """
        if not isinstance(resource_key, ResourceKey):
            raise RegistryCoreValidationError("invalid namespace admission resource key")
        if not isinstance(resource_type_registry, ResourceTypeRegistry):
            raise RegistryCoreValidationError("invalid namespace admission type registry")
        self.resolve_workspace_for_namespace(resource_key.namespace)
        return resource_type_registry.resolve_owner_descriptor(resource_key.resource_type)
