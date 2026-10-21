"""Keep explicit creative instructions outside deterministic dataset exploration."""

from pathlib import Path

import pytest

from resource_registry.assistant.exploration_routing import (
    ExplorationRoutingPolicy,
    ExplorationRoutingRequest,
    SchemaColumnRoutingEvidence,
)

INCIDENT_REQUEST = (
    (Path(__file__).parent / "fixtures" / "creative_assembly_render_specs_request.txt")
    .read_text(encoding="utf-8")
    .strip()
)


@pytest.mark.parametrize("has_schema", [False, True])
@pytest.mark.parametrize(
    "question",
    [
        pytest.param(INCIDENT_REQUEST, id="original-incident"),
        "RENDER SPEC: representative campaign material.",
        "  \nrender specs, representative campaign material.",
        "Please RENDER SPECS. Find campaign values.",
        "Create render specs: profile campaign values.",
        "Generate render specs: top campaign values.",
        "Build render specs: distinct campaign values.",
        "Prepare render specs: sample campaign values.",
        "Return render specs: compare datasets.",
        "Please return RENDER SPEC\nrepresentative campaign material.",
        "Continue the concepts. Compare datasets from the representative brief.",
        "Create three concepts. Sample campaign values.",
        "Generate three concepts. Find campaign values.",
        "Develop concepts. Profile campaign values.",
        "Refine the concepts. Create a derived dataset.",
        "Revise the concept. Show top campaign values.",
        "Create three concepts. Brief: register the attached file as a dataset.",
    ],
)
def test_creative_requests_bypass_exploration(question: str, has_schema: bool) -> None:
    """Preserve creative orchestration before inspecting brief keywords.

    Args:
        question: Creative instruction followed by potential exploration triggers.
        has_schema: Whether authoritative campaign schema has already been retrieved.
    """
    decision = ExplorationRoutingPolicy().route(
        ExplorationRoutingRequest(
            question=question,
            tenant_id="test-namespace",
            dataset_id="test-dataset",
            schema_columns=(SchemaColumnRoutingEvidence("campaign"),) if has_schema else (),
        )
    )

    assert decision.status == "bypass_normal_flow"
    assert decision.tool_calls == ()


@pytest.mark.parametrize(
    ("question", "operation"),
    [
        ("Show 5 representative campaign values.", "sample_values"),
        ('Find campaign values containing "render specs".', "search_values"),
        ('Find campaign values containing "Generate render specs".', "search_values"),
        ("Show sample campaign values. Brief: RENDER SPECS.", "sample_values"),
        ('"RENDER SPECS" appears in campaign examples.', "sample_values"),
        ("RENDER SPECSpecific sample campaign values.", "sample_values"),
    ],
)
def test_exploration_mentions_keep_existing_routing(question: str, operation: str) -> None:
    """Keep actual exploration requests even when their content mentions render specs.

    Args:
        question: Dataset exploration instruction with creative words as data.
        operation: Expected exploration operation.
    """
    decision = ExplorationRoutingPolicy().route(
        ExplorationRoutingRequest(
            question=question,
            tenant_id="test-namespace",
            dataset_id="test-dataset",
            schema_columns=(SchemaColumnRoutingEvidence("campaign"),),
        )
    )

    assert decision.status == "ready"
    assert decision.operation == operation
    assert decision.columns == ("campaign",)
    assert decision.tool_calls[0].name == "resource_registry_explore_dataset"
