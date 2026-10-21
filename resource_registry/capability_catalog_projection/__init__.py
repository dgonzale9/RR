"""Expose pure immutable, non-authoritative capability Catalog projections."""

from .contracts import (
    CapabilityAvailabilityObservation,
    CapabilityCatalogDescriptor,
    CapabilityCatalogProjectionKey,
    CapabilityCatalogProjectionSet,
)
from .errors import CapabilityCatalogProjectionValidationError

__all__ = (  # noqa: RUF022
    "CapabilityCatalogProjectionValidationError",
    "CapabilityCatalogProjectionKey",
    "CapabilityAvailabilityObservation",
    "CapabilityCatalogDescriptor",
    "CapabilityCatalogProjectionSet",
)
