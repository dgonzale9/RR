from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ReadAuthorizationContext:
    tenant_id: str
    actor: str | None = None
    roles: tuple[str, ...] = ()
    scopes: tuple[str, ...] = ()
    allowed_dataset_ids: frozenset[str] | None = None
    enforced_dataset_ids: frozenset[str] | None = None
    allow_cross_tenant: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
