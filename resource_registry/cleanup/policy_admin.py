"""Configure organization cleanup policy through the existing trusted Admin scope."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django import forms
from django.contrib import admin
from django.db.models import QuerySet
from django.http import HttpRequest

from Organization.models import Organization
from Organization.organization_acl import user_can_manage_organization
from resource_registry.models import DatasetManagementConfiguration

if TYPE_CHECKING:
    PolicyModelAdmin = admin.ModelAdmin[DatasetManagementConfiguration]
    PolicyModelForm = forms.ModelForm[DatasetManagementConfiguration]
else:
    PolicyModelAdmin = admin.ModelAdmin
    PolicyModelForm = forms.ModelForm


class CleanupPolicyForm(PolicyModelForm):
    """Enforce minimum recovery periods when configuring a pilot organization."""

    dataset_recovery_days = forms.IntegerField(min_value=30, label="Dataset recovery days")
    intake_source_retention_days = forms.IntegerField(
        min_value=30, label="Published source retention days"
    )
    discarded_candidate_retention_days = forms.IntegerField(
        min_value=7, label="Discarded candidate retention days"
    )

    class Meta:
        model = DatasetManagementConfiguration
        fields = (
            "cleanup_mode",
            "dataset_recovery_days",
            "intake_source_retention_days",
            "discarded_candidate_retention_days",
        )


@admin.register(DatasetManagementConfiguration)
class CleanupPolicyAdmin(PolicyModelAdmin):
    """Permit organization managers to configure cleanup without granting new access."""

    form = CleanupPolicyForm
    list_display = (
        "organization",
        "cleanup_mode",
        "dataset_recovery_days",
        "intake_source_retention_days",
        "discarded_candidate_retention_days",
    )
    readonly_fields = ("organization", "enabled", "legacy_updates_blocked")
    fields = (*readonly_fields, *CleanupPolicyForm.Meta.fields)
    actions = None

    def get_queryset(self, request: HttpRequest) -> QuerySet[DatasetManagementConfiguration]:
        """Return only the trusted host organization's policy when its manager is admitted."""
        organization = getattr(request, "organization", None)
        if not isinstance(organization, Organization) or not user_can_manage_organization(
            request.user, organization
        ):
            return DatasetManagementConfiguration.objects.none()
        return (
            super()
            .get_queryset(request)
            .filter(organization=organization)
            .select_related("organization")
        )

    def has_module_permission(self, request: HttpRequest) -> bool:
        """Expose policy settings only to staff who manage this organization."""
        return (
            request.user.is_active and request.user.is_staff and self.get_queryset(request).exists()
        )

    def has_view_permission(
        self, request: HttpRequest, obj: DatasetManagementConfiguration | None = None
    ) -> bool:
        """Enforce object scope for list and direct policy URLs."""
        return self.has_module_permission(request) and (
            obj is None or self.get_queryset(request).filter(pk=obj.pk).exists()
        )

    def has_change_permission(
        self, request: HttpRequest, obj: DatasetManagementConfiguration | None = None
    ) -> bool:
        """Recheck organization authority for policy changes."""
        return self.has_view_permission(request, obj)

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Keep dataset management rollout enablement in its existing operational flow."""
        return False

    def has_delete_permission(
        self, request: HttpRequest, obj: DatasetManagementConfiguration | None = None
    ) -> bool:
        """Preserve durable policy and prevent generic deletion."""
        return False
