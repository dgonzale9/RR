"""Derive columns that may be referenced in dataset queries."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from resource_registry.read_auth.field_filtering import REDACTED


def get_queryable_schema_column_names(
    schema_json: Sequence[Mapping[str, object]],
) -> frozenset[str]:
    """Return visible schema column names excluding masked metadata.

    Args:
        schema_json: Schema already filtered for the calling authorization context.

    Returns:
        Column names safe for query and exploration use.
    """
    return frozenset(
        str(column["name"])
        for column in schema_json
        if column.get("name") and str(column.get("type", "")).upper() != REDACTED
    )
