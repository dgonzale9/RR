class TransformationOperationalSecurityError(ValueError):
    """Raise when operational security validation fails closed."""


class TransformationPermissionDeniedError(TransformationOperationalSecurityError):
    """Raise when the trusted principal lacks a required permission or scope."""


class TransformationGovernanceDeniedError(TransformationOperationalSecurityError):
    """Raise when exact provider or output governance denies execution."""


class TransformationSecurityPolicyConfigurationError(TransformationOperationalSecurityError):
    """Raise when transformation security policy data is unavailable or malformed."""
