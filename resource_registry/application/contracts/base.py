from __future__ import annotations

import math
from dataclasses import dataclass, field

APPLICATION_CONTRACT_VERSION = "transformation-application/1.0"


@dataclass(frozen=True, kw_only=True)
class ApplicationContract:
    """Identify one immutable transport-neutral application contract."""

    contract_version: str = field(default=APPLICATION_CONTRACT_VERSION, init=False)


def validate_duration_seconds(value: float | None) -> None:
    """Require an optional finite non-negative public duration."""
    if value is not None and (not math.isfinite(value) or value < 0):
        raise ValueError("Application duration must be finite and non-negative.")


def validate_progress(value: float | None) -> None:
    """Require optional finite public progress in the inclusive zero-to-one range."""
    if value is not None and (not math.isfinite(value) or not 0 <= value <= 1):
        raise ValueError("Application progress must be finite and between zero and one.")
