from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class LifecycleActionType(StrEnum):
    DEPRECATE = "DEPRECATE"
    DELETE_REFERENCE = "DELETE_REFERENCE"
    DELETE_PHYSICAL = "DELETE_PHYSICAL"


@dataclass(frozen=True)
class LifecycleAction:
    action_type: str
    artifact_id: str
    dataset_version_id: str
    reason: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RetentionEvaluationResult:
    tenant_id: str
    dataset_id: str
    evaluated_at: datetime
    total_versions: int
    versions_to_keep: list[str] = field(default_factory=list)
    versions_to_deprecate: list[str] = field(default_factory=list)
    versions_to_delete_reference: list[str] = field(default_factory=list)
    versions_to_delete_physical: list[str] = field(default_factory=list)
    actions: list[LifecycleAction] = field(default_factory=list)


@dataclass(frozen=True)
class ResolvedPolicy:
    source: str
    policy_key: str
    tenant_id: str
    dataset_id: str | None
    revision: int | None
    policy_id: str | None
    rules_json: dict[str, Any]


@dataclass(frozen=True)
class DatasetVersionSnapshot:
    dataset_version_id: str
    version_number: int
    created_at: datetime


@dataclass(frozen=True)
class ArtifactSnapshot:
    artifact_id: str
    dataset_version_id: str
    storage_provider: str | None = None


def now_utc() -> datetime:
    return datetime.now(UTC)
