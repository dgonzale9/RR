"""Resolve visible datasets for an authorized Resource Registry Workspace."""

from __future__ import annotations

from typing import Any, cast

from django.db.models import Exists, OuterRef, Q

from resource_registry.dataset_visibility import build_dataset_visibility_filter
from resource_registry.models import (
    Dataset,
    DatasetStatus,
    DatasetVisibilityScope,
    StagedResourcePublicationDataset,
    StagedResourcePublicationStatus,
)


def get_visible_workspace_dataset_ids(*, namespace_id: str, workspace_id: int) -> frozenset[str]:
    """Return active Workspace and Organization datasets admitted to a Workspace.

    Args:
        namespace_id: Canonical namespace of the authorized Workspace.
        workspace_id: Persisted identifier of that Workspace.

    Returns:
        Visible dataset identifiers after publication assignment checks.
    """
    staged_assignment_queryset = StagedResourcePublicationDataset.objects.filter(
        dataset_id=OuterRef("pk"),
    )
    active_staged_assignment_queryset = staged_assignment_queryset.filter(
        detached_at__isnull=True,
        publication__assignment_status=StagedResourcePublicationStatus.ACTIVE,
    ).filter(
        Q(
            publication__registry_namespace_id=namespace_id,
            publication__workspace_id=workspace_id,
            publication__visibility_scope=DatasetVisibilityScope.WORKSPACE,
        )
        | Q(
            publication__organization_id=OuterRef("organization_id"),
            publication__workspace__isnull=True,
            publication__visibility_scope=DatasetVisibilityScope.ORGANIZATION,
        ),
    )
    dataset_queryset = Dataset.objects.filter(
        build_dataset_visibility_filter(tenant_id=namespace_id),
        status__in=(DatasetStatus.DRAFT, DatasetStatus.ACTIVE),
        archived_at__isnull=True,
    )
    # django-stubs' annotate plugin is incompatible with the pinned mypy version.
    annotated_queryset = cast(Any, dataset_queryset)
    return frozenset(
        str(dataset_id)
        for dataset_id in (
            annotated_queryset.annotate(
                has_staged_assignment=Exists(staged_assignment_queryset),
                has_active_staged_assignment=Exists(active_staged_assignment_queryset),
            )
            .filter(Q(has_staged_assignment=False) | Q(has_active_staged_assignment=True))
            .values_list("id", flat=True)
        )
    )
