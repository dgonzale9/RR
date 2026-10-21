from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence

from django.db.models import Q
from django.db.models.query import QuerySet
from django.utils import timezone

from resource_registry.models import (
    Dataset,
    GovernancePolicy,
    GovernancePolicyType,
)
from resource_registry.policies.resolution import PolicyResolutionService
from resource_registry.transformations.operational.contracts import (
    ResolvedTransformationSecurityPolicy,
    TransformationProviderOperationAuthorization,
)
from resource_registry.transformations.operational.exceptions import (
    TransformationSecurityPolicyConfigurationError,
)
from resource_registry.transformations.runs import TransformationOutputPolicyKind
from resource_registry.utils.json_checksum import canonicalize_transformation_specification

TRANSFORMATION_SECURITY_POLICY_KEY = "transformation_execution_security"
TRANSFORMATION_SECURITY_POLICY_CONTRACT_VERSION = "1.0"


def calculate_governance_rules_checksum(rules: Mapping[str, object]) -> str:
    """Calculate a deterministic SHA-256 checksum for governance rules."""
    canonical = canonicalize_transformation_specification(rules)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class TransformationSecurityPolicyResolver:
    """Resolve exact fail-closed tenant and dataset transformation governance."""

    def __init__(
        self,
        *,
        policy_resolution_service: PolicyResolutionService | None = None,
    ) -> None:
        self._policy_resolution_service = policy_resolution_service or PolicyResolutionService()

    def resolve(
        self,
        *,
        tenant_id: str,
        dataset_ids: Sequence[str],
    ) -> ResolvedTransformationSecurityPolicy:
        """Resolve the intersection of tenant and every exact dataset policy."""
        tenant_policy = self._get_tenant_policy(tenant_id=tenant_id)
        policies = [tenant_policy]
        for dataset_id in sorted(set(dataset_ids)):
            policies.append(
                self._get_dataset_policy(
                    tenant_id=tenant_id,
                    dataset_id=dataset_id,
                ),
            )
        parsed = [self._parse_policy(policy=policy) for policy in policies]
        allowed_operations = set(parsed[0][0])
        allowed_output_kinds = set(parsed[0][1])
        for operations, output_kinds in parsed[1:]:
            allowed_operations.intersection_update(operations)
            allowed_output_kinds.intersection_update(output_kinds)
        provenance = tuple(
            {
                "policy_id": str(policy.id),
                "policy_key": policy.policy_key,
                "revision": int(policy.revision),
                "rules_checksum": calculate_governance_rules_checksum(
                    _require_mapping(policy.rules_json),
                ),
                "scope": "TENANT" if policy.is_tenant_default else "DATASET",
            }
            for policy in policies
        )
        return ResolvedTransformationSecurityPolicy(
            allowed_operations=frozenset(allowed_operations),
            allowed_output_policy_kinds=frozenset(allowed_output_kinds),
            tenant_policy_revision=int(tenant_policy.revision),
            policy_provenance=provenance,
        )

    def is_available(self, *, tenant_id: str) -> bool:
        """Return whether one valid tenant transformation policy is available."""
        try:
            policy = self._get_tenant_policy(tenant_id=tenant_id)
            self._parse_policy(policy=policy)
        except TransformationSecurityPolicyConfigurationError:
            return False
        return True

    def _get_tenant_policy(self, *, tenant_id: str) -> GovernancePolicy:
        resolved = self._policy_resolution_service.resolve_tenant_policy(
            tenant_id=tenant_id,
            policy_key=TRANSFORMATION_SECURITY_POLICY_KEY,
        )
        policy = None
        if resolved is not None and resolved.policy_id is not None:
            policy = (
                self._eligible_policies()
                .filter(
                    id=resolved.policy_id,
                    tenant_id=tenant_id,
                    policy_type=GovernancePolicyType.ACCESS,
                    is_tenant_default=True,
                )
                .first()
            )
        if policy is None:
            raise TransformationSecurityPolicyConfigurationError(
                "Active tenant transformation security policy is required.",
            )
        return policy

    def _get_dataset_policy(self, *, tenant_id: str, dataset_id: str) -> GovernancePolicy:
        dataset = Dataset.objects.filter(tenant_id=tenant_id, id=dataset_id).first()
        if dataset is None:
            raise TransformationSecurityPolicyConfigurationError(
                "Dataset transformation policy scope is unavailable.",
            )
        resolved = self._policy_resolution_service.resolve_dataset_policy(
            dataset=dataset,
            policy_key=TRANSFORMATION_SECURITY_POLICY_KEY,
        )
        policy = None
        if resolved is not None and resolved.policy_id is not None:
            policy = (
                self._eligible_policies()
                .filter(
                    id=resolved.policy_id,
                    tenant_id=tenant_id,
                    policy_type=GovernancePolicyType.ACCESS,
                )
                .first()
            )
        if policy is None:
            raise TransformationSecurityPolicyConfigurationError(
                "Active dataset transformation security policy is required.",
            )
        return policy

    def _eligible_policies(self) -> QuerySet[GovernancePolicy]:
        return GovernancePolicy.objects.filter(is_active=True).filter(
            self._effective_time_filter(prefix=""),
        )

    def _effective_time_filter(self, *, prefix: str) -> Q:
        now = timezone.now()
        return (
            Q(**{f"{prefix}effective_from__isnull": True})
            | Q(**{f"{prefix}effective_from__lte": now})
        ) & (Q(**{f"{prefix}effective_to__isnull": True}) | Q(**{f"{prefix}effective_to__gt": now}))

    def _parse_policy(
        self,
        *,
        policy: GovernancePolicy,
    ) -> tuple[
        frozenset[TransformationProviderOperationAuthorization],
        frozenset[str],
    ]:
        rules = _require_mapping(policy.rules_json)
        expected_checksum = calculate_governance_rules_checksum(rules)
        persisted_checksum = getattr(policy, "rules_checksum", None)
        if persisted_checksum is None or persisted_checksum != expected_checksum:
            raise TransformationSecurityPolicyConfigurationError(
                "Transformation security policy checksum is invalid.",
            )
        if set(rules) != {
            "contract_version",
            "allowed_provider_operations",
            "allowed_output_policy_kinds",
        }:
            raise TransformationSecurityPolicyConfigurationError(
                "Transformation security policy has an invalid field set.",
            )
        if rules["contract_version"] != TRANSFORMATION_SECURITY_POLICY_CONTRACT_VERSION:
            raise TransformationSecurityPolicyConfigurationError(
                "Transformation security policy contract version is unsupported.",
            )
        operation_values = _require_sequence(rules["allowed_provider_operations"])
        operations = frozenset(_parse_operation(value) for value in operation_values)
        output_values = _require_sequence(rules["allowed_output_policy_kinds"])
        supported = {kind.value for kind in TransformationOutputPolicyKind}
        output_kinds = frozenset(_require_exact_string(value) for value in output_values)
        if not output_kinds.issubset(supported):
            raise TransformationSecurityPolicyConfigurationError(
                "Transformation security policy contains an unsupported output policy kind.",
            )
        return operations, output_kinds


def _parse_operation(value: object) -> TransformationProviderOperationAuthorization:
    item = _require_mapping(value)
    expected_fields = {
        "provider_key",
        "provider_version",
        "operation_key",
        "operation_version",
    }
    if set(item) != expected_fields:
        raise TransformationSecurityPolicyConfigurationError(
            "Provider governance identity has an invalid field set.",
        )
    return TransformationProviderOperationAuthorization(
        provider_key=_require_exact_string(item["provider_key"]),
        provider_version=_require_exact_string(item["provider_version"]),
        operation_key=_require_exact_string(item["operation_key"]),
        operation_version=_require_exact_string(item["operation_version"]),
    )


def _require_mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise TransformationSecurityPolicyConfigurationError(
            "Transformation security policy value must be an object.",
        )
    return value


def _require_sequence(value: object) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        raise TransformationSecurityPolicyConfigurationError(
            "Transformation security policy value must be an array.",
        )
    return value


def _require_exact_string(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or "*" in value:
        raise TransformationSecurityPolicyConfigurationError(
            "Transformation security identity must be an exact non-wildcard string.",
        )
    return value
