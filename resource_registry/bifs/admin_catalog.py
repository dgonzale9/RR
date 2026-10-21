"""Project canonical Registry records into assignable administrative catalog groups."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import TypeAlias, cast

from django.db.models import Manager, prefetch_related_objects
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response
from rest_framework.serializers import ListSerializer

from BotServer.models import BuiltInFunction, BuiltInFunctionPersonaRelationship
from resource_registry.bifs.grouped_tools import GROUPED_REGISTRY_ACTIONS

CatalogRow: TypeAlias = dict[str, object]

# These are API identifiers, never database primary keys. Keep them stable across releases.
REGISTRY_CATALOG_GROUP_IDS: Mapping[str, int] = {
    "resource_registry_inspect": -4101,
    "resource_registry_query": -4102,
    "resource_registry_manage_outputs": -4103,
    "resource_registry_manage_transformations": -4104,
}
REGISTRY_CATALOG_GROUP_NAMES = {value: key for key, value in REGISTRY_CATALOG_GROUP_IDS.items()}
_OPERATION_GROUPS = {
    f"resource_registry_{action}": group
    for group, actions in GROUPED_REGISTRY_ACTIONS.items()
    for action in actions
}
_GROUP_LABELS = {
    "resource_registry_inspect": "Resource Registry: Inspect",
    "resource_registry_query": "Resource Registry: Query",
    "resource_registry_manage_outputs": "Resource Registry: Manage Outputs",
    "resource_registry_manage_transformations": "Resource Registry: Manage Transformations",
}
# Temporary discovery policy only; keep assigned groups and model tools available.
_HIDDEN_CATALOG_GROUPS = frozenset(
    {"resource_registry_manage_outputs", "resource_registry_manage_transformations"}
)


def _merge_metadata(rows: Sequence[CatalogRow], field: str) -> list[CatalogRow]:
    values: dict[object, CatalogRow] = {}
    for row in rows:
        for item in cast(list[CatalogRow], row.get(field, [])):
            values.setdefault(item["id"], item)
    return list(values.values())


def _build_group_row(group: str, rows: Sequence[CatalogRow]) -> CatalogRow:
    by_name = {row["name"]: row for row in rows}
    actions = [
        {"action": action, "builtin_function_id": by_name[f"resource_registry_{action}"]["id"]}
        for action in GROUPED_REGISTRY_ACTIONS[group]
        if f"resource_registry_{action}" in by_name
    ]
    return {
        **rows[0],
        "id": REGISTRY_CATALOG_GROUP_IDS[group],
        "name": group,
        "display_name": _GROUP_LABELS[group],
        "instructions": "Available actions: " + ", ".join(str(item["action"]) for item in actions),
        "categories": _merge_metadata(rows, "categories"),
        "tags": _merge_metadata(rows, "tags"),
        "is_grouped": True,
        "is_partial": len(actions) < len(GROUPED_REGISTRY_ACTIONS[group]),
        "actions": actions,
    }


def project_registry_catalog(rows: Sequence[CatalogRow]) -> list[CatalogRow]:
    """Replace visible canonical operations with nonempty groups without granting access.

    Args:
        rows: Already authorized, serialized BIF records.

    Returns:
        Unrelated records unchanged and in order, followed by their visible Registry groups.
        The two replacement BIFs are not registered group operations and pass through unchanged.
        Output and transformation groups are temporarily hidden from this catalog only.
    """
    remaining = []
    grouped: dict[str, list[CatalogRow]] = {}
    for row in rows:
        group = _OPERATION_GROUPS.get(str(row["name"]))
        if group is None:
            remaining.append(row)
        else:
            grouped.setdefault(group, []).append(row)
    remaining.extend(
        _build_group_row(group, grouped[group])
        for group in GROUPED_REGISTRY_ACTIONS
        if group in grouped and group not in _HIDDEN_CATALOG_GROUPS
    )
    return remaining


def list_registry_catalog(view: GenericAPIView[BuiltInFunction]) -> Response:
    """Serialize authorized BIFs and group Registry operations before pagination.

    Args:
        view: Existing catalog view with its secure queryset already set.

    Returns:
        The existing paginated response contract with Registry catalog entries projected.
    """
    queryset = view.filter_queryset(view.get_queryset()).select_related("last_modified_by")
    queryset = queryset.prefetch_related("categories__tags", "tags")
    rows = project_registry_catalog(view.get_serializer(queryset, many=True).data)
    # DRF supports lists here; its stubs currently restrict this argument to QuerySet.
    page = view.paginate_queryset(rows)  # type: ignore[arg-type]
    return view.get_paginated_response(page) if page is not None else Response(rows)


class RegistryAssignmentListSerializer(ListSerializer[BuiltInFunctionPersonaRelationship]):
    """Present actual assigned actions under the same virtual IDs as the catalog."""

    def to_representation(self, data: object) -> list[CatalogRow]:
        """Group nested BIFs without adding assignments or changing individual serializers.

        Args:
            data: Existing persisted relationships, either a queryset or service result list.

        Returns:
            Unrelated relationships unchanged, followed by groups containing assigned actions only.
            Group relationship_ids retain the original individual relationship identifiers.
        """
        if isinstance(data, Manager):
            data = data.all()
        relationships = list(cast(Iterable[BuiltInFunctionPersonaRelationship], data))
        prefetch_related_objects(
            relationships,
            "persona",
            "last_modified_by",
            "builtin_function__last_modified_by",
            "builtin_function__categories__tags",
            "builtin_function__tags",
        )
        rows = cast(list[CatalogRow], super().to_representation(relationships))
        remaining = []
        # Keep separate Personas separate if a caller serializes relationships in bulk.
        grouped: dict[tuple[object, str], list[CatalogRow]] = {}
        for row in rows:
            function = cast(CatalogRow, row["builtin_function"])
            group = _OPERATION_GROUPS.get(str(function["name"]))
            if group is None:
                remaining.append(row)
            else:
                persona = cast(CatalogRow, row["persona"])
                grouped.setdefault((persona["id"], group), []).append(row)
        for (_, group), members in grouped.items():
            remaining.append(
                {
                    **members[0],
                    "relationship_ids": [member["id"] for member in members],
                    "builtin_function": _build_group_row(
                        group, [cast(CatalogRow, member["builtin_function"]) for member in members]
                    ),
                }
            )
        return remaining
