from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class BifCapabilityContext:
    """Request capability context used to decide BIF call eligibility."""

    tenant_id: str
    actor: str | None = None
    roles: tuple[str, ...] = ()
    scopes: tuple[str, ...] = ()
    enabled_capabilities: frozenset[str] = field(default_factory=frozenset)
    disabled_capabilities: frozenset[str] = field(default_factory=frozenset)
    metadata: dict[str, Any] = field(default_factory=dict)
