"""Define validation failures for pure execution request contracts."""


class ExecutionRequestValidationError(ValueError):
    """Report malformed immutable execution request contracts."""


class ProviderSelectionValidationError(ExecutionRequestValidationError):
    """Report malformed pure Router Provider-selection evidence."""
