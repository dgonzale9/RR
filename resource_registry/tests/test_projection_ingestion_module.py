"""Verify pure Projection Ingestion Module contracts."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from resource_registry.catalog_module import (
    CatalogGeneration,
    CatalogGenerationKey,
    CatalogProjectionKey,
    CatalogProjectionRecord,
    CatalogProjectionStore,
)
from resource_registry.identity_type_module import ResourceTypeDeclaration, ResourceTypeRegistry
from resource_registry.namespace_module import NamespaceDeclaration, NamespaceRegistry
from resource_registry.projection_ingestion_module import (
    ProjectionIngestionCheckpoint,
    ProjectionIngestionEvidence,
    ProjectionIngestionSourceKind,
    ProjectionIngestionState,
    ProjectionIngestionStatus,
)
from resource_registry.registry_core import (
    NamespaceKey,
    OwnerDescriptor,
    OwnershipClassification,
    ProjectionTombstoneState,
    RegistryCoreValidationError,
    ResourceKey,
    ResourceTypeKey,
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_MODULE_SOURCE_PATH = _REPOSITORY_ROOT / "resource_registry" / "projection_ingestion_module.py"
_SECOND_SEQUENCE = 2
_PROHIBITED_IMPORT_ROOTS = frozenset(
    {
        "django",
        "BotPlatform",
        "BotServer",
        "Embeddings",
        "MCPServer",
        "Organization",
        "resource_registry.api",
        "resource_registry.application",
        "resource_registry.authorization",
        "resource_registry.bifs",
        "resource_registry.events",
        "resource_registry.migrations",
        "resource_registry.models",
        "resource_registry.providers",
        "resource_registry.read",
        "resource_registry.read_auth",
        "resource_registry.services",
        "resource_registry.storage",
        "resource_registry.transformations",
    }
)


def _build_namespace() -> NamespaceKey:
    """Build an admitted namespace fixture.

    Returns:
        An exact Registry namespace key.
    """
    return NamespaceKey(UUID("5dbf68a1-9567-4d74-931f-c36c4247f80b"))


def _build_resource_key(resource_id: str = "ingestion-1") -> ResourceKey:
    """Build an exact source resource fixture.

    Args:
        resource_id: Exact owner-issued resource identifier.

    Returns:
        A valid resource key.
    """
    return ResourceKey(_build_namespace(), ResourceTypeKey("atlas.catalog.document"), resource_id)


def _build_owner() -> OwnerDescriptor:
    """Build the declared source owner fixture.

    Returns:
        A valid domain-backed owner descriptor.
    """
    return OwnerDescriptor("document-catalog", OwnershipClassification.DOMAIN_BACKED)


def _build_generation(value: str = "ingestion-v1") -> CatalogGeneration:
    """Build an isolated Catalog generation fixture.

    Args:
        value: Exact generation identifier.

    Returns:
        A valid Catalog generation.
    """
    return CatalogGeneration(CatalogGenerationKey(value), "1")


def _build_store() -> CatalogProjectionStore:
    """Build an empty admitted Catalog store fixture.

    Returns:
        A valid empty Catalog store.
    """
    resource_type = ResourceTypeKey("atlas.catalog.document")
    type_registry = ResourceTypeRegistry((ResourceTypeDeclaration(resource_type, _build_owner()),))
    namespace_registry = NamespaceRegistry((NamespaceDeclaration(_build_namespace(), 42),))
    return CatalogProjectionStore(type_registry, namespace_registry, ())


def _build_evidence(
    sequence: int = 1,
    event_identity: tuple[str, str] = ("event-1", "idempotency-1"),
    generation: CatalogGeneration | None = None,
    source_kind: ProjectionIngestionSourceKind = ProjectionIngestionSourceKind.EVENT,
    tombstone_state: ProjectionTombstoneState = ProjectionTombstoneState.PRESENT,
) -> ProjectionIngestionEvidence:
    """Build valid immutable ingestion evidence.

    Args:
        sequence: Positive source sequence for the exact resource stream.
        event_identity: Exact source-issued event and idempotency identities.
        generation: Optional target Catalog generation.
        source_kind: Source evidence kind.
        tombstone_state: Whether the projection is present or tombstoned.

    Returns:
        A valid ingestion evidence value.
    """
    projected_generation = _build_generation() if generation is None else generation
    event_id, idempotency_key = event_identity
    tombstoned_at: datetime | None = None
    tombstone_reason_category: str | None = None
    if tombstone_state is ProjectionTombstoneState.TOMBSTONE:
        tombstoned_at = datetime(2026, 7, 29, 12, tzinfo=UTC)
        tombstone_reason_category = "source-deleted"
    projection = CatalogProjectionRecord(
        generation=projected_generation,
        projection_key=CatalogProjectionKey("projection-ingestion-1"),
        resource_key=_build_resource_key(),
        source_authority=_build_owner(),
        projection_revision=f"projection-r{sequence}",
        source_event_position=f"event-{sequence}",
        projected_at=datetime(2026, 7, 29, 11, tzinfo=UTC),
        source_updated_at=datetime(2026, 7, 29, 10, tzinfo=UTC),
        tombstone_state=tombstone_state,
        tombstoned_at=tombstoned_at,
        tombstone_reason_category=tombstone_reason_category,
    )
    return ProjectionIngestionEvidence(
        event_id,
        idempotency_key,
        source_kind,
        sequence,
        projected_generation.key,
        projection,
    )


def test_projection_ingestion_module_exports_and_preserves_immutable_evidence() -> None:
    """Expose only authorized contracts and preserve frozen value semantics."""
    from resource_registry import projection_ingestion_module

    evidence = _build_evidence()
    assert projection_ingestion_module.__all__ == (
        "ProjectionIngestionApplication",
        "ProjectionIngestionCheckpoint",
        "ProjectionIngestionEvidence",
        "ProjectionIngestionSourceKind",
        "ProjectionIngestionState",
        "ProjectionIngestionStatus",
    )
    assert tuple(field.name for field in fields(ProjectionIngestionEvidence)) == (
        "event_id",
        "idempotency_key",
        "source_kind",
        "source_sequence",
        "target_generation",
        "projection",
    )
    with pytest.raises(FrozenInstanceError):
        evidence.event_id = "other"  # type: ignore[misc]


def test_ingestion_applies_ordered_evidence_and_replaces_exact_projection() -> None:
    """Apply ordered evidence immutably and retain only the newest exact projection."""
    state = ProjectionIngestionState(_build_store())
    first_application = state.apply_evidence(_build_evidence())
    second_application = first_application.state.apply_evidence(
        _build_evidence(_SECOND_SEQUENCE, ("event-2", "idempotency-2"))
    )

    assert first_application.status is ProjectionIngestionStatus.APPLIED
    assert second_application.status is ProjectionIngestionStatus.APPLIED
    assert len(second_application.state.catalog_store.projections) == 1
    assert second_application.state.catalog_store.projections[0].source_event_position == "event-2"
    assert second_application.state.checkpoints[0].source_sequence == _SECOND_SEQUENCE


def test_ingestion_classifies_duplicate_stale_gap_and_conflicting_duplicate_evidence() -> None:
    """Distinguish idempotency, ordering, and integrity outcomes without mutating state."""
    state = ProjectionIngestionState(_build_store())
    evidence = _build_evidence()
    applied_state = state.apply_evidence(evidence).state

    assert applied_state.apply_evidence(evidence).status is ProjectionIngestionStatus.DUPLICATE
    assert (
        applied_state.apply_evidence(
            _build_evidence(1, ("event-stale", "idempotency-stale"))
        ).status
        is ProjectionIngestionStatus.STALE
    )
    assert (
        applied_state.apply_evidence(_build_evidence(3, ("event-gap", "idempotency-gap"))).status
        is ProjectionIngestionStatus.GAP
    )
    assert (
        applied_state.apply_evidence(_build_evidence(2, ("event-1", "different-key"))).status
        is ProjectionIngestionStatus.REJECTED
    )


def test_ingestion_allows_snapshot_baselines_and_isolates_generations() -> None:
    """Allow snapshot anchors while keeping target generations isolated."""
    first_generation = _build_generation("ingestion-v1")
    second_generation = _build_generation("ingestion-v2")
    state = ProjectionIngestionState(_build_store())
    snapshot_application = state.apply_evidence(
        _build_evidence(
            8,
            ("snapshot-8", "snapshot-key-8"),
            first_generation,
            ProjectionIngestionSourceKind.SNAPSHOT,
        )
    )
    tombstone_application = snapshot_application.state.apply_evidence(
        _build_evidence(
            8,
            ("backfill-8", "backfill-key-8"),
            second_generation,
            ProjectionIngestionSourceKind.BACKFILL,
            ProjectionTombstoneState.TOMBSTONE,
        )
    )

    assert (
        len(tombstone_application.state.catalog_store.list_projections(first_generation.key)) == 1
    )
    assert tombstone_application.state.catalog_store.list_projections(second_generation.key) == ()
    assert len(
        tombstone_application.state.catalog_store.list_projections(
            second_generation.key, include_tombstones=True
        )
    ) == 1


def test_ingestion_state_rejects_catalog_projections_without_checkpoints() -> None:
    """Reject pre-populated Catalog state that cannot establish source ordering."""
    evidence = _build_evidence()
    prepopulated_store = CatalogProjectionStore(
        _build_store().resource_type_registry,
        _build_store().namespace_registry,
        (evidence.projection,),
    )

    with pytest.raises(
        RegistryCoreValidationError,
        match="^catalog projection missing ingestion checkpoint$",
    ):
        ProjectionIngestionState(prepopulated_store)

    checkpoint = ProjectionIngestionCheckpoint(
        evidence.target_generation,
        evidence.projection.resource_key,
        evidence.source_sequence,
        evidence.event_id,
        evidence.idempotency_key,
    )
    assert ProjectionIngestionState(prepopulated_store, (evidence,), (checkpoint,)).checkpoints == (
        checkpoint,
    )


def test_ingestion_state_rejects_processed_evidence_without_current_state() -> None:
    """Reject duplicate evidence that lacks a matching checkpoint and Catalog projection."""
    evidence = _build_evidence()

    with pytest.raises(
        RegistryCoreValidationError,
        match="^processed projection evidence missing checkpoint$",
    ):
        ProjectionIngestionState(_build_store(), (evidence,))


def test_ingestion_state_rejects_processed_sequence_gaps() -> None:
    """Reject retained event history that skips source progress before a current checkpoint."""
    first_evidence = _build_evidence()
    third_evidence = _build_evidence(3, ("event-3", "idempotency-3"))
    prepopulated_store = CatalogProjectionStore(
        _build_store().resource_type_registry,
        _build_store().namespace_registry,
        (third_evidence.projection,),
    )
    checkpoint = ProjectionIngestionCheckpoint(
        third_evidence.target_generation,
        third_evidence.projection.resource_key,
        third_evidence.source_sequence,
        third_evidence.event_id,
        third_evidence.idempotency_key,
    )

    with pytest.raises(
        RegistryCoreValidationError,
        match="^projection ingestion processed sequence gap$",
    ):
        ProjectionIngestionState(
            prepopulated_store,
            (first_evidence, third_evidence),
            (checkpoint,),
        )


def test_ingestion_rejects_projection_key_collisions_without_mutating_state() -> None:
    """Return a safe rejection when a projection key belongs to another resource."""
    first_evidence = _build_evidence()
    applied_state = ProjectionIngestionState(_build_store()).apply_evidence(first_evidence).state
    conflicting_projection = CatalogProjectionRecord(
        generation=first_evidence.projection.generation,
        projection_key=first_evidence.projection.projection_key,
        resource_key=_build_resource_key("other-resource"),
        source_authority=_build_owner(),
        projection_revision="projection-r1",
        source_event_position="event-1",
        projected_at=datetime(2026, 7, 29, 11, tzinfo=UTC),
        source_updated_at=datetime(2026, 7, 29, 10, tzinfo=UTC),
    )
    conflicting_evidence = ProjectionIngestionEvidence(
        "other-event-1",
        "other-idempotency-1",
        ProjectionIngestionSourceKind.EVENT,
        1,
        conflicting_projection.generation.key,
        conflicting_projection,
    )

    application = applied_state.apply_evidence(conflicting_evidence)

    assert application.status is ProjectionIngestionStatus.REJECTED
    assert application.state is applied_state


def test_ingestion_state_rejects_checkpoints_without_processed_evidence() -> None:
    """Reject current Catalog/checkpoint state that cannot classify exact duplicate delivery."""
    evidence = _build_evidence()
    prepopulated_store = CatalogProjectionStore(
        _build_store().resource_type_registry,
        _build_store().namespace_registry,
        (evidence.projection,),
    )
    checkpoint = ProjectionIngestionCheckpoint(
        evidence.target_generation,
        evidence.projection.resource_key,
        evidence.source_sequence,
        evidence.event_id,
        evidence.idempotency_key,
    )

    with pytest.raises(
        RegistryCoreValidationError,
        match="^ingestion checkpoint missing processed evidence$",
    ):
        ProjectionIngestionState(prepopulated_store, (), (checkpoint,))


@pytest.mark.parametrize(
    ("factory", "message"),
    (
        (
            lambda: ProjectionIngestionEvidence(
                "event-1",
                "idempotency-1",
                ProjectionIngestionSourceKind.EVENT,
                0,
                _build_generation().key,
                _build_evidence().projection,
            ),
            "invalid projection ingestion source sequence",
        ),
        (
            lambda: ProjectionIngestionEvidence(
                "event-1",
                "idempotency-1",
                ProjectionIngestionSourceKind.EVENT,
                1,
                CatalogGenerationKey("other-generation"),
                _build_evidence().projection,
            ),
            "projection ingestion generation mismatch",
        ),
    ),
)
def test_ingestion_evidence_fails_closed_for_invalid_source_or_generation(
    factory: object,
    message: str,
) -> None:
    """Reject malformed source sequence and target-generation evidence.

    Args:
        factory: Zero-argument callable constructing invalid source evidence.
        message: Stable validation message expected from the invalid value.
    """
    with pytest.raises(RegistryCoreValidationError, match=f"^{message}$"):
        factory()  # type: ignore[operator]


def test_projection_ingestion_module_has_clean_import_and_no_runtime_dependencies() -> None:
    """Keep Projection Ingestion independent of persistence and runtime layers."""
    environment = os.environ.copy()
    environment.pop("DJANGO_SETTINGS_MODULE", None)
    process = subprocess.run(
        [sys.executable, "-c", "import resource_registry.projection_ingestion_module"],
        cwd=_REPOSITORY_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert process.returncode == 0, process.stderr
    source_tree = ast.parse(_MODULE_SOURCE_PATH.read_text(encoding="utf-8"))
    imported_modules = {
        imported_name.name
        for node in ast.walk(source_tree)
        if isinstance(node, ast.Import)
        for imported_name in node.names
    }
    imported_modules.update(
        node.module
        for node in ast.walk(source_tree)
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module is not None
    )
    assert not any(
        imported_module in _PROHIBITED_IMPORT_ROOTS
        or any(
            imported_module.startswith(f"{prohibited_import_root}.")
            for prohibited_import_root in _PROHIBITED_IMPORT_ROOTS
        )
        for imported_module in imported_modules
    )
