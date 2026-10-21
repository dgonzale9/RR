"""Test ERR-1.5-S4 durable metadata schema persistence contracts."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from threading import Barrier, Thread
from types import SimpleNamespace
from typing import Literal, cast

import pytest
from django.db import (
    DatabaseError,
    IntegrityError,
    OperationalError,
    close_old_connections,
    connection,
    transaction,
)

import resource_registry.metadata_schema.persistence as metadata_schema_persistence
from resource_registry.metadata_schema import (
    MetadataClassificationProfile,
    MetadataEnvelopeAdmission,
    MetadataFieldClassification,
    MetadataFieldDefinition,
    MetadataNamespaceDeclaration,
    MetadataPayloadBounds,
    MetadataSchemaActiveSelection,
    MetadataSchemaDefinition,
    MetadataSchemaDefinitionRegistry,
    MetadataSchemaPublication,
    MetadataSchemaPublicationStatus,
    MetadataSchemaRegistry,
    MetadataSchemaVersion,
)
from resource_registry.metadata_schema.errors import MetadataSchemaPersistenceError
from resource_registry.metadata_schema.persistence import (
    DjangoMetadataSchemaPublicationRepository,
    MetadataSchemaPersistenceAuditMetadata,
    MetadataSchemaWriteRecoveryState,
    _run_write_transaction,
)
from resource_registry.models import (
    MetadataSchemaActiveSelectionRecord,
    MetadataSchemaPublicationRecord,
    MetadataSchemaPublicationSequence,
)
from resource_registry.registry_core import (
    MetadataClassification,
    MetadataEnvelope,
    MetadataNamespaceKey,
    MetadataProvenance,
    MetadataSchemaKey,
    MetadataScope,
    NamespaceKey,
    ResourceKey,
    ResourceTypeKey,
)


def _build_declaration(version: str) -> MetadataSchemaVersion:
    namespace = MetadataNamespaceKey(value="atlas.metadata.example")
    return MetadataSchemaVersion(
        namespace=namespace,
        schema=MetadataSchemaKey(namespace=namespace, name="example"),
        schema_version=version,
        semantic_owner="metadata-team",
        persistence_owner="registry-store",
        allowed_scopes=(MetadataScope.RESOURCE,),
        classification=MetadataClassification.INTERNAL,
        bounds=MetadataPayloadBounds(
            max_payload_bytes=100,
            max_nesting_depth=4,
            max_object_key_count=10,
            max_collection_item_count=20,
        ),
    )


def _build_definition(declaration: MetadataSchemaVersion) -> MetadataSchemaDefinition:
    return MetadataSchemaDefinition(
        declaration=declaration,
        fields=(
            MetadataFieldDefinition(
                name="title", scalar_type="string", required=False, allows_null=True
            ),
        ),
        classification_profile=MetadataClassificationProfile(
            schema_classification=MetadataClassification.INTERNAL,
            field_classifications=(
                MetadataFieldClassification(
                    field_name="title", classification=MetadataClassification.INTERNAL
                ),
            ),
        ),
    )


def _repository(
    *versions: str,
) -> tuple[DjangoMetadataSchemaPublicationRepository, tuple[MetadataSchemaDefinition, ...]]:
    definitions = tuple(_build_definition(_build_declaration(version)) for version in versions)
    namespace = definitions[0].declaration.namespace
    registry = MetadataSchemaRegistry(
        namespace_declarations=(
            MetadataNamespaceDeclaration(
                namespace=namespace,
                semantic_owner="metadata-team",
                persistence_owner="registry-store",
            ),
        ),
        schema_version_declarations=tuple(definition.declaration for definition in definitions),
    )
    return (
        DjangoMetadataSchemaPublicationRepository(
            definition_registry=MetadataSchemaDefinitionRegistry(
                declaration_registry=registry, definitions=definitions
            )
        ),
        definitions,
    )


def _publication(definition: MetadataSchemaDefinition) -> MetadataSchemaPublication:
    return MetadataSchemaPublication(
        definition=definition, status=MetadataSchemaPublicationStatus.PUBLISHED
    )


class DriverConnectionError(Exception):
    """Expose a PostgreSQL connection SQLSTATE for transaction-classification tests."""

    sqlstate = "08006"


def _build_database_failure(failure_kind: str) -> DatabaseError:
    """Build one driver-redacted database error for a public persistence boundary test."""
    if failure_kind == "repository":
        return DatabaseError(
            "internal query connection commit rollback acknowledgement driver detail"
        )
    if failure_kind == "integrity":
        return IntegrityError(
            "internal constraint query connection commit rollback acknowledgement detail"
        )
    sqlstate = "40001" if failure_kind in {"serialization", "integrity_serialization"} else "40P01"
    failure: DatabaseError
    if failure_kind == "integrity_serialization":
        failure = IntegrityError(
            "internal constraint SQLSTATE driver connection commit rollback acknowledgement detail"
        )
    else:
        failure = OperationalError(
            "internal SQLSTATE driver query connection commit rollback acknowledgement detail"
        )
    driver_error = DriverConnectionError()
    driver_error.sqlstate = sqlstate
    failure.__cause__ = driver_error
    return failure


def _definition_with_changed_snapshot(
    definition: MetadataSchemaDefinition,
) -> MetadataSchemaDefinition:
    """Build an otherwise-identical definition with a different canonical field shape."""
    return MetadataSchemaDefinition(
        declaration=definition.declaration,
        fields=(
            MetadataFieldDefinition(
                name="title", scalar_type="string", required=True, allows_null=False
            ),
        ),
        classification_profile=definition.classification_profile,
    )


@pytest.mark.django_db(transaction=True)
def test_repository_persists_canonical_snapshot_and_exact_idempotency() -> None:
    repository, (definition,) = _repository("opaque/v1")

    first = repository.record_publication(
        _publication(definition),
        MetadataSchemaPersistenceAuditMetadata(
            trusted_actor_reference="actor-1", correlation_identifier="correlation-1"
        ),
    )
    repeated = repository.record_publication(_publication(definition))

    assert first.created is True
    assert repeated.created is False
    record = MetadataSchemaPublicationRecord.objects.get()
    assert record.publication_sequence == 1
    assert record.trusted_actor_reference == "actor-1"
    assert record.correlation_identifier == "correlation-1"
    assert record.published_at is not None
    assert record.recorded_at is not None

    reconstructed = repository.read_publication(definition.schema, definition.schema_version)
    assert reconstructed == _publication(definition)
    assert repository.list_publications(definition.schema) == (reconstructed,)


@pytest.mark.django_db(transaction=True)
def test_s5_admits_against_s4_durable_selection_and_exact_publication() -> None:
    """Exercise S5 against the public S4 repository rather than a simulated reader."""
    repository, (definition,) = _repository("opaque/v1")
    repository.record_publication(_publication(definition))
    repository.activate(definition.schema, definition.schema_version, expected_revision=0)
    candidate = MetadataEnvelope(
        scope=MetadataScope.RESOURCE,
        subject=ResourceKey(
            NamespaceKey("8dbf68a1-9567-4d74-931f-c36c4247f80b"),
            ResourceTypeKey("atlas.data.dataset"),
            "dataset-1",
        ),
        schema=definition.schema,
        schema_version=definition.schema_version,
        revision=1,
        classification=MetadataClassification.INTERNAL,
        provenance=MetadataProvenance(source_kind="catalog", producer="s5-integration"),
        observed_at=datetime(2026, 7, 23, tzinfo=UTC),
        payload={"title": "example"},
    )

    result = MetadataEnvelopeAdmission(reader=repository).admit(candidate)

    assert result.status.value == "admitted"
    assert result.candidate == candidate
    assert result.schema_binding is not None
    assert result.schema_binding.schema == definition.schema
    assert result.schema_binding.schema_version == definition.schema_version
    assert result.schema_binding.selection_revision == 0


@pytest.mark.django_db(transaction=True)
def test_repository_reconstructs_from_snapshot_without_a_live_definition_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify durable publication reads do not consult S1 or S2 after persistence."""
    repository, (definition,) = _repository("v1")
    repository.record_publication(_publication(definition))

    def fail_live_resolution(
        *args: object, **kwargs: object
    ) -> MetadataSchemaDefinition:
        raise AssertionError("live S2 resolution is prohibited for durable reconstruction")

    monkeypatch.setattr(MetadataSchemaDefinitionRegistry, "resolve", fail_live_resolution)
    assert repository.read_publication(definition.schema, "v1") == _publication(definition)


@pytest.mark.django_db(transaction=True)
def test_repository_rejects_unequal_snapshot_without_mutating_history() -> None:
    """Reject same identity with a different canonical snapshot and retain the original row."""
    repository, (definition,) = _repository("v1")
    repository.record_publication(_publication(definition))
    conflicting_definition = _definition_with_changed_snapshot(definition)
    conflicting_repository, _ = _repository("v1")
    conflicting_repository._definition_registry = MetadataSchemaDefinitionRegistry(
        declaration_registry=repository._definition_registry._declaration_registry,
        definitions=(conflicting_definition,),
    )

    with pytest.raises(ValueError, match="^conflicting metadata schema publication history$"):
        conflicting_repository.record_publication(_publication(conflicting_definition))

    record = MetadataSchemaPublicationRecord.objects.get()
    assert record.definition_snapshot_json["fields"][0]["required"] is False


@pytest.mark.django_db(transaction=True)
def test_database_rejects_publication_history_update_and_delete() -> None:
    """Prove the database trigger preserves immutable append-only publication history."""
    repository, (definition,) = _repository("v1")
    repository.record_publication(_publication(definition))
    record = MetadataSchemaPublicationRecord.objects.get()
    original_snapshot = record.definition_snapshot_json

    with pytest.raises(DatabaseError):
        MetadataSchemaPublicationRecord.objects.filter(pk=record.pk).update(schema_version="mutated")
    with pytest.raises(DatabaseError):
        MetadataSchemaPublicationRecord.objects.filter(pk=record.pk).delete()

    record.refresh_from_db()
    assert record.schema_version == "v1"
    assert record.definition_snapshot_json == original_snapshot


@pytest.mark.django_db(transaction=True)
def test_repository_allocates_per_schema_sequences_and_rolls_back() -> None:
    repository, definitions = _repository("v1", "v2")

    with pytest.raises(RuntimeError):
        with transaction.atomic():
            repository.record_publication(_publication(definitions[0]))
            raise RuntimeError("force rollback")
    assert MetadataSchemaPublicationRecord.objects.count() == 0
    assert MetadataSchemaPublicationSequence.objects.count() == 0

    repository.record_publication(_publication(definitions[0]))
    repository.record_publication(_publication(definitions[1]))
    assert list(
        MetadataSchemaPublicationRecord.objects.order_by("publication_sequence").values_list(
            "publication_sequence", flat=True
        )
    ) == [1, 2]


@pytest.mark.django_db(transaction=True)
def test_repository_preserves_stale_revision_precedence_and_selection_idempotency() -> None:
    repository, definitions = _repository("v1", "v2")
    for definition in definitions:
        repository.record_publication(_publication(definition))

    first = repository.activate(definitions[0].schema, "v1", 0)
    same = repository.activate(definitions[0].schema, "v1", 0)
    replacement = repository.activate(definitions[0].schema, "v2", 0)

    assert first.changed is True
    assert same.changed is False
    assert replacement.selection.revision == 1
    with pytest.raises(
        ValueError, match="^metadata schema publication concurrency conflict$"
    ):
        repository.activate(definitions[0].schema, "v2", 0)


@pytest.mark.django_db(transaction=True)
def test_replacement_updates_timestamps_and_stale_failure_preserves_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Persist a new activation event atomically and leave failed CAS state untouched."""
    repository, definitions = _repository("v1", "v2")
    for definition in definitions:
        repository.record_publication(_publication(definition))
    repository.activate(definitions[0].schema, "v1", 0)
    initial = MetadataSchemaActiveSelectionRecord.objects.get()
    initial_timestamp = initial.activated_at
    replacement_timestamp = initial_timestamp + timedelta(seconds=1)
    monkeypatch.setattr(
        "resource_registry.metadata_schema.persistence.timezone.now", lambda: replacement_timestamp
    )

    repository.activate(definitions[0].schema, "v2", 0)
    replaced = MetadataSchemaActiveSelectionRecord.objects.get()
    assert replaced.activated_at == replacement_timestamp
    assert replaced.recorded_at == replacement_timestamp
    assert replaced.activated_at > initial_timestamp

    with pytest.raises(ValueError, match="^metadata schema publication concurrency conflict$"):
        repository.activate(definitions[0].schema, "v1", 0)
    unchanged = MetadataSchemaActiveSelectionRecord.objects.get()
    assert unchanged.schema_version == "v2"
    assert unchanged.activated_at == replacement_timestamp


@pytest.mark.django_db(transaction=True)
def test_repository_enforces_database_constraints_and_active_projection_reference() -> None:
    repository, (definition,) = _repository("v1")
    repository.record_publication(_publication(definition))
    record = MetadataSchemaPublicationRecord.objects.get()

    with transaction.atomic():
        with pytest.raises(IntegrityError):
            MetadataSchemaPublicationRecord.objects.create(
                schema_namespace=record.schema_namespace,
                schema_name=record.schema_name,
                schema_version="v2",
                publication_sequence=1,
                definition_snapshot_json=record.definition_snapshot_json,
            )

    selection = repository.activate(definition.schema, "v1", 0)
    persisted_selection = MetadataSchemaActiveSelectionRecord.objects.get()
    assert persisted_selection.publication_id == record.id
    assert selection.selection.schema_version == "v1"


@pytest.mark.django_db(transaction=True)
def test_concurrent_publication_allocates_distinct_committed_sequences() -> None:
    repository, definitions = _repository("v1", "v2")
    barrier = Barrier(2)
    errors: list[BaseException] = []

    def publish(definition: MetadataSchemaDefinition) -> None:
        close_old_connections()
        try:
            barrier.wait()
            repository.record_publication(_publication(definition))
        except BaseException as error:
            errors.append(error)
        finally:
            close_old_connections()

    _run_concurrently(
        lambda: publish(definitions[0]),
        lambda: publish(definitions[1]),
    )

    assert errors == []
    assert list(
        MetadataSchemaPublicationRecord.objects.order_by("publication_sequence").values_list(
            "publication_sequence", flat=True
        )
    ) == [1, 2]


@pytest.mark.django_db(transaction=True)
def test_concurrent_subsequent_publication_allocates_next_sequence() -> None:
    """Serialize concurrent publications after the per-schema sequence row already exists."""
    repository, definitions = _repository("v1", "v2", "v3")
    repository.record_publication(_publication(definitions[0]))
    barrier = Barrier(2)
    errors: list[BaseException] = []

    def publish(definition: MetadataSchemaDefinition) -> None:
        close_old_connections()
        try:
            barrier.wait()
            repository.record_publication(_publication(definition))
        except BaseException as error:
            errors.append(error)
        finally:
            close_old_connections()

    _run_concurrently(lambda: publish(definitions[1]), lambda: publish(definitions[2]))

    assert errors == []
    assert list(
        MetadataSchemaPublicationRecord.objects.order_by("publication_sequence").values_list(
            "publication_sequence", flat=True
        )
    ) == [1, 2, 3]


@pytest.mark.django_db(transaction=True)
def test_concurrent_initial_activation_allows_one_compare_and_set_winner() -> None:
    repository, definitions = _repository("v1", "v2")
    for definition in definitions:
        repository.record_publication(_publication(definition))
    barrier = Barrier(2)
    changed: list[bool] = []
    errors: list[BaseException] = []

    def activate(version: str) -> None:
        close_old_connections()
        try:
            barrier.wait()
            changed.append(repository.activate(definitions[0].schema, version, 0).changed)
        except BaseException as error:
            errors.append(error)
        finally:
            close_old_connections()

    _run_concurrently(lambda: activate("v1"), lambda: activate("v2"))

    assert changed == [True]
    assert len(errors) == 1
    assert str(errors[0]) == "metadata schema publication concurrency conflict"


@pytest.mark.django_db(transaction=True)
def test_concurrent_replacement_activation_consumes_one_expected_revision() -> None:
    """Ensure conditional replacement CAS admits only one concurrent expected revision."""
    repository, definitions = _repository("v1", "v2", "v3")
    for definition in definitions:
        repository.record_publication(_publication(definition))
    repository.activate(definitions[0].schema, "v1", 0)
    barrier = Barrier(2)
    errors: list[BaseException] = []
    changed: list[bool] = []

    def activate(version: str) -> None:
        close_old_connections()
        try:
            barrier.wait()
            changed.append(repository.activate(definitions[0].schema, version, 0).changed)
        except BaseException as error:
            errors.append(error)
        finally:
            close_old_connections()

    _run_concurrently(lambda: activate("v2"), lambda: activate("v3"))
    assert changed == [True]
    assert len(errors) == 1
    assert str(errors[0]) == "metadata schema publication concurrency conflict"
    assert MetadataSchemaActiveSelectionRecord.objects.get().revision == 1


@pytest.mark.django_db(transaction=True)
def test_unknown_commit_outcome_requires_fresh_exact_recovery_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Classify committed, absent, and conflicting state after ambiguous acknowledgement."""
    repository, definitions = _repository("v1", "v2")
    committed = repository.record_publication(_publication(definitions[0])).publication

    assert (
        repository.recover_publication_outcome(committed)
        is MetadataSchemaWriteRecoveryState.COMMITTED
    )
    assert (
        repository.recover_publication_outcome(_publication(definitions[1]))
        is MetadataSchemaWriteRecoveryState.NOT_COMMITTED
    )

    class ConnectionAcknowledgementError(OperationalError):
        """Simulate a driver reporting a connection loss while leaving commit ambiguous."""

    class DriverConnectionError(Exception):
        """Expose the PostgreSQL connection-failure SQLSTATE used by the classifier."""

        sqlstate = "08006"

    ambiguous = ConnectionAcknowledgementError("connection lost")
    ambiguous.__cause__ = DriverConnectionError()

    class FailingAtomic:
        """Raise the simulated acknowledgement failure when the write transaction exits."""

        def __enter__(self) -> FailingAtomic:
            return self

        def __exit__(self, *args: object) -> bool:
            raise ambiguous

    monkeypatch.setattr(transaction, "atomic", FailingAtomic)
    with pytest.raises(ValueError, match="^metadata schema commit outcome unknown$") as error:
        _run_write_transaction(lambda: None)
    assert cast(MetadataSchemaPersistenceError, error.value).requires_fresh_read is True


def test_public_record_publication_maps_failed_rollback_to_unknown_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserve ordinary body errors only after rollback succeeds and fail closed otherwise."""
    repository, _ = _repository("v1")

    class SuccessfulRollbackAtomic:
        """Simulate a transaction context whose rollback completes normally."""

        def __enter__(self) -> SuccessfulRollbackAtomic:
            return self

        def __exit__(self, *args: object) -> Literal[False]:
            return False

    monkeypatch.setattr(transaction, "atomic", SuccessfulRollbackAtomic)
    with pytest.raises(ValueError, match="^invalid metadata schema persistence request$"):
        repository.record_publication(object())  # type: ignore[arg-type]

    rollback_error = OperationalError("rollback connection failure")
    rollback_error.__cause__ = DriverConnectionError()

    class FailedRollbackAtomic:
        """Simulate rollback failure with a misleading serialization SQLSTATE."""

        def __enter__(self) -> FailedRollbackAtomic:
            return self

        def __exit__(self, exception_type: object, *args: object) -> Literal[False]:
            if exception_type is not None:
                raise rollback_error
            return False

    monkeypatch.setattr(transaction, "atomic", FailedRollbackAtomic)
    with pytest.raises(ValueError, match="^metadata schema commit outcome unknown$") as error:
        repository.record_publication(object())  # type: ignore[arg-type]
    assert cast(MetadataSchemaPersistenceError, error.value).requires_fresh_read is True


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("sqlstate", "expected_error", "drop_connection"),
    (
        ("40001", "metadata schema transaction failure", False),
        ("40P01", "metadata schema transaction failure", False),
        ("08006", "metadata schema commit outcome unknown", False),
        ("40001", "metadata schema commit outcome unknown", True),
        ("40P01", "metadata schema commit outcome unknown", True),
    ),
)
def test_public_activation_classifies_commit_and_rollback_outcomes(
    monkeypatch: pytest.MonkeyPatch,
    sqlstate: str,
    expected_error: str,
    drop_connection: bool,
) -> None:
    """Preserve confirmed rollback mappings and fail closed when Django drops the connection."""
    repository, definitions = _repository("v1", "v2")
    for definition in definitions:
        repository.record_publication(_publication(definition))
    repository.activate(definitions[0].schema, "v1", 0)

    class CommitFailureError(OperationalError):
        """Simulate Django re-raising the original commit exception after rollback handling."""

    driver_error = DriverConnectionError()
    driver_error.sqlstate = sqlstate
    commit_error = CommitFailureError(
        "internal commit rollback acknowledgement connection SQLSTATE driver detail"
    )
    commit_error.__cause__ = driver_error

    class CommitFailingAtomic:
        """Fail only after the public activation has completed its successful write body."""

        def __enter__(self) -> CommitFailingAtomic:
            return self

        def __exit__(self, exception_type: object, *args: object) -> Literal[False]:
            if exception_type is None:
                if drop_connection:
                    connection.connection = None
                raise commit_error
            return False

    monkeypatch.setattr(transaction, "atomic", CommitFailingAtomic)
    with pytest.raises(ValueError, match=f"^{expected_error}$") as error:
        repository.activate(definitions[0].schema, "v2", 0)
    public_error = cast(MetadataSchemaPersistenceError, error.value)
    assert all(
        detail not in str(public_error)
        for detail in (
            "internal commit",
            "rollback acknowledgement",
            "connection SQLSTATE",
            "driver detail",
        )
    )
    assert public_error.requires_fresh_read is (
        expected_error.endswith("unknown")
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("operation_name", "failure_kind", "expected_error"),
    (
        ("publication", "repository", "metadata schema repository failure"),
        ("publication", "serialization", "metadata schema transaction failure"),
        ("publication", "deadlock", "metadata schema transaction failure"),
        ("publication", "integrity", "metadata schema repository failure"),
        ("publication", "integrity_serialization", "metadata schema transaction failure"),
        ("history", "repository", "metadata schema repository failure"),
        ("history", "serialization", "metadata schema transaction failure"),
        ("history", "deadlock", "metadata schema transaction failure"),
        ("history", "integrity", "metadata schema repository failure"),
        ("history", "integrity_serialization", "metadata schema transaction failure"),
        ("active_selection", "repository", "metadata schema repository failure"),
        ("active_selection", "serialization", "metadata schema transaction failure"),
        ("active_selection", "deadlock", "metadata schema transaction failure"),
        ("active_selection", "integrity", "metadata schema repository failure"),
        ("active_selection", "integrity_serialization", "metadata schema transaction failure"),
    ),
)
def test_public_read_operations_translate_database_failures_stably(
    monkeypatch: pytest.MonkeyPatch,
    operation_name: str,
    failure_kind: str,
    expected_error: str,
) -> None:
    """Map each public read boundary failure without leaking driver or query details."""
    repository, (definition,) = _repository("v1")
    repository.record_publication(_publication(definition))
    repository.activate(definition.schema, "v1", 0)
    publication_count = MetadataSchemaPublicationRecord.objects.count()
    selection_count = MetadataSchemaActiveSelectionRecord.objects.count()
    failure = _build_database_failure(failure_kind)

    def raise_database_failure(*args: object, **kwargs: object) -> object:
        raise failure

    if operation_name == "active_selection":
        monkeypatch.setattr(
            MetadataSchemaActiveSelectionRecord.objects, "filter", raise_database_failure
        )

        def operation() -> object:
            """Execute the selected public active-selection read operation."""
            return repository.read_active_selection(definition.schema)

    else:
        monkeypatch.setattr(
            MetadataSchemaPublicationRecord.objects,
            "filter",
            raise_database_failure,
        )
        if operation_name == "publication":

            def operation() -> object:
                """Execute the selected public exact-publication read operation."""
                return repository.read_publication(definition.schema, "v1")

        else:

            def operation() -> object:
                """Execute the selected public publication-history read operation."""
                return repository.list_publications(definition.schema)

    with pytest.raises(ValueError, match=f"^{expected_error}$") as error:
        operation()
    public_error = cast(MetadataSchemaPersistenceError, error.value)
    assert public_error.requires_fresh_read is False
    assert all(
        detail not in str(public_error)
        for detail in (
            "internal",
            "SQLSTATE",
            "driver",
            "query",
            "connection",
            "commit",
            "rollback",
            "acknowledgement",
            "40001",
            "40P01",
        )
    )
    assert MetadataSchemaPublicationRecord.objects.count() == publication_count
    assert MetadataSchemaActiveSelectionRecord.objects.count() == selection_count


@pytest.mark.django_db(transaction=True)
def test_active_selection_recovery_classifies_committed_and_not_committed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Classify exact durable active state and a proven absent projection through fresh reads."""
    repository, (definition,) = _repository("v1")
    repository.record_publication(_publication(definition))
    fresh_read_count = 0
    original_force_fresh_connection = metadata_schema_persistence._force_fresh_connection

    def force_and_count_fresh_connection() -> None:
        """Require the production fresh connection operation while recording its invocation."""
        nonlocal fresh_read_count
        fresh_read_count += 1
        original_force_fresh_connection()

    monkeypatch.setattr(
        metadata_schema_persistence,
        "_force_fresh_connection",
        force_and_count_fresh_connection,
    )
    absent_selection = MetadataSchemaActiveSelection(
        schema=definition.schema,
        schema_version=definition.schema_version,
        revision=0,
    )
    assert (
        repository.recover_active_selection_outcome(absent_selection)
        is MetadataSchemaWriteRecoveryState.NOT_COMMITTED
    )
    assert MetadataSchemaActiveSelectionRecord.objects.filter(
        schema_namespace=definition.schema.namespace.value,
        schema_name=definition.schema.name,
    ).count() == 0

    committed_selection = repository.activate(definition.schema, "v1", 0).selection
    before_count = MetadataSchemaActiveSelectionRecord.objects.count()
    assert (
        repository.recover_active_selection_outcome(committed_selection)
        is MetadataSchemaWriteRecoveryState.COMMITTED
    )
    assert MetadataSchemaActiveSelectionRecord.objects.count() == before_count
    assert fresh_read_count == len((absent_selection, committed_selection))


@pytest.mark.django_db(transaction=True)
def test_recovery_classifies_malformed_durable_publication_and_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Treat successful reads of malformed durable state as indeterminate without mutation."""
    repository, (definition,) = _repository("v1")
    publication = _publication(definition)
    record = SimpleNamespace(
        schema_namespace=definition.schema.namespace.value,
        schema_name=definition.schema.name,
        schema_version=definition.schema_version,
        publication_status="PUBLISHED",
        definition_snapshot_json={"malformed": True},
    )

    class OneRecordQuery:
        """Return one malformed row after the recovery query completes successfully."""

        def first(self) -> object:
            return record

    monkeypatch.setattr(
        MetadataSchemaPublicationRecord.objects, "filter", lambda **kwargs: OneRecordQuery()
    )
    assert (
        repository.recover_publication_outcome(publication)
        is MetadataSchemaWriteRecoveryState.CONFLICTING_OR_INDETERMINATE
    )

    malformed_selection = SimpleNamespace(
        schema_namespace=definition.schema.namespace.value,
        schema_name=definition.schema.name,
        schema_version=definition.schema_version,
        revision=0,
        publication=SimpleNamespace(
            schema_namespace="wrong.namespace",
            schema_name=definition.schema.name,
            schema_version=definition.schema_version,
        ),
    )

    class OneMalformedSelectionQuery:
        """Return one same-schema projection with a malformed history reference."""

        def first(self) -> object:
            return malformed_selection

    monkeypatch.setattr(
        MetadataSchemaActiveSelectionRecord.objects,
        "filter",
        lambda **kwargs: OneMalformedSelectionQuery(),
    )
    assert (
        repository.recover_active_selection_outcome(
            MetadataSchemaActiveSelection(
                schema=definition.schema,
                schema_version=definition.schema_version,
                revision=0,
            )
        )
        is MetadataSchemaWriteRecoveryState.CONFLICTING_OR_INDETERMINATE
    )


@pytest.mark.django_db(transaction=True)
def test_recovery_classifies_conflicting_publication_and_active_selection() -> None:
    """Return conflict only after a fresh durable read proves the expected state differs."""
    repository, definitions = _repository("v1", "v2")
    for definition in definitions:
        repository.record_publication(_publication(definition))
    repository.activate(definitions[0].schema, "v1", 0)

    assert (
        repository.recover_publication_outcome(
            _publication(_definition_with_changed_snapshot(definitions[0]))
        )
        is MetadataSchemaWriteRecoveryState.CONFLICTING_OR_INDETERMINATE
    )
    assert (
        repository.recover_active_selection_outcome(
            MetadataSchemaActiveSelection(
                schema=definitions[0].schema,
                schema_version="v2",
                revision=0,
            )
        )
        is MetadataSchemaWriteRecoveryState.CONFLICTING_OR_INDETERMINATE
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("failure_stage", ("close", "reopen", "query", "compare"))
@pytest.mark.parametrize("recovery_kind", ("publication", "active_selection"))
def test_public_recovery_translates_every_preparation_and_read_failure(
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
    recovery_kind: str,
) -> None:
    """Hide recovery infrastructure and snapshot failures behind stable S4 errors."""
    repository, definitions = _repository("v1", "v2")
    publication = repository.record_publication(_publication(definitions[0])).publication
    repository.record_publication(_publication(definitions[1]))
    active_selection = repository.activate(definitions[0].schema, "v1", 0).selection

    def fail_with_internal_detail(*args: object, **kwargs: object) -> object:
        raise RuntimeError("internal driver detail")

    if failure_stage == "close":
        monkeypatch.setattr(connection, "close", fail_with_internal_detail)
    elif failure_stage == "reopen":
        monkeypatch.setattr(
            connection, "ensure_connection", fail_with_internal_detail
        )
    elif failure_stage == "query":
        read_method = (
            "_read_publication" if recovery_kind == "publication" else "_read_active_selection"
        )
        monkeypatch.setattr(repository, read_method, fail_with_internal_detail)
    elif recovery_kind == "publication":
        serialize_definition = metadata_schema_persistence._serialize_definition
        calls = 0
        comparison_call_number = 2

        def fail_during_comparison(value: MetadataSchemaDefinition) -> dict[str, object]:
            nonlocal calls
            calls += 1
            if calls == comparison_call_number:
                raise RuntimeError("internal snapshot detail")
            return serialize_definition(value)

        monkeypatch.setattr(
            metadata_schema_persistence, "_serialize_definition", fail_during_comparison
        )
    else:
        monkeypatch.setattr(
            MetadataSchemaActiveSelection,
            "__eq__",
            fail_with_internal_detail,
        )

    with pytest.raises(ValueError, match="^metadata schema repository failure$") as error:
        if recovery_kind == "publication":
            repository.recover_publication_outcome(publication)
        else:
            repository.recover_active_selection_outcome(active_selection)
    assert "internal" not in str(error.value)


def _run_concurrently(first: Callable[[], None], second: Callable[[], None]) -> None:
    """Run two database worker functions and wait for both to complete."""
    first_thread = Thread(target=first)
    second_thread = Thread(target=second)
    first_thread.start()
    second_thread.start()
    first_thread.join()
    second_thread.join()


@pytest.mark.django_db(transaction=True)
def test_s4_immutability_trigger_is_present_in_initial_schema() -> None:
    """Provide the publication-history trigger from the consolidated initial schema."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM pg_trigger WHERE tgname = 'mspr_immutable_history_trigger'"
        )
        assert cursor.fetchone() == (1,)
