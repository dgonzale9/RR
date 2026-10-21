from __future__ import annotations

from typing import cast

from rest_framework import serializers

from resource_registry.application.contracts import (
    ErrorCategory,
    ExecutionState,
    HealthState,
    OutputKind,
    OutputStatus,
    RunStatus,
    StepStatus,
    TransformationExecutionRequest,
    TransformationInputBinding,
)
from resource_registry.application.read import TransformationRunFilters


class TransformationInputBindingSerializer(serializers.Serializer[TransformationInputBinding]):
    """Map an explicit input reference without resolving registry resources."""

    input_name = serializers.CharField(max_length=255)
    dataset_id = serializers.UUIDField(format="hex_verbose")
    dataset_version_id = serializers.UUIDField(format="hex_verbose")
    artifact_id = serializers.UUIDField(format="hex_verbose", required=False, allow_null=True)


class TransformationExecutionRequestSerializer(serializers.Serializer[object]):
    """Parse one transformation submission into the immutable application command DTO."""

    tenant_id = serializers.UUIDField(format="hex_verbose", required=False)
    definition_id = serializers.UUIDField(format="hex_verbose")
    definition_version_id = serializers.UUIDField(format="hex_verbose")
    parameters = serializers.JSONField(required=False, default=dict)
    input_bindings = TransformationInputBindingSerializer(many=True)
    caller_idempotency_key = serializers.CharField(max_length=128)
    correlation_id = serializers.CharField(max_length=128)

    def to_application_request(self, *, tenant_id: str) -> TransformationExecutionRequest:
        """Return a Story 1 request after DRF transport validation succeeds.

        Args:
            tenant_id: Canonical namespace UUID admitted for the authenticated user.

        Returns:
            Immutable application request scoped to the admitted namespace.
        """
        values = self.validated_data
        bindings = tuple(
            TransformationInputBinding(
                input_name=str(item["input_name"]),
                dataset_id=str(item["dataset_id"]),
                dataset_version_id=str(item["dataset_version_id"]),
                artifact_id=(
                    str(item["artifact_id"]) if item.get("artifact_id") is not None else None
                ),
            )
            for item in cast(list[dict[str, object]], values["input_bindings"])
        )
        return TransformationExecutionRequest(
            tenant_id=tenant_id,
            definition_id=str(values["definition_id"]),
            definition_version_id=str(values["definition_version_id"]),
            parameters=cast(dict[str, object], values["parameters"]),
            input_bindings=bindings,
            caller_idempotency_key=str(values["caller_idempotency_key"]),
            correlation_id=str(values["correlation_id"]),
        )

    def get_requested_tenant_id(self) -> str | None:
        """Return the optional namespace selection supplied in the request body.

        Returns:
            Canonical body UUID text when supplied; otherwise None.
        """
        tenant_id = self.validated_data.get("tenant_id")
        return str(tenant_id) if tenant_id is not None else None


class TransformationRunFilterSerializer(serializers.Serializer[object]):
    """Parse only the filters and pagination supported by the Story 2 read service."""

    status = serializers.ChoiceField(choices=tuple(RunStatus), required=False)
    definition_id = serializers.UUIDField(format="hex_verbose", required=False)
    definition_version_id = serializers.UUIDField(format="hex_verbose", required=False)
    created_after = serializers.DateTimeField(required=False)
    created_before = serializers.DateTimeField(required=False)
    correlation_id = serializers.CharField(max_length=128, required=False)
    limit = serializers.IntegerField(required=False, default=50)
    offset = serializers.IntegerField(required=False, default=0)

    def to_filters(self) -> TransformationRunFilters:
        """Return immutable Story 2 filters after query parsing succeeds."""
        values = self.validated_data
        status_value = values.get("status")
        return TransformationRunFilters(
            status=RunStatus(str(status_value)) if status_value is not None else None,
            definition_id=(
                str(values["definition_id"]) if values.get("definition_id") is not None else None
            ),
            definition_version_id=(
                str(values["definition_version_id"])
                if values.get("definition_version_id") is not None
                else None
            ),
            created_after=values.get("created_after"),
            created_before=values.get("created_before"),
            correlation_id=(
                str(values["correlation_id"]) if values.get("correlation_id") is not None else None
            ),
        )

    def get_limit(self) -> int:
        """Return the validated Story 2 page limit."""
        return int(self.validated_data["limit"])

    def get_offset(self) -> int:
        """Return the validated Story 2 page offset."""
        return int(self.validated_data["offset"])


class TransformationRunReferenceSerializer(serializers.Serializer[object]):
    """Serialize a public immutable run reference."""

    contract_version = serializers.CharField(read_only=True)
    tenant_id = serializers.UUIDField(format="hex_verbose", read_only=True)
    run_id = serializers.UUIDField(format="hex_verbose", read_only=True)
    definition_id = serializers.UUIDField(format="hex_verbose", read_only=True)
    definition_version_id = serializers.UUIDField(format="hex_verbose", read_only=True)
    status = serializers.ChoiceField(choices=tuple(RunStatus), read_only=True)


class TransformationExecutionAcceptedSerializer(serializers.Serializer[object]):
    """Serialize durable asynchronous acceptance without implying completion."""

    contract_version = serializers.CharField(read_only=True)
    run = TransformationRunReferenceSerializer(read_only=True)
    execution_state = serializers.ChoiceField(choices=tuple(ExecutionState), read_only=True)
    idempotent_replay = serializers.BooleanField(read_only=True)
    correlation_id = serializers.CharField(read_only=True)


class TransformationStepSummarySerializer(serializers.Serializer[object]):
    """Serialize a safe transformation step-attempt summary."""

    contract_version = serializers.CharField(read_only=True)
    step_id = serializers.CharField(read_only=True)
    attempt_number = serializers.IntegerField(read_only=True)
    status = serializers.ChoiceField(choices=tuple(StepStatus), read_only=True)
    duration_seconds = serializers.FloatField(read_only=True, allow_null=True)
    error_code = serializers.CharField(read_only=True, allow_null=True)


class TransformationOutputReferenceSerializer(serializers.Serializer[object]):
    """Serialize public output registration identifiers without storage references."""

    contract_version = serializers.CharField(read_only=True)
    output_id = serializers.UUIDField(format="hex_verbose", read_only=True)
    logical_name = serializers.CharField(read_only=True)
    kind = serializers.ChoiceField(choices=tuple(OutputKind), read_only=True)
    status = serializers.ChoiceField(choices=tuple(OutputStatus), read_only=True)
    dataset_id = serializers.UUIDField(format="hex_verbose", read_only=True, allow_null=True)
    dataset_version_id = serializers.UUIDField(
        format="hex_verbose",
        read_only=True,
        allow_null=True,
    )
    artifact_id = serializers.UUIDField(format="hex_verbose", read_only=True, allow_null=True)


class TransformationDiagnosticSummarySerializer(serializers.Serializer[object]):
    """Serialize safe aggregate diagnostics without runtime payloads."""

    contract_version = serializers.CharField(read_only=True)
    health_state = serializers.ChoiceField(choices=tuple(HealthState), read_only=True)
    aggregate_counts = serializers.JSONField(read_only=True)
    warnings = serializers.ListField(child=serializers.CharField(), read_only=True)
    durations = serializers.JSONField(read_only=True)
    duration_seconds = serializers.FloatField(read_only=True, allow_null=True)
    progress = serializers.FloatField(read_only=True, allow_null=True)


class ApplicationErrorSerializer(serializers.Serializer[object]):
    """Document the stable Story 1 application error transport schema."""

    contract_version = serializers.CharField(read_only=True)
    code = serializers.CharField(read_only=True)
    category = serializers.ChoiceField(choices=tuple(ErrorCategory), read_only=True)
    message = serializers.CharField(read_only=True)
    retryable = serializers.BooleanField(read_only=True)
    correlation_id = serializers.CharField(read_only=True, allow_null=True, required=False)
    metadata = serializers.JSONField(read_only=True)
