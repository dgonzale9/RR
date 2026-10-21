"""Define pure immutable, non-authoritative capability Catalog projections."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from resource_registry.capability_definitions import CapabilityContractRef
from resource_registry.registry_core import OwnerDescriptor

from .errors import CapabilityCatalogProjectionValidationError

__all__ = (  # noqa: RUF022
    "CapabilityCatalogProjectionKey",
    "CapabilityAvailabilityObservation",
    "CapabilityCatalogDescriptor",
    "CapabilityCatalogProjectionSet",
)

_MAXIMUM_REFERENCE_LENGTH = 512


@dataclass(frozen=True, slots=True)
class CapabilityCatalogProjectionKey:
    """Identify one exact Catalog-owned capability descriptor projection.

    Args:
        value: Exact nonblank Catalog projection identity without normalization.

    Raises:
        CapabilityCatalogProjectionValidationError: If the key is invalid.
    """

    value: str

    def __post_init__(self) -> None:
        """Validate the Catalog-only projection identity.

        Raises:
            CapabilityCatalogProjectionValidationError: If the key is invalid.
        """
        if not _is_valid_opaque_reference(self.value):
            raise CapabilityCatalogProjectionValidationError(
                "invalid capability catalog projection key"
            )


@dataclass(frozen=True, slots=True)
class CapabilityAvailabilityObservation:
    """Carry safe freshness evidence without asserting live availability or eligibility.

    Args:
        category: Exact opaque safe observation category supplied by its owner.
        observed_at: UTC instant at which the owner observed the category.
        expires_at: UTC instant after which the observation is stale.

    Raises:
        CapabilityCatalogProjectionValidationError: If observation evidence is invalid.
    """

    category: str
    observed_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        """Validate observation freshness evidence without evaluating health or eligibility.

        Raises:
            CapabilityCatalogProjectionValidationError: If observation evidence is invalid.
        """
        if not _is_valid_opaque_reference(self.category):
            raise CapabilityCatalogProjectionValidationError(
                "invalid capability availability category"
            )
        if not _is_utc_datetime(self.observed_at):
            raise CapabilityCatalogProjectionValidationError(
                "invalid capability availability observed at"
            )
        if not _is_utc_datetime(self.expires_at) or self.expires_at <= self.observed_at:
            raise CapabilityCatalogProjectionValidationError(
                "invalid capability availability expires at"
            )


@dataclass(frozen=True, slots=True)
class CapabilityCatalogDescriptor:
    """Carry immutable safe capability discovery evidence without authority or execution behavior.

    Args:
        projection_key: Exact Catalog-only identity for this descriptor.
        contract_ref: Exact portable capability contract represented by the descriptor.
        source_authority: Owner descriptor that supplied the projected evidence.
        projection_revision: Exact opaque projection revision.
        source_event_position: Exact opaque source progress evidence.
        projected_at: UTC instant at which Catalog accepted the descriptor.
        availability_observation: Safe non-authoritative freshness evidence.
        binding_references: Ordered opaque safe summaries of existing owner-issued bindings.
        narrowing_constraint_references: Ordered opaque safe Governance/Entitlement summaries.

    Raises:
        CapabilityCatalogProjectionValidationError: If descriptor evidence is malformed.
    """

    projection_key: CapabilityCatalogProjectionKey
    contract_ref: CapabilityContractRef
    source_authority: OwnerDescriptor
    projection_revision: str
    source_event_position: str
    projected_at: datetime
    availability_observation: CapabilityAvailabilityObservation
    binding_references: tuple[str, ...] = ()
    narrowing_constraint_references: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Validate descriptor evidence without resolving owner facts or discovery authority.

        Raises:
            CapabilityCatalogProjectionValidationError: If descriptor evidence is invalid.
        """
        if not isinstance(self.projection_key, CapabilityCatalogProjectionKey):
            raise CapabilityCatalogProjectionValidationError(
                "invalid capability catalog descriptor key"
            )
        if not isinstance(self.contract_ref, CapabilityContractRef):
            raise CapabilityCatalogProjectionValidationError(
                "invalid capability catalog descriptor contract"
            )
        if not isinstance(self.source_authority, OwnerDescriptor):
            raise CapabilityCatalogProjectionValidationError(
                "invalid capability catalog descriptor source"
            )
        if not _is_valid_opaque_reference(self.projection_revision):
            raise CapabilityCatalogProjectionValidationError(
                "invalid capability catalog descriptor revision"
            )
        if not _is_valid_opaque_reference(self.source_event_position):
            raise CapabilityCatalogProjectionValidationError(
                "invalid capability catalog descriptor event position"
            )
        if not _is_utc_datetime(self.projected_at):
            raise CapabilityCatalogProjectionValidationError(
                "invalid capability catalog descriptor projected at"
            )
        if not isinstance(self.availability_observation, CapabilityAvailabilityObservation):
            raise CapabilityCatalogProjectionValidationError(
                "invalid capability catalog availability observation"
            )
        if not _are_valid_safe_references(self.binding_references):
            raise CapabilityCatalogProjectionValidationError(
                "invalid capability catalog binding references"
            )
        if not _are_valid_safe_references(self.narrowing_constraint_references):
            raise CapabilityCatalogProjectionValidationError(
                "invalid capability catalog narrowing references"
            )


@dataclass(frozen=True, slots=True)
class CapabilityCatalogProjectionSet:
    """Validate immutable exact capability Catalog descriptors in declaration order.

    Args:
        descriptors: Exact immutable descriptors in declaration order.

    Raises:
        CapabilityCatalogProjectionValidationError: If descriptors are malformed or duplicate.
    """

    descriptors: tuple[CapabilityCatalogDescriptor, ...]

    def __post_init__(self) -> None:
        """Validate descriptor identity without providing a store, query, or selection service.

        Raises:
            CapabilityCatalogProjectionValidationError: If descriptors are invalid or duplicate.
        """
        if type(self.descriptors) is not tuple:
            raise CapabilityCatalogProjectionValidationError(
                "invalid capability catalog descriptors"
            )
        projection_keys: set[CapabilityCatalogProjectionKey] = set()
        for descriptor in self.descriptors:
            if not isinstance(descriptor, CapabilityCatalogDescriptor):
                raise CapabilityCatalogProjectionValidationError(
                    "invalid capability catalog descriptor"
                )
            if descriptor.projection_key in projection_keys:
                raise CapabilityCatalogProjectionValidationError(
                    "duplicate capability catalog projection key"
                )
            projection_keys.add(descriptor.projection_key)


def _is_valid_opaque_reference(value: object) -> bool:
    """Check whether an opaque reference is bounded without changing its exact value.

    Args:
        value: Candidate opaque reference value.

    Returns:
        Whether the value is a bounded, nonblank string.
    """
    return (
        type(value) is str and 0 < len(value) <= _MAXIMUM_REFERENCE_LENGTH and not value.isspace()
    )


def _is_utc_datetime(value: object) -> bool:
    """Check whether a value is a timezone-aware UTC datetime.

    Args:
        value: Candidate timestamp.

    Returns:
        Whether the timestamp is timezone-aware UTC.
    """
    return isinstance(value, datetime) and value.tzinfo is UTC


def _are_valid_safe_references(values: object) -> bool:
    """Check whether ordered opaque projection summaries are bounded and exact.

    Args:
        values: Candidate ordered projection summaries.

    Returns:
        Whether the summaries are an immutable tuple of opaque references.
    """
    return type(values) is tuple and all(_is_valid_opaque_reference(value) for value in values)
