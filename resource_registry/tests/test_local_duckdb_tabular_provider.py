from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from resource_registry.application.transformation_runtime.provider_dependencies import (
    RuntimeProviderInputMaterializer,
    RuntimeProviderOutputWorkspace,
)
from resource_registry.security.integrity import ArtifactIntegrityVerifier
from resource_registry.services.duckdb_service import InMemoryDuckDbConnectionProvider
from resource_registry.storage.local import LocalStorageProvider
from resource_registry.storage.read_adapters import LocalArtifactReadAdapter
from resource_registry.transformations.execution.runtime import (
    DefaultTransformationProviderRuntimeContext,
    DenyingTransformationRuntimeSecretAccess,
    MonotonicTransformationDeadlineSignal,
)
from resource_registry.transformations.execution_plan.capability_checksum import (
    calculate_registered_operation_capability_checksum,
)
from resource_registry.transformations.providers import (
    ProviderOperationalLimits,
    TransformationIntegrityMetadata,
    TransformationProviderExecutionError,
    TransformationProviderExecutionRequest,
    TransformationProviderExpectedOutput,
    TransformationProviderInputReference,
    TransformationProviderRegistry,
    TransformationProviderValidationRequest,
)
from resource_registry.transformations.providers.builtin.catalog import (
    PRODUCTION_TRANSFORMATION_PROVIDER_CATALOG,
)
from resource_registry.transformations.providers.builtin.local_duckdb_tabular import (
    PROJECT_COLUMNS_IDENTITY,
    PROVIDER_IDENTITY,
    LocalDuckDbTabularProvider,
)
from resource_registry.transformations.providers.builtin.local_duckdb_tabular.capabilities import (
    PARQUET_FORMAT,
)
from resource_registry.transformations.providers.dependencies import ProviderDependencies
from resource_registry.transformations.providers.exceptions import (
    TransformationProviderNotFoundError,
)


class _NeverCancelled:
    def is_cancellation_requested(self) -> bool:
        return False


class _AlwaysCancelled:
    def is_cancellation_requested(self) -> bool:
        return True


def _dependencies(
    tmp_path: Path,
    *,
    operational_limits: ProviderOperationalLimits | None = None,
) -> ProviderDependencies:
    storage_provider = LocalStorageProvider(base_directory=tmp_path / "storage")
    integrity = ArtifactIntegrityVerifier()
    return ProviderDependencies(
        input_materializer=RuntimeProviderInputMaterializer(
            read_adapters=(LocalArtifactReadAdapter(storage_provider=storage_provider),),
            integrity_verifier=integrity,
        ),
        output_workspace=RuntimeProviderOutputWorkspace(
            storage_provider=storage_provider,
            integrity_verifier=integrity,
        ),
        duckdb_connections=InMemoryDuckDbConnectionProvider(),
        operational_limits=operational_limits,
    )


def _write_input(path: Path) -> None:
    with duckdb.connect(database=":memory:") as connection:
        connection.execute(
            "CREATE TABLE source(identifier INTEGER, label VARCHAR, amount DOUBLE, note VARCHAR)",
        )
        connection.execute(
            "INSERT INTO source VALUES (1, 'first', 1.5, NULL), (2, 'second', 2.5, 'ok')",
        )
        connection.execute("COPY source TO ? (FORMAT PARQUET, COMPRESSION ZSTD)", [str(path)])


def _validation_request(
    configuration: dict[str, object],
) -> TransformationProviderValidationRequest:
    return TransformationProviderValidationRequest(
        identity=PROJECT_COLUMNS_IDENTITY,
        step_id="project",
        configuration=configuration,
        declared_input_formats=(PARQUET_FORMAT,),
        declared_output_formats=(PARQUET_FORMAT,),
    )


def _execution_request(
    path: Path,
    configuration: dict[str, object],
) -> TransformationProviderExecutionRequest:
    integrity = ArtifactIntegrityVerifier()
    return TransformationProviderExecutionRequest(
        identity=PROJECT_COLUMNS_IDENTITY,
        run_id="run-1",
        run_attempt_number=1,
        run_attempt_token="attempt-token",
        step_id="project",
        step_index=0,
        step_attempt_id="step-attempt-1",
        attempt_idempotency_token="idempotency-token",
        configuration=configuration,
        bound_parameters={},
        inputs=(
            TransformationProviderInputReference(
                logical_name="source",
                materialized_reference=str(path),
                data_format=PARQUET_FORMAT,
                integrity=TransformationIntegrityMetadata(
                    checksum_algorithm="sha256",
                    checksum_value=integrity.compute_sha256(str(path)),
                    size_bytes=path.stat().st_size,
                ),
            ),
        ),
        expected_outputs=(
            TransformationProviderExpectedOutput(
                logical_name="projected",
                accepted_formats=(PARQUET_FORMAT,),
                is_temporary=False,
            ),
        ),
    )


def _runtime_context() -> DefaultTransformationProviderRuntimeContext:
    return DefaultTransformationProviderRuntimeContext(
        cancellation_signal=_NeverCancelled(),
        deadline_signal=MonotonicTransformationDeadlineSignal(),
        secret_access=DenyingTransformationRuntimeSecretAccess(),
    )


def test_descriptor_and_catalog_are_exact_and_immutable(tmp_path: Path) -> None:
    dependencies = _dependencies(tmp_path)
    factory = PRODUCTION_TRANSFORMATION_PROVIDER_CATALOG[PROVIDER_IDENTITY]
    provider = factory(dependencies)

    assert provider.provider_identity == PROVIDER_IDENTITY
    assert tuple(item.identity for item in provider.capability_descriptor.operations) == (
        PROJECT_COLUMNS_IDENTITY,
    )
    with pytest.raises(TypeError):
        PRODUCTION_TRANSFORMATION_PROVIDER_CATALOG[PROVIDER_IDENTITY] = factory  # type: ignore[index]


@pytest.mark.parametrize(
    ("configuration", "expected_code"),
    [
        ({}, "configuration_required_invalid"),
        (
            {
                "columns": [
                    {"source": "identifier", "target": "first"},
                    {"source": "identifier", "target": "second"},
                ],
            },
            "duplicate_source_column",
        ),
        (
            {
                "columns": [
                    {"source": "identifier", "target": "same"},
                    {"source": "label", "target": "same"},
                ],
            },
            "duplicate_target_column",
        ),
        (
            {"columns": [{"source": " identifier", "target": "identifier"}]},
            "source_column_not_exact",
        ),
    ],
)
def test_validation_is_deterministic_and_sanitized(
    tmp_path: Path,
    configuration: dict[str, object],
    expected_code: str,
) -> None:
    provider = LocalDuckDbTabularProvider(dependencies=_dependencies(tmp_path))

    first = provider.validate_operation(request=_validation_request(configuration))
    second = provider.validate_operation(request=_validation_request(configuration))

    assert first == second
    assert expected_code in {finding.code for finding in first.findings}
    assert all("identifier" not in finding.message for finding in first.findings)


def test_validation_rejects_non_parquet_formats(tmp_path: Path) -> None:
    provider = LocalDuckDbTabularProvider(dependencies=_dependencies(tmp_path))
    request = TransformationProviderValidationRequest(
        identity=PROJECT_COLUMNS_IDENTITY,
        step_id="project",
        configuration={"columns": [{"source": "identifier", "target": "identifier"}]},
        declared_input_formats=("CSV",),
        declared_output_formats=("JSON",),
    )

    result = provider.validate_operation(request=request)

    assert {finding.code for finding in result.findings} == {
        "input_contract_invalid",
        "output_contract_invalid",
    }


def test_execution_preserves_order_values_types_nulls_and_bytes(tmp_path: Path) -> None:
    input_path = tmp_path / "input.parquet"
    _write_input(input_path)
    dependencies = _dependencies(tmp_path)
    provider = LocalDuckDbTabularProvider(dependencies=dependencies)
    request = _execution_request(
        input_path,
        {
            "columns": [
                {"source": "label", "target": "name"},
                {"source": "identifier", "target": "id"},
                {"source": "note", "target": "note"},
            ],
        },
    )

    first = provider.execute(request=request, runtime_context=_runtime_context())
    second = provider.execute(request=request, runtime_context=_runtime_context())

    assert first.outputs[0].integrity.checksum_value == second.outputs[0].integrity.checksum_value
    assert first.outputs[0].materialized_reference == second.outputs[0].materialized_reference
    with dependencies.input_materializer.materialize(
        reference=TransformationProviderInputReference(
            logical_name="result",
            materialized_reference=first.outputs[0].materialized_reference,
            data_format=PARQUET_FORMAT,
            integrity=first.outputs[0].integrity,
        ),
    ) as output:
        with duckdb.connect(database=":memory:") as connection:
            description = connection.execute(
                "DESCRIBE SELECT * FROM read_parquet(?)",
                [output.local_path],
            ).fetchall()
            rows = connection.execute(
                "SELECT * FROM read_parquet(?)",
                [output.local_path],
            ).fetchall()
    assert [row[0] for row in description] == ["name", "id", "note"]
    assert [row[1] for row in description] == ["VARCHAR", "INTEGER", "VARCHAR"]
    assert rows == [("first", 1, None), ("second", 2, "ok")]


@pytest.mark.parametrize(
    ("limits", "columns", "expected_code"),
    [
        (
            (10_000_000, 10_000_000, 100, 10, 1),
            ("identifier", "label"),
            "selected_column_limit_exceeded",
        ),
        (
            (10_000_000, 10_000_000, 100, 3, 10),
            ("identifier",),
            "source_column_limit_exceeded",
        ),
        (
            (10_000_000, 10_000_000, 1, 10, 10),
            ("identifier",),
            "row_limit_exceeded",
        ),
    ],
)
def test_execution_limits_fail_closed(
    tmp_path: Path,
    limits: tuple[int, int, int, int, int],
    columns: tuple[str, ...],
    expected_code: str,
) -> None:
    input_path = tmp_path / "limited.parquet"
    _write_input(input_path)
    maximum_input, maximum_output, rows, source_columns, selected_columns = limits
    dependencies = _dependencies(
        tmp_path,
        operational_limits=ProviderOperationalLimits(
            maximum_input_bytes=maximum_input,
            maximum_output_bytes=maximum_output,
            maximum_row_count=rows,
            maximum_source_columns=source_columns,
            maximum_selected_columns=selected_columns,
            provider_timeout_seconds=60,
            materialization_timeout_seconds=60,
        ),
    )
    provider = LocalDuckDbTabularProvider(dependencies=dependencies)
    configuration: dict[str, object] = {
        "columns": [{"source": column, "target": column} for column in columns],
    }

    with pytest.raises(TransformationProviderExecutionError) as captured:
        provider.execute(
            request=_execution_request(input_path, configuration),
            runtime_context=_runtime_context(),
        )

    assert captured.value.failure.code == expected_code


def test_missing_source_column_fails_without_exposing_column(tmp_path: Path) -> None:
    input_path = tmp_path / "input.parquet"
    _write_input(input_path)
    provider = LocalDuckDbTabularProvider(dependencies=_dependencies(tmp_path))
    request = _execution_request(
        input_path,
        {"columns": [{"source": "private_missing", "target": "safe"}]},
    )

    with pytest.raises(TransformationProviderExecutionError) as captured:
        provider.execute(request=request, runtime_context=_runtime_context())

    assert captured.value.failure.code == "source_column_missing"
    assert "private_missing" not in captured.value.failure.message


def test_capability_and_registry_checksums_are_deterministic(tmp_path: Path) -> None:
    provider = LocalDuckDbTabularProvider(dependencies=_dependencies(tmp_path))
    operation = provider.capability_descriptor.operations[0]
    first_capability = calculate_registered_operation_capability_checksum(
        provider_descriptor=provider.capability_descriptor,
        operation_descriptor=operation,
    )
    second_capability = calculate_registered_operation_capability_checksum(
        provider_descriptor=provider.capability_descriptor,
        operation_descriptor=operation,
    )
    registry = TransformationProviderRegistry()
    registry.register_provider(provider=provider)
    registry.validate()
    first_registry = registry.manifest_checksum
    second_registry = TransformationProviderRegistry()
    second_registry.register_provider(
        provider=LocalDuckDbTabularProvider(dependencies=_dependencies(tmp_path)),
    )
    second_registry.validate()

    assert first_capability == second_capability
    assert first_registry == second_registry.manifest_checksum


def test_registry_requires_exact_production_provider_version(tmp_path: Path) -> None:
    registry = TransformationProviderRegistry()
    registry.register_provider(
        provider=LocalDuckDbTabularProvider(dependencies=_dependencies(tmp_path)),
    )
    registry.validate()
    registry.freeze()

    resolved = registry.resolve_operation(
        provider_key="local_duckdb_tabular",
        provider_version="1.0.0",
        operation_key="project_columns",
        operation_version="1.0.0",
    )
    assert resolved.operation.identity == PROJECT_COLUMNS_IDENTITY
    with pytest.raises(TransformationProviderNotFoundError):
        registry.resolve_operation(
            provider_key="local_duckdb_tabular",
            provider_version="2.0.0",
            operation_key="project_columns",
            operation_version="1.0.0",
        )


def test_provider_observes_cooperative_cancellation_before_io(tmp_path: Path) -> None:
    input_path = tmp_path / "input.parquet"
    _write_input(input_path)
    provider = LocalDuckDbTabularProvider(dependencies=_dependencies(tmp_path))
    context = DefaultTransformationProviderRuntimeContext(
        cancellation_signal=_AlwaysCancelled(),
        deadline_signal=MonotonicTransformationDeadlineSignal(),
        secret_access=DenyingTransformationRuntimeSecretAccess(),
    )

    with pytest.raises(TransformationProviderExecutionError) as captured:
        provider.execute(
            request=_execution_request(
                input_path,
                {"columns": [{"source": "identifier", "target": "identifier"}]},
            ),
            runtime_context=context,
        )

    assert captured.value.failure.code == "provider_cancelled"
    assert captured.value.failure.side_effect_disposition.value == "NOT_STARTED"
