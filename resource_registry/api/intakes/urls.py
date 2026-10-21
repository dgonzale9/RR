"""Routes for private unassigned workbook intake."""

from django.urls import path

from resource_registry.api.intakes.views import (
    StagedResourceIntakeBlockView,
    StagedResourceIntakeCancelView,
    StagedResourceIntakeCollectionView,
    StagedResourceIntakeCompleteView,
    StagedResourceIntakeDetailView,
    StagedResourceIntakePublishView,
)

app_name = "resource_registry_intakes"

urlpatterns = [
    path("intakes/", StagedResourceIntakeCollectionView.as_view(), name="collection"),
    path("intakes/<uuid:intake_id>/", StagedResourceIntakeDetailView.as_view(), name="detail"),
    path(
        "intakes/<uuid:intake_id>/blocks/<int:block_index>/",
        StagedResourceIntakeBlockView.as_view(),
        name="block",
    ),
    path(
        "intakes/<uuid:intake_id>/complete/",
        StagedResourceIntakeCompleteView.as_view(),
        name="complete",
    ),
    path(
        "intakes/<uuid:intake_id>/cancel/", StagedResourceIntakeCancelView.as_view(), name="cancel"
    ),
    path(
        "intakes/<uuid:intake_id>/publish/",
        StagedResourceIntakePublishView.as_view(),
        name="publish",
    ),
]
