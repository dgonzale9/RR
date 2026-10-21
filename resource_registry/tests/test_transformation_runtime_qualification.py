from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from unittest.mock import MagicMock, patch

import duckdb
import pytest
from django.db import close_old_connections, connection
from django.contrib.auth.models import User
from django.test import override_settings

from resource_registry.application.transformation_runtime import (
    RuntimeCompositionRoot,
    TransformationRuntime,
)
from resource_registry.models import (
    Dataset,
    DatasetArtifact,
    DatasetLineage,
    DatasetPolicyAssignment,
    DatasetSourceType,
    DatasetVisibilityScope,
    DatasetVersion,
    DatasetVersionStatus,
    GovernancePolicy,
    GovernancePolicyType,
    TransformationDefinition,
    TransformationDefinitionVersion,
    TransformationDefinitionVersionStatus,
    TransformationRun,
    TransformationRunOutput,
    TransformationRunOutputStatus,
    TransformationRunStatus,
    TransformationRunStepAttempt,
    TransformationWorkspaceLease,
    TransformationWorkspaceLeaseState,
    RegistryNamespace,
)
from Organization.models import Organization, Workspace
from resource_registry.read.transformation_input_binding_read_service import (
    TransformationInputBindingReadService,
)
from resource_registry.read_auth.context import ReadAuthorizationContext
from resource_registry.services.transformation_run_execution_coordinator import (
    TransformationRunExecutionCoordinator,
)
from resource_registry.storage.local import LocalStorageProvider
from resource_registry.tests.test_transformation_attempt_persistence import _Verifier
from resource_registry.tests.test_transformation_runtime_composition import _configuration
from resource_registry.tests.transformation_run_test_helpers import (
    build_test_authorization_decision,
    build_test_execution_plan,
)
from resource_registry.tests.transformation_runtime_test_helpers import (
    build_test_job_service,
    build_test_output_finalization_service,
    freeze_test_provider_registry,
)
from resource_registry.transformations.binding.results import TransformationInputBindingRequest
from resource_registry.transformations.binding.service import TransformationSpecificationBindingService
from resource_registry.transformations.dsl.typed_parser import TransformationTypedParser
from resource_registry.transformations.execution.checkpoint_persistence import (
    TransformationStepCheckpointService,
)
from resource_registry.transformations.execution.service import TransformationRunExecutionService
from resource_registry.transformations.execution_plan import (
    EXECUTION_PLAN_CONTRACT_VERSION,
    TransformationDefinitionCompilationSnapshot,
    TransformationExecutionPlan,
    TransformationExecutionPlanCompilationRequest,
)
from resource_registry.transformations.operational.authorization import (
    ADD_DATASET_PERMISSION,
    EXECUTE_TRANSFORMATION_PERMISSION,
    USE_TRANSFORMATION_PROVIDER_PERMISSION,
    VIEW_TRANSFORMATION_DIAGNOSTICS_PERMISSION,
)
from resource_registry.transformations.operational.contracts import (
    TransformationAuthorizationContext,
    TransformationOperationalHealthStatus,
    TransformationPrincipalKind,
)
from resource_registry.transformations.operational.exceptions import (
    TransformationPermissionDeniedError,
)
from resource_registry.transformations.operational.governance import (
    TRANSFORMATION_SECURITY_POLICY_CONTRACT_VERSION,
    TRANSFORMATION_SECURITY_POLICY_KEY,
    calculate_governance_rules_checksum,
)
from resource_registry.transformations.providers import (
    ResolvedTransformationOperationIdentity,
    TransformationIntegrityMetadata,
    TransformationOperationCapabilityDescriptor,
    TransformationProviderCapabilityDescriptor,
    TransformationProviderExecutionRequest,
    TransformationProviderExecutionResult,
    TransformationProviderIdentity,
    TransformationProviderOutputReference,
    TransformationProviderRegistry,
    TransformationProviderRuntimeContext,
    TransformationProviderValidationRequest,
    TransformationProviderValidationResult,
)
from resource_registry.transformations.providers.builtin.local_duckdb_tabular import (
    PROJECT_COLUMNS_IDENTITY,
    PROVIDER_IDENTITY,
)
from resource_registry.transformations.runs import (
    TransformationOutputPolicy,
    TransformationOutputPolicyKind,
    TransformationRunCreationRequest,
    TransformationRunCreationService,
)
from resource_registry.transformations.validation.structural_validator import (
    TransformationStructuralValidator,
)
from resource_registry.utils.json_checksum import calculate_transformation_specification_checksum

pytestmark = pytest.mark.django_db(transaction=True)

_SHA256_HEX_LENGTH = 64
_QUALIFICATION_REPORT_PATH = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "fixtures"
    / "TRANSFORMATION_RUNTIME_QUALIFICATION_REPORT.json"
)


@dataclass
class _DeterministicFileProvider:
    workspace: Path

    def __post_init__(self) -> None:
        self.requests: list[TransformationProviderExecutionRequest] = []
        identity = ResolvedTransformationOperationIdentity(
            provider_key="provider",
            provider_version="v1",
            operation_key="transform",
            operation_version="op1",
        )
        self._descriptor = TransformationProviderCapabilityDescriptor(
            identity=identity.provider_identity,
            operations=(
                TransformationOperationCapabilityDescriptor(
                    identity=identity,
                    supported_input_formats=("PARQUET",),
                    supported_output_formats=("PARQUET",),
                    is_idempotent=True,
                    is_checkpoint_reuse_safe=True,
                    is_checkpoint_reconciliation_safe=False,
                    supports_cooperative_cancellation=True,
                    metadata={"bounded": True},
                ),
            ),
            metadata={"trusted": True},
        )

    @property
    def provider_identity(self) -> TransformationProviderIdentity:
        return self._descriptor.identity

    @property
    def capability_descriptor(self) -> TransformationProviderCapabilityDescriptor:
        return self._descriptor

    def validate_operation(
        self,
        *,
        request: TransformationProviderValidationRequest,
    ) -> TransformationProviderValidationResult:
        _ = request
        return TransformationProviderValidationResult(is_valid=True)

    def execute(
        self,
        *,
        request: TransformationProviderExecutionRequest,
        runtime_context: TransformationProviderRuntimeContext,
    ) -> TransformationProviderExecutionResult:
        _ = runtime_context
        self.requests.append(request)
        output_references: list[TransformationProviderOutputReference] = []
        for output_binding in request.expected_outputs:
            destination = self.workspace / (
                f"{request.step_id}_{output_binding.logical_name}.parquet"
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            payload = b"story4-deterministic-output"
            destination.write_bytes(payload)
            output_references.append(
                TransformationProviderOutputReference(
                    logical_name=output_binding.logical_name,
                    materialized_reference=str(destination),
                    data_format=output_binding.accepted_formats[0],
                    integrity=TransformationIntegrityMetadata(
                        checksum_algorithm="sha256",
                        checksum_value=hashlib.sha256(payload).hexdigest(),
                        size_bytes=len(payload),
                    ),
                    is_temporary=False,
                ),
            )
        return TransformationProviderExecutionResult(
            identity=request.identity,
            outputs=tuple(output_references),
        )


@dataclass(frozen=True)
class _QualifiedDefinition:
    runtime: TransformationRuntime
    tenant_id: uuid.UUID
    definition: TransformationDefinition
    version: TransformationDefinitionVersion
    source_dataset: Dataset
    source_version: DatasetVersion
    plan: TransformationExecutionPlan
    compilation_seconds: float


def _production_specification() -> dict[str, object]:
    return {
        "dsl_version": "1.0",
        "kind": "transformation",
        "parameters": {},
        "inputs": [{"name": "source", "format": "PARQUET"}],
        "outputs": [{"name": "result", "format": "PARQUET"}],
        "steps": [
            {
                "step_id": "project",
                "provider": {
                    "key": PROVIDER_IDENTITY.provider_key,
                    "version": PROVIDER_IDENTITY.provider_version,
                },
                "operation": {
                    "key": PROJECT_COLUMNS_IDENTITY.operation_key,
                    "version": PROJECT_COLUMNS_IDENTITY.operation_version,
                },
                "config": {
                    "columns": [
                        {"source": "identifier", "target": "id"},
                        {"source": "label", "target": "name"},
                    ],
                },
                "inputs": [{"name": "source", "format": "PARQUET"}],
                "outputs": [{"name": "result", "format": "PARQUET"}],
            },
        ],
    }


def _write_production_input(path: Path) -> str:
    with duckdb.connect(database=":memory:") as database:
        database.execute("CREATE TABLE source(identifier INTEGER, label VARCHAR)")
        database.execute("INSERT INTO source VALUES (1, 'first'), (2, 'second')")
        database.execute("COPY source TO ? (FORMAT PARQUET, COMPRESSION ZSTD)", [str(path)])
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _create_governance(*, tenant_id: uuid.UUID, dataset: Dataset) -> None:
    operation = {
        "provider_key": PROVIDER_IDENTITY.provider_key,
        "provider_version": PROVIDER_IDENTITY.provider_version,
        "operation_key": PROJECT_COLUMNS_IDENTITY.operation_key,
        "operation_version": PROJECT_COLUMNS_IDENTITY.operation_version,
    }
    rules = {
        "contract_version": TRANSFORMATION_SECURITY_POLICY_CONTRACT_VERSION,
        "allowed_provider_operations": [operation],
        "allowed_output_policy_kinds": [TransformationOutputPolicyKind.NEW_DATASET.value],
    }
    GovernancePolicy.objects.create(
        tenant_id=tenant_id,
        policy_key=TRANSFORMATION_SECURITY_POLICY_KEY,
        revision=1,
        policy_type=GovernancePolicyType.ACCESS,
        rules_json=rules,
        rules_checksum=calculate_governance_rules_checksum(rules),
        is_active=True,
        is_tenant_default=True,
    )
    dataset_policy = GovernancePolicy.objects.create(
        tenant_id=tenant_id,
        policy_key=TRANSFORMATION_SECURITY_POLICY_KEY,
        revision=2,
        policy_type=GovernancePolicyType.ACCESS,
        rules_json=rules,
        rules_checksum=calculate_governance_rules_checksum(rules),
        is_active=True,
        is_tenant_default=False,
    )
    DatasetPolicyAssignment.objects.create(
        dataset=dataset,
        governance_policy=dataset_policy,
    )


def _compile_production_definition(
    *,
    runtime: TransformationRuntime,
    tenant_id: uuid.UUID,
    input_path: Path,
) -> _QualifiedDefinition:
    content_hash = _write_production_input(input_path)
    owner = User.objects.create_user(username=f"qualification-owner-{uuid.uuid4().hex[:8]}")
    organization = Organization.objects.create(
        name=f"Qualification Organization {uuid.uuid4().hex[:8]}",
        slug=f"qualification-organization-{uuid.uuid4().hex[:8]}",
        last_modified_by=owner,
    )
    workspace = Workspace.objects.create(
        organization=organization,
        name="Qualification Workspace",
        slug=f"qualification-workspace-{uuid.uuid4().hex[:8]}",
        last_modified_by=owner,
    )
    RegistryNamespace.objects.create(id=tenant_id, workspace=workspace)
    source_dataset = Dataset.objects.create(
        tenant_id=tenant_id,
        organization=organization,
        workspace=workspace,
        visibility_scope=DatasetVisibilityScope.WORKSPACE,
        slug=f"qualification-source-{uuid.uuid4().hex[:8]}",
        name="Qualification Source",
        source_type=DatasetSourceType.FILE_UPLOAD,
    )
    source_version = DatasetVersion.objects.create(
        dataset=source_dataset,
        version_number=1,
        source_uri=str(input_path),
        content_hash=content_hash,
        status=DatasetVersionStatus.READY,
    )
    raw = _production_specification()
    checksum = calculate_transformation_specification_checksum(raw)
    definition = TransformationDefinition.objects.create(
        tenant_id=tenant_id,
        definition_key=f"production-qualification-{uuid.uuid4().hex[:8]}",
        name="Production Runtime Qualification",
    )
    version = TransformationDefinitionVersion.objects.create(
        definition=definition,
        version_number=1,
        status=TransformationDefinitionVersionStatus.PUBLISHED,
        specification_json=raw,
        specification_checksum=checksum,
    )
    structural = TransformationStructuralValidator().validate(raw)
    assert structural.success
    assert structural.normalized_specification is not None
    specification = TransformationTypedParser().parse(structural.normalized_specification)
    binding = TransformationSpecificationBindingService().validate_and_bind(
        specification=specification,
        supplied_parameters={},
        supplied_input_bindings=(
            TransformationInputBindingRequest(
                input_name="source",
                dataset_id=str(source_dataset.id),
                dataset_version_id=str(source_version.id),
            ),
        ),
        input_resolver=TransformationInputBindingReadService(
            context=ReadAuthorizationContext(
                tenant_id=str(tenant_id),
                roles=("admin",),
                allowed_dataset_ids=frozenset({str(source_dataset.id)}),
            ),
        ),
    )
    assert binding.is_valid
    request = TransformationExecutionPlanCompilationRequest(
        definition=TransformationDefinitionCompilationSnapshot(
            tenant_id=str(tenant_id),
            definition_id=str(definition.id),
            definition_version_id=str(version.id),
            definition_is_archived=False,
            version_status=version.status,
            dsl_version="1.0",
            specification_checksum=checksum,
            specification_json=raw,
        ),
        specification=specification,
        binding_result=binding,
    )
    started = time.perf_counter()
    compiled = runtime.execution_plan_compiler.compile(request=request)
    compilation_seconds = time.perf_counter() - started
    repeated = runtime.execution_plan_compiler.compile(request=request)
    reordered = runtime.execution_plan_compiler.compile(
        request=replace(
            request,
            definition=replace(
                request.definition,
                specification_json={key: raw[key] for key in reversed(tuple(raw))},
            ),
        ),
    )
    assert compiled.is_valid and compiled.plan is not None
    assert repeated.plan == compiled.plan
    assert reordered.plan == compiled.plan
    return _QualifiedDefinition(
        runtime=runtime,
        tenant_id=tenant_id,
        definition=definition,
        version=version,
        source_dataset=source_dataset,
        source_version=source_version,
        plan=compiled.plan,
        compilation_seconds=compilation_seconds,
    )


def _authorize_and_create_run(
    *,
    qualified: _QualifiedDefinition,
    output_slug: str,
) -> TransformationRun:
    if not GovernancePolicy.objects.filter(
        tenant_id=qualified.tenant_id,
        policy_key=TRANSFORMATION_SECURITY_POLICY_KEY,
        is_tenant_default=True,
    ).exists():
        _create_governance(tenant_id=qualified.tenant_id, dataset=qualified.source_dataset)
    policy = TransformationOutputPolicy(
        logical_name="result",
        policy_kind=TransformationOutputPolicyKind.NEW_DATASET,
        destination_storage_provider="local",
        configuration={"dataset_slug": output_slug, "dataset_name": "Qualified Output"},
    )
    context = TransformationAuthorizationContext(
        tenant_id=str(qualified.tenant_id),
        actor="service:runtime-qualification",
        principal_kind=TransformationPrincipalKind.SERVICE,
        permissions=frozenset(
            {
                EXECUTE_TRANSFORMATION_PERMISSION,
                USE_TRANSFORMATION_PROVIDER_PERMISSION,
                ADD_DATASET_PERMISSION,
            },
        ),
        correlation_id=f"qualification-{uuid.uuid4()}",
        allowed_dataset_ids=frozenset({str(qualified.source_dataset.id)}),
        allowed_transformation_definition_ids=frozenset({str(qualified.definition.id)}),
    )
    result = qualified.runtime.authorized_creation_service.create_run(
        context=context,
        request=TransformationRunCreationRequest(
            tenant_id=qualified.tenant_id,
            definition_id=qualified.definition.id,
            definition_version_id=qualified.version.id,
            execution_plan=qualified.plan,
            output_policies=(policy,),
        ),
    )
    return TransformationRun.objects.get(id=result.run_id)


def _create_qualified_run(
    *,
    tenant_id: uuid.UUID,
    definition: TransformationDefinition | None = None,
    version: TransformationDefinitionVersion | None = None,
) -> TransformationRun:
    active_definition = definition or TransformationDefinition.objects.create(
        tenant_id=tenant_id,
        definition_key=f"qualification-{uuid.uuid4()}",
        name="Story 4 Qualification",
    )
    active_version = version or TransformationDefinitionVersion.objects.create(
        definition=active_definition,
        version_number=1,
        status=TransformationDefinitionVersionStatus.PUBLISHED,
        specification_json={"dsl_version": "1.0"},
        specification_checksum="a" * 64,
    )
    source_dataset, _ = Dataset.objects.get_or_create(
        id=uuid.UUID(int=101),
        defaults={
            "tenant_id": tenant_id,
            "slug": f"source-{uuid.uuid4().hex[:8]}",
            "name": "Source",
            "source_type": DatasetSourceType.FILE_UPLOAD,
        },
    )
    DatasetVersion.objects.get_or_create(
        id=uuid.UUID(int=102),
        defaults={
            "dataset": source_dataset,
            "version_number": 1,
            "source_uri": "local://input.parquet",
            "content_hash": "input-content",
            "status": DatasetVersionStatus.READY,
        },
    )
    execution_plan = build_test_execution_plan(
        tenant_id=tenant_id,
        definition_id=active_definition.id,
        definition_version_id=active_version.id,
        specification_checksum="a" * 64,
        output_names=("result",),
    )
    output_policies = (
        TransformationOutputPolicy(
            logical_name="result",
            policy_kind=TransformationOutputPolicyKind.NEW_DATASET,
            destination_storage_provider="local",
            configuration={
                "dataset_slug": f"qualified-result-{uuid.uuid4().hex[:8]}",
                "dataset_name": "Qualified Result",
            },
        ),
    )
    creation = TransformationRunCreationService().create_run(
        request=TransformationRunCreationRequest(
            tenant_id=tenant_id,
            definition_id=active_definition.id,
            definition_version_id=active_version.id,
            execution_plan=execution_plan,
            authorization_decision=build_test_authorization_decision(
                tenant_id=tenant_id,
                definition_id=active_definition.id,
                definition_version_id=active_version.id,
                dataset_ids=tuple(input_item.dataset_id for input_item in execution_plan.inputs),
            ),
            output_policies=output_policies,
        ),
    )
    return TransformationRun.objects.get(id=creation.run_id)


def _execute_run_to_output_pending(
    *,
    run: TransformationRun,
    provider_workspace: Path,
) -> None:
    provider = _DeterministicFileProvider(workspace=provider_workspace)
    registry = TransformationProviderRegistry()
    registry.register_provider(provider=provider)
    freeze_test_provider_registry(registry)
    job_service = build_test_job_service()
    coordinator = TransformationRunExecutionCoordinator(
        execution_service=TransformationRunExecutionService(
            provider_registry=registry,
            persistence=TransformationStepCheckpointService(),
        ),
        job_service=job_service,
        metrics=MagicMock(),
    )
    claimed = job_service.claim_next_run(
        worker_id="qualification-worker",
        checkpoint_reference_verifier=_Verifier(),
        lease_seconds=120,
        execution_timeout_seconds=0,
        tenant_id=str(run.tenant_id),
    )
    assert claimed is not None
    lifecycle = coordinator.execute_claimed_run(claimed=claimed)
    assert lifecycle.lifecycle.action.value == "OUTPUT_PENDING"


def _fingerprint(run: TransformationRun) -> dict[str, object]:
    output = TransformationRunOutput.objects.get(run=run)
    step = TransformationRunStepAttempt.objects.get(run=run)
    lineage = DatasetLineage.objects.get(job_id=run.id)
    checkpoint_output = step.checkpoint_outputs_json[0]
    return {
        "plan_checksum": run.execution_plan_checksum,
        "checkpoint_logical_name": checkpoint_output["logical_name"],
        "checkpoint_data_format": checkpoint_output["data_format"],
        "checkpoint_integrity": checkpoint_output["integrity"],
        "output_checksum": output.checksum_value,
        "output_checksum_algorithm": output.checksum_algorithm,
        "output_status": output.status,
        "run_status": run.status,
        "lineage_upstream": str(lineage.upstream_dataset_version_id),
        "lineage_hash": lineage.transformation_hash,
    }


def test_runtime_identity_is_stable_across_health_transitions() -> None:
    runtime = RuntimeCompositionRoot().bootstrap_or_raise(
        configuration=_configuration(PROVIDER_IDENTITY),
    )
    identity_before = runtime.identity
    tenant_id = str(uuid.uuid4())

    with (
        patch.object(runtime.health_service._metrics, "is_available", return_value=True) as metrics,
        patch.object(runtime.health_service._audit_service, "is_available", return_value=True),
        patch.object(
            runtime.health_service._authorization_service,
            "is_available",
            return_value=True,
        ),
        patch.object(
            runtime.health_service._policy_resolver,
            "is_available",
            return_value=True,
        ) as policies,
    ):
        ready = runtime.health_service.get_health(tenant_id=tenant_id)

        metrics.return_value = False
        degraded = runtime.health_service.get_health(tenant_id=tenant_id)

        metrics.return_value = True
        recovered_ready = runtime.health_service.get_health(tenant_id=tenant_id)

        policies.return_value = False
        not_ready = runtime.health_service.get_health(tenant_id=tenant_id)

    assert ready.status == TransformationOperationalHealthStatus.READY
    assert degraded.status == TransformationOperationalHealthStatus.DEGRADED
    assert recovered_ready.status == TransformationOperationalHealthStatus.READY
    assert not_ready.status == TransformationOperationalHealthStatus.NOT_READY
    assert runtime.identity == identity_before


def test_qualification_report_matches_deterministic_runtime_identity() -> None:
    runtime = RuntimeCompositionRoot().bootstrap_or_raise(
        configuration=_configuration(PROVIDER_IDENTITY),
        worker_id="qualification-report",
    )
    report = json.loads(_QUALIFICATION_REPORT_PATH.read_text(encoding="utf-8"))

    assert report["architecture_version"] == runtime.identity.runtime_contract_version
    assert report["runtime_version"] == runtime.identity.runtime_contract_version
    assert report["registry_checksum"] == runtime.identity.registry_manifest_checksum
    assert report["configuration_checksum"] == runtime.identity.configuration_checksum
    assert report["runtime_checksum"] == runtime.identity.runtime_checksum
    assert report["provider_versions"] == [
        f"{PROVIDER_IDENTITY.provider_key}@{PROVIDER_IDENTITY.provider_version}",
    ]
    assert report["compiler_version"] == f"execution-plan/{EXECUTION_PLAN_CONTRACT_VERSION}"
    assert report["passed_scenarios"] == len(report["executed_scenarios"])
    assert report["failed_scenarios"] == 0


@override_settings(RESOURCE_REGISTRY_STORAGE_PROVIDER="local")
def test_production_runtime_qualifies_definition_through_cleanup(
    tmp_path: Path,
    record_property: Callable[[str, object], None],
) -> None:
    storage_root = tmp_path / "storage"
    with override_settings(RESOURCE_REGISTRY_LOCAL_STORAGE_DIR=storage_root):
        startup_started = time.perf_counter()
        runtime = RuntimeCompositionRoot().bootstrap_or_raise(
            configuration=_configuration(PROVIDER_IDENTITY),
            worker_id="qualification-worker",
        )
        startup_seconds = time.perf_counter() - startup_started
        qualified = _compile_production_definition(
            runtime=runtime,
            tenant_id=uuid.uuid4(),
            input_path=tmp_path / "input.parquet",
        )
        run = _authorize_and_create_run(
            qualified=qualified,
            output_slug=f"qualified-{uuid.uuid4().hex[:8]}",
        )

        execution_started = time.perf_counter()
        execution_stats = runtime.worker_runner.run(
            once=True,
            tenant_id=str(qualified.tenant_id),
        )
        execution_seconds = time.perf_counter() - execution_started
        run.refresh_from_db()
        assert execution_stats.processed_runs == 1
        assert run.status == TransformationRunStatus.OUTPUT_PENDING

        close_old_connections()
        finalization_started = time.perf_counter()
        finalization_stats = runtime.output_finalization_worker_runner.run(
            once=True,
            tenant_id=str(qualified.tenant_id),
        )
        finalization_seconds = time.perf_counter() - finalization_started
        run.refresh_from_db()
        assert finalization_stats.processed_runs == 1
        assert run.status == TransformationRunStatus.SUCCEEDED

        cleanup_stats = runtime.output_finalization_worker_runner.run(
            once=True,
            tenant_id=str(qualified.tenant_id),
        )

    output = TransformationRunOutput.objects.get(run=run)
    step = TransformationRunStepAttempt.objects.get(run=run)
    lineage = DatasetLineage.objects.get(job_id=run.id)
    lease = TransformationWorkspaceLease.objects.get(run=run)
    assert output.status == TransformationRunOutputStatus.REGISTERED
    assert output.dataset_artifact_id is not None
    assert DatasetArtifact.objects.filter(id=output.dataset_artifact_id).count() == 1
    assert DatasetLineage.objects.filter(job_id=run.id).count() == 1
    assert lineage.transformation_hash == run.execution_plan_checksum
    assert step.checkpoint_checksum is not None
    assert lease.state == TransformationWorkspaceLeaseState.RELEASED
    assert cleanup_stats.cleaned_leases == 1
    assert runtime.provider_registry.resolve_operation(
        provider_key=PROVIDER_IDENTITY.provider_key,
        provider_version=PROVIDER_IDENTITY.provider_version,
        operation_key=PROJECT_COLUMNS_IDENTITY.operation_key,
        operation_version=PROJECT_COLUMNS_IDENTITY.operation_version,
    ).operation.identity == PROJECT_COLUMNS_IDENTITY
    plan_step = qualified.plan.steps[0]
    assert plan_step.provider_key == PROVIDER_IDENTITY.provider_key
    assert plan_step.provider_version == PROVIDER_IDENTITY.provider_version
    assert plan_step.operation_key == PROJECT_COLUMNS_IDENTITY.operation_key
    assert plan_step.operation_version == PROJECT_COLUMNS_IDENTITY.operation_version
    assert len(plan_step.capabilities.capability_checksum) == _SHA256_HEX_LENGTH
    diagnostics = runtime.diagnostics_service.get_diagnostics(
        context=TransformationAuthorizationContext(
            tenant_id=str(qualified.tenant_id),
            actor="operator:qualification",
            principal_kind=TransformationPrincipalKind.SERVICE,
            permissions=frozenset({VIEW_TRANSFORMATION_DIAGNOSTICS_PERMISSION}),
            correlation_id="qualification-diagnostics",
        ),
    )
    assert diagnostics.runs_by_status == {TransformationRunStatus.SUCCEEDED: 1}
    assert diagnostics.outputs_by_status == {TransformationRunOutputStatus.REGISTERED: 1}
    assert diagnostics.workspace_leases_by_state == {
        TransformationWorkspaceLeaseState.RELEASED: 1,
    }
    for metric_name, value in {
        "runtime_startup_seconds": startup_seconds,
        "compilation_seconds": qualified.compilation_seconds,
        "execution_seconds": execution_seconds,
        "finalization_seconds": finalization_seconds,
    }.items():
        assert value >= 0
        record_property(metric_name, value)


@override_settings(RESOURCE_REGISTRY_STORAGE_PROVIDER="local")
def test_corrupt_checkpoint_fails_closed_without_registration(tmp_path: Path) -> None:
    with override_settings(RESOURCE_REGISTRY_LOCAL_STORAGE_DIR=tmp_path / "storage"):
        runtime = RuntimeCompositionRoot().bootstrap_or_raise(
            configuration=_configuration(PROVIDER_IDENTITY),
            worker_id="chaos-worker",
        )
        qualified = _compile_production_definition(
            runtime=runtime,
            tenant_id=uuid.uuid4(),
            input_path=tmp_path / "input.parquet",
        )
        run = _authorize_and_create_run(
            qualified=qualified,
            output_slug=f"corrupt-{uuid.uuid4().hex[:8]}",
        )
        runtime.worker_runner.run(once=True, tenant_id=str(qualified.tenant_id))
        step = TransformationRunStepAttempt.objects.get(run=run)
        step.checkpoint_outputs_json = [{"corrupted": True}]
        step.save(update_fields=["checkpoint_outputs_json"])

        runtime.output_finalization_worker_runner.run(
            once=True,
            tenant_id=str(qualified.tenant_id),
        )

    run.refresh_from_db()
    assert run.status == TransformationRunStatus.FAILED
    assert run.error_code == "output_checkpoint_invalid"
    assert DatasetArtifact.objects.filter(transformation_run_outputs__run=run).count() == 0
    assert DatasetLineage.objects.filter(job_id=run.id).count() == 0


@override_settings(RESOURCE_REGISTRY_STORAGE_PROVIDER="local")
def test_simulated_storage_interruption_retries_without_duplicate_assets(tmp_path: Path) -> None:
    with override_settings(RESOURCE_REGISTRY_LOCAL_STORAGE_DIR=tmp_path / "storage"):
        runtime = RuntimeCompositionRoot().bootstrap_or_raise(
            configuration=_configuration(PROVIDER_IDENTITY),
            worker_id="storage-chaos-worker",
        )
        qualified = _compile_production_definition(
            runtime=runtime,
            tenant_id=uuid.uuid4(),
            input_path=tmp_path / "input.parquet",
        )
        run = _authorize_and_create_run(
            qualified=qualified,
            output_slug=f"retry-{uuid.uuid4().hex[:8]}",
        )
        runtime.worker_runner.run(once=True, tenant_id=str(qualified.tenant_id))

        with patch.object(LocalStorageProvider, "put", side_effect=RuntimeError("interrupted")):
            runtime.output_finalization_worker_runner.run(
                once=True,
                tenant_id=str(qualified.tenant_id),
            )
        run.refresh_from_db()
        assert run.status == TransformationRunStatus.OUTPUT_PENDING
        assert run.finalization_retry_count == 1

        runtime.output_finalization_worker_runner.run(
            once=True,
            tenant_id=str(qualified.tenant_id),
        )

    run.refresh_from_db()
    assert run.status == TransformationRunStatus.SUCCEEDED
    assert DatasetArtifact.objects.filter(transformation_run_outputs__run=run).count() == 1
    assert DatasetLineage.objects.filter(job_id=run.id).count() == 1


@override_settings(RESOURCE_REGISTRY_STORAGE_PROVIDER="local")
def test_production_authorization_rejects_cross_tenant_creation(tmp_path: Path) -> None:
    with override_settings(RESOURCE_REGISTRY_LOCAL_STORAGE_DIR=tmp_path / "storage"):
        runtime = RuntimeCompositionRoot().bootstrap_or_raise(
            configuration=_configuration(PROVIDER_IDENTITY),
        )
        qualified = _compile_production_definition(
            runtime=runtime,
            tenant_id=uuid.uuid4(),
            input_path=tmp_path / "input.parquet",
        )
        _create_governance(
            tenant_id=qualified.tenant_id,
            dataset=qualified.source_dataset,
        )
        policy = TransformationOutputPolicy(
            logical_name="result",
            policy_kind=TransformationOutputPolicyKind.NEW_DATASET,
            destination_storage_provider="local",
            configuration={"dataset_slug": "denied", "dataset_name": "Denied"},
        )
        context = TransformationAuthorizationContext(
            tenant_id=str(uuid.uuid4()),
            actor="service:wrong-tenant",
            principal_kind=TransformationPrincipalKind.SERVICE,
            permissions=frozenset(
                {
                    EXECUTE_TRANSFORMATION_PERMISSION,
                    USE_TRANSFORMATION_PROVIDER_PERMISSION,
                    ADD_DATASET_PERMISSION,
                },
            ),
            correlation_id="cross-tenant-denied",
        )

        with pytest.raises(TransformationPermissionDeniedError):
            runtime.authorized_creation_service.create_run(
                context=context,
                request=TransformationRunCreationRequest(
                    tenant_id=qualified.tenant_id,
                    definition_id=qualified.definition.id,
                    definition_version_id=qualified.version.id,
                    execution_plan=qualified.plan,
                    output_policies=(policy,),
                ),
            )

    assert TransformationRun.objects.filter(definition=qualified.definition).count() == 0


@override_settings(RESOURCE_REGISTRY_STORAGE_PROVIDER="local")
def test_postgresql_multi_tenant_workers_complete_without_duplicates(tmp_path: Path) -> None:
    if connection.vendor != "postgresql":
        pytest.skip("Production concurrency qualification requires PostgreSQL.")
    with override_settings(RESOURCE_REGISTRY_LOCAL_STORAGE_DIR=tmp_path / "storage"):
        runtimes = tuple(
            RuntimeCompositionRoot().bootstrap_or_raise(
                configuration=_configuration(PROVIDER_IDENTITY),
                worker_id=f"qualification-worker-{index}",
            )
            for index in range(2)
        )
        qualified = tuple(
            _compile_production_definition(
                runtime=runtimes[index],
                tenant_id=uuid.uuid4(),
                input_path=tmp_path / f"input-{index}.parquet",
            )
            for index in range(2)
        )
        runs = tuple(
            _authorize_and_create_run(
                qualified=qualified[index],
                output_slug=f"concurrent-{index}-{uuid.uuid4().hex[:8]}",
            )
            for index in range(2)
        )

        def execute(index: int) -> int:
            close_old_connections()
            try:
                return runtimes[index].worker_runner.run(
                    once=True,
                    tenant_id=str(qualified[index].tenant_id),
                ).processed_runs
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            execution_counts = tuple(executor.map(execute, range(2)))
        assert execution_counts == (1, 1)

        def finalize(index: int) -> int:
            close_old_connections()
            try:
                return runtimes[index].output_finalization_worker_runner.run(
                    once=True,
                    tenant_id=str(qualified[index].tenant_id),
                ).processed_runs
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            finalization_counts = tuple(executor.map(finalize, range(2)))
        assert finalization_counts == (1, 1)

    for run in runs:
        run.refresh_from_db()
        assert run.status == TransformationRunStatus.SUCCEEDED
        assert DatasetArtifact.objects.filter(transformation_run_outputs__run=run).count() == 1
        assert DatasetLineage.objects.filter(job_id=run.id).count() == 1
    assert len({run.outputs.get().dataset_artifact_id for run in runs}) == len(runs)


def test_end_to_end_runtime_pipeline_registers_dataset_and_lineage(tmp_path: Path) -> None:
    tenant_id = uuid.uuid4()
    run = _create_qualified_run(tenant_id=tenant_id)

    _execute_run_to_output_pending(run=run, provider_workspace=tmp_path)

    finalization = build_test_output_finalization_service().finalize_run(
        tenant_id=run.tenant_id,
        run_id=run.id,
    )

    run.refresh_from_db()
    output = TransformationRunOutput.objects.get(run=run)
    lineage = DatasetLineage.objects.get(job_id=run.id)
    assert finalization.status == TransformationRunStatus.SUCCEEDED
    assert run.status == TransformationRunStatus.SUCCEEDED
    assert output.status == TransformationRunOutputStatus.REGISTERED
    assert output.dataset_id is not None
    assert output.dataset_version_id is not None
    assert lineage.transformation_hash == run.execution_plan_checksum


def test_repeated_end_to_end_runs_are_deterministic_except_identifiers(tmp_path: Path) -> None:
    tenant_id = uuid.uuid4()
    first_run = _create_qualified_run(tenant_id=tenant_id)
    _execute_run_to_output_pending(run=first_run, provider_workspace=tmp_path / "first")
    build_test_output_finalization_service().finalize_run(
        tenant_id=first_run.tenant_id,
        run_id=first_run.id,
    )

    second_run = _create_qualified_run(
        tenant_id=tenant_id,
        definition=first_run.definition,
        version=first_run.definition_version,
    )
    _execute_run_to_output_pending(run=second_run, provider_workspace=tmp_path / "second")
    build_test_output_finalization_service().finalize_run(
        tenant_id=second_run.tenant_id,
        run_id=second_run.id,
    )

    first_run.refresh_from_db()
    second_run.refresh_from_db()
    assert _fingerprint(first_run) == _fingerprint(second_run)
