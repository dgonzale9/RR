class DataRegistryAuthorizationError(Exception):
    """Base exception for read-authorization failures."""


class MissingAuthorizationContextError(DataRegistryAuthorizationError):
    """Raised when read authorization context is missing required data."""


class TenantAccessDeniedError(DataRegistryAuthorizationError):
    """Raised when an actor attempts a disallowed tenant read."""


class DatasetAccessDeniedError(DataRegistryAuthorizationError):
    """Raised when a dataset is outside the actor authorization scope."""


class PermissionDeniedError(DataRegistryAuthorizationError):
    """Raised when a required read permission is absent."""


class InvalidVisibilityPolicyError(DataRegistryAuthorizationError):
    """Raised when schema/profile visibility policy configuration is invalid."""
