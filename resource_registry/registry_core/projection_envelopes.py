"""Define immutable projection-envelope contracts for Registry Core."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .errors import RegistryCoreValidationError
from .ownership import OwnerDescriptor
from .references import OpaqueReference, _is_non_blank_string
from .version import REGISTRY_CORE_CONTRACT_VERSION

__all__ = ("ProjectionEnvelope", "ProjectionTombstoneState")


class ProjectionTombstoneState(str, Enum):
    """Classify whether a projection envelope carries present or removal evidence."""

    PRESENT = "present"
    TOMBSTONE = "tombstone"

    @classmethod
    def _missing_(cls, value: object) -> ProjectionTombstoneState | None:
        """Reject values outside the authoritative tombstone-state pair.

        Args:
            value: Candidate tombstone state.

        Raises:
            RegistryCoreValidationError: If the value is not an accepted tombstone state.
        """
        raise RegistryCoreValidationError("invalid projection tombstone state")


@dataclass(frozen=True, slots=True)
class ProjectionEnvelope:
    """Carry immutable opaque evidence for a future projection consumer.

    Args:
        contract_version: Exact Registry Core contract version for this value.
        source_owner: Declarative bounded context that issued the evidence.
        subject: Opaque subject/reference evidence.
        idempotency_key: Opaque source-issued exact deduplication token.
        ordering_token: Optional opaque source-issued ordering evidence.
        tombstone_state: Whether the envelope carries present or removal evidence.
        payload: Ordered opaque related-reference evidence.

    Raises:
        RegistryCoreValidationError: If a field does not satisfy the envelope contract.
    """

    contract_version: str
    source_owner: OwnerDescriptor
    subject: OpaqueReference
    idempotency_key: str
    ordering_token: str | None = None
    tombstone_state: ProjectionTombstoneState = ProjectionTombstoneState.PRESENT
    payload: tuple[OpaqueReference, ...] = ()

    def __post_init__(self) -> None:
        """Validate the envelope fields without storing, ordering, or resolving them.

        Raises:
            RegistryCoreValidationError: If a value violates the envelope contract.
        """
        if (
            not isinstance(self.contract_version, str)
            or isinstance(self.contract_version, Enum)
            or self.contract_version != REGISTRY_CORE_CONTRACT_VERSION
        ):
            raise RegistryCoreValidationError("unsupported projection contract version")
        if not isinstance(self.source_owner, OwnerDescriptor):
            raise RegistryCoreValidationError("invalid projection source owner")
        if not isinstance(self.subject, OpaqueReference):
            raise RegistryCoreValidationError("invalid projection subject")
        if not _is_non_blank_string(self.idempotency_key):
            raise RegistryCoreValidationError("invalid projection idempotency key")
        if self.ordering_token is not None and not _is_non_blank_string(self.ordering_token):
            raise RegistryCoreValidationError("invalid projection ordering token")
        if not isinstance(self.tombstone_state, ProjectionTombstoneState):
            raise RegistryCoreValidationError("invalid projection tombstone state")
        if not isinstance(self.payload, tuple) or not all(
            isinstance(reference, OpaqueReference) for reference in self.payload
        ):
            raise RegistryCoreValidationError("invalid projection payload")
        if self.tombstone_state is ProjectionTombstoneState.TOMBSTONE and self.payload:
            raise RegistryCoreValidationError("tombstone projection payload must be empty")
