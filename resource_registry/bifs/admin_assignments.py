"""Translate virtual Registry catalog IDs into existing per-operation assignments."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, cast

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.request import Request
from rest_framework.response import Response

from BotServer.models import BuiltInFunction, BuiltInFunctionPersonaRelationship, Persona
from BotServer.util.builtin_function_persona_service import BuiltInFunctionPersonaService
from resource_registry.bifs.admin_catalog import REGISTRY_CATALOG_GROUP_NAMES, CatalogRow
from resource_registry.bifs.grouped_tools import GROUPED_REGISTRY_ACTIONS

if TYPE_CHECKING:
    from BotServer.persona_api import BuiltInFunctionToPersonaView


def _has_group_ids(payload: object) -> bool:
    if not isinstance(payload, Mapping):
        return False
    requested = payload.get("builtin_function_ids", [payload.get("builtin_function")])
    if not isinstance(requested, list):
        return False
    # The original serializer also accepts integer strings. Do not change its validation.
    group_ids = {str(value) for value in REGISTRY_CATALOG_GROUP_NAMES}
    return any(str(value) in group_ids for value in requested)


def _expand_group_ids(
    view: BuiltInFunctionToPersonaView,
    request: Request,
    persona: Persona,
    requested: Sequence[int],
) -> dict[int, list[int]]:
    names = {
        f"resource_registry_{action}"
        for identifier in requested
        if identifier in REGISTRY_CATALOG_GROUP_NAMES
        for action in GROUPED_REGISTRY_ACTIONS[REGISTRY_CATALOG_GROUP_NAMES[identifier]]
    }
    if request.method == "DELETE":
        # Include protected assignments so the existing service reports denied removals.
        queryset = BuiltInFunction.objects.filter(
            builtinfunctionpersonarelationship__persona=persona, name__in=names
        )
    else:
        queryset = view.get_secure_queryset(
            request, model_class=BuiltInFunction, action="view"
        ).filter(name__in=names)
    available = dict(queryset.values_list("name", "pk"))
    return {
        identifier: (
            [
                available[f"resource_registry_{action}"]
                for action in GROUPED_REGISTRY_ACTIONS[REGISTRY_CATALOG_GROUP_NAMES[identifier]]
                if f"resource_registry_{action}" in available
            ]
            if identifier in REGISTRY_CATALOG_GROUP_NAMES
            else [identifier]
        )
        for identifier in requested
    }


def _project_rejections(
    expansion: Mapping[int, list[int]], rejected: Sequence[CatalogRow], *, removing: bool
) -> list[CatalogRow]:
    by_id = {row["builtin_function"]: row for row in rejected}
    projected: list[CatalogRow] = []
    for requested, operation_ids in expansion.items():
        failures = [by_id[identifier] for identifier in operation_ids if identifier in by_id]
        if not operation_ids:
            projected.append(
                {
                    "builtin_function": requested,
                    "detail": (
                        "Built in function relationship not found"
                        if removing
                        else "Built in function not found"
                    ),
                    "code": (
                        "builtin_function_relationship_not_found"
                        if removing
                        else "builtin_function_not_found"
                    ),
                }
            )
        elif failures:
            projected.append({**failures[0], "builtin_function": requested})
    return projected


def apply_registry_catalog_assignment(
    view: BuiltInFunctionToPersonaView, request: Request
) -> Response | None:
    """Apply a grouped add/remove request through the original assignment service.

    Args:
        view: Existing assignment endpoint, including its secure query and response helpers.
        request: Incoming single-ID or batch-ID request.

    Returns:
        None without queries for requests with no registered group IDs; otherwise the existing
        response shape, with results and rejections mapped back to requested catalog IDs.

    Raises:
        Http404: If the caller cannot change the target Persona.
        ValidationError: If the original input serializer rejects the payload.

    Side effects:
        Create/delete only canonical relationships through the existing service and save the
        Persona. The service preserves per-operation organization admission and delete permissions.
    """
    if not _has_group_ids(request.data):
        return None
    serializer = view.get_serializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    requested = cast(list[int], serializer.validated_data["resolved_ids"])
    persona = get_object_or_404(
        view.get_secure_queryset(request, model_class=Persona, action="change"),
        pk=view.kwargs["pk"],
    )
    expansion = _expand_group_ids(view, request, persona, requested)
    canonical_ids = list(dict.fromkeys(value for values in expansion.values() for value in values))
    if request.method == "DELETE":
        result = BuiltInFunctionPersonaService.remove_builtin_functions(
            persona=persona,
            builtin_function_ids=canonical_ids,
            secure_queryset=view.get_secure_queryset(
                request, model_class=BuiltInFunctionPersonaRelationship, action="delete"
            ),
        )
        removed = set(result["removed"])
        projected_removed = [
            identifier for identifier, members in expansion.items() if removed.intersection(members)
        ]
        persona.save()
        return view._build_delete_response(
            projected_removed, _project_rejections(expansion, result["rejected"], removing=True)
        )
    result = BuiltInFunctionPersonaService.assign_builtin_functions(
        persona=persona,
        builtin_function_ids=canonical_ids,
        user=request.user,
        organization=getattr(request, "organization", None),
    )
    BuiltInFunction.objects.filter(
        pk__in=[relationship.builtin_function_id for relationship in result["created"]]
    ).update(last_interaction_date=timezone.now())
    persona.save()
    return view._build_response(
        result["created"], _project_rejections(expansion, result["rejected"], removing=False)
    )
