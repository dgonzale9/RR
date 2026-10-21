"""Define pure provider-neutral SearchDocument builder contracts."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256

from .catalog_module import CatalogProjectionRecord
from .registry_core import RegistryCoreValidationError, ResourceTypeKey

__all__ = (
    "SearchDocument",
    "SearchDocumentBuildResult",
    "SearchDocumentBuildStatus",
    "SearchDocumentBuilder",
    "SearchDocumentField",
    "SearchDocumentFieldKind",
    "SearchDocumentSchema",
)

_MAXIMUM_DOCUMENT_BYTES = 64 * 1024
_MAXIMUM_FIELDS = 128
_MAXIMUM_FACET_FIELDS = 100
_MAXIMUM_SEARCHABLE_TEXT_BYTES = 16 * 1024
_MAXIMUM_FIELD_VALUE_LENGTH = 2048
_SCHEMA_FIELD_ARITY = 2
_MINIMUM_BASE64_VALUE_LENGTH = 16
_BASE64_VALUE_PATTERN = re.compile(
    r"^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$"
)
_SAFE_SECURITY_REFERENCE_PATTERN = re.compile(
    r"^(?:namespace|visibility|authorization-scope|policy-reference):[a-z0-9][a-z0-9._-]*$"
)
_SENSITIVE_FIELD_IDENTIFIER_PATTERN = re.compile(
    r"(?:^|[._-])(?:api[_-]?key|secret|password|credential|access[_-]?token|"
    r"refresh[_-]?token|acl)(?:$|[._-])",
    re.IGNORECASE,
)
_SENSITIVE_VALUE_PATTERN = re.compile(
    r"\b(?:api[_-]?key|secret|password|credential|access[_-]?token|"
    r"refresh[_-]?token)\b\s*[:=]",
    re.IGNORECASE,
)


class SearchDocumentFieldKind(str, Enum):
    """Classify one approved provider-neutral SearchDocument field."""

    TEXT = "text"
    FILTER = "filter"
    FACET = "facet"
    SORT = "sort"
    RANKING = "ranking"
    SECURITY = "security"

    @classmethod
    def _missing_(cls, value: object) -> SearchDocumentFieldKind | None:
        """Reject unknown SearchDocument field kinds.

        Args:
            value: Candidate field kind.

        Raises:
            RegistryCoreValidationError: If the field kind is invalid.
        """
        raise RegistryCoreValidationError("invalid search document field kind")


class SearchDocumentBuildStatus(str, Enum):
    """Classify the bounded outcome of one SearchDocument build attempt."""

    BUILT = "built"
    REJECTED = "rejected"
    QUARANTINED = "quarantined"
    OVERSIZE = "oversize"


@dataclass(frozen=True, slots=True)
class SearchDocumentField:
    """Carry one schema-approved safe value and its owner-field provenance.

    Args:
        name: Exact schema-approved field name.
        kind: Declared SearchDocument field kind.
        value: Safe bounded string value.
        source_field: Exact approved source-field provenance identifier.

    Raises:
        RegistryCoreValidationError: If a field component is invalid or unbounded.
    """

    name: str
    kind: SearchDocumentFieldKind
    value: str
    source_field: str

    def __post_init__(self) -> None:
        """Validate safe bounded field evidence.

        Raises:
            RegistryCoreValidationError: If a field component is invalid or unbounded.
        """
        if not _is_bounded_non_blank_string(self.name):
            raise RegistryCoreValidationError("invalid search document field name")
        if not isinstance(self.kind, SearchDocumentFieldKind):
            raise RegistryCoreValidationError("invalid search document field kind")
        if not _is_bounded_non_blank_string(self.value):
            raise RegistryCoreValidationError("invalid search document field value")
        if not _is_bounded_non_blank_string(self.source_field):
            raise RegistryCoreValidationError("invalid search document source field")
        if _has_disallowed_field_content(self.name, self.kind, self.value, self.source_field):
            raise RegistryCoreValidationError("unsafe search document field content")


@dataclass(frozen=True, slots=True)
class SearchDocumentSchema:
    """Declare approved SearchDocument fields for one resource type and projection schema.

    Args:
        resource_type: Exact resource type admitted by this schema.
        projection_schema_version: Exact Catalog projection schema version.
        fields: Unique exact field name/kind pairs approved for document construction.

    Raises:
        RegistryCoreValidationError: If schema evidence is invalid or ambiguous.
    """

    resource_type: ResourceTypeKey
    projection_schema_version: str
    fields: tuple[tuple[str, SearchDocumentFieldKind], ...]

    def __post_init__(self) -> None:
        """Validate explicit schema field admission.

        Raises:
            RegistryCoreValidationError: If schema evidence is invalid or ambiguous.
        """
        if not isinstance(self.resource_type, ResourceTypeKey):
            raise RegistryCoreValidationError("invalid search document schema resource type")
        if not _is_bounded_non_blank_string(self.projection_schema_version):
            raise RegistryCoreValidationError("invalid search document schema version")
        if type(self.fields) is not tuple or len(self.fields) > _MAXIMUM_FIELDS:
            raise RegistryCoreValidationError("invalid search document schema fields")
        identities: set[tuple[str, SearchDocumentFieldKind]] = set()
        for field in self.fields:
            if type(field) is not tuple or len(field) != _SCHEMA_FIELD_ARITY:
                raise RegistryCoreValidationError("invalid search document schema field")
            name, kind = field
            if (
                not _is_bounded_non_blank_string(name)
                or not isinstance(kind, SearchDocumentFieldKind)
                or _SENSITIVE_FIELD_IDENTIFIER_PATTERN.search(name) is not None
            ):
                raise RegistryCoreValidationError("invalid search document schema field")
            identity = (name, kind)
            if identity in identities:
                raise RegistryCoreValidationError("duplicate search document schema field")
            identities.add(identity)


@dataclass(frozen=True, slots=True)
class SearchDocument:
    """Carry one immutable derived Search projection without provider or query behavior.

    Args:
        document_id: Deterministic internal Search document identity.
        projection: Exact Catalog projection that provides canonical identity and evidence.
        index_schema_generation: Exact Search schema generation used to derive the document.
        fields: Safe, approved, and provenance-carrying document fields.

    Raises:
        RegistryCoreValidationError: If the document evidence is malformed.
    """

    document_id: str
    projection: CatalogProjectionRecord
    index_schema_generation: str
    fields: tuple[SearchDocumentField, ...]

    def __post_init__(self) -> None:
        """Validate immutable document evidence without provider behavior.

        Raises:
            RegistryCoreValidationError: If document evidence is invalid.
        """
        if not _is_bounded_non_blank_string(self.document_id):
            raise RegistryCoreValidationError("invalid search document identity")
        if not isinstance(self.projection, CatalogProjectionRecord):
            raise RegistryCoreValidationError("invalid search document projection")
        if not _is_bounded_non_blank_string(self.index_schema_generation):
            raise RegistryCoreValidationError("invalid search document index schema generation")
        if type(self.fields) is not tuple or len(self.fields) > _MAXIMUM_FIELDS:
            raise RegistryCoreValidationError("invalid search document fields")
        if any(not isinstance(field, SearchDocumentField) for field in self.fields):
            raise RegistryCoreValidationError("invalid search document field")


@dataclass(frozen=True, slots=True)
class SearchDocumentBuildResult:
    """Carry a non-throwing classified SearchDocument build outcome.

    Args:
        status: Classified build outcome.
        document: Built document when and only when the status is ``BUILT``.
        reason: Stable safe rejection evidence without source payloads.

    Raises:
        RegistryCoreValidationError: If the result fields conflict.
    """

    status: SearchDocumentBuildStatus
    document: SearchDocument | None
    reason: str | None = None

    def __post_init__(self) -> None:
        """Validate that successful and failed outcomes cannot be ambiguous.

        Raises:
            RegistryCoreValidationError: If status, document, or reason fields conflict.
        """
        if not isinstance(self.status, SearchDocumentBuildStatus):
            raise RegistryCoreValidationError("invalid search document build status")
        if self.status is SearchDocumentBuildStatus.BUILT:
            if not isinstance(self.document, SearchDocument) or self.reason is not None:
                raise RegistryCoreValidationError("invalid built search document result")
            return
        if self.document is not None or not _is_bounded_non_blank_string(self.reason):
            raise RegistryCoreValidationError("invalid failed search document result")


@dataclass(frozen=True, slots=True)
class SearchDocumentBuilder:
    """Build safe provider-neutral documents from explicit schemas and Catalog projections.

    Args:
        schemas: Exact approved resource-type/projection-schema declarations.
    """

    schemas: tuple[SearchDocumentSchema, ...]

    def __post_init__(self) -> None:
        """Validate unique schemas without runtime registration or I/O.

        Raises:
            RegistryCoreValidationError: If schemas are invalid or collide.
        """
        if type(self.schemas) is not tuple:
            raise RegistryCoreValidationError("invalid search document schemas")
        identities: set[tuple[ResourceTypeKey, str]] = set()
        for schema in self.schemas:
            if not isinstance(schema, SearchDocumentSchema):
                raise RegistryCoreValidationError("invalid search document schema")
            identity = (schema.resource_type, schema.projection_schema_version)
            if identity in identities:
                raise RegistryCoreValidationError("duplicate search document schema")
            identities.add(identity)

    def build_document(
        self,
        projection: CatalogProjectionRecord,
        index_schema_generation: str,
        fields: tuple[SearchDocumentField, ...],
    ) -> SearchDocument:
        """Build one bounded SearchDocument from approved Catalog evidence.

        Args:
            projection: Exact Catalog projection supplying identity, provenance, freshness, and
                tombstone state.
            index_schema_generation: Exact non-blank Search index schema generation.
            fields: Explicit schema-approved safe fields with source provenance.

        Returns:
            An immutable provider-neutral SearchDocument.

        Raises:
            RegistryCoreValidationError: If inputs are invalid, unapproved, tombstoned, or exceed
                bounds.
        """
        if not isinstance(projection, CatalogProjectionRecord):
            raise RegistryCoreValidationError("invalid search document projection")
        if not _is_bounded_non_blank_string(index_schema_generation):
            raise RegistryCoreValidationError("invalid search document index schema generation")
        schema = self._resolve_schema(projection)
        _validate_document_fields(fields, schema, projection.tombstone_state.value == "tombstone")
        document_id = _build_document_id(projection, index_schema_generation)
        document = SearchDocument(document_id, projection, index_schema_generation, fields)
        if len(_serialize_document(document)) > _MAXIMUM_DOCUMENT_BYTES:
            raise RegistryCoreValidationError("search document exceeds limit")
        return document

    def build_document_result(
        self,
        projection: CatalogProjectionRecord,
        index_schema_generation: str,
        fields: tuple[SearchDocumentField, ...],
    ) -> SearchDocumentBuildResult:
        """Build one document and classify a safe non-throwing failure outcome.

        Args:
            projection: Exact Catalog projection supplying immutable source evidence.
            index_schema_generation: Exact Search index schema generation.
            fields: Candidate approved fields with source-field provenance.

        Returns:
            A built document or a rejected, quarantined, or oversize outcome.
        """
        try:
            document = self.build_document(projection, index_schema_generation, fields)
        except RegistryCoreValidationError as error:
            reason = str(error)
            return SearchDocumentBuildResult(
                _classify_build_failure(reason),
                None,
                reason,
            )
        return SearchDocumentBuildResult(SearchDocumentBuildStatus.BUILT, document)

    def _resolve_schema(self, projection: CatalogProjectionRecord) -> SearchDocumentSchema:
        """Resolve the exact approved schema for one Catalog projection.

        Args:
            projection: Catalog projection whose type/schema require an exact schema.

        Returns:
            The matching immutable schema declaration.

        Raises:
            RegistryCoreValidationError: If no exact schema is declared.
        """
        for schema in self.schemas:
            if (
                schema.resource_type == projection.resource_key.resource_type
                and schema.projection_schema_version
                == projection.generation.projection_schema_version
            ):
                return schema
        raise RegistryCoreValidationError("unknown search document schema")


def _validate_document_fields(
    fields: tuple[SearchDocumentField, ...],
    schema: SearchDocumentSchema,
    is_tombstoned: bool,
) -> None:
    """Validate bounded, schema-approved field evidence for one document.

    Args:
        fields: Candidate safe fields with source-field provenance.
        schema: Exact schema admitting field name/kind pairs.
        is_tombstoned: Whether the projection is a removal record.

    Raises:
        RegistryCoreValidationError: If fields are malformed, unapproved, or exceed bounds.
    """
    if type(fields) is not tuple or len(fields) > _MAXIMUM_FIELDS:
        raise RegistryCoreValidationError("invalid search document fields")
    if is_tombstoned and fields:
        raise RegistryCoreValidationError("tombstoned search document has fields")
    approved_fields = set(schema.fields)
    identities: set[tuple[str, SearchDocumentFieldKind]] = set()
    searchable_text_bytes = 0
    facet_fields = 0
    for field in fields:
        if not isinstance(field, SearchDocumentField):
            raise RegistryCoreValidationError("invalid search document field")
        identity = (field.name, field.kind)
        if identity not in approved_fields:
            raise RegistryCoreValidationError("unapproved search document field")
        if identity in identities:
            raise RegistryCoreValidationError("duplicate search document field")
        identities.add(identity)
        if field.kind is SearchDocumentFieldKind.TEXT:
            searchable_text_bytes += len(field.value.encode("utf-8"))
        if field.kind is SearchDocumentFieldKind.FACET:
            facet_fields += 1
    if searchable_text_bytes > _MAXIMUM_SEARCHABLE_TEXT_BYTES:
        raise RegistryCoreValidationError("search document text exceeds limit")
    if facet_fields > _MAXIMUM_FACET_FIELDS:
        raise RegistryCoreValidationError("search document facets exceed limit")


def _build_document_id(projection: CatalogProjectionRecord, index_schema_generation: str) -> str:
    """Build a deterministic internal document identity.

    Args:
        projection: Exact Catalog projection.
        index_schema_generation: Exact Search index schema generation.

    Returns:
        A SHA-256 hexadecimal internal document identity.
    """
    material = json.dumps(
        (
            projection.generation.key.value,
            projection.projection_key.value,
            index_schema_generation,
            "resource",
        ),
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(material).hexdigest()


def _serialize_document(document: SearchDocument) -> bytes:
    """Serialize safe document evidence deterministically for bound validation.

    Args:
        document: SearchDocument whose bounded safe evidence is serialized.

    Returns:
        Canonical UTF-8 JSON bytes used only for the document-size bound.
    """
    payload = {
        "document_id": document.document_id,
        "index_schema_generation": document.index_schema_generation,
        "projection": {
            "generation": document.projection.generation.key.value,
            "projection_schema_version": document.projection.generation.projection_schema_version,
            "projection_key": document.projection.projection_key.value,
            "resource_key": {
                "namespace": str(document.projection.resource_key.namespace.value),
                "resource_type": document.projection.resource_key.resource_type.value,
                "resource_id": document.projection.resource_key.resource_id,
            },
            "source_authority": {
                "bounded_context_identifier": (
                    document.projection.source_authority.bounded_context_identifier
                ),
                "ownership_classification": (
                    document.projection.source_authority.ownership_classification.value
                ),
            },
            "projection_revision": document.projection.projection_revision,
            "source_event_position": document.projection.source_event_position,
            "projected_at": document.projection.projected_at.isoformat(),
            "source_updated_at": document.projection.source_updated_at.isoformat(),
            "tombstone_state": document.projection.tombstone_state.value,
            "tombstoned_at": (
                document.projection.tombstoned_at.isoformat()
                if document.projection.tombstoned_at is not None
                else None
            ),
            "tombstone_reason_category": document.projection.tombstone_reason_category,
        },
        "fields": [
            (field.name, field.kind.value, field.value, field.source_field)
            for field in document.fields
        ],
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _classify_build_failure(reason: str) -> SearchDocumentBuildStatus:
    """Classify a stable builder validation reason without inspecting source payloads.

    Args:
        reason: Stable error reason raised by the pure builder.

    Returns:
        The matching bounded failure outcome.
    """
    if "exceed" in reason:
        return SearchDocumentBuildStatus.OVERSIZE
    if reason in {
        "unsafe search document field content",
        "unapproved search document field",
        "unknown search document schema",
        "tombstoned search document has fields",
    }:
        return SearchDocumentBuildStatus.QUARANTINED
    return SearchDocumentBuildStatus.REJECTED


def _is_non_blank_string(value: object) -> bool:
    """Determine whether a value is a non-blank plain string.

    Args:
        value: Candidate value.

    Returns:
        Whether the candidate is a non-blank plain string.
    """
    return type(value) is str and bool(value.strip())


def _is_bounded_non_blank_string(value: object) -> bool:
    """Determine whether a value is a bounded non-blank plain string.

    Args:
        value: Candidate string value.

    Returns:
        Whether the candidate is valid and within the declared string bound.
    """
    if type(value) is not str:
        return False
    return bool(value.strip()) and len(value) <= _MAXIMUM_FIELD_VALUE_LENGTH


def _has_disallowed_field_content(
    name: str,
    kind: SearchDocumentFieldKind,
    value: str,
    source_field: str,
) -> bool:
    """Determine whether field evidence could expose protected or non-reference content.

    Args:
        name: Exact schema-approved field name.
        kind: Declared SearchDocument field kind.
        value: Candidate safe field value.
        source_field: Exact approved owner-field provenance identifier.

    Returns:
        Whether the field contains a secret marker, an ACL, encoded binary, or invalid security
        evidence.
    """
    if _SENSITIVE_FIELD_IDENTIFIER_PATTERN.search(name) is not None:
        return True
    if _SENSITIVE_FIELD_IDENTIFIER_PATTERN.search(source_field) is not None:
        return True
    if _SENSITIVE_VALUE_PATTERN.search(value) is not None:
        return True
    if _looks_like_base64(value):
        return True
    return kind is SearchDocumentFieldKind.SECURITY and (
        _SAFE_SECURITY_REFERENCE_PATTERN.fullmatch(value) is None
    )


def _looks_like_base64(value: str) -> bool:
    """Determine whether a long value has base64 encoding syntax.

    Args:
        value: Candidate bounded field value.

    Returns:
        Whether the candidate has base64 syntax and is long enough to carry encoded content.
    """
    return (
        len(value) >= _MINIMUM_BASE64_VALUE_LENGTH
        and _BASE64_VALUE_PATTERN.fullmatch(value) is not None
    )
