from __future__ import annotations

from collections.abc import Mapping

from jsonschema import Draft202012Validator, FormatChecker  # type: ignore[import-untyped]

from resource_registry.application.contracts import ValidationError

_UUID_SCHEMA: dict[str, object] = {"type": "string", "format": "uuid"}
_BOUNDED_TEXT_SCHEMA: dict[str, object] = {"type": "string", "minLength": 1, "maxLength": 128}

INPUT_BINDING_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["input_name", "dataset_id", "dataset_version_id"],
    "properties": {
        "input_name": {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9_-]{0,254}$"},
        "dataset_id": _UUID_SCHEMA,
        "dataset_version_id": _UUID_SCHEMA,
        "artifact_id": {"oneOf": [_UUID_SCHEMA, {"type": "null"}]},
    },
}

START_TRANSFORMATION_RUN_SCHEMA: dict[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "definition_id",
        "definition_version_id",
        "parameters",
        "input_bindings",
        "idempotency_key",
    ],
    "properties": {
        "tenant_id": _UUID_SCHEMA,
        "definition_id": _UUID_SCHEMA,
        "definition_version_id": _UUID_SCHEMA,
        "parameters": {"type": "object"},
        "input_bindings": {
            "type": "array",
            "items": INPUT_BINDING_SCHEMA,
            "maxItems": 100,
        },
        "idempotency_key": {
            "type": "string",
            "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$",
        },
        "correlation_id": _BOUNDED_TEXT_SCHEMA,
    },
}

GET_TRANSFORMATION_RUN_SCHEMA: dict[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["run_id"],
    "properties": {"tenant_id": _UUID_SCHEMA, "run_id": _UUID_SCHEMA},
}

LIST_TRANSFORMATION_RUNS_SCHEMA: dict[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "tenant_id": _UUID_SCHEMA,
        "status": {
            "type": "string",
            "enum": ["QUEUED", "RUNNING", "OUTPUT_PENDING", "SUCCEEDED", "FAILED", "CANCELLED"],
        },
        "definition_id": _UUID_SCHEMA,
        "definition_version_id": _UUID_SCHEMA,
        "created_after": {"type": "string", "format": "date-time"},
        "created_before": {"type": "string", "format": "date-time"},
        "correlation_id": _BOUNDED_TEXT_SCHEMA,
        "limit": {"type": "integer"},
        "offset": {"type": "integer"},
    },
}

LIST_TRANSFORMATION_STEPS_SCHEMA = GET_TRANSFORMATION_RUN_SCHEMA
LIST_TRANSFORMATION_OUTPUTS_SCHEMA = GET_TRANSFORMATION_RUN_SCHEMA
GET_TRANSFORMATION_DIAGNOSTICS_SCHEMA = GET_TRANSFORMATION_RUN_SCHEMA


def validate_bif_input(
    *,
    schema: Mapping[str, object],
    payload: Mapping[str, object],
    correlation_id: str | None,
) -> None:
    """Validate transport shape with JSON Schema Draft 2020-12."""
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(payload), key=lambda error: tuple(error.absolute_path))
    if not errors:
        return
    field_names = sorted(
        {str(error.absolute_path[0]) if error.absolute_path else "request" for error in errors},
    )
    raise ValidationError(
        code="invalid_transformation_bif_request",
        public_message="The transformation built-in function request is invalid.",
        correlation_id=correlation_id,
        metadata={"field_names": field_names},
    )
