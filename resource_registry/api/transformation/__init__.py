"""Expose the versioned transformation REST transport adapter."""

from resource_registry.api.transformation.dependencies import (
    TransformationRestApplicationServices,
    configure_transformation_rest_application_services,
)

__all__ = [
    "TransformationRestApplicationServices",
    "configure_transformation_rest_application_services",
]
