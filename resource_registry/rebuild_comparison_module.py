"""Define pure fixture rebuild and projection-comparison contracts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum

from .catalog_module import CatalogGenerationKey, CatalogProjectionRecord, CatalogProjectionStore
from .projection_ingestion_module import (
    ProjectionIngestionEvidence,
    ProjectionIngestionState,
    ProjectionIngestionStatus,
)
from .registry_core import RegistryCoreValidationError, ResourceKey
from .search_document_module import SearchDocument
from .search_provider_module import SearchAuthorizationScopeBinding, SearchGenerationKey

__all__ = (
    "ProjectionComparisonBaseline",
    "ProjectionComparisonReport",
    "ProjectionDriftFinding",
    "ProjectionDriftKind",
    "ProjectionParityFact",
    "RebuildComparisonError",
    "RebuildComparisonErrorCode",
    "RebuildComparisonService",
    "RebuildFixture",
    "RebuildResult",
    "SearchResultComparisonFixture",
)

_MAXIMUM_FIXTURE_EVIDENCE = 1_000
_MAXIMUM_FINDINGS = 1_000
_MAXIMUM_SAFE_IDENTIFIER_LENGTH = 256
_SHA256_HEX_LENGTH = 64


class RebuildComparisonErrorCode(str, Enum):
    """Classify stable rebuild/comparison failures without runtime details."""

    VALIDATION = "validation"
    INCOMPLETE = "incomplete"
    CONTEXT_MISMATCH = "context_mismatch"


class RebuildComparisonError(RegistryCoreValidationError):
    """Report a stable rebuild/comparison failure.

    Args:
        code: Stable category safe for fixture callers.
    """

    def __init__(self, code: RebuildComparisonErrorCode) -> None:
        """Initialize the failure with its stable category.

        Args:
            code: Stable category safe for fixture callers.
        """
        self.code = code
        super().__init__(code.value)


class ProjectionDriftKind(str, Enum):
    """Classify deterministic safe projection-comparison differences."""

    MISSING = "missing"
    UNEXPECTED = "unexpected"
    REVISION_MISMATCH = "revision_mismatch"
    TOMBSTONE_MISMATCH = "tombstone_mismatch"
    FRESHNESS_MISMATCH = "freshness_mismatch"
    CHECKSUM_MISMATCH = "checksum_mismatch"
    COUNT_MISMATCH = "count_mismatch"
    RESULT_ORDER_MISMATCH = "result_order_mismatch"


@dataclass(frozen=True, slots=True)
class RebuildFixture:
    """Carry immutable fixture evidence for one isolated rebuild evaluation.

    Args:
        snapshot_identifier: Exact safe fixture snapshot or checkpoint identity.
        catalog_store: Empty Catalog state admitting the fixture target generation.
        catalog_generation: Exact inactive Catalog generation receiving fixture evidence.
        search_generation: Exact inactive Search generation paired with the rebuild.
        search_schema_generation: Exact Search schema-generation evidence.
        evidence: Ordered immutable Projection Ingestion fixture evidence.
        search_documents: Safe Search documents derived from admitted fixture projections.

    Raises:
        RebuildComparisonError: If fixture evidence is malformed, non-isolated, or incomplete.
    """

    snapshot_identifier: str
    catalog_store: CatalogProjectionStore
    catalog_generation: CatalogGenerationKey
    search_generation: SearchGenerationKey
    search_schema_generation: str
    evidence: tuple[ProjectionIngestionEvidence, ...]
    search_documents: tuple[SearchDocument, ...]

    def __post_init__(self) -> None:
        """Validate bounded fixture evidence before rebuilding derived state.

        Raises:
            RebuildComparisonError: If fixture evidence is invalid or ambiguous.
        """
        _require_safe_identifier(self.snapshot_identifier)
        if not isinstance(self.catalog_store, CatalogProjectionStore):
            raise RebuildComparisonError(RebuildComparisonErrorCode.VALIDATION)
        if self.catalog_store.projections:
            raise RebuildComparisonError(RebuildComparisonErrorCode.VALIDATION)
        if not isinstance(self.catalog_generation, CatalogGenerationKey) or not isinstance(
            self.search_generation, SearchGenerationKey
        ):
            raise RebuildComparisonError(RebuildComparisonErrorCode.VALIDATION)
        _require_safe_identifier(self.search_schema_generation)
        if (
            type(self.evidence) is not tuple
            or not self.evidence
            or len(self.evidence) > _MAXIMUM_FIXTURE_EVIDENCE
            or any(not isinstance(item, ProjectionIngestionEvidence) for item in self.evidence)
        ):
            raise RebuildComparisonError(RebuildComparisonErrorCode.VALIDATION)
        if any(item.target_generation != self.catalog_generation for item in self.evidence):
            raise RebuildComparisonError(RebuildComparisonErrorCode.VALIDATION)
        if type(self.search_documents) is not tuple or len(self.search_documents) > len(
            self.evidence
        ):
            raise RebuildComparisonError(RebuildComparisonErrorCode.VALIDATION)
        document_ids: set[str] = set()
        for document in self.search_documents:
            if not isinstance(document, SearchDocument):
                raise RebuildComparisonError(RebuildComparisonErrorCode.VALIDATION)
            if document.index_schema_generation != self.search_schema_generation:
                raise RebuildComparisonError(RebuildComparisonErrorCode.VALIDATION)
            if document.projection.generation.key != self.catalog_generation:
                raise RebuildComparisonError(RebuildComparisonErrorCode.VALIDATION)
            if document.document_id in document_ids:
                raise RebuildComparisonError(RebuildComparisonErrorCode.VALIDATION)
            document_ids.add(document.document_id)


@dataclass(frozen=True, slots=True)
class ProjectionParityFact:
    """Carry safe canonical projection facts for deterministic parity comparison.

    Args:
        resource_key: Exact canonical resource identity.
        projection_revision: Exact Catalog projection revision.
        is_tombstone: Whether the projection carries removal evidence.
        source_updated_at: Exact authoritative source-update instant.

    Raises:
        RebuildComparisonError: If safe parity evidence is invalid.
    """

    resource_key: ResourceKey
    projection_revision: str
    is_tombstone: bool
    source_updated_at: datetime

    def __post_init__(self) -> None:
        """Validate immutable safe parity evidence.

        Raises:
            RebuildComparisonError: If parity evidence is malformed.
        """
        if not isinstance(self.resource_key, ResourceKey) or not isinstance(
            self.is_tombstone, bool
        ):
            raise RebuildComparisonError(RebuildComparisonErrorCode.VALIDATION)
        _require_safe_identifier(self.projection_revision)
        if not _is_utc_datetime(self.source_updated_at):
            raise RebuildComparisonError(RebuildComparisonErrorCode.VALIDATION)


@dataclass(frozen=True, slots=True)
class ProjectionComparisonBaseline:
    """Carry immutable expected evidence for one fixture comparison.

    Args:
        catalog_generation: Exact Catalog generation represented by the baseline.
        search_generation: Exact Search generation represented by the baseline.
        checksum: Normalized digest expected for the rebuild output.
        projection_count: Expected number of Catalog projections, including tombstones.
        tombstone_count: Expected number of tombstone projections.
        freshness_at: Expected latest source-update instant.
        facts: Expected safe per-resource projection facts.

    Raises:
        RebuildComparisonError: If expected evidence is malformed.
    """

    catalog_generation: CatalogGenerationKey
    search_generation: SearchGenerationKey
    checksum: str
    projection_count: int
    tombstone_count: int
    freshness_at: datetime
    facts: tuple[ProjectionParityFact, ...] = ()

    def __post_init__(self) -> None:
        """Validate bounded expected rebuild evidence.

        Raises:
            RebuildComparisonError: If baseline evidence is invalid.
        """
        if not isinstance(self.catalog_generation, CatalogGenerationKey) or not isinstance(
            self.search_generation, SearchGenerationKey
        ):
            raise RebuildComparisonError(RebuildComparisonErrorCode.VALIDATION)
        if len(self.checksum) != _SHA256_HEX_LENGTH or any(
            character not in "0123456789abcdef" for character in self.checksum
        ):
            raise RebuildComparisonError(RebuildComparisonErrorCode.VALIDATION)
        if (
            type(self.projection_count) is not int
            or type(self.tombstone_count) is not int
            or self.projection_count < 0
            or self.tombstone_count < 0
            or self.tombstone_count > self.projection_count
            or not _is_utc_datetime(self.freshness_at)
            or type(self.facts) is not tuple
            or len(self.facts) > _MAXIMUM_FIXTURE_EVIDENCE
            or any(not isinstance(fact, ProjectionParityFact) for fact in self.facts)
        ):
            raise RebuildComparisonError(RebuildComparisonErrorCode.VALIDATION)
        if len({fact.resource_key for fact in self.facts}) != len(self.facts):
            raise RebuildComparisonError(RebuildComparisonErrorCode.VALIDATION)


@dataclass(frozen=True, slots=True)
class ProjectionDriftFinding:
    """Describe one bounded safe rebuild-comparison difference.

    Args:
        kind: Stable category of detected difference.
        resource_key: Exact canonical key when the difference is resource-specific.

    Raises:
        RebuildComparisonError: If finding evidence is malformed.
    """

    kind: ProjectionDriftKind
    resource_key: ResourceKey | None = None

    def __post_init__(self) -> None:
        """Validate safe drift evidence.

        Raises:
            RebuildComparisonError: If the finding is invalid.
        """
        if not isinstance(self.kind, ProjectionDriftKind):
            raise RebuildComparisonError(RebuildComparisonErrorCode.VALIDATION)
        if self.resource_key is not None and not isinstance(self.resource_key, ResourceKey):
            raise RebuildComparisonError(RebuildComparisonErrorCode.VALIDATION)


@dataclass(frozen=True, slots=True)
class ProjectionComparisonReport:
    """Report deterministic, bounded rebuild parity evidence.

    Args:
        is_parity: Whether no drift finding was detected.
        checksum: Normalized rebuild digest.
        projection_count: Number of Catalog projections including tombstones.
        tombstone_count: Number of tombstone projections.
        freshness_at: Latest source-update instant in fixture evidence.
        findings: Ordered safe drift findings.

    Raises:
        RebuildComparisonError: If report evidence is inconsistent.
    """

    is_parity: bool
    checksum: str
    projection_count: int
    tombstone_count: int
    freshness_at: datetime
    findings: tuple[ProjectionDriftFinding, ...] = ()

    def __post_init__(self) -> None:
        """Validate deterministic report evidence.

        Raises:
            RebuildComparisonError: If report fields conflict.
        """
        if not isinstance(self.is_parity, bool) or type(self.findings) is not tuple:
            raise RebuildComparisonError(RebuildComparisonErrorCode.VALIDATION)
        if len(self.findings) > _MAXIMUM_FINDINGS or any(
            not isinstance(item, ProjectionDriftFinding) for item in self.findings
        ):
            raise RebuildComparisonError(RebuildComparisonErrorCode.VALIDATION)
        baseline = ProjectionComparisonBaseline(
            CatalogGenerationKey("report"),
            SearchGenerationKey("report"),
            self.checksum,
            self.projection_count,
            self.tombstone_count,
            self.freshness_at,
        )
        del baseline
        if self.is_parity != (not self.findings):
            raise RebuildComparisonError(RebuildComparisonErrorCode.VALIDATION)


@dataclass(frozen=True, slots=True)
class RebuildResult:
    """Carry the immutable output of one fixture rebuild.

    Args:
        fixture: Exact fixture that was evaluated.
        report: Deterministic safe rebuild evidence without activation or mutation authority.
        facts: Ordered safe per-resource facts produced by the rebuild.

    Raises:
        RebuildComparisonError: If output evidence is invalid.
    """

    fixture: RebuildFixture
    report: ProjectionComparisonReport
    facts: tuple[ProjectionParityFact, ...]

    def __post_init__(self) -> None:
        """Validate rebuilt fixture evidence.

        Raises:
            RebuildComparisonError: If rebuild output is invalid.
        """
        if not isinstance(self.fixture, RebuildFixture) or not isinstance(
            self.report, ProjectionComparisonReport
        ):
            raise RebuildComparisonError(RebuildComparisonErrorCode.VALIDATION)
        if type(self.facts) is not tuple or any(
            not isinstance(fact, ProjectionParityFact) for fact in self.facts
        ):
            raise RebuildComparisonError(RebuildComparisonErrorCode.VALIDATION)


@dataclass(frozen=True, slots=True)
class SearchResultComparisonFixture:
    """Carry safe deterministic result-reference evidence for one fixture query.

    Args:
        query_identifier: Opaque fixture query identity, not caller query text.
        scope_binding: Exact server-derived scope binding used by both fixtures.
        search_generation: Exact generation that produced the references.
        search_schema_generation: Exact schema generation that produced the references.
        sort_values: Exact deterministic sort declaration evidence.
        resource_keys: Ordered canonical candidate references without document details.

    Raises:
        RebuildComparisonError: If fixture comparison evidence is invalid.
    """

    query_identifier: str
    scope_binding: SearchAuthorizationScopeBinding
    search_generation: SearchGenerationKey
    search_schema_generation: str
    sort_values: tuple[str, ...]
    resource_keys: tuple[ResourceKey, ...]

    def __post_init__(self) -> None:
        """Validate bounded result-reference evidence.

        Raises:
            RebuildComparisonError: If result fixture evidence is invalid.
        """
        _require_safe_identifier(self.query_identifier)
        if not isinstance(self.scope_binding, SearchAuthorizationScopeBinding) or not isinstance(
            self.search_generation, SearchGenerationKey
        ):
            raise RebuildComparisonError(RebuildComparisonErrorCode.VALIDATION)
        _require_safe_identifier(self.search_schema_generation)
        if type(self.sort_values) is not tuple or type(self.resource_keys) is not tuple:
            raise RebuildComparisonError(RebuildComparisonErrorCode.VALIDATION)
        if any(not isinstance(value, str) or not value for value in self.sort_values) or any(
            not isinstance(key, ResourceKey) for key in self.resource_keys
        ):
            raise RebuildComparisonError(RebuildComparisonErrorCode.VALIDATION)


class RebuildComparisonService:
    """Build and compare isolated fixture evidence without runtime side effects."""

    def rebuild_fixture(self, fixture: RebuildFixture) -> RebuildResult:
        """Apply fixture evidence to an isolated Catalog state and calculate safe evidence.

        Args:
            fixture: Valid immutable rebuild fixture.

        Returns:
            A deterministic rebuild result with no active-generation change.

        Raises:
            RebuildComparisonError: If fixture application is incomplete or invalid.
        """
        if not isinstance(fixture, RebuildFixture):
            raise RebuildComparisonError(RebuildComparisonErrorCode.VALIDATION)
        state = ProjectionIngestionState(fixture.catalog_store)
        for evidence in fixture.evidence:
            application = state.apply_evidence(evidence)
            if application.status is not ProjectionIngestionStatus.APPLIED:
                raise RebuildComparisonError(RebuildComparisonErrorCode.INCOMPLETE)
            state = application.state
        projections = state.catalog_store.list_projections(
            fixture.catalog_generation, include_tombstones=True
        )
        projection_keys = {projection.resource_key for projection in projections}
        if any(
            document.projection.resource_key not in projection_keys
            for document in fixture.search_documents
        ):
            raise RebuildComparisonError(RebuildComparisonErrorCode.INCOMPLETE)
        tombstone_count = sum(
            projection.tombstone_state.value == "tombstone" for projection in projections
        )
        report = ProjectionComparisonReport(
            True,
            _calculate_projection_checksum(projections, fixture.search_documents),
            len(projections),
            tombstone_count,
            max(projection.source_updated_at for projection in projections),
        )
        facts = tuple(
            ProjectionParityFact(
                projection.resource_key,
                projection.projection_revision,
                projection.tombstone_state.value == "tombstone",
                projection.source_updated_at,
            )
            for projection in projections
        )
        return RebuildResult(fixture, report, facts)

    def compare_rebuild(
        self,
        baseline: ProjectionComparisonBaseline,
        candidate: RebuildResult,
    ) -> ProjectionComparisonReport:
        """Compare one rebuilt fixture with expected bounded parity evidence.

        Args:
            baseline: Expected isolated-generation rebuild evidence.
            candidate: Rebuilt fixture result to compare.

        Returns:
            A deterministic parity or drift report.

        Raises:
            RebuildComparisonError: If comparison contexts are incompatible.
        """
        if not isinstance(baseline, ProjectionComparisonBaseline) or not isinstance(
            candidate, RebuildResult
        ):
            raise RebuildComparisonError(RebuildComparisonErrorCode.VALIDATION)
        if (
            baseline.catalog_generation != candidate.fixture.catalog_generation
            or baseline.search_generation != candidate.fixture.search_generation
        ):
            raise RebuildComparisonError(RebuildComparisonErrorCode.CONTEXT_MISMATCH)
        findings: list[ProjectionDriftFinding] = []
        report = candidate.report
        expected_facts = {fact.resource_key: fact for fact in baseline.facts}
        candidate_facts = {fact.resource_key: fact for fact in candidate.facts}
        for resource_key in sorted(
            expected_facts.keys() - candidate_facts.keys(), key=_resource_sort_key
        ):
            findings.append(ProjectionDriftFinding(ProjectionDriftKind.MISSING, resource_key))
        for resource_key in sorted(
            candidate_facts.keys() - expected_facts.keys(), key=_resource_sort_key
        ):
            findings.append(ProjectionDriftFinding(ProjectionDriftKind.UNEXPECTED, resource_key))
        for resource_key in sorted(
            expected_facts.keys() & candidate_facts.keys(), key=_resource_sort_key
        ):
            expected_fact = expected_facts[resource_key]
            candidate_fact = candidate_facts[resource_key]
            if expected_fact.projection_revision != candidate_fact.projection_revision:
                findings.append(
                    ProjectionDriftFinding(ProjectionDriftKind.REVISION_MISMATCH, resource_key)
                )
            if expected_fact.is_tombstone != candidate_fact.is_tombstone:
                findings.append(
                    ProjectionDriftFinding(ProjectionDriftKind.TOMBSTONE_MISMATCH, resource_key)
                )
            if expected_fact.source_updated_at != candidate_fact.source_updated_at:
                findings.append(
                    ProjectionDriftFinding(ProjectionDriftKind.FRESHNESS_MISMATCH, resource_key)
                )
        if baseline.projection_count != report.projection_count:
            findings.append(ProjectionDriftFinding(ProjectionDriftKind.COUNT_MISMATCH))
        if baseline.tombstone_count != report.tombstone_count:
            findings.append(ProjectionDriftFinding(ProjectionDriftKind.TOMBSTONE_MISMATCH))
        if baseline.freshness_at != report.freshness_at:
            findings.append(ProjectionDriftFinding(ProjectionDriftKind.FRESHNESS_MISMATCH))
        if baseline.checksum != report.checksum:
            findings.append(ProjectionDriftFinding(ProjectionDriftKind.CHECKSUM_MISMATCH))
        return ProjectionComparisonReport(
            not findings,
            report.checksum,
            report.projection_count,
            report.tombstone_count,
            report.freshness_at,
            tuple(findings),
        )

    def compare_result_references(
        self,
        expected: SearchResultComparisonFixture,
        candidate: SearchResultComparisonFixture,
    ) -> ProjectionDriftFinding | None:
        """Compare ordered safe fixture result references in one exact search context.

        Args:
            expected: Expected safe fixture result references.
            candidate: Candidate safe fixture result references.

        Returns:
            A result-order drift finding, or ``None`` when references match.

        Raises:
            RebuildComparisonError: If query contexts differ or evidence is invalid.
        """
        if not isinstance(expected, SearchResultComparisonFixture) or not isinstance(
            candidate, SearchResultComparisonFixture
        ):
            raise RebuildComparisonError(RebuildComparisonErrorCode.VALIDATION)
        if (
            expected.query_identifier,
            expected.scope_binding,
            expected.search_generation,
            expected.search_schema_generation,
            expected.sort_values,
        ) != (
            candidate.query_identifier,
            candidate.scope_binding,
            candidate.search_generation,
            candidate.search_schema_generation,
            candidate.sort_values,
        ):
            raise RebuildComparisonError(RebuildComparisonErrorCode.CONTEXT_MISMATCH)
        if expected.resource_keys == candidate.resource_keys:
            return None
        return ProjectionDriftFinding(ProjectionDriftKind.RESULT_ORDER_MISMATCH)


def _calculate_projection_checksum(
    projections: tuple[CatalogProjectionRecord, ...], search_documents: tuple[SearchDocument, ...]
) -> str:
    """Calculate a normalized digest over bounded projection and document identity evidence.

    Args:
        projections: Ordered immutable Catalog projections admitted by the fixture.
        search_documents: Safe Search documents derived from those projections.

    Returns:
        A SHA-256 hexadecimal digest over normalized fixture facts.
    """
    projection_facts = [
        (
            str(projection.resource_key.namespace.value),
            projection.resource_key.resource_type.value,
            projection.resource_key.resource_id,
            projection.projection_revision,
            projection.source_event_position,
            projection.source_updated_at.isoformat(),
            projection.tombstone_state.value,
        )
        for projection in projections
    ]
    document_facts = sorted(
        (document.document_id, document.projection.resource_key.resource_id)
        for document in search_documents
    )
    payload = json.dumps(
        (projection_facts, document_facts), separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _is_utc_datetime(value: object) -> bool:
    """Determine whether a value is an aware UTC datetime.

    Args:
        value: Candidate datetime value.

    Returns:
        Whether the value is an aware datetime using UTC.
    """
    return isinstance(value, datetime) and value.tzinfo is UTC


def _resource_sort_key(resource_key: ResourceKey) -> tuple[str, str, str]:
    """Build deterministic ordering for safe canonical resource references.

    Args:
        resource_key: Exact canonical resource identity.

    Returns:
        Namespace, resource type, and resource identifier ordering values.
    """
    return (
        str(resource_key.namespace.value),
        resource_key.resource_type.value,
        resource_key.resource_id,
    )


def _require_safe_identifier(value: object) -> None:
    """Require one bounded non-blank opaque fixture identifier.

    Args:
        value: Candidate fixture identifier.

    Raises:
        RebuildComparisonError: If the identifier is invalid.
    """
    if type(value) is not str or not value.strip() or len(value) > _MAXIMUM_SAFE_IDENTIFIER_LENGTH:
        raise RebuildComparisonError(RebuildComparisonErrorCode.VALIDATION)
