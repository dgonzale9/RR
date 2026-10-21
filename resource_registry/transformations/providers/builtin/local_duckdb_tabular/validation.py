from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]  # No upstream stubs.

from resource_registry.transformations.binding.immutable_json import thaw_json_value
from resource_registry.transformations.providers import (
    TransformationProviderValidationFinding,
    TransformationProviderValidationRequest,
    TransformationProviderValidationResult,
)
from resource_registry.transformations.providers.builtin.local_duckdb_tabular.capabilities import (
    PARQUET_FORMAT,
    PROJECT_COLUMNS_IDENTITY,
)

PROJECT_COLUMNS_CONFIGURATION_SCHEMA: Mapping[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["columns"],
    "properties": {
        "columns": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["source", "target"],
                "properties": {
                    "source": {"type": "string", "minLength": 1},
                    "target": {"type": "string", "minLength": 1},
                },
            },
        },
    },
}


@dataclass(frozen=True)
class ProjectedColumn:
    """Describe one exact source-to-target projection."""

    source: str
    target: str


def validate_project_columns_request(
    request: TransformationProviderValidationRequest,
) -> TransformationProviderValidationResult:
    """Validate one immutable project-columns configuration deterministically."""
    findings: list[TransformationProviderValidationFinding] = []
    if request.identity != PROJECT_COLUMNS_IDENTITY:
        findings.append(_finding("operation_identity_invalid", "$.identity"))
    if request.declared_input_formats != (PARQUET_FORMAT,):
        findings.append(_finding("input_contract_invalid", "$.inputs"))
    if request.declared_output_formats != (PARQUET_FORMAT,):
        findings.append(_finding("output_contract_invalid", "$.outputs"))
    validator = Draft202012Validator(PROJECT_COLUMNS_CONFIGURATION_SCHEMA)
    mutable_configuration = thaw_json_value(request.configuration)
    for error in sorted(validator.iter_errors(mutable_configuration), key=_schema_error_key):
        path = ".".join(str(item) for item in error.absolute_path)
        findings.append(
            _finding(
                f"configuration_{error.validator}_invalid",
                f"$.config{'.' + path if path else ''}",
            ),
        )
    if not findings:
        columns = parse_projected_columns(request.configuration)
        if len({column.source for column in columns}) != len(columns):
            findings.append(_finding("duplicate_source_column", "$.config.columns"))
        if len({column.target for column in columns}) != len(columns):
            findings.append(_finding("duplicate_target_column", "$.config.columns"))
        for index, column in enumerate(columns):
            if column.source != column.source.strip():
                findings.append(
                    _finding("source_column_not_exact", f"$.config.columns.{index}.source"),
                )
            if column.target != column.target.strip():
                findings.append(
                    _finding("target_column_not_exact", f"$.config.columns.{index}.target"),
                )
    return TransformationProviderValidationResult(
        is_valid=not findings,
        findings=tuple(findings),
    )


def parse_projected_columns(configuration: Mapping[str, object]) -> tuple[ProjectedColumn, ...]:
    """Parse a configuration already validated against the approved schema."""
    values = configuration.get("columns")
    if not isinstance(values, Sequence) or isinstance(values, str | bytes | bytearray):
        return ()
    columns: list[ProjectedColumn] = []
    for value in values:
        if not isinstance(value, Mapping):
            return ()
        source = value.get("source")
        target = value.get("target")
        if not isinstance(source, str) or not isinstance(target, str):
            return ()
        columns.append(ProjectedColumn(source=source, target=target))
    return tuple(columns)


def _finding(code: str, path: str) -> TransformationProviderValidationFinding:
    return TransformationProviderValidationFinding(
        identity=PROJECT_COLUMNS_IDENTITY,
        code=code,
        path=path,
        severity="error",
        message="Project-columns configuration does not satisfy the approved contract.",
    )


def _schema_error_key(error: object) -> tuple[str, str]:
    path = getattr(error, "absolute_path", ())
    validator = getattr(error, "validator", "")
    return (".".join(str(item) for item in path), str(validator))
