class ExternalDatasetProviderError(Exception):
    """Base external provider framework error."""


class ProviderNotRegisteredError(ExternalDatasetProviderError):
    """Raised when a provider key is not registered."""


class ProviderJobNotFoundError(ExternalDatasetProviderError):
    """Raised when a provider job identifier cannot be found."""


class InvalidProviderJobStateError(ExternalDatasetProviderError):
    """Raised when provider job transition is invalid."""
