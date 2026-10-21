from resource_registry.policies.defaults import get_system_default_policy
from resource_registry.policies.evaluation import PolicyExecutionService
from resource_registry.policies.exceptions import (
    InvalidPolicyConfigurationError,
    PolicyEvaluationError,
    UnsupportedPolicyError,
)
from resource_registry.policies.lifecycle import LifecyclePolicyEvaluator
from resource_registry.policies.models import (
    ArtifactSnapshot,
    DatasetVersionSnapshot,
    LifecycleAction,
    LifecycleActionType,
    ResolvedPolicy,
    RetentionEvaluationResult,
)
from resource_registry.policies.resolution import PolicyResolutionService
from resource_registry.policies.retention import RetentionPolicyEvaluator

__all__ = [
    "ArtifactSnapshot",
    "DatasetVersionSnapshot",
    "InvalidPolicyConfigurationError",
    "LifecycleAction",
    "LifecycleActionType",
    "LifecyclePolicyEvaluator",
    "PolicyConfigurationService",
    "PolicyEvaluationError",
    "PolicyExecutionService",
    "PolicyResolutionService",
    "ResolvedPolicy",
    "RetentionEvaluationResult",
    "RetentionPolicyEvaluator",
    "UnsupportedPolicyError",
    "get_system_default_policy",
]


def __getattr__(name: str) -> object:
    """Lazily expose configuration services without importing transformation dependencies."""
    if name == "PolicyConfigurationService":
        from resource_registry.policies.configuration import PolicyConfigurationService

        return PolicyConfigurationService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
