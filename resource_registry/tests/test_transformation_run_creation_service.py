from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import patch

import pytest
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.utils import timezone

from resource_registry.models import (
    TransformationDefinition,
    TransformationDefinitionVersion,
    TransformationDefinitionVersionStatus,
    TransformationRun,
    TransformationRunOutput,
    TransformationRunOutputStatus,
    TransformationRunStatus,
    TransformationRunStepAttempt,
)
from resource_registry.tests.transformation_run_test_helpers import (
    build_test_authorization_decision,
    build_test_execution_plan,
    build_test_output_policies,
)
from resource_registry.transformations.runs import (
    TransformationRunCreationRequest,
    TransformationRunCreationService,
)
from resource_registry.transformations.runs.exceptions import (
    ArchivedTransformationRunDefinitionError,
    IneligibleTransformationRunDefinitionVersionError,
    InvalidTransformationRunIdempotencyKeyError,
    TransformationExecutionPlanChecksumMismatchError,
    TransformationRunDefinitionNotFoundError,
    TransformationRunDefinitionVersionNotFoundError,
    TransformationRunIdempotencyConflictError,
    TransformationRunPlanIdentityMismatchError,
    TransformationRunSpecificationChecksumMismatchError,
)

pytestmark = pytest.mark.django_db

_SPECIFICATION_CHECKSUM = "a" * 64
_DEFAULT_PARAMETER_VALUE = 0.5
_TWO_ROWS = 2


def _create_definition_and_version(
    *,
    tenant_id: uuid.UUID | None = None,
    definition_key: str | None = None,
    archived: bool = False,
    status: str = TransformationDefinitionVersionStatus.PUBLISHED,
) -> tuple[TransformationDefinition, TransformationDefinitionVersion]:
    definition = TransformationDefinition.objects.create(
        tenant_id=tenant_id or uuid.uuid4(),
        definition_key=definition_key or f"definition-{uuid.uuid4()}",
        name="Transformation",
        archived_at=timezone.now() if archived else None,
    )
    version = TransformationDefinitionVersion.objects.create(
        definition=definition,
        version_number=1,
        status=status,
        specification_json={"dsl_version": "1.0"},
        specification_checksum=_SPECIFICATION_CHECKSUM,
    )
    return definition, version


def _build_request(
    *,
    definition: TransformationDefinition,
    version: TransformationDefinitionVersion,
    idempotency_key: str | None = None,
    parameter_value: float = 0.5,
    correlation_id: str | None = None,
) -> TransformationRunCreationRequest:
    plan = build_test_execution_plan(
        tenant_id=definition.tenant_id,
        definition_id=definition.id,
        definition_version_id=version.id,
        specification_checksum=_SPECIFICATION_CHECKSUM,
        parameter_value=parameter_value,
    )
    return TransformationRunCreationRequest(
        tenant_id=definition.tenant_id,
        definition_id=definition.id,
        definition_version_id=version.id,
        execution_plan=plan,
        authorization_decision=build_test_authorization_decision(
            tenant_id=definition.tenant_id,
            definition_id=definition.id,
            definition_version_id=version.id,
        ),
        output_policies=build_test_output_policies(
            tuple(output.logical_name for output in plan.outputs),
        ),
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
    )


def test_creation_persists_queued_run_plan_and_pending_output_slots() -> None:
    definition, version = _create_definition_and_version()
    request = _build_request(definition=definition, version=version)

    result = TransformationRunCreationService().create_run(request=request)

    run = TransformationRun.objects.get(pk=result.run_id)
    outputs = list(run.outputs.order_by("output_index"))
    assert result.was_created is True
    assert run.status == TransformationRunStatus.QUEUED
    assert run.latest_attempt_number == 0
    assert run.retry_count == 0
    assert run.cancellation_requested_at is None
    assert run.started_at is None
    assert run.completed_at is None
    assert run.error_code is None
    assert run.execution_plan_checksum == request.execution_plan.plan_checksum
    assert run.execution_plan_snapshot_json["plan_checksum"] == request.execution_plan.plan_checksum
    assert run.execution_plan_snapshot_json["parameters"][0]["value"] == _DEFAULT_PARAMETER_VALUE
    assert run.execution_plan_snapshot_json["inputs"][0]["logical_name"] == "source"
    assert run.authorization_contract_version == request.authorization_decision.contract_version
    assert run.authorization_checksum == request.authorization_decision.checksum
    assert run.authorization_snapshot_json["tenant_id"] == str(definition.tenant_id)
    assert run.authorization_policy_revision == 1
    assert run.governance_policy_revision == 1
    assert run.authorized_actor == "test-actor"
    assert run.authorized_at is not None
    assert run.correlation_id == str(run.id)
    assert TransformationRunStepAttempt.objects.filter(run=run).count() == 0
    assert [output.logical_name for output in outputs] == ["result", "audit"]
    assert [output.output_index for output in outputs] == [0, 1]
    assert all(output.status == TransformationRunOutputStatus.PENDING for output in outputs)
    assert all(output.output_kind == "STANDALONE" for output in outputs)
    assert all(output.output_policy_checksum is not None for output in outputs)
    assert all(output.canonical_uri is None for output in outputs)
    assert all(output.producing_step_attempt is None for output in outputs)
    assert all(output.dataset_id is None for output in outputs)


@pytest.mark.parametrize(
    ("archived", "status", "expected_error"),
    [
        (
            True,
            TransformationDefinitionVersionStatus.PUBLISHED,
            ArchivedTransformationRunDefinitionError,
        ),
        (
            False,
            TransformationDefinitionVersionStatus.DRAFT,
            IneligibleTransformationRunDefinitionVersionError,
        ),
        (
            False,
            TransformationDefinitionVersionStatus.DEPRECATED,
            IneligibleTransformationRunDefinitionVersionError,
        ),
    ],
)
def test_ineligible_definition_lifecycle_creates_no_rows(
    archived: bool,
    status: str,
    expected_error: type[Exception],
) -> None:
    definition, version = _create_definition_and_version(archived=archived, status=status)
    request = _build_request(definition=definition, version=version)

    with pytest.raises(expected_error):
        TransformationRunCreationService().create_run(request=request)

    assert TransformationRun.objects.filter(definition=definition).count() == 0
    assert TransformationRunOutput.objects.filter(run__definition=definition).count() == 0


def test_wrong_tenant_and_wrong_parent_do_not_disclose_or_create() -> None:
    definition, version = _create_definition_and_version()
    other_definition, other_version = _create_definition_and_version(
        tenant_id=definition.tenant_id,
    )
    valid = _build_request(definition=definition, version=version)
    wrong_tenant = replace(valid, tenant_id=uuid.uuid4())
    wrong_parent_plan = build_test_execution_plan(
        tenant_id=definition.tenant_id,
        definition_id=definition.id,
        definition_version_id=other_version.id,
        specification_checksum=_SPECIFICATION_CHECKSUM,
    )
    wrong_parent = replace(
        valid,
        definition_version_id=other_version.id,
        execution_plan=wrong_parent_plan,
        authorization_decision=build_test_authorization_decision(
            tenant_id=definition.tenant_id,
            definition_id=definition.id,
            definition_version_id=other_version.id,
        ),
    )

    with pytest.raises(TransformationRunPlanIdentityMismatchError):
        TransformationRunCreationService().create_run(request=wrong_tenant)
    with pytest.raises(TransformationRunDefinitionVersionNotFoundError):
        TransformationRunCreationService().create_run(request=wrong_parent)

    assert other_definition.id != definition.id
    assert TransformationRun.objects.count() == 0


def test_missing_tenant_scoped_definition_is_not_disclosed() -> None:
    definition, version = _create_definition_and_version()
    request = _build_request(definition=definition, version=version)
    other_tenant_id = uuid.uuid4()
    other_tenant_plan = build_test_execution_plan(
        tenant_id=other_tenant_id,
        definition_id=definition.id,
        definition_version_id=version.id,
        specification_checksum=_SPECIFICATION_CHECKSUM,
    )

    with pytest.raises(TransformationRunDefinitionNotFoundError):
        TransformationRunCreationService().create_run(
            request=replace(
                request,
                tenant_id=other_tenant_id,
                execution_plan=other_tenant_plan,
                authorization_decision=build_test_authorization_decision(
                    tenant_id=other_tenant_id,
                    definition_id=definition.id,
                    definition_version_id=version.id,
                ),
            ),
        )


def test_specification_or_plan_checksum_drift_creates_no_run() -> None:
    definition, version = _create_definition_and_version()
    request = _build_request(definition=definition, version=version)
    version.specification_checksum = "c" * 64
    version.save(update_fields=["specification_checksum"])

    with pytest.raises(TransformationRunSpecificationChecksumMismatchError):
        TransformationRunCreationService().create_run(request=request)
    with pytest.raises(TransformationExecutionPlanChecksumMismatchError):
        TransformationRunCreationService().create_run(
            request=replace(
                request,
                execution_plan=replace(request.execution_plan, plan_checksum="d" * 64),
            ),
        )

    assert TransformationRun.objects.count() == 0


def test_active_equivalent_request_returns_existing_run() -> None:
    definition, version = _create_definition_and_version()
    request = _build_request(
        definition=definition,
        version=version,
        correlation_id="first-correlation",
    )
    service = TransformationRunCreationService()

    first = service.create_run(request=request)
    repeated = service.create_run(
        request=replace(request, correlation_id="ignored-correlation"),
    )

    assert first.was_created is True
    assert repeated.was_created is False
    assert repeated.run_id == first.run_id
    assert repeated.request_fingerprint == first.request_fingerprint
    assert TransformationRun.objects.count() == 1


def test_terminal_equivalent_request_creates_new_run_without_caller_key() -> None:
    definition, version = _create_definition_and_version()
    request = _build_request(definition=definition, version=version)
    service = TransformationRunCreationService()
    first = service.create_run(request=request)
    TransformationRun.objects.filter(pk=first.run_id).update(
        status=TransformationRunStatus.SUCCEEDED,
        completed_at=timezone.now(),
    )

    second = service.create_run(request=request)

    assert second.was_created is True
    assert second.run_id != first.run_id
    assert TransformationRun.objects.count() == _TWO_ROWS


def test_caller_key_is_permanent_and_conflicting_reuse_is_rejected() -> None:
    definition, version = _create_definition_and_version()
    service = TransformationRunCreationService()
    original = _build_request(
        definition=definition,
        version=version,
        idempotency_key="Exact Key",
    )
    changed = _build_request(
        definition=definition,
        version=version,
        idempotency_key="Exact Key",
        parameter_value=0.75,
    )

    first = service.create_run(request=original)
    repeated = service.create_run(request=original)
    with pytest.raises(TransformationRunIdempotencyConflictError):
        service.create_run(request=changed)

    assert repeated.run_id == first.run_id
    assert repeated.was_created is False
    assert repeated.idempotency_key == "Exact Key"
    assert repeated.idempotency_key != f"transformation_run:{repeated.request_fingerprint}"


@pytest.mark.parametrize("key", ["", "   ", "x" * 129])
def test_invalid_caller_key_is_rejected(key: str) -> None:
    definition, version = _create_definition_and_version()

    with pytest.raises(InvalidTransformationRunIdempotencyKeyError):
        TransformationRunCreationService().create_run(
            request=_build_request(
                definition=definition,
                version=version,
                idempotency_key=key,
            ),
        )


def test_same_caller_key_is_isolated_by_tenant() -> None:
    first_definition, first_version = _create_definition_and_version()
    second_definition, second_version = _create_definition_and_version()
    service = TransformationRunCreationService()

    first = service.create_run(
        request=_build_request(
            definition=first_definition,
            version=first_version,
            idempotency_key="shared-key",
        ),
    )
    second = service.create_run(
        request=_build_request(
            definition=second_definition,
            version=second_version,
            idempotency_key="shared-key",
        ),
    )

    assert first.run_id != second.run_id
    assert TransformationRun.objects.count() == _TWO_ROWS


def test_unrelated_integrity_error_is_not_swallowed() -> None:
    definition, version = _create_definition_and_version()
    request = _build_request(definition=definition, version=version)
    failure = IntegrityError("unrelated constraint")

    with patch.object(TransformationRun.objects, "create", side_effect=failure):
        with pytest.raises(IntegrityError, match="unrelated constraint"):
            TransformationRunCreationService().create_run(request=request)


def test_database_enforces_active_fingerprint_and_output_integrity_pair() -> None:
    definition, version = _create_definition_and_version()
    result = TransformationRunCreationService().create_run(
        request=_build_request(definition=definition, version=version),
    )
    run = TransformationRun.objects.get(pk=result.run_id)

    with pytest.raises(IntegrityError), transaction.atomic():
        TransformationRun.objects.create(
            tenant_id=run.tenant_id,
            definition=run.definition,
            definition_version=run.definition_version,
            specification_checksum=run.specification_checksum,
            plan_contract_version=run.plan_contract_version,
            dsl_version=run.dsl_version,
            execution_plan_snapshot_json=run.execution_plan_snapshot_json,
            execution_plan_checksum=run.execution_plan_checksum,
            request_fingerprint=run.request_fingerprint,
            status=TransformationRunStatus.QUEUED,
            correlation_id=str(uuid.uuid4()),
        )

    output = run.outputs.first()
    assert output is not None
    with pytest.raises(IntegrityError), transaction.atomic():
        TransformationRunOutput.objects.filter(pk=output.pk).update(
            checksum_algorithm="sha256",
        )


def test_database_enforces_permanent_tenant_scoped_caller_key() -> None:
    definition, version = _create_definition_and_version()
    result = TransformationRunCreationService().create_run(
        request=_build_request(
            definition=definition,
            version=version,
            idempotency_key="permanent-key",
        ),
    )
    run = TransformationRun.objects.get(pk=result.run_id)
    TransformationRun.objects.filter(pk=run.pk).update(
        status=TransformationRunStatus.SUCCEEDED,
        completed_at=timezone.now(),
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        TransformationRun.objects.create(
            tenant_id=run.tenant_id,
            definition=run.definition,
            definition_version=run.definition_version,
            specification_checksum=run.specification_checksum,
            plan_contract_version=run.plan_contract_version,
            dsl_version=run.dsl_version,
            execution_plan_snapshot_json=run.execution_plan_snapshot_json,
            execution_plan_checksum="e" * 64,
            request_fingerprint="f" * 64,
            idempotency_key="permanent-key",
            status=TransformationRunStatus.QUEUED,
            correlation_id=str(uuid.uuid4()),
        )


@pytest.mark.django_db(transaction=True)
def test_concurrent_equivalent_requests_create_one_active_run() -> None:
    if connection.vendor != "postgresql":
        pytest.skip("Concurrent active-duplicate verification requires PostgreSQL.")
    definition, version = _create_definition_and_version()
    request = _build_request(definition=definition, version=version)
    barrier = threading.Barrier(2)

    def create_concurrently() -> uuid.UUID:
        close_old_connections()
        barrier.wait()
        try:
            return TransformationRunCreationService().create_run(request=request).run_id
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        run_ids = list(executor.map(lambda _index: create_concurrently(), range(2)))

    assert run_ids[0] == run_ids[1]
    assert TransformationRun.objects.filter(status=TransformationRunStatus.QUEUED).count() == 1
