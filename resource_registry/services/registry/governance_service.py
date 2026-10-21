from __future__ import annotations

from resource_registry.models import (
    Dataset,
    DatasetPolicyAssignment,
    DatasetPolicyAssignmentType,
    GovernancePolicy,
)


class GovernanceBoundaryError(ValueError):
    """Raised when governance actions violate tenant boundaries."""


class GovernanceService:
    """Handles policy assignment and active policy resolution."""

    def assign_policy(
        self,
        *,
        dataset: Dataset,
        governance_policy: GovernancePolicy,
        assignment_type: str = DatasetPolicyAssignmentType.OVERRIDE,
        assigned_by=None,
    ) -> DatasetPolicyAssignment:
        self._validate_tenant_boundary(dataset=dataset, governance_policy=governance_policy)
        assignment, _ = DatasetPolicyAssignment.objects.update_or_create(
            dataset=dataset,
            governance_policy=governance_policy,
            defaults={
                "assignment_type": assignment_type,
                "assigned_by": assigned_by,
            },
        )
        return assignment

    def remove_policy(self, *, dataset: Dataset, governance_policy: GovernancePolicy) -> bool:
        self._validate_tenant_boundary(dataset=dataset, governance_policy=governance_policy)
        deleted_count, _ = DatasetPolicyAssignment.objects.filter(
            dataset=dataset,
            governance_policy=governance_policy,
        ).delete()
        return deleted_count > 0

    def get_active_policies(self, *, dataset: Dataset):
        assigned_queryset = GovernancePolicy.objects.filter(
            dataset_assignments__dataset=dataset,
            is_active=True,
            tenant_id=dataset.tenant_id,
        )
        has_override_assignment = DatasetPolicyAssignment.objects.filter(
            dataset=dataset,
            assignment_type=DatasetPolicyAssignmentType.OVERRIDE,
        ).exists()
        if has_override_assignment:
            return assigned_queryset.distinct()

        default_queryset = GovernancePolicy.objects.filter(
            tenant_id=dataset.tenant_id,
            is_active=True,
            is_tenant_default=True,
        )
        return (assigned_queryset | default_queryset).distinct()

    def _validate_tenant_boundary(
        self,
        *,
        dataset: Dataset,
        governance_policy: GovernancePolicy,
    ) -> None:
        if dataset.tenant_id != governance_policy.tenant_id:
            raise GovernanceBoundaryError(
                "Dataset and governance policy must belong to the same tenant.",
            )
