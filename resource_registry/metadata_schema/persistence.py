"""Persist immutable metadata schema publications and active selections."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, TypeVar

from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.models import F
from django.utils import timezone

from resource_registry.models import (
    MetadataSchemaActiveSelectionRecord,
    MetadataSchemaPublicationRecord,
    MetadataSchemaPublicationSequence,
)
from resource_registry.registry_core import (
    MetadataClassification,
    MetadataNamespaceKey,
    MetadataSchemaKey,
    MetadataScope,
)

from .declarations import MetadataPayloadBounds, MetadataSchemaVersion
from .definitions import (
    MetadataClassificationProfile,
    MetadataCompatibilityStatus,
    MetadataFieldClassification,
    MetadataFieldDefinition,
    MetadataSchemaDefinition,
    MetadataSchemaDefinitionRegistry,
    MetadataSchemaValidationError,
)
from .errors import MetadataSchemaPersistenceError
from .publications import (
    MetadataSchemaActivationResult,
    MetadataSchemaActiveSelection,
    MetadataSchemaPublication,
    MetadataSchemaPublicationResult,
    MetadataSchemaPublicationStatus,
)

_MAXIMUM_AUDIT_IDENTIFIER_LENGTH = 255
_Result = TypeVar("_Result")


class MetadataSchemaWriteRecoveryState(str, Enum):
    """Classify a fresh exact durable read after an unknown write outcome."""

    COMMITTED = "committed"
    NOT_COMMITTED = "not_committed"
    CONFLICTING_OR_INDETERMINATE = "conflicting_or_indeterminate"


@dataclass(frozen=True, slots=True, kw_only=True)
class MetadataSchemaPersistenceAuditMetadata:
    """Carry optional safe caller audit identifiers for one persistence write."""

    trusted_actor_reference: str | None = None
    correlation_identifier: str | None = None


class MetadataSchemaPublicationPersistence(Protocol):
    """Expose repository-neutral durable S4 publication and selection operations."""

    def record_publication(
        self,
        publication: MetadataSchemaPublication,
        audit_metadata: MetadataSchemaPersistenceAuditMetadata | None = None,
    ) -> MetadataSchemaPublicationResult:
        """Record one publication history entry without activating it."""
        ...

    def read_publication(
        self, schema: MetadataSchemaKey, schema_version: str
    ) -> MetadataSchemaPublication:
        """Read one exact durable publication."""
        ...

    def list_publications(self, schema: MetadataSchemaKey) -> tuple[MetadataSchemaPublication, ...]:
        """List durable publications in per-schema sequence order."""
        ...

    def read_active_selection(self, schema: MetadataSchemaKey) -> MetadataSchemaActiveSelection:
        """Read one exact schema's active selection projection."""
        ...

    def activate(
        self,
        schema: MetadataSchemaKey,
        schema_version: str,
        expected_revision: int,
        audit_metadata: MetadataSchemaPersistenceAuditMetadata | None = None,
    ) -> MetadataSchemaActivationResult:
        """Durably compare-and-set one schema's active selection."""
        ...

    def recover_publication_outcome(
        self, publication: MetadataSchemaPublication
    ) -> MetadataSchemaWriteRecoveryState:
        """Classify an unknown publication result through a fresh exact durable read."""
        ...

    def recover_active_selection_outcome(
        self, selection: MetadataSchemaActiveSelection
    ) -> MetadataSchemaWriteRecoveryState:
        """Classify an unknown activation result through a fresh exact durable read."""
        ...


class DjangoMetadataSchemaPublicationRepository:
    """Implement S4 persistence contracts using Django transactions and constraints."""

    def __init__(self, *, definition_registry: MetadataSchemaDefinitionRegistry) -> None:
        """Store the S2 public definition boundary used by persistence operations.

        Args:
            definition_registry: Public S2 definition resolver and compatibility boundary.

        Raises:
            MetadataSchemaPersistenceError: If the resolver is invalid.
        """
        if not isinstance(definition_registry, MetadataSchemaDefinitionRegistry):
            raise MetadataSchemaPersistenceError("invalid metadata schema persistence request")
        self._definition_registry = definition_registry

    def record_publication(
        self,
        publication: MetadataSchemaPublication,
        audit_metadata: MetadataSchemaPersistenceAuditMetadata | None = None,
    ) -> MetadataSchemaPublicationResult:
        """Persist one immutable canonical publication snapshot.

        Args:
            publication: Exact S3 publication to persist.
            audit_metadata: Optional safe caller audit identifiers.

        Returns:
            The durable publication and whether this call created it.

        Raises:
            MetadataSchemaPersistenceError: If validation, identity, or persistence fails.
        """
        for attempt in range(2):
            try:
                return _run_write_transaction(
                    lambda: self._record_publication_once(publication, audit_metadata)
                )
            except _PublicationInsertCollisionError:
                if attempt == 1:
                    raise MetadataSchemaPersistenceError(
                        "metadata schema repository failure"
                    ) from None
        raise MetadataSchemaPersistenceError("metadata schema repository failure")

    def read_publication(
        self, schema: MetadataSchemaKey, schema_version: str
    ) -> MetadataSchemaPublication:
        """Read one exact durable publication without resolving a live S2 definition.

        Args:
            schema: Exact metadata schema identity.
            schema_version: Exact opaque published version.

        Returns:
            The reconstructed immutable publication.

        Raises:
            MetadataSchemaPersistenceError: If the request is invalid or unpublished.
        """
        return _run_read_operation(lambda: self._read_publication(schema, schema_version))

    def list_publications(self, schema: MetadataSchemaKey) -> tuple[MetadataSchemaPublication, ...]:
        """List one schema's immutable history by committed sequence.

        Args:
            schema: Exact metadata schema identity.

        Returns:
            Immutable publications in ascending committed sequence order.

        Raises:
            MetadataSchemaPersistenceError: If the identity is invalid or history is empty.
        """
        return _run_read_operation(lambda: self._list_publications(schema))

    def read_active_selection(self, schema: MetadataSchemaKey) -> MetadataSchemaActiveSelection:
        """Read one exact schema's active-selection projection.

        Args:
            schema: Exact metadata schema identity.

        Returns:
            The immutable active selection.

        Raises:
            MetadataSchemaPersistenceError: If the identity is invalid or unselected.
        """
        return _run_read_operation(lambda: self._read_active_selection(schema))

    def activate(
        self,
        schema: MetadataSchemaKey,
        schema_version: str,
        expected_revision: int,
        audit_metadata: MetadataSchemaPersistenceAuditMetadata | None = None,
    ) -> MetadataSchemaActivationResult:
        """Compare-and-set one active selection using database-enforced revision predicates.

        Args:
            schema: Exact metadata schema identity.
            schema_version: Exact durable candidate version.
            expected_revision: Expected current per-schema revision.
            audit_metadata: Optional safe caller audit identifiers.

        Returns:
            The durable selection and whether it changed.

        Raises:
            MetadataSchemaPersistenceError: If validation, compatibility, CAS, or persistence fails.
        """
        return _run_write_transaction(
            lambda: self._activate(schema, schema_version, expected_revision, audit_metadata)
        )

    def recover_publication_outcome(
        self, publication: MetadataSchemaPublication
    ) -> MetadataSchemaWriteRecoveryState:
        """Classify an unknown publication outcome through a fresh durable read."""
        return _run_recovery_operation(lambda: self._recover_publication_outcome(publication))

    def _recover_publication_outcome(
        self, publication: MetadataSchemaPublication
    ) -> MetadataSchemaWriteRecoveryState:
        """Recover one publication outcome after replacing the prior database connection."""
        definition = _validate_publication_value(publication)
        expected_snapshot = _serialize_definition(definition)
        _force_fresh_connection()
        try:
            actual = self._read_publication(definition.schema, definition.schema_version)
        except MetadataSchemaPersistenceError as error:
            if str(error) == "unknown metadata schema publication":
                return MetadataSchemaWriteRecoveryState.NOT_COMMITTED
            if str(error) == "metadata schema persistence inconsistency":
                return MetadataSchemaWriteRecoveryState.CONFLICTING_OR_INDETERMINATE
            raise
        return (
            MetadataSchemaWriteRecoveryState.COMMITTED
            if _serialize_definition(actual.definition) == expected_snapshot
            else MetadataSchemaWriteRecoveryState.CONFLICTING_OR_INDETERMINATE
        )

    def recover_active_selection_outcome(
        self, selection: MetadataSchemaActiveSelection
    ) -> MetadataSchemaWriteRecoveryState:
        """Classify an unknown activation outcome through a fresh durable read."""
        return _run_recovery_operation(lambda: self._recover_active_selection_outcome(selection))

    def _recover_active_selection_outcome(
        self, selection: MetadataSchemaActiveSelection
    ) -> MetadataSchemaWriteRecoveryState:
        """Recover one active-selection outcome after replacing the prior database connection."""
        if not isinstance(selection, MetadataSchemaActiveSelection):
            raise MetadataSchemaPersistenceError("invalid metadata schema persistence request")
        _validate_identity(selection.schema, selection.schema_version)
        _validate_revision(selection.revision)
        _force_fresh_connection()
        try:
            actual = self._read_active_selection(selection.schema)
        except MetadataSchemaPersistenceError as error:
            if str(error) == "unknown metadata schema active selection":
                return MetadataSchemaWriteRecoveryState.NOT_COMMITTED
            if str(error) == "metadata schema persistence inconsistency":
                return MetadataSchemaWriteRecoveryState.CONFLICTING_OR_INDETERMINATE
            raise
        return (
            MetadataSchemaWriteRecoveryState.COMMITTED
            if actual == selection
            else MetadataSchemaWriteRecoveryState.CONFLICTING_OR_INDETERMINATE
        )

    def _record_publication_once(
        self,
        publication: MetadataSchemaPublication,
        audit_metadata: MetadataSchemaPersistenceAuditMetadata | None,
    ) -> MetadataSchemaPublicationResult:
        """Write one publication or signal that a concurrent insert needs a reread."""
        definition = self._validate_publication_and_resolve_definition(publication)
        audit = _validate_audit_metadata(audit_metadata)
        snapshot = _serialize_definition(definition)
        namespace, name = _schema_components(definition.schema)
        existing = MetadataSchemaPublicationRecord.objects.filter(
            schema_namespace=namespace, schema_name=name, schema_version=definition.schema_version
        ).first()
        if existing is not None:
            return _publication_result_from_record(existing, snapshot=snapshot)
        try:
            sequence = self._allocate_next_sequence(namespace=namespace, name=name)
            record = MetadataSchemaPublicationRecord.objects.create(
                schema_namespace=namespace,
                schema_name=name,
                schema_version=definition.schema_version,
                publication_sequence=sequence,
                definition_snapshot_json=snapshot,
                trusted_actor_reference=audit.trusted_actor_reference,
                correlation_identifier=audit.correlation_identifier,
            )
        except IntegrityError as error:
            raise _PublicationInsertCollisionError() from error
        return MetadataSchemaPublicationResult(
            publication=_publication_from_record(record), created=True
        )

    @staticmethod
    def _read_publication(
        schema: MetadataSchemaKey, schema_version: str
    ) -> MetadataSchemaPublication:
        """Read one exact durable publication after request validation."""
        namespace, name = _validate_identity(schema, schema_version)
        record = MetadataSchemaPublicationRecord.objects.filter(
            schema_namespace=namespace, schema_name=name, schema_version=schema_version
        ).first()
        if record is None:
            raise MetadataSchemaPersistenceError("unknown metadata schema publication")
        return _publication_from_record(record)

    @staticmethod
    def _list_publications(schema: MetadataSchemaKey) -> tuple[MetadataSchemaPublication, ...]:
        """Read one schema's durable history after request validation."""
        namespace, name = _validate_schema(schema)
        records = tuple(
            MetadataSchemaPublicationRecord.objects.filter(
                schema_namespace=namespace, schema_name=name
            ).order_by("publication_sequence")
        )
        if not records:
            raise MetadataSchemaPersistenceError("unknown metadata schema publication")
        return tuple(_publication_from_record(record) for record in records)

    @staticmethod
    def _read_active_selection(schema: MetadataSchemaKey) -> MetadataSchemaActiveSelection:
        """Read one exact active selection after request validation."""
        namespace, name = _validate_schema(schema)
        record = MetadataSchemaActiveSelectionRecord.objects.filter(
            schema_namespace=namespace, schema_name=name
        ).first()
        if record is None:
            raise MetadataSchemaPersistenceError("unknown metadata schema active selection")
        _validate_active_reference(record)
        return _selection_from_record(record)

    def _activate(
        self,
        schema: MetadataSchemaKey,
        schema_version: str,
        expected_revision: int,
        audit_metadata: MetadataSchemaPersistenceAuditMetadata | None,
    ) -> MetadataSchemaActivationResult:
        """Execute one activation inside the transaction supplied by the caller."""
        namespace, name = _validate_identity(schema, schema_version)
        _validate_revision(expected_revision)
        audit = _validate_audit_metadata(audit_metadata)
        self._resolve_definition(schema, schema_version)
        candidate = MetadataSchemaPublicationRecord.objects.filter(
            schema_namespace=namespace, schema_name=name, schema_version=schema_version
        ).first()
        if candidate is None:
            raise MetadataSchemaPersistenceError("unpublished metadata schema active version")
        current = MetadataSchemaActiveSelectionRecord.objects.filter(
            schema_namespace=namespace, schema_name=name
        ).first()
        if current is None:
            if expected_revision != 0:
                raise MetadataSchemaPersistenceError(
                    "metadata schema publication concurrency conflict"
                )
            try:
                created = MetadataSchemaActiveSelectionRecord.objects.create(
                    schema_namespace=namespace,
                    schema_name=name,
                    publication=candidate,
                    schema_version=schema_version,
                    revision=0,
                    trusted_actor_reference=audit.trusted_actor_reference,
                    correlation_identifier=audit.correlation_identifier,
                )
            except IntegrityError:
                raise MetadataSchemaPersistenceError(
                    "metadata schema publication concurrency conflict"
                ) from None
            return MetadataSchemaActivationResult(
                selection=_selection_from_record(created), changed=True
            )
        _validate_active_reference(current)
        if expected_revision != current.revision:
            raise MetadataSchemaPersistenceError("metadata schema publication concurrency conflict")
        if current.schema_version == schema_version:
            return MetadataSchemaActivationResult(
                selection=_selection_from_record(current), changed=False
            )
        self._validate_compatibility(schema, current.schema_version, schema_version)
        activation_timestamp = timezone.now()
        updated = MetadataSchemaActiveSelectionRecord.objects.filter(
            pk=current.pk, revision=expected_revision
        ).update(
            publication=candidate,
            schema_version=schema_version,
            revision=F("revision") + 1,
            trusted_actor_reference=audit.trusted_actor_reference,
            correlation_identifier=audit.correlation_identifier,
            activated_at=activation_timestamp,
            recorded_at=activation_timestamp,
        )
        if updated != 1:
            raise MetadataSchemaPersistenceError("metadata schema publication concurrency conflict")
        current.refresh_from_db()
        return MetadataSchemaActivationResult(
            selection=_selection_from_record(current), changed=True
        )

    @staticmethod
    def _allocate_next_sequence(*, namespace: str, name: str) -> int:
        """Allocate one serialized committed sequence inside the caller transaction."""
        sequence_row = (
            MetadataSchemaPublicationSequence.objects.select_for_update()
            .filter(schema_namespace=namespace, schema_name=name)
            .first()
        )
        if sequence_row is None:
            sequence_row = MetadataSchemaPublicationSequence.objects.create(
                schema_namespace=namespace, schema_name=name, last_committed_sequence=0
            )
            sequence_row = MetadataSchemaPublicationSequence.objects.select_for_update().get(
                pk=sequence_row.pk
            )
        sequence_row.last_committed_sequence += 1
        sequence_row.save(update_fields=["last_committed_sequence"])
        return sequence_row.last_committed_sequence

    def _validate_publication_and_resolve_definition(
        self, publication: MetadataSchemaPublication
    ) -> MetadataSchemaDefinition:
        """Validate an S3 publication and resolve its exact S2 definition."""
        definition = _validate_publication_value(publication)
        _validate_identity(definition.schema, definition.schema_version)
        resolved = self._resolve_definition(definition.schema, definition.schema_version)
        if resolved != definition:
            raise MetadataSchemaPersistenceError("invalid metadata schema persistence request")
        return resolved

    def _resolve_definition(
        self, schema: MetadataSchemaKey, schema_version: str
    ) -> MetadataSchemaDefinition:
        """Resolve one definition through the public S2 boundary only."""
        try:
            return self._definition_registry.resolve(schema, schema_version)
        except MetadataSchemaValidationError as error:
            raise MetadataSchemaPersistenceError("unknown metadata schema definition") from error

    def _validate_compatibility(
        self, schema: MetadataSchemaKey, current_version: str, candidate_version: str
    ) -> None:
        """Fail closed unless S2 reports compatible active replacement."""
        try:
            assessment = self._definition_registry.assess_writer_compatibility(
                base_schema=schema,
                base_schema_version=current_version,
                candidate_schema=schema,
                candidate_schema_version=candidate_version,
            )
        except MetadataSchemaValidationError as error:
            raise MetadataSchemaPersistenceError("unknown metadata schema definition") from error
        if assessment.status is MetadataCompatibilityStatus.COMPATIBLE:
            return
        if assessment.status is MetadataCompatibilityStatus.INCOMPATIBLE:
            raise MetadataSchemaPersistenceError("incompatible metadata schema active version")
        if assessment.status is MetadataCompatibilityStatus.REVIEW_REQUIRED:
            raise MetadataSchemaPersistenceError("metadata schema active version review required")
        raise MetadataSchemaPersistenceError("invalid metadata schema compatibility result")


class _PublicationInsertCollisionError(Exception):
    """Signal an identity/sequence insert collision that needs a clean transaction retry."""


def _run_read_operation(operation: Callable[[], _Result]) -> _Result:
    """Translate every read-boundary failure into one stable S4 application error."""
    try:
        return operation()
    except MetadataSchemaPersistenceError:
        raise
    except DatabaseError as error:
        raise _translate_database_error(error, commit_outcome_unknown=False) from None
    except Exception:
        raise MetadataSchemaPersistenceError("metadata schema repository failure") from None


def _run_recovery_operation(operation: Callable[[], _Result]) -> _Result:
    """Translate every recovery preparation, fresh-read, and comparison failure stably."""
    return _run_read_operation(operation)


def _run_write_transaction(operation: Callable[[], _Result]) -> _Result:
    """Run one write atomically and distinguish known rollback from indeterminate outcome."""
    atomic_context = transaction.atomic()
    database_connection = transaction.get_connection()
    try:
        atomic_context.__enter__()
    except DatabaseError as error:
        raise _translate_database_error(error, commit_outcome_unknown=False) from None
    except Exception:
        raise MetadataSchemaPersistenceError("metadata schema repository failure") from None

    try:
        result = operation()
    except Exception as operation_error:
        _rollback_write_transaction(atomic_context, operation_error)
        _raise_write_operation_error(operation_error)

    try:
        atomic_context.__exit__(None, None, None)
    except DatabaseError as error:
        raise _translate_database_error(
            error,
            commit_outcome_unknown=(
                _is_acknowledgement_error(error)
                or _is_rollback_confirmation_unavailable(database_connection)
            ),
        ) from None
    except Exception:
        raise MetadataSchemaPersistenceError("metadata schema repository failure") from None
    return result


def _is_rollback_confirmation_unavailable(database_connection: object) -> bool:
    """Detect Django's closed connection state after a failed rollback following commit failure."""
    return bool(
        getattr(database_connection, "closed_in_transaction", False)
        or getattr(database_connection, "connection", object()) is None
    )


def _rollback_write_transaction(
    atomic_context: AbstractContextManager[object], operation_error: Exception
) -> None:
    """Roll back one entered write transaction or report the durable result as indeterminate."""
    try:
        atomic_context.__exit__(
            type(operation_error), operation_error, operation_error.__traceback__
        )
    except Exception:
        raise MetadataSchemaPersistenceError("metadata schema commit outcome unknown") from None


def _raise_write_operation_error(operation_error: Exception) -> None:
    """Translate a body failure only after its rollback completed successfully."""
    if isinstance(operation_error, _PublicationInsertCollisionError):
        raise operation_error
    if isinstance(operation_error, MetadataSchemaPersistenceError):
        raise operation_error
    if isinstance(operation_error, DatabaseError):
        raise _translate_database_error(operation_error, commit_outcome_unknown=False) from None
    raise MetadataSchemaPersistenceError("metadata schema repository failure") from None


def _translate_database_error(
    error: DatabaseError, *, commit_outcome_unknown: bool
) -> MetadataSchemaPersistenceError:
    """Map driver and ORM errors without exposing their vendor-specific details."""
    if commit_outcome_unknown:
        return MetadataSchemaPersistenceError("metadata schema commit outcome unknown")
    sqlstate = _database_sqlstate(error)
    if sqlstate in {"40001", "40P01"}:
        return MetadataSchemaPersistenceError("metadata schema transaction failure")
    return MetadataSchemaPersistenceError("metadata schema repository failure")


def _is_acknowledgement_error(error: DatabaseError) -> bool:
    """Return whether a post-write connection failure leaves commit acknowledgement ambiguous."""
    sqlstate = _database_sqlstate(error)
    return sqlstate is not None and sqlstate.startswith("08")


def _database_sqlstate(error: DatabaseError) -> str | None:
    """Read a driver SQLSTATE when available without depending on a concrete driver type."""
    cause = error.__cause__ or error
    sqlstate = getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None)
    return sqlstate if type(sqlstate) is str else None


def _force_fresh_connection() -> None:
    """Require recovery reads to use a connection opened after the unknown write outcome."""
    if connection.in_atomic_block:
        raise MetadataSchemaPersistenceError("metadata schema repository failure")
    connection.close()
    connection.ensure_connection()


def _validate_publication_value(publication: object) -> MetadataSchemaDefinition:
    """Extract the exact S3 publication definition after validating its public value type."""
    if not isinstance(publication, MetadataSchemaPublication):
        raise MetadataSchemaPersistenceError("invalid metadata schema persistence request")
    return publication.definition


def _validate_schema(schema: object) -> tuple[str, str]:
    """Validate and flatten one exact schema identity for durable storage."""
    if not isinstance(schema, MetadataSchemaKey):
        raise MetadataSchemaPersistenceError("invalid metadata schema persistence identity")
    return _schema_components(schema)


def _validate_identity(schema: object, schema_version: object) -> tuple[str, str]:
    """Validate schema identity and opaque version in documented order."""
    namespace, name = _validate_schema(schema)
    if type(schema_version) is not str or not schema_version.strip():
        raise MetadataSchemaPersistenceError("invalid metadata schema persistence identity")
    return namespace, name


def _validate_revision(expected_revision: object) -> None:
    """Validate one non-negative non-boolean active-selection revision."""
    if type(expected_revision) is not int or expected_revision < 0:
        raise MetadataSchemaPersistenceError("invalid metadata schema persistence request")


def _validate_audit_metadata(
    audit_metadata: MetadataSchemaPersistenceAuditMetadata | None,
) -> MetadataSchemaPersistenceAuditMetadata:
    """Validate optional safe caller audit identifiers."""
    if audit_metadata is None:
        return MetadataSchemaPersistenceAuditMetadata()
    if not isinstance(audit_metadata, MetadataSchemaPersistenceAuditMetadata):
        raise MetadataSchemaPersistenceError("invalid metadata schema audit metadata")
    for value in (audit_metadata.trusted_actor_reference, audit_metadata.correlation_identifier):
        if value is not None and (
            type(value) is not str
            or not value.strip()
            or len(value) > _MAXIMUM_AUDIT_IDENTIFIER_LENGTH
        ):
            raise MetadataSchemaPersistenceError("invalid metadata schema audit metadata")
    return audit_metadata


def _schema_components(schema: MetadataSchemaKey) -> tuple[str, str]:
    """Flatten one validated Registry Core schema key into durable columns."""
    return schema.namespace.value, schema.name


def _publication_result_from_record(
    record: MetadataSchemaPublicationRecord, *, snapshot: Mapping[str, object]
) -> MetadataSchemaPublicationResult:
    """Return idempotent success or immutable-snapshot conflict for one existing record."""
    if record.definition_snapshot_json != snapshot:
        raise MetadataSchemaPersistenceError("conflicting metadata schema publication history")
    return MetadataSchemaPublicationResult(
        publication=_publication_from_record(record), created=False
    )


def _publication_from_record(record: MetadataSchemaPublicationRecord) -> MetadataSchemaPublication:
    """Reconstruct one public publication entirely from its durable canonical snapshot."""
    try:
        definition = _deserialize_definition(record.definition_snapshot_json)
    except (KeyError, TypeError, ValueError, MetadataSchemaValidationError) as error:
        raise MetadataSchemaPersistenceError("metadata schema persistence inconsistency") from error
    if (
        definition.schema.namespace.value != record.schema_namespace
        or definition.schema.name != record.schema_name
        or definition.schema_version != record.schema_version
        or record.publication_status != "PUBLISHED"
    ):
        raise MetadataSchemaPersistenceError("metadata schema persistence inconsistency")
    return MetadataSchemaPublication(
        definition=definition, status=MetadataSchemaPublicationStatus.PUBLISHED
    )


def _selection_from_record(
    record: MetadataSchemaActiveSelectionRecord,
) -> MetadataSchemaActiveSelection:
    """Map one durable active projection to its repository-neutral S3 value."""
    return MetadataSchemaActiveSelection(
        schema=MetadataSchemaKey(
            namespace=MetadataNamespaceKey(value=record.schema_namespace), name=record.schema_name
        ),
        schema_version=record.schema_version,
        revision=record.revision,
    )


def _validate_active_reference(record: MetadataSchemaActiveSelectionRecord) -> None:
    """Fail closed when a projection does not reference its own schema's history row."""
    publication = record.publication
    if (
        publication.schema_namespace != record.schema_namespace
        or publication.schema_name != record.schema_name
        or publication.schema_version != record.schema_version
    ):
        raise MetadataSchemaPersistenceError("metadata schema persistence inconsistency")


def _serialize_definition(definition: MetadataSchemaDefinition) -> dict[str, object]:
    """Build the complete canonical JSON-compatible S2 definition snapshot."""
    declaration = definition.declaration
    return {
        "schema_namespace": definition.schema.namespace.value,
        "schema_name": definition.schema.name,
        "schema_version": definition.schema_version,
        "declaration": {
            "semantic_owner": declaration.semantic_owner,
            "persistence_owner": declaration.persistence_owner,
            "allowed_scopes": [scope.value for scope in declaration.allowed_scopes],
            "classification": declaration.classification.value,
            "bounds": _serialize_bounds(declaration.bounds),
        },
        "bounds": _serialize_bounds(definition.bounds) if definition.bounds is not None else None,
        "fields": [
            {
                "name": field.name,
                "scalar_type": field.scalar_type,
                "required": field.required,
                "allows_null": field.allows_null,
                "semantic_owner": field.semantic_owner,
                "persistence_owner": field.persistence_owner,
            }
            for field in definition.fields
        ],
        "classification_profile": {
            "schema_classification": definition.classification_profile.schema_classification.value,
            "field_classifications": [
                {"field_name": entry.field_name, "classification": entry.classification.value}
                for entry in definition.classification_profile.field_classifications
            ],
        },
    }


def _serialize_bounds(bounds: MetadataPayloadBounds) -> dict[str, int]:
    """Map immutable payload bounds to a canonical JSON object."""
    return {
        "max_payload_bytes": bounds.max_payload_bytes,
        "max_nesting_depth": bounds.max_nesting_depth,
        "max_object_key_count": bounds.max_object_key_count,
        "max_collection_item_count": bounds.max_collection_item_count,
    }


def _deserialize_definition(snapshot: object) -> MetadataSchemaDefinition:
    """Reconstruct one complete S2 definition solely from its canonical snapshot."""
    if not isinstance(snapshot, Mapping):
        raise ValueError("snapshot is not a mapping")
    declaration_data = _mapping(snapshot["declaration"])
    profile_data = _mapping(snapshot["classification_profile"])
    namespace = MetadataNamespaceKey(value=_string(snapshot["schema_namespace"]))
    schema = MetadataSchemaKey(namespace=namespace, name=_string(snapshot["schema_name"]))
    declaration = MetadataSchemaVersion(
        namespace=namespace,
        schema=schema,
        schema_version=_string(snapshot["schema_version"]),
        semantic_owner=_string(declaration_data["semantic_owner"]),
        persistence_owner=_string(declaration_data["persistence_owner"]),
        allowed_scopes=tuple(
            MetadataScope(value) for value in _list(declaration_data["allowed_scopes"])
        ),
        classification=MetadataClassification(_string(declaration_data["classification"])),
        bounds=_deserialize_bounds(declaration_data["bounds"]),
    )
    fields = tuple(
        MetadataFieldDefinition(
            name=_string(field_data["name"]),
            scalar_type=_string(field_data["scalar_type"]),
            required=_bool(field_data["required"]),
            allows_null=_bool(field_data["allows_null"]),
            semantic_owner=_optional_string(field_data.get("semantic_owner")),
            persistence_owner=_optional_string(field_data.get("persistence_owner")),
        )
        for item in _list(snapshot["fields"])
        for field_data in (_mapping(item),)
    )
    profile = MetadataClassificationProfile(
        schema_classification=MetadataClassification(_string(profile_data["schema_classification"])),
        field_classifications=tuple(
            MetadataFieldClassification(
                field_name=_string(entry_data["field_name"]),
                classification=MetadataClassification(_string(entry_data["classification"])),
            )
            for item in _list(profile_data["field_classifications"])
            for entry_data in (_mapping(item),)
        ),
    )
    bounds_data = snapshot.get("bounds")
    return MetadataSchemaDefinition(
        declaration=declaration,
        fields=fields,
        bounds=_deserialize_bounds(bounds_data) if bounds_data is not None else None,
        classification_profile=profile,
    )


def _deserialize_bounds(value: object) -> MetadataPayloadBounds:
    """Reconstruct immutable bounds from a canonical snapshot object."""
    data = _mapping(value)
    return MetadataPayloadBounds(
        max_payload_bytes=_integer(data["max_payload_bytes"]),
        max_nesting_depth=_integer(data["max_nesting_depth"]),
        max_object_key_count=_integer(data["max_object_key_count"]),
        max_collection_item_count=_integer(data["max_collection_item_count"]),
    )


def _mapping(value: object) -> Mapping[str, object]:
    """Require one string-keyed snapshot mapping."""
    if not isinstance(value, Mapping) or not all(type(key) is str for key in value):
        raise ValueError("invalid snapshot mapping")
    return value


def _list(value: object) -> list[object]:
    """Require one snapshot list."""
    if not isinstance(value, list):
        raise ValueError("invalid snapshot list")
    return value


def _string(value: object) -> str:
    """Require one plain snapshot string."""
    if type(value) is not str:
        raise ValueError("invalid snapshot string")
    return value


def _optional_string(value: object) -> str | None:
    """Require one optional plain snapshot string."""
    if value is None:
        return None
    return _string(value)


def _bool(value: object) -> bool:
    """Require one plain snapshot boolean."""
    if type(value) is not bool:
        raise ValueError("invalid snapshot boolean")
    return value


def _integer(value: object) -> int:
    """Require one plain snapshot integer."""
    if type(value) is not int:
        raise ValueError("invalid snapshot integer")
    return value
