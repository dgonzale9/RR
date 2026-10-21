"""URLs for the Resource Registry large-file upload control plane."""

from django.urls import path

from resource_registry.api.uploads.views import (
    UploadSessionCancelView,
    UploadSessionCollectionView,
    UploadSessionCompleteView,
    UploadSessionDetailView,
    UploadSessionRegistrationView,
)

app_name = "resource_registry_uploads"

urlpatterns = [
    path("uploads/", UploadSessionCollectionView.as_view(), name="collection"),
    path("uploads/<uuid:upload_id>/", UploadSessionDetailView.as_view(), name="detail"),
    path(
        "uploads/<uuid:upload_id>/complete/",
        UploadSessionCompleteView.as_view(),
        name="complete",
    ),
    path("uploads/<uuid:upload_id>/cancel/", UploadSessionCancelView.as_view(), name="cancel"),
    path(
        "uploads/<uuid:upload_id>/register/",
        UploadSessionRegistrationView.as_view(),
        name="register",
    ),
]
