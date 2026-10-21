"""Stable failures for Registry authorization context construction."""


class RegistryAuthorizationContextError(Exception):
    """Report failure to construct a trusted Registry authorization context."""


class RegistryAuthorizationAuthenticationRequiredError(RegistryAuthorizationContextError):
    """Report that Registry authorization requires an authenticated persisted user."""


class RegistryAuthorizationOrganizationRequiredError(RegistryAuthorizationContextError):
    """Report that Registry authorization requires a persisted Organization."""


class RegistryAuthorizationOrganizationDisabledError(RegistryAuthorizationContextError):
    """Report that Resource Registry access is disabled for the Organization."""
