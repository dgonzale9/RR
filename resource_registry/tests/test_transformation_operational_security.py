from __future__ import annotations

import hashlib
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.db import IntegrityError, close_old_connections, connection
from django.test import override_settings

from resource_registry.events.audit_service import AuditService
from resource_registry.events.event_publisher import InMemoryEventPublisher
from resource_registry.events.event_types import EventType
from resource_registry.models import (
    Dataset,
    DatasetPolicyAssignment,
    DatasetSourceType,
    GovernancePolicy,
    GovernancePolicyType,
    TransformationRunStatus,
)
from resource_registry.tests.test_transformation_attempt_persistence import _create_run
from resource_registry.tests.test_transformation_output_finalization import (
    _create_output_pending_run,
    _standalone_policy,
)
from resource_registry.tests.test_transformation_run_creation_service import (
    _build_request,
    _create_definition_and_version,
)
from resource_registry.tests.transformation_run_test_helpers import (
    build_test_execution_plan,
    build_test_output_policies,
)
from resource_registry.tests.transformation_runtime_test_helpers import (
    build_test_output_finalization_service,
)
from resource_registry.transformations.operational.authorization import (
    EXECUTE_TRANSFORMATION_PERMISSION,
    USE_TRANSFORMATION_PROVIDER_PERMISSION,
    VIEW_TRANSFORMATION_DIAGNOSTICS_PERMISSION,
    TransformationAuthorizationService,
)
from resource_registry.transformations.operational.contracts import (
    ResolvedTransformationSecurityPolicy,
    TransformationAuthorizationContext,
    TransformationOperationalHealthStatus,
    TransformationPrincipalKind,
    TransformationProviderOperationAuthorization,
)
from resource_registry.transformations.operational.creation import (
    AuthorizedTransformationRunCreationService,
)
from resource_registry.transformations.operational.diagnostics import (
    TransformationOperationalDiagnosticsService,
)
from resource_registry.transformations.operational.exceptions import (
    TransformationPermissionDeniedError,
    TransformationSecurityPolicyConfigurationError,
)
from resource_registry.transformations.operational.governance import (
    TRANSFORMATION_SECURITY_POLICY_CONTRACT_VERSION,
    TRANSFORMATION_SECURITY_POLICY_KEY,
    TransformationSecurityPolicyResolver,
    calculate_governance_rules_checksum,
)
from resource_registry.transformations.operational.health import TransformationOperationalHealthService
from resource_registry.transformations.operational.logging import (
    TransformationOperationalLogContextBuilder,
)
from resource_registry.transformations.operational.metrics import (
    NoOpMetricsAdapter,
    ProductionMetricsAdapter,
)
from resource_registry.transformations.runs import TransformationRunCreationService
from resource_registry.utils.json_checksum import canonicalize_transformation_specification

_GOVERNANCE_REVISION = 7
_SHA256_HEX_LENGTH = 64


def _build_authorization_fixture() -> tuple[
    TransformationAuthorizationService,
    TransformationAuthorizationContext,
    object,
]:
    tenant_id = uuid.uuid4()
    definition_id = uuid.uuid4()
    definition_version_id = uuid.uuid4()
    plan = build_test_execution_plan(
        tenant_id=tenant_id,
        definition_id=definition_id,
        definition_version_id=definition_version_id,
        specification_checksum="a" * 64,
        output_names=("result",),
    )
    operation = TransformationProviderOperationAuthorization(
        provider_key="provider",
        provider_version="v1",
        operation_key="transform",
        operation_version="op1",
    )
    policy_resolver = MagicMock()
    policy_resolver.resolve.return_value = ResolvedTransformationSecurityPolicy(
        allowed_operations=frozenset({operation}),
        allowed_output_policy_kinds=frozenset({"STANDALONE"}),
        tenant_policy_revision=_GOVERNANCE_REVISION,
        policy_provenance=(
            {
                "policy_id": str(uuid.uuid4()),
                "policy_key": TRANSFORMATION_SECURITY_POLICY_KEY,
                "revision": _GOVERNANCE_REVISION,
                "rules_checksum": "b" * 64,
                "scope": "TENANT",
            },
        ),
    )
    provider_registry = MagicMock()
    provider_registry.resolve_operation.return_value = SimpleNamespace()
    service = TransformationAuthorizationService(
        policy_resolver=policy_resolver,
        provider_registry=provider_registry,
    )
    context = TransformationAuthorizationContext(
        tenant_id=str(tenant_id),
        actor="service:transformer",
        principal_kind=TransformationPrincipalKind.SERVICE,
        permissions=frozenset(
            {EXECUTE_TRANSFORMATION_PERMISSION, USE_TRANSFORMATION_PROVIDER_PERMISSION},
        ),
        correlation_id="correlation-1",
        allowed_dataset_ids=frozenset({str(uuid.UUID(int=101))}),
        allowed_transformation_definition_ids=frozenset({str(definition_id)}),
    )
    return service, context, plan


def test_authorization_snapshot_is_deterministic_and_exact() -> None:
    service, context, plan = _build_authorization_fixture()
    policies = build_test_output_policies(("result",))

    first = service.authorize_run_creation(context=context, plan=plan, output_policies=policies)
    second = service.authorize_run_creation(context=context, plan=plan, output_policies=policies)

    assert first == second
    assert first.snapshot["tenant_id"] == context.tenant_id
    assert first.snapshot["governance_policy_revision"] == _GOVERNANCE_REVISION
    assert len(first.checksum) == _SHA256_HEX_LENGTH


def test_authorization_fails_closed_for_permission_and_tenant_mismatch() -> None:
    service, context, plan = _build_authorization_fixture()
    policies = build_test_output_policies(("result",))
    missing_permission = TransformationAuthorizationContext(
        tenant_id=context.tenant_id,
        actor=context.actor,
        principal_kind=context.principal_kind,
        permissions=frozenset({EXECUTE_TRANSFORMATION_PERMISSION}),
        correlation_id=context.correlation_id,
    )
    wrong_tenant = TransformationAuthorizationContext(
        tenant_id=str(uuid.uuid4()),
        actor=context.actor,
        principal_kind=context.principal_kind,
        permissions=context.permissions,
        correlation_id=context.correlation_id,
    )

    with pytest.raises(TransformationPermissionDeniedError):
        service.authorize_run_creation(
            context=missing_permission,
            plan=plan,
            output_policies=policies,
        )
    with pytest.raises(TransformationPermissionDeniedError):
        service.authorize_run_creation(
            context=wrong_tenant,
            plan=plan,
            output_policies=policies,
        )


@pytest.mark.django_db
def test_authorized_creation_persists_correlation_and_emits_run_created() -> None:
    definition, version = _create_definition_and_version()
    request = _build_request(definition=definition, version=version)
    authorization = MagicMock()
    authorization.authorize_run_creation.return_value = request.authorization_decision
    publisher = InMemoryEventPublisher()
    context = TransformationAuthorizationContext(
        tenant_id=str(definition.tenant_id),
        actor="service:transformer",
        principal_kind=TransformationPrincipalKind.SERVICE,
        permissions=frozenset(),
        correlation_id="correlation-created",
    )

    result = AuthorizedTransformationRunCreationService(
        authorization_service=authorization,
        creation_service=TransformationRunCreationService(),
        audit_service=AuditService(publisher=publisher),
        metrics=NoOpMetricsAdapter(),
        log_context_builder=TransformationOperationalLogContextBuilder(),
    ).create_run(context=context, request=request)

    event = publisher.list_events(event_type=EventType.RUN_CREATED)[0]
    assert event.run_id == str(result.run_id)
    assert event.correlation_id == context.correlation_id


def test_transformation_audit_event_is_deterministic_immutable_and_sanitized() -> None:
    publisher = InMemoryEventPublisher()
    audit = AuditService(publisher=publisher)
    arguments = {
        "event_type": EventType.RUN_FAILED,
        "tenant_id": "tenant-1",
        "run_id": "run-1",
        "correlation_id": "correlation-1",
        "transition": "RUNNING->FAILED",
        "attempt_number": 2,
        "metadata": {
            "error_code": "provider_failed",
            "parameters": {"secret": "never"},
            "signed_url": "https://storage.test/item?sig=never",
        },
    }

    first = audit.record_transformation_event(**arguments)
    second = audit.record_transformation_event(**arguments)

    assert first.event_id == second.event_id
    assert first.metadata == {"error_code": "provider_failed"}
    with pytest.raises(TypeError):
        first.metadata["new"] = "value"  # type: ignore[index]


@pytest.mark.django_db
def test_output_completion_propagates_run_correlation_to_audit(tmp_path: Path) -> None:
    source_path = tmp_path / "source.parquet"
    source_path.write_bytes(b"output")
    run = _create_output_pending_run(
        source_path=source_path,
        policy=_standalone_policy(),
    )
    publisher = InMemoryEventPublisher()

    with override_settings(RESOURCE_REGISTRY_LOCAL_STORAGE_DIR=tmp_path / "managed"):
        result = build_test_output_finalization_service(
            audit_service=AuditService(publisher=publisher),
        ).finalize_run(tenant_id=run.tenant_id, run_id=run.id)

    event = publisher.list_events(event_type=EventType.RUN_SUCCEEDED)[0]
    assert result.status == TransformationRunStatus.SUCCEEDED
    assert event.correlation_id == run.correlation_id


def test_log_context_and_metric_labels_reject_sensitive_or_high_cardinality_data(
    caplog: pytest.LogCaptureFixture,
) -> None:
    context = TransformationOperationalLogContextBuilder().build(
        values={
            "tenant_id": "tenant-1",
            "run_id": "run-1",
            "outcome": "failed",
            "parameters": {"password": "never"},
            "stable_error_code": "provider_failed",
        },
    )
    adapter = ProductionMetricsAdapter(logger=logging.getLogger("story8-test"))

    assert "parameters" not in context
    assert set(context) == {"tenant_id", "run_id", "outcome", "stable_error_code"}
    with pytest.raises(ValueError):
        adapter._emit("counter", "unsafe", 1, {"run_id": "run-1"})
    with caplog.at_level(logging.INFO, logger="story8-test"):
        adapter.record_output_finalization(
            policy_kind="STANDALONE",
            outcome="succeeded",
            duration_ms=12,
            correlation_id="correlation-1",
        )
    metric_record = next(
        record
        for record in caplog.records
        if getattr(record, "metric_name", "") == "transformation_output_finalizations_total"
    )
    assert metric_record.metric_labels == {
        "policy_kind": "STANDALONE",
        "outcome": "succeeded",
        "error_code": "",
    }
    assert "correlation_id" not in metric_record.metric_labels


@pytest.mark.django_db
def test_health_contract_distinguishes_ready_degraded_and_not_ready() -> None:
    authorization = MagicMock()
    authorization.is_available.return_value = True
    policy = MagicMock()
    policy.is_available.return_value = True
    registry = MagicMock()
    registry.list_provider_identities.return_value = (SimpleNamespace(),)
    audit = MagicMock()
    audit.is_available.return_value = True
    metrics = MagicMock()
    metrics.is_available.return_value = True
    service = TransformationOperationalHealthService(
        authorization_service=authorization,
        policy_resolver=policy,
        provider_registry=registry,
        audit_service=audit,
        metrics=metrics,
        workspace_quota_bytes=1_000,
    )

    assert service.get_health(tenant_id=str(uuid.uuid4())).status == (
        TransformationOperationalHealthStatus.READY
    )
    metrics.is_available.return_value = False
    assert service.get_health(tenant_id=str(uuid.uuid4())).status == (
        TransformationOperationalHealthStatus.DEGRADED
    )
    policy.is_available.return_value = False
    assert service.get_health(tenant_id=str(uuid.uuid4())).status == (
        TransformationOperationalHealthStatus.NOT_READY
    )


@pytest.mark.django_db
def test_governance_missing_policy_denies_by_default() -> None:
    with pytest.raises(TransformationSecurityPolicyConfigurationError):
        TransformationSecurityPolicyResolver().resolve(
            tenant_id=str(uuid.uuid4()),
            dataset_ids=(),
        )


@pytest.mark.django_db
def test_governance_intersects_tenant_and_dataset_exact_operations() -> None:
    tenant_id = uuid.uuid4()
    dataset = Dataset.objects.create(
        tenant_id=tenant_id,
        slug="governed-input",
        name="Governed Input",
        source_type=DatasetSourceType.FILE_UPLOAD,
    )
    allowed_operation = {
        "provider_key": "provider",
        "provider_version": "v1",
        "operation_key": "transform",
        "operation_version": "op1",
    }
    tenant_rules = {
        "contract_version": TRANSFORMATION_SECURITY_POLICY_CONTRACT_VERSION,
        "allowed_provider_operations": [
            allowed_operation,
            {
                **allowed_operation,
                "operation_version": "op2",
            },
        ],
        "allowed_output_policy_kinds": ["STANDALONE", "NEW_DATASET"],
    }
    dataset_rules = {
        "contract_version": TRANSFORMATION_SECURITY_POLICY_CONTRACT_VERSION,
        "allowed_provider_operations": [allowed_operation],
        "allowed_output_policy_kinds": ["STANDALONE"],
    }
    GovernancePolicy.objects.create(
        tenant_id=tenant_id,
        policy_key=TRANSFORMATION_SECURITY_POLICY_KEY,
        revision=1,
        policy_type=GovernancePolicyType.ACCESS,
        rules_json=tenant_rules,
        rules_checksum=calculate_governance_rules_checksum(tenant_rules),
        is_active=True,
        is_tenant_default=True,
    )
    dataset_policy = GovernancePolicy.objects.create(
        tenant_id=tenant_id,
        policy_key=TRANSFORMATION_SECURITY_POLICY_KEY,
        revision=2,
        policy_type=GovernancePolicyType.ACCESS,
        rules_json=dataset_rules,
        rules_checksum=calculate_governance_rules_checksum(dataset_rules),
        is_active=True,
        is_tenant_default=False,
    )
    DatasetPolicyAssignment.objects.create(
        dataset=dataset,
        governance_policy=dataset_policy,
    )

    resolved = TransformationSecurityPolicyResolver().resolve(
        tenant_id=str(tenant_id),
        dataset_ids=(str(dataset.id),),
    )

    assert resolved.allowed_operations == frozenset(
        {TransformationProviderOperationAuthorization(**allowed_operation)},
    )
    assert resolved.allowed_output_policy_kinds == frozenset({"STANDALONE"})


@pytest.mark.django_db
def test_diagnostics_are_tenant_scoped_aggregates_only() -> None:
    run = _create_run()
    context = TransformationAuthorizationContext(
        tenant_id=str(run.tenant_id),
        actor="operator",
        principal_kind=TransformationPrincipalKind.USER,
        permissions=frozenset({VIEW_TRANSFORMATION_DIAGNOSTICS_PERMISSION}),
        correlation_id=run.correlation_id,
    )

    snapshot = TransformationOperationalDiagnosticsService(
        authorization_service=MagicMock(),
        provider_registry=MagicMock(),
    ).get_diagnostics(
        context=context,
    )

    assert snapshot.tenant_id == str(run.tenant_id)
    assert snapshot.runs_by_status == {"QUEUED": 1}
    assert snapshot.steps_by_status == {}
    assert snapshot.outputs_by_status == {"PENDING": 2}


@pytest.mark.django_db(transaction=True)
def test_postgresql_concurrent_tenant_policy_activation_has_one_winner() -> None:
    if connection.vendor != "postgresql":
        pytest.skip("Concurrent policy activation verification requires PostgreSQL.")
    tenant_id = uuid.uuid4()
    rules = {
        "contract_version": TRANSFORMATION_SECURITY_POLICY_CONTRACT_VERSION,
        "allowed_provider_operations": [],
        "allowed_output_policy_kinds": [],
    }
    checksum = calculate_governance_rules_checksum(rules)
    barrier = threading.Barrier(2)

    def create_policy(revision: int) -> bool:
        close_old_connections()
        barrier.wait()
        try:
            GovernancePolicy.objects.create(
                tenant_id=tenant_id,
                policy_key=TRANSFORMATION_SECURITY_POLICY_KEY,
                revision=revision,
                policy_type=GovernancePolicyType.ACCESS,
                rules_json=rules,
                rules_checksum=checksum,
                is_active=True,
                is_tenant_default=True,
            )
        except IntegrityError:
            return False
        finally:
            close_old_connections()
        return True

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(create_policy, (1, 2)))

    assert sorted(results) == [False, True]
    assert (
        GovernancePolicy.objects.filter(
            tenant_id=tenant_id,
            policy_key=TRANSFORMATION_SECURITY_POLICY_KEY,
            is_active=True,
            is_tenant_default=True,
        ).count()
        == 1
    )


def test_policy_checksum_is_canonical() -> None:
    left = {"b": [], "a": TRANSFORMATION_SECURITY_POLICY_CONTRACT_VERSION}
    right = {"a": TRANSFORMATION_SECURITY_POLICY_CONTRACT_VERSION, "b": []}

    assert (
        calculate_governance_rules_checksum(left)
        == hashlib.sha256(
            canonicalize_transformation_specification(right).encode("utf-8"),
        ).hexdigest()
    )
