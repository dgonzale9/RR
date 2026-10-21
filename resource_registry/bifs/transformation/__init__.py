"""Expose thin transformation built-in function adapters."""

from resource_registry.bifs.transformation.definitions import (
    TRANSFORMATION_BIF_CONTRACT_VERSION,
    TRANSFORMATION_BIF_DEFINITIONS,
    TransformationBifDefinition,
)
from resource_registry.bifs.transformation.handlers import (
    TransformationBifApplicationServices,
    resource_registry_get_transformation_diagnostics,
    resource_registry_get_transformation_run,
    resource_registry_list_transformation_outputs,
    resource_registry_list_transformation_runs,
    resource_registry_list_transformation_steps,
    resource_registry_start_transformation_run,
)
from resource_registry.bifs.transformation.registration import build_transformation_bif_registry

__all__ = [
    "TRANSFORMATION_BIF_CONTRACT_VERSION",
    "TRANSFORMATION_BIF_DEFINITIONS",
    "TransformationBifApplicationServices",
    "TransformationBifDefinition",
    "build_transformation_bif_registry",
    "resource_registry_get_transformation_diagnostics",
    "resource_registry_get_transformation_run",
    "resource_registry_list_transformation_outputs",
    "resource_registry_list_transformation_runs",
    "resource_registry_list_transformation_steps",
    "resource_registry_start_transformation_run",
]
