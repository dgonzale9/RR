from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Protocol, cast
from uuid import UUID, uuid4

from resource_registry.application.contracts import AuthorizationError
from resource_registry.application.read import TransformationReadContext
from resource_registry.application.services import TransformationExecutionCallerContext
from resource_registry.bifs.capability_context import BifCapabilityContext
from resource_registry.bifs.capability_policy import DataRegistryBifCapabilityPolicy
from resource_registry.bifs.exceptions import DataRegistryBifCapabilityError
from resource_registry.bifs.read_only_namespace_admission import (
    AdmittedBifNamespace,
    ReadOnlyBifNamespaceAdmissionService,
)
from resource_registry.read_auth.exceptions import DataRegistryAuthorizationError


class TransformationBifContextManager(Protocol):
    """Expose trusted agent context entries used by BIF invocation mapping."""

    def get_all_registry_entries(self, request: object) -> Mapping[str, object]:
        """Return trusted context registry entries for one invocation."""


class TransformationReadNamespaceAdmission(Protocol):
    """Admit a legacy selector to one canonical authorized namespace."""

    def admit_namespace(
        self,
        *,
        request: object,
        requested_namespace_id: object,
    ) -> str:
        """Return the canonical namespace authorized for a read invocation."""

    def admit_namespace_with_provenance(
        self,
        *,
        request: object,
        requested_namespace_id: object,
    ) -> AdmittedBifNamespace:
        """Return canonical namespace identity with submission provenance."""


class TransformationBifTrustedContextBuilder:
    """Build application contexts only from trusted BIF invocation state."""

    def __init__(
        self,
        *,
        capability_policy: DataRegistryBifCapabilityPolicy | None = None,
        namespace_admission_service: TransformationReadNamespaceAdmission | None = None,
    ) -> None:
        self._capability_policy = capability_policy or DataRegistryBifCapabilityPolicy()
        self._namespace_admission_service = (
            namespace_admission_service or ReadOnlyBifNamespaceAdmissionService()
        )

    def build_execution_context(
        self,
        *,
        request: object,
        context_manager: object,
        function_name: str,
        requested_tenant_id: str | None,
        correlation_id: str,
    ) -> TransformationExecutionCallerContext:
        """Build and capability-check one trusted execution caller context."""
        _ = context_manager
        namespace_admission = self._admit_submission_namespace(
            request=request,
            requested_tenant_id=requested_tenant_id,
            correlation_id=correlation_id,
        )
        tenant_id = namespace_admission.namespace_id
        self._assert_capability(
            request=request,
            function_name=function_name,
            tenant_id=tenant_id,
            correlation_id=correlation_id,
        )
        return TransformationExecutionCallerContext(
            tenant_id=tenant_id,
            actor=self._get_actor(request=request, correlation_id=correlation_id),
            principal_kind="USER",
            permissions=self._get_permissions(request=request),
            allowed_definition_ids=self._get_optional_identifier_set(
                request=request,
                key="resource_registry_allowed_transformation_definition_ids",
            ),
            allowed_dataset_ids=self._get_effective_allowed_dataset_ids(
                request=request,
                namespace_admission=namespace_admission,
            ),
            created_by_id=self._get_user_identifier(request=request),
        )

    def build_read_context(
        self,
        *,
        request: object,
        context_manager: object,
        function_name: str,
        requested_tenant_id: str | None,
        correlation_id: str | None,
    ) -> TransformationReadContext:
        """Build and capability-check one trusted application read context."""
        _ = context_manager
        active_correlation_id = correlation_id or str(uuid4())
        namespace_admission = self._admit_read_namespace(
            request=request,
            requested_tenant_id=requested_tenant_id,
            correlation_id=active_correlation_id,
        )
        tenant_id = namespace_admission.namespace_id
        self._assert_capability(
            request=request,
            function_name=function_name,
            tenant_id=tenant_id,
            correlation_id=active_correlation_id,
        )
        return TransformationReadContext(
            tenant_id=tenant_id,
            actor=self._get_actor(request=request, correlation_id=active_correlation_id),
            permissions=self._get_permissions(request=request),
            correlation_id=active_correlation_id,
            allowed_definition_ids=self._get_optional_identifier_set(
                request=request,
                key="resource_registry_allowed_transformation_definition_ids",
            ),
            allowed_dataset_ids=self._get_effective_allowed_dataset_ids(
                request=request,
                namespace_admission=namespace_admission,
            ),
        )

    def _admit_read_namespace(
        self,
        *,
        request: object,
        requested_tenant_id: str | None,
        correlation_id: str,
    ) -> AdmittedBifNamespace:
        try:
            return self._namespace_admission_service.admit_namespace_with_provenance(
                request=request,
                requested_namespace_id=requested_tenant_id,
            )
        except DataRegistryAuthorizationError as error:
            raise AuthorizationError(
                code="transformation_bif_tenant_not_authorized",
                public_message="The transformation tenant is not authorized.",
                correlation_id=correlation_id,
            ) from error

    def _admit_submission_namespace(
        self,
        *,
        request: object,
        requested_tenant_id: str | None,
        correlation_id: str,
    ) -> AdmittedBifNamespace:
        try:
            return self._namespace_admission_service.admit_namespace_with_provenance(
                request=request,
                requested_namespace_id=requested_tenant_id,
            )
        except DataRegistryAuthorizationError as error:
            raise AuthorizationError(
                code="transformation_bif_tenant_not_authorized",
                public_message="The transformation tenant is not authorized.",
                correlation_id=correlation_id,
            ) from error

    def _assert_capability(
        self,
        *,
        request: object,
        function_name: str,
        tenant_id: str,
        correlation_id: str,
    ) -> None:
        try:
            self._capability_policy.assert_can_call(
                function_name,
                BifCapabilityContext(
                    tenant_id=tenant_id,
                    actor=self._get_actor(request=request, correlation_id=correlation_id),
                    roles=self._get_string_tuple(request=request, key="resource_registry_roles"),
                    scopes=self._get_string_tuple(request=request, key="resource_registry_scopes"),
                    enabled_capabilities=self._get_string_set(
                        request=request,
                        key="resource_registry_enabled_capabilities",
                    ),
                    disabled_capabilities=self._get_string_set(
                        request=request,
                        key="resource_registry_disabled_capabilities",
                    ),
                    metadata={"requested_tenant_id": tenant_id},
                ),
            )
        except DataRegistryBifCapabilityError:
            raise AuthorizationError(
                code="transformation_bif_capability_denied",
                public_message="The transformation capability is not authorized.",
                correlation_id=correlation_id,
            ) from None

    @staticmethod
    def _get_actor(*, request: object, correlation_id: str) -> str:
        user = getattr(request, "user", None)
        username = getattr(user, "username", None)
        actor = str(username or getattr(user, "pk", "")).strip()
        if not actor:
            raise AuthorizationError(
                code="transformation_bif_actor_required",
                public_message="A trusted transformation actor is required.",
                correlation_id=correlation_id,
            )
        return actor

    @staticmethod
    def _get_permissions(*, request: object) -> frozenset[str]:
        user = getattr(request, "user", None)
        getter = getattr(user, "get_all_permissions", None)
        if not callable(getter):
            return frozenset()
        values = cast(Collection[object], getter())
        return frozenset(str(value) for value in values)

    @staticmethod
    def _get_user_identifier(*, request: object) -> int | None:
        value = getattr(getattr(request, "user", None), "pk", None)
        return value if isinstance(value, int) else None

    def _get_optional_identifier_set(self, *, request: object, key: str) -> frozenset[str] | None:
        value = self._get_session_value(request=request, key=key)
        if value is None:
            return None
        if not isinstance(value, list | tuple | set | frozenset):
            return frozenset()
        identifiers = {identifier for item in value if (identifier := self._canonical_uuid(item))}
        return frozenset(identifiers)

    def _get_effective_allowed_dataset_ids(
        self,
        *,
        request: object,
        namespace_admission: AdmittedBifNamespace,
    ) -> frozenset[str] | None:
        """Return request ACL IDs constrained to the admitted Persona Workspace when required.

        Args:
            request: Authenticated BIF invocation request.
            namespace_admission: Immutable namespace provenance admitted for this invocation.

        Returns:
            Dataset IDs allowed by both the request ACL and Persona Workspace scope, or the
            unmodified optional request ACL for generic Registry consumers.
        """
        requested_dataset_ids = self._get_optional_identifier_set(
            request=request,
            key="resource_registry_allowed_dataset_ids",
        )
        if not namespace_admission.is_persona_workspace_scoped:
            return requested_dataset_ids
        workspace_dataset_ids = namespace_admission.enforced_dataset_ids
        if namespace_admission.workspace_id is None or workspace_dataset_ids is None:
            raise DataRegistryAuthorizationError("Resource Registry workspace is not authorized.")
        if requested_dataset_ids is None:
            return workspace_dataset_ids
        return workspace_dataset_ids & requested_dataset_ids

    def _get_string_tuple(self, *, request: object, key: str) -> tuple[str, ...]:
        return tuple(sorted(self._get_string_set(request=request, key=key)))

    def _get_string_set(self, *, request: object, key: str) -> frozenset[str]:
        value = self._get_session_value(request=request, key=key)
        if not isinstance(value, list | tuple | set | frozenset):
            return frozenset()
        return frozenset(str(item) for item in value)

    @staticmethod
    def _get_session_value(*, request: object, key: str) -> object | None:
        session = getattr(request, "session", None)
        return session.get(key) if isinstance(session, Mapping) else None

    @staticmethod
    def _canonical_uuid(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        try:
            parsed = UUID(value)
        except ValueError:
            return None
        return str(parsed) if str(parsed) == value.lower() else None
