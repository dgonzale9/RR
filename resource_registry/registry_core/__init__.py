"""Expose the public boundary for pure Enterprise Resource Registry contracts.

This pure standard-library package is intentionally unused by current runtime paths. Completed Epic
0.1 and implemented ERR-0.2-S1/S2 expose approved immutable contracts from this explicit surface.
Epic 0.3 S2 adds the independently reviewed and approved pure metadata values. The package remains
unused by current runtime paths.
"""

from .errors import RegistryCoreValidationError
from .identity import NamespaceKey, ResourceKey, ResourceTypeKey
from .metadata import (
    MetadataClassification,
    MetadataEnvelope,
    MetadataNamespaceKey,
    MetadataProvenance,
    MetadataSchemaKey,
    MetadataScope,
)
from .ownership import OwnerDescriptor, OwnershipClassification
from .projection_envelopes import ProjectionEnvelope, ProjectionTombstoneState
from .references import OpaqueReference
from .version import REGISTRY_CORE_CONTRACT_VERSION
from .versioning import ArtifactKey, ResourceVersionKey

__all__ = (  # noqa: RUF022
    "REGISTRY_CORE_CONTRACT_VERSION",
    "OwnerDescriptor",
    "OwnershipClassification",
    "OpaqueReference",
    "ProjectionEnvelope",
    "ProjectionTombstoneState",
    "RegistryCoreValidationError",
    "NamespaceKey",
    "ResourceTypeKey",
    "ResourceKey",
    "ResourceVersionKey",
    "ArtifactKey",
    "MetadataNamespaceKey",
    "MetadataSchemaKey",
    "MetadataClassification",
    "MetadataScope",
    "MetadataProvenance",
    "MetadataEnvelope",
)
