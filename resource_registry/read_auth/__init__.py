from resource_registry.read_auth.classifications import DataClassification
from resource_registry.read_auth.context import ReadAuthorizationContext
from resource_registry.read_auth.exceptions import (
    DataRegistryAuthorizationError,
    DatasetAccessDeniedError,
    InvalidVisibilityPolicyError,
    MissingAuthorizationContextError,
    PermissionDeniedError,
    TenantAccessDeniedError,
)
from resource_registry.read_auth.field_filtering import FieldFilterService
from resource_registry.read_auth.permissions import ReadAuthorizationService, ReadPermission
from resource_registry.read_auth.visibility import (
    DATASET_PROFILE_FULL_READ,
    DATASET_PROFILE_MASKED_READ,
    DATASET_SCHEMA_FULL_READ,
    DATASET_SCHEMA_MASKED_READ,
    SchemaProfileVisibilityResolver,
)
from resource_registry.read_auth.visibility_diagnostics import (
    VisibilityDiagnosticsReport,
    VisibilityPrecedenceTrace,
    VisibilityRuleDiagnosticsService,
    VisibilityRuleMatch,
)
from resource_registry.read_auth.visibility_explainability import (
    FilteredWithExplanation,
    VisibilityDecision,
    VisibilityExplanation,
)

__all__ = [
    "DATASET_PROFILE_FULL_READ",
    "DATASET_PROFILE_MASKED_READ",
    "DATASET_SCHEMA_FULL_READ",
    "DATASET_SCHEMA_MASKED_READ",
    "AuthorizedDataRegistryReadFacade",
    "DataClassification",
    "DataRegistryAuthorizationError",
    "DatasetAccessDeniedError",
    "FieldFilterService",
    "FilteredWithExplanation",
    "InvalidVisibilityPolicyError",
    "MissingAuthorizationContextError",
    "PermissionDeniedError",
    "ReadAuthorizationContext",
    "ReadAuthorizationService",
    "ReadPermission",
    "SchemaProfileVisibilityResolver",
    "TenantAccessDeniedError",
    "VisibilityDecision",
    "VisibilityDiagnosticsReport",
    "VisibilityExplanation",
    "VisibilityPrecedenceTrace",
    "VisibilityRuleDiagnosticsService",
    "VisibilityRuleMatch",
]


def __getattr__(name: str) -> object:
    """Lazily expose read facades to avoid importing read services during bootstrap."""
    if name == "AuthorizedDataRegistryReadFacade":
        from resource_registry.read_auth.policy import AuthorizedDataRegistryReadFacade

        return AuthorizedDataRegistryReadFacade
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
