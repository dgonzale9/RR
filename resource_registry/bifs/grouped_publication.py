"""Publish only Resource Registry assignments through grouped provider tools."""

from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from typing import Literal, overload

from django.db.models import QuerySet

from BotServer.models import BuiltInFunction, BuiltInFunctionPersonaRelationship, FunctionParameter
from resource_registry.assistant.campaign_rendering_intent import CampaignRenderingIntentPolicy
from resource_registry.bifs.grouped_tools import (
    GROUPED_REGISTRY_ACTIONS,
    GROUPED_REGISTRY_OPERATION_NAMES,
    FunctionToolDefinition,
    build_grouped_registry_tools,
)
from resource_registry.performance.instrumentation import (
    RegistryPerformanceStage,
    instrument_registry_function,
)
from resource_registry.performance.payload_metrics import measure_registry_publication_result

RegistryToolProvider = Literal["openai", "openai_responses", "gemini", "anthropic"]


def build_registry_operation_definition(
    function: BuiltInFunction, parameters: Iterable[FunctionParameter]
) -> FunctionToolDefinition:
    """Describe an admitted Registry operation using previously loaded parameters.

    Args:
        function: Original Registry BIF selected by assignment or admission.
        parameters: Loaded parameter metadata for that operation.

    Returns:
        The original parameter schema and required argument names.
    """
    properties: dict[str, object] = {}
    required: list[str] = []
    for parameter in parameters:
        properties[parameter.name] = {"type": parameter.type, "description": parameter.description}
        if parameter.required:
            required.append(parameter.name)
    return FunctionToolDefinition(
        name=function.name,
        description=function.description,
        parameters={"type": "object", "properties": properties, "required": required},
    )


@overload
def prepare_registry_tools(
    relationships: QuerySet[BuiltInFunctionPersonaRelationship, BuiltInFunctionPersonaRelationship],
    *,
    persona: object | None,
    provider: RegistryToolProvider,
    statement: str,
) -> tuple[
    QuerySet[BuiltInFunctionPersonaRelationship, BuiltInFunctionPersonaRelationship],
    list[dict[str, object]],
]: ...


@overload
def prepare_registry_tools(
    relationships: Iterable[BuiltInFunctionPersonaRelationship],
    *,
    persona: object | None,
    provider: RegistryToolProvider,
    statement: str,
) -> tuple[Iterable[BuiltInFunctionPersonaRelationship], list[dict[str, object]]]: ...


@instrument_registry_function(
    RegistryPerformanceStage.REGISTRY_TOOL_PUBLICATION,
    payload_measurement=measure_registry_publication_result,
)
def prepare_registry_tools(
    relationships: Iterable[BuiltInFunctionPersonaRelationship],
    *,
    persona: object | None,
    provider: RegistryToolProvider,
    statement: str,
) -> tuple[Iterable[BuiltInFunctionPersonaRelationship], list[dict[str, object]]]:
    """Separate Registry publication from the client's unchanged legacy BIF construction.

    Args:
        relationships: Current assignment queryset or already loaded assignments.
        persona: Active Persona used by the Registry campaign presentation policy.
        provider: Provider envelope for grouped Registry tools only.
        statement: Current request used to identify campaign rendering intent.

    Returns:
        Assignments left for the original formatter and grouped Registry definitions.
        Queryset inputs retain a queryset of the remaining assignments.
        Registry operations are always published through their assigned grouped actions.
    """
    registry_relationships: Iterable[BuiltInFunctionPersonaRelationship]
    remaining: Iterable[BuiltInFunctionPersonaRelationship]
    if isinstance(relationships, QuerySet):
        registry_relationships = (
            relationships.filter(builtin_function__name__in=GROUPED_REGISTRY_OPERATION_NAMES)
            .select_related("builtin_function")
            .prefetch_related("builtin_function__functionparameter_set")
        )
        remaining = relationships.exclude(
            builtin_function__name__in=GROUPED_REGISTRY_OPERATION_NAMES
        )
    else:
        loaded = list(relationships)
        registry_relationships = [
            relationship
            for relationship in loaded
            if relationship.builtin_function.name in GROUPED_REGISTRY_OPERATION_NAMES
        ]
        remaining = [
            relationship
            for relationship in loaded
            if relationship.builtin_function.name not in GROUPED_REGISTRY_OPERATION_NAMES
        ]

    definitions: list[FunctionToolDefinition] = []
    for relationship in registry_relationships:
        function = relationship.builtin_function
        parameters = getattr(function, "_prefetched_objects_cache", {}).get("functionparameter_set")
        if parameters is None:
            parameters = FunctionParameter.objects.filter(function=function)
        definitions.append(build_registry_operation_definition(function, parameters))
    if not definitions:
        return remaining, []

    excluded_actions: frozenset[str] = frozenset()
    if CampaignRenderingIntentPolicy().should_exclude_exploration_tool(
        persona=persona, statement=statement
    ):
        excluded_actions = frozenset({"explore_dataset"})
    return remaining, [
        format_registry_tool(definition, provider)
        for definition in build_grouped_registry_tools(
            definitions, excluded_actions=excluded_actions
        )
    ]


def format_registry_tool(
    definition: FunctionToolDefinition, provider: RegistryToolProvider
) -> dict[str, object]:
    """Adapt one grouped Registry definition to its provider envelope.

    Args:
        definition: One of the four grouped Registry contracts.
        provider: Supported model provider envelope.

    Returns:
        A fresh provider-specific Registry tool definition.

    Raises:
        ValueError: If called with a tool outside the four Registry groups.
    """
    if definition.name not in GROUPED_REGISTRY_ACTIONS:
        raise ValueError("Only grouped Registry definitions can use this adapter.")
    schema_key = {"anthropic": "input_schema", "gemini": "parametersJsonSchema"}.get(
        provider, "parameters"
    )
    function: dict[str, object] = {
        "name": definition.name,
        "description": definition.description,
        schema_key: deepcopy(definition.parameters),
    }
    if provider == "openai":
        return {"type": "function", "function": function}
    if provider == "openai_responses":
        # Action-specific optional fields must not be normalized to required fields.
        return {"type": "function", **function, "strict": False}
    return function
