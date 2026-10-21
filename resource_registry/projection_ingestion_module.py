"""Define pure Projection Ingestion Module contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from itertools import pairwise

from .catalog_module import CatalogGenerationKey, CatalogProjectionRecord, CatalogProjectionStore
from .registry_core import RegistryCoreValidationError, ResourceKey

__all__ = (
    "ProjectionIngestionApplication",
    "ProjectionIngestionCheckpoint",
    "ProjectionIngestionEvidence",
    "ProjectionIngestionSourceKind",
    "ProjectionIngestionState",
    "ProjectionIngestionStatus",
)


class ProjectionIngestionSourceKind(str, Enum):
    """Classify immutable source evidence without naming a transport provider."""

    EVENT = "event"
    SNAPSHOT = "snapshot"
    BACKFILL = "backfill"

    @classmethod
    def _missing_(cls, value: object) -> ProjectionIngestionSourceKind | None:
        """Reject source kinds outside the approved evidence set.

        Args:
            value: Candidate source kind.

        Raises:
            RegistryCoreValidationError: If the source kind is invalid.
        """
        raise RegistryCoreValidationError("invalid projection ingestion source kind")


class ProjectionIngestionStatus(str, Enum):
    """Classify the safe result of one in-memory projection-ingestion evaluation."""

    APPLIED = "applied"
    DUPLICATE = "duplicate"
    STALE = "stale"
    GAP = "gap"
    REJECTED = "rejected"

    @classmethod
    def _missing_(cls, value: object) -> ProjectionIngestionStatus | None:
        """Reject result statuses outside the approved evaluation outcomes.

        Args:
            value: Candidate ingestion status.

        Raises:
            RegistryCoreValidationError: If the ingestion status is invalid.
        """
        raise RegistryCoreValidationError("invalid projection ingestion status")


@dataclass(frozen=True, slots=True)
class ProjectionIngestionEvidence:
    """Carry immutable source evidence for one Catalog projection transition.

    Args:
        event_id: Exact non-blank source-issued event identity.
        idempotency_key: Exact non-blank source-issued deduplication identity.
        source_kind: Whether evidence originated from an event, snapshot, or backfill.
        source_sequence: Positive source-issued sequence within the exact resource stream.
        target_generation: Explicit Catalog generation that may receive the projection.
        projection: Normalized Catalog projection carrying exact owner and tombstone evidence.

    Raises:
        RegistryCoreValidationError: If source, generation, or projection evidence is invalid.
    """

    event_id: str
    idempotency_key: str
    source_kind: ProjectionIngestionSourceKind
    source_sequence: int
    target_generation: CatalogGenerationKey
    projection: CatalogProjectionRecord

    def __post_init__(self) -> None:
        """Validate exact source and target-generation evidence.

        Raises:
            RegistryCoreValidationError: If source, generation, or projection evidence is invalid.
        """
        if not _is_non_blank_plain_string(self.event_id):
            raise RegistryCoreValidationError("invalid projection ingestion event id")
        if not _is_non_blank_plain_string(self.idempotency_key):
            raise RegistryCoreValidationError("invalid projection ingestion idempotency key")
        if not isinstance(self.source_kind, ProjectionIngestionSourceKind):
            raise RegistryCoreValidationError("invalid projection ingestion source kind")
        if type(self.source_sequence) is not int or self.source_sequence <= 0:
            raise RegistryCoreValidationError("invalid projection ingestion source sequence")
        if not isinstance(self.target_generation, CatalogGenerationKey):
            raise RegistryCoreValidationError("invalid projection ingestion target generation")
        if not isinstance(self.projection, CatalogProjectionRecord):
            raise RegistryCoreValidationError("invalid projection ingestion projection")
        if self.projection.generation.key != self.target_generation:
            raise RegistryCoreValidationError("projection ingestion generation mismatch")


@dataclass(frozen=True, slots=True)
class ProjectionIngestionCheckpoint:
    """Record the latest in-memory source progress applied to one resource/generation pair.

    Args:
        target_generation: Exact Catalog generation receiving source evidence.
        resource_key: Exact owner-issued resource identity whose progress is recorded.
        source_sequence: Last accepted positive source sequence.
        event_id: Exact event identity that advanced the checkpoint.
        idempotency_key: Exact idempotency identity that advanced the checkpoint.

    Raises:
        RegistryCoreValidationError: If checkpoint evidence is invalid.
    """

    target_generation: CatalogGenerationKey
    resource_key: ResourceKey
    source_sequence: int
    event_id: str
    idempotency_key: str

    def __post_init__(self) -> None:
        """Validate the immutable checkpoint evidence.

        Raises:
            RegistryCoreValidationError: If checkpoint evidence is invalid.
        """
        if not isinstance(self.target_generation, CatalogGenerationKey):
            raise RegistryCoreValidationError("invalid projection ingestion checkpoint generation")
        if not isinstance(self.resource_key, ResourceKey):
            raise RegistryCoreValidationError("invalid projection ingestion checkpoint resource")
        if type(self.source_sequence) is not int or self.source_sequence <= 0:
            raise RegistryCoreValidationError("invalid projection ingestion checkpoint sequence")
        if not _is_non_blank_plain_string(self.event_id):
            raise RegistryCoreValidationError("invalid projection ingestion checkpoint event id")
        if not _is_non_blank_plain_string(self.idempotency_key):
            raise RegistryCoreValidationError(
                "invalid projection ingestion checkpoint idempotency key"
            )


@dataclass(frozen=True, slots=True)
class ProjectionIngestionApplication:
    """Report one pure ingestion decision and its resulting immutable state.

    Args:
        status: Safe evaluation result for the submitted evidence.
        state: Resulting immutable ingestion state; unchanged for non-applied outcomes.

    Raises:
        RegistryCoreValidationError: If the status or state is invalid.
    """

    status: ProjectionIngestionStatus
    state: ProjectionIngestionState

    def __post_init__(self) -> None:
        """Validate application result components.

        Raises:
            RegistryCoreValidationError: If the status or state is invalid.
        """
        if not isinstance(self.status, ProjectionIngestionStatus):
            raise RegistryCoreValidationError("invalid projection ingestion application status")
        if not isinstance(self.state, ProjectionIngestionState):
            raise RegistryCoreValidationError("invalid projection ingestion application state")


@dataclass(frozen=True, slots=True)
class ProjectionIngestionState:
    """Apply immutable fixture evidence to isolated in-memory Catalog generations.

    Args:
        catalog_store: Catalog-owned projection store that receives derived records.
        processed_evidence: Exact successfully applied source evidence for duplicate detection.
        checkpoints: Latest applied source progress by resource and target generation.

    Raises:
        RegistryCoreValidationError: If state evidence is malformed, inconsistent, or ambiguous.
    """

    catalog_store: CatalogProjectionStore
    processed_evidence: tuple[ProjectionIngestionEvidence, ...] = ()
    checkpoints: tuple[ProjectionIngestionCheckpoint, ...] = ()

    def __post_init__(self) -> None:
        """Validate immutable deduplication and checkpoint state.

        Raises:
            RegistryCoreValidationError: If state evidence is malformed, inconsistent, or ambiguous.
        """
        if not isinstance(self.catalog_store, CatalogProjectionStore):
            raise RegistryCoreValidationError("invalid projection ingestion catalog store")
        if type(self.processed_evidence) is not tuple:
            raise RegistryCoreValidationError("invalid projection ingestion processed evidence")
        if type(self.checkpoints) is not tuple:
            raise RegistryCoreValidationError("invalid projection ingestion checkpoints")

        processed_event_ids: set[str] = set()
        processed_idempotency_keys: set[str] = set()
        for evidence in self.processed_evidence:
            if not isinstance(evidence, ProjectionIngestionEvidence):
                raise RegistryCoreValidationError("invalid projection ingestion processed event")
            if evidence.event_id in processed_event_ids:
                raise RegistryCoreValidationError("duplicate projection ingestion event id")
            if evidence.idempotency_key in processed_idempotency_keys:
                raise RegistryCoreValidationError("duplicate projection ingestion idempotency key")
            processed_event_ids.add(evidence.event_id)
            processed_idempotency_keys.add(evidence.idempotency_key)

        checkpoint_identities: set[tuple[CatalogGenerationKey, ResourceKey]] = set()
        for checkpoint in self.checkpoints:
            if not isinstance(checkpoint, ProjectionIngestionCheckpoint):
                raise RegistryCoreValidationError("invalid projection ingestion checkpoint")
            checkpoint_identity = (checkpoint.target_generation, checkpoint.resource_key)
            if checkpoint_identity in checkpoint_identities:
                raise RegistryCoreValidationError("duplicate projection ingestion checkpoint")
            checkpoint_identities.add(checkpoint_identity)

        catalog_projection_identities = {
            (projection.generation.key, projection.resource_key)
            for projection in self.catalog_store.projections
        }
        if not catalog_projection_identities.issubset(checkpoint_identities):
            raise RegistryCoreValidationError("catalog projection missing ingestion checkpoint")
        if not checkpoint_identities.issubset(catalog_projection_identities):
            raise RegistryCoreValidationError("ingestion checkpoint missing catalog projection")
        _validate_processed_evidence_consistency(
            self.processed_evidence,
            self.checkpoints,
            self.catalog_store.projections,
        )

    def apply_evidence(
        self,
        evidence: ProjectionIngestionEvidence,
    ) -> ProjectionIngestionApplication:
        """Evaluate one source fixture without transport, persistence, or runtime side effects.

        Args:
            evidence: Exact immutable source evidence to evaluate for one target generation.

        Returns:
            The safe decision and a new immutable state only when evidence is applied.

        Raises:
            RegistryCoreValidationError: If submitted evidence is invalid.
        """
        if not isinstance(evidence, ProjectionIngestionEvidence):
            raise RegistryCoreValidationError("invalid projection ingestion evidence")
        duplicate_status = self._get_duplicate_status(evidence)
        if duplicate_status is not None:
            return ProjectionIngestionApplication(duplicate_status, self)

        checkpoint = self._find_checkpoint(
            evidence.target_generation,
            evidence.projection.resource_key,
        )
        ordering_status = _get_ordering_status(evidence, checkpoint)
        if ordering_status is not None:
            return ProjectionIngestionApplication(ordering_status, self)
        if self._has_conflicting_projection_key(evidence):
            return ProjectionIngestionApplication(ProjectionIngestionStatus.REJECTED, self)

        replacement_projections = tuple(
            projection
            for projection in self.catalog_store.projections
            if not (
                projection.generation.key == evidence.target_generation
                and projection.resource_key == evidence.projection.resource_key
            )
        )
        updated_catalog_store = CatalogProjectionStore(
            self.catalog_store.resource_type_registry,
            self.catalog_store.namespace_registry,
            (*replacement_projections, evidence.projection),
        )
        updated_checkpoint = ProjectionIngestionCheckpoint(
            evidence.target_generation,
            evidence.projection.resource_key,
            evidence.source_sequence,
            evidence.event_id,
            evidence.idempotency_key,
        )
        return ProjectionIngestionApplication(
            ProjectionIngestionStatus.APPLIED,
            ProjectionIngestionState(
                updated_catalog_store,
                (*self.processed_evidence, evidence),
                (*self._without_checkpoint(updated_checkpoint), updated_checkpoint),
            ),
        )

    def _get_duplicate_status(
        self,
        evidence: ProjectionIngestionEvidence,
    ) -> ProjectionIngestionStatus | None:
        """Classify duplicate source evidence without mutating state.

        Args:
            evidence: Exact source evidence to compare with prior applied evidence.

        Returns:
            Duplicate or rejected status when an identity collides, otherwise `None`.
        """
        for processed_evidence in self.processed_evidence:
            event_matches = processed_evidence.event_id == evidence.event_id
            idempotency_matches = processed_evidence.idempotency_key == evidence.idempotency_key
            if event_matches or idempotency_matches:
                if processed_evidence == evidence:
                    return ProjectionIngestionStatus.DUPLICATE
                return ProjectionIngestionStatus.REJECTED
        return None

    def _has_conflicting_projection_key(self, evidence: ProjectionIngestionEvidence) -> bool:
        """Determine whether an internal projection key belongs to another resource.

        Args:
            evidence: Exact source evidence whose projection identity is being admitted.

        Returns:
            Whether the target generation already assigns the key to another resource.
        """
        return any(
            projection.generation.key == evidence.target_generation
            and projection.projection_key == evidence.projection.projection_key
            and projection.resource_key != evidence.projection.resource_key
            for projection in self.catalog_store.projections
        )

    def _find_checkpoint(
        self,
        target_generation: CatalogGenerationKey,
        resource_key: ResourceKey,
    ) -> ProjectionIngestionCheckpoint | None:
        """Find exact progress evidence for one resource in one Catalog generation.

        Args:
            target_generation: Exact target generation.
            resource_key: Exact owner-issued resource identity.

        Returns:
            The matching immutable checkpoint, if one exists.
        """
        for checkpoint in self.checkpoints:
            if (
                checkpoint.target_generation == target_generation
                and checkpoint.resource_key == resource_key
            ):
                return checkpoint
        return None

    def _without_checkpoint(
        self,
        replacement_checkpoint: ProjectionIngestionCheckpoint,
    ) -> tuple[ProjectionIngestionCheckpoint, ...]:
        """Remove the exact checkpoint that a successful application supersedes.

        Args:
            replacement_checkpoint: New checkpoint that supersedes any exact prior identity.

        Returns:
            Existing checkpoints excluding the replacement identity.
        """
        return tuple(
            checkpoint
            for checkpoint in self.checkpoints
            if (
                checkpoint.target_generation != replacement_checkpoint.target_generation
                or checkpoint.resource_key != replacement_checkpoint.resource_key
            )
        )


def _get_ordering_status(
    evidence: ProjectionIngestionEvidence,
    checkpoint: ProjectionIngestionCheckpoint | None,
) -> ProjectionIngestionStatus | None:
    """Classify source progress without inferring missing ordering evidence.

    Args:
        evidence: Exact source evidence under evaluation.
        checkpoint: Latest applied progress for the same resource/generation, if any.

    Returns:
        Stale or gap status when progress cannot be applied, otherwise `None`.
    """
    if checkpoint is None:
        if (
            evidence.source_kind is ProjectionIngestionSourceKind.EVENT
            and evidence.source_sequence != 1
        ):
            return ProjectionIngestionStatus.GAP
        return None
    if evidence.source_sequence <= checkpoint.source_sequence:
        return ProjectionIngestionStatus.STALE
    if evidence.source_sequence != checkpoint.source_sequence + 1:
        return ProjectionIngestionStatus.GAP
    return None


def _validate_processed_evidence_consistency(
    processed_evidence: tuple[ProjectionIngestionEvidence, ...],
    checkpoints: tuple[ProjectionIngestionCheckpoint, ...],
    catalog_projections: tuple[CatalogProjectionRecord, ...],
) -> None:
    """Validate that processed evidence, checkpoints, and Catalog state agree.

    Args:
        processed_evidence: Successfully applied evidence retained for duplicate detection.
        checkpoints: Latest source progress for each resource/generation pair.
        catalog_projections: Current Catalog projections for each resource/generation pair.

    Raises:
        RegistryCoreValidationError: If retained evidence cannot prove its current checkpoint and
            Catalog projection.
    """
    checkpoints_by_identity = {
        (checkpoint.target_generation, checkpoint.resource_key): checkpoint
        for checkpoint in checkpoints
    }
    catalog_by_identity = {
        (projection.generation.key, projection.resource_key): projection
        for projection in catalog_projections
    }
    evidence_by_identity: dict[
        tuple[CatalogGenerationKey, ResourceKey], list[ProjectionIngestionEvidence]
    ] = {}
    evidence_sequences: set[tuple[CatalogGenerationKey, ResourceKey, int]] = set()
    for evidence in processed_evidence:
        identity = (evidence.target_generation, evidence.projection.resource_key)
        sequence_identity = (*identity, evidence.source_sequence)
        if sequence_identity in evidence_sequences:
            raise RegistryCoreValidationError("duplicate projection ingestion processed sequence")
        evidence_sequences.add(sequence_identity)
        evidence_by_identity.setdefault(identity, []).append(evidence)

    if not set(checkpoints_by_identity).issubset(evidence_by_identity):
        raise RegistryCoreValidationError("ingestion checkpoint missing processed evidence")

    for identity, evidences in evidence_by_identity.items():
        ordered_evidence = tuple(sorted(evidences, key=lambda evidence: evidence.source_sequence))
        _validate_processed_evidence_sequence(ordered_evidence)
        latest_evidence = ordered_evidence[-1]
        checkpoint = checkpoints_by_identity.get(identity)
        if checkpoint is None:
            raise RegistryCoreValidationError("processed projection evidence missing checkpoint")
        if (
            checkpoint.source_sequence != latest_evidence.source_sequence
            or checkpoint.event_id != latest_evidence.event_id
            or checkpoint.idempotency_key != latest_evidence.idempotency_key
        ):
            raise RegistryCoreValidationError(
                "ingestion checkpoint does not match processed evidence"
            )
        if catalog_by_identity.get(identity) != latest_evidence.projection:
            raise RegistryCoreValidationError(
                "catalog projection does not match processed evidence"
            )


def _validate_processed_evidence_sequence(
    ordered_evidence: tuple[ProjectionIngestionEvidence, ...],
) -> None:
    """Validate contiguous retained source progress for one resource/generation stream.

    Args:
        ordered_evidence: Applied evidence ordered by strictly increasing source sequence.

    Raises:
        RegistryCoreValidationError: If the initial evidence or any subsequent sequence has a gap.
    """
    first_evidence = ordered_evidence[0]
    if (
        first_evidence.source_kind is ProjectionIngestionSourceKind.EVENT
        and first_evidence.source_sequence != 1
    ):
        raise RegistryCoreValidationError("projection ingestion initial event sequence is not one")
    for previous_evidence, current_evidence in pairwise(ordered_evidence):
        if current_evidence.source_sequence != previous_evidence.source_sequence + 1:
            raise RegistryCoreValidationError("projection ingestion processed sequence gap")


def _is_non_blank_plain_string(candidate_value: object) -> bool:
    """Determine whether a value is an opaque non-blank plain string.

    Args:
        candidate_value: Value to validate.

    Returns:
        Whether the value is a non-blank plain string.
    """
    return type(candidate_value) is str and bool(candidate_value.strip())
