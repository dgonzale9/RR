from __future__ import annotations

from resource_registry.events.audit_service import AuditService
from resource_registry.models import Dataset, DatasetPolicyAssignment, GovernancePolicy
from resource_registry.policies.defaults import get_system_default_policy
from resource_registry.policies.models import ResolvedPolicy
from resource_registry.policies.provenance import (
    ResolvedPolicyWithProvenance,
    build_policy_provenance,
)


class PolicyResolutionService:
    """Deterministic policy resolver for dataset/tenant/system scopes."""

    def __init__(self, *, audit_service: AuditService | None = None) -> None:
        self._audit_service = audit_service or AuditService()

    def resolve_dataset_policy(self, *, dataset: Dataset, policy_key: str) -> ResolvedPolicy | None:
        assignment = (
            DatasetPolicyAssignment.objects.select_related("governance_policy")
            .filter(
                dataset=dataset,
                governance_policy__policy_key=policy_key,
                governance_policy__tenant_id=dataset.tenant_id,
                governance_policy__is_active=True,
            )
            .order_by("-assigned_at", "-governance_policy__revision")
            .first()
        )
        if assignment is None:
            return None
        policy = assignment.governance_policy
        return ResolvedPolicy(
            source="dataset_override",
            policy_key=policy.policy_key,
            tenant_id=str(policy.tenant_id),
            dataset_id=str(dataset.id),
            revision=int(policy.revision),
            policy_id=str(policy.id),
            rules_json=dict(policy.rules_json or {}),
        )

    def resolve_dataset_policy_with_provenance(
        self,
        *,
        dataset: Dataset,
        policy_key: str,
    ) -> ResolvedPolicyWithProvenance | None:
        resolved = self.resolve_dataset_policy(dataset=dataset, policy_key=policy_key)
        if resolved is None:
            return None
        return ResolvedPolicyWithProvenance(
            resolved_policy=resolved,
            provenance=build_policy_provenance(
                resolved_policy=resolved,
                resolution_path=["dataset_override", "tenant_default", "system_default"],
            ),
        )

    def resolve_tenant_policy(self, *, tenant_id: str, policy_key: str) -> ResolvedPolicy | None:
        policy = (
            GovernancePolicy.objects.filter(
                tenant_id=tenant_id,
                policy_key=policy_key,
                is_active=True,
                is_tenant_default=True,
            )
            .order_by("-revision", "-created_at")
            .first()
        )
        if policy is None:
            return None
        return ResolvedPolicy(
            source="tenant_default",
            policy_key=policy.policy_key,
            tenant_id=str(policy.tenant_id),
            dataset_id=None,
            revision=int(policy.revision),
            policy_id=str(policy.id),
            rules_json=dict(policy.rules_json or {}),
        )

    def resolve_tenant_policy_with_provenance(
        self,
        *,
        tenant_id: str,
        policy_key: str,
    ) -> ResolvedPolicyWithProvenance | None:
        resolved = self.resolve_tenant_policy(tenant_id=tenant_id, policy_key=policy_key)
        if resolved is None:
            return None
        return ResolvedPolicyWithProvenance(
            resolved_policy=resolved,
            provenance=build_policy_provenance(
                resolved_policy=resolved,
                resolution_path=["tenant_default", "system_default"],
            ),
        )

    def resolve_effective_policy(self, *, dataset: Dataset, policy_key: str) -> ResolvedPolicy:
        resolved = self.resolve_dataset_policy(dataset=dataset, policy_key=policy_key)
        if resolved is None:
            resolved = self.resolve_tenant_policy(
                tenant_id=str(dataset.tenant_id),
                policy_key=policy_key,
            )
        if resolved is None:
            resolved = ResolvedPolicy(
                source="system_default",
                policy_key=policy_key,
                tenant_id=str(dataset.tenant_id),
                dataset_id=str(dataset.id),
                revision=None,
                policy_id=None,
                rules_json=get_system_default_policy(policy_key),
            )
        self._safe_audit(
            lambda: self._audit_service.record_policy_resolved(
                tenant_id=str(dataset.tenant_id),
                dataset_id=str(dataset.id),
                metadata={
                    "policy_key": policy_key,
                    "source": resolved.source,
                    "revision": resolved.revision,
                },
            ),
        )
        return resolved

    def resolve_effective_policy_with_provenance(
        self,
        *,
        dataset: Dataset,
        policy_key: str,
    ) -> ResolvedPolicyWithProvenance:
        resolved = self.resolve_effective_policy(dataset=dataset, policy_key=policy_key)
        resolved_with_provenance = ResolvedPolicyWithProvenance(
            resolved_policy=resolved,
            provenance=build_policy_provenance(
                resolved_policy=resolved,
                resolution_path=["dataset_override", "tenant_default", "system_default"],
            ),
        )
        self._safe_audit(
            lambda: self._audit_service.record_policy_provenance_resolved(
                tenant_id=str(dataset.tenant_id),
                dataset_id=str(dataset.id),
                metadata={
                    "policy_key": policy_key,
                    "source": resolved_with_provenance.provenance.source,
                    "revision": resolved_with_provenance.provenance.revision,
                    "rules_hash": resolved_with_provenance.provenance.rules_hash,
                },
            ),
        )
        return resolved_with_provenance

    def _safe_audit(self, audit_call) -> None:
        try:
            audit_call()
        except Exception:
            return
