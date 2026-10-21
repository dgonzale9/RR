"""Define immutable metadata schema bodies and pure compatibility evaluation."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import TypeAlias, TypeVar

from resource_registry.registry_core import (
    MetadataClassification,
    MetadataSchemaKey,
    RegistryCoreValidationError,
)

from .declarations import MetadataPayloadBounds, MetadataSchemaRegistry, MetadataSchemaVersion
from .errors import MetadataSchemaValidationError

__all__ = (  # noqa: RUF022
    "MetadataSchemaValidationError",
    "MetadataFieldDefinition",
    "MetadataFieldClassification",
    "MetadataClassificationProfile",
    "MetadataSchemaDefinition",
    "MetadataSchemaDefinitionRegistry",
    "MetadataCompatibilityStatus",
    "MetadataCompatibilityAssessment",
)

_FIELD_NAME_PATTERN = re.compile(r"[a-z][a-z0-9_-]*\Z", re.ASCII)
_SCALAR_TYPES = frozenset({"string", "integer", "number", "boolean"})
_BoundedType = TypeVar("_BoundedType")
FieldOwner: TypeAlias = tuple[str, str]


@dataclass(frozen=True, slots=True, kw_only=True)
class MetadataFieldDefinition:
    """Declare one top-level scalar member of a metadata schema body.

    Args:
        name: Exact lowercase logical field identity.
        scalar_type: One supported non-null scalar type.
        required: Whether a future payload must contain the field.
        allows_null: Whether a present field may hold JSON null.
        semantic_owner: Optional opaque semantic owner override.
        persistence_owner: Optional opaque persistence owner override.

    Raises:
        MetadataSchemaValidationError: If the field declaration is invalid.
    """

    name: str
    scalar_type: str
    required: bool
    allows_null: bool
    semantic_owner: str | None = None
    persistence_owner: str | None = None

    def __post_init__(self) -> None:
        """Validate the intrinsic field declaration.

        Raises:
            MetadataSchemaValidationError: If the field declaration is invalid.
        """
        if not _is_field_name(self.name):
            raise MetadataSchemaValidationError("invalid metadata schema field declaration")
        if self.scalar_type not in _SCALAR_TYPES or type(self.scalar_type) is not str:
            raise MetadataSchemaValidationError("invalid metadata schema field declaration")
        if type(self.required) is not bool or type(self.allows_null) is not bool:
            raise MetadataSchemaValidationError("invalid metadata schema field declaration")
        if (self.semantic_owner is None) != (self.persistence_owner is None):
            raise MetadataSchemaValidationError("invalid metadata schema field ownership")
        if self.semantic_owner is not None and (
            not _is_non_blank_plain_string(self.semantic_owner)
            or not _is_non_blank_plain_string(self.persistence_owner)
        ):
            raise MetadataSchemaValidationError("invalid metadata schema field ownership")


@dataclass(frozen=True, slots=True, kw_only=True)
class MetadataFieldClassification:
    """Attach one Registry Core classification to one exact field identity.

    Args:
        field_name: Exact field identity to classify.
        classification: Registry Core classification value.

    Raises:
        MetadataSchemaValidationError: If the profile entry is malformed.
    """

    field_name: str
    classification: MetadataClassification

    def __post_init__(self) -> None:
        """Validate the intrinsic profile entry.

        Raises:
            MetadataSchemaValidationError: If the profile entry is malformed.
        """
        if not _is_field_name(self.field_name):
            raise MetadataSchemaValidationError("invalid metadata schema classification profile")
        if not isinstance(self.classification, MetadataClassification):
            raise MetadataSchemaValidationError("unknown metadata classification")


@dataclass(frozen=True, slots=True, init=False)
class MetadataClassificationProfile:
    """Declare a schema classification floor and field classification entries.

    Args:
        schema_classification: Registry Core classification floor.
        field_classifications: Entries to associate with exact field identities.

    Raises:
        MetadataSchemaValidationError: If the profile input is invalid.
    """

    schema_classification: MetadataClassification
    field_classifications: tuple[MetadataFieldClassification, ...]

    def __init__(
        self,
        *,
        schema_classification: MetadataClassification,
        field_classifications: Iterable[MetadataFieldClassification],
    ) -> None:
        """Snapshot the profile entries without assigning schema-field semantics.

        Args:
            schema_classification: Registry Core classification floor.
            field_classifications: Entries to associate with exact field identities.

        Raises:
            MetadataSchemaValidationError: If the profile input is invalid.
        """
        if not isinstance(schema_classification, MetadataClassification):
            raise MetadataSchemaValidationError("unknown metadata classification")
        entries = _snapshot_iterable(
            field_classifications, "invalid metadata schema classification profile"
        )
        if not all(isinstance(entry, MetadataFieldClassification) for entry in entries):
            raise MetadataSchemaValidationError("invalid metadata schema classification profile")
        object.__setattr__(self, "schema_classification", schema_classification)
        object.__setattr__(self, "field_classifications", entries)


@dataclass(frozen=True, slots=True, init=False)
class MetadataSchemaDefinition:
    """Bind immutable fields, optional bounds, and a profile to one S1 declaration.

    Args:
        declaration: Exact S1 schema-version declaration to bind.
        fields: Top-level scalar fields in immutable schema-body order.
        bounds: Optional complete bounds narrower than the declaration bounds.
        classification_profile: Schema floor and per-field classifications.

    Raises:
        MetadataSchemaValidationError: If the schema body is invalid.
    """

    declaration: MetadataSchemaVersion
    fields: tuple[MetadataFieldDefinition, ...]
    bounds: MetadataPayloadBounds | None
    classification_profile: MetadataClassificationProfile
    _field_classifications: tuple[MetadataFieldClassification, ...] = field(repr=False)

    def __init__(
        self,
        *,
        declaration: MetadataSchemaVersion,
        fields: Iterable[MetadataFieldDefinition],
        bounds: MetadataPayloadBounds | None = None,
        classification_profile: MetadataClassificationProfile,
    ) -> None:
        """Snapshot and validate a complete immutable schema definition.

        Args:
            declaration: Exact S1 schema-version declaration to bind.
            fields: Top-level scalar fields in immutable schema-body order.
            bounds: Optional complete bounds narrower than the declaration bounds.
            classification_profile: Schema floor and per-field classifications.

        Raises:
            MetadataSchemaValidationError: If the schema body is invalid.
        """
        if not isinstance(declaration, MetadataSchemaVersion):
            raise MetadataSchemaValidationError("invalid metadata schema definition")
        field_snapshot = _snapshot_iterable(fields, "invalid metadata schema definition")
        if not field_snapshot or not all(
            isinstance(item, MetadataFieldDefinition) for item in field_snapshot
        ):
            raise MetadataSchemaValidationError("invalid metadata schema definition")
        _validate_unique_field_names(field_snapshot)
        if bounds is not None and not isinstance(bounds, MetadataPayloadBounds):
            raise MetadataSchemaValidationError("invalid metadata schema bounds")
        if bounds is not None and not _are_bounds_narrower_or_equal(bounds, declaration.bounds):
            raise MetadataSchemaValidationError("invalid metadata schema bounds")
        if not isinstance(classification_profile, MetadataClassificationProfile):
            raise MetadataSchemaValidationError("invalid metadata schema classification profile")
        ordered_profile = _validate_and_order_profile(
            classification_profile, field_snapshot, declaration
        )
        object.__setattr__(self, "declaration", declaration)
        object.__setattr__(self, "fields", field_snapshot)
        object.__setattr__(self, "bounds", bounds)
        object.__setattr__(
            self,
            "classification_profile",
            MetadataClassificationProfile(
                schema_classification=classification_profile.schema_classification,
                field_classifications=ordered_profile,
            ),
        )
        object.__setattr__(self, "_field_classifications", ordered_profile)

    @property
    def schema(self) -> MetadataSchemaKey:
        """Return the exact schema identity supplied by the bound S1 declaration.

        Returns:
            The exact immutable metadata schema identity.
        """
        return self.declaration.schema

    @property
    def schema_version(self) -> str:
        """Return the opaque schema version supplied by the bound S1 declaration.

        Returns:
            The exact opaque schema version.
        """
        return self.declaration.schema_version

    @property
    def effective_bounds(self) -> MetadataPayloadBounds:
        """Return the body bounds or the inherited S1 bounds.

        Returns:
            The immutable effective bounds snapshot.
        """
        return self.bounds or self.declaration.bounds

    def get_field_owner(self, field_name: str) -> FieldOwner:
        """Resolve one field's explicit or inherited owner pair.

        Args:
            field_name: Exact field identity.

        Returns:
            The semantic and persistence owner pair.

        Raises:
            MetadataSchemaValidationError: If the field identity is invalid or unknown.
        """
        if not _is_field_name(field_name):
            raise MetadataSchemaValidationError("invalid metadata schema field declaration")
        for field_definition in self.fields:
            if field_definition.name == field_name:
                return (
                    field_definition.semantic_owner or self.declaration.semantic_owner,
                    field_definition.persistence_owner or self.declaration.persistence_owner,
                )
        raise MetadataSchemaValidationError("unknown metadata schema field")

    def get_field_classification(self, field_name: str) -> MetadataClassification:
        """Resolve one field's Registry Core classification.

        Args:
            field_name: Exact field identity.

        Returns:
            The field's immutable Registry Core classification.

        Raises:
            MetadataSchemaValidationError: If the field identity is invalid or unknown.
        """
        if not _is_field_name(field_name):
            raise MetadataSchemaValidationError("invalid metadata schema field declaration")
        for entry in self._field_classifications:
            if entry.field_name == field_name:
                return entry.classification
        raise MetadataSchemaValidationError("unknown metadata schema field")


class MetadataCompatibilityStatus(str, Enum):
    """Represent the pure directional writer compatibility outcome."""

    COMPATIBLE = "compatible"
    INCOMPATIBLE = "incompatible"
    REVIEW_REQUIRED = "review_required"


@dataclass(frozen=True, slots=True, init=False)
class MetadataCompatibilityAssessment:
    """Carry an immutable compatibility outcome and deterministic reason codes.

    Args:
        status: Compatibility outcome for the requested writer direction.
        reasons: Ordered reason codes explaining the outcome.
    """

    status: MetadataCompatibilityStatus
    reasons: tuple[str, ...]

    def __init__(
        self,
        *,
        status: MetadataCompatibilityStatus,
        reasons: Iterable[str],
    ) -> None:
        """Snapshot an immutable compatibility outcome.

        Args:
            status: Compatibility outcome for the requested writer direction.
            reasons: Ordered reason codes explaining the outcome.

        Raises:
            MetadataSchemaValidationError: If the result value is malformed.
        """
        if not isinstance(status, MetadataCompatibilityStatus):
            raise MetadataSchemaValidationError("invalid metadata schema compatibility result")
        reason_snapshot = _snapshot_iterable(
            reasons, "invalid metadata schema compatibility result"
        )
        if not all(_is_non_blank_plain_string(reason) for reason in reason_snapshot):
            raise MetadataSchemaValidationError("invalid metadata schema compatibility result")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "reasons", reason_snapshot)


@dataclass(frozen=True, slots=True, init=False)
class MetadataSchemaDefinitionRegistry:
    """Register and resolve complete metadata schema definitions in memory.

    Args:
        declaration_registry: Existing S1 declaration resolver.
        definitions: Optional definitions in preserved registration order.

    Raises:
        MetadataSchemaValidationError: If the registry input is invalid.
    """

    _declaration_registry: MetadataSchemaRegistry
    _definitions: tuple[MetadataSchemaDefinition, ...]
    _definitions_by_identity: Mapping[tuple[MetadataSchemaKey, str], MetadataSchemaDefinition] = (
        field(compare=False, hash=False, repr=False)
    )

    def __init__(
        self,
        *,
        declaration_registry: MetadataSchemaRegistry,
        definitions: Iterable[MetadataSchemaDefinition] = (),
    ) -> None:
        """Snapshot and atomically validate definitions against an S1 registry.

        Args:
            declaration_registry: Existing S1 declaration resolver.
            definitions: Optional definitions in preserved registration order.

        Raises:
            MetadataSchemaValidationError: If the registry input is invalid.
        """
        if not isinstance(declaration_registry, MetadataSchemaRegistry):
            raise MetadataSchemaValidationError("invalid metadata schema registry")
        definition_snapshot = _snapshot_iterable(definitions, "invalid metadata schema registry")
        index: dict[tuple[MetadataSchemaKey, str], MetadataSchemaDefinition] = {}
        for definition in definition_snapshot:
            self._validate_registration(definition, index, declaration_registry)
            index[(definition.schema, definition.schema_version)] = definition
        object.__setattr__(self, "_declaration_registry", declaration_registry)
        object.__setattr__(self, "_definitions", definition_snapshot)
        object.__setattr__(self, "_definitions_by_identity", MappingProxyType(index))

    @property
    def definitions(self) -> tuple[MetadataSchemaDefinition, ...]:
        """Return definitions in deterministic registration order.

        Returns:
            The immutable definition snapshot.
        """
        return self._definitions

    def register(self, definition: MetadataSchemaDefinition) -> MetadataSchemaDefinitionRegistry:
        """Return a new registry with one complete valid definition atomically registered.

        Args:
            definition: Complete definition to validate and register.

        Returns:
            A new immutable registry containing the supplied definition.

        Raises:
            MetadataSchemaValidationError: If the definition is invalid or already registered.
        """
        self._validate_registration(
            definition, self._definitions_by_identity, self._declaration_registry
        )
        return MetadataSchemaDefinitionRegistry(
            declaration_registry=self._declaration_registry,
            definitions=(*self._definitions, definition),
        )

    def resolve(self, schema: MetadataSchemaKey, schema_version: str) -> MetadataSchemaDefinition:
        """Resolve one definition by exact schema and opaque schema-version identity.

        Args:
            schema: Exact metadata schema identity.
            schema_version: Exact non-blank opaque schema version.

        Returns:
            The immutable matching definition snapshot.

        Raises:
            MetadataSchemaValidationError: If the request is invalid or unknown.
        """
        _validate_identity(schema, schema_version)
        try:
            return self._definitions_by_identity[(schema, schema_version)]
        except KeyError as error:
            raise MetadataSchemaValidationError("unknown metadata schema definition") from error

    def assess_writer_compatibility(
        self,
        *,
        base_schema: MetadataSchemaKey,
        base_schema_version: str,
        candidate_schema: MetadataSchemaKey,
        candidate_schema_version: str,
    ) -> MetadataCompatibilityAssessment:
        """Assess whether candidate accepts every S2-valid writer payload of base.

        Args:
            base_schema: Exact base schema identity.
            base_schema_version: Exact opaque base schema version.
            candidate_schema: Exact candidate schema identity.
            candidate_schema_version: Exact opaque candidate schema version.

        Returns:
            An immutable directional writer compatibility assessment.

        Raises:
            MetadataSchemaValidationError: If the request is malformed or unresolved.
        """
        base_definition = self.resolve(base_schema, base_schema_version)
        candidate_definition = self.resolve(candidate_schema, candidate_schema_version)
        if base_definition.schema != candidate_definition.schema:
            raise MetadataSchemaValidationError("malformed metadata schema compatibility request")
        return _assess_writer_compatibility(base_definition, candidate_definition)

    @staticmethod
    def _validate_registration(
        definition: object,
        definitions_by_identity: Mapping[tuple[MetadataSchemaKey, str], MetadataSchemaDefinition],
        declaration_registry: MetadataSchemaRegistry,
    ) -> None:
        """Validate a complete definition before exposing any registry mutation.

        Args:
            definition: Candidate complete definition.
            definitions_by_identity: Existing immutable definition index.
            declaration_registry: Exact S1 declaration resolver.

        Raises:
            MetadataSchemaValidationError: If validation or duplicate detection fails.
        """
        if not isinstance(definition, MetadataSchemaDefinition):
            raise MetadataSchemaValidationError("invalid metadata schema definition")
        try:
            resolved_declaration = declaration_registry.resolve_schema_version(
                definition.schema, definition.schema_version
            )
        except RegistryCoreValidationError as error:
            raise MetadataSchemaValidationError("unknown metadata schema declaration") from error
        if resolved_declaration != definition.declaration:
            raise MetadataSchemaValidationError("invalid metadata schema definition binding")
        identity = (definition.schema, definition.schema_version)
        if identity in definitions_by_identity:
            raise MetadataSchemaValidationError("duplicate metadata schema definition")


def _assess_writer_compatibility(  # noqa: PLR0912
    base: MetadataSchemaDefinition, candidate: MetadataSchemaDefinition
) -> MetadataCompatibilityAssessment:
    """Calculate the specification-defined directional writer compatibility result.

    Args:
        base: Existing writer contract.
        candidate: Candidate writer contract.

    Returns:
        An immutable compatibility result with reasons in deterministic order.
    """
    field_reasons: list[str] = []
    has_incompatible_change = False
    has_review_required_change = False
    if base.declaration.allowed_scopes != candidate.declaration.allowed_scopes:
        raise MetadataSchemaValidationError("malformed metadata schema compatibility request")
    if base.declaration.classification != candidate.declaration.classification:
        raise MetadataSchemaValidationError("malformed metadata schema compatibility request")
    candidate_fields = {field.name: field for field in candidate.fields}
    for base_field in base.fields:
        candidate_field = candidate_fields.get(base_field.name)
        if candidate_field is None:
            field_reasons.append(f"field_removed:{base_field.name}")
            has_incompatible_change = True
            continue
        if base_field.scalar_type != candidate_field.scalar_type:
            field_reasons.append(f"scalar_type_changed:{base_field.name}")
            has_incompatible_change = True
        if not base_field.required and candidate_field.required:
            field_reasons.append(f"field_made_required:{base_field.name}")
            has_incompatible_change = True
        if base_field.allows_null and not candidate_field.allows_null:
            field_reasons.append(f"null_disallowed:{base_field.name}")
            has_incompatible_change = True
        if base.get_field_owner(base_field.name) != candidate.get_field_owner(base_field.name):
            field_reasons.append(f"owner_changed:{base_field.name}")
            has_review_required_change = True
        if base.get_field_classification(base_field.name) != candidate.get_field_classification(
            base_field.name
        ):
            field_reasons.append(f"classification_changed:{base_field.name}")
            has_review_required_change = True
    base_field_names = {field.name for field in base.fields}
    for candidate_field in candidate.fields:
        if candidate_field.name not in base_field_names and candidate_field.required:
            field_reasons.append(f"required_field_added:{candidate_field.name}")
            has_incompatible_change = True
    definition_reasons: list[str] = []
    if not _are_bounds_narrower_or_equal(base.effective_bounds, candidate.effective_bounds):
        definition_reasons.append("effective_bounds_narrowed")
        has_incompatible_change = True
    if (
        base.declaration.semantic_owner,
        base.declaration.persistence_owner,
    ) != (
        candidate.declaration.semantic_owner,
        candidate.declaration.persistence_owner,
    ):
        definition_reasons.append("schema_owner_changed")
        has_review_required_change = True
    reasons = tuple(field_reasons + definition_reasons)
    if has_incompatible_change:
        return MetadataCompatibilityAssessment(
            status=MetadataCompatibilityStatus.INCOMPATIBLE,
            reasons=reasons,
        )
    if has_review_required_change:
        return MetadataCompatibilityAssessment(
            status=MetadataCompatibilityStatus.REVIEW_REQUIRED,
            reasons=reasons,
        )
    return MetadataCompatibilityAssessment(
        status=MetadataCompatibilityStatus.COMPATIBLE, reasons=()
    )


def _validate_identity(schema: object, schema_version: object) -> None:
    """Validate an exact definition identity in documented argument order.

    Args:
        schema: Candidate schema key.
        schema_version: Candidate opaque version.

    Raises:
        MetadataSchemaValidationError: If an identity component is invalid.
    """
    if not isinstance(schema, MetadataSchemaKey):
        raise MetadataSchemaValidationError("invalid metadata schema key")
    if not _is_non_blank_plain_string(schema_version):
        raise MetadataSchemaValidationError("invalid metadata schema version")


def _validate_unique_field_names(fields: tuple[MetadataFieldDefinition, ...]) -> None:
    """Reject duplicate field identities in supplied schema-body order.

    Args:
        fields: Intrinsically valid field snapshots.

    Raises:
        MetadataSchemaValidationError: If a duplicate name is present.
    """
    seen_names: set[str] = set()
    for field_definition in fields:
        if field_definition.name in seen_names:
            raise MetadataSchemaValidationError("duplicate metadata schema field")
        seen_names.add(field_definition.name)


def _validate_and_order_profile(
    profile: MetadataClassificationProfile,
    fields: tuple[MetadataFieldDefinition, ...],
    declaration: MetadataSchemaVersion,
) -> tuple[MetadataFieldClassification, ...]:
    """Validate a complete profile and order entries by schema-body field order.

    Args:
        profile: Candidate classification profile.
        fields: Valid schema-body fields in supplied order.
        declaration: Bound S1 declaration.

    Returns:
        Profile entries ordered by schema-body field identity.

    Raises:
        MetadataSchemaValidationError: If profile values conflict with the declaration.
    """
    if profile.schema_classification != declaration.classification:
        raise MetadataSchemaValidationError("conflicting metadata schema classification")
    entries_by_field: dict[str, MetadataFieldClassification] = {}
    for entry in profile.field_classifications:
        if entry.field_name in entries_by_field:
            raise MetadataSchemaValidationError("duplicate metadata schema classification")
        entries_by_field[entry.field_name] = entry
    ordered_entries: list[MetadataFieldClassification] = []
    for field_definition in fields:
        try:
            entry = entries_by_field.pop(field_definition.name)
        except KeyError as error:
            raise MetadataSchemaValidationError(
                "missing metadata schema field classification"
            ) from error
        if entry.classification != declaration.classification:
            raise MetadataSchemaValidationError("conflicting metadata schema classification")
        ordered_entries.append(entry)
    if entries_by_field:
        raise MetadataSchemaValidationError("unknown metadata schema profile field")
    return tuple(ordered_entries)


def _are_bounds_narrower_or_equal(
    candidate: MetadataPayloadBounds, maximum: MetadataPayloadBounds
) -> bool:
    """Determine whether every candidate bound is no broader than its maximum.

    Args:
        candidate: Proposed effective bounds.
        maximum: S1 or base maximum bounds.

    Returns:
        Whether every candidate bound is less than or equal to its maximum.
    """
    return all(
        candidate_value <= maximum_value
        for candidate_value, maximum_value in zip(
            (
                candidate.max_payload_bytes,
                candidate.max_nesting_depth,
                candidate.max_object_key_count,
                candidate.max_collection_item_count,
            ),
            (
                maximum.max_payload_bytes,
                maximum.max_nesting_depth,
                maximum.max_object_key_count,
                maximum.max_collection_item_count,
            ),
            strict=True,
        )
    )


def _snapshot_iterable(candidate: Iterable[_BoundedType], message: str) -> tuple[_BoundedType, ...]:
    """Eagerly snapshot one permitted iterable into an immutable tuple.

    Args:
        candidate: Candidate iterable to snapshot.
        message: Stable domain error message.

    Returns:
        The exact immutable input snapshot.

    Raises:
        MetadataSchemaValidationError: If the candidate is not an allowed iterable.
    """
    if (
        candidate is None
        or isinstance(candidate, str | bytes | Mapping)
        or not isinstance(candidate, Iterable)
    ):
        raise MetadataSchemaValidationError(message)
    try:
        return tuple(candidate)
    except (TypeError, ValueError):
        raise MetadataSchemaValidationError(message) from None


def _is_field_name(candidate: object) -> bool:
    """Determine whether a field identity follows the approved schema-name grammar.

    Args:
        candidate: Candidate field identity.

    Returns:
        Whether the identity is lowercase ASCII and top-level.
    """
    return type(candidate) is str and bool(_FIELD_NAME_PATTERN.fullmatch(candidate))


def _is_non_blank_plain_string(candidate: object) -> bool:
    """Determine whether a candidate is a non-blank opaque string.

    Args:
        candidate: Candidate opaque value.

    Returns:
        Whether the value is a non-blank plain string.
    """
    return type(candidate) is str and bool(candidate.strip())
