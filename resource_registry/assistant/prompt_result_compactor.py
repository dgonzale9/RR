"""Compact Resource Registry tool results before future LLM prompt replay."""

from __future__ import annotations

import json
from typing import Any, Protocol

_PROMPT_RESULT_ITEM_LIMIT = 5
_SCHEMA_PROMPT_COLUMN_LIMIT = 64
_PROMPT_RESULT_TEXT_LIMIT = 240
_COMPACTED_TOOL_NAMES = frozenset(
    {
        "resource_registry_list_datasets",
        "resource_registry_get_dataset_schema",
        "resource_registry_explore_dataset",
    },
)


class PromptResultCompactor(Protocol):
    """Define the prompt-result compaction dependency used by routing adapters."""

    def compact(self, *, tool_name: str, raw_result: Any) -> Any:
        """Compact one tool result for future prompt context."""


class ResourceRegistryPromptResultCompactor:
    """Build bounded Resource Registry results for future LLM prompt context."""

    def compact(self, *, tool_name: str, raw_result: Any) -> Any:
        """Compact one supported BIF result while leaving all other results unchanged.

        Args:
            tool_name: Canonical BIF name that produced the result.
            raw_result: Unmodified BIF result used by the active routing flow.

        Returns:
            The original result for unsupported tools or malformed payloads; otherwise a bounded
            JSON-compatible representation suitable for prompt replay.
        """
        if tool_name not in _COMPACTED_TOOL_NAMES:
            return raw_result

        payload = self._parse_payload(raw_result)
        if payload is None:
            return raw_result
        if tool_name == "resource_registry_get_dataset_schema":
            return self._compact_schema_result(payload)
        if tool_name == "resource_registry_list_datasets":
            return self._compact_dataset_listing_result(payload)
        return self._compact_exploration_result(payload)

    @staticmethod
    def _parse_payload(raw_result: Any) -> dict[str, Any] | None:
        """Parse a JSON-compatible tool result without changing malformed outputs."""
        if isinstance(raw_result, dict):
            return raw_result
        if not isinstance(raw_result, str):
            return None
        try:
            payload = json.loads(raw_result)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def _compact_schema_result(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Retain only bounded schema names and types for prompt replay."""
        compacted = self._compact_envelope(payload)
        data = payload.get("data")
        schema = data.get("schema") if isinstance(data, dict) else None
        if not isinstance(schema, dict):
            return compacted

        raw_columns = schema.get("schema_json")
        columns = raw_columns if isinstance(raw_columns, list) else []
        compacted["data"] = {
            "schema": {
                **(
                    {"dataset_version_id": self._compact_value(schema["dataset_version_id"])}
                    if schema.get("dataset_version_id") is not None
                    else {}
                ),
                "column_count": self._compact_value(schema.get("column_count", len(columns))),
                "schema_json": [
                    {
                        "name": self._compact_value(column.get("name")),
                        "type": self._compact_value(column.get("type")),
                    }
                    for column in columns[:_SCHEMA_PROMPT_COLUMN_LIMIT]
                    if isinstance(column, dict) and column.get("name")
                ],
            },
        }
        return compacted

    def _compact_dataset_listing_result(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Retain bounded dataset identity fields for prompt replay."""
        compacted = self._compact_envelope(payload)
        data = payload.get("data")
        datasets = data.get("datasets") if isinstance(data, dict) else None
        if not isinstance(datasets, list):
            return compacted

        compacted["data"] = {
            "datasets": [
                {
                    key: self._compact_value(dataset.get(key))
                    for key in (
                        "id",
                        "dataset_id",
                        "name",
                        "display_name",
                        "slug",
                        "version_id",
                        "current_version_id",
                    )
                    if dataset.get(key) is not None
                }
                for dataset in datasets[:_PROMPT_RESULT_ITEM_LIMIT]
                if isinstance(dataset, dict)
            ],
        }
        return compacted

    def _compact_exploration_result(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Retain bounded exploration evidence for prompt replay."""
        compacted = self._compact_envelope(payload)
        for key in ("operation", "column", "row_count", "limit", "truncated"):
            if key in payload:
                compacted[key] = self._compact_value(payload[key])
        if isinstance(payload.get("columns"), list):
            compacted["columns"] = self._compact_list(payload["columns"])
        for key in ("values", "matches", "rows", "top_values", "statistics"):
            if isinstance(payload.get(key), list):
                compacted[key] = self._compact_list(payload[key])
        return compacted

    def _compact_envelope(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Retain the stable safe response envelope in compact prompt results."""
        compacted: dict[str, Any] = {}
        for key in ("success", "status", "code", "error"):
            if key in payload:
                compacted[key] = self._compact_value(payload[key])
        if isinstance(payload.get("warnings"), list):
            compacted["warnings"] = self._compact_list(payload["warnings"])
        return compacted

    def _compact_list(self, values: list[Any]) -> list[Any]:
        """Bound one prompt-context collection while preserving JSON-compatible values."""
        return [self._compact_value(value) for value in values[:_PROMPT_RESULT_ITEM_LIMIT]]

    def _compact_value(self, value: Any) -> Any:
        """Bound text and nested collections stored in future prompt context."""
        if isinstance(value, str):
            return value[:_PROMPT_RESULT_TEXT_LIMIT]
        if isinstance(value, list):
            return self._compact_list(value)
        if isinstance(value, dict):
            return {
                str(key): self._compact_value(nested_value) for key, nested_value in value.items()
            }
        return value
