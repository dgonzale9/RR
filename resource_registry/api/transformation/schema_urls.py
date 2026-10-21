"""Limit the canonical transformation schema to the approved REST surface."""

from django.urls import include, path

urlpatterns = [
    path("api/v1/transformations/", include("resource_registry.api.transformation.urls")),
]
