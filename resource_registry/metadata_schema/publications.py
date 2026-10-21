"""Define immutable metadata schema publication and active-selection contracts."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import TypeVar

from resource_registry.registry_core import MetadataSchemaKey

from .definitions import (
    MetadataCompatibilityStatus,
    MetadataSchemaDefinition,
    MetadataSchemaDefinitionRegistry,
    MetadataSchemaValidationError,
)
from .errors import MetadataSchemaPublicationValidationError

__all__ = (  # noqa: RUF022
    "MetadataSchemaPublicationValidationError",
    "MetadataSchemaPublicationStatus",
    "MetadataSchemaPublication",
    "MetadataSchemaActiveSelection",
    "MetadataSchemaPublicationResult",
    "MetadataSchemaActivationResult",
    "MetadataSchemaPublicationChange",
    "MetadataSchemaActivationChange",
    "MetadataSchemaPublicationRegistry",
)

PublicationItem = TypeVar("PublicationItem")


class MetadataSchemaPublicationStatus(str, Enum):
    """Represent the sole terminal metadata schema publication state."""

    PUBLISHED = "published"


@dataclass(frozen=True, slots=True, kw_only=True)
class MetadataSchemaPublication:
    """Record one immutable published metadata schema definition.

    Args:
        definition: Exact S2 definition being published.
        status: Terminal S3 publication status.

    Raises:
        MetadataSchemaPublicationValidationError: If the publication value is invalid.
    """

    definition: MetadataSchemaDefinition
    status: MetadataSchemaPublicationStatus

    def __post_init__(self) -> None:
        """Validate the immutable publication value.

        Raises:
            MetadataSchemaPublicationValidationError: If the definition or status is invalid.
        """
        if not isinstance(self.definition, MetadataSchemaDefinition):
            raise MetadataSchemaPublicationValidationError(
                "invalid metadata schema publication registry"
            )
        if self.status is not MetadataSchemaPublicationStatus.PUBLISHED:
            raise MetadataSchemaPublicationValidationError(
                "invalid metadata schema publication transition"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class MetadataSchemaActiveSelection:
    """Select one exact published version as active for a schema.

    Args:
        schema: Exact schema whose active selection is represented.
        schema_version: Exact opaque version of the active publication.
        revision: Per-schema active-selection compare-and-set revision.

    Raises:
        MetadataSchemaPublicationValidationError: If a selection field is invalid.
    """

    schema: MetadataSchemaKey
    schema_version: str
    revision: int

    def __post_init__(self) -> None:
        """Validate selection identity and revision in field order.

        Raises:
            MetadataSchemaPublicationValidationError: If a selection field is invalid.
        """
        _validate_schema_identity(self.schema)
        _validate_schema_version(self.schema_version)
        _validate_revision(self.revision)


@dataclass(frozen=True, slots=True, kw_only=True)
class MetadataSchemaPublicationResult:
    """Report one publication outcome without exposing mutable state.

    Args:
        publication: Published immutable definition.
        created: Whether this operation added the publication.

    Raises:
        MetadataSchemaPublicationValidationError: If the result is malformed.
    """

    publication: MetadataSchemaPublication
    created: bool

    def __post_init__(self) -> None:
        """Validate publication result fields.

        Raises:
            MetadataSchemaPublicationValidationError: If the result is malformed.
        """
        if (
            not isinstance(self.publication, MetadataSchemaPublication)
            or type(self.created) is not bool
        ):
            raise MetadataSchemaPublicationValidationError(
                "invalid metadata schema publication registry"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class MetadataSchemaActivationResult:
    """Report one active-selection outcome without exposing mutable state.

    Args:
        selection: Immutable resulting active selection.
        changed: Whether this operation changed the selected version.

    Raises:
        MetadataSchemaPublicationValidationError: If the result is malformed.
    """

    selection: MetadataSchemaActiveSelection
    changed: bool

    def __post_init__(self) -> None:
        """Validate activation result fields.

        Raises:
            MetadataSchemaPublicationValidationError: If the result is malformed.
        """
        if (
            not isinstance(self.selection, MetadataSchemaActiveSelection)
            or type(self.changed) is not bool
        ):
            raise MetadataSchemaPublicationValidationError(
                "invalid metadata schema publication registry"
            )


@dataclass(frozen=True, slots=True, init=False)
class MetadataSchemaPublicationRegistry:
    """Publish exact S2 definitions and select active versions in memory.

    Args:
        definition_registry: Exact immutable S2 definition resolver.
        publications: Publications in deterministic input order.
        active_selections: Active selections in deterministic input order.

    Raises:
        MetadataSchemaPublicationValidationError: If registry input is malformed or inconsistent.
    """

    _definition_registry: MetadataSchemaDefinitionRegistry
    _publications: tuple[MetadataSchemaPublication, ...]
    _active_selections: tuple[MetadataSchemaActiveSelection, ...]
    _publications_by_identity: Mapping[tuple[MetadataSchemaKey, str], MetadataSchemaPublication] = (
        field(compare=False, hash=False, repr=False)
    )
    _active_selections_by_schema: Mapping[MetadataSchemaKey, MetadataSchemaActiveSelection] = field(
        compare=False, hash=False, repr=False
    )

    def __init__(
        self,
        *,
        definition_registry: MetadataSchemaDefinitionRegistry,
        publications: Iterable[MetadataSchemaPublication] = (),
        active_selections: Iterable[MetadataSchemaActiveSelection] = (),
    ) -> None:
        """Snapshot and validate all publication state before exposure.

        Args:
            definition_registry: Exact immutable S2 definition resolver.
            publications: Publications in deterministic input order.
            active_selections: Active selections in deterministic input order.

        Raises:
            MetadataSchemaPublicationValidationError: If registry input is malformed or
                inconsistent.
        """
        if not isinstance(definition_registry, MetadataSchemaDefinitionRegistry):
            raise MetadataSchemaPublicationValidationError(
                "invalid metadata schema publication registry"
            )
        publication_snapshot = _snapshot_iterable(publications)
        selection_snapshot = _snapshot_iterable(active_selections)
        publication_index: dict[tuple[MetadataSchemaKey, str], MetadataSchemaPublication] = {}
        for publication in publication_snapshot:
            if not isinstance(publication, MetadataSchemaPublication):
                raise MetadataSchemaPublicationValidationError(
                    "invalid metadata schema publication registry"
                )
            _validate_registered_definition(definition_registry, publication.definition)
            identity = (publication.definition.schema, publication.definition.schema_version)
            if identity in publication_index:
                raise MetadataSchemaPublicationValidationError(
                    "duplicate metadata schema publication"
                )
            publication_index[identity] = publication

        selection_index: dict[MetadataSchemaKey, MetadataSchemaActiveSelection] = {}
        for selection in selection_snapshot:
            if not isinstance(selection, MetadataSchemaActiveSelection):
                raise MetadataSchemaPublicationValidationError(
                    "invalid metadata schema publication registry"
                )
            if selection.schema in selection_index:
                raise MetadataSchemaPublicationValidationError(
                    "conflicting metadata schema active selection"
                )
            selection_index[selection.schema] = selection

        for selection in selection_snapshot:
            selected_publication = publication_index.get(
                (selection.schema, selection.schema_version)
            )
            if selected_publication is None:
                if any(
                    candidate.definition.schema_version == selection.schema_version
                    for candidate in publication_snapshot
                ):
                    raise MetadataSchemaPublicationValidationError(
                        "invalid metadata schema active selection"
                    )
                raise MetadataSchemaPublicationValidationError(
                    "unknown metadata schema active version"
                )

        object.__setattr__(self, "_definition_registry", definition_registry)
        object.__setattr__(self, "_publications", publication_snapshot)
        object.__setattr__(self, "_active_selections", selection_snapshot)
        object.__setattr__(self, "_publications_by_identity", MappingProxyType(publication_index))
        object.__setattr__(self, "_active_selections_by_schema", MappingProxyType(selection_index))

    @property
    def publications(self) -> tuple[MetadataSchemaPublication, ...]:
        """Return publications in deterministic registration order.

        Returns:
            Immutable publication snapshot.
        """
        return self._publications

    @property
    def active_selections(self) -> tuple[MetadataSchemaActiveSelection, ...]:
        """Return active selections in deterministic first-selection order.

        Returns:
            Immutable active-selection snapshot.
        """
        return self._active_selections

    def resolve_publication(
        self, schema: MetadataSchemaKey, schema_version: str
    ) -> MetadataSchemaPublication:
        """Resolve one exact published definition.

        Args:
            schema: Exact schema identity.
            schema_version: Exact opaque published version.

        Returns:
            Matching immutable publication.

        Raises:
            MetadataSchemaPublicationValidationError: If input is invalid or unpublished.
        """
        _validate_schema_identity(schema)
        _validate_schema_version(schema_version)
        try:
            return self._publications_by_identity[(schema, schema_version)]
        except KeyError as error:
            raise MetadataSchemaPublicationValidationError(
                "unknown metadata schema publication"
            ) from error

    def resolve_active(self, schema: MetadataSchemaKey) -> MetadataSchemaActiveSelection:
        """Resolve one exact schema's active selection.

        Args:
            schema: Exact schema identity.

        Returns:
            Matching immutable active selection.

        Raises:
            MetadataSchemaPublicationValidationError: If input is invalid or unselected.
        """
        _validate_schema_identity(schema)
        try:
            return self._active_selections_by_schema[schema]
        except KeyError as error:
            raise MetadataSchemaPublicationValidationError(
                "unknown metadata schema active selection"
            ) from error

    def publish(
        self, schema: MetadataSchemaKey, schema_version: str
    ) -> MetadataSchemaPublicationChange:
        """Publish one exact S2 definition without activating it.

        Args:
            schema: Exact schema identity.
            schema_version: Exact opaque definition version.

        Returns:
            Immutable replacement registry and publication result.

        Raises:
            MetadataSchemaPublicationValidationError: If input is invalid or the definition is
                absent.
        """
        _validate_schema_identity(schema)
        _validate_schema_version(schema_version)
        definition = _resolve_definition(self._definition_registry, schema, schema_version)
        existing = self._publications_by_identity.get((schema, schema_version))
        if existing is not None:
            return MetadataSchemaPublicationChange(
                registry=self,
                result=MetadataSchemaPublicationResult(publication=existing, created=False),
            )
        publication = MetadataSchemaPublication(
            definition=definition, status=MetadataSchemaPublicationStatus.PUBLISHED
        )
        registry = MetadataSchemaPublicationRegistry(
            definition_registry=self._definition_registry,
            publications=(*self._publications, publication),
            active_selections=self._active_selections,
        )
        return MetadataSchemaPublicationChange(
            registry=registry,
            result=MetadataSchemaPublicationResult(publication=publication, created=True),
        )

    def activate(
        self, schema: MetadataSchemaKey, schema_version: str, expected_revision: int
    ) -> MetadataSchemaActivationChange:
        """Select one exact publication as active with per-schema compare-and-set semantics.

        Args:
            schema: Exact schema identity.
            schema_version: Exact opaque published version.
            expected_revision: Expected per-schema selection revision.

        Returns:
            Immutable replacement registry and activation result.

        Raises:
            MetadataSchemaPublicationValidationError: If input, publication state, compatibility,
                or compare-and-set state is invalid.
        """
        _validate_schema_identity(schema)
        _validate_schema_version(schema_version)
        _validate_revision(expected_revision)
        _resolve_definition(self._definition_registry, schema, schema_version)
        publication = self._publications_by_identity.get((schema, schema_version))
        if publication is None:
            raise MetadataSchemaPublicationValidationError(
                "unpublished metadata schema active version"
            )
        current_selection = self._active_selections_by_schema.get(schema)
        if current_selection is None:
            if expected_revision != 0:
                raise MetadataSchemaPublicationValidationError(
                    "metadata schema publication concurrency conflict"
                )
            selection = MetadataSchemaActiveSelection(
                schema=schema, schema_version=schema_version, revision=0
            )
            registry = MetadataSchemaPublicationRegistry(
                definition_registry=self._definition_registry,
                publications=self._publications,
                active_selections=(*self._active_selections, selection),
            )
            return MetadataSchemaActivationChange(
                registry=registry,
                result=MetadataSchemaActivationResult(selection=selection, changed=True),
            )
        if expected_revision != current_selection.revision:
            raise MetadataSchemaPublicationValidationError(
                "metadata schema publication concurrency conflict"
            )
        if current_selection.schema_version == schema_version:
            return MetadataSchemaActivationChange(
                registry=self,
                result=MetadataSchemaActivationResult(selection=current_selection, changed=False),
            )
        _validate_replacement_compatibility(
            definition_registry=self._definition_registry,
            current_selection=current_selection,
            candidate=publication.definition,
        )
        selection = MetadataSchemaActiveSelection(
            schema=schema,
            schema_version=schema_version,
            revision=current_selection.revision + 1,
        )
        registry = MetadataSchemaPublicationRegistry(
            definition_registry=self._definition_registry,
            publications=self._publications,
            active_selections=tuple(
                selection if existing.schema == schema else existing
                for existing in self._active_selections
            ),
        )
        return MetadataSchemaActivationChange(
            registry=registry,
            result=MetadataSchemaActivationResult(selection=selection, changed=True),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class MetadataSchemaPublicationChange:
    """Return immutable publication replacement state and outcome.

    Args:
        registry: Resulting immutable publication registry.
        result: Publication outcome.

    Raises:
        MetadataSchemaPublicationValidationError: If the change is malformed.
    """

    registry: MetadataSchemaPublicationRegistry
    result: MetadataSchemaPublicationResult

    def __post_init__(self) -> None:
        """Validate publication change fields.

        Raises:
            MetadataSchemaPublicationValidationError: If the change is malformed.
        """
        if not isinstance(self.registry, MetadataSchemaPublicationRegistry) or not isinstance(
            self.result, MetadataSchemaPublicationResult
        ):
            raise MetadataSchemaPublicationValidationError(
                "invalid metadata schema publication registry"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class MetadataSchemaActivationChange:
    """Return immutable activation replacement state and outcome.

    Args:
        registry: Resulting immutable publication registry.
        result: Activation outcome.

    Raises:
        MetadataSchemaPublicationValidationError: If the change is malformed.
    """

    registry: MetadataSchemaPublicationRegistry
    result: MetadataSchemaActivationResult

    def __post_init__(self) -> None:
        """Validate activation change fields.

        Raises:
            MetadataSchemaPublicationValidationError: If the change is malformed.
        """
        if not isinstance(self.registry, MetadataSchemaPublicationRegistry) or not isinstance(
            self.result, MetadataSchemaActivationResult
        ):
            raise MetadataSchemaPublicationValidationError(
                "invalid metadata schema publication registry"
            )


def _resolve_definition(
    definition_registry: MetadataSchemaDefinitionRegistry,
    schema: MetadataSchemaKey,
    schema_version: str,
) -> MetadataSchemaDefinition:
    """Resolve one exact S2 definition with S3-owned errors.

    Args:
        definition_registry: Exact S2 definition resolver.
        schema: Exact schema identity.
        schema_version: Exact opaque schema version.

    Returns:
        Matching immutable S2 definition.

    Raises:
        MetadataSchemaPublicationValidationError: If no exact definition exists.
    """
    try:
        return definition_registry.resolve(schema, schema_version)
    except MetadataSchemaValidationError as error:
        raise MetadataSchemaPublicationValidationError(
            "unknown metadata schema definition"
        ) from error


def _validate_registered_definition(
    definition_registry: MetadataSchemaDefinitionRegistry, definition: MetadataSchemaDefinition
) -> None:
    """Verify that a publication references its registry's exact S2 definition.

    Args:
        definition_registry: Exact S2 definition resolver.
        definition: Candidate immutable definition.

    Raises:
        MetadataSchemaPublicationValidationError: If the definition is unregistered or inconsistent.
    """
    resolved_definition = _resolve_definition(
        definition_registry, definition.schema, definition.schema_version
    )
    if resolved_definition != definition:
        raise MetadataSchemaPublicationValidationError("unknown metadata schema definition")


def _validate_replacement_compatibility(
    *,
    definition_registry: MetadataSchemaDefinitionRegistry,
    current_selection: MetadataSchemaActiveSelection,
    candidate: MetadataSchemaDefinition,
) -> None:
    """Fail closed unless S2 permits one active-version replacement.

    Args:
        definition_registry: Exact S2 definition resolver.
        current_selection: Current exact active selection.
        candidate: Exact published candidate definition.

    Raises:
        MetadataSchemaPublicationValidationError: If compatibility rejects or cannot assess the
            replacement.
    """
    try:
        assessment = definition_registry.assess_writer_compatibility(
            base_schema=current_selection.schema,
            base_schema_version=current_selection.schema_version,
            candidate_schema=candidate.schema,
            candidate_schema_version=candidate.schema_version,
        )
        status = assessment.status
    except (AttributeError, MetadataSchemaValidationError) as error:
        raise MetadataSchemaPublicationValidationError(
            "invalid metadata schema publication transition"
        ) from error
    if status is MetadataCompatibilityStatus.COMPATIBLE:
        return
    if status is MetadataCompatibilityStatus.INCOMPATIBLE:
        raise MetadataSchemaPublicationValidationError(
            "incompatible metadata schema active version"
        )
    if status is MetadataCompatibilityStatus.REVIEW_REQUIRED:
        raise MetadataSchemaPublicationValidationError(
            "metadata schema active version review required"
        )
    raise MetadataSchemaPublicationValidationError("invalid metadata schema publication transition")


def _snapshot_iterable(candidate: Iterable[PublicationItem]) -> tuple[PublicationItem, ...]:
    """Eagerly snapshot a permitted registry iterable.

    Args:
        candidate: Candidate iterable to snapshot.

    Returns:
        Immutable tuple snapshot.

    Raises:
        MetadataSchemaPublicationValidationError: If the input is not an allowed iterable.
    """
    if (
        candidate is None
        or isinstance(candidate, str | bytes | Mapping)
        or not isinstance(candidate, Iterable)
    ):
        raise MetadataSchemaPublicationValidationError(
            "invalid metadata schema publication registry"
        )
    try:
        return tuple(candidate)
    except (TypeError, ValueError):
        raise MetadataSchemaPublicationValidationError(
            "invalid metadata schema publication registry"
        ) from None


def _validate_schema_identity(schema: object) -> None:
    """Validate one exact schema identity.

    Args:
        schema: Candidate schema identity.

    Raises:
        MetadataSchemaPublicationValidationError: If the schema is invalid.
    """
    if not isinstance(schema, MetadataSchemaKey):
        raise MetadataSchemaPublicationValidationError("invalid metadata schema key")


def _validate_schema_version(schema_version: object) -> None:
    """Validate one exact opaque schema version.

    Args:
        schema_version: Candidate opaque version.

    Raises:
        MetadataSchemaPublicationValidationError: If the version is invalid.
    """
    if type(schema_version) is not str or not schema_version.strip():
        raise MetadataSchemaPublicationValidationError("invalid metadata schema version")


def _validate_revision(revision: object) -> None:
    """Validate one non-negative per-schema selection revision.

    Args:
        revision: Candidate selection revision.

    Raises:
        MetadataSchemaPublicationValidationError: If the revision is invalid.
    """
    if type(revision) is not int or revision < 0:
        raise MetadataSchemaPublicationValidationError("invalid metadata schema active revision")
