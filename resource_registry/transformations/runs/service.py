from __future__ import annotations

import hashlib
import uuid
from uuid import UUID

from django.db import IntegrityError, transaction
from django.utils import timezone

from resource_registry.cleanup.admission import lock_referenced_datasets
from resource_registry.models import (
    Dataset,
    TransformationDefinition,
    TransformationDefinitionVersion,
    TransformationDefinitionVersionStatus,
    TransformationRun,
    TransformationRunOutput,
    TransformationRunOutputKind,
    TransformationRunOutputPolicyKind,
    TransformationRunOutputStatus,
    TransformationRunStatus,
)
from resource_registry.transformations.binding.immutable_json import thaw_json_value
from resource_registry.transformations.execution_plan import ExecutionPlanOutput
from resource_registry.transformations.operational.contracts import (
    TransformationAuthorizationDecision,
)
from resource_registry.transformations.runs.contracts import (
    TransformationRunCreationRequest,
    TransformationRunCreationResult,
)
from resource_registry.transformations.runs.exceptions import (
    ArchivedTransformationRunDefinitionError,
    IneligibleTransformationRunDefinitionVersionError,
    InvalidTransformationRunCreationRequestError,
    InvalidTransformationRunIdempotencyKeyError,
    TransformationRunDefinitionNotFoundError,
    TransformationRunDefinitionVersionNotFoundError,
    TransformationRunIdempotencyConflictError,
    TransformationRunPlanIdentityMismatchError,
    TransformationRunSpecificationChecksumMismatchError,
)
from resource_registry.transformations.runs.fingerprint import (
    build_transformation_run_request_fingerprint,
)
from resource_registry.transformations.runs.output_policies import (
    build_output_destination_key,
    calculate_output_policy_checksum,
    canonicalize_output_policies,
)
from resource_registry.transformations.runs.serialization import (
    serialize_transformation_execution_plan,
)
from resource_registry.utils.json_checksum import canonicalize_transformation_specification

_ACTIVE_STATUSES = (
    TransformationRunStatus.QUEUED,
    TransformationRunStatus.RUNNING,
    TransformationRunStatus.OUTPUT_PENDING,
)
_MAX_CALLER_IDENTIFIER_LENGTH = 128


class TransformationRunCreationService:
    """Persist complete execution plans as tenant-safe idempotent transformation runs."""

    def create_run(
        self,
        *,
        request: TransformationRunCreationRequest,
    ) -> TransformationRunCreationResult:
        """Create or idempotently resolve one durable transformation run.

        Args:
            request: Immutable explicit identities, complete plan, and optional caller metadata.

        Returns:
            Immutable identifiers and creation outcome for the resolved run.

        Raises:
            InvalidTransformationRunCreationRequestError: If explicit identities are malformed.
            InvalidTransformationRunIdempotencyKeyError: If a caller key is invalid.
            TransformationRunPlanIdentityMismatchError: If request and plan identities differ.
            TransformationRunDefinitionNotFoundError: If the tenant-scoped definition is absent.
            TransformationRunDefinitionVersionNotFoundError: If the exact child version is absent.
            ArchivedTransformationRunDefinitionError: If the definition is archived.
            IneligibleTransformationRunDefinitionVersionError: If the version is not published.
            TransformationRunSpecificationChecksumMismatchError: If persisted checksum differs.
            TransformationRunIdempotencyConflictError: If a caller key has different semantics.
        """
        tenant_id = _parse_uuid(request.tenant_id, field_name="tenant ID")
        definition_id = _parse_uuid(request.definition_id, field_name="definition ID")
        definition_version_id = _parse_uuid(
            request.definition_version_id,
            field_name="definition version ID",
        )
        idempotency_key = _validate_idempotency_key(request.idempotency_key)
        plan = request.execution_plan
        _validate_plan_identity(
            tenant_id=tenant_id,
            definition_id=definition_id,
            definition_version_id=definition_version_id,
            plan_tenant_id=plan.tenant_id,
            plan_definition_id=plan.definition_id,
            plan_definition_version_id=plan.definition_version_id,
        )
        plan_snapshot = serialize_transformation_execution_plan(plan=plan)
        authorization_decision = _validate_authorization_decision(
            request.authorization_decision,
            tenant_id=tenant_id,
            definition_id=definition_id,
            definition_version_id=definition_version_id,
        )
        policy_snapshots = canonicalize_output_policies(
            outputs=plan.outputs,
            policies=request.output_policies,
        )
        policy_fingerprint_snapshots = tuple(
            {
                **policy_snapshot,
                "output_policy_checksum": calculate_output_policy_checksum(policy_snapshot),
            }
            for policy_snapshot in policy_snapshots
        )
        request_fingerprint = build_transformation_run_request_fingerprint(
            tenant_id=str(tenant_id),
            definition_id=str(definition_id),
            definition_version_id=str(definition_version_id),
            specification_checksum=plan.specification_checksum,
            execution_plan_checksum=plan.plan_checksum,
            output_policies=policy_fingerprint_snapshots,
            authorization_checksum=authorization_decision.checksum,
        )
        run_id = uuid.uuid4()
        correlation_id = _validate_correlation_id(
            request.correlation_id,
            default_value=str(run_id),
        )

        try:
            with transaction.atomic():
                definition = self._lock_definition(
                    tenant_id=tenant_id,
                    definition_id=definition_id,
                )
                version = self._lock_definition_version(
                    definition=definition,
                    definition_version_id=definition_version_id,
                )
                self._validate_lifecycle_and_checksum(
                    definition=definition,
                    version=version,
                    specification_checksum=plan.specification_checksum,
                )

                if idempotency_key is not None:
                    keyed_run = self._find_by_idempotency_key(
                        tenant_id=tenant_id,
                        idempotency_key=idempotency_key,
                        lock=True,
                    )
                    if keyed_run is not None:
                        return self._resolve_keyed_run(
                            run=keyed_run,
                            request_fingerprint=request_fingerprint,
                        )

                active_run = self._find_active_by_fingerprint(
                    tenant_id=tenant_id,
                    request_fingerprint=request_fingerprint,
                    lock=True,
                )
                if active_run is not None:
                    return self._build_result(run=active_run, was_created=False)

                lock_referenced_datasets(plan_snapshot)
                lock_referenced_datasets(policy_snapshots)
                run = TransformationRun.objects.create(
                    id=run_id,
                    tenant_id=tenant_id,
                    definition=definition,
                    definition_version=version,
                    specification_checksum=plan.specification_checksum,
                    plan_contract_version=plan.contract_version,
                    dsl_version=plan.dsl_version,
                    execution_plan_snapshot_json=plan_snapshot,
                    execution_plan_checksum=plan.plan_checksum,
                    authorization_contract_version=authorization_decision.contract_version,
                    authorization_snapshot_json=thaw_json_value(
                        authorization_decision.snapshot,
                    ),
                    authorization_checksum=authorization_decision.checksum,
                    authorization_policy_revision=(
                        authorization_decision.authorization_policy_revision
                    ),
                    governance_policy_revision=(authorization_decision.governance_policy_revision),
                    authorized_actor=authorization_decision.authorized_actor,
                    authorized_at=timezone.now(),
                    request_fingerprint=request_fingerprint,
                    idempotency_key=idempotency_key,
                    status=TransformationRunStatus.QUEUED,
                    latest_attempt_number=0,
                    retry_count=0,
                    created_by_id=request.created_by_id,
                    correlation_id=correlation_id,
                )
                target_datasets = self._resolve_target_datasets(
                    tenant_id=tenant_id,
                    policy_snapshots=policy_snapshots,
                )
                TransformationRunOutput.objects.bulk_create(
                    [
                        self._build_output_row(
                            run=run,
                            tenant_id=tenant_id,
                            policy_snapshot=policy_snapshots[output_index],
                            target_datasets=target_datasets,
                            output_index=output_index,
                            output=output,
                        )
                        for output_index, output in enumerate(plan.outputs)
                    ],
                )
                return self._build_result(run=run, was_created=True)
        except IntegrityError as error:
            return self._resolve_concurrent_creation(
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                integrity_error=error,
            )

    def _resolve_target_datasets(
        self,
        *,
        tenant_id: UUID,
        policy_snapshots: tuple[dict[str, object], ...],
    ) -> dict[str, Dataset]:
        """Lock exact tenant-scoped target datasets required by output policies."""
        target_ids = {
            str(snapshot["target_dataset_id"])
            for snapshot in policy_snapshots
            if snapshot["target_dataset_id"] is not None
        }
        if not target_ids:
            return {}
        datasets = {
            str(dataset.id): dataset
            for dataset in Dataset.objects.select_for_update().filter(
                tenant_id=tenant_id,
                id__in=target_ids,
            )
        }
        if set(datasets) != target_ids:
            raise InvalidTransformationRunCreationRequestError(
                "Output policy target dataset was not found in the run tenant.",
            )
        return datasets

    def _build_output_row(
        self,
        *,
        run: TransformationRun,
        tenant_id: UUID,
        policy_snapshot: dict[str, object],
        target_datasets: dict[str, Dataset],
        output_index: int,
        output: ExecutionPlanOutput,
    ) -> TransformationRunOutput:
        """Build one policy-bound output row without persisting it."""
        logical_name = output.logical_name
        data_format = output.data_format
        policy_kind = str(policy_snapshot["policy_kind"])
        target_dataset_id = policy_snapshot["target_dataset_id"]
        output_kind = (
            TransformationRunOutputKind.STANDALONE
            if policy_kind == TransformationRunOutputPolicyKind.STANDALONE
            else TransformationRunOutputKind.DATASET_BACKED
        )
        return TransformationRunOutput(
            run=run,
            output_index=output_index,
            logical_name=logical_name,
            source_symbol=output.source_symbol,
            producer_step_id=output.producer_step_id,
            producer_step_index=output.producer_step_index,
            producer_output_index=output.producer_output_index,
            data_format=data_format,
            declaration_metadata_json=output.to_dict()["metadata"],
            status=TransformationRunOutputStatus.PENDING,
            output_kind=output_kind,
            output_policy_kind=policy_kind,
            output_policy_json=policy_snapshot,
            output_policy_checksum=calculate_output_policy_checksum(policy_snapshot),
            output_policy_contract_version=str(policy_snapshot["contract_version"]),
            destination_storage_provider=str(
                policy_snapshot["destination_storage_provider"],
            ),
            destination_object_key=build_output_destination_key(
                tenant_id=tenant_id,
                run_id=run.id,
                output_index=output_index,
                logical_name=logical_name,
                data_format=data_format,
            ),
            target_dataset=(
                target_datasets[str(target_dataset_id)] if target_dataset_id is not None else None
            ),
        )

    def _lock_definition(
        self,
        *,
        tenant_id: UUID,
        definition_id: UUID,
    ) -> TransformationDefinition:
        """Lock one definition through its tenant boundary without existence disclosure."""
        try:
            return TransformationDefinition.objects.select_for_update().get(
                tenant_id=tenant_id,
                id=definition_id,
            )
        except TransformationDefinition.DoesNotExist as error:
            raise TransformationRunDefinitionNotFoundError(
                "Transformation definition not found.",
            ) from error

    def _lock_definition_version(
        self,
        *,
        definition: TransformationDefinition,
        definition_version_id: UUID,
    ) -> TransformationDefinitionVersion:
        """Lock one exact version through its already tenant-scoped parent."""
        try:
            return TransformationDefinitionVersion.objects.select_for_update().get(
                definition=definition,
                id=definition_version_id,
            )
        except TransformationDefinitionVersion.DoesNotExist as error:
            raise TransformationRunDefinitionVersionNotFoundError(
                "Transformation definition version not found.",
            ) from error

    def _validate_lifecycle_and_checksum(
        self,
        *,
        definition: TransformationDefinition,
        version: TransformationDefinitionVersion,
        specification_checksum: str,
    ) -> None:
        """Recheck creation eligibility under definition and version row locks."""
        if definition.archived_at is not None:
            raise ArchivedTransformationRunDefinitionError(
                "Archived transformation definitions cannot start runs.",
            )
        if version.status != TransformationDefinitionVersionStatus.PUBLISHED:
            raise IneligibleTransformationRunDefinitionVersionError(
                "Only a published transformation definition version can start a run.",
            )
        if version.specification_checksum != specification_checksum:
            raise TransformationRunSpecificationChecksumMismatchError(
                "Persisted specification checksum does not match the execution plan.",
            )

    def _find_by_idempotency_key(
        self,
        *,
        tenant_id: UUID,
        idempotency_key: str,
        lock: bool,
    ) -> TransformationRun | None:
        """Find one permanently associated caller key inside its tenant scope."""
        queryset = TransformationRun.objects.filter(
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
        )
        if lock:
            queryset = queryset.select_for_update()
        return queryset.first()

    def _find_active_by_fingerprint(
        self,
        *,
        tenant_id: UUID,
        request_fingerprint: str,
        lock: bool,
    ) -> TransformationRun | None:
        """Find an execution-equivalent active run in one tenant."""
        queryset = TransformationRun.objects.filter(
            tenant_id=tenant_id,
            request_fingerprint=request_fingerprint,
            status__in=_ACTIVE_STATUSES,
        )
        if lock:
            queryset = queryset.select_for_update()
        return queryset.first()

    def _resolve_keyed_run(
        self,
        *,
        run: TransformationRun,
        request_fingerprint: str,
    ) -> TransformationRunCreationResult:
        """Return a matching permanent key or reject semantic key reuse."""
        if run.request_fingerprint != request_fingerprint:
            raise TransformationRunIdempotencyConflictError(
                "Idempotency key is already associated with a different run request.",
            )
        return self._build_result(run=run, was_created=False)

    def _resolve_concurrent_creation(
        self,
        *,
        tenant_id: UUID,
        idempotency_key: str | None,
        request_fingerprint: str,
        integrity_error: IntegrityError,
    ) -> TransformationRunCreationResult:
        """Resolve a database-enforced duplicate after the failed transaction is closed."""
        if idempotency_key is not None:
            keyed_run = self._find_by_idempotency_key(
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                lock=False,
            )
            if keyed_run is not None:
                return self._resolve_keyed_run(
                    run=keyed_run,
                    request_fingerprint=request_fingerprint,
                )
        active_run = self._find_active_by_fingerprint(
            tenant_id=tenant_id,
            request_fingerprint=request_fingerprint,
            lock=False,
        )
        if active_run is not None:
            return self._build_result(run=active_run, was_created=False)
        raise integrity_error

    def _build_result(
        self,
        *,
        run: TransformationRun,
        was_created: bool,
    ) -> TransformationRunCreationResult:
        """Build the immutable service result without exposing a mutable ORM model."""
        return TransformationRunCreationResult(
            run_id=run.id,
            tenant_id=run.tenant_id,
            status=run.status,
            definition_id=run.definition_id,
            definition_version_id=run.definition_version_id,
            specification_checksum=run.specification_checksum,
            execution_plan_checksum=run.execution_plan_checksum,
            request_fingerprint=run.request_fingerprint,
            idempotency_key=run.idempotency_key,
            was_created=was_created,
            output_slot_count=run.outputs.count(),
        )


def _parse_uuid(value: UUID | str, *, field_name: str) -> UUID:
    """Parse one required explicit UUID without normalization beyond UUID syntax."""
    try:
        return value if isinstance(value, UUID) else UUID(value)
    except (TypeError, ValueError, AttributeError) as error:
        raise InvalidTransformationRunCreationRequestError(
            f"Explicit {field_name} must be a valid UUID.",
        ) from error


def _validate_authorization_decision(
    decision: TransformationAuthorizationDecision | None,
    *,
    tenant_id: UUID,
    definition_id: UUID,
    definition_version_id: UUID,
) -> TransformationAuthorizationDecision:
    """Validate the complete immutable authorization decision before persistence."""
    if decision is None:
        raise InvalidTransformationRunCreationRequestError(
            "Explicit transformation authorization decision is required.",
        )
    snapshot = decision.snapshot
    mutable_snapshot = thaw_json_value(snapshot)
    if not isinstance(mutable_snapshot, dict):
        raise InvalidTransformationRunCreationRequestError(
            "Transformation authorization snapshot must be a JSON object.",
        )
    calculated = hashlib.sha256(
        canonicalize_transformation_specification(mutable_snapshot).encode("utf-8"),
    ).hexdigest()
    if calculated != decision.checksum:
        raise InvalidTransformationRunCreationRequestError(
            "Transformation authorization checksum is invalid.",
        )
    expected_identity = {
        "tenant_id": str(tenant_id),
        "definition_id": str(definition_id),
        "definition_version_id": str(definition_version_id),
        "authorized_actor": decision.authorized_actor,
    }
    if any(snapshot.get(key) != value for key, value in expected_identity.items()):
        raise InvalidTransformationRunCreationRequestError(
            "Transformation authorization identity does not match the run request.",
        )
    if (
        not decision.authorized_actor
        or decision.authorized_actor != decision.authorized_actor.strip()
    ):
        raise InvalidTransformationRunCreationRequestError(
            "Transformation authorized actor must be an exact non-empty string.",
        )
    return decision


def _validate_plan_identity(
    *,
    tenant_id: UUID,
    definition_id: UUID,
    definition_version_id: UUID,
    plan_tenant_id: str,
    plan_definition_id: str,
    plan_definition_version_id: str,
) -> None:
    """Require request and plan identities to match exactly after UUID parsing."""
    try:
        matches = (
            tenant_id == UUID(plan_tenant_id)
            and definition_id == UUID(plan_definition_id)
            and definition_version_id == UUID(plan_definition_version_id)
        )
    except (TypeError, ValueError, AttributeError) as error:
        raise TransformationRunPlanIdentityMismatchError(
            "Execution plan contains malformed persistence identities.",
        ) from error
    if not matches:
        raise TransformationRunPlanIdentityMismatchError(
            "Creation request and execution plan identities do not match.",
        )


def _validate_idempotency_key(value: str | None) -> str | None:
    """Validate an optional caller key without trimming or normalization."""
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > _MAX_CALLER_IDENTIFIER_LENGTH
    ):
        raise InvalidTransformationRunIdempotencyKeyError(
            "Idempotency key must be a non-whitespace string of at most 128 characters.",
        )
    return value


def _validate_correlation_id(value: str | None, *, default_value: str) -> str:
    """Validate safe bounded correlation identity or use the preallocated run UUID."""
    if value is None:
        return default_value
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > _MAX_CALLER_IDENTIFIER_LENGTH
    ):
        raise InvalidTransformationRunCreationRequestError(
            "Correlation ID must be a non-whitespace string of at most 128 characters.",
        )
    return value
