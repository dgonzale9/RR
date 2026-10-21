from __future__ import annotations

from collections.abc import Sequence

from resource_registry.events.audit_service import AuditService
from resource_registry.events.event_publisher import InMemoryEventPublisher
from resource_registry.security.integrity import ArtifactIntegrityVerifier
from resource_registry.services.registry.artifact_lifecycle_service import ArtifactLifecycleService
from resource_registry.services.registry.dataset_registry_service import DatasetRegistryService
from resource_registry.services.registry.lineage_service import LineageService
from resource_registry.services.transformation_output_finalization_job_service import (
    TransformationOutputFinalizationJobService,
)
from resource_registry.services.transformation_run_job_service import TransformationRunJobService
from resource_registry.storage.factory import DataRegistryStorageFactory
from resource_registry.storage.read_adapters import ArtifactReadAdapter
from resource_registry.transformations.execution.attempt_persistence import (
    TransformationRunAttemptPersistenceService,
)
from resource_registry.transformations.operational.logging import (
    TransformationOperationalLogContextBuilder,
)
from resource_registry.transformations.operational.metrics import NoOpMetricsAdapter
from resource_registry.transformations.output_finalization import (
    TransformationRunOutputFinalizationService,
)
from resource_registry.transformations.providers import TransformationProviderRegistry


def build_test_audit_service() -> AuditService:
    """Build an isolated append-only audit service for runtime tests."""
    return AuditService(publisher=InMemoryEventPublisher())


def build_test_job_service(
    *,
    max_retries: int = 3,
    audit_service: AuditService | None = None,
) -> TransformationRunJobService:
    """Build a fully injected transformation job service for tests."""
    return TransformationRunJobService(
        attempt_persistence=TransformationRunAttemptPersistenceService(),
        max_retries=max_retries,
        audit_service=audit_service or build_test_audit_service(),
    )


def build_test_output_finalization_service(
    *,
    audit_service: AuditService | None = None,
    source_read_adapters: Sequence[ArtifactReadAdapter] = (),
) -> TransformationRunOutputFinalizationService:
    """Build a fully injected output-finalization service for tests."""
    active_audit_service = audit_service or build_test_audit_service()
    return TransformationRunOutputFinalizationService(
        storage_factory=DataRegistryStorageFactory(),
        source_read_adapters=source_read_adapters,
        integrity_verifier=ArtifactIntegrityVerifier(),
        artifact_lifecycle_service=ArtifactLifecycleService(
            audit_service=active_audit_service,
        ),
        dataset_registry_service=DatasetRegistryService(),
        lineage_service=LineageService(),
        audit_service=active_audit_service,
        metrics=NoOpMetricsAdapter(),
        log_context_builder=TransformationOperationalLogContextBuilder(),
    )


def build_test_output_finalization_job_service(
    *,
    max_retries: int = 3,
    per_tenant_concurrency: int = 2,
    audit_service: AuditService | None = None,
) -> TransformationOutputFinalizationJobService:
    """Build an isolated durable output-finalization job service."""
    return TransformationOutputFinalizationJobService(
        finalization_service=build_test_output_finalization_service(),
        max_retries=max_retries,
        per_tenant_concurrency=per_tenant_concurrency,
        audit_service=audit_service or build_test_audit_service(),
    )


def freeze_test_provider_registry(
    registry: TransformationProviderRegistry,
) -> TransformationProviderRegistry:
    """Validate and freeze one explicitly assembled test registry."""
    registry.validate()
    registry.freeze()
    return registry
