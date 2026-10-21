from __future__ import annotations

import json
from collections.abc import Mapping
from importlib import resources

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError

from resource_registry.read.transformation_definition_version_read_service import (
    TransformationDefinitionVersionReadService,
)
from resource_registry.transformations.dsl.parser import (
    TransformationSpecificationParser,
    thaw_json_value,
)
from resource_registry.transformations.validation.exceptions import (
    TransformationDslSchemaVersionError,
    TransformationSpecificationParseError,
)
from resource_registry.transformations.validation.results import (
    TransformationStructuralValidationFinding,
    TransformationStructuralValidationResult,
)

SUPPORTED_DSL_VERSIONS = frozenset({"1.0"})
TRANSFORMATION_SCHEMA_RESOURCE = "transformation.schema.json"


class TransformationStructuralValidator:
    """Validate transformation specifications against the core DSL structure only."""

    def __init__(
        self,
        *,
        parser: TransformationSpecificationParser | None = None,
        version_read_service: TransformationDefinitionVersionReadService | None = None,
    ) -> None:
        self._parser = parser or TransformationSpecificationParser()
        self._version_read_service = (
            version_read_service or TransformationDefinitionVersionReadService()
        )

    def validate(self, specification: object) -> TransformationStructuralValidationResult:
        """Validate the structure of a parsed transformation specification document.

        Args:
            specification: Parsed transformation specification JSON.

        Returns:
            Structural validation result with findings and immutable normalized specification.
        """
        try:
            parsed_specification = self._parser.parse(specification)
        except TransformationSpecificationParseError as error:
            return _failure_result(
                TransformationStructuralValidationFinding(
                    code="parse_error",
                    message=str(error),
                ),
            )

        dsl_version = parsed_specification.specification.get("dsl_version")
        if isinstance(dsl_version, str) and dsl_version not in SUPPORTED_DSL_VERSIONS:
            return _failure_result(
                TransformationStructuralValidationFinding(
                    code="unsupported_dsl_version",
                    message=f"Unsupported Transformation DSL version '{dsl_version}'.",
                    path="$.dsl_version",
                ),
            )

        schema = load_transformation_schema()
        validator = Draft202012Validator(schema)
        mutable_specification = thaw_json_value(parsed_specification.specification)
        errors = sorted(
            validator.iter_errors(mutable_specification),
            key=lambda error: (list(error.path), list(error.schema_path)),
        )
        findings = tuple(_finding_from_schema_error(error) for error in errors)
        return TransformationStructuralValidationResult(
            success=not findings,
            findings=findings,
            normalized_specification=(parsed_specification.specification if not findings else None),
        )

    def validate_persisted_version(
        self,
        *,
        tenant_id: str,
        definition_id: str,
        version_id: str,
    ) -> TransformationStructuralValidationResult:
        """Validate a persisted transformation definition version through read services.

        Args:
            tenant_id: Tenant identifier for the parent transformation definition.
            definition_id: Parent transformation definition UUID.
            version_id: Transformation definition version UUID.

        Returns:
            Structural validation result for the persisted specification JSON.
        """
        version = self._version_read_service.get_version_by_id(
            tenant_id=tenant_id,
            definition_id=definition_id,
            version_id=version_id,
        )
        return self.validate(version.specification_json)


def load_transformation_schema(*, dsl_version: str = "1.0") -> Mapping[str, object]:
    """Load the code-owned JSON Schema for a supported Transformation DSL version.

    Args:
        dsl_version: Transformation DSL version to load.

    Returns:
        Parsed JSON Schema document.

    Raises:
        TransformationDslSchemaVersionError: If no schema is available for the requested version.
    """
    if dsl_version not in SUPPORTED_DSL_VERSIONS:
        raise TransformationDslSchemaVersionError(
            f"Unsupported Transformation DSL version '{dsl_version}'.",
        )
    schema_text = (
        resources.files("resource_registry.transformations.dsl.schemas")
        .joinpath(TRANSFORMATION_SCHEMA_RESOURCE)
        .read_text(encoding="utf-8")
    )
    schema = json.loads(schema_text)
    if not isinstance(schema, Mapping):
        raise TransformationDslSchemaVersionError(
            "Transformation DSL schema resource must contain a JSON object.",
        )
    Draft202012Validator.check_schema(schema)
    return schema


def _failure_result(
    finding: TransformationStructuralValidationFinding,
) -> TransformationStructuralValidationResult:
    """Build a failed validation result from a single finding."""
    return TransformationStructuralValidationResult(
        success=False,
        findings=(finding,),
    )


def _finding_from_schema_error(
    error: JsonSchemaValidationError,
) -> TransformationStructuralValidationFinding:
    """Convert a JSON Schema error into a deterministic structural finding."""
    path = "$"
    if error.path:
        path = "$." + ".".join(str(item) for item in error.path)
    return TransformationStructuralValidationFinding(
        code="schema_validation_error",
        message=str(error.message),
        path=path,
    )
