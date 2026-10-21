from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from resource_registry.application.contracts import RunStatus


@dataclass(frozen=True, kw_only=True)
class TransformationReadContext:
    """Carry trusted application read authorization and resource scopes."""

    tenant_id: str
    actor: str
    permissions: frozenset[str]
    correlation_id: str
    allowed_definition_ids: frozenset[str] | None = None
    allowed_dataset_ids: frozenset[str] | None = None


@dataclass(frozen=True, kw_only=True)
class TransformationRunFilters:
    """Contain deterministic optional transformation run filters."""

    definition_id: str | None = None
    definition_version_id: str | None = None
    status: RunStatus | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None
    correlation_id: str | None = None
