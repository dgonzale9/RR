class DataRegistryBifCapabilityError(Exception):
    """Base exception for Resource Registry BIF capability failures."""


class MissingCapabilityContextError(DataRegistryBifCapabilityError):
    """Raised when required capability context data is missing."""


class UnknownBifFunctionError(DataRegistryBifCapabilityError):
    """Raised when a function is not present in the capability manifest."""


class CapabilityDeniedError(DataRegistryBifCapabilityError):
    """Raised when a function call is denied by capability policy."""


class CapabilityResolutionError(DataRegistryBifCapabilityError):
    """Raised when persistent capability resolution fails."""


class InvalidCapabilityPolicyError(CapabilityResolutionError):
    """Raised when resolved capability policy shape or values are invalid."""
