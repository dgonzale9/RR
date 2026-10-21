from __future__ import annotations

from collections.abc import Callable
from typing import Any

from resource_registry.events.audit_service import AuditService
from resource_registry.models import Dataset, DatasetPolicyAssignment, GovernancePolicy
from resource_registry.policies.exceptions import InvalidPolicyConfigurationError, PolicyEvaluationError
from resource_registry.transformations.operational.governance import (
    TRANSFORMATION_SECURITY_POLICY_KEY,
    calculate_governance_rules_checksum,
)


class PolicyConfigurationService:
    """Manages tenant policy persistence, revisions, activation, and assignment."""

    def __init__(self, *, audit_service: AuditService | None = None) -> None:
        self._audit_service = audit_service or AuditService()

    def create_policy(  # noqa: PLR0913
        self,
        *,
        tenant_id: str,
        policy_key: str,
        policy_type: str,
        rules_json: dict[str, Any],
        description: str | None = None,
        is_tenant_default: bool = False,
        is_active: bool = True,
    ) -> GovernancePolicy:
        if GovernancePolicy.objects.filter(
            tenant_id=tenant_id,
            policy_key=policy_key,
            revision=1,
        ).exists():
            raise InvalidPolicyConfigurationError(
                f"Policy {policy_key} already exists for tenant.",
            )
        policy = GovernancePolicy.objects.create(
            tenant_id=tenant_id,
            policy_key=policy_key,
            revision=1,
            policy_type=policy_type,
            rules_json=rules_json,
            rules_checksum=(
                calculate_governance_rules_checksum(rules_json)
                if policy_key == TRANSFORMATION_SECURITY_POLICY_KEY
                else None
            ),
            description=description,
            is_tenant_default=is_tenant_default,
            is_active=False,
        )
        if is_active:
            self.activate_policy(policy=policy)
        self._safe_audit(
            lambda: self._audit_service.record_policy_created(
                tenant_id=str(tenant_id),
                dataset_id=None,
                metadata={
                    "policy_id": str(policy.id),
                    "policy_key": policy_key,
                    "revision": 1,
                },
            ),
        )
        return policy

    def create_policy_revision(
        self,
        *,
        base_policy: GovernancePolicy,
        rules_json: dict[str, Any],
        description: str | None = None,
        activate: bool = False,
    ) -> GovernancePolicy:
        if getattr(base_policy, "pk", None) is None:
            raise InvalidPolicyConfigurationError("Base policy must be persisted.")
        latest = (
            GovernancePolicy.objects.filter(
                tenant_id=base_policy.tenant_id,
                policy_key=base_policy.policy_key,
            )
            .order_by("-revision")
            .first()
        )
        next_revision = 1 if latest is None else int(latest.revision) + 1
        policy = GovernancePolicy.objects.create(
            tenant_id=base_policy.tenant_id,
            policy_key=base_policy.policy_key,
            revision=next_revision,
            policy_type=base_policy.policy_type,
            rules_json=rules_json,
            rules_checksum=(
                calculate_governance_rules_checksum(rules_json)
                if base_policy.policy_key == TRANSFORMATION_SECURITY_POLICY_KEY
                else None
            ),
            description=description if description is not None else base_policy.description,
            is_tenant_default=base_policy.is_tenant_default,
            is_active=False,
        )
        if activate:
            self.activate_policy(policy=policy)
        self._safe_audit(
            lambda: self._audit_service.record_policy_revision_created(
                tenant_id=str(base_policy.tenant_id),
                dataset_id=None,
                metadata={
                    "policy_id": str(policy.id),
                    "policy_key": policy.policy_key,
                    "revision": next_revision,
                },
            ),
        )
        return policy

    def activate_policy(self, *, policy: GovernancePolicy) -> GovernancePolicy:
        if getattr(policy, "pk", None) is None:
            raise InvalidPolicyConfigurationError(
                "Cannot activate a non-persisted policy revision.",
            )
        active_scope = GovernancePolicy.objects.filter(
            tenant_id=policy.tenant_id,
            policy_key=policy.policy_key,
            is_active=True,
        )
        if policy.is_tenant_default:
            active_scope = active_scope.filter(is_tenant_default=True)
        else:
            active_scope = active_scope.filter(pk=policy.pk)
        active_scope.exclude(pk=policy.pk).update(is_active=False)
        policy.is_active = True
        policy.save(update_fields=["is_active", "updated_at"])
        self._safe_audit(
            lambda: self._audit_service.record_policy_activated(
                tenant_id=str(policy.tenant_id),
                dataset_id=None,
                metadata={
                    "policy_id": str(policy.id),
                    "policy_key": policy.policy_key,
                    "revision": int(policy.revision),
                },
            ),
        )
        return policy

    def deactivate_policy(self, *, policy: GovernancePolicy) -> GovernancePolicy:
        policy.is_active = False
        policy.save(update_fields=["is_active", "updated_at"])
        self._safe_audit(
            lambda: self._audit_service.record_policy_deactivated(
                tenant_id=str(policy.tenant_id),
                dataset_id=None,
                metadata={
                    "policy_id": str(policy.id),
                    "policy_key": policy.policy_key,
                    "revision": int(policy.revision),
                },
            ),
        )
        return policy

    def assign_policy_to_dataset(
        self,
        *,
        dataset: Dataset,
        policy: GovernancePolicy,
        assignment_type: str | None = None,
        assigned_by: object | None = None,
    ) -> DatasetPolicyAssignment:
        self._validate_tenant_boundary(dataset=dataset, policy=policy)
        assignment_defaults: dict[str, Any] = {"assigned_by": assigned_by}
        if assignment_type is not None:
            assignment_defaults["assignment_type"] = assignment_type
        assignment, _ = DatasetPolicyAssignment.objects.update_or_create(
            dataset=dataset,
            governance_policy=policy,
            defaults=assignment_defaults,
        )
        self._safe_audit(
            lambda: self._audit_service.record_policy_assigned(
                tenant_id=str(dataset.tenant_id),
                dataset_id=str(dataset.id),
                metadata={
                    "policy_id": str(policy.id),
                    "policy_key": policy.policy_key,
                    "revision": int(policy.revision),
                },
            ),
        )
        return assignment

    def remove_policy_from_dataset(
        self,
        *,
        dataset: Dataset,
        policy: GovernancePolicy,
    ) -> bool:
        self._validate_tenant_boundary(dataset=dataset, policy=policy)
        deleted_count, _ = DatasetPolicyAssignment.objects.filter(
            dataset=dataset,
            governance_policy=policy,
        ).delete()
        if deleted_count:
            self._safe_audit(
                lambda: self._audit_service.record_policy_removed(
                    tenant_id=str(dataset.tenant_id),
                    dataset_id=str(dataset.id),
                    metadata={
                        "policy_id": str(policy.id),
                        "policy_key": policy.policy_key,
                        "revision": int(policy.revision),
                    },
                ),
            )
        return deleted_count > 0

    def get_dataset_policy(
        self,
        *,
        dataset: Dataset,
        policy_key: str,
    ) -> GovernancePolicy | None:
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
        return assignment.governance_policy if assignment else None

    def get_tenant_default_policy(
        self,
        *,
        tenant_id: str,
        policy_key: str,
    ) -> GovernancePolicy | None:
        return (
            GovernancePolicy.objects.filter(
                tenant_id=tenant_id,
                policy_key=policy_key,
                is_tenant_default=True,
                is_active=True,
            )
            .order_by("-revision", "-created_at")
            .first()
        )

    def _validate_tenant_boundary(self, *, dataset: Dataset, policy: GovernancePolicy) -> None:
        if dataset.tenant_id != policy.tenant_id:
            raise PolicyEvaluationError(
                "Dataset and policy must belong to the same tenant.",
            )

    def _safe_audit(self, audit_call: Callable[[], object]) -> None:
        try:
            audit_call()
        except Exception:
            return
