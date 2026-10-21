"""Normalize dataset format aliases independently of transport and storage."""


def normalize_dataset_format(source_format: str) -> str:
    """Resolve a format name or suffix to its canonical dataset identifier.

    Args:
        source_format: Format name or extension, optionally prefixed with a dot.

    Returns:
        Lowercase format, with NDJSON represented as JSONL.
    """
    normalized = source_format.strip().lower().removeprefix(".")
    return "jsonl" if normalized == "ndjson" else normalized
