"""Test deterministic Resource Registry Assistant exploration routing."""

from __future__ import annotations

import pytest

from resource_registry.assistant.exploration_routing import (
    DEFAULT_EXPLORATION_LIMIT,
    EXPLORATION_TOOL_NAME,
    LIST_DATASETS_TOOL_NAME,
    MAX_EXPLORATION_LIMIT,
    MAX_ROUTING_RETRIES,
    SCHEMA_TOOL_NAME,
    DatasetRoutingCandidate,
    ExplorationRoutingDecision,
    ExplorationRoutingPolicy,
    ExplorationRoutingRequest,
    SchemaColumnRoutingEvidence,
)

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DATASET_ID = "22222222-2222-2222-2222-222222222222"
VERSION_ID = "33333333-3333-3333-3333-333333333333"
EXPLICIT_EXPLORATION_LIMIT = 12
EXPECTED_DEFAULT_EXPLORATION_LIMIT = 5


@pytest.fixture
def routing_policy() -> ExplorationRoutingPolicy:
    """Build the deterministic routing policy under test."""
    return ExplorationRoutingPolicy()


def _request(  # noqa: PLR0913
    question: str,
    *,
    schema_columns: tuple[SchemaColumnRoutingEvidence, ...] = (
        SchemaColumnRoutingEvidence("category", "VARCHAR"),
    ),
    dataset_id: str | None = DATASET_ID,
    dataset_name: str | None = None,
    candidates: tuple[DatasetRoutingCandidate, ...] = (),
    version_id: str | None = VERSION_ID,
    limit: int | None = None,
    retry_count: int = 0,
) -> ExplorationRoutingRequest:
    return ExplorationRoutingRequest(
        question=question,
        tenant_id=TENANT_ID,
        dataset_id=dataset_id,
        dataset_name=dataset_name,
        version_id=version_id,
        dataset_candidates=candidates,
        schema_columns=schema_columns,
        limit=limit,
        retry_count=retry_count,
    )


def _ready_arguments(decision: ExplorationRoutingDecision) -> dict[str, object]:
    assert decision.status == "ready"
    assert len(decision.tool_calls) == 1
    assert decision.tool_calls[0].name == EXPLORATION_TOOL_NAME
    return decision.tool_calls[0].arguments


@pytest.mark.parametrize(
    ("question", "expected_operation", "schema_columns"),
    [
        (
            "What unique values exist in category?",
            "distinct_values",
            (SchemaColumnRoutingEvidence("category", "VARCHAR"),),
        ),
        (
            "Show the most common ticket_type values.",
            "top_values",
            (SchemaColumnRoutingEvidence("ticket_type", "VARCHAR"),),
        ),
        (
            "Give me a few sample values for device_type and os_name.",
            "sample_values",
            (
                SchemaColumnRoutingEvidence("device_type", "VARCHAR"),
                SchemaColumnRoutingEvidence("os_name", "VARCHAR"),
            ),
        ),
        (
            "Find values containing \"premium\" in product_name.",
            "search_values",
            (SchemaColumnRoutingEvidence("product_name", "VARCHAR"),),
        ),
        (
            "How complete is region_code?",
            "column_profile",
            (SchemaColumnRoutingEvidence("region_code", "VARCHAR"),),
        ),
    ],
)
def test_supported_intents_route_to_approved_operations(
    routing_policy: ExplorationRoutingPolicy,
    question: str,
    expected_operation: str,
    schema_columns: tuple[SchemaColumnRoutingEvidence, ...],
) -> None:
    """Route neutral-domain questions to the five approved exploration operations."""
    decision = routing_policy.route(_request(question, schema_columns=schema_columns))

    arguments = _ready_arguments(decision)
    assert arguments["operation"] == expected_operation
    assert arguments["tenant_id"] == TENANT_ID
    assert arguments["dataset_id"] == DATASET_ID
    assert arguments["version_id"] == VERSION_ID
    assert "sql" not in arguments


def test_schema_is_requested_before_exploration(routing_policy: ExplorationRoutingPolicy) -> None:
    """Require authoritative schema evidence before exploration tool execution."""
    decision = routing_policy.route(
        _request("What unique values exist in category?", schema_columns=()),
    )

    assert decision.status == "needs_schema"
    assert [tool_call.name for tool_call in decision.tool_calls] == [SCHEMA_TOOL_NAME]
    assert all(tool_call.name != EXPLORATION_TOOL_NAME for tool_call in decision.tool_calls)


@pytest.mark.parametrize(
    "question",
    [
        "Register or update the attached file in the Resource Registry for my current "
        "Workspace. If it matches an existing dataset, replace its contents with a new "
        "version. Then check the result and tell me whether it was created, updated, "
        "or had no changes.",
        "Replace the dataset with the uploaded sales.csv file and show sample values "
        "after it finishes.",
    ],
)
def test_dataset_import_requests_bypass_exploration(
    routing_policy: ExplorationRoutingPolicy,
    question: str,
) -> None:
    """Leave dataset writes to the import BIF even when search words occur in the prompt."""
    decision = routing_policy.route(_request(question, schema_columns=()))

    assert decision.status == "bypass_normal_flow"
    assert decision.tool_calls == ()


@pytest.mark.parametrize(
    ("question", "expected_command"),
    [
        ("Register or update the attached file in my current Workspace.", True),
        ("Register the attached file.", True),
        ("Registra este archivo.", True),
        ("Please replace this dataset with the attached file.", True),
        ("Upload this file to the Resource Registry.", True),
        ("Sube este archivo al Resource Registry.", True),
        ("Upload this CSV to the current Workspace.", True),
        ("Update this dataset using the attached file.", True),
        ("Use resource_registry_replace_dataset with upload_id and dataset_id.", True),
        ("Upload this file.", False),
        ("Sube este archivo.", False),
        ("Import the attached file.", False),
        ("Upload it.\nUploaded file(s): sales.csv", False),
        ("Upload this CSV to chat.", False),
        ("Sube este archivo al chat.", False),
        ("Register this file as a chat source document.", False),
        ("Summarize the attached CSV.", False),
        ("Analyze the attached XLSX.", False),
        ("What would happen if I upload this file?", False),
        ("Do not register the attached file; explain the process.", False),
        ("Update the chart for this dataset.\nUploaded file(s): sales.json", False),
    ],
)
def test_only_explicit_file_import_commands_trigger_automatic_write(
    routing_policy: ExplorationRoutingPolicy, question: str, expected_command: bool
) -> None:
    """Distinguish an import command from a hypothetical or another task."""
    assert routing_policy.is_explicit_dataset_import_command(question) is expected_command


def test_exact_schema_column_is_accepted(routing_policy: ExplorationRoutingPolicy) -> None:
    """Accept exact canonical schema columns."""
    decision = routing_policy.route(_request("List distinct category values."))

    arguments = _ready_arguments(decision)
    assert arguments["columns"] == ["category"]


@pytest.mark.parametrize("has_schema", [False, True])
@pytest.mark.parametrize(
    "question",
    [
        "Continue with the three concepts for Young families using the selected dataset. "
        "Retrieve the template assets and their matching Connections Theme JSON through "
        "get_brand_visual_guidance_documents.",
        "Generate three concepts using matching template assets from the selected dataset.",
        "Please refine the concepts with matching themes and complete typography.",
        "Retrieve matching Connections Theme JSON through get_brand_visual_guidance_documents.",
        "Use get_brand_tone_theme_documents to find the approved theme.",
    ],
)
def test_creative_workflow_requests_bypass_column_exploration(
    routing_policy: ExplorationRoutingPolicy,
    question: str,
    has_schema: bool,
) -> None:
    """Leave concept and guidance-tool requests to the agent without schema or value calls."""
    decision = routing_policy.route(
        _request(
            question,
            schema_columns=(SchemaColumnRoutingEvidence("brand"),) if has_schema else (),
        ),
    )

    assert decision.status == "bypass_normal_flow"
    assert decision.tool_calls == ()


@pytest.mark.parametrize(
    "question",
    [
        "For the previously generated concepts, provide the exact verified dataset_id and "
        "version_id required for rendering. Also provide the complete Theme Library JSON. "
        "Return the verified pair, preserving IDs exactly.",
        'Search terms: "Bank of America" AND "Soccer at Schools" AND "Trade Desk". '
        "Retrieve the exact verified dataset_id and version_id for the already-selected "
        "concepts. Return the verified pair and full Theme Library JSON.",
        "Return the verified `dataset_id` and `version_id` for the selected render, "
        "preserving IDs exactly.",
    ],
)
def test_dataset_selection_metadata_requests_bypass_column_exploration(
    routing_policy: ExplorationRoutingPolicy, question: str
) -> None:
    """Leave dataset and version ID retrieval to the agent's Registry tools."""
    decision = routing_policy.route(
        _request(question, schema_columns=(SchemaColumnRoutingEvidence("brand"),))
    )

    assert decision.status == "bypass_normal_flow"
    assert decision.tool_calls == ()


def test_value_search_with_explicit_dataset_selectors_still_routes(
    routing_policy: ExplorationRoutingPolicy,
) -> None:
    """Keep actual value searches eligible when they include selector IDs."""
    decision = routing_policy.route(
        _request(
            f"Find values exactly 'premium' in product_name using dataset_id {DATASET_ID} "
            f"and version_id {VERSION_ID}.",
            schema_columns=(SchemaColumnRoutingEvidence("product_name"),),
        )
    )

    assert _ready_arguments(decision)["operation"] == "search_values"


@pytest.mark.parametrize(
    "question",
    [
        "Find values containing 'Global' using get_brand_visual_guidance_documents.",
        "Find values containing 'Global' in brand_name.",
        "Find values containing 'Global' in rebranding.",
        "Find values containing 'Global' in brandish.",
    ],
)
def test_column_names_inside_other_identifiers_do_not_match(
    routing_policy: ExplorationRoutingPolicy,
    question: str,
) -> None:
    """Reject substring matches as evidence for a schema column reference."""
    decision = routing_policy.route(
        _request(question, schema_columns=(SchemaColumnRoutingEvidence("brand"),)),
    )

    assert decision.status == "unknown_column"
    assert decision.tool_calls == ()


@pytest.mark.parametrize("column_reference", ["brand", "BRAND", "`brand`", "(brand)"])
def test_explicit_brand_search_remains_supported(
    routing_policy: ExplorationRoutingPolicy,
    column_reference: str,
) -> None:
    """Keep explicit inventory searches even when a search literal names a guidance tool."""
    decision = routing_policy.route(
        _request(
            f"Find values containing 'get_brand_visual_guidance_documents' in {column_reference}.",
            schema_columns=(SchemaColumnRoutingEvidence("brand"),),
        ),
    )

    arguments = _ready_arguments(decision)
    assert arguments["columns"] == ["brand"]
    assert arguments["terms"] == ["get_brand_visual_guidance_documents"]


@pytest.mark.parametrize(
    "column_reference", ["font_family", "font family", "font-family", "FontFamily"]
)
def test_compound_column_references_preserve_normalized_matching(
    routing_policy: ExplorationRoutingPolicy,
    column_reference: str,
) -> None:
    """Keep supported spellings of complete compound column names."""
    decision = routing_policy.route(
        _request(
            f"List distinct {column_reference} values.",
            schema_columns=(SchemaColumnRoutingEvidence("font_family"),),
        ),
    )

    arguments = _ready_arguments(decision)
    assert arguments["columns"] == ["font_family"]


@pytest.mark.parametrize("column_name", ["brand_name", "font_family", "categoría"])
def test_exact_column_reference_does_not_select_identifier_fragments(
    routing_policy: ExplorationRoutingPolicy,
    column_name: str,
) -> None:
    """Preserve complete identifiers, including Unicode, without selecting component columns."""
    decision = routing_policy.route(
        _request(
            f"Find values matching 'Global' in {column_name}.",
            schema_columns=(
                SchemaColumnRoutingEvidence("brand"),
                SchemaColumnRoutingEvidence("family"),
                SchemaColumnRoutingEvidence(column_name),
            ),
        ),
    )

    arguments = _ready_arguments(decision)
    assert arguments["columns"] == [column_name]
    assert arguments["terms"] == ["Global"]


def test_unique_normalized_column_match_is_accepted_once(
    routing_policy: ExplorationRoutingPolicy,
) -> None:
    """Accept one unambiguous punctuation-normalized column correction."""
    decision = routing_policy.route(
        _request(
            "List distinct \"Product Status\" values.",
            schema_columns=(SchemaColumnRoutingEvidence("product_status", "VARCHAR"),),
        ),
    )

    arguments = _ready_arguments(decision)
    assert arguments["columns"] == ["product_status"]
    assert decision.retry_count <= MAX_ROUTING_RETRIES


def test_ambiguous_normalized_column_does_not_execute(
    routing_policy: ExplorationRoutingPolicy,
) -> None:
    """Require clarification when normalized column matching is ambiguous."""
    decision = routing_policy.route(
        _request(
            "List distinct \"status\" values.",
            schema_columns=(
                SchemaColumnRoutingEvidence("status", "VARCHAR"),
                SchemaColumnRoutingEvidence("Status", "VARCHAR"),
            ),
        ),
    )

    assert decision.status == "ambiguous_column"
    assert decision.tool_calls == ()


def test_unknown_column_does_not_execute(routing_policy: ExplorationRoutingPolicy) -> None:
    """Reject natural-language labels that are not validated schema columns."""
    decision = routing_policy.route(
        _request(
            "List distinct location values.",
            schema_columns=(
                SchemaColumnRoutingEvidence("city", "VARCHAR"),
                SchemaColumnRoutingEvidence("region", "VARCHAR"),
            ),
        ),
    )

    assert decision.status == "unknown_column"
    assert decision.tool_calls == ()


def test_dataset_context_is_preserved(routing_policy: ExplorationRoutingPolicy) -> None:
    """Preserve trusted dataset and version context."""
    decision = routing_policy.route(_request("List distinct category values."))

    arguments = _ready_arguments(decision)
    assert arguments["dataset_id"] == DATASET_ID
    assert arguments["version_id"] == VERSION_ID


def test_absent_version_uses_existing_bif_resolution(
    routing_policy: ExplorationRoutingPolicy,
) -> None:
    """Omit version_id when trusted context has not selected an explicit version."""
    decision = routing_policy.route(_request("List distinct category values.", version_id=None))

    arguments = _ready_arguments(decision)
    assert "version_id" not in arguments


def test_ambiguous_dataset_identity_does_not_execute(
    routing_policy: ExplorationRoutingPolicy,
) -> None:
    """Reject multiple matching dataset names."""
    decision = routing_policy.route(
        _request(
            "List distinct category values.",
            dataset_id=None,
            dataset_name="inventory",
            candidates=(
                DatasetRoutingCandidate("dataset-a", "Inventory"),
                DatasetRoutingCandidate("dataset-b", "inventory"),
            ),
        ),
    )

    assert decision.status == "ambiguous_dataset"
    assert decision.tool_calls == ()


def test_missing_dataset_requests_existing_dataset_listing_tool(
    routing_policy: ExplorationRoutingPolicy,
) -> None:
    """Use existing dataset discovery tools when dataset identity is absent."""
    decision = routing_policy.route(
        _request("List distinct category values.", dataset_id=None, candidates=()),
    )

    assert decision.status == "needs_dataset"
    assert decision.tool_calls[0].name == LIST_DATASETS_TOOL_NAME
    assert decision.tool_calls[0].arguments["tenant_id"] == TENANT_ID


@pytest.mark.parametrize(
    "question",
    [
        "Find datasets",
        "Search datasets",
        "Discover datasets",
        "Show datasets",
        "List datasets",
        "Which datasets are available?",
    ],
)
def test_bare_dataset_discovery_routes_to_listing_without_search(
    routing_policy: ExplorationRoutingPolicy,
    question: str,
) -> None:
    """Route bare dataset discovery to listing even when a dataset is active."""
    decision = routing_policy.route(_request(question))

    assert decision.status == "dataset_listing"
    assert len(decision.tool_calls) == 1
    assert decision.tool_calls[0].name == LIST_DATASETS_TOOL_NAME
    assert decision.tool_calls[0].arguments == {"tenant_id": TENANT_ID}


@pytest.mark.parametrize(
    ("question", "search_term"),
    [
        ("Find datasets related to financial audiences", "financial audiences"),
        ("Search datasets for demographics", "demographics"),
        ("Which datasets contain audience information?", "audience information"),
        ("Discover datasets about travel", "travel"),
        ("Find datasets mentioning demographics", "demographics"),
        ("Datasets with market trends", "market trends"),
        (
            "Find datasets related to financial audiences for this tenant",
            "financial audiences",
        ),
        (
            "Search datasets for demographics for tenant 11111111-1111-1111-1111-111111111111",
            "demographics",
        ),
    ],
)
def test_dataset_discovery_search_precedes_active_dataset_exploration(
    routing_policy: ExplorationRoutingPolicy,
    question: str,
    search_term: str,
) -> None:
    """Route dataset-subject search to discovery even when a dataset is active."""
    decision = routing_policy.route(_request(question))

    assert decision.status == "dataset_listing"
    assert len(decision.tool_calls) == 1
    assert decision.tool_calls[0].name == LIST_DATASETS_TOOL_NAME
    assert decision.tool_calls[0].arguments == {
        "tenant_id": TENANT_ID,
        "search": search_term,
    }


@pytest.mark.parametrize(
    ("question", "schema_column", "expected_term"),
    [
        (
            "Search the active dataset for financial audiences",
            "financial_audiences",
            "financial audiences",
        ),
        ("Search the current dataset for demographics", "demographics", "demographics"),
        ("Search this dataset for travel", "travel", "travel"),
        ("Find rows mentioning travel", "travel", "travel"),
        ("Find values containing demographics", "demographics", "demographics"),
    ],
)
def test_active_dataset_searches_do_not_route_to_dataset_discovery(
    routing_policy: ExplorationRoutingPolicy,
    question: str,
    schema_column: str,
    expected_term: str,
) -> None:
    """Keep non-dataset value search on the active dataset exploration path."""
    decision = routing_policy.route(
        _request(
            question,
            schema_columns=(SchemaColumnRoutingEvidence(schema_column, "VARCHAR"),),
        ),
    )

    arguments = _ready_arguments(decision)
    assert arguments["operation"] == "search_values"
    assert arguments["dataset_id"] == DATASET_ID
    assert arguments["columns"] == [schema_column]
    assert arguments["terms"] == [expected_term]


def test_active_dataset_record_search_does_not_route_to_dataset_discovery(
    routing_policy: ExplorationRoutingPolicy,
) -> None:
    """Keep selected-dataset container references out of dataset discovery."""
    decision = routing_policy.route(
        _request(
            "Find records in the selected dataset",
            schema_columns=(SchemaColumnRoutingEvidence("records", "VARCHAR"),),
        ),
    )

    arguments = _ready_arguments(decision)
    assert arguments["operation"] == "search_values"
    assert arguments["dataset_id"] == DATASET_ID
    assert arguments["columns"] == ["records"]


@pytest.mark.parametrize(
    ("question", "expected_mode"),
    [
        ("Find values containing \"premium\" in product_name.", "contains"),
        ("Find values exactly \"premium\" in product_name.", "exact"),
        ("Find values that start with \"ret\" in product_name.", "prefix"),
    ],
)
def test_search_match_modes_are_supported(
    routing_policy: ExplorationRoutingPolicy,
    question: str,
    expected_mode: str,
) -> None:
    """Map search wording only to supported Story 6.2 match modes."""
    decision = routing_policy.route(
        _request(question, schema_columns=(SchemaColumnRoutingEvidence("product_name"),)),
    )

    arguments = _ready_arguments(decision)
    assert arguments["operation"] == "search_values"
    assert arguments["terms"] == ["premium"] or arguments["terms"] == ["ret"]
    assert arguments["match_mode"] == expected_mode


def test_unsupported_search_mode_does_not_execute(
    routing_policy: ExplorationRoutingPolicy,
) -> None:
    """Reject unsupported match behavior instead of silently downgrading it."""
    decision = routing_policy.route(
        _request(
            "Find fuzzy matches for \"premium\" in product_name.",
            schema_columns=(SchemaColumnRoutingEvidence("product_name"),),
        ),
    )

    assert decision.status == "exploration_ambiguity"
    assert decision.tool_calls == ()


def test_missing_search_terms_do_not_execute(routing_policy: ExplorationRoutingPolicy) -> None:
    """Require explicit search terms for search_values."""
    decision = routing_policy.route(
        _request(
            "Search values in product_name.",
            schema_columns=(SchemaColumnRoutingEvidence("product_name"),),
        ),
    )

    assert decision.status == "missing_terms"
    assert decision.tool_calls == ()


def test_limits_are_bounded(routing_policy: ExplorationRoutingPolicy) -> None:
    """Clamp oversized user limits to the Story 6.2 maximum."""
    decision = routing_policy.route(_request("Show the top 500 category values.", limit=500))

    arguments = _ready_arguments(decision)
    assert arguments["limit"] == MAX_EXPLORATION_LIMIT


def test_implicit_exploration_limit_matches_rendered_evidence_limit(
    routing_policy: ExplorationRoutingPolicy,
) -> None:
    """Use five results by default because the reasoner renders at most five."""
    decision = routing_policy.route(_request("Show unique category values."))

    arguments = _ready_arguments(decision)
    assert DEFAULT_EXPLORATION_LIMIT == EXPECTED_DEFAULT_EXPLORATION_LIMIT
    assert arguments["limit"] == DEFAULT_EXPLORATION_LIMIT


def test_explicit_valid_exploration_limit_is_preserved(
    routing_policy: ExplorationRoutingPolicy,
) -> None:
    """Preserve a user's bounded explicit exploration limit."""
    decision = routing_policy.route(
        _request("Show 12 unique category values.", limit=EXPLICIT_EXPLORATION_LIMIT)
    )

    arguments = _ready_arguments(decision)
    assert arguments["limit"] == EXPLICIT_EXPLORATION_LIMIT


@pytest.mark.parametrize("invalid_limit", ["many", True])
def test_invalid_limits_fall_back_to_default(
    routing_policy: ExplorationRoutingPolicy,
    invalid_limit,
) -> None:
    """Avoid uncaught router exceptions for invalid limit input."""
    decision = routing_policy.route(_request("Show unique category values.", limit=invalid_limit))

    arguments = _ready_arguments(decision)
    assert arguments["limit"] == DEFAULT_EXPLORATION_LIMIT


def test_zero_or_negative_limits_use_safe_minimum(
    routing_policy: ExplorationRoutingPolicy,
) -> None:
    """Normalize non-positive numeric limits to the safe minimum."""
    decision = routing_policy.route(_request("Show unique category values.", limit=-5))

    arguments = _ready_arguments(decision)
    assert arguments["limit"] == 1


def test_show_all_does_not_create_unbounded_limit(
    routing_policy: ExplorationRoutingPolicy,
) -> None:
    """Translate broad limit language to the bounded maximum, not an unbounded request."""
    decision = routing_policy.route(_request("Show all unique category values."))

    arguments = _ready_arguments(decision)
    assert arguments["limit"] == MAX_EXPLORATION_LIMIT


@pytest.mark.parametrize(
    ("question", "expected_status"),
    [
        ("Show the top five sample values for category.", "exploration_ambiguity"),
        ("What is the highest price?", "bypass_normal_flow"),
        (
            "Find the most common value containing premium in category.",
            "exploration_ambiguity",
        ),
        ("How many unique values are missing in category?", "exploration_ambiguity"),
        ("Show examples of null values in category.", "exploration_ambiguity"),
    ],
)
def test_ambiguous_or_unsupported_overlap_does_not_route(
    routing_policy: ExplorationRoutingPolicy,
    question: str,
    expected_status: str,
) -> None:
    """Reject overlapping or unsupported exploration requests conservatively."""
    decision = routing_policy.route(_request(question))

    assert decision.status == expected_status
    assert decision.tool_calls == ()


def test_authorization_denial_is_not_retried(routing_policy: ExplorationRoutingPolicy) -> None:
    """Keep policy failures outside retry routing."""
    assert not routing_policy.can_retry_after_error(
        error_code="authorization_denied",
        retry_count=0,
    )
    assert not routing_policy.can_retry_after_error(
        error_code="exploration_column_not_allowed",
        retry_count=0,
    )


def test_retry_budget_allows_only_one_deterministic_retry(
    routing_policy: ExplorationRoutingPolicy,
) -> None:
    """Allow one narrow retry and reject recursive retry loops."""
    assert routing_policy.can_retry_after_error(
        error_code="unknown_exploration_column",
        retry_count=0,
    )
    assert not routing_policy.can_retry_after_error(
        error_code="unknown_exploration_column",
        retry_count=MAX_ROUTING_RETRIES,
    )


@pytest.mark.parametrize(
    "question",
    [
        "Sum quantity by category.",
        "Join this dataset with another dataset by code.",
        "Compare row counts and top category signals across these three datasets.",
        "Transform category into a new column.",
        "Recommend the best option from category.",
    ],
)
def test_unsupported_requests_are_not_forced_into_exploration(
    routing_policy: ExplorationRoutingPolicy,
    question: str,
) -> None:
    """Bypass requests outside the five bounded exploration operations."""
    decision = routing_policy.route(_request(question))

    assert decision.status == "bypass_normal_flow"
    assert decision.tool_calls == ()


def test_generated_arguments_conform_to_exploration_bif_contract(
    routing_policy: ExplorationRoutingPolicy,
) -> None:
    """Generate only canonical exploration BIF arguments."""
    decision = routing_policy.route(
        _request(
            "Find values containing \"premium\" in product_name.",
            schema_columns=(SchemaColumnRoutingEvidence("product_name"),),
        ),
    )

    arguments = _ready_arguments(decision)
    assert set(arguments) == {
        "tenant_id",
        "dataset_id",
        "version_id",
        "operation",
        "columns",
        "limit",
        "terms",
        "match_mode",
    }


def test_router_does_not_reference_direct_exploration_backend() -> None:
    """Protect assistant routing from direct service or executor dependencies."""
    import resource_registry.assistant.exploration_routing as routing_module

    source = routing_module.__loader__.get_source(routing_module.__name__)
    assert source is not None
    assert "DatasetExplorationService" not in source
    assert "DatasetQueryExecutor" not in source
    assert "DuckDB" not in source
