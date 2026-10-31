"""Build deterministic synthetic inputs for Registry benchmark scenarios."""

from __future__ import annotations

from collections.abc import Sequence
from types import SimpleNamespace

from resource_registry.assistant.exploration_routing import (
    ExplorationRoutingRequest,
    SchemaColumnRoutingEvidence,
)
from resource_registry.bifs.grouped_tools import GROUPED_REGISTRY_ACTIONS, FunctionToolDefinition


def build_routing_cases() -> dict[str, ExplorationRoutingRequest]:
    """Build stable, non-sensitive routing requests for the benchmark matrix.

    Returns:
        Requests covering listing, schema, exploration, fallback, and rejection.
    """
    return {
        "listing": ExplorationRoutingRequest(
            question="List available datasets", tenant_id="benchmark-tenant"
        ),
        "schema": ExplorationRoutingRequest(
            question="What unique values exist in category?",
            tenant_id="benchmark-tenant",
            dataset_id="benchmark-dataset",
        ),
        "explore": ExplorationRoutingRequest(
            question="What unique values exist in category?",
            tenant_id="benchmark-tenant",
            dataset_id="benchmark-dataset",
            schema_columns=(SchemaColumnRoutingEvidence(name="category"),),
        ),
        "fallback": ExplorationRoutingRequest(
            question="Continue creating concepts", tenant_id="benchmark-tenant"
        ),
        "rejected": ExplorationRoutingRequest(
            question="What unique values exist in unknown_column?",
            tenant_id="benchmark-tenant",
            dataset_id="benchmark-dataset",
            schema_columns=(SchemaColumnRoutingEvidence(name="category"),),
        ),
    }


def build_operation_definitions(action_names: Sequence[str]) -> list[FunctionToolDefinition]:
    """Build schema definitions for exactly the selected Registry actions.

    Args:
        action_names: Grouped action names to include.

    Returns:
        Provider-neutral definitions with deterministic parameter metadata.
    """
    return [
        FunctionToolDefinition(
            name=f"resource_registry_{action}",
            description="Benchmark operation description.",
            parameters={
                "type": "object",
                "properties": {
                    "tenant_id": {"type": "string", "description": "Authorized namespace."},
                    "dataset_id": {"type": "string", "description": "Authorized dataset."},
                    "limit": {"type": "integer", "description": "Bounded result count."},
                },
                "required": ["tenant_id"],
            },
        )
        for action in action_names
    ]


def build_all_action_names() -> tuple[str, ...]:
    """Return the canonical 31 grouped action names in publication order.

    Returns:
        The current grouped Registry actions without generating new names.
    """
    return tuple(action for actions in GROUPED_REGISTRY_ACTIONS.values() for action in actions)


def build_cached_publication_relationships(action_names: Sequence[str]) -> list[SimpleNamespace]:
    """Build assigned-looking relationships with preloaded parameter metadata.

    This fixture is synthetic: only the database scenario tests real Persona
    assignments and queryset filtering.

    Args:
        action_names: Canonical grouped actions represented by the fixture.

    Returns:
        Iterable relationship doubles that never query the database.
    """
    relationships: list[SimpleNamespace] = []
    for action in action_names:
        parameters = [
            SimpleNamespace(
                name=name,
                type=parameter_type,
                description=description,
                required=name == "tenant_id",
            )
            for name, parameter_type, description in (
                ("tenant_id", "string", "Authorized namespace."),
                ("dataset_id", "string", "Authorized dataset."),
                ("limit", "integer", "Bounded result count."),
            )
        ]
        function = SimpleNamespace(
            name=f"resource_registry_{action}",
            description="Benchmark operation description.",
            _prefetched_objects_cache={"functionparameter_set": parameters},
        )
        relationships.append(SimpleNamespace(builtin_function=function))
    return relationships
