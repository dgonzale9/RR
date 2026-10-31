"""Route Resource Registry exploration questions to canonical BIF calls."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

EXPLORATION_TOOL_NAME = "resource_registry_explore_dataset"
SCHEMA_TOOL_NAME = "resource_registry_get_dataset_schema"
LIST_DATASETS_TOOL_NAME = "resource_registry_list_datasets"

DEFAULT_EXPLORATION_LIMIT = 5
MAX_EXPLORATION_LIMIT = 100
MAX_ROUTING_RETRIES = 1
NON_RETRYABLE_ROUTING_ERROR_CODES = frozenset(
    {
        "authorization_denied",
        "capability_denied",
        "forbidden",
        "tenant_denied",
        "cross_tenant_denied",
        "exploration_column_not_allowed",
        "restricted_dataset",
        "restricted_column",
        "unsupported_operation",
        "missing_search_terms",
        "ambiguous_exploration_column",
    },
)

ExplorationOperation = Literal[
    "distinct_values",
    "top_values",
    "sample_values",
    "search_values",
    "column_profile",
]
SearchMatchMode = Literal["contains", "exact", "prefix"]
RoutingStatus = Literal[
    "ready",
    "dataset_listing",
    "needs_dataset",
    "ambiguous_dataset",
    "needs_schema",
    "ambiguous_column",
    "unknown_column",
    "missing_terms",
    "unsupported",
    "exploration_ambiguity",
    "bypass_normal_flow",
]

_AGGREGATION_PATTERN = re.compile(
    r"\b(sum|average|avg|mean|median|calculate|compute|percentage|percent)\b",
    flags=re.IGNORECASE,
)
_NUMERIC_RANKING_PATTERN = re.compile(
    r"\b(highest|lowest|largest|smallest|maximum|minimum|max(?:imum)? numeric|min(?:imum)? "
    r"numeric)\b",
    flags=re.IGNORECASE,
)
_FILTER_PATTERN = re.compile(
    r"\b(where|filter|filtered|only rows|only values|greater than|less than|before|after)\b",
    flags=re.IGNORECASE,
)
_NULL_SAMPLE_PATTERN = re.compile(
    r"\b(examples?|samples?)\b.*\b(null|missing)\b|\b(null|missing)\b.*\b(examples?|samples?)\b",
    flags=re.IGNORECASE,
)
_COMBINED_SEARCH_FREQUENCY_PATTERN = re.compile(
    r"\b(most common|most frequent|frequency|top)\b.*\b(containing|contains|matching|matches|"
    r"search|find)\b|\b(containing|contains|matching|matches|search|find)\b.*\b(most common|"
    r"most frequent|frequency|top)\b",
    flags=re.IGNORECASE,
)
_TOP_SAMPLE_PATTERN = re.compile(
    r"\btop\b.*\b(sample|samples|examples?)\b|\b(sample|samples|examples?)\b.*\btop\b",
    flags=re.IGNORECASE,
)
_UNIQUE_MISSING_PATTERN = re.compile(
    r"\b(unique|distinct)\b.*\b(missing|null)\b|\b(missing|null)\b.*\b(unique|distinct)\b",
    flags=re.IGNORECASE,
)
_JOIN_PATTERN = re.compile(
    r"\b(join|combine|merge|across datasets|between datasets)\b",
    flags=re.IGNORECASE,
)
_MULTI_DATASET_COMPARISON_PATTERN = re.compile(
    r"\b(compare|comparison|across)\b.*\b(datasets?|sheets?)\b|"
    r"\b(row counts?|top categor(?:y|ies)|signals?)\b.*\b(across|between)\b.*"
    r"\b(datasets?|sheets?)\b",
    flags=re.IGNORECASE,
)
_TRANSFORM_PATTERN = re.compile(
    r"\b(transform|create a derived|derive|new column|convert|cleanse)\b",
    flags=re.IGNORECASE,
)
_RECOMMEND_PATTERN = re.compile(
    r"\b(recommend|suggest|best action|what should|which option should)\b",
    flags=re.IGNORECASE,
)
_CONCEPT_WORKFLOW_PATTERN = re.compile(
    r"^\s*(?:please\s+)?(?:continue|create|generate|develop|refine|revise)\b"
    r"[^.!?\n]*\bconcepts?\b",
    flags=re.IGNORECASE,
)
_RENDER_SPEC_WORKFLOW_PATTERN = re.compile(
    r"^\s*(?:please\s+)?(?:(?:create|generate|build|prepare|return)\s+)?render\s+specs?\b",
    flags=re.IGNORECASE,
)
_DOCUMENT_TOOL_REQUEST_PATTERN = re.compile(
    r"^\s*(?:please\s+)?(?:(?:use|call|invoke)\s+|"
    r"(?:retrieve|fetch|get)\b[^.!?\n]*\b(?:through|using|via|with)\s+)"
    r"`?get_[a-z0-9_]+_documents\b",
    flags=re.IGNORECASE,
)
_DATASET_SELECTION_METADATA_REQUEST_PATTERN = re.compile(
    r"\b(?:provide|retrieve|return|get|identify|show|give)\s+"
    r"(?:the\s+)?(?:(?:exact|verified|current|pinned|selected|original)\s+){0,4}"
    r"(?:`?dataset_id`?\s*(?:and|,|/)\s*`?version_id`?|"
    r"`?version_id`?\s*(?:and|,|/)\s*`?dataset_id`?)(?!\w)",
    flags=re.IGNORECASE,
)
_DISTINCT_PATTERN = re.compile(
    r"\b(distinct|unique|values exist|what values|which values|categories present|statuses "
    r"appear)\b",
    flags=re.IGNORECASE,
)
_TOP_PATTERN = re.compile(
    r"\b(most common|most frequent|occurs most|frequency distribution|top\s+\d*|leading)\b",
    flags=re.IGNORECASE,
)
_SAMPLE_PATTERN = re.compile(
    r"\b(sample|samples|examples|example values|few values|what does the data look like|"
    r"representative)\b",
    flags=re.IGNORECASE,
)
_SEARCH_PATTERN = re.compile(
    r"\b(find|search|matching|matches|contains|contain|equals|exactly|starts with|"
    r"start with|look for)\b",
    flags=re.IGNORECASE,
)
_PROFILE_PATTERN = re.compile(
    r"\b(profile|complete|completeness|nulls|null values|missing values|statistics|stats|"
    r"data type|type of data|high cardinality|cardinality)\b",
    flags=re.IGNORECASE,
)
_BARE_DATASET_PROFILE_PATTERN = re.compile(
    r"^\s*(?:show|view|get|display)?\s*(?:the\s+)?(?:dataset\s+)?profile\s*[?.!]*\s*$",
    flags=re.IGNORECASE,
)
_UNSUPPORTED_MATCH_MODE_PATTERN = re.compile(
    r"\b(regex|regular expression|fuzzy|semantic|phonetic|wildcard|edit distance)\b",
    flags=re.IGNORECASE,
)
_SHOW_ALL_PATTERN = re.compile(r"\b(all|every|unlimited)\b", flags=re.IGNORECASE)
_NUMBER_PATTERN = re.compile(r"\b(\d{1,6})\b")
_QUOTED_PHRASE_PATTERN = re.compile(r"[\"“”']([^\"“”']+)[\"“”']")
_TERM_AFTER_PATTERN = re.compile(
    r"\b(?:containing|contains|contain|matching|matches|search(?:\s+\w+){0,3}\s+for|"
    r"find(?:\s+\w+){0,3}|look for|equals|exactly|starts with|start with)\s+"
    r"[\"“”']?([A-Za-z0-9_. -]+?)[\"“”']?(?:\s+(?:in|within|from|for|on)\b|[?.!,]|$)",
    flags=re.IGNORECASE,
)
_DATASET_DISCOVERY_TERM_PATTERN = re.compile(
    r"\bdatasets?\s+(?:related\s+to|about|containing|contains?|mentioning|mentions?|for|with)\s+"
    r"[\"“”']?([A-Za-z0-9_. -]+?)[\"“”']?(?:[?.!,]|$)",
    flags=re.IGNORECASE,
)
_TENANT_CONTEXT_SUFFIX_PATTERN = re.compile(
    r"\s+(?:for|in|within)\s+(?:this\s+tenant|the\s+current\s+tenant|tenant"
    r"(?:\s+id)?(?:\s+[0-9a-fA-F-]{8,})?)\s*$",
    flags=re.IGNORECASE,
)
_DATASET_LISTING_ACTION_PATTERN = re.compile(
    r"\b(list|show|find|search|discover|identify|view|which|what)\b",
    flags=re.IGNORECASE,
)
_DATASET_LISTING_SUBJECT_PATTERN = re.compile(
    r"\b(dataset|datasets)\b",
    flags=re.IGNORECASE,
)
_DATASET_LISTING_PLURAL_SUBJECT_PATTERN = re.compile(
    r"\bdatasets\b",
    flags=re.IGNORECASE,
)
_DATASET_LISTING_SCOPE_PATTERN = re.compile(
    r"\b(available|avialable|accessible|registered|mine|my|access|available to me|can access)\b",
    flags=re.IGNORECASE,
)
_ACTIVE_DATASET_REFERENCE_PATTERN = re.compile(
    r"\b(?:the\s+)?(?:active|current|selected|this|that)\s+dataset\b",
    flags=re.IGNORECASE,
)
_DATASET_LISTING_FALSE_POSITIVE_PATTERN = re.compile(
    r"\b("
    r"schema|schemas|column|columns|field|fields|distinct|unique|top|values?|"
    r"create|register|transform|derived|lineage|version|versions"
    r")\b",
    flags=re.IGNORECASE,
)
@dataclass(frozen=True)
class DatasetRoutingCandidate:
    """Represent one authorized dataset candidate from existing discovery tools.

    Args:
        dataset_id: Canonical dataset identifier.
        name: Human-readable dataset name.
        version_id: Optional version identifier already established by trusted context.

    """

    dataset_id: str
    name: str
    version_id: str | None = None


@dataclass(frozen=True)
class SchemaColumnRoutingEvidence:
    """Represent one authorized schema column returned by schema tools.

    Args:
        name: Canonical schema column name.
        data_type: Optional column data type from schema metadata.

    """

    name: str
    data_type: str = ""


@dataclass(frozen=True)
class AssistantToolCall:
    """Represent one deterministic assistant tool call.

    Args:
        name: Canonical BIF name.
        arguments: JSON-compatible BIF arguments.

    """

    name: str
    arguments: dict[str, object]


@dataclass(frozen=True)
class ExplorationRoutingRequest:
    """Represent a deterministic exploration-routing request.

    Args:
        question: User question to route.
        tenant_id: Trusted tenant identifier from runtime context.
        dataset_id: Optional trusted or explicit dataset identifier.
        dataset_name: Optional user-supplied dataset name.
        version_id: Optional explicit or trusted dataset version identifier.
        dataset_candidates: Authorized dataset candidates from existing discovery tools.
        schema_columns: Authoritative schema metadata from existing schema tools.
        limit: Optional user-supplied bounded result limit.
        retry_count: Number of deterministic routing retries already used.

    """

    question: str
    tenant_id: str
    dataset_id: str | None = None
    dataset_name: str | None = None
    version_id: str | None = None
    dataset_candidates: tuple[DatasetRoutingCandidate, ...] = ()
    schema_columns: tuple[SchemaColumnRoutingEvidence, ...] = ()
    limit: int | None = None
    retry_count: int = 0


@dataclass(frozen=True)
class ExplorationRoutingDecision:
    """Represent the result of deterministic exploration routing.

    Args:
        status: Routing outcome.
        operation: Selected exploration operation when known.
        dataset_id: Resolved dataset identifier when known.
        version_id: Resolved version identifier when provided by trusted context or tools.
        columns: Validated canonical schema columns.
        terms: Explicit search terms for search routing.
        match_mode: Supported search match mode.
        limit: Bounded limit to send to the exploration BIF.
        tool_calls: Deterministic tool calls required for the next step.
        candidate_datasets: Dataset candidates when dataset identity is ambiguous.
        candidate_columns: Column candidates when column identity is ambiguous.
        retry_count: Retry count represented by the decision.
        message: Safe user-facing routing explanation.

    """

    status: RoutingStatus
    operation: ExplorationOperation | None = None
    dataset_id: str | None = None
    version_id: str | None = None
    columns: tuple[str, ...] = ()
    terms: tuple[str, ...] = ()
    match_mode: SearchMatchMode | None = None
    limit: int = DEFAULT_EXPLORATION_LIMIT
    tool_calls: tuple[AssistantToolCall, ...] = ()
    candidate_datasets: tuple[str, ...] = ()
    candidate_columns: tuple[str, ...] = ()
    retry_count: int = 0
    message: str = ""


class ExplorationRoutingPolicy:
    """Select deterministic Resource Registry exploration BIF calls from user questions."""

    def is_creative_workflow_intent(self, question: str) -> bool:
        """Identify an opening instruction to prepare concepts or render specs.

        Args:
            question: Raw user instruction, optionally followed by brief content.

        Returns:
            True when the opening instruction belongs to normal creative orchestration.
        """
        return any(
            pattern.search(question) is not None
            for pattern in (_CONCEPT_WORKFLOW_PATTERN, _RENDER_SPEC_WORKFLOW_PATTERN)
        )

    def is_dataset_listing_intent(self, question: str) -> bool:
        """Return whether a question requests discovery of available datasets.

        Args:
            question: Raw user question.

        Returns:
            True when deterministic dataset listing should handle the question.
        """
        return self._is_dataset_listing_intent(question)

    def can_retry_after_error(self, *, error_code: str, retry_count: int) -> bool:
        """Return whether routing may retry after a safe deterministic error.

        Args:
            error_code: Sanitized tool or routing error code.
            retry_count: Number of retries already used for the current exploration intent.

        Returns:
            True only for recognized narrow correction cases within the retry budget.

        """

        normalized_code = str(error_code or "").strip().lower()
        if retry_count >= MAX_ROUTING_RETRIES:
            return False
        if normalized_code in NON_RETRYABLE_ROUTING_ERROR_CODES:
            return False
        return normalized_code in {
            "unknown_exploration_column",
            "invalid_request",
            "invalid_argument",
            "stale_schema",
            "schema_not_found",
            "version_not_found",
        }

    def route(
        self,
        request: ExplorationRoutingRequest,
    ) -> ExplorationRoutingDecision:
        """Route one user question to schema or exploration tool calls.

        Args:
            request: Immutable routing request with trusted context and optional schema evidence.

        Returns:
            A deterministic routing decision. Ready decisions contain only canonical
            `resource_registry_explore_dataset` calls.

        """

        question = request.question.strip()
        unsupported_decision = self._unsupported_decision(question)
        if unsupported_decision is not None:
            return unsupported_decision

        if self._is_dataset_listing_intent(question):
            arguments: dict[str, object] = {"tenant_id": request.tenant_id}
            search_term = self._extract_dataset_discovery_search_term(question)
            if search_term:
                arguments["search"] = search_term
            return ExplorationRoutingDecision(
                status="dataset_listing",
                tool_calls=(
                    AssistantToolCall(
                        name=LIST_DATASETS_TOOL_NAME,
                        arguments=arguments,
                    ),
                ),
                message="Route the question to deterministic dataset listing.",
            )

        operation = self._select_operation(question)
        if operation is None:
            return ExplorationRoutingDecision(
                status="bypass_normal_flow",
                message="The request is not supported by bounded dataset exploration operations.",
            )

        dataset_resolution = self._resolve_dataset(request)
        if dataset_resolution.status != "ready":
            return dataset_resolution

        if not request.schema_columns:
            return ExplorationRoutingDecision(
                status="needs_schema",
                operation=operation,
                dataset_id=dataset_resolution.dataset_id,
                version_id=dataset_resolution.version_id,
                tool_calls=(
                    AssistantToolCall(
                        name=SCHEMA_TOOL_NAME,
                        arguments=self._base_arguments(
                            tenant_id=request.tenant_id,
                            dataset_id=str(dataset_resolution.dataset_id),
                            version_id=dataset_resolution.version_id,
                        ),
                    ),
                ),
                message="Retrieve authoritative schema metadata before exploring the dataset.",
            )

        if operation == "search_values":
            terms = self._extract_search_terms(question)
            if not terms:
                return ExplorationRoutingDecision(
                    status="missing_terms",
                    operation=operation,
                    dataset_id=dataset_resolution.dataset_id,
                    version_id=dataset_resolution.version_id,
                    message="Search exploration requires explicit search terms.",
                )
            match_mode = self._select_match_mode(question)
            if match_mode is None:
                return ExplorationRoutingDecision(
                    status="exploration_ambiguity",
                    operation=operation,
                    dataset_id=dataset_resolution.dataset_id,
                    version_id=dataset_resolution.version_id,
                    terms=terms,
                    message="The requested search match behavior is not supported.",
                )
        else:
            terms = ()
            match_mode = None

        column_resolution = self._resolve_columns(
            question=question,
            operation=operation,
            schema_columns=request.schema_columns,
            retry_count=request.retry_count,
        )
        if column_resolution.status != "ready":
            return ExplorationRoutingDecision(
                status=column_resolution.status,
                operation=operation,
                dataset_id=dataset_resolution.dataset_id,
                version_id=dataset_resolution.version_id,
                candidate_columns=column_resolution.candidate_columns,
                retry_count=column_resolution.retry_count,
                message=column_resolution.message,
            )

        limit = self._normalize_limit(request.limit, question)
        arguments = self._base_arguments(
            tenant_id=request.tenant_id,
            dataset_id=str(dataset_resolution.dataset_id),
            version_id=dataset_resolution.version_id,
        )
        arguments.update(
            {
                "operation": operation,
                "columns": list(column_resolution.columns),
                "limit": limit,
            },
        )
        if terms:
            arguments["terms"] = list(terms)
        if match_mode is not None:
            arguments["match_mode"] = match_mode

        return ExplorationRoutingDecision(
            status="ready",
            operation=operation,
            dataset_id=dataset_resolution.dataset_id,
            version_id=dataset_resolution.version_id,
            columns=column_resolution.columns,
            terms=terms,
            match_mode=match_mode,
            limit=limit,
            tool_calls=(AssistantToolCall(name=EXPLORATION_TOOL_NAME, arguments=arguments),),
            retry_count=column_resolution.retry_count,
            message="Route the question to bounded dataset exploration evidence.",
        )

    def _unsupported_decision(self, question: str) -> ExplorationRoutingDecision | None:
        if self.is_creative_workflow_intent(question):
            return self._bypass_decision(
                "Creative workflow requests belong to normal agent/tool orchestration."
            )
        unconditional_rules: tuple[tuple[re.Pattern[str], str, RoutingStatus], ...] = (
            (
                _DOCUMENT_TOOL_REQUEST_PATTERN,
                "Document retrieval instructions belong to normal agent/tool orchestration.",
                "bypass_normal_flow",
            ),
            (
                _DATASET_SELECTION_METADATA_REQUEST_PATTERN,
                "Dataset selection metadata requests belong to normal agent/tool orchestration.",
                "bypass_normal_flow",
            ),
            (
                _JOIN_PATTERN,
                "The request requires multiple datasets and is outside exploration routing.",
                "bypass_normal_flow",
            ),
            (
                _MULTI_DATASET_COMPARISON_PATTERN,
                "The request requires multi-dataset comparison and is outside exploration routing.",
                "bypass_normal_flow",
            ),
            (
                _TRANSFORM_PATTERN,
                "The request requires transformation behavior and is outside exploration routing.",
                "bypass_normal_flow",
            ),
            (
                _RECOMMEND_PATTERN,
                "The request requires recommendations and is outside exploration routing.",
                "bypass_normal_flow",
            ),
            (
                _NUMERIC_RANKING_PATTERN,
                "The request requires numeric ranking that is outside exploration routing.",
                "bypass_normal_flow",
            ),
            (
                _TOP_SAMPLE_PATTERN,
                "The request combines ranking and sampling and needs clarification.",
                "exploration_ambiguity",
            ),
            (
                _COMBINED_SEARCH_FREQUENCY_PATTERN,
                "The request combines search and frequency ranking and needs clarification.",
                "exploration_ambiguity",
            ),
            (
                _UNIQUE_MISSING_PATTERN,
                "The request combines uniqueness and missingness and needs clarification.",
                "exploration_ambiguity",
            ),
            (
                _NULL_SAMPLE_PATTERN,
                "The request requires filtered null sampling, which exploration does not support.",
                "exploration_ambiguity",
            ),
        )
        for pattern, message, status in unconditional_rules:
            if pattern.search(question):
                return self._unsupported_decision_for_status(message=message, status=status)

        if _BARE_DATASET_PROFILE_PATTERN.search(question):
            return self._bypass_decision(
                "The request should use the dataset profile read tool.",
            )
        if _FILTER_PATTERN.search(question) and not _SEARCH_PATTERN.search(question):
            return self._bypass_decision(
                "The request requires filtering that is outside exploration routing.",
            )
        if _AGGREGATION_PATTERN.search(question) and not _PROFILE_PATTERN.search(question):
            return self._bypass_decision(
                "The request requires aggregation that is outside exploration routing.",
            )
        return None

    def _is_dataset_listing_intent(self, question: str) -> bool:
        normalized_question = self._normalize_text(question)
        if not normalized_question:
            return False
        if self._is_active_dataset_reference(normalized_question):
            return False
        if _DATASET_LISTING_FALSE_POSITIVE_PATTERN.search(normalized_question):
            return False
        if not _DATASET_LISTING_SUBJECT_PATTERN.search(normalized_question):
            return False
        has_listing_action = _DATASET_LISTING_ACTION_PATTERN.search(normalized_question) is not None
        has_plural_dataset_subject = (
            _DATASET_LISTING_PLURAL_SUBJECT_PATTERN.search(normalized_question) is not None
        )
        has_listing_scope = _DATASET_LISTING_SCOPE_PATTERN.search(normalized_question) is not None
        has_my_datasets_phrase = bool(re.search(r"\bmy datasets?\b", normalized_question))
        has_access_phrase = bool(
            re.search(r"\bdatasets?\s+(?:i\s+)?(?:can\s+)?access\b", normalized_question),
        )
        has_available_phrase = bool(
            re.search(r"\bdatasets?\s+available(?:\s+to\s+me)?\b", normalized_question),
        )
        has_discovery_search_term = (
            self._extract_dataset_discovery_search_term(question) is not None
        )
        has_bare_dataset_discovery = has_listing_action and has_plural_dataset_subject
        return has_discovery_search_term or (
            has_listing_action
            and (
                has_bare_dataset_discovery
                or has_listing_scope
                or has_my_datasets_phrase
                or has_access_phrase
                or has_available_phrase
            )
        )

    def _extract_dataset_discovery_search_term(self, question: str) -> str | None:
        if self._is_active_dataset_reference(question):
            return None
        match = _DATASET_DISCOVERY_TERM_PATTERN.search(question)
        if match is None:
            return None
        return self._normalize_dataset_discovery_search_term(match.group(1))

    def _normalize_dataset_discovery_search_term(self, value: str) -> str | None:
        term = str(value or "").strip(" \t\r\n\"'“”.,!?")
        previous_term = ""
        while previous_term != term:
            previous_term = term
            term = _TENANT_CONTEXT_SUFFIX_PATTERN.sub("", term).strip(" \t\r\n\"'“”.,!?")
        term = re.sub(r"\s+", " ", term).strip()
        return term or None

    def _is_active_dataset_reference(self, question: str) -> bool:
        return _ACTIVE_DATASET_REFERENCE_PATTERN.search(question) is not None

    def _unsupported_decision_for_status(
        self,
        *,
        message: str,
        status: RoutingStatus,
    ) -> ExplorationRoutingDecision:
        if status == "bypass_normal_flow":
            return self._bypass_decision(message)
        return self._exploration_ambiguity_decision(message)

    def _bypass_decision(self, message: str) -> ExplorationRoutingDecision:
        return ExplorationRoutingDecision(status="bypass_normal_flow", message=message)

    def _exploration_ambiguity_decision(self, message: str) -> ExplorationRoutingDecision:
        return ExplorationRoutingDecision(status="exploration_ambiguity", message=message)

    def _select_operation(self, question: str) -> ExplorationOperation | None:
        if _SEARCH_PATTERN.search(question):
            return "search_values"
        if _PROFILE_PATTERN.search(question):
            return "column_profile"
        if _TOP_PATTERN.search(question):
            return "top_values"
        if _DISTINCT_PATTERN.search(question):
            return "distinct_values"
        if _SAMPLE_PATTERN.search(question):
            return "sample_values"
        return None

    def _resolve_dataset(
        self,
        request: ExplorationRoutingRequest,
    ) -> ExplorationRoutingDecision:
        if request.dataset_id:
            version_id = request.version_id or self._version_for_dataset_id(
                request.dataset_id,
                request.dataset_candidates,
            )
            return ExplorationRoutingDecision(
                status="ready",
                dataset_id=request.dataset_id,
                version_id=version_id,
            )

        if request.dataset_name:
            requested_name = self._normalize_text(request.dataset_name)
            matches = tuple(
                candidate
                for candidate in request.dataset_candidates
                if self._normalize_text(candidate.name) == requested_name
            )
            if len(matches) == 1:
                return ExplorationRoutingDecision(
                    status="ready",
                    dataset_id=matches[0].dataset_id,
                    version_id=request.version_id or matches[0].version_id,
                )
            if len(matches) > 1:
                return ExplorationRoutingDecision(
                    status="ambiguous_dataset",
                    candidate_datasets=tuple(candidate.name for candidate in matches),
                    message="Multiple authorized datasets match the requested name.",
                )

        if request.dataset_candidates:
            return ExplorationRoutingDecision(
                status="needs_dataset",
                tool_calls=(
                    AssistantToolCall(
                        name=LIST_DATASETS_TOOL_NAME,
                        arguments={"tenant_id": request.tenant_id, "limit": 10},
                    ),
                ),
                candidate_datasets=tuple(
                    candidate.name for candidate in request.dataset_candidates
                ),
                message="Select one authorized dataset before exploring values.",
            )
        return ExplorationRoutingDecision(
            status="needs_dataset",
            tool_calls=(
                AssistantToolCall(
                    name=LIST_DATASETS_TOOL_NAME,
                    arguments={"tenant_id": request.tenant_id, "limit": 10},
                ),
            ),
            message="Identify the registered dataset before exploring values.",
        )

    def _version_for_dataset_id(
        self,
        dataset_id: str,
        candidates: tuple[DatasetRoutingCandidate, ...],
    ) -> str | None:
        for candidate in candidates:
            if candidate.dataset_id == dataset_id:
                return candidate.version_id
        return None

    def _resolve_columns(
        self,
        *,
        question: str,
        operation: ExplorationOperation,
        schema_columns: tuple[SchemaColumnRoutingEvidence, ...],
        retry_count: int,
    ) -> ExplorationRoutingDecision:
        mentions = self._extract_column_mentions(question, schema_columns)
        if not mentions:
            return ExplorationRoutingDecision(
                status="unknown_column",
                message="No requested column matched the authorized schema.",
            )

        resolved: list[str] = []
        ambiguous: list[str] = []
        for mention in mentions:
            match = self._match_column(mention, schema_columns)
            if match.status == "ready":
                resolved.extend(column for column in match.columns if column not in resolved)
                continue
            if match.status == "ambiguous_column":
                ambiguous.extend(match.candidate_columns)
                continue
            if retry_count >= MAX_ROUTING_RETRIES:
                return match
            retry_match = self._match_column(mention, schema_columns, allow_retry=True)
            if retry_match.status != "ready":
                return retry_match
            resolved.extend(column for column in retry_match.columns if column not in resolved)
            retry_count += 1

        if ambiguous:
            return ExplorationRoutingDecision(
                status="ambiguous_column",
                candidate_columns=tuple(sorted(set(ambiguous))),
                retry_count=retry_count,
                message="Multiple schema columns match the requested column.",
            )
        if not resolved:
            return ExplorationRoutingDecision(
                status="unknown_column",
                retry_count=retry_count,
                message="No requested column matched the authorized schema.",
            )
        if operation in {"distinct_values", "top_values", "column_profile"} and len(resolved) != 1:
            return ExplorationRoutingDecision(
                status="ambiguous_column",
                candidate_columns=tuple(resolved),
                retry_count=retry_count,
                message=f"{operation} requires exactly one validated schema column.",
            )
        return ExplorationRoutingDecision(
            status="ready",
            columns=tuple(resolved),
            retry_count=retry_count,
        )

    def _extract_column_mentions(
        self,
        question: str,
        schema_columns: tuple[SchemaColumnRoutingEvidence, ...],
    ) -> tuple[str, ...]:
        mentions: list[str] = []
        for column in schema_columns:
            if self._contains_column_reference(question, column.name):
                mentions.append(column.name)
        search_terms = set(self._extract_search_terms(question))
        for quoted_phrase in _QUOTED_PHRASE_PATTERN.findall(question):
            clean_phrase = quoted_phrase.strip()
            if clean_phrase in search_terms:
                continue
            if clean_phrase and clean_phrase not in mentions:
                mentions.append(clean_phrase)
        return tuple(mentions)

    def _contains_column_reference(self, question: str, column_name: str) -> bool:
        column_parts = re.findall(r"[^\W_]+", column_name.lower())
        if not column_parts:
            return False
        # Normalize separators within a complete name, never within another identifier.
        # In particular, `brand` inside `get_brand_visual_guidance_documents` is not a column.
        column_pattern = r"[\W_]*".join(re.escape(part) for part in column_parts)
        return re.search(rf"(?<!\w){column_pattern}(?!\w)", question, re.IGNORECASE) is not None

    def _match_column(
        self,
        mention: str,
        schema_columns: tuple[SchemaColumnRoutingEvidence, ...],
        *,
        allow_retry: bool = False,
    ) -> ExplorationRoutingDecision:
        lower_matches = tuple(
            column.name for column in schema_columns if column.name.lower() == mention.lower()
        )
        if len(lower_matches) > 1:
            return ExplorationRoutingDecision(
                status="ambiguous_column",
                candidate_columns=lower_matches,
                message="Multiple case-insensitive schema columns match the requested column.",
            )

        exact_matches = tuple(column.name for column in schema_columns if column.name == mention)
        if len(exact_matches) == 1:
            return ExplorationRoutingDecision(status="ready", columns=exact_matches)
        if len(exact_matches) > 1:
            return ExplorationRoutingDecision(
                status="ambiguous_column",
                candidate_columns=exact_matches,
                message="Multiple exact schema columns match the requested column.",
            )

        if len(lower_matches) == 1:
            return ExplorationRoutingDecision(
                status="ready",
                columns=lower_matches,
                retry_count=1 if allow_retry else 0,
            )

        normalized_matches = tuple(
            column.name
            for column in schema_columns
            if self._compact_text(column.name) == self._compact_text(mention)
        )
        if len(normalized_matches) == 1:
            return ExplorationRoutingDecision(
                status="ready",
                columns=normalized_matches,
                retry_count=1 if allow_retry else 0,
            )
        if len(normalized_matches) > 1:
            return ExplorationRoutingDecision(
                status="ambiguous_column",
                candidate_columns=normalized_matches,
                message="Multiple normalized schema columns match the requested column.",
            )
        return ExplorationRoutingDecision(
            status="unknown_column",
            message="The requested column does not exist in the authorized schema.",
        )

    def _extract_search_terms(self, question: str) -> tuple[str, ...]:
        quoted_terms = tuple(
            term.strip() for term in _QUOTED_PHRASE_PATTERN.findall(question) if term.strip()
        )
        if quoted_terms:
            return quoted_terms
        match = _TERM_AFTER_PATTERN.search(question)
        if match:
            term = match.group(1).strip()
            if term:
                return (term,)
        return ()

    def _select_match_mode(self, question: str) -> SearchMatchMode | None:
        if _UNSUPPORTED_MATCH_MODE_PATTERN.search(question):
            return None
        normalized = question.lower()
        if "starts with" in normalized or "start with" in normalized:
            return "prefix"
        if "equals" in normalized or "exactly" in normalized:
            return "exact"
        return "contains"

    def _normalize_limit(self, limit: int | None, question: str) -> int:
        if limit is not None:
            if isinstance(limit, bool):
                return DEFAULT_EXPLORATION_LIMIT
            try:
                parsed_limit = int(limit)
            except (TypeError, ValueError):
                return DEFAULT_EXPLORATION_LIMIT
            return min(max(parsed_limit, 1), MAX_EXPLORATION_LIMIT)
        numbers = tuple(int(match) for match in _NUMBER_PATTERN.findall(question))
        if numbers:
            return min(max(numbers[0], 1), MAX_EXPLORATION_LIMIT)
        if _SHOW_ALL_PATTERN.search(question):
            return MAX_EXPLORATION_LIMIT
        return DEFAULT_EXPLORATION_LIMIT

    def _base_arguments(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        version_id: str | None,
    ) -> dict[str, object]:
        arguments: dict[str, object] = {
            "tenant_id": tenant_id,
            "dataset_id": dataset_id,
        }
        if version_id:
            arguments["version_id"] = version_id
        return arguments

    def _normalize_text(self, value: str) -> str:
        return " ".join(re.split(r"[^a-z0-9]+", value.lower())).strip()

    def _compact_text(self, value: str) -> str:
        return "".join(re.split(r"[^a-z0-9]+", value.lower()))
