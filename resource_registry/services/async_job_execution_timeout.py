from __future__ import annotations


def normalize_execution_timeout_seconds(value: float | int | None) -> float | None:
    """Normalize execution timeout configuration.

    Args:
        value: Configured timeout seconds. ``None`` or values <= 0 disable soft timeout detection.

    Returns:
        Positive timeout seconds, or ``None`` when timeout detection is disabled.
    """
    if value is None:
        return None
    timeout_seconds = float(value)
    if timeout_seconds <= 0:
        return None
    return timeout_seconds


def has_execution_timeout_elapsed(
    *,
    elapsed_seconds: float,
    timeout_seconds: float | None,
) -> bool:
    """Return whether soft execution timeout has elapsed.

    Args:
        elapsed_seconds: Measured wall-clock execution duration.
        timeout_seconds: Normalized timeout seconds, or ``None`` when disabled.

    Returns:
        ``True`` when timeout detection is enabled and elapsed duration exceeds the limit.
    """
    return timeout_seconds is not None and elapsed_seconds >= timeout_seconds


def build_execution_timeout_message(
    *,
    job_family: str,
    timeout_seconds: float | None,
) -> str:
    """Build stable timeout error text for persisted async job failures.

    Args:
        job_family: Human-readable async job family name.
        timeout_seconds: Configured timeout seconds, or ``None`` when unknown.

    Returns:
        Stable human-readable timeout failure message.
    """
    if timeout_seconds is None:
        return f"{job_family} execution exceeded configured timeout."
    return f"{job_family} execution exceeded timeout of {timeout_seconds:g} seconds."
