from __future__ import annotations

from pathlib import Path

from resource_registry.transformations.providers import (
    ProviderSideEffectDisposition,
    TransformationIntegrityMetadata,
    TransformationProviderCapabilityDescriptor,
    TransformationProviderExecutionError,
    TransformationProviderExecutionRequest,
    TransformationProviderExecutionResult,
    TransformationProviderFailure,
    TransformationProviderIdentity,
    TransformationProviderOutputReference,
    TransformationProviderRuntimeContext,
    TransformationProviderValidationRequest,
    TransformationProviderValidationResult,
)
from resource_registry.transformations.providers.builtin.local_duckdb_tabular.capabilities import (
    PARQUET_FORMAT,
    PROJECT_COLUMNS_IDENTITY,
    PROVIDER_CAPABILITY_DESCRIPTOR,
    PROVIDER_IDENTITY,
)
from resource_registry.transformations.providers.builtin.local_duckdb_tabular.validation import (
    parse_projected_columns,
    validate_project_columns_request,
)
from resource_registry.transformations.providers.dependencies import ProviderDependencies


class LocalDuckDbTabularProvider:
    """Project exact Parquet columns through injected local DuckDB runtime ports."""

    def __init__(self, *, dependencies: ProviderDependencies) -> None:
        self._dependencies = dependencies
        limits = dependencies.operational_limits
        self._capability_descriptor = (
            PROVIDER_CAPABILITY_DESCRIPTOR
            if limits is None
            else TransformationProviderCapabilityDescriptor(
                identity=PROVIDER_IDENTITY,
                operations=PROVIDER_CAPABILITY_DESCRIPTOR.operations,
                metadata={
                    "operational_limits": {
                        "maximum_input_bytes": limits.maximum_input_bytes,
                        "maximum_output_bytes": limits.maximum_output_bytes,
                        "maximum_row_count": limits.maximum_row_count,
                        "maximum_source_columns": limits.maximum_source_columns,
                        "maximum_selected_columns": limits.maximum_selected_columns,
                        "provider_timeout_seconds": limits.provider_timeout_seconds,
                        "materialization_timeout_seconds": limits.materialization_timeout_seconds,
                    },
                },
            )
        )

    @property
    def provider_identity(self) -> TransformationProviderIdentity:
        """Return the exact immutable production provider identity."""
        return PROVIDER_IDENTITY

    @property
    def capability_descriptor(self) -> TransformationProviderCapabilityDescriptor:
        """Return the immutable single-operation capability descriptor."""
        return self._capability_descriptor

    def validate_operation(
        self,
        *,
        request: TransformationProviderValidationRequest,
    ) -> TransformationProviderValidationResult:
        """Validate project-columns configuration without reading input data."""
        return validate_project_columns_request(request)

    def execute(
        self,
        *,
        request: TransformationProviderExecutionRequest,
        runtime_context: TransformationProviderRuntimeContext,
    ) -> TransformationProviderExecutionResult:
        """Materialize, project, publish, and return one verified Parquet reference."""
        self._validate_execution_contract(request=request)
        self._raise_if_cancelled_or_expired(runtime_context=runtime_context)
        input_reference = request.inputs[0]
        expected_output = request.expected_outputs[0]
        try:
            target = self._dependencies.output_workspace.prepare_output(
                run_id=request.run_id,
                attempt_idempotency_token=request.attempt_idempotency_token,
                logical_name=expected_output.logical_name,
                suffix=".parquet",
            )
        except Exception as error:
            raise self._failure(
                code="output_workspace_unavailable",
                message="Project-columns output workspace is unavailable.",
                retryable=True,
                disposition=ProviderSideEffectDisposition.NOT_STARTED,
            ) from error
        try:
            with self._dependencies.input_materializer.materialize(
                reference=input_reference,
            ) as materialized:
                self._raise_if_cancelled_or_expired(runtime_context=runtime_context)
                self._execute_projection(
                    input_path=materialized.local_path,
                    output_path=target.local_path,
                    request=request,
                )
        except TransformationProviderExecutionError:
            self._dependencies.output_workspace.discard_local_output(target=target)
            raise
        except Exception as error:
            self._dependencies.output_workspace.discard_local_output(target=target)
            raise self._failure(
                code="project_columns_execution_failed",
                message="Project-columns execution failed safely.",
                retryable=False,
                disposition=ProviderSideEffectDisposition.NOT_STARTED,
            ) from error
        try:
            self._raise_if_cancelled_or_expired(runtime_context=runtime_context)
            published = self._dependencies.output_workspace.publish_output(target=target)
        except TransformationProviderExecutionError:
            raise
        except Exception as error:
            raise self._failure(
                code="output_workspace_publish_failed",
                message="Project-columns output publication failed safely.",
                retryable=True,
                disposition=ProviderSideEffectDisposition.UNKNOWN,
            ) from error
        finally:
            self._dependencies.output_workspace.discard_local_output(target=target)
        return TransformationProviderExecutionResult(
            identity=PROJECT_COLUMNS_IDENTITY,
            outputs=(
                TransformationProviderOutputReference(
                    logical_name=expected_output.logical_name,
                    materialized_reference=published.materialized_reference,
                    data_format=PARQUET_FORMAT,
                    integrity=TransformationIntegrityMetadata(
                        checksum_algorithm="sha256",
                        checksum_value=published.checksum,
                        size_bytes=published.size_bytes,
                    ),
                    is_temporary=expected_output.is_temporary,
                ),
            ),
            side_effect_disposition=ProviderSideEffectDisposition.APPLIED,
        )

    def _validate_execution_contract(
        self,
        *,
        request: TransformationProviderExecutionRequest,
    ) -> None:
        validation = validate_project_columns_request(
            TransformationProviderValidationRequest(
                identity=request.identity,
                step_id=request.step_id,
                configuration=request.configuration,
                declared_input_formats=tuple(item.data_format for item in request.inputs),
                declared_output_formats=tuple(
                    data_format
                    for item in request.expected_outputs
                    for data_format in item.accepted_formats
                ),
            ),
        )
        if (
            not validation.is_valid
            or len(request.inputs) != 1
            or len(request.expected_outputs) != 1
            or request.expected_outputs[0].accepted_formats != (PARQUET_FORMAT,)
        ):
            raise self._failure(
                code="project_columns_contract_invalid",
                message="Project-columns execution contract is invalid.",
                retryable=False,
                disposition=ProviderSideEffectDisposition.NOT_STARTED,
            )

    def _execute_projection(
        self,
        *,
        input_path: str,
        output_path: str,
        request: TransformationProviderExecutionRequest,
    ) -> None:
        columns = parse_projected_columns(request.configuration)
        limits = self._dependencies.operational_limits
        if limits is not None and len(columns) > limits.maximum_selected_columns:
            raise self._failure(
                code="selected_column_limit_exceeded",
                message="Selected columns exceed the configured provider limit.",
                retryable=False,
                disposition=ProviderSideEffectDisposition.NOT_STARTED,
            )
        with self._dependencies.duckdb_connections.connect() as connection:
            connection.execute("SET threads = 1")
            connection.execute("SET preserve_insertion_order = true")
            connection.execute(
                "CREATE TEMP VIEW provider_source AS SELECT * FROM "
                f"read_parquet({_quote_literal(input_path)})",
            )
            schema_rows = connection.execute(
                "DESCRIBE SELECT * FROM provider_source",
            ).fetchall()
            available_columns = {str(row[0]) for row in schema_rows}
            if limits is not None and len(available_columns) > limits.maximum_source_columns:
                raise self._failure(
                    code="source_column_limit_exceeded",
                    message="Source columns exceed the configured provider limit.",
                    retryable=False,
                    disposition=ProviderSideEffectDisposition.NOT_STARTED,
                )
            if limits is not None:
                count_row = connection.execute(
                    "SELECT COUNT(*) FROM provider_source",
                ).fetchone()
                if count_row is None:
                    raise self._failure(
                        code="row_limit_unavailable",
                        message="Source row limit validation was unavailable.",
                        retryable=False,
                        disposition=ProviderSideEffectDisposition.NOT_STARTED,
                    )
                row_count = int(count_row[0])
                if row_count > limits.maximum_row_count:
                    raise self._failure(
                        code="row_limit_exceeded",
                        message="Source rows exceed the configured provider limit.",
                        retryable=False,
                        disposition=ProviderSideEffectDisposition.NOT_STARTED,
                    )
            if any(column.source not in available_columns for column in columns):
                raise self._failure(
                    code="source_column_missing",
                    message="A configured source column is unavailable.",
                    retryable=False,
                    disposition=ProviderSideEffectDisposition.NOT_STARTED,
                )
            projection = ", ".join(
                f"{_quote_identifier(column.source)} AS {_quote_identifier(column.target)}"
                for column in columns
            )
            connection.execute(
                f"COPY (SELECT {projection} FROM provider_source) "
                f"TO {_quote_literal(output_path)} (FORMAT PARQUET, COMPRESSION ZSTD)",
            )
        if not Path(output_path).is_file():
            raise self._failure(
                code="output_not_created",
                message="Project-columns output was not created.",
                retryable=True,
                disposition=ProviderSideEffectDisposition.NOT_STARTED,
            )

    def _raise_if_cancelled_or_expired(
        self,
        *,
        runtime_context: TransformationProviderRuntimeContext,
    ) -> None:
        if runtime_context.cancellation.is_cancellation_requested():
            raise self._failure(
                code="provider_cancelled",
                message="Project-columns execution was cancelled.",
                retryable=False,
                disposition=ProviderSideEffectDisposition.NOT_STARTED,
            )
        if runtime_context.deadline.has_deadline_expired():
            raise self._failure(
                code="provider_deadline_expired",
                message="Project-columns execution deadline expired.",
                retryable=False,
                disposition=ProviderSideEffectDisposition.NOT_STARTED,
            )

    def _failure(
        self,
        *,
        code: str,
        message: str,
        retryable: bool,
        disposition: ProviderSideEffectDisposition,
    ) -> TransformationProviderExecutionError:
        return TransformationProviderExecutionError(
            TransformationProviderFailure(
                identity=PROJECT_COLUMNS_IDENTITY,
                code=code,
                message=message,
                retryable=retryable,
                side_effect_disposition=disposition,
            ),
        )


def _quote_identifier(value: str) -> str:
    return f'"{value.replace(chr(34), chr(34) * 2)}"'


def _quote_literal(value: str) -> str:
    return f"'{value.replace(chr(39), chr(39) * 2)}'"
