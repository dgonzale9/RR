"""Define pure Catalog Module projection-store contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from .identity_type_module import ResourceTypeRegistry
from .namespace_module import NamespaceRegistry
from .registry_core import (
    NamespaceKey,
    OwnerDescriptor,
    ProjectionTombstoneState,
    RegistryCoreValidationError,
    ResourceKey,
    ResourceTypeKey,
)

__all__ = (
    "CatalogGeneration",
    "CatalogGenerationKey",
    "CatalogProjectionKey",
    "CatalogProjectionRecord",
    "CatalogProjectionStore",
)


@dataclass(frozen=True, slots=True)
class CatalogGenerationKey:
    """Identify one isolated Catalog projection generation.

    Args:
        value: Exact non-blank Catalog-owned generation identifier.

    Raises:
        RegistryCoreValidationError: If the generation identifier is invalid.
    """

    value: str

    def __post_init__(self) -> None:
        """Validate the opaque generation identifier.

        Raises:
            RegistryCoreValidationError: If the generation identifier is invalid.
        """
        if not _is_non_blank_plain_string(self.value):
            raise RegistryCoreValidationError("invalid catalog generation key")


@dataclass(frozen=True, slots=True)
class CatalogGeneration:
    """Declare one isolated Catalog generation and its projection schema.

    Args:
        key: Exact Catalog-owned generation identity.
        projection_schema_version: Exact non-blank Catalog projection schema version.

    Raises:
        RegistryCoreValidationError: If a generation field is invalid.
    """

    key: CatalogGenerationKey
    projection_schema_version: str

    def __post_init__(self) -> None:
        """Validate generation identity and schema-version evidence.

        Raises:
            RegistryCoreValidationError: If a generation field is invalid.
        """
        if not isinstance(self.key, CatalogGenerationKey):
            raise RegistryCoreValidationError("invalid catalog generation key")
        if not _is_non_blank_plain_string(self.projection_schema_version):
            raise RegistryCoreValidationError("invalid catalog projection schema version")


@dataclass(frozen=True, slots=True)
class CatalogProjectionKey:
    """Identify one Catalog record without replacing owner resource identity.

    Args:
        value: Exact non-blank Catalog-owned projection identifier.

    Raises:
        RegistryCoreValidationError: If the projection identifier is invalid.
    """

    value: str

    def __post_init__(self) -> None:
        """Validate the opaque projection identifier.

        Raises:
            RegistryCoreValidationError: If the projection identifier is invalid.
        """
        if not _is_non_blank_plain_string(self.value):
            raise RegistryCoreValidationError("invalid catalog projection key")


@dataclass(frozen=True, slots=True)
class CatalogProjectionRecord:
    """Carry normalized non-authoritative evidence for one exact resource projection.

    Args:
        generation: Isolated Catalog generation that owns this record.
        projection_key: Catalog-only identity for this record within its generation.
        resource_key: Exact owner-issued canonical resource identity.
        source_authority: Declared owner that issued the projection evidence.
        projection_revision: Exact opaque revision assigned by the Catalog projection.
        source_event_position: Exact opaque source progress evidence.
        projected_at: UTC instant at which Catalog accepted the projection.
        source_updated_at: UTC instant supplied by the authoritative source.
        tombstone_state: Whether the record is present or carries removal evidence.
        tombstoned_at: UTC effective time for a tombstone, when applicable.
        tombstone_reason_category: Safe opaque removal category, when applicable.

    Raises:
        RegistryCoreValidationError: If record evidence is malformed or tombstone fields conflict.
    """

    generation: CatalogGeneration
    projection_key: CatalogProjectionKey
    resource_key: ResourceKey
    source_authority: OwnerDescriptor
    projection_revision: str
    source_event_position: str
    projected_at: datetime
    source_updated_at: datetime
    tombstone_state: ProjectionTombstoneState = ProjectionTombstoneState.PRESENT
    tombstoned_at: datetime | None = None
    tombstone_reason_category: str | None = None

    def __post_init__(self) -> None:
        """Validate immutable Catalog evidence without resolving the owner.

        Raises:
            RegistryCoreValidationError: If record evidence or tombstone fields are invalid.
        """
        if not isinstance(self.generation, CatalogGeneration):
            raise RegistryCoreValidationError("invalid catalog projection generation")
        if not isinstance(self.projection_key, CatalogProjectionKey):
            raise RegistryCoreValidationError("invalid catalog projection key")
        if not isinstance(self.resource_key, ResourceKey):
            raise RegistryCoreValidationError("invalid catalog projection resource key")
        if not isinstance(self.source_authority, OwnerDescriptor):
            raise RegistryCoreValidationError("invalid catalog projection source authority")
        if not _is_non_blank_plain_string(self.projection_revision):
            raise RegistryCoreValidationError("invalid catalog projection revision")
        if not _is_non_blank_plain_string(self.source_event_position):
            raise RegistryCoreValidationError("invalid catalog projection source event position")
        if not _is_utc_datetime(self.projected_at):
            raise RegistryCoreValidationError("invalid catalog projection projected at")
        if not _is_utc_datetime(self.source_updated_at):
            raise RegistryCoreValidationError("invalid catalog projection source updated at")
        if not isinstance(self.tombstone_state, ProjectionTombstoneState):
            raise RegistryCoreValidationError("invalid catalog projection tombstone state")
        _validate_catalog_projection_tombstone_evidence(
            self.tombstone_state,
            self.tombstoned_at,
            self.tombstone_reason_category,
        )


def _validate_catalog_projection_tombstone_evidence(
    tombstone_state: ProjectionTombstoneState,
    tombstoned_at: datetime | None,
    tombstone_reason_category: str | None,
) -> None:
    """Validate removal evidence without interpreting a source-specific reason.

    Args:
        tombstone_state: Declared present or tombstone state.
        tombstoned_at: Effective UTC time for a tombstone.
        tombstone_reason_category: Safe opaque source-provided removal category.

    Raises:
        RegistryCoreValidationError: If present or tombstone evidence conflicts.
    """
    if tombstone_state is ProjectionTombstoneState.PRESENT:
        if tombstoned_at is not None or tombstone_reason_category is not None:
            raise RegistryCoreValidationError("present catalog projection has tombstone evidence")
        return
    if not _is_utc_datetime(tombstoned_at):
        raise RegistryCoreValidationError("invalid catalog projection tombstoned at")
    if not _is_non_blank_plain_string(tombstone_reason_category):
        raise RegistryCoreValidationError("invalid catalog projection tombstone reason")


@dataclass(frozen=True, slots=True)
class CatalogProjectionStore:
    """Resolve immutable exact/list Catalog projections without owner mutation or runtime adoption.

    Args:
        resource_type_registry: Completed type declarations used to verify source ownership.
        namespace_registry: Completed namespace declarations used to admit exact resource keys.
        projections: Immutable records with unique generation/projection and
            generation/resource pairs.

    Raises:
        RegistryCoreValidationError: If dependencies or records are invalid or inconsistent.
    """

    resource_type_registry: ResourceTypeRegistry
    namespace_registry: NamespaceRegistry
    projections: tuple[CatalogProjectionRecord, ...]

    def __post_init__(self) -> None:
        """Validate store dependencies, declared ownership, and exact collision keys.

        Raises:
            RegistryCoreValidationError: If dependencies or records are invalid or inconsistent.
        """
        if not isinstance(self.resource_type_registry, ResourceTypeRegistry):
            raise RegistryCoreValidationError("invalid catalog projection type registry")
        if not isinstance(self.namespace_registry, NamespaceRegistry):
            raise RegistryCoreValidationError("invalid catalog projection namespace registry")
        if type(self.projections) is not tuple:
            raise RegistryCoreValidationError("invalid catalog projection records")

        projection_identities: set[tuple[CatalogGenerationKey, CatalogProjectionKey]] = set()
        resource_identities: set[tuple[CatalogGenerationKey, ResourceKey]] = set()
        for projection in self.projections:
            if not isinstance(projection, CatalogProjectionRecord):
                raise RegistryCoreValidationError("invalid catalog projection record")
            declared_owner = self.namespace_registry.admit_resource_key(
                projection.resource_key, self.resource_type_registry
            )
            if projection.source_authority != declared_owner:
                raise RegistryCoreValidationError("catalog projection source authority mismatch")
            projection_identity = (projection.generation.key, projection.projection_key)
            if projection_identity in projection_identities:
                raise RegistryCoreValidationError("duplicate catalog projection key")
            resource_identity = (projection.generation.key, projection.resource_key)
            if resource_identity in resource_identities:
                raise RegistryCoreValidationError("duplicate catalog projection resource")
            projection_identities.add(projection_identity)
            resource_identities.add(resource_identity)

    def resolve_projection(
        self,
        generation_key: CatalogGenerationKey,
        resource_key: ResourceKey,
    ) -> CatalogProjectionRecord:
        """Resolve one exact projection within one explicit Catalog generation.

        Args:
            generation_key: Exact generation in which to resolve the resource.
            resource_key: Exact owner-issued canonical resource identity.

        Returns:
            The immutable projection record carrying its freshness and tombstone evidence.

        Raises:
            RegistryCoreValidationError: If lookup inputs are invalid or no exact projection exists.
        """
        if not isinstance(generation_key, CatalogGenerationKey):
            raise RegistryCoreValidationError("invalid catalog projection generation key")
        if not isinstance(resource_key, ResourceKey):
            raise RegistryCoreValidationError("invalid catalog projection resource key")
        for projection in self.projections:
            if (
                projection.generation.key == generation_key
                and projection.resource_key == resource_key
            ):
                return projection
        raise RegistryCoreValidationError("unknown catalog projection")

    def list_projections(
        self,
        generation_key: CatalogGenerationKey,
        namespace: NamespaceKey | None = None,
        resource_type: ResourceTypeKey | None = None,
        include_tombstones: bool = False,
    ) -> tuple[CatalogProjectionRecord, ...]:
        """List deterministic bounded Catalog records from one explicit generation.

        Args:
            generation_key: Exact generation from which to list projections.
            namespace: Optional exact namespace filter.
            resource_type: Optional exact resource-type filter.
            include_tombstones: Whether to retain tombstoned records in the result.

        Returns:
            Projection records ordered by namespace, type, resource ID, and projection key.

        Raises:
            RegistryCoreValidationError: If list inputs are invalid.
        """
        if not isinstance(generation_key, CatalogGenerationKey):
            raise RegistryCoreValidationError("invalid catalog projection generation key")
        if namespace is not None and not isinstance(namespace, NamespaceKey):
            raise RegistryCoreValidationError("invalid catalog projection namespace filter")
        if resource_type is not None and not isinstance(resource_type, ResourceTypeKey):
            raise RegistryCoreValidationError("invalid catalog projection type filter")
        if type(include_tombstones) is not bool:
            raise RegistryCoreValidationError("invalid catalog projection tombstone inclusion")

        matching_projections = tuple(
            projection
            for projection in self.projections
            if projection.generation.key == generation_key
            and (namespace is None or projection.resource_key.namespace == namespace)
            and (resource_type is None or projection.resource_key.resource_type == resource_type)
            and (
                include_tombstones
                or projection.tombstone_state is ProjectionTombstoneState.PRESENT
            )
        )
        return tuple(sorted(matching_projections, key=_get_projection_sort_key))


def _get_projection_sort_key(
    projection: CatalogProjectionRecord,
) -> tuple[str, str, str, str]:
    """Build the deterministic list ordering key for one Catalog projection.

    Args:
        projection: Valid immutable Catalog projection to order.

    Returns:
        Namespace, type, resource, and projection identifiers in deterministic order.
    """
    return (
        str(projection.resource_key.namespace.value),
        projection.resource_key.resource_type.value,
        projection.resource_key.resource_id,
        projection.projection_key.value,
    )


def _is_non_blank_plain_string(candidate_value: object) -> bool:
    """Determine whether a value is an opaque non-blank plain string.

    Args:
        candidate_value: Value to validate.

    Returns:
        Whether the value is a non-blank plain string.
    """
    return type(candidate_value) is str and bool(candidate_value.strip())


def _is_utc_datetime(candidate_value: object) -> bool:
    """Determine whether a value is an aware UTC datetime.

    Args:
        candidate_value: Value to validate.

    Returns:
        Whether the value is an aware datetime using the UTC timezone singleton.
    """
    return isinstance(candidate_value, datetime) and candidate_value.tzinfo is UTC
