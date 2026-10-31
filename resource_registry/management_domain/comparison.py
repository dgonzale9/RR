"""Compare schemas before publishing a candidate dataset."""

from collections.abc import Mapping, Sequence


def compare_dataset_schemas(
    previous: Sequence[Mapping[str, object]],
    candidate: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Identify additive and potentially incompatible schema changes.

    Args:
        previous: Previously published column definitions.
        candidate: Proposed column definitions.

    Returns:
        Added, removed and changed column names and manager-publication requirement.
    """
    before = {
        str(column["name"]): str(column.get("dtype", column.get("type", ""))) for column in previous
    }
    after = {
        str(column["name"]): str(column.get("dtype", column.get("type", "")))
        for column in candidate
    }
    removed = sorted(before.keys() - after.keys())
    changed = sorted(name for name in before.keys() & after.keys() if before[name] != after[name])
    return {
        "added": sorted(after.keys() - before.keys()),
        "removed": removed,
        "changed": changed,
        "requires_manager": bool(removed or changed),
    }
