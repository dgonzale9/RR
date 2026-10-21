from __future__ import annotations

import math
import re
from collections.abc import Mapping
from uuid import UUID

from resource_registry.application.contracts import (
    FrozenJsonMapping,
    TransformationExecutionRequest,
    TransformationInputBinding,
    ValidationError,
)

_IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_CORRELATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_INPUT_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,254}$")


class TransformationExecutionRequestValidator:
    """Validate application request shape without duplicating runtime semantics."""

    def validate(self, *, request: TransformationExecutionRequest) -> None:
        """Reject malformed identifiers and mutable or unsafe application values."""
        correlation_id = (
            request.correlation_id
            if isinstance(request.correlation_id, str) and request.correlation_id.strip()
            else None
        )
        for field_name, value in (
            ("tenant_id", request.tenant_id),
            ("definition_id", request.definition_id),
            ("definition_version_id", request.definition_version_id),
        ):
            self._validate_uuid(value=value, field_name=field_name, correlation_id=correlation_id)
        if not isinstance(request.caller_idempotency_key, str) or not (
            _IDEMPOTENCY_KEY_PATTERN.fullmatch(request.caller_idempotency_key)
        ):
            self._raise_validation(
                code="invalid_transformation_idempotency_key",
                message="The transformation idempotency key is invalid.",
                correlation_id=correlation_id,
                field_name="caller_idempotency_key",
            )
        if not isinstance(request.correlation_id, str) or not (
            _CORRELATION_ID_PATTERN.fullmatch(request.correlation_id)
        ):
            self._raise_validation(
                code="invalid_transformation_correlation_id",
                message="The transformation correlation ID is invalid.",
                correlation_id=None,
                field_name="correlation_id",
            )
        if not isinstance(request.parameters, FrozenJsonMapping):
            self._raise_validation(
                code="invalid_transformation_parameters",
                message="Transformation parameters must be immutable JSON data.",
                correlation_id=request.correlation_id,
                field_name="parameters",
            )
        self._validate_immutable_value(
            value=request.parameters,
            correlation_id=request.correlation_id,
        )
        if not isinstance(request.input_bindings, tuple):
            self._raise_validation(
                code="invalid_transformation_input_bindings",
                message="Transformation input bindings must be immutable.",
                correlation_id=request.correlation_id,
                field_name="input_bindings",
            )
        for binding in request.input_bindings:
            if not isinstance(binding, TransformationInputBinding):
                self._raise_validation(
                    code="invalid_transformation_input_bindings",
                    message="Transformation input bindings must use the application contract.",
                    correlation_id=request.correlation_id,
                    field_name="input_bindings",
                )
            if not isinstance(binding.input_name, str) or not (
                _INPUT_NAME_PATTERN.fullmatch(binding.input_name)
            ):
                self._raise_validation(
                    code="invalid_transformation_input_name",
                    message="A transformation input name is invalid.",
                    correlation_id=request.correlation_id,
                    field_name="input_bindings",
                )
            self._validate_uuid(
                value=binding.dataset_id,
                field_name="dataset_id",
                correlation_id=request.correlation_id,
            )
            self._validate_uuid(
                value=binding.dataset_version_id,
                field_name="dataset_version_id",
                correlation_id=request.correlation_id,
            )
            if binding.artifact_id is not None:
                self._validate_uuid(
                    value=binding.artifact_id,
                    field_name="artifact_id",
                    correlation_id=request.correlation_id,
                )

    def _validate_immutable_value(self, *, value: object, correlation_id: str) -> None:
        if isinstance(value, Mapping):
            if not isinstance(value, FrozenJsonMapping):
                self._raise_validation(
                    code="invalid_transformation_parameters",
                    message="Transformation parameters must be recursively immutable.",
                    correlation_id=correlation_id,
                    field_name="parameters",
                )
            for nested_value in value.values():
                self._validate_immutable_value(
                    value=nested_value,
                    correlation_id=correlation_id,
                )
        elif isinstance(value, tuple):
            for nested_value in value:
                self._validate_immutable_value(
                    value=nested_value,
                    correlation_id=correlation_id,
                )
        elif isinstance(value, bool | str) or value is None or isinstance(value, int):
            return
        elif isinstance(value, float) and math.isfinite(value):
            return
        else:
            self._raise_validation(
                code="invalid_transformation_parameters",
                message="Transformation parameters must be immutable finite JSON data.",
                correlation_id=correlation_id,
                field_name="parameters",
            )

    def _validate_uuid(
        self,
        *,
        value: str,
        field_name: str,
        correlation_id: str | None,
    ) -> None:
        try:
            parsed = UUID(value)
        except (TypeError, ValueError, AttributeError):
            parsed = None
        if parsed is None or str(parsed) != value.lower():
            self._raise_validation(
                code="invalid_transformation_identifier",
                message="A required transformation identifier is invalid.",
                correlation_id=correlation_id,
                field_name=field_name,
            )

    @staticmethod
    def _raise_validation(
        *,
        code: str,
        message: str,
        correlation_id: str | None,
        field_name: str,
    ) -> None:
        raise ValidationError(
            code=code,
            public_message=message,
            correlation_id=correlation_id,
            metadata={"field_names": [field_name]},
        )
