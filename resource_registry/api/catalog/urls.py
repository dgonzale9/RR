"""Catalogue list routes for AICP / env-sync browsers."""

from django.urls import path

from resource_registry.api.catalog.views import DatasetCatalogView, NamespaceCatalogView
from resource_registry.api.management.views import (
    DatasetChangeActionView,
    DatasetChangeCollectionView,
    DatasetChangeDetailView,
    DatasetDetailView,
    DatasetExternalSourceDeactivateView,
    DatasetExternalSourceDetailView,
    DatasetLifecycleView,
    DatasetRelatedView,
    DatasetRestoreView,
)
from resource_registry.cleanup.api import (
    CleanupRequestActionView,
    CleanupRequestCollectionView,
    CleanupRequestConfirmationView,
    CleanupRequestDetailView,
    DatasetDeletionPreviewView,
    DatasetDeletionRequestView,
)

app_name = "resource_registry_catalog"

urlpatterns = [
    path(
        "dataset-sources/<uuid:source_id>/",
        DatasetExternalSourceDetailView.as_view(),
        name="dataset-source-detail",
    ),
    path(
        "dataset-sources/<uuid:source_id>/deactivate/",
        DatasetExternalSourceDeactivateView.as_view(),
        name="dataset-source-deactivate",
    ),
    path(
        "datasets/<uuid:dataset_id>/deletion-preview/",
        DatasetDeletionPreviewView.as_view(),
        name="dataset-deletion-preview",
    ),
    path(
        "datasets/<uuid:dataset_id>/deletion-requests/",
        DatasetDeletionRequestView.as_view(),
        name="dataset-deletion-request",
    ),
    path("cleanup-requests/", CleanupRequestCollectionView.as_view(), name="cleanup-requests"),
    path(
        "cleanup-requests/<uuid:request_id>/",
        CleanupRequestDetailView.as_view(),
        name="cleanup-detail",
    ),
    path("namespaces/", NamespaceCatalogView.as_view(), name="namespaces"),
    path("datasets/", DatasetCatalogView.as_view(), name="datasets"),
    path("datasets/<uuid:dataset_id>/", DatasetDetailView.as_view(), name="dataset-detail"),
    path("dataset-changes/", DatasetChangeCollectionView.as_view(), name="dataset-changes"),
    path(
        "dataset-changes/<uuid:change_id>/",
        DatasetChangeDetailView.as_view(),
        name="dataset-change-detail",
    ),
]
urlpatterns += [
    path(
        f"cleanup-requests/<uuid:request_id>/{operation}/",
        (
            CleanupRequestConfirmationView if operation == "confirm" else CleanupRequestActionView
        ).as_view(),
        {"operation": operation},
        name=f"cleanup-{operation}",
    )
    for operation in ("cancel", "retry", "confirm")
]
urlpatterns += [
    path(
        f"datasets/<uuid:dataset_id>/{section}/",
        DatasetRelatedView.as_view(),
        {"section": section},
        name=f"dataset-{section}",
    )
    for section in ("versions", "preview", "activity", "usage")
]
urlpatterns += [
    path(
        f"datasets/<uuid:dataset_id>/{operation}/",
        (DatasetRestoreView if operation == "restore-version" else DatasetLifecycleView).as_view(),
        {"operation": operation},
        name=f"dataset-{operation}",
    )
    for operation in ("archive", "unarchive", "restore-version")
]
urlpatterns += [
    path(
        f"dataset-changes/<uuid:change_id>/{operation}/",
        DatasetChangeActionView.as_view(),
        {"operation": operation},
        name=f"dataset-change-{operation}",
    )
    for operation in ("publish", "cancel", "retry")
]
