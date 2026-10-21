"""Define pure provider-neutral Search port and generation contracts."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from threading import RLock
from typing import Protocol, cast

from .registry_core import RegistryCoreValidationError, ResourceKey
from .search_document_module import SearchDocument, SearchDocumentFieldKind

__all__ = (
    "InMemorySearchProvider",
    "SearchAliasKey",
    "SearchAuthorizationScopeBinding",
    "SearchDocumentWrite",
    "SearchDocumentWriteResult",
    "SearchGenerationKey",
    "SearchGenerationState",
    "SearchHealth",
    "SearchProviderCapability",
    "SearchProviderError",
    "SearchProviderErrorCode",
    "SearchProviderPort",
    "SearchQuery",
    "SearchQueryFilter",
    "SearchQueryResult",
    "SearchResultHit",
    "SearchSortDirection",
    "SearchSortField",
)

_MAXIMUM_QUERY_LENGTH = 512
_MAXIMUM_FILTERS = 20
_MAXIMUM_SORT_FIELDS = 10
_MAXIMUM_REQUESTED_CAPABILITIES = 10
_MAXIMUM_PAGE_SIZE = 100
_MAXIMUM_OPAQUE_VALUE_LENGTH = 2048
_AUTHORIZATION_SCOPE_FINGERPRINT_PATTERN = re.compile(r"^scope:sha256:[0-9a-f]{64}$")
_PROHIBITED_QUERY_PATTERN = re.compile(r"(?:[*?\[\]{}()|^$\\]|\b(?:regex|script)\b)", re.IGNORECASE)


class SearchProviderCapability(str, Enum):
    """Declare one provider-neutral Search capability."""

    LEXICAL = "lexical"
    PREFIX = "prefix"
    TYPED_FILTERS = "typed_filters"
    DETERMINISTIC_SORT = "deterministic_sort"
    PAGINATION = "pagination"
    BULK_INDEX = "bulk_index"
    GENERATIONS = "generations"
    FACETS = "facets"
    HIGHLIGHTING = "highlighting"
    SUGGESTIONS = "suggestions"
    SEMANTIC = "semantic"


class SearchGenerationState(str, Enum):
    """Classify one Search generation lifecycle state."""

    DRAFT = "draft"
    BUILDING = "building"
    VALIDATED = "validated"
    ACTIVE = "active"
    ROLLBACK_RETAINED = "rollback_retained"
    FAILED = "failed"
    RETIRED = "retired"


class SearchSortDirection(str, Enum):
    """Declare a stable provider-neutral sort direction."""

    ASCENDING = "ascending"
    DESCENDING = "descending"


class SearchProviderErrorCode(str, Enum):
    """Classify a stable Search provider failure without provider details."""

    VALIDATION = "validation"
    UNSUPPORTED_CAPABILITY = "unsupported_search_capability"
    GENERATION_SCHEMA_MISMATCH = "generation_schema_mismatch"
    STALE_OR_INVALID_CURSOR = "stale_or_invalid_cursor"
    CONFLICT = "conflict"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    INTEGRITY = "integrity"
    GENERATION_NOT_READY = "generation_not_ready"
    RETIRED_GENERATION = "retired_generation"


class SearchProviderError(RegistryCoreValidationError):
    """Report a stable provider-neutral Search failure.

    Args:
        code: Stable failure category safe for caller handling.
    """

    def __init__(self, code: SearchProviderErrorCode) -> None:
        """Initialize the error with its stable classification.

        Args:
            code: Stable failure category safe for caller handling.
        """
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True, slots=True)
class SearchGenerationKey:
    """Identify one replaceable physical Search generation.

    Args:
        value: Exact opaque generation identifier.
    """

    value: str

    def __post_init__(self) -> None:
        """Validate the opaque generation identity.

        Raises:
            RegistryCoreValidationError: If the generation identity is invalid.
        """
        _require_bounded_string(self.value, "invalid search generation key")


@dataclass(frozen=True, slots=True)
class SearchAliasKey:
    """Identify one logical alias that resolves to an active generation.

    Args:
        value: Exact opaque logical alias identifier.
    """

    value: str

    def __post_init__(self) -> None:
        """Validate the opaque logical alias identity.

        Raises:
            RegistryCoreValidationError: If the alias identity is invalid.
        """
        _require_bounded_string(self.value, "invalid search alias key")


@dataclass(frozen=True, slots=True)
class SearchAuthorizationScopeBinding:
    """Carry an opaque server-derived authorization scope binding.

    Args:
        value: Opaque scope fingerprint or reference without policy payload.
    """

    value: str

    def __post_init__(self) -> None:
        """Validate the opaque scope binding.

        Raises:
            RegistryCoreValidationError: If the scope binding is invalid.
        """
        if (
            not isinstance(self.value, str)
            or _AUTHORIZATION_SCOPE_FINGERPRINT_PATTERN.fullmatch(self.value) is None
        ):
            raise RegistryCoreValidationError("invalid search authorization scope binding")


@dataclass(frozen=True, slots=True)
class SearchQueryFilter:
    """Carry one exact typed Search filter.

    Args:
        field_name: Exact schema-approved filter field name.
        expected_value: Exact normalized value to match.
    """

    field_name: str
    expected_value: str

    def __post_init__(self) -> None:
        """Validate one bounded filter without provider syntax.

        Raises:
            RegistryCoreValidationError: If a filter component is invalid.
        """
        _require_bounded_string(self.field_name, "invalid search query filter field")
        _require_bounded_string(self.expected_value, "invalid search query filter value")


@dataclass(frozen=True, slots=True)
class SearchSortField:
    """Declare one deterministic Search sort field.

    Args:
        field_name: Exact approved sort field name or ``document_id``.
        direction: Normalized provider-neutral sort direction.
    """

    field_name: str
    direction: SearchSortDirection

    def __post_init__(self) -> None:
        """Validate the provider-neutral sort declaration.

        Raises:
            RegistryCoreValidationError: If sort evidence is invalid.
        """
        _require_bounded_string(self.field_name, "invalid search sort field")
        if not isinstance(self.direction, SearchSortDirection):
            raise RegistryCoreValidationError("invalid search sort direction")


@dataclass(frozen=True, slots=True)
class SearchQuery:
    """Describe one bounded provider-neutral Search request.

    Args:
        text: Optional lexical or prefix text input.
        filters: Exact filters that only narrow candidates.
        sort_fields: Deterministic sort declarations.
        page_size: Maximum candidate count for one page.
        cursor: Opaque continuation cursor returned by this port.
        requested_capabilities: Optional provider-neutral features requested by a caller.
    """

    text: str
    filters: tuple[SearchQueryFilter, ...] = ()
    sort_fields: tuple[SearchSortField, ...] = ()
    page_size: int = 20
    cursor: str | None = None
    requested_capabilities: tuple[SearchProviderCapability, ...] = ()

    def __post_init__(self) -> None:
        """Validate bounded query evidence without a provider DSL.

        Raises:
            RegistryCoreValidationError: If query evidence is invalid or exceeds limits.
        """
        if (
            not isinstance(self.text, str)
            or len(self.text) > _MAXIMUM_QUERY_LENGTH
            or _PROHIBITED_QUERY_PATTERN.search(self.text) is not None
        ):
            raise RegistryCoreValidationError("invalid search query text")
        if type(self.filters) is not tuple or len(self.filters) > _MAXIMUM_FILTERS:
            raise RegistryCoreValidationError("invalid search query filters")
        if any(not isinstance(item, SearchQueryFilter) for item in self.filters):
            raise RegistryCoreValidationError("invalid search query filters")
        if (
            type(self.sort_fields) is not tuple
            or len(self.sort_fields) > _MAXIMUM_SORT_FIELDS
            or any(not isinstance(item, SearchSortField) for item in self.sort_fields)
        ):
            raise RegistryCoreValidationError("invalid search sort fields")
        if type(self.page_size) is not int or not 1 <= self.page_size <= _MAXIMUM_PAGE_SIZE:
            raise RegistryCoreValidationError("invalid search page size")
        if self.cursor is not None:
            _require_bounded_string(self.cursor, "invalid search cursor")
        if (
            type(self.requested_capabilities) is not tuple
            or len(self.requested_capabilities) > _MAXIMUM_REQUESTED_CAPABILITIES
            or any(
                not isinstance(capability, SearchProviderCapability)
                for capability in self.requested_capabilities
            )
        ):
            raise RegistryCoreValidationError("invalid search requested capabilities")


@dataclass(frozen=True, slots=True)
class SearchResultHit:
    """Carry one non-authoritative normalized Search candidate.

    Args:
        document_id: Internal deterministic Search document identity.
        resource_key: Exact canonical resource reference for owner resolution.
        projection_key: Exact replaceable Catalog projection reference.
        score: Normalized non-authoritative ranking score.
        sort_values: Stable provider-neutral sort evidence.
        index_schema_generation: Exact schema generation that produced the hit.
        source_revision: Exact source revision preserved from the Catalog projection.
        source_event_position: Exact source event position preserved from the Catalog projection.
        projected_at: Exact Catalog projection timestamp.
        source_updated_at: Exact authoritative source-update timestamp.
        indexed_at: Exact timestamp when this adapter indexed the document.
    """

    document_id: str
    resource_key: ResourceKey
    projection_key: str
    score: int
    sort_values: tuple[str, ...]
    index_schema_generation: str
    source_revision: str
    source_event_position: str
    projected_at: datetime
    source_updated_at: datetime
    indexed_at: datetime

    def __post_init__(self) -> None:
        """Validate normalized Search candidate evidence."""
        _require_bounded_string(self.document_id, "invalid search result document identity")
        if not isinstance(self.resource_key, ResourceKey):
            raise RegistryCoreValidationError("invalid search result resource key")
        _require_bounded_string(self.projection_key, "invalid search result projection key")
        if type(self.score) is not int or self.score < 0:
            raise RegistryCoreValidationError("invalid search result score")
        if type(self.sort_values) is not tuple or len(self.sort_values) > _MAXIMUM_SORT_FIELDS:
            raise RegistryCoreValidationError("invalid search result sort values")
        for value in self.sort_values:
            _require_bounded_string(value, "invalid search result sort values")
        _require_bounded_string(
            self.index_schema_generation, "invalid search result schema generation"
        )
        _require_bounded_string(self.source_revision, "invalid search result source revision")
        _require_bounded_string(
            self.source_event_position, "invalid search result source event position"
        )
        if not all(
            isinstance(value, datetime) and value.tzinfo is UTC
            for value in (self.projected_at, self.source_updated_at, self.indexed_at)
        ):
            raise RegistryCoreValidationError("invalid search result freshness")


@dataclass(frozen=True, slots=True)
class SearchQueryResult:
    """Carry one deterministic page of provider-neutral Search candidates.

    Args:
        hits: Authorized-Discovery-unverified candidate hits.
        next_cursor: Opaque continuation cursor, when more candidates remain.
        generation: Exact generation queried through the logical alias.
        is_partial: Whether the adapter classified this page as partial/degraded.
    """

    hits: tuple[SearchResultHit, ...]
    next_cursor: str | None
    generation: SearchGenerationKey
    is_partial: bool = False

    def __post_init__(self) -> None:
        """Validate a bounded deterministic candidate page."""
        if (
            type(self.hits) is not tuple
            or len(self.hits) > _MAXIMUM_PAGE_SIZE
            or any(not isinstance(hit, SearchResultHit) for hit in self.hits)
        ):
            raise RegistryCoreValidationError("invalid search result hits")
        if self.next_cursor is not None:
            _require_bounded_string(self.next_cursor, "invalid search result cursor")
        if not isinstance(self.generation, SearchGenerationKey) or not isinstance(
            self.is_partial, bool
        ):
            raise RegistryCoreValidationError("invalid search query result")


@dataclass(frozen=True, slots=True)
class SearchDocumentWriteResult:
    """Classify one idempotent Search document write outcome.

    Args:
        applied: Whether the generation changed.
        generation: Exact generation receiving the attempted write.
    """

    applied: bool
    generation: SearchGenerationKey

    def __post_init__(self) -> None:
        """Validate one idempotent write outcome."""
        if not isinstance(self.applied, bool) or not isinstance(
            self.generation, SearchGenerationKey
        ):
            raise RegistryCoreValidationError("invalid search document write result")


@dataclass(frozen=True, slots=True)
class SearchDocumentWrite:
    """Carry one exact bulk-index document write request.

    Args:
        document: Approved provider-neutral Search document.
        source_revision: Monotonic source revision preventing regression.
        idempotency_key: Exact immutable idempotency evidence.
    """

    document: SearchDocument
    source_revision: int
    idempotency_key: str

    def __post_init__(self) -> None:
        """Validate bulk-index write evidence."""
        if (
            not isinstance(self.document, SearchDocument)
            or type(self.source_revision) is not int
            or self.source_revision < 1
        ):
            raise RegistryCoreValidationError("invalid search document write")
        _require_bounded_string(self.idempotency_key, "invalid search idempotency key")


@dataclass(frozen=True, slots=True)
class SearchHealth:
    """Report safe readiness evidence for one Search adapter.

    Args:
        indexing_ready: Whether indexing operations are ready.
        query_ready: Whether query operations are ready.
        generation_ready: Whether a requested generation is validated or active.
        is_degraded: Whether the adapter returned a partial/degraded read state.
    """

    indexing_ready: bool
    query_ready: bool
    generation_ready: bool
    is_degraded: bool = False

    def __post_init__(self) -> None:
        """Validate safe provider readiness evidence."""
        if not all(
            isinstance(value, bool)
            for value in (
                self.indexing_ready,
                self.query_ready,
                self.generation_ready,
                self.is_degraded,
            )
        ):
            raise RegistryCoreValidationError("invalid search health")


class SearchProviderPort(Protocol):
    """Define the provider-neutral Search adapter operations.

    Concrete adapters translate these values without leaking a provider DSL or cursor.
    """

    def create_generation(
        self, generation: SearchGenerationKey, index_schema_generation: str
    ) -> None:
        """Create one inactive generation bound to an exact schema generation."""

    def begin_generation(self, generation: SearchGenerationKey) -> None:
        """Allow document writes to one draft generation."""

    def create_or_update_schema(
        self, generation: SearchGenerationKey, index_schema_generation: str
    ) -> None:
        """Create or update an empty draft generation schema contract."""

    def index_document(
        self,
        generation: SearchGenerationKey,
        document: SearchDocument,
        source_revision: int,
        idempotency_key: str,
    ) -> SearchDocumentWriteResult:
        """Index one approved document or apply its tombstone semantics."""

    def bulk_index(
        self, generation: SearchGenerationKey, writes: tuple[SearchDocumentWrite, ...]
    ) -> tuple[SearchDocumentWriteResult, ...]:
        """Index bounded exact writes into one explicit generation."""

    def delete_document(
        self,
        generation: SearchGenerationKey,
        document_id: str,
        source_revision: int,
        idempotency_key: str,
    ) -> SearchDocumentWriteResult:
        """Delete one document using exact source revision evidence."""

    def rebuild_index(
        self, source_generation: SearchGenerationKey, target_generation: SearchGenerationKey
    ) -> None:
        """Copy deterministic documents from one generation into a building target."""

    def validate_generation(self, generation: SearchGenerationKey) -> None:
        """Validate one building generation before alias activation."""

    def activate_generation(self, alias: SearchAliasKey, generation: SearchGenerationKey) -> None:
        """Atomically resolve one logical alias to a validated generation."""

    def rollback_alias(self, alias: SearchAliasKey) -> None:
        """Atomically restore one retained predecessor generation."""

    def fail_generation(self, generation: SearchGenerationKey) -> None:
        """Mark one non-active generation as failed."""

    def retire_generation(self, generation: SearchGenerationKey) -> None:
        """Retire one non-active generation and invalidate its cursors."""

    def search(
        self,
        alias: SearchAliasKey,
        query: SearchQuery,
        scope_binding: SearchAuthorizationScopeBinding,
    ) -> SearchQueryResult:
        """Return deterministic candidates through an explicit active alias."""

    def health(self, generation: SearchGenerationKey) -> SearchHealth:
        """Return safe readiness evidence for one exact generation."""


@dataclass(slots=True)
class _StoredDocument:
    """Store internal in-memory adapter evidence for one document."""

    document: SearchDocument
    source_revision: int
    idempotency_key: str
    fingerprint: str
    indexed_at: datetime


@dataclass(slots=True)
class _GenerationRecord:
    """Store internal in-memory adapter evidence for one generation."""

    index_schema_generation: str
    state: SearchGenerationState
    documents: dict[str, _StoredDocument]
    retired_document_revisions: dict[str, int]
    retired_document_idempotency_keys: dict[str, str]
    retired_document_operation_kinds: dict[str, str]
    retired_document_fingerprints: dict[str, str]


class InMemorySearchProvider:
    """Provide a deterministic in-memory Search port conformance adapter.

    This adapter is intentionally process-local, non-durable, and prohibited as production
    authority.
    """

    def __init__(
        self,
        indexed_at_clock: Callable[[], datetime] | None = None,
        query_failure: SearchProviderErrorCode | None = None,
        return_partial: bool = False,
    ) -> None:
        """Initialize an empty isolated conformance adapter.

        Args:
            indexed_at_clock: Optional clock for deterministic index-freshness evidence.
            query_failure: Optional normalized unavailable or timeout fixture outcome.
            return_partial: Whether successful fixture queries report partial/degraded status.
        """
        if query_failure not in {
            None,
            SearchProviderErrorCode.UNAVAILABLE,
            SearchProviderErrorCode.TIMEOUT,
        }:
            raise RegistryCoreValidationError("invalid search query failure")
        if not isinstance(return_partial, bool):
            raise RegistryCoreValidationError("invalid search partial result setting")
        self._generations: dict[SearchGenerationKey, _GenerationRecord] = {}
        self._aliases: dict[SearchAliasKey, SearchGenerationKey] = {}
        self._alias_epochs: dict[SearchAliasKey, int] = {}
        self._rollback_targets: dict[SearchAliasKey, SearchGenerationKey] = {}
        self._alias_transition_lock = RLock()
        self._indexed_at_clock = indexed_at_clock or _get_current_utc_time
        self._cursor_secret = secrets.token_bytes(32)
        self._query_failure = query_failure
        self._return_partial = return_partial

    def create_generation(
        self, generation: SearchGenerationKey, index_schema_generation: str
    ) -> None:
        """Create one draft generation with an exact Search schema generation.

        Args:
            generation: Exact new physical generation identity.
            index_schema_generation: Exact SearchDocument schema generation.

        Raises:
            SearchProviderError: If the generation already exists or evidence is invalid.
        """
        _require_generation_key(generation)
        _require_provider_bounded_string(index_schema_generation)
        if generation in self._generations:
            raise SearchProviderError(SearchProviderErrorCode.CONFLICT)
        self._generations[generation] = _GenerationRecord(
            index_schema_generation,
            SearchGenerationState.DRAFT,
            {},
            {},
            {},
            {},
            {},
        )

    def begin_generation(self, generation: SearchGenerationKey) -> None:
        """Move one draft generation into its writeable building state.

        Args:
            generation: Exact draft generation identity.

        Raises:
            SearchProviderError: If the generation is missing or not draft.
        """
        record = self._get_generation(generation)
        if record.state is not SearchGenerationState.DRAFT:
            raise SearchProviderError(SearchProviderErrorCode.GENERATION_NOT_READY)
        record.state = SearchGenerationState.BUILDING

    def create_or_update_schema(
        self, generation: SearchGenerationKey, index_schema_generation: str
    ) -> None:
        """Create or update an empty draft generation schema contract.

        Args:
            generation: Exact generation receiving the schema contract.
            index_schema_generation: Exact SearchDocument schema generation.

        Raises:
            SearchProviderError: If an existing generation cannot change schema safely.
        """
        _require_generation_key(generation)
        _require_provider_bounded_string(index_schema_generation)
        existing = self._generations.get(generation)
        if existing is None:
            self.create_generation(generation, index_schema_generation)
            return
        if existing.state is not SearchGenerationState.DRAFT or existing.documents:
            raise SearchProviderError(SearchProviderErrorCode.GENERATION_NOT_READY)
        existing.index_schema_generation = index_schema_generation

    def index_document(  # noqa: PLR0911, PLR0912
        self,
        generation: SearchGenerationKey,
        document: SearchDocument,
        source_revision: int,
        idempotency_key: str,
    ) -> SearchDocumentWriteResult:
        """Index one approved document or delete it when it is tombstoned.

        Args:
            generation: Exact building generation receiving the write.
            document: Exact approved provider-neutral Search document.
            source_revision: Monotonic source revision used to prevent regression.
            idempotency_key: Exact immutable idempotency evidence for this write.

        Returns:
            Whether the exact generation changed.

        Raises:
            SearchProviderError: If write evidence conflicts or the generation is not writeable.
        """
        record = self._get_generation(generation)
        if record.state is not SearchGenerationState.BUILDING:
            raise SearchProviderError(SearchProviderErrorCode.GENERATION_NOT_READY)
        if (
            not isinstance(document, SearchDocument)
            or type(source_revision) is not int
            or source_revision < 1
        ):
            raise SearchProviderError(SearchProviderErrorCode.VALIDATION)
        _require_provider_bounded_string(idempotency_key)
        if document.index_schema_generation != record.index_schema_generation:
            raise SearchProviderError(SearchProviderErrorCode.GENERATION_SCHEMA_MISMATCH)
        if document.projection.tombstone_state.value == "tombstone" and document.fields:
            raise SearchProviderError(SearchProviderErrorCode.VALIDATION)
        fingerprint = _fingerprint_document(document, source_revision)
        existing = record.documents.get(document.document_id)
        retired_revision = record.retired_document_revisions.get(document.document_id, 0)
        if existing is not None:
            if source_revision == existing.source_revision:
                if (
                    existing.idempotency_key != idempotency_key
                    or existing.fingerprint != fingerprint
                ):
                    raise SearchProviderError(SearchProviderErrorCode.INTEGRITY)
                return SearchDocumentWriteResult(False, generation)
            if existing.idempotency_key == idempotency_key:
                if existing.fingerprint != fingerprint:
                    raise SearchProviderError(SearchProviderErrorCode.INTEGRITY)
                return SearchDocumentWriteResult(False, generation)
            if source_revision < existing.source_revision:
                return SearchDocumentWriteResult(False, generation)
        elif source_revision < retired_revision:
            return SearchDocumentWriteResult(False, generation)
        elif source_revision == retired_revision:
            retired_idempotency_key = record.retired_document_idempotency_keys.get(
                document.document_id
            )
            if (
                document.projection.tombstone_state.value == "tombstone"
                and retired_idempotency_key == idempotency_key
                and record.retired_document_operation_kinds.get(document.document_id) == "tombstone"
                and record.retired_document_fingerprints.get(document.document_id) == fingerprint
            ):
                return SearchDocumentWriteResult(False, generation)
            raise SearchProviderError(SearchProviderErrorCode.INTEGRITY)
        if document.projection.tombstone_state.value == "tombstone":
            return self._record_delete(
                record,
                generation,
                document.document_id,
                source_revision,
                idempotency_key,
                "tombstone",
                fingerprint,
            )
        record.documents[document.document_id] = _StoredDocument(
            document,
            source_revision,
            idempotency_key,
            fingerprint,
            self._indexed_at_clock(),
        )
        return SearchDocumentWriteResult(True, generation)

    def bulk_index(
        self, generation: SearchGenerationKey, writes: tuple[SearchDocumentWrite, ...]
    ) -> tuple[SearchDocumentWriteResult, ...]:
        """Index bounded exact writes into one explicit generation.

        Args:
            generation: Exact building generation receiving every write.
            writes: Explicit immutable document write evidence.

        Returns:
            One classified write outcome per input, in input order.

        Raises:
            SearchProviderError: If the write collection or generation is invalid.
        """
        _require_generation_key(generation)
        record = self._get_generation(generation)
        if record.state is not SearchGenerationState.BUILDING:
            raise SearchProviderError(SearchProviderErrorCode.GENERATION_NOT_READY)
        if type(writes) is not tuple or len(writes) > _MAXIMUM_PAGE_SIZE:
            raise SearchProviderError(SearchProviderErrorCode.VALIDATION)
        if any(not isinstance(write, SearchDocumentWrite) for write in writes):
            raise SearchProviderError(SearchProviderErrorCode.VALIDATION)
        return tuple(
            self.index_document(
                generation, write.document, write.source_revision, write.idempotency_key
            )
            for write in writes
        )

    def delete_document(
        self,
        generation: SearchGenerationKey,
        document_id: str,
        source_revision: int,
        idempotency_key: str,
    ) -> SearchDocumentWriteResult:
        """Delete one document using exact source revision evidence.

        Args:
            generation: Exact building generation receiving the delete.
            document_id: Deterministic internal Search document identity.
            source_revision: Monotonic source revision preventing stale deletion.
            idempotency_key: Exact immutable idempotency evidence.

        Returns:
            Whether the exact generation changed.

        Raises:
            SearchProviderError: If deletion evidence conflicts or the generation is not writeable.
        """
        record = self._get_generation(generation)
        if record.state is not SearchGenerationState.BUILDING:
            raise SearchProviderError(SearchProviderErrorCode.GENERATION_NOT_READY)
        _require_provider_bounded_string(document_id)
        if type(source_revision) is not int or source_revision < 1:
            raise SearchProviderError(SearchProviderErrorCode.VALIDATION)
        _require_provider_bounded_string(idempotency_key)
        return self._record_delete(
            record,
            generation,
            document_id,
            source_revision,
            idempotency_key,
            "delete",
            _fingerprint_delete(document_id, source_revision),
        )

    def _record_delete(  # noqa: PLR0913
        self,
        record: _GenerationRecord,
        generation: SearchGenerationKey,
        document_id: str,
        source_revision: int,
        idempotency_key: str,
        operation_kind: str,
        fingerprint: str,
    ) -> SearchDocumentWriteResult:
        """Record one canonical deletion/tombstone mutation with exact retry evidence."""
        existing = record.documents.get(document_id)
        retired_revision = record.retired_document_revisions.get(document_id, 0)
        if existing is not None and source_revision == existing.source_revision:
            raise SearchProviderError(SearchProviderErrorCode.INTEGRITY)
        if source_revision < retired_revision or (
            existing is not None and source_revision < existing.source_revision
        ):
            return SearchDocumentWriteResult(False, generation)
        if source_revision == retired_revision:
            if (
                record.retired_document_idempotency_keys.get(document_id) == idempotency_key
                and record.retired_document_operation_kinds.get(document_id) == operation_kind
                and record.retired_document_fingerprints.get(document_id) == fingerprint
            ):
                return SearchDocumentWriteResult(False, generation)
            raise SearchProviderError(SearchProviderErrorCode.INTEGRITY)
        record.documents.pop(document_id, None)
        record.retired_document_revisions[document_id] = source_revision
        record.retired_document_idempotency_keys[document_id] = idempotency_key
        record.retired_document_operation_kinds[document_id] = operation_kind
        record.retired_document_fingerprints[document_id] = fingerprint
        return SearchDocumentWriteResult(True, generation)

    def rebuild_index(
        self, source_generation: SearchGenerationKey, target_generation: SearchGenerationKey
    ) -> None:
        """Copy deterministic documents from one generation into a building target.

        Args:
            source_generation: Exact validated or active source generation.
            target_generation: Exact building target generation.

        Raises:
            SearchProviderError: If source/target states or schemas are incompatible.
        """
        source = self._get_generation(source_generation)
        target = self._get_generation(target_generation)
        if (
            source.state not in {SearchGenerationState.VALIDATED, SearchGenerationState.ACTIVE}
            or target.state is not SearchGenerationState.BUILDING
        ):
            raise SearchProviderError(SearchProviderErrorCode.GENERATION_NOT_READY)
        if source.index_schema_generation != target.index_schema_generation:
            raise SearchProviderError(SearchProviderErrorCode.GENERATION_SCHEMA_MISMATCH)
        target.documents.clear()
        target.retired_document_revisions.clear()
        target.retired_document_idempotency_keys.clear()
        target.retired_document_operation_kinds.clear()
        target.retired_document_fingerprints.clear()
        target.retired_document_revisions = dict(source.retired_document_revisions)
        target.retired_document_idempotency_keys = dict(source.retired_document_idempotency_keys)
        target.retired_document_operation_kinds = dict(source.retired_document_operation_kinds)
        target.retired_document_fingerprints = dict(source.retired_document_fingerprints)
        for document_id in sorted(source.documents):
            stored = source.documents[document_id]
            self.index_document(
                target_generation, stored.document, stored.source_revision, stored.idempotency_key
            )

    def validate_generation(self, generation: SearchGenerationKey) -> None:
        """Validate one building generation before it may become active.

        Args:
            generation: Exact building generation identity.

        Raises:
            SearchProviderError: If the generation is not ready for validation.
        """
        record = self._get_generation(generation)
        if record.state is not SearchGenerationState.BUILDING:
            raise SearchProviderError(SearchProviderErrorCode.GENERATION_NOT_READY)
        record.state = SearchGenerationState.VALIDATED

    def activate_generation(self, alias: SearchAliasKey, generation: SearchGenerationKey) -> None:
        """Atomically move one logical alias to a validated generation.

        Args:
            alias: Exact logical alias to update.
            generation: Exact validated target generation.

        Raises:
            SearchProviderError: If the target cannot safely activate.
        """
        with self._alias_transition_lock:
            _require_alias_key(alias)
            target = self._get_generation(generation)
            if target.state is not SearchGenerationState.VALIDATED:
                raise SearchProviderError(SearchProviderErrorCode.GENERATION_NOT_READY)
            retained_generation = self._rollback_targets.get(alias)
            if retained_generation is not None:
                retained = self._get_generation(retained_generation)
                if retained.state is not SearchGenerationState.ROLLBACK_RETAINED:
                    raise SearchProviderError(SearchProviderErrorCode.GENERATION_NOT_READY)
                retained.state = SearchGenerationState.RETIRED
            prior_generation = self._aliases.get(alias)
            if prior_generation is not None:
                prior = self._get_generation(prior_generation)
                prior.state = SearchGenerationState.ROLLBACK_RETAINED
                self._rollback_targets[alias] = prior_generation
            self._aliases[alias] = generation
            self._alias_epochs[alias] = self._alias_epochs.get(alias, 0) + 1
            target.state = SearchGenerationState.ACTIVE

    def rollback_alias(self, alias: SearchAliasKey) -> None:
        """Atomically restore the one retained predecessor for an alias.

        Args:
            alias: Exact logical alias whose predecessor is restored.

        Raises:
            SearchProviderError: If no retained predecessor can be restored.
        """
        with self._alias_transition_lock:
            _require_alias_key(alias)
            prior_generation = self._rollback_targets.get(alias)
            active_generation = self._aliases.get(alias)
            if prior_generation is None or active_generation is None:
                raise SearchProviderError(SearchProviderErrorCode.GENERATION_NOT_READY)
            prior = self._get_generation(prior_generation)
            if prior.state is not SearchGenerationState.ROLLBACK_RETAINED:
                raise SearchProviderError(SearchProviderErrorCode.GENERATION_NOT_READY)
            active = self._get_generation(active_generation)
            active.state = SearchGenerationState.ROLLBACK_RETAINED
            prior.state = SearchGenerationState.ACTIVE
            self._aliases[alias] = prior_generation
            self._rollback_targets[alias] = active_generation
            self._alias_epochs[alias] = self._alias_epochs.get(alias, 0) + 1

    def fail_generation(self, generation: SearchGenerationKey) -> None:
        """Mark one non-active generation as failed.

        Args:
            generation: Exact draft, building, or validated generation.

        Raises:
            SearchProviderError: If the generation is active or retained for rollback.
        """
        record = self._get_generation(generation)
        if record.state not in {
            SearchGenerationState.DRAFT,
            SearchGenerationState.BUILDING,
            SearchGenerationState.VALIDATED,
        }:
            raise SearchProviderError(SearchProviderErrorCode.GENERATION_NOT_READY)
        record.state = SearchGenerationState.FAILED

    def retire_generation(self, generation: SearchGenerationKey) -> None:
        """Retire one non-active generation and clear any retained rollback reference.

        Args:
            generation: Exact failed or rollback-retained generation.

        Raises:
            SearchProviderError: If retirement would remove an active generation.
        """
        with self._alias_transition_lock:
            record = self._get_generation(generation)
            if record.state not in {
                SearchGenerationState.FAILED,
                SearchGenerationState.ROLLBACK_RETAINED,
            }:
                raise SearchProviderError(SearchProviderErrorCode.GENERATION_NOT_READY)
            for alias, retained_generation in tuple(self._rollback_targets.items()):
                if retained_generation == generation:
                    del self._rollback_targets[alias]
            record.state = SearchGenerationState.RETIRED

    def search(
        self,
        alias: SearchAliasKey,
        query: SearchQuery,
        scope_binding: SearchAuthorizationScopeBinding,
    ) -> SearchQueryResult:
        """Return deterministic provider-neutral candidates through an active alias.

        Args:
            alias: Exact logical alias resolving to an active generation.
            query: Bounded provider-neutral query.
            scope_binding: Opaque server-derived authorization scope binding.

        Returns:
            A deterministic candidate page bound to the resolved generation.

        Raises:
            SearchProviderError: If the alias, cursor, or requested capability is invalid.
        """
        if (
            not isinstance(alias, SearchAliasKey)
            or not isinstance(query, SearchQuery)
            or not isinstance(scope_binding, SearchAuthorizationScopeBinding)
        ):
            raise SearchProviderError(SearchProviderErrorCode.VALIDATION)
        if self._query_failure is not None:
            raise SearchProviderError(self._query_failure)
        self._require_supported_capabilities(query)
        generation, record, alias_epoch = self._resolve_active_alias(alias)
        self._validate_query_fields(record, query)
        start_index = _decode_cursor(
            query,
            scope_binding,
            alias,
            alias_epoch,
            generation,
            record.index_schema_generation,
            self._cursor_secret,
        )
        matched_documents = [
            stored for stored in record.documents.values() if _matches_query(stored.document, query)
        ]
        ordered_documents = _sort_documents(
            [stored.document for stored in matched_documents], query.sort_fields
        )
        page_documents = ordered_documents[start_index : start_index + query.page_size]
        next_index = start_index + len(page_documents)
        next_cursor = None
        if next_index < len(ordered_documents):
            next_cursor = _encode_cursor(
                query,
                scope_binding,
                alias,
                alias_epoch,
                generation,
                record.index_schema_generation,
                next_index,
                self._cursor_secret,
            )
        hits = tuple(
            SearchResultHit(
                document.document_id,
                document.projection.resource_key,
                document.projection.projection_key.value,
                _calculate_score(document, query.text),
                _build_sort_values(document, query.sort_fields),
                document.index_schema_generation,
                document.projection.projection_revision,
                document.projection.source_event_position,
                document.projection.projected_at,
                document.projection.source_updated_at,
                record.documents[document.document_id].indexed_at,
            )
            for document in page_documents
        )
        return SearchQueryResult(hits, next_cursor, generation, self._return_partial)

    def health(self, generation: SearchGenerationKey) -> SearchHealth:
        """Return safe readiness evidence for one exact generation.

        Args:
            generation: Exact generation whose readiness is requested.

        Returns:
            Provider-neutral readiness evidence.
        """
        record = self._get_generation(generation)
        return SearchHealth(
            True,
            self._query_failure is None,
            record.state in {SearchGenerationState.VALIDATED, SearchGenerationState.ACTIVE},
            self._return_partial,
        )

    def _get_generation(self, generation: SearchGenerationKey) -> _GenerationRecord:
        """Resolve one exact generation or raise a stable readiness error."""
        _require_generation_key(generation)
        record = self._generations.get(generation)
        if record is None or record.state is SearchGenerationState.RETIRED:
            raise SearchProviderError(SearchProviderErrorCode.RETIRED_GENERATION)
        return record

    def _resolve_active_alias(
        self, alias: SearchAliasKey
    ) -> tuple[SearchGenerationKey, _GenerationRecord, int]:
        """Resolve one active alias and its cutover epoch atomically."""
        with self._alias_transition_lock:
            generation = self._aliases.get(alias)
            if generation is None:
                raise SearchProviderError(SearchProviderErrorCode.GENERATION_NOT_READY)
            record = self._get_generation(generation)
            if record.state is not SearchGenerationState.ACTIVE:
                raise SearchProviderError(SearchProviderErrorCode.GENERATION_NOT_READY)
            return generation, record, self._alias_epochs[alias]

    def _require_supported_capabilities(self, query: SearchQuery) -> None:
        """Reject optional features that the conformance adapter does not provide."""
        supported = {
            SearchProviderCapability.LEXICAL,
            SearchProviderCapability.PREFIX,
            SearchProviderCapability.TYPED_FILTERS,
            SearchProviderCapability.DETERMINISTIC_SORT,
            SearchProviderCapability.PAGINATION,
            SearchProviderCapability.BULK_INDEX,
            SearchProviderCapability.GENERATIONS,
        }
        if any(capability not in supported for capability in query.requested_capabilities):
            raise SearchProviderError(SearchProviderErrorCode.UNSUPPORTED_CAPABILITY)

    def _validate_query_fields(self, record: _GenerationRecord, query: SearchQuery) -> None:
        """Reject filters that lack admitted fields or collide with security field names."""
        filter_fields = {
            field.name
            for stored in record.documents.values()
            for field in stored.document.fields
            if field.kind is SearchDocumentFieldKind.FILTER
        }
        security_fields = {
            field.name
            for stored in record.documents.values()
            for field in stored.document.fields
            if field.kind is SearchDocumentFieldKind.SECURITY
        }
        sort_fields = {
            field.name
            for stored in record.documents.values()
            for field in stored.document.fields
            if field.kind is SearchDocumentFieldKind.SORT
        }
        if any(
            query_filter.field_name not in filter_fields
            or query_filter.field_name in security_fields
            for query_filter in query.filters
        ):
            raise SearchProviderError(SearchProviderErrorCode.VALIDATION)
        if any(
            sort.field_name != "document_id" and sort.field_name not in sort_fields
            for sort in query.sort_fields
        ):
            raise SearchProviderError(SearchProviderErrorCode.VALIDATION)


def _require_bounded_string(value: object, error_message: str) -> None:
    """Require a bounded non-blank plain string."""
    if not isinstance(value, str) or not value.strip() or len(value) > _MAXIMUM_OPAQUE_VALUE_LENGTH:
        raise RegistryCoreValidationError(error_message)


def _require_provider_bounded_string(value: object) -> None:
    """Require bounded port input and normalize malformed evidence to validation."""
    try:
        _require_bounded_string(value, "invalid provider input")
    except RegistryCoreValidationError:
        raise SearchProviderError(SearchProviderErrorCode.VALIDATION) from None


def _require_generation_key(value: object) -> None:
    """Require one exact typed Search generation identity."""
    if not isinstance(value, SearchGenerationKey):
        raise SearchProviderError(SearchProviderErrorCode.VALIDATION)


def _require_alias_key(value: object) -> None:
    """Require one exact typed Search logical alias identity."""
    if not isinstance(value, SearchAliasKey):
        raise SearchProviderError(SearchProviderErrorCode.VALIDATION)


def _get_current_utc_time() -> datetime:
    """Return the current UTC timestamp for in-memory index evidence."""
    return datetime.now(UTC)


def _fingerprint_document(document: SearchDocument, source_revision: int) -> str:
    """Build safe deterministic write-integrity evidence for one document."""
    material = json.dumps(
        (
            document.document_id,
            document.index_schema_generation,
            source_revision,
            document.projection,
            document.fields,
        ),
        default=str,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _fingerprint_delete(document_id: str, source_revision: int) -> str:
    """Build canonical idempotency evidence for a direct delete operation."""
    material = json.dumps(("delete", document_id, source_revision), separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(material).hexdigest()


def _matches_query(document: SearchDocument, query: SearchQuery) -> bool:
    """Evaluate deterministic lexical/prefix and exact-filter fixture semantics."""
    text = query.text.casefold().strip()
    text_values = tuple(
        field.value.casefold()
        for field in document.fields
        if field.kind is SearchDocumentFieldKind.TEXT
    )
    if text and not any(
        text in value or any(word.startswith(text) for word in value.split())
        for value in text_values
    ):
        return False
    for query_filter in query.filters:
        if not any(
            field.name == query_filter.field_name and field.value == query_filter.expected_value
            for field in document.fields
            if field.kind is SearchDocumentFieldKind.FILTER
        ):
            return False
    return True


def _sort_documents(
    documents: list[SearchDocument], sort_fields: tuple[SearchSortField, ...]
) -> list[SearchDocument]:
    """Sort documents deterministically and preserve document identity as a tie-breaker."""
    ordered_documents = list(documents)
    effective_sort_fields = sort_fields or (
        SearchSortField("document_id", SearchSortDirection.ASCENDING),
    )
    ordered_documents.sort(key=lambda document: document.document_id)
    for sort_field in reversed(effective_sort_fields):
        ordered_documents.sort(
            key=lambda document: _resolve_sort_value(document, sort_field.field_name),
            reverse=sort_field.direction is SearchSortDirection.DESCENDING,
        )
    return ordered_documents


def _resolve_sort_value(document: SearchDocument, field_name: str) -> str:
    """Resolve one approved sort value without a provider-specific field language."""
    if field_name == "document_id":
        return document.document_id
    for field in document.fields:
        if field.name == field_name and field.kind is SearchDocumentFieldKind.SORT:
            return field.value
    return ""


def _build_sort_values(
    document: SearchDocument, sort_fields: tuple[SearchSortField, ...]
) -> tuple[str, ...]:
    """Build stable provider-neutral sort evidence for one returned hit."""
    effective_sort_fields = sort_fields or (
        SearchSortField("document_id", SearchSortDirection.ASCENDING),
    )
    return tuple(_resolve_sort_value(document, item.field_name) for item in effective_sort_fields)


def _calculate_score(document: SearchDocument, query_text: str) -> int:
    """Calculate a bounded deterministic fixture ranking score."""
    normalized_query = query_text.casefold().strip()
    if not normalized_query:
        return 0
    return sum(
        field.value.casefold().count(normalized_query)
        for field in document.fields
        if field.kind is SearchDocumentFieldKind.TEXT
    )


def _encode_cursor(  # noqa: PLR0913
    query: SearchQuery,
    scope_binding: SearchAuthorizationScopeBinding,
    alias: SearchAliasKey,
    alias_epoch: int,
    generation: SearchGenerationKey,
    schema_generation: str,
    next_index: int,
    cursor_secret: bytes,
) -> str:
    """Encode an integrity-protected opaque continuation cursor."""
    payload = json.dumps(
        {
            "alias": alias.value,
            "alias_epoch": alias_epoch,
            "generation": generation.value,
            "index": next_index,
            "query": _query_hash(query),
            "schema": schema_generation,
            "scope": hashlib.sha256(scope_binding.value.encode("utf-8")).hexdigest(),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    signature = hmac.new(cursor_secret, payload, hashlib.sha256).hexdigest().encode("ascii")
    return base64.urlsafe_b64encode(payload + b"." + signature).decode("ascii")


def _decode_cursor(  # noqa: PLR0913
    query: SearchQuery,
    scope_binding: SearchAuthorizationScopeBinding,
    alias: SearchAliasKey,
    alias_epoch: int,
    generation: SearchGenerationKey,
    schema_generation: str,
    cursor_secret: bytes,
) -> int:
    """Validate an opaque cursor against the exact query, scope, and generation."""
    if query.cursor is None:
        return 0
    try:
        encoded = base64.b64decode(query.cursor.encode("ascii"), altchars=b"-_", validate=True)
        payload, signature = encoded.rsplit(b".", 1)
        expected_signature = (
            hmac.new(cursor_secret, payload, hashlib.sha256).hexdigest().encode("ascii")
        )
        if not hmac.compare_digest(signature, expected_signature):
            raise ValueError
        decoded_object = json.loads(payload.decode("utf-8"))
        if not isinstance(decoded_object, dict):
            raise ValueError
        decoded = cast(dict[str, object], decoded_object)
        cursor_index = decoded.get("index")
        if (
            decoded.get("alias") != alias.value
            or decoded.get("alias_epoch") != alias_epoch
            or decoded.get("generation") != generation.value
            or decoded.get("schema") != schema_generation
            or decoded.get("query") != _query_hash(query)
            or decoded.get("scope")
            != hashlib.sha256(scope_binding.value.encode("utf-8")).hexdigest()
            or not isinstance(cursor_index, int)
            or cursor_index < 0
        ):
            raise ValueError
        return cursor_index
    except (UnicodeDecodeError, ValueError, binascii.Error, json.JSONDecodeError):
        raise SearchProviderError(SearchProviderErrorCode.STALE_OR_INVALID_CURSOR) from None


def _query_hash(query: SearchQuery) -> str:
    """Hash normalized query evidence without its opaque continuation cursor."""
    material = json.dumps(
        (
            query.text,
            tuple((item.field_name, item.expected_value) for item in query.filters),
            tuple((item.field_name, item.direction.value) for item in query.sort_fields),
            query.page_size,
            tuple(item.value for item in query.requested_capabilities),
        ),
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()
