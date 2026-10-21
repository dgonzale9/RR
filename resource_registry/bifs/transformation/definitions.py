from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from resource_registry.bifs.transformation.schemas import (
    GET_TRANSFORMATION_DIAGNOSTICS_SCHEMA,
    GET_TRANSFORMATION_RUN_SCHEMA,
    LIST_TRANSFORMATION_OUTPUTS_SCHEMA,
    LIST_TRANSFORMATION_RUNS_SCHEMA,
    LIST_TRANSFORMATION_STEPS_SCHEMA,
    START_TRANSFORMATION_RUN_SCHEMA,
)

TRANSFORMATION_BIF_CONTRACT_VERSION = "transformation-bif/1.0"
TRANSFORMATION_BIF_OUTPUT_SCHEMA: dict[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "oneOf": [
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["success", "data"],
            "properties": {
                "success": {"const": True},
                "data": {"type": "object"},
            },
        },
        {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "success",
                "status",
                "code",
                "category",
                "error",
                "retryable",
                "correlation_id",
                "metadata",
            ],
            "properties": {
                "success": {"const": False},
                "status": {"type": "integer"},
                "code": {"type": "string"},
                "category": {"type": "string"},
                "error": {"type": "string"},
                "retryable": {"type": "boolean"},
                "correlation_id": {"type": ["string", "null"]},
                "metadata": {"type": "object"},
            },
        },
    ],
}


@dataclass(frozen=True, kw_only=True)
class TransformationBifDefinition:
    """Describe one statically registered transformation BIF contract."""

    name: str
    description: str
    input_schema: Mapping[str, object]
    output_schema: Mapping[str, object]
    output_description: str
    required_permissions: tuple[str, ...]
    capability_key: str
    is_asynchronous: bool
    version: str = TRANSFORMATION_BIF_CONTRACT_VERSION


TRANSFORMATION_BIF_DEFINITIONS: tuple[TransformationBifDefinition, ...] = (
    TransformationBifDefinition(
        name="resource_registry_start_transformation_run",
        description=(
            "Durably submit one exact transformation definition version for asynchronous execution."
        ),
        input_schema=START_TRANSFORMATION_RUN_SCHEMA,
        output_schema=TRANSFORMATION_BIF_OUTPUT_SCHEMA,
        output_description="Accepted public run reference and idempotency outcome.",
        required_permissions=(
            "resource_registry.execute_transformation",
            "resource_registry.use_transformation_provider",
        ),
        capability_key="resource_registry.transformation.run.start",
        is_asynchronous=True,
    ),
    TransformationBifDefinition(
        name="resource_registry_get_transformation_run",
        description="Get one authorized public transformation run reference.",
        input_schema=GET_TRANSFORMATION_RUN_SCHEMA,
        output_schema=TRANSFORMATION_BIF_OUTPUT_SCHEMA,
        output_description="Public transformation run reference.",
        required_permissions=("resource_registry.view_transformationrun",),
        capability_key="resource_registry.transformation.run.read",
        is_asynchronous=False,
    ),
    TransformationBifDefinition(
        name="resource_registry_list_transformation_runs",
        description="List authorized transformation runs with approved filters and pagination.",
        input_schema=LIST_TRANSFORMATION_RUNS_SCHEMA,
        output_schema=TRANSFORMATION_BIF_OUTPUT_SCHEMA,
        output_description="Ordered public transformation run references.",
        required_permissions=("resource_registry.view_transformationrun",),
        capability_key="resource_registry.transformation.run.read",
        is_asynchronous=False,
    ),
    TransformationBifDefinition(
        name="resource_registry_list_transformation_steps",
        description="List public step-attempt summaries for one authorized run.",
        input_schema=LIST_TRANSFORMATION_STEPS_SCHEMA,
        output_schema=TRANSFORMATION_BIF_OUTPUT_SCHEMA,
        output_description="Ordered public step-attempt summaries.",
        required_permissions=("resource_registry.view_transformationrun",),
        capability_key="resource_registry.transformation.step.read",
        is_asynchronous=False,
    ),
    TransformationBifDefinition(
        name="resource_registry_list_transformation_outputs",
        description="List public output references for one authorized transformation run.",
        input_schema=LIST_TRANSFORMATION_OUTPUTS_SCHEMA,
        output_schema=TRANSFORMATION_BIF_OUTPUT_SCHEMA,
        output_description="Ordered public output references without storage locations.",
        required_permissions=("resource_registry.view_transformationrun",),
        capability_key="resource_registry.transformation.output.read",
        is_asynchronous=False,
    ),
    TransformationBifDefinition(
        name="resource_registry_get_transformation_diagnostics",
        description="Get sanitized aggregate diagnostics for one authorized transformation run.",
        input_schema=GET_TRANSFORMATION_DIAGNOSTICS_SCHEMA,
        output_schema=TRANSFORMATION_BIF_OUTPUT_SCHEMA,
        output_description="Sanitized aggregate diagnostics.",
        required_permissions=("resource_registry.view_transformation_diagnostics",),
        capability_key="resource_registry.transformation.diagnostics.read",
        is_asynchronous=False,
    ),
)
