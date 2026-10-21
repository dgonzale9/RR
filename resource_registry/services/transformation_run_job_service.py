from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import replace
from datetime import timedelta
from threading import Event
from typing import cast
from uuid import UUID

from django.db import DatabaseError, transaction
from django.db.models import Q
from django.utils import timezone

from resource_registry.events.audit_service import AuditService
from resource_registry.events.event_types import EventType
from resource_registry.models import (
    TransformationRun,
    TransformationRunStatus,
    TransformationRunStepAttempt,
    TransformationRunStepAttemptStatus,
    TransformationRunStepSideEffectDisposition,
)
from resource_registry.services.async_job_execution_timeout import (
    normalize_execution_timeout_seconds,
)
from resource_registry.services.payload_sanitizer import sanitize_payload
from resource_registry.services.transformation_run_worker_contracts import (
    ClaimedTransformationRun,
    TransformationRunAttemptClaimMetadata,
    TransformationRunLifecycleAction,
    TransformationRunLifecycleResult,
    TransformationRunQueueMetricsSnapshot,
)
from resource_registry.services.transformation_run_worker_runtime import (
    DurableTransformationRunCancellationSignal,
)
from resource_registry.transformations.execution import (
    TransformationRunAttemptExecutionOutcome,
    TransformationRunAttemptExecutionRequest,
    TransformationRunAttemptExecutionResult,
)
from resource_registry.transformations.execution.attempt_persistence import (
    TransformationRunAttemptPersistenceService,
)
from resource_registry.transformations.execution.checksum import (
    CHECKPOINT_CONTRACT_VERSION,
    calculate_complete_checkpoint_checksum,
)
from resource_registry.transformations.execution.exceptions import (
    InvalidPersistedAuthorizationSnapshotError,
    InvalidPersistedExecutionPlanError,
    PersistedExecutionPlanIdentityMismatchError,
    UnsupportedPersistedAuthorizationSnapshotError,
    UnsupportedPersistedExecutionPlanError,
)
from resource_registry.transformations.execution.ports import (
    TransformationCheckpointReferenceVerifier,
)
from resource_registry.transformations.execution.runtime import (
    DefaultTransformationProviderRuntimeContext,
    DenyingTransformationRuntimeSecretAccess,
    MonotonicTransformationDeadlineSignal,
)
from resource_registry.transformations.execution_plan import (
    TransformationExecutionPlan,
    TransformationExecutionPlanStep,
    deserialize_transformation_execution_plan,
)
from resource_registry.transformations.providers import ProviderSideEffectDisposition

_CLAIM_FAILURES = (
    InvalidPersistedAuthorizationSnapshotError,
    InvalidPersistedExecutionPlanError,
    PersistedExecutionPlanIdentityMismatchError,
    UnsupportedPersistedExecutionPlanError,
    UnsupportedPersistedAuthorizationSnapshotError,
)
_MAX_WORKER_ID_LENGTH = 128


class _InitializationCancellationSignal:
    def is_cancellation_requested(self) -> bool:
        return False


class TransformationRunJobService:
    """Own durable Story 6 queue, claim, retry, handoff, and recovery transitions."""

    def __init__(
        self,
        *,
        attempt_persistence: TransformationRunAttemptPersistenceService,
        max_retries: int,
        audit_service: AuditService,
    ) -> None:
        self._attempt_persistence = attempt_persistence
        self._max_retries = max(int(max_retries), 0)
        self._audit_service = audit_service

    def claim_next_run(
        self,
        *,
        worker_id: str,
        checkpoint_reference_verifier: TransformationCheckpointReferenceVerifier,
        lease_seconds: int,
        execution_timeout_seconds: float | int | None,
        tenant_id: str | None = None,
    ) -> ClaimedTransformationRun | None:
        """Atomically select, claim, and initialize one deterministic queued run."""
        normalized_worker_id = _validate_worker_id(worker_id)
        effective_lease_seconds = max(int(lease_seconds), 1)
        timeout_seconds = normalize_execution_timeout_seconds(execution_timeout_seconds)
        claimed_at = timezone.now()
        deadline_at = (
            claimed_at + timedelta(seconds=timeout_seconds) if timeout_seconds is not None else None
        )
        monotonic_deadline = (
            time.monotonic() + timeout_seconds if timeout_seconds is not None else None
        )
        with transaction.atomic():
            queryset = TransformationRun.objects.filter(
                status=TransformationRunStatus.QUEUED,
                cancellation_requested_at__isnull=True,
                claim_owner__isnull=True,
                retry_count__lte=self._max_retries,
            )
            if tenant_id is not None:
                queryset = queryset.filter(tenant_id=tenant_id)
            queryset = queryset.order_by("created_at", "id")
            try:
                run = queryset.select_for_update(skip_locked=True).first()
            except DatabaseError:
                run = queryset.select_for_update().first()
            if run is None:
                return None
            claim = TransformationRunAttemptClaimMetadata(
                worker_id=normalized_worker_id,
                claimed_at=claimed_at,
                lease_expires_at=claimed_at + timedelta(seconds=effective_lease_seconds),
                attempt_deadline_at=deadline_at,
            )
            initialization_context = DefaultTransformationProviderRuntimeContext(
                cancellation_signal=_InitializationCancellationSignal(),
                deadline_signal=MonotonicTransformationDeadlineSignal(monotonic_deadline),
                secret_access=DenyingTransformationRuntimeSecretAccess(),
            )
            try:
                initialized = self._attempt_persistence.initialize_attempt(
                    tenant_id=str(run.tenant_id),
                    run_id=str(run.id),
                    runtime_context=initialization_context,
                    checkpoint_reference_verifier=checkpoint_reference_verifier,
                    claim=claim,
                    correlation_id=run.correlation_id,
                )
            except _CLAIM_FAILURES as error:
                self._terminalize_invalid_persisted_authority(run=run, error=error)
                return None
            if not initialized.is_initialized or initialized.execution_request is None:
                return None
            lease_lost_event = Event()
            execution_request = initialized.execution_request
            runtime_context = DefaultTransformationProviderRuntimeContext(
                cancellation_signal=DurableTransformationRunCancellationSignal(
                    tenant_id=execution_request.tenant_id,
                    run_id=execution_request.run_id,
                    attempt_number=execution_request.attempt_number,
                    attempt_token=execution_request.attempt_token,
                    lease_lost_event=lease_lost_event,
                ),
                deadline_signal=MonotonicTransformationDeadlineSignal(monotonic_deadline),
                secret_access=DenyingTransformationRuntimeSecretAccess(),
            )
            execution_request = replace(execution_request, runtime_context=runtime_context)
            self._audit_run(
                run=run,
                event_type=EventType.RUN_STARTED,
                transition="QUEUED_TO_RUNNING",
                attempt_number=execution_request.attempt_number,
                metadata={"worker_id": normalized_worker_id},
            )
            return ClaimedTransformationRun(
                worker_id=normalized_worker_id,
                execution_request=execution_request,
                lease_lost_event=lease_lost_event,
            )

    def renew_lease(self, *, claimed: ClaimedTransformationRun, lease_seconds: int) -> bool:
        """Conditionally renew only the matching live claim fence."""
        now = timezone.now()
        request = claimed.execution_request
        updated = (
            TransformationRun.objects.filter(
                tenant_id=request.tenant_id,
                id=request.run_id,
                status=TransformationRunStatus.RUNNING,
                latest_attempt_number=request.attempt_number,
                current_attempt_token=request.attempt_token,
                claim_owner=claimed.worker_id,
                cancellation_requested_at__isnull=True,
            )
            .filter(
                Q(attempt_deadline_at__isnull=True) | Q(attempt_deadline_at__gt=now),
            )
            .update(
                heartbeat_at=now,
                lease_expires_at=now + timedelta(seconds=max(int(lease_seconds), 1)),
                updated_at=now,
            )
        )
        return updated == 1

    def apply_attempt_result(  # noqa: PLR0911
        self,
        *,
        claimed: ClaimedTransformationRun,
        result: TransformationRunAttemptExecutionResult,
    ) -> TransformationRunLifecycleResult:
        """Apply one fenced lifecycle decision from a Story 5 attempt result."""
        request = claimed.execution_request
        with transaction.atomic():
            run = (
                TransformationRun.objects.select_for_update()
                .filter(
                    tenant_id=request.tenant_id,
                    id=request.run_id,
                )
                .first()
            )
            if run is None:
                return _lifecycle_result(
                    request=request,
                    action=TransformationRunLifecycleAction.STALE,
                    retry_count=0,
                    error_code="run_not_found",
                )
            if run.status == TransformationRunStatus.CANCELLED:
                return _lifecycle_result(
                    request=request,
                    action=TransformationRunLifecycleAction.CANCELLED,
                    retry_count=run.retry_count,
                )
            if not _has_matching_claim(run=run, claimed=claimed):
                return _lifecycle_result(
                    request=request,
                    action=TransformationRunLifecycleAction.STALE,
                    retry_count=run.retry_count,
                    error_code="stale_attempt",
                )
            if run.cancellation_requested_at is not None or result.outcome == (
                TransformationRunAttemptExecutionOutcome.CANCELLED
            ):
                self._cancel_locked_run(run=run)
                return _lifecycle_result(
                    request=request,
                    action=TransformationRunLifecycleAction.CANCELLED,
                    retry_count=run.retry_count,
                )
            if result.outcome == TransformationRunAttemptExecutionOutcome.STALE:
                return _lifecycle_result(
                    request=request,
                    action=TransformationRunLifecycleAction.STALE,
                    retry_count=run.retry_count,
                    error_code="stale_attempt",
                )
            if result.outcome == TransformationRunAttemptExecutionOutcome.SUCCEEDED:
                if not self._has_complete_successful_attempt(run=run):
                    self._fail_locked_run(
                        run=run,
                        error_code="attempt_history_invalid",
                        error_message="Successful attempt history is incomplete.",
                        execution_ms=result.execution_ms,
                    )
                    return _lifecycle_result(
                        request=request,
                        action=TransformationRunLifecycleAction.FAILED,
                        retry_count=run.retry_count,
                        error_code="attempt_history_invalid",
                    )
                self._handoff_locked_run(run=run, execution_ms=result.execution_ms)
                return _lifecycle_result(
                    request=request,
                    action=TransformationRunLifecycleAction.OUTPUT_PENDING,
                    retry_count=run.retry_count,
                )
            failure = result.failure
            error_code = failure.code if failure is not None else "attempt_failed"
            error_message = (
                failure.message if failure is not None else "Transformation attempt failed."
            )
            if not self._has_durable_failed_attempt(run=run, result=result):
                self._fail_locked_run(
                    run=run,
                    error_code="attempt_history_invalid",
                    error_message="Failed attempt history is incomplete.",
                    execution_ms=result.execution_ms,
                )
                return _lifecycle_result(
                    request=request,
                    action=TransformationRunLifecycleAction.FAILED,
                    retry_count=run.retry_count,
                    error_code="attempt_history_invalid",
                )
            retryable = result.retryable and self._is_result_retry_safe(
                run=run,
                result=result,
            )
            if retryable and run.retry_count < self._max_retries:
                self._requeue_locked_run(
                    run=run,
                    error_code=error_code,
                    error_message=error_message,
                    execution_ms=result.execution_ms,
                )
                return _lifecycle_result(
                    request=request,
                    action=TransformationRunLifecycleAction.REQUEUED,
                    retry_count=run.retry_count,
                    error_code=error_code,
                )
            terminal_code = (
                "retry_exhausted"
                if retryable and run.retry_count >= self._max_retries
                else error_code
            )
            self._fail_locked_run(
                run=run,
                error_code=terminal_code,
                error_message=error_message,
                execution_ms=result.execution_ms,
            )
            return _lifecycle_result(
                request=request,
                action=TransformationRunLifecycleAction.FAILED,
                retry_count=run.retry_count,
                error_code=terminal_code,
            )

    def request_cancellation(self, *, tenant_id: str, run_id: str) -> TransformationRun:
        """Persist tenant-scoped cancellation intent without adding a public API."""
        now = timezone.now()
        with transaction.atomic():
            run = TransformationRun.objects.select_for_update().get(
                tenant_id=tenant_id,
                id=run_id,
            )
            if run.status in (
                TransformationRunStatus.OUTPUT_PENDING,
                TransformationRunStatus.SUCCEEDED,
                TransformationRunStatus.FAILED,
                TransformationRunStatus.CANCELLED,
            ):
                return run
            if run.cancellation_requested_at is None:
                run.cancellation_requested_at = now
            if run.status == TransformationRunStatus.QUEUED:
                run.status = TransformationRunStatus.CANCELLED
                run.completed_at = now
                _clear_claim(run)
            run.save()
            if run.status == TransformationRunStatus.CANCELLED:
                self._audit_run(
                    run=run,
                    event_type=EventType.RUN_CANCELLED,
                    transition="QUEUED_TO_CANCELLED",
                )
            return run

    def cancel_requested_queued_runs(self, *, tenant_id: str | None = None) -> int:
        """Terminalize queued cancellation requests before queue selection."""
        now = timezone.now()
        queryset = TransformationRun.objects.filter(
            status=TransformationRunStatus.QUEUED,
            cancellation_requested_at__isnull=False,
        )
        if tenant_id is not None:
            queryset = queryset.filter(tenant_id=tenant_id)
        return queryset.update(status=TransformationRunStatus.CANCELLED, completed_at=now)

    def get_queue_metrics(
        self,
        *,
        tenant_id: str | None = None,
    ) -> TransformationRunQueueMetricsSnapshot:
        """Return safe queue gauges for the configured metrics adapter."""
        queryset = TransformationRun.objects.all()
        if tenant_id is not None:
            queryset = queryset.filter(tenant_id=tenant_id)
        oldest = (
            queryset.filter(status=TransformationRunStatus.QUEUED).order_by("created_at").first()
        )
        oldest_age = (
            max((timezone.now() - oldest.created_at).total_seconds(), 0.0)
            if oldest is not None
            else 0.0
        )
        return TransformationRunQueueMetricsSnapshot(
            queued_count=queryset.filter(status=TransformationRunStatus.QUEUED).count(),
            running_count=queryset.filter(status=TransformationRunStatus.RUNNING).count(),
            output_pending_count=queryset.filter(
                status=TransformationRunStatus.OUTPUT_PENDING,
            ).count(),
            oldest_queued_age_seconds=oldest_age,
        )

    def recover_expired_runs(
        self,
        *,
        batch_size: int,
        tenant_id: str | None = None,
    ) -> tuple[TransformationRunLifecycleResult, ...]:
        """Recover expired claims deterministically without provider execution."""
        now = timezone.now()
        with transaction.atomic():
            queryset = TransformationRun.objects.filter(
                status=TransformationRunStatus.RUNNING,
                lease_expires_at__lt=now,
            )
            if tenant_id is not None:
                queryset = queryset.filter(tenant_id=tenant_id)
            queryset = queryset.order_by("lease_expires_at", "created_at", "id")
            try:
                runs = tuple(
                    queryset.select_for_update(skip_locked=True)[: max(int(batch_size), 1)],
                )
            except DatabaseError:
                runs = tuple(queryset.select_for_update()[: max(int(batch_size), 1)])
            results = tuple(self._recover_locked_run(run=run) for run in runs)
            for run, result in zip(runs, results, strict=True):
                self._audit_run(
                    run=run,
                    event_type=EventType.RECOVERY_EXECUTED,
                    transition=f"RECOVERY_TO_{result.action.value}",
                    attempt_number=result.attempt_number,
                    metadata={
                        "outcome": result.action.value,
                        "retry_count": result.retry_count,
                        "error_code": result.error_code or "",
                    },
                )
            return results

    def _recover_locked_run(  # noqa: PLR0911
        self,
        *,
        run: TransformationRun,
    ) -> TransformationRunLifecycleResult:
        request_stub = _RequestIdentity(run=run)
        if run.cancellation_requested_at is not None:
            self._cancel_locked_run(run=run, recover_steps=True)
            return request_stub.result(TransformationRunLifecycleAction.CANCELLED)
        try:
            plan = deserialize_transformation_execution_plan(
                cast(Mapping[str, object], run.execution_plan_snapshot_json),
            )
        except ValueError:
            self._fail_locked_run(
                run=run,
                error_code="invalid_plan_snapshot",
                error_message="Persisted execution plan failed recovery validation.",
            )
            return request_stub.result(
                TransformationRunLifecycleAction.FAILED,
                error_code="invalid_plan_snapshot",
            )
        if not _plan_matches_run(plan=plan, run=run):
            self._fail_locked_run(
                run=run,
                error_code="plan_identity_mismatch",
                error_message="Persisted execution plan identity does not match the run.",
            )
            return request_stub.result(
                TransformationRunLifecycleAction.FAILED,
                error_code="plan_identity_mismatch",
            )
        attempts = tuple(
            TransformationRunStepAttempt.objects.select_for_update()
            .filter(
                run=run,
                run_attempt_number=run.latest_attempt_number,
                run_attempt_token=_current_attempt_token(run),
            )
            .order_by("step_index"),
        )
        if len(attempts) != len(plan.steps):
            self._fail_locked_run(
                run=run,
                error_code="attempt_history_invalid",
                error_message="Attempt history does not match the execution plan.",
            )
            return request_stub.result(
                TransformationRunLifecycleAction.FAILED,
                error_code="attempt_history_invalid",
            )
        if all(row.status == TransformationRunStepAttemptStatus.SUCCEEDED for row in attempts):
            if self._has_complete_successful_attempt(run=run, plan=plan, attempts=attempts):
                self._handoff_locked_run(run=run)
                return request_stub.result(TransformationRunLifecycleAction.OUTPUT_PENDING)
            self._fail_locked_run(
                run=run,
                error_code="checkpoint_integrity_failure",
                error_message="Successful attempt checkpoint integrity is incomplete.",
            )
            return request_stub.result(
                TransformationRunLifecycleAction.FAILED,
                error_code="checkpoint_integrity_failure",
            )
        failed = next(
            (row for row in attempts if row.status == TransformationRunStepAttemptStatus.FAILED),
            None,
        )
        if failed is not None:
            retryable = bool(failed.is_retryable) and self._is_step_retry_safe(
                plan=plan,
                step_attempt=failed,
            )
            return self._recover_retry_or_fail(
                run=run,
                request_stub=request_stub,
                retryable=retryable,
                error_code=failed.error_code or "attempt_failed",
                error_message=failed.error_message or "Recovered failed transformation attempt.",
            )
        running = next(
            (row for row in attempts if row.status == TransformationRunStepAttemptStatus.RUNNING),
            None,
        )
        retryable = True
        error_code = "worker_lease_expired"
        if running is not None and running.invocation_may_have_started:
            running.side_effect_disposition = TransformationRunStepSideEffectDisposition.UNKNOWN
            retryable = self._plan_step_is_idempotent(plan=plan, step_id=running.step_id)
            error_code = "side_effect_ambiguous" if not retryable else "worker_lease_expired"
        self._close_incomplete_attempt_rows(run=run, error_code=error_code)
        return self._recover_retry_or_fail(
            run=run,
            request_stub=request_stub,
            retryable=retryable,
            error_code=error_code,
            error_message="Recovered expired transformation worker claim.",
        )

    def _recover_retry_or_fail(
        self,
        *,
        run: TransformationRun,
        request_stub: _RequestIdentity,
        retryable: bool,
        error_code: str,
        error_message: str,
    ) -> TransformationRunLifecycleResult:
        if retryable and run.retry_count < self._max_retries:
            self._requeue_locked_run(
                run=run,
                error_code=error_code,
                error_message=error_message,
            )
            return request_stub.result(
                TransformationRunLifecycleAction.REQUEUED,
                error_code=error_code,
            )
        terminal_code = "retry_exhausted" if retryable else error_code
        self._fail_locked_run(
            run=run,
            error_code=terminal_code,
            error_message=error_message,
        )
        return request_stub.result(
            TransformationRunLifecycleAction.FAILED,
            error_code=terminal_code,
        )

    def _has_complete_successful_attempt(
        self,
        *,
        run: TransformationRun,
        plan: TransformationExecutionPlan | None = None,
        attempts: tuple[TransformationRunStepAttempt, ...] | None = None,
    ) -> bool:
        verified_plan = plan or deserialize_transformation_execution_plan(
            cast(Mapping[str, object], run.execution_plan_snapshot_json),
        )
        rows = attempts or tuple(
            TransformationRunStepAttempt.objects.filter(
                run=run,
                run_attempt_number=run.latest_attempt_number,
                run_attempt_token=_current_attempt_token(run),
            ).order_by("step_index"),
        )
        if len(rows) != len(verified_plan.steps):
            return False
        for row, step in zip(rows, verified_plan.steps, strict=True):
            if not self._has_valid_successful_checkpoint(
                run=run,
                row=row,
                step=step,
            ):
                return False
        output_slots = tuple(run.outputs.order_by("output_index"))
        if len(output_slots) != len(verified_plan.outputs):
            return False
        rows_by_step_id = {row.step_id: row for row in rows}
        return all(
            slot.output_index == output_index
            and slot.logical_name == output.logical_name
            and slot.source_symbol == output.source_symbol
            and slot.producer_step_id == output.producer_step_id
            and slot.producer_step_index == output.producer_step_index
            and slot.producer_output_index == output.producer_output_index
            and slot.data_format == output.data_format
            and slot.producing_step_attempt_id == rows_by_step_id[output.producer_step_id].id
            for output_index, (slot, output) in enumerate(
                zip(output_slots, verified_plan.outputs, strict=True),
            )
        )

    def _has_valid_successful_checkpoint(
        self,
        *,
        run: TransformationRun,
        row: TransformationRunStepAttempt,
        step: TransformationExecutionPlanStep,
    ) -> bool:
        if (
            row.status != TransformationRunStepAttemptStatus.SUCCEEDED
            or row.step_id != step.step_id
            or row.step_index != step.step_index
            or row.provider_key != step.provider_key
            or row.provider_version != step.provider_version
            or row.operation_key != step.operation_key
            or row.operation_version != step.operation_version
            or row.checkpoint_contract_version != CHECKPOINT_CONTRACT_VERSION
            or row.plan_checksum != run.execution_plan_checksum
            or row.capability_checksum != step.capabilities.capability_checksum
            or row.checkpoint_input_checksum is None
            or row.checkpoint_output_checksum is None
            or row.checkpoint_checksum is None
        ):
            return False
        expected = calculate_complete_checkpoint_checksum(
            run_id=str(run.id),
            run_attempt_number=row.run_attempt_number,
            run_attempt_token=str(row.run_attempt_token),
            plan_checksum=row.plan_checksum,
            step_id=row.step_id,
            step_index=row.step_index,
            provider_key=row.provider_key,
            provider_version=row.provider_version,
            operation_key=row.operation_key,
            operation_version=row.operation_version,
            capability_checksum=row.capability_checksum,
            input_snapshot=cast(list[Mapping[str, object]], row.checkpoint_inputs_json),
            input_checksum=row.checkpoint_input_checksum,
            output_snapshot=cast(list[Mapping[str, object]], row.checkpoint_outputs_json),
            output_checksum=row.checkpoint_output_checksum,
            attempt_idempotency_token=row.attempt_idempotency_token,
            side_effect_disposition=ProviderSideEffectDisposition(
                row.side_effect_disposition,
            ),
        )
        return expected == row.checkpoint_checksum

    def _is_result_retry_safe(
        self,
        *,
        run: TransformationRun,
        result: TransformationRunAttemptExecutionResult,
    ) -> bool:
        failure = result.failure
        if (
            failure is None
            or failure.side_effect_disposition == ProviderSideEffectDisposition.NOT_STARTED
        ):
            return True
        if failure.step_id is None:
            return False
        plan = deserialize_transformation_execution_plan(
            cast(Mapping[str, object], run.execution_plan_snapshot_json),
        )
        return self._plan_step_is_idempotent(plan=plan, step_id=failure.step_id)

    def _has_durable_failed_attempt(
        self,
        *,
        run: TransformationRun,
        result: TransformationRunAttemptExecutionResult,
    ) -> bool:
        failure = result.failure
        queryset = TransformationRunStepAttempt.objects.filter(
            run=run,
            run_attempt_number=result.attempt_number,
            run_attempt_token=result.attempt_token,
            status=TransformationRunStepAttemptStatus.FAILED,
        )
        if failure is not None and failure.step_id is not None:
            queryset = queryset.filter(step_id=failure.step_id)
        row = queryset.order_by("step_index").first()
        return (
            row is not None
            and row.error_code == (failure.code if failure is not None else row.error_code)
            and bool(row.is_retryable) == result.retryable
        )

    def _is_step_retry_safe(
        self,
        *,
        plan: TransformationExecutionPlan,
        step_attempt: TransformationRunStepAttempt,
    ) -> bool:
        if step_attempt.side_effect_disposition == (
            TransformationRunStepSideEffectDisposition.NOT_STARTED
        ):
            return True
        return self._plan_step_is_idempotent(plan=plan, step_id=step_attempt.step_id)

    def _plan_step_is_idempotent(
        self,
        *,
        plan: TransformationExecutionPlan,
        step_id: str,
    ) -> bool:
        return next(
            (step.capabilities.is_idempotent for step in plan.steps if step.step_id == step_id),
            False,
        )

    def _close_incomplete_attempt_rows(
        self,
        *,
        run: TransformationRun,
        error_code: str,
    ) -> None:
        now = timezone.now()
        rows = TransformationRunStepAttempt.objects.select_for_update().filter(
            run=run,
            run_attempt_number=run.latest_attempt_number,
            run_attempt_token=_current_attempt_token(run),
            status__in=[
                TransformationRunStepAttemptStatus.RUNNING,
                TransformationRunStepAttemptStatus.QUEUED,
            ],
        )
        for row in rows:
            if row.status == TransformationRunStepAttemptStatus.RUNNING:
                row.status = TransformationRunStepAttemptStatus.FAILED
                row.side_effect_disposition = (
                    TransformationRunStepSideEffectDisposition.UNKNOWN
                    if row.invocation_may_have_started
                    else TransformationRunStepSideEffectDisposition.NOT_STARTED
                )
                row.error_code = error_code
                row.error_classification = error_code
                row.error_message = "Transformation worker lease expired."
                row.is_retryable = row.side_effect_disposition == (
                    TransformationRunStepSideEffectDisposition.NOT_STARTED
                )
            else:
                row.status = TransformationRunStepAttemptStatus.CANCELLED
                row.side_effect_disposition = TransformationRunStepSideEffectDisposition.NOT_STARTED
            row.completed_at = now
            row.save()

    def _requeue_locked_run(
        self,
        *,
        run: TransformationRun,
        error_code: str,
        error_message: str,
        execution_ms: int | None = None,
    ) -> None:
        run.status = TransformationRunStatus.QUEUED
        run.retry_count += 1
        run.completed_at = None
        run.execution_ms = execution_ms
        run.error_code = error_code
        run.error_message = _sanitize_message(error_message)
        run.error_metadata_json = {}
        _clear_claim(run)
        run.save()
        self._audit_run(
            run=run,
            event_type=EventType.RETRY_SCHEDULED,
            transition="RUNNING_TO_QUEUED",
            attempt_number=run.latest_attempt_number,
            metadata={"retry_count": run.retry_count, "error_code": error_code},
        )

    def _fail_locked_run(
        self,
        *,
        run: TransformationRun,
        error_code: str,
        error_message: str,
        execution_ms: int | None = None,
    ) -> None:
        run.status = TransformationRunStatus.FAILED
        run.completed_at = timezone.now()
        run.execution_ms = execution_ms
        run.error_code = error_code
        run.error_message = _sanitize_message(error_message)
        run.error_metadata_json = {}
        _clear_claim(run)
        run.save()
        self._audit_run(
            run=run,
            event_type=EventType.RUN_FAILED,
            transition="ACTIVE_TO_FAILED",
            attempt_number=run.latest_attempt_number,
            metadata={"retry_count": run.retry_count, "error_code": error_code},
        )
        if error_code == "retry_exhausted":
            self._audit_run(
                run=run,
                event_type=EventType.RETRY_EXHAUSTED,
                transition="RUNNING_TO_FAILED",
                attempt_number=run.latest_attempt_number,
                metadata={"retry_count": run.retry_count},
            )

    def _handoff_locked_run(
        self,
        *,
        run: TransformationRun,
        execution_ms: int | None = None,
    ) -> None:
        run.status = TransformationRunStatus.OUTPUT_PENDING
        run.completed_at = None
        run.execution_ms = execution_ms
        run.error_code = None
        run.error_message = None
        run.error_metadata_json = {}
        _clear_claim(run)
        run.save()

    def _cancel_locked_run(
        self,
        *,
        run: TransformationRun,
        recover_steps: bool = False,
    ) -> None:
        if recover_steps:
            self._close_incomplete_attempt_rows(run=run, error_code="execution_cancelled")
        run.status = TransformationRunStatus.CANCELLED
        run.completed_at = timezone.now()
        run.error_code = None
        run.error_message = None
        run.error_metadata_json = {}
        _clear_claim(run)
        run.save()
        self._audit_run(
            run=run,
            event_type=EventType.RUN_CANCELLED,
            transition="ACTIVE_TO_CANCELLED",
            attempt_number=run.latest_attempt_number,
        )

    def _audit_run(
        self,
        *,
        run: TransformationRun,
        event_type: EventType,
        transition: str,
        attempt_number: int | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        try:
            self._audit_service.record_transformation_event(
                event_type=event_type,
                tenant_id=str(run.tenant_id),
                run_id=str(run.id),
                correlation_id=run.correlation_id,
                transition=transition,
                actor=run.authorized_actor,
                attempt_number=attempt_number,
                metadata=metadata,
            )
        except Exception:
            return

    def _terminalize_invalid_persisted_authority(
        self,
        *,
        run: TransformationRun,
        error: Exception,
    ) -> None:
        self._fail_locked_run(
            run=run,
            error_code=str(getattr(error, "code", "invalid_plan_snapshot")),
            error_message="Persisted execution authority is not eligible for worker execution.",
        )


class _RequestIdentity:
    def __init__(self, *, run: TransformationRun) -> None:
        self.run_id = str(run.id)
        self.attempt_number = run.latest_attempt_number
        self.retry_count = run.retry_count
        self.tenant_id = str(run.tenant_id)
        self.definition_id = str(run.definition_id)
        self.definition_version_id = str(run.definition_version_id)
        self.correlation_id = run.correlation_id

    def result(
        self,
        action: TransformationRunLifecycleAction,
        *,
        error_code: str | None = None,
    ) -> TransformationRunLifecycleResult:
        retry_count = self.retry_count + (
            1 if action == TransformationRunLifecycleAction.REQUEUED else 0
        )
        return TransformationRunLifecycleResult(
            run_id=self.run_id,
            attempt_number=self.attempt_number,
            action=action,
            retry_count=retry_count,
            error_code=error_code,
            tenant_id=self.tenant_id,
            definition_id=self.definition_id,
            definition_version_id=self.definition_version_id,
            correlation_id=self.correlation_id,
        )


def _has_matching_claim(*, run: TransformationRun, claimed: ClaimedTransformationRun) -> bool:
    request = claimed.execution_request
    return (
        run.status == TransformationRunStatus.RUNNING
        and run.latest_attempt_number == request.attempt_number
        and str(run.current_attempt_token) == request.attempt_token
        and run.claim_owner == claimed.worker_id
    )


def _plan_matches_run(*, plan: TransformationExecutionPlan, run: TransformationRun) -> bool:
    return (
        plan.tenant_id == str(run.tenant_id)
        and plan.definition_id == str(run.definition_id)
        and plan.definition_version_id == str(run.definition_version_id)
        and plan.dsl_version == run.dsl_version
        and plan.specification_checksum == run.specification_checksum
        and plan.contract_version == run.plan_contract_version
        and plan.plan_checksum == run.execution_plan_checksum
    )


def _clear_claim(run: TransformationRun) -> None:
    run.current_attempt_token = None
    run.claim_owner = None
    run.claimed_at = None
    run.heartbeat_at = None
    run.lease_expires_at = None
    run.attempt_deadline_at = None


def _validate_worker_id(value: str) -> str:
    if not value or value != value.strip() or len(value) > _MAX_WORKER_ID_LENGTH:
        raise ValueError("Worker ID must be an exact string of at most 128 characters.")
    return value


def _sanitize_message(value: str) -> str:
    sanitized = sanitize_payload({"message": value})
    if not isinstance(sanitized, dict):
        return "Transformation worker lifecycle failure."
    return str(sanitized.get("message") or "Transformation worker lifecycle failure.")


def _current_attempt_token(run: TransformationRun) -> UUID:
    if run.current_attempt_token is None:
        raise ValueError("Running transformation run has no current attempt token.")
    return run.current_attempt_token


def _lifecycle_result(
    *,
    request: TransformationRunAttemptExecutionRequest,
    action: TransformationRunLifecycleAction,
    retry_count: int,
    error_code: str | None = None,
) -> TransformationRunLifecycleResult:
    return TransformationRunLifecycleResult(
        run_id=request.run_id,
        attempt_number=request.attempt_number,
        action=action,
        retry_count=retry_count,
        error_code=error_code,
        tenant_id=request.tenant_id,
        definition_id=request.plan.definition_id,
        definition_version_id=request.plan.definition_version_id,
        correlation_id=request.correlation_id,
    )
