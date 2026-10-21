from __future__ import annotations

import hashlib
from typing import Any, ClassVar

from resource_registry.events.event_models import DomainEvent
from resource_registry.events.event_publisher import (
    EventPublisher,
    ProductionAppendOnlyEventPublisher,
)
from resource_registry.events.event_types import EventType


class AuditService:
    """Centralized audit event generator."""

    _SENSITIVE_KEYS: ClassVar[set[str]] = {
        "preview",
        "raw_content",
        "rows",
        "credentials",
        "token",
        "password",
        "secret",
        "parameters",
        "execution_plan",
        "checkpoint",
        "provider_response",
        "provider_payload",
    }

    def __init__(self, publisher: EventPublisher | None = None) -> None:
        self._publisher = publisher or ProductionAppendOnlyEventPublisher()

    def record_transformation_event(  # noqa: PLR0913
        self,
        *,
        event_type: EventType,
        tenant_id: str,
        run_id: str,
        correlation_id: str,
        transition: str,
        actor: str | None = None,
        attempt_number: int | None = None,
        output_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> DomainEvent:
        """Publish one deterministic sanitized transformation lifecycle event."""
        allowed_types = {
            EventType.RUN_CREATED,
            EventType.RUN_STARTED,
            EventType.RUN_CANCELLED,
            EventType.RUN_FAILED,
            EventType.RUN_SUCCEEDED,
            EventType.OUTPUT_REGISTERED,
            EventType.RECOVERY_EXECUTED,
            EventType.RETRY_SCHEDULED,
            EventType.RETRY_EXHAUSTED,
        }
        if event_type not in allowed_types:
            raise ValueError("Unsupported transformation audit event type.")
        identity = "|".join(
            (
                event_type.value,
                run_id,
                str(attempt_number or 0),
                output_id or "",
                transition,
            ),
        )
        event = DomainEvent(
            event_type=event_type,
            tenant_id=tenant_id,
            actor=actor,
            correlation_id=correlation_id,
            run_id=run_id,
            attempt_number=attempt_number,
            output_id=output_id,
            transition=transition,
            metadata=self._sanitize_metadata(metadata or {}),
            event_id=hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        )
        return self._publisher.publish(event)

    def is_available(self) -> bool:
        """Return whether the configured append-only publisher is available."""
        return self._publisher.is_available()

    def record_dataset_registered(  # noqa: PLR0913
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        dataset_version_id: str | None,
        actor: str | None = None,
        correlation_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> DomainEvent:
        return self._publish(
            event_type=EventType.DATASET_REGISTERED,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            dataset_version_id=dataset_version_id,
            actor=actor,
            correlation_id=correlation_id,
            metadata=metadata,
        )

    def record_dataset_deduplicated(  # noqa: PLR0913
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        dataset_version_id: str,
        actor: str | None = None,
        correlation_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> DomainEvent:
        return self._publish(
            event_type=EventType.DATASET_DEDUPLICATED,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            dataset_version_id=dataset_version_id,
            actor=actor,
            correlation_id=correlation_id,
            metadata=metadata,
        )

    def record_dataset_version_created(  # noqa: PLR0913
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        dataset_version_id: str,
        actor: str | None = None,
        correlation_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> DomainEvent:
        return self._publish(
            event_type=EventType.DATASET_VERSION_CREATED,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            dataset_version_id=dataset_version_id,
            actor=actor,
            correlation_id=correlation_id,
            metadata=metadata,
        )

    def record_dataset_schema_created(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        dataset_version_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> DomainEvent:
        return self._publish(
            event_type=EventType.DATASET_SCHEMA_CREATED,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            dataset_version_id=dataset_version_id,
            metadata=metadata,
        )

    def record_dataset_profile_created(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        dataset_version_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> DomainEvent:
        return self._publish(
            event_type=EventType.DATASET_PROFILE_CREATED,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            dataset_version_id=dataset_version_id,
            metadata=metadata,
        )

    def record_dataset_artifact_registered(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        dataset_version_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> DomainEvent:
        return self._publish(
            event_type=EventType.DATASET_ARTIFACT_REGISTERED,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            dataset_version_id=dataset_version_id,
            metadata=metadata,
        )

    def record_dataset_artifact_uploaded(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        dataset_version_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> DomainEvent:
        return self._publish(
            event_type=EventType.DATASET_ARTIFACT_UPLOADED,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            dataset_version_id=dataset_version_id,
            metadata=metadata,
        )

    def record_dataset_artifact_deprecated(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        dataset_version_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> DomainEvent:
        return self._publish(
            event_type=EventType.DATASET_ARTIFACT_DEPRECATED,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            dataset_version_id=dataset_version_id,
            metadata=metadata,
        )

    def record_dataset_artifact_restored(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        dataset_version_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> DomainEvent:
        return self._publish(
            event_type=EventType.DATASET_ARTIFACT_RESTORED,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            dataset_version_id=dataset_version_id,
            metadata=metadata,
        )

    def record_dataset_artifact_reference_deleted(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        dataset_version_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> DomainEvent:
        return self._publish(
            event_type=EventType.DATASET_ARTIFACT_REFERENCE_DELETED,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            dataset_version_id=dataset_version_id,
            metadata=metadata,
        )

    def record_dataset_artifact_physical_deleted(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        dataset_version_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> DomainEvent:
        return self._publish(
            event_type=EventType.DATASET_ARTIFACT_PHYSICAL_DELETED,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            dataset_version_id=dataset_version_id,
            metadata=metadata,
        )

    def record_dataset_artifact_orphan_cleaned(
        self,
        *,
        tenant_id: str,
        dataset_id: str | None = None,
        dataset_version_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> DomainEvent:
        return self._publish(
            event_type=EventType.DATASET_ARTIFACT_ORPHAN_CLEANED,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            dataset_version_id=dataset_version_id,
            metadata=metadata,
        )

    def record_retention_policy_evaluated(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> DomainEvent:
        return self._publish(
            event_type=EventType.RETENTION_POLICY_EVALUATED,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            metadata=metadata,
        )

    def record_retention_policy_applied(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> DomainEvent:
        return self._publish(
            event_type=EventType.RETENTION_POLICY_APPLIED,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            metadata=metadata,
        )

    def record_artifact_retention_action_executed(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        dataset_version_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> DomainEvent:
        return self._publish(
            event_type=EventType.ARTIFACT_RETENTION_ACTION_EXECUTED,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            dataset_version_id=dataset_version_id,
            metadata=metadata,
        )

    def record_policy_created(
        self,
        *,
        tenant_id: str,
        dataset_id: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> DomainEvent:
        return self._publish(
            event_type=EventType.POLICY_CREATED,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            metadata=metadata,
        )

    def record_policy_revision_created(
        self,
        *,
        tenant_id: str,
        dataset_id: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> DomainEvent:
        return self._publish(
            event_type=EventType.POLICY_REVISION_CREATED,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            metadata=metadata,
        )

    def record_policy_activated(
        self,
        *,
        tenant_id: str,
        dataset_id: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> DomainEvent:
        return self._publish(
            event_type=EventType.POLICY_ACTIVATED,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            metadata=metadata,
        )

    def record_policy_deactivated(
        self,
        *,
        tenant_id: str,
        dataset_id: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> DomainEvent:
        return self._publish(
            event_type=EventType.POLICY_DEACTIVATED,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            metadata=metadata,
        )

    def record_policy_assignment(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        actor: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> DomainEvent:
        return self._publish(
            event_type=EventType.POLICY_ASSIGNED,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            actor=actor,
            metadata=metadata,
        )

    def record_policy_assigned(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> DomainEvent:
        return self._publish(
            event_type=EventType.POLICY_ASSIGNED,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            metadata=metadata,
        )

    def record_policy_removed(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> DomainEvent:
        return self._publish(
            event_type=EventType.POLICY_REMOVED,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            metadata=metadata,
        )

    def record_policy_resolved(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> DomainEvent:
        return self._publish(
            event_type=EventType.POLICY_RESOLVED,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            metadata=metadata,
        )

    def record_external_job_submitted(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> DomainEvent:
        return self._publish(
            event_type=EventType.EXTERNAL_JOB_SUBMITTED,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            metadata=metadata,
        )

    def record_external_job_running(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> DomainEvent:
        return self._publish(
            event_type=EventType.EXTERNAL_JOB_RUNNING,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            metadata=metadata,
        )

    def record_external_job_synced(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> DomainEvent:
        return self._publish(
            event_type=EventType.EXTERNAL_JOB_SYNCED,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            metadata=metadata,
        )

    def record_external_job_completed(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> DomainEvent:
        return self._publish(
            event_type=EventType.EXTERNAL_JOB_COMPLETED,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            metadata=metadata,
        )

    def record_external_job_failed(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> DomainEvent:
        return self._publish(
            event_type=EventType.EXTERNAL_JOB_FAILED,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            metadata=metadata,
        )

    def record_external_job_cancelled(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> DomainEvent:
        return self._publish(
            event_type=EventType.EXTERNAL_JOB_CANCELLED,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            metadata=metadata,
        )

    def record_external_job_result_ingested(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> DomainEvent:
        return self._publish(
            event_type=EventType.EXTERNAL_JOB_RESULT_INGESTED,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            metadata=metadata,
        )

    def record_external_job_result_deduplicated(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> DomainEvent:
        return self._publish(
            event_type=EventType.EXTERNAL_JOB_RESULT_DEDUPLICATED,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            metadata=metadata,
        )

    def record_external_job_result_ingestion_failed(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> DomainEvent:
        return self._publish(
            event_type=EventType.EXTERNAL_JOB_RESULT_INGESTION_FAILED,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            metadata=metadata,
        )

    def record_artifact_guardrail_rejected(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> DomainEvent:
        return self._publish(
            event_type=EventType.ARTIFACT_GUARDRAIL_REJECTED,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            metadata=metadata,
        )

    def record_artifact_integrity_verified(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> DomainEvent:
        return self._publish(
            event_type=EventType.ARTIFACT_INTEGRITY_VERIFIED,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            metadata=metadata,
        )

    def record_artifact_integrity_failed(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> DomainEvent:
        return self._publish(
            event_type=EventType.ARTIFACT_INTEGRITY_FAILED,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            metadata=metadata,
        )

    def record_artifact_materialization_policy_resolved(
        self,
        *,
        tenant_id: str,
        dataset_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> DomainEvent:
        return self._publish(
            event_type=EventType.ARTIFACT_MATERIALIZATION_POLICY_RESOLVED,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            metadata=metadata,
        )

    def record_artifact_materialization_policy_rejected(
        self,
        *,
        tenant_id: str,
        dataset_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> DomainEvent:
        return self._publish(
            event_type=EventType.ARTIFACT_MATERIALIZATION_POLICY_REJECTED,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            metadata=metadata,
        )

    def record_policy_provenance_resolved(
        self,
        *,
        tenant_id: str,
        dataset_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> DomainEvent:
        return self._publish(
            event_type=EventType.POLICY_PROVENANCE_RESOLVED,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            metadata=metadata,
        )

    def record_policy_enforcement_recorded(
        self,
        *,
        tenant_id: str,
        dataset_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> DomainEvent:
        return self._publish(
            event_type=EventType.POLICY_ENFORCEMENT_RECORDED,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            metadata=metadata,
        )

    def record_schema_visibility_filter_applied(
        self,
        *,
        tenant_id: str,
        dataset_id: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> DomainEvent:
        return self._publish(
            event_type=EventType.SCHEMA_VISIBILITY_FILTER_APPLIED,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            metadata=metadata,
        )

    def record_profile_visibility_filter_applied(
        self,
        *,
        tenant_id: str,
        dataset_id: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> DomainEvent:
        return self._publish(
            event_type=EventType.PROFILE_VISIBILITY_FILTER_APPLIED,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            metadata=metadata,
        )

    def record_visibility_explanation_generated(
        self,
        *,
        tenant_id: str,
        dataset_id: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> DomainEvent:
        return self._publish(
            event_type=EventType.VISIBILITY_EXPLANATION_GENERATED,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            metadata=metadata,
        )

    def record_visibility_diagnostics_generated(
        self,
        *,
        tenant_id: str,
        dataset_id: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> DomainEvent:
        return self._publish(
            event_type=EventType.VISIBILITY_DIAGNOSTICS_GENERATED,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            metadata=metadata,
        )

    def record_bif_capabilities_resolved(
        self,
        *,
        tenant_id: str,
        actor: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> DomainEvent:
        return self._publish(
            event_type=EventType.BIF_CAPABILITIES_RESOLVED,
            tenant_id=tenant_id,
            actor=actor,
            metadata=metadata,
        )

    def record_bif_capability_denied(
        self,
        *,
        tenant_id: str,
        actor: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> DomainEvent:
        return self._publish(
            event_type=EventType.BIF_CAPABILITY_DENIED,
            tenant_id=tenant_id,
            actor=actor,
            metadata=metadata,
        )

    def record_job_transition(  # noqa: PLR0913
        self,
        *,
        event_type: EventType,
        tenant_id: str | None,
        dataset_id: str | None,
        dataset_version_id: str | None = None,
        actor: str | None = None,
        correlation_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> DomainEvent:
        return self._publish(
            event_type=event_type,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            dataset_version_id=dataset_version_id,
            actor=actor,
            correlation_id=correlation_id,
            metadata=metadata,
        )

    def _publish(  # noqa: PLR0913
        self,
        *,
        event_type: EventType,
        tenant_id: str | None,
        dataset_id: str | None = None,
        dataset_version_id: str | None = None,
        actor: str | None = None,
        correlation_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> DomainEvent:
        event = DomainEvent(
            event_type=event_type,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            dataset_version_id=dataset_version_id,
            actor=actor,
            correlation_id=correlation_id,
            metadata=self._sanitize_metadata(metadata or {}),
        )
        return self._publisher.publish(event)

    def _sanitize_metadata(self, metadata: dict[str, Any]) -> dict[str, object]:
        from resource_registry.services.payload_sanitizer import (
            DEFAULT_BLOCKED_KEYS,
            sanitize_payload,
        )

        sanitized = sanitize_payload(
            metadata,
            blocked_keys=DEFAULT_BLOCKED_KEYS | frozenset(self._SENSITIVE_KEYS),
        )
        return sanitized if isinstance(sanitized, dict) else {}
