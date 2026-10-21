"""Recognize dataset references that require conversation context."""

from __future__ import annotations

_CONTEXTUAL_DATASET_REFERENCES = frozenset(
    {
        "",
        "this dataset",
        "current dataset",
        "selected dataset",
        "that dataset",
        "this sheet",
        "current sheet",
        "selected sheet",
        "este dataset",
        "dataset actual",
        "esta hoja",
        "hoja actual",
    }
)


def is_contextual_dataset_reference(value: object) -> bool:
    """Identify a dataset reference that does not specify an exact dataset ID.

    Args:
        value: Dataset selector supplied to a Registry tool.

    Returns:
        Whether the selector refers to conversation context instead of an ID.
    """
    return str(value or "").strip().lower() in _CONTEXTUAL_DATASET_REFERENCES
