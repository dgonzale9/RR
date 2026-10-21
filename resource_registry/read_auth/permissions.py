from __future__ import annotations

from enum import StrEnum

from resource_registry.read_auth.context import ReadAuthorizationContext
from resource_registry.read_auth.exceptions import (
    DatasetAccessDeniedError,
    MissingAuthorizationContextError,
    PermissionDeniedError,
    TenantAccessDeniedError,
)


class ReadPermission(StrEnum):
    DATASET_LIST = "dataset:list"
    DATASET_READ = "dataset:read"
    DATASET_SCHEMA_READ = "dataset:schema:read"
    DATASET_PROFILE_READ = "dataset:profile:read"
    DATASET_ARTIFACT_READ = "dataset:artifact:read"
    DATASET_JOB_READ = "dataset:job:read"
    DATASET_CONTEXT_READ = "dataset:context:read"
    DATASET_POLICY_READ = "dataset:policy:read"
    DATASET_SENSITIVE_METADATA_READ = "dataset:sensitive_metadata:read"


ROLE_PERMISSION_MAP: dict[str, frozenset[ReadPermission]] = {
    "admin": frozenset(ReadPermission),
    "reader": frozenset(
        {
            ReadPermission.DATASET_LIST,
            ReadPermission.DATASET_READ,
            ReadPermission.DATASET_SCHEMA_READ,
            ReadPermission.DATASET_PROFILE_READ,
            ReadPermission.DATASET_ARTIFACT_READ,
            ReadPermission.DATASET_JOB_READ,
            ReadPermission.DATASET_CONTEXT_READ,
            ReadPermission.DATASET_POLICY_READ,
        },
    ),
    "restricted_reader": frozenset(
        {
            ReadPermission.DATASET_LIST,
            ReadPermission.DATASET_READ,
            ReadPermission.DATASET_SCHEMA_READ,
            ReadPermission.DATASET_PROFILE_READ,
            ReadPermission.DATASET_ARTIFACT_READ,
            ReadPermission.DATASET_JOB_READ,
            ReadPermission.DATASET_CONTEXT_READ,
            ReadPermission.DATASET_POLICY_READ,
        },
    ),
}


class ReadAuthorizationService:
    """API-agnostic authorization checks for data registry read operations."""

    def assert_can_list_datasets(self, context: ReadAuthorizationContext) -> None:
        self._assert_permission(context, ReadPermission.DATASET_LIST)

    def assert_can_read_dataset(self, context: ReadAuthorizationContext, dataset_id: str) -> None:
        self._assert_permission(context, ReadPermission.DATASET_READ)
        self._assert_dataset_scope(context, dataset_id)

    def assert_can_read_schema(self, context: ReadAuthorizationContext, dataset_id: str) -> None:
        self._assert_permission(context, ReadPermission.DATASET_SCHEMA_READ)
        self._assert_dataset_scope(context, dataset_id)

    def assert_can_read_profile(self, context: ReadAuthorizationContext, dataset_id: str) -> None:
        self._assert_permission(context, ReadPermission.DATASET_PROFILE_READ)
        self._assert_dataset_scope(context, dataset_id)

    def assert_can_read_artifact(self, context: ReadAuthorizationContext, dataset_id: str) -> None:
        self._assert_permission(context, ReadPermission.DATASET_ARTIFACT_READ)
        self._assert_dataset_scope(context, dataset_id)

    def assert_can_read_job(
        self,
        context: ReadAuthorizationContext,
        dataset_id: str | None = None,
    ) -> None:
        self._assert_permission(context, ReadPermission.DATASET_JOB_READ)
        if dataset_id is not None:
            self._assert_dataset_scope(context, dataset_id)

    def assert_can_read_context(self, context: ReadAuthorizationContext, dataset_id: str) -> None:
        self._assert_permission(context, ReadPermission.DATASET_CONTEXT_READ)
        self._assert_dataset_scope(context, dataset_id)

    def filter_allowed_dataset_ids(
        self,
        context: ReadAuthorizationContext,
        candidate_ids: list[str],
    ) -> list[str]:
        self._assert_context(context)
        if context.enforced_dataset_ids is not None:
            candidate_ids = [
                dataset_id for dataset_id in candidate_ids if dataset_id in context.enforced_dataset_ids
            ]
        if self._is_admin(context):
            return list(candidate_ids)
        allowed_ids = context.allowed_dataset_ids
        if allowed_ids is None:
            return list(candidate_ids)
        return [dataset_id for dataset_id in candidate_ids if dataset_id in allowed_ids]

    def get_effective_allowed_dataset_ids(
        self,
        context: ReadAuthorizationContext,
    ) -> frozenset[str] | None:
        """Resolve the Dataset scope that must constrain a tenant-scoped read.

        Args:
            context: Authenticated read context.

        Returns:
            ``None`` for an unrestricted caller, otherwise the exact allowed Dataset IDs.
        """
        self._assert_context(context)
        if context.enforced_dataset_ids is not None:
            if self._is_admin(context) or context.allowed_dataset_ids is None:
                return context.enforced_dataset_ids
            return context.enforced_dataset_ids & context.allowed_dataset_ids
        if self._is_admin(context):
            return None
        return context.allowed_dataset_ids

    def has_permission(
        self,
        context: ReadAuthorizationContext,
        permission: ReadPermission,
    ) -> bool:
        self._assert_context(context)
        permissions = self._effective_permissions(context)
        return permission in permissions

    def _assert_permission(
        self,
        context: ReadAuthorizationContext,
        permission: ReadPermission,
    ) -> None:
        self._assert_context(context)
        if permission not in self._effective_permissions(context):
            raise PermissionDeniedError(f"Missing permission: {permission}.")

    def _assert_dataset_scope(self, context: ReadAuthorizationContext, dataset_id: str) -> None:
        if not dataset_id:
            raise DatasetAccessDeniedError("Dataset ID is required for scoped authorization.")
        if (
            context.enforced_dataset_ids is not None
            and dataset_id not in context.enforced_dataset_ids
        ):
            raise DatasetAccessDeniedError("Dataset is outside enforced dataset scope.")
        if self._is_admin(context):
            return
        allowed_ids = context.allowed_dataset_ids
        if allowed_ids is None:
            return
        if dataset_id not in allowed_ids:
            raise DatasetAccessDeniedError("Dataset is outside allowed dataset scope.")

    def _effective_permissions(self, context: ReadAuthorizationContext) -> set[ReadPermission]:
        permissions: set[ReadPermission] = set()
        for role in context.roles:
            permissions.update(ROLE_PERMISSION_MAP.get(role, frozenset()))
        for scope in context.scopes:
            matched = _permission_from_scope(scope)
            if matched is not None:
                permissions.add(matched)
        return permissions

    def _assert_context(self, context: ReadAuthorizationContext) -> None:
        if not context.tenant_id:
            raise MissingAuthorizationContextError("tenant_id is required for read authorization.")
        if context.allow_cross_tenant:
            return
        requested_tenant = context.metadata.get("requested_tenant_id")
        if requested_tenant and str(requested_tenant) != str(context.tenant_id):
            raise TenantAccessDeniedError("Cross-tenant reads are not allowed.")

    def _is_admin(self, context: ReadAuthorizationContext) -> bool:
        return "admin" in context.roles


def _permission_from_scope(scope: str) -> ReadPermission | None:
    normalized = scope.strip().lower()
    for permission in ReadPermission:
        if normalized == permission.value:
            return permission
        if normalized == f"read:{permission.value}":
            return permission
    return None
