class PolicyEvaluationError(Exception):
    """Base policy evaluation/execution error."""


class UnsupportedPolicyError(PolicyEvaluationError):
    """Raised when a requested policy or action is not supported."""


class InvalidPolicyConfigurationError(PolicyEvaluationError):
    """Raised when policy configuration values are invalid."""
