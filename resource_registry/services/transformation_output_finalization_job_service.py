from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from threading import Event

from django.db import DatabaseError, IntegrityError, transaction
from django.utils import timezone

from resource_registry.events.audit_service import AuditService
from resource_registry.events.event_types import EventType
from resource_registry.models import (
    TransformationRun,
    TransformationRunOutput,
    TransformationRunOutputKind,
    TransformationRunOutputStatus,
    TransformationRunStatus,
    TransformationWorkspaceLease,
    TransformationWorkspaceLeaseState,
)
from resource_registry.services.transformation_output_finalization_worker_contracts import (
    ClaimedTransformationOutputFinalization,
    TransformationOutputFinalizationLifecycleAction,
    TransformationOutputFinalizationLifecycleResult,
    TransformationOutputFinalizationQueueSnapshot,
)
from resource_registry.transformations.execution.authorization_validation import (
    validate_transformation_authorization_snapshot,
)
from resource_registry.transformations.execution.exceptions import (
    InvalidPersistedAuthorizationSnapshotError,
    UnsupportedPersistedAuthorizationSnapshotError,
)
from resource_registry.transformations.execution_plan import (
    TransformationExecutionPlanDeserializationError,
    UnsupportedTransformationExecutionPlanVersionError,
    deserialize_transformation_execution_plan,
)
from resource_registry.transformations.output_finalization import (
    TransformationOutputFinalizationResult,
    TransformationRunOutputFinalizationService,
)
from resource_registry.transformations.output_finalization.contracts import (
    TransformationOutputFinalizationClaim,
)


class TransformationOutputFinalizationJobService:
    """Own durable output-finalization queue, claim, retry, and recovery transitions."""

    def __init__(
        self,
        *,
        finalization_service: TransformationRunOutputFinalizationService,
        max_retries: int,
        per_tenant_concurrency: int,
        audit_service: AuditService,
    ) -> None:
        self._finalization_service = finalization_service
        self._max_retries = max(int(max_retries), 0)
        self._per_tenant_concurrency = max(int(per_tenant_concurrency), 1)
        self._audit_service = audit_service

    def claim_next_run(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
        timeout_seconds: float | int,
        tenant_id: str | None = None,
    ) -> ClaimedTransformationOutputFinalization | None:
        """Atomically claim one deterministic output-pending run."""
        normalized_worker_id = _validate_worker_id(worker_id)
        timeout = float(timeout_seconds)
        if timeout <= 0:
            raise ValueError("Finalization timeout must be positive.")
        now = timezone.now()
        deadline = now + timedelta(seconds=timeout)
        lease_expiry = min(
            now + timedelta(seconds=max(int(lease_seconds), 1)),
            deadline,
        )
        try:
            with transaction.atomic():
                queryset = TransformationRun.objects.filter(
                    status=TransformationRunStatus.OUTPUT_PENDING,
                    completed_at__isnull=True,
                    current_finalization_token__isnull=True,
                    finalization_retry_count__lte=self._max_retries,
                )
                if tenant_id is not None:
                    queryset = queryset.filter(tenant_id=tenant_id)
                run = (
                    queryset.order_by("created_at", "id")
                    .select_for_update(skip_locked=True)
                    .first()
                )
                if run is None:
                    return None
                if self._terminalize_invalid_authority(run=run, now=now) is not None:
                    return None
                outputs = tuple(run.outputs.select_for_update().order_by("output_index", "id"))
                if outputs and all(_is_terminal_output(output) for output in outputs):
                    self._finalization_service.complete_run_if_ready(
                        tenant_id=run.tenant_id,
                        run_id=run.id,
                    )
                    return None
                slot = self._allocate_tenant_slot(tenant_id=str(run.tenant_id), now=now)
                if slot is None:
                    return None
                token = uuid.uuid4()
                run.current_finalization_token = token
                run.finalization_owner = normalized_worker_id
                run.finalization_claimed_at = now
                run.finalization_heartbeat_at = now
                run.finalization_lease_expires_at = lease_expiry
                run.finalization_deadline_at = deadline
                run.finalization_attempt_number += 1
                run.finalization_tenant_slot = slot
                run.save()
                claim = TransformationOutputFinalizationClaim(
                    tenant_id=str(run.tenant_id),
                    run_id=str(run.id),
                    worker_id=normalized_worker_id,
                    token=str(token),
                    attempt_number=run.finalization_attempt_number,
                    claimed_at=now,
                    lease_expires_at=lease_expiry,
                    deadline_at=deadline,
                    correlation_id=run.correlation_id,
                )
                return ClaimedTransformationOutputFinalization(
                    claim=claim,
                    lease_lost_event=Event(),
                )
        except IntegrityError:
            return None

    def renew_lease(
        self,
        *,
        claimed: ClaimedTransformationOutputFinalization,
        lease_seconds: int,
    ) -> bool:
        """Renew the matching lease without extending its persisted deadline."""
        now = timezone.now()
        claim = claimed.claim
        candidate_expiry = now + timedelta(seconds=max(int(lease_seconds), 1))
        lease_expiry = min(candidate_expiry, claim.deadline_at)
        if lease_expiry <= now:
            return False
        updated = TransformationRun.objects.filter(
            tenant_id=claim.tenant_id,
            id=claim.run_id,
            status=TransformationRunStatus.OUTPUT_PENDING,
            current_finalization_token=claim.token,
            finalization_owner=claim.worker_id,
            finalization_attempt_number=claim.attempt_number,
            finalization_deadline_at__gt=now,
        ).update(
            finalization_heartbeat_at=now,
            finalization_lease_expires_at=lease_expiry,
            updated_at=now,
        )
        return updated == 1

    def apply_result(  # noqa: PLR0911
        self,
        *,
        claimed: ClaimedTransformationOutputFinalization,
        result: TransformationOutputFinalizationResult,
    ) -> TransformationOutputFinalizationLifecycleResult:
        """Apply one fenced retry, terminal, or stale lifecycle result."""
        claim = claimed.claim
        with transaction.atomic():
            run = TransformationRun.objects.select_for_update().filter(
                tenant_id=claim.tenant_id,
                id=claim.run_id,
            ).first()
            if run is None:
                return _result(claim, TransformationOutputFinalizationLifecycleAction.STALE, 0)
            if run.status == TransformationRunStatus.SUCCEEDED and result.status == run.status:
                return _result(
                    claim,
                    TransformationOutputFinalizationLifecycleAction.SUCCEEDED,
                    run.finalization_retry_count,
                )
            if run.status == TransformationRunStatus.FAILED and result.status == run.status:
                return _result(
                    claim,
                    TransformationOutputFinalizationLifecycleAction.FAILED,
                    run.finalization_retry_count,
                    result.error_code,
                )
            if not _has_matching_claim(run=run, claim=claim):
                return _result(
                    claim,
                    TransformationOutputFinalizationLifecycleAction.STALE,
                    run.finalization_retry_count,
                )
            if result.status == TransformationRunStatus.SUCCEEDED:
                return _result(
                    claim,
                    TransformationOutputFinalizationLifecycleAction.SUCCEEDED,
                    run.finalization_retry_count,
                )
            if result.status == TransformationRunStatus.FAILED or not result.is_retryable:
                self._finalization_service.fail_claimed_run(
                    claim=claim,
                    error_code=result.error_code or "output_finalization_failed",
                )
                return _result(
                    claim,
                    TransformationOutputFinalizationLifecycleAction.FAILED,
                    run.finalization_retry_count,
                    result.error_code,
                )
            if run.finalization_retry_count < self._max_retries:
                run.finalization_retry_count += 1
                _clear_claim(run)
                run.error_code = result.error_code
                run.error_message = "Transformation output finalization remains pending."
                run.save()
                self._audit(
                    run=run,
                    event_type=EventType.RETRY_SCHEDULED,
                    transition="OUTPUT_FINALIZATION_REQUEUED",
                    error_code=result.error_code or "",
                )
                return _result(
                    claim,
                    TransformationOutputFinalizationLifecycleAction.REQUEUED,
                    run.finalization_retry_count,
                    result.error_code,
                )
            self._finalization_service.fail_claimed_run(
                claim=claim,
                error_code="output_finalization_retry_exhausted",
            )
            self._audit(
                run=run,
                event_type=EventType.RETRY_EXHAUSTED,
                transition="OUTPUT_FINALIZATION_RETRY_EXHAUSTED",
                error_code="output_finalization_retry_exhausted",
            )
            return _result(
                claim,
                TransformationOutputFinalizationLifecycleAction.FAILED,
                run.finalization_retry_count,
                "output_finalization_retry_exhausted",
            )

    def recover_expired_runs(
        self,
        *,
        batch_size: int,
        tenant_id: str | None = None,
    ) -> tuple[TransformationOutputFinalizationLifecycleResult, ...]:
        """Release expired fences for deterministic Story 7 recovery."""
        now = timezone.now()
        with transaction.atomic():
            queryset = TransformationRun.objects.filter(
                status=TransformationRunStatus.OUTPUT_PENDING,
                current_finalization_token__isnull=False,
                finalization_lease_expires_at__lt=now,
            )
            if tenant_id is not None:
                queryset = queryset.filter(tenant_id=tenant_id)
            queryset = queryset.order_by("finalization_lease_expires_at", "created_at", "id")
            try:
                runs = tuple(
                    queryset.select_for_update(skip_locked=True)[: max(int(batch_size), 1)],
                )
            except DatabaseError:
                runs = tuple(queryset.select_for_update()[: max(int(batch_size), 1)])
            results: list[TransformationOutputFinalizationLifecycleResult] = []
            for run in runs:
                claim = _claim_from_run(run)
                authority_error_code = self._terminalize_invalid_authority(run=run, now=now)
                if authority_error_code is not None:
                    results.append(
                        _result(
                            claim,
                            TransformationOutputFinalizationLifecycleAction.FAILED,
                            run.finalization_retry_count,
                            authority_error_code,
                        )
                    )
                    continue
                outputs = tuple(run.outputs.select_for_update().order_by("output_index", "id"))
                if outputs and all(_is_terminal_output(output) for output in outputs):
                    self._finalization_service.complete_run_if_ready(
                        tenant_id=run.tenant_id,
                        run_id=run.id,
                        claim=claim,
                    )
                    action = TransformationOutputFinalizationLifecycleAction.SUCCEEDED
                    results.append(
                        _result(claim, action, run.finalization_retry_count, None),
                    )
                    self._audit(
                        run=run,
                        event_type=EventType.RECOVERY_EXECUTED,
                        transition="OUTPUT_FINALIZATION_RECOVERY_SUCCEEDED",
                        error_code="",
                    )
                    continue
                if run.finalization_retry_count < self._max_retries:
                    run.finalization_retry_count += 1
                    _clear_claim(run)
                    run.error_code = "output_finalization_lease_expired"
                    run.error_message = "Output finalization lease expired and was recovered."
                    run.save()
                    action = TransformationOutputFinalizationLifecycleAction.REQUEUED
                    code = "output_finalization_lease_expired"
                else:
                    self._finalization_service.fail_claimed_run(
                        claim=claim,
                        error_code="output_finalization_retry_exhausted",
                    )
                    action = TransformationOutputFinalizationLifecycleAction.FAILED
                    code = "output_finalization_retry_exhausted"
                results.append(
                    _result(claim, action, run.finalization_retry_count, code),
                )
                self._audit(
                    run=run,
                    event_type=EventType.RECOVERY_EXECUTED,
                    transition=f"OUTPUT_FINALIZATION_RECOVERY_{action.value}",
                    error_code=code,
                )
            return tuple(results)

    def _terminalize_invalid_authority(
        self,
        *,
        run: TransformationRun,
        now: datetime,
    ) -> str | None:
        try:
            plan = deserialize_transformation_execution_plan(run.execution_plan_snapshot_json)
            validate_transformation_authorization_snapshot(run=run, plan=plan)
        except (
            InvalidPersistedAuthorizationSnapshotError,
            UnsupportedPersistedAuthorizationSnapshotError,
            TransformationExecutionPlanDeserializationError,
            UnsupportedTransformationExecutionPlanVersionError,
        ) as error:
            error_code = str(getattr(error, "code", "invalid_authorization_snapshot"))
            run.status = TransformationRunStatus.FAILED
            run.error_code = error_code
            run.error_message = (
                "Persisted Transformation authority is not eligible for finalization."
            )
            run.completed_at = now
            _clear_claim(run)
            run.save()
            self._audit(
                run=run,
                event_type=EventType.RUN_FAILED,
                transition="OUTPUT_FINALIZATION_AUTHORITY_INVALID",
                error_code=error_code,
            )
            return error_code
        return None

    def get_queue_snapshot(
        self,
        *,
        tenant_id: str | None = None,
    ) -> TransformationOutputFinalizationQueueSnapshot:
        """Return sanitized queue, stale-claim, and cleanup gauges."""
        now = timezone.now()
        queryset = TransformationRun.objects.filter(status=TransformationRunStatus.OUTPUT_PENDING)
        leases = TransformationWorkspaceLease.objects.all()
        if tenant_id is not None:
            queryset = queryset.filter(tenant_id=tenant_id)
            leases = leases.filter(tenant_id=tenant_id)
        oldest = queryset.order_by("created_at").first()
        return TransformationOutputFinalizationQueueSnapshot(
            pending_count=queryset.filter(current_finalization_token__isnull=True).count(),
            active_count=queryset.filter(current_finalization_token__isnull=False).count(),
            stale_count=queryset.filter(finalization_lease_expires_at__lt=now).count(),
            cleanup_pending_count=leases.filter(
                state=TransformationWorkspaceLeaseState.CLEANUP_PENDING,
            ).count(),
            oldest_pending_age_seconds=(
                max((now - oldest.created_at).total_seconds(), 0.0) if oldest else 0.0
            ),
        )

    def _allocate_tenant_slot(self, *, tenant_id: str, now: datetime) -> int | None:
        active_slots = set(
            TransformationRun.objects.filter(
                tenant_id=tenant_id,
                status=TransformationRunStatus.OUTPUT_PENDING,
                current_finalization_token__isnull=False,
                finalization_lease_expires_at__gt=now,
            ).values_list("finalization_tenant_slot", flat=True),
        )
        return next(
            (
                slot
                for slot in range(1, self._per_tenant_concurrency + 1)
                if slot not in active_slots
            ),
            None,
        )

    def _audit(
        self,
        *,
        run: TransformationRun,
        event_type: EventType,
        transition: str,
        error_code: str,
    ) -> None:
        try:
            self._audit_service.record_transformation_event(
                event_type=event_type,
                tenant_id=str(run.tenant_id),
                run_id=str(run.id),
                correlation_id=run.correlation_id,
                transition=transition,
                actor=run.authorized_actor,
                metadata={
                    "phase": "output_finalization",
                    "attempt_number": run.finalization_attempt_number,
                    "retry_count": run.finalization_retry_count,
                    "error_code": error_code,
                },
            )
        except Exception:
            return


def _is_terminal_output(output: TransformationRunOutput) -> bool:
    kind = output.output_kind
    status = output.status
    if kind == TransformationRunOutputKind.STANDALONE:
        return status == TransformationRunOutputStatus.MATERIALIZED
    return status == TransformationRunOutputStatus.REGISTERED


def _has_matching_claim(
    *,
    run: TransformationRun,
    claim: TransformationOutputFinalizationClaim,
) -> bool:
    return (
        run.status == TransformationRunStatus.OUTPUT_PENDING
        and str(run.current_finalization_token) == claim.token
        and run.finalization_owner == claim.worker_id
        and run.finalization_attempt_number == claim.attempt_number
    )


def _clear_claim(run: TransformationRun) -> None:
    run.current_finalization_token = None
    run.finalization_owner = None
    run.finalization_claimed_at = None
    run.finalization_heartbeat_at = None
    run.finalization_lease_expires_at = None
    run.finalization_deadline_at = None
    run.finalization_tenant_slot = None


def _claim_from_run(run: TransformationRun) -> TransformationOutputFinalizationClaim:
    if (
        run.current_finalization_token is None
        or run.finalization_owner is None
        or run.finalization_claimed_at is None
        or run.finalization_lease_expires_at is None
        or run.finalization_deadline_at is None
    ):
        raise ValueError("Persisted finalization claim is incomplete.")
    return TransformationOutputFinalizationClaim(
        tenant_id=str(run.tenant_id),
        run_id=str(run.id),
        worker_id=run.finalization_owner,
        token=str(run.current_finalization_token),
        attempt_number=run.finalization_attempt_number,
        claimed_at=run.finalization_claimed_at,
        lease_expires_at=run.finalization_lease_expires_at,
        deadline_at=run.finalization_deadline_at,
        correlation_id=run.correlation_id,
    )


def _result(
    claim: TransformationOutputFinalizationClaim,
    action: TransformationOutputFinalizationLifecycleAction,
    retry_count: int,
    error_code: str | None = None,
) -> TransformationOutputFinalizationLifecycleResult:
    return TransformationOutputFinalizationLifecycleResult(
        tenant_id=claim.tenant_id,
        run_id=claim.run_id,
        attempt_number=claim.attempt_number,
        retry_count=retry_count,
        action=action,
        error_code=error_code,
        correlation_id=claim.correlation_id,
    )


def _validate_worker_id(worker_id: str) -> str:
    if not worker_id or worker_id != worker_id.strip():
        raise ValueError("Worker ID must be an exact non-empty string.")
    return worker_id
