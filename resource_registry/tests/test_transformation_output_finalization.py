from __future__ import annotations

import hashlib
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import NoReturn
from unittest.mock import patch

import pytest
from django.db import close_old_connections, connection
from django.test import override_settings
from django.utils import timezone

from resource_registry.models import (
    ArtifactLifecycleStatus,
    Dataset,
    DatasetArtifact,
    DatasetLineage,
    DatasetSourceType,
    DatasetStatus,
    DatasetVersion,
    DatasetVersionStatus,
    TransformationDefinition,
    TransformationDefinitionVersion,
    TransformationDefinitionVersionStatus,
    TransformationRun,
    TransformationRunOutput,
    TransformationRunOutputStatus,
    TransformationRunStatus,
    TransformationRunStepAttempt,
    TransformationRunStepAttemptStatus,
)
from resource_registry.storage.local import LocalStorageProvider
from resource_registry.tests.transformation_run_test_helpers import (
    build_test_authorization_decision,
    build_test_execution_plan,
)
from resource_registry.tests.transformation_runtime_test_helpers import (
    build_test_output_finalization_service,
)
from resource_registry.transformations.runs import (
    TransformationOutputPolicy,
    TransformationOutputPolicyKind,
    TransformationRunCreationRequest,
    TransformationRunCreationService,
)
from resource_registry.transformations.runs.output_policies import (
    calculate_output_policy_checksum,
)

pytestmark = pytest.mark.django_db

_SPECIFICATION_CHECKSUM = "a" * 64
_CHECKPOINT_CHECKSUM = "b" * 64
_CAPABILITY_CHECKSUM = "c" * 64
_IDEMPOTENCY_TOKEN = "d" * 64
_INPUT_DATASET_ID = uuid.UUID(int=101)
_INPUT_VERSION_ID = uuid.UUID(int=102)
_SECOND_VERSION_NUMBER = 2


def _create_output_pending_run(
    *,
    source_path: Path,
    policy: TransformationOutputPolicy,
    source_checksum: str | None = None,
    tenant_id: uuid.UUID | None = None,
) -> TransformationRun:
    resolved_tenant_id = tenant_id or uuid.uuid4()
    definition = TransformationDefinition.objects.create(
        tenant_id=resolved_tenant_id,
        definition_key=f"definition-{uuid.uuid4()}",
        name="Transformation",
    )
    version = TransformationDefinitionVersion.objects.create(
        definition=definition,
        version_number=1,
        status=TransformationDefinitionVersionStatus.PUBLISHED,
        specification_json={"dsl_version": "1.0"},
        specification_checksum=_SPECIFICATION_CHECKSUM,
    )
    input_dataset = Dataset.objects.create(
        id=_INPUT_DATASET_ID,
        tenant_id=resolved_tenant_id,
        slug="input",
        name="Input",
        source_type=DatasetSourceType.FILE_UPLOAD,
    )
    DatasetVersion.objects.create(
        id=_INPUT_VERSION_ID,
        dataset=input_dataset,
        version_number=1,
        source_uri="local://input.parquet",
        content_hash="input-content",
        status=DatasetVersionStatus.READY,
    )
    plan = build_test_execution_plan(
        tenant_id=resolved_tenant_id,
        definition_id=definition.id,
        definition_version_id=version.id,
        specification_checksum=_SPECIFICATION_CHECKSUM,
        output_names=("result",),
    )
    result = TransformationRunCreationService().create_run(
        request=TransformationRunCreationRequest(
            tenant_id=resolved_tenant_id,
            definition_id=definition.id,
            definition_version_id=version.id,
            execution_plan=plan,
            authorization_decision=build_test_authorization_decision(
                tenant_id=resolved_tenant_id,
                definition_id=definition.id,
                definition_version_id=version.id,
                dataset_ids=tuple(input_item.dataset_id for input_item in plan.inputs),
            ),
            output_policies=(policy,),
        ),
    )
    run = TransformationRun.objects.get(id=result.run_id)
    actual_checksum = hashlib.sha256(source_path.read_bytes()).hexdigest()
    attempt_token = uuid.uuid4()
    attempt = TransformationRunStepAttempt.objects.create(
        run=run,
        run_attempt_number=1,
        run_attempt_token=attempt_token,
        step_index=0,
        step_id="transform",
        provider_key="provider",
        provider_version="v1",
        operation_key="transform",
        operation_version="op1",
        status=TransformationRunStepAttemptStatus.SUCCEEDED,
        checkpoint_inputs_json=[],
        checkpoint_outputs_json=[
            {
                "logical_name": "result",
                "materialized_reference": str(source_path),
                "data_format": "PARQUET",
                "integrity": {
                    "checksum_algorithm": "sha256",
                    "checksum_value": source_checksum or actual_checksum,
                    "size_bytes": source_path.stat().st_size,
                    "etag": None,
                    "version_identifier": None,
                    "metadata": {},
                },
                "metadata": {},
                "is_temporary": False,
            },
        ],
        plan_checksum=plan.plan_checksum,
        capability_checksum=_CAPABILITY_CHECKSUM,
        checkpoint_input_checksum=_CHECKPOINT_CHECKSUM,
        checkpoint_output_checksum=_CHECKPOINT_CHECKSUM,
        checkpoint_checksum=_CHECKPOINT_CHECKSUM,
        attempt_idempotency_token=_IDEMPOTENCY_TOKEN,
        completed_at=run.created_at,
    )
    run.status = TransformationRunStatus.OUTPUT_PENDING
    run.latest_attempt_number = 1
    run.save(update_fields=["status", "latest_attempt_number", "updated_at"])
    output = run.outputs.get()
    output.producing_step_attempt = attempt
    output.save(update_fields=["producing_step_attempt", "updated_at"])
    return run


def _standalone_policy() -> TransformationOutputPolicy:
    return TransformationOutputPolicy(
        logical_name="result",
        policy_kind=TransformationOutputPolicyKind.STANDALONE,
        destination_storage_provider="local",
    )


@override_settings(RESOURCE_REGISTRY_STORAGE_PROVIDER="local")
def test_standalone_output_materializes_and_duplicate_processing_is_noop(tmp_path: Path) -> None:
    source_path = tmp_path / "source.parquet"
    source_path.write_bytes(b"durable output")
    managed_directory = tmp_path / "managed"
    with override_settings(RESOURCE_REGISTRY_LOCAL_STORAGE_DIR=managed_directory):
        run = _create_output_pending_run(source_path=source_path, policy=_standalone_policy())
        service = build_test_output_finalization_service()

        first = service.finalize_run(tenant_id=run.tenant_id, run_id=run.id)
        repeated = service.finalize_run(tenant_id=run.tenant_id, run_id=run.id)

    output = TransformationRunOutput.objects.get(run=run)
    assert first.status == TransformationRunStatus.SUCCEEDED
    assert repeated.status == TransformationRunStatus.SUCCEEDED
    assert output.status == TransformationRunOutputStatus.MATERIALIZED
    assert output.checksum_algorithm == "sha256"
    assert output.canonical_uri is not None
    assert len(tuple(managed_directory.rglob("*.parquet"))) == 1


def test_dataset_output_registers_version_artifact_and_exact_lineage(tmp_path: Path) -> None:
    source_path = tmp_path / "source.parquet"
    source_path.write_bytes(b"dataset output")
    policy = TransformationOutputPolicy(
        logical_name="result",
        policy_kind=TransformationOutputPolicyKind.NEW_DATASET,
        destination_storage_provider="local",
        configuration={"dataset_slug": "derived", "dataset_name": "Derived"},
    )
    with override_settings(RESOURCE_REGISTRY_LOCAL_STORAGE_DIR=tmp_path / "managed"):
        run = _create_output_pending_run(source_path=source_path, policy=policy)
        result = build_test_output_finalization_service().finalize_run(
            tenant_id=run.tenant_id,
            run_id=run.id,
        )

    output = TransformationRunOutput.objects.get(run=run)
    artifact = DatasetArtifact.objects.get(id=output.dataset_artifact_id)
    lineage = DatasetLineage.objects.get(downstream_dataset_version=output.dataset_version)
    assert result.status == TransformationRunStatus.SUCCEEDED
    assert output.status == TransformationRunOutputStatus.REGISTERED
    assert output.dataset is not None
    assert output.dataset.source_type == DatasetSourceType.TRANSFORMATION
    assert output.dataset_version is not None
    assert output.dataset_version.status == DatasetVersionStatus.READY
    assert artifact.uri == output.canonical_uri
    assert artifact.lifecycle_status == ArtifactLifecycleStatus.ACTIVE
    assert lineage.upstream_dataset_version_id == _INPUT_VERSION_ID
    assert lineage.job_id == run.id
    assert lineage.transformation_hash == run.execution_plan_checksum


def test_new_dataset_version_policy_creates_immutable_next_version(tmp_path: Path) -> None:
    source_path = tmp_path / "source.parquet"
    source_path.write_bytes(b"new version")
    tenant_id = uuid.uuid4()
    target = Dataset.objects.create(
        tenant_id=tenant_id,
        slug="target",
        name="Target",
        source_type=DatasetSourceType.TRANSFORMATION,
    )
    DatasetVersion.objects.create(
        dataset=target,
        version_number=1,
        source_uri="local://old.parquet",
        content_hash="old-content",
        status=DatasetVersionStatus.READY,
    )
    policy = TransformationOutputPolicy(
        logical_name="result",
        policy_kind=TransformationOutputPolicyKind.NEW_DATASET_VERSION,
        destination_storage_provider="local",
        target_dataset_id=target.id,
    )
    with override_settings(RESOURCE_REGISTRY_LOCAL_STORAGE_DIR=tmp_path / "managed"):
        run = _create_output_pending_run(
            source_path=source_path,
            policy=policy,
            tenant_id=tenant_id,
        )
        output = run.outputs.get()
        assert output.output_policy_checksum == calculate_output_policy_checksum(
            output.output_policy_json,
        )
        result = build_test_output_finalization_service().finalize_run(
            tenant_id=run.tenant_id,
            run_id=run.id,
        )

    output.refresh_from_db()
    assert result.status == TransformationRunStatus.SUCCEEDED
    assert output.dataset_id == target.id
    assert output.dataset_version is not None
    assert output.dataset_version.version_number == _SECOND_VERSION_NUMBER


def test_new_dataset_version_policy_restores_archived_target_visibility(tmp_path: Path) -> None:
    """Clear archival metadata when finalization restores an existing target dataset."""
    source_path = tmp_path / "source.parquet"
    source_path.write_bytes(b"restored version")
    tenant_id = uuid.uuid4()
    target = Dataset.objects.create(
        tenant_id=tenant_id,
        slug="archived-target",
        name="Archived Target",
        source_type=DatasetSourceType.TRANSFORMATION,
        status=DatasetStatus.ARCHIVED,
        archived_at=timezone.now(),
    )
    policy = TransformationOutputPolicy(
        logical_name="result",
        policy_kind=TransformationOutputPolicyKind.NEW_DATASET_VERSION,
        destination_storage_provider="local",
        target_dataset_id=target.id,
    )
    with override_settings(RESOURCE_REGISTRY_LOCAL_STORAGE_DIR=tmp_path / "managed"):
        run = _create_output_pending_run(
            source_path=source_path,
            policy=policy,
            tenant_id=tenant_id,
        )
        result = build_test_output_finalization_service().finalize_run(
            tenant_id=run.tenant_id,
            run_id=run.id,
        )

    target.refresh_from_db()
    assert result.status == TransformationRunStatus.SUCCEEDED
    assert target.status == DatasetStatus.ACTIVE
    assert target.archived_at is None


def test_source_integrity_failure_fails_closed_without_managed_artifact(tmp_path: Path) -> None:
    source_path = tmp_path / "source.parquet"
    source_path.write_bytes(b"tampered")
    managed_directory = tmp_path / "managed"
    with override_settings(RESOURCE_REGISTRY_LOCAL_STORAGE_DIR=managed_directory):
        run = _create_output_pending_run(
            source_path=source_path,
            policy=_standalone_policy(),
            source_checksum="0" * 64,
        )
        result = build_test_output_finalization_service().finalize_run(
            tenant_id=run.tenant_id,
            run_id=run.id,
        )

    output = run.outputs.get()
    assert result.status == TransformationRunStatus.FAILED
    assert output.status == TransformationRunOutputStatus.FAILED
    assert output.error_code == "output_integrity_failed"
    assert not tuple(managed_directory.rglob("*.parquet"))


def test_restart_adopts_verified_destination_without_provider_reexecution(tmp_path: Path) -> None:
    source_path = tmp_path / "source.parquet"
    source_path.write_bytes(b"recoverable output")
    managed_directory = tmp_path / "managed"
    original_put = LocalStorageProvider.put

    def upload_then_interrupt(
        provider: LocalStorageProvider,
        local_path: str,
        destination_path: str,
    ) -> NoReturn:
        original_put(provider, local_path, destination_path)
        raise RuntimeError("simulated worker interruption")

    with override_settings(RESOURCE_REGISTRY_LOCAL_STORAGE_DIR=managed_directory):
        run = _create_output_pending_run(source_path=source_path, policy=_standalone_policy())
        service = build_test_output_finalization_service()
        with patch.object(LocalStorageProvider, "put", upload_then_interrupt):
            interrupted = service.finalize_run(tenant_id=run.tenant_id, run_id=run.id)
        source_path.unlink()
        recovered = service.finalize_run(tenant_id=run.tenant_id, run_id=run.id)

    output = run.outputs.get()
    assert interrupted.status == TransformationRunStatus.OUTPUT_PENDING
    assert recovered.status == TransformationRunStatus.SUCCEEDED
    assert output.status == TransformationRunOutputStatus.MATERIALIZED
    assert output.finalization_attempt_count == 1


def test_permanent_registration_failure_compensates_managed_orphan(tmp_path: Path) -> None:
    source_path = tmp_path / "source.parquet"
    source_path.write_bytes(b"orphan candidate")
    managed_directory = tmp_path / "managed"
    tenant_id = uuid.uuid4()
    Dataset.objects.create(
        tenant_id=tenant_id,
        slug="derived",
        name="Existing",
        source_type=DatasetSourceType.TRANSFORMATION,
    )
    policy = TransformationOutputPolicy(
        logical_name="result",
        policy_kind=TransformationOutputPolicyKind.NEW_DATASET,
        destination_storage_provider="local",
        configuration={"dataset_slug": "derived", "dataset_name": "Derived"},
    )
    with override_settings(RESOURCE_REGISTRY_LOCAL_STORAGE_DIR=managed_directory):
        run = _create_output_pending_run(
            source_path=source_path,
            policy=policy,
            tenant_id=tenant_id,
        )
        result = build_test_output_finalization_service().finalize_run(
            tenant_id=run.tenant_id,
            run_id=run.id,
        )

    output = run.outputs.get()
    assert result.status == TransformationRunStatus.FAILED
    assert output.status == TransformationRunOutputStatus.FAILED
    assert output.error_code == "dataset_registration_failed"
    assert not tuple(managed_directory.rglob("*.parquet"))


def test_output_pending_with_terminal_outputs_completes_without_storage(tmp_path: Path) -> None:
    source_path = tmp_path / "source.parquet"
    source_path.write_bytes(b"already materialized")
    run = _create_output_pending_run(source_path=source_path, policy=_standalone_policy())
    output = run.outputs.get()
    checksum = hashlib.sha256(source_path.read_bytes()).hexdigest()
    output.status = TransformationRunOutputStatus.MATERIALIZED
    output.canonical_uri = "local://already/materialized.parquet"
    output.checksum_algorithm = "sha256"
    output.checksum_value = checksum
    output.size_bytes = source_path.stat().st_size
    output.completed_at = run.created_at
    output.save()

    result = build_test_output_finalization_service().finalize_run(
        tenant_id=run.tenant_id,
        run_id=run.id,
    )

    assert result.status == TransformationRunStatus.SUCCEEDED


@pytest.mark.skipif(connection.vendor != "postgresql", reason="PostgreSQL locking required")
@pytest.mark.django_db(transaction=True)
def test_concurrent_duplicate_finalization_registers_one_managed_output(tmp_path: Path) -> None:
    source_path = tmp_path / "source.parquet"
    source_path.write_bytes(b"concurrent output")
    managed_directory = tmp_path / "managed"
    with override_settings(RESOURCE_REGISTRY_LOCAL_STORAGE_DIR=managed_directory):
        run = _create_output_pending_run(source_path=source_path, policy=_standalone_policy())

        def finalize() -> str:
            close_old_connections()
            try:
                return (
                    build_test_output_finalization_service()
                    .finalize_run(
                        tenant_id=run.tenant_id,
                        run_id=run.id,
                    )
                    .status
                )
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = tuple(executor.map(lambda _: finalize(), range(2)))

    assert set(statuses) <= {
        TransformationRunStatus.OUTPUT_PENDING,
        TransformationRunStatus.SUCCEEDED,
    }
    assert TransformationRun.objects.get(id=run.id).status == TransformationRunStatus.SUCCEEDED
    assert len(tuple(managed_directory.rglob("*.parquet"))) == 1
