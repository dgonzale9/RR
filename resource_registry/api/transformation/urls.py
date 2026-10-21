from django.urls import path

from resource_registry.api.transformation.views import (
    TransformationRunCollectionView,
    TransformationRunDetailView,
    TransformationRunDiagnosticsView,
    TransformationRunOutputListView,
    TransformationRunStepListView,
)

app_name = "transformation_api"

urlpatterns = [
    path("runs/", TransformationRunCollectionView.as_view(), name="run-collection"),
    path("runs/<uuid:run_id>", TransformationRunDetailView.as_view(), name="run-detail"),
    path(
        "runs/<uuid:run_id>/steps/",
        TransformationRunStepListView.as_view(),
        name="run-step-list",
    ),
    path(
        "runs/<uuid:run_id>/outputs/",
        TransformationRunOutputListView.as_view(),
        name="run-output-list",
    ),
    path(
        "runs/<uuid:run_id>/diagnostics/",
        TransformationRunDiagnosticsView.as_view(),
        name="run-diagnostics",
    ),
]
