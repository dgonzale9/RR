from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from resource_registry.policies.models import ResolvedPolicy


class PolicySource(StrEnum):
    DATASET_OVERRIDE = "DATASET_OVERRIDE"
    TENANT_DEFAULT = "TENANT_DEFAULT"
    SYSTEM_DEFAULT = "SYSTEM_DEFAULT"


@dataclass(frozen=True)
class PolicyProvenance:
    policy_key: str
    source: PolicySource
    tenant_id: str
    dataset_id: str | None
    governance_policy_id: str | None
    revision: int | None
    resolved_at: datetime
    rules_hash: str
    resolution_path: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ResolvedPolicyWithProvenance:
    resolved_policy: ResolvedPolicy
    provenance: PolicyProvenance


@dataclass(frozen=True)
class PolicyEnforcementContext:
    policy_key: str
    policy_source: str | None
    policy_revision: int | None
    enforcement_time: datetime
    enforcement_result: str
    enforcement_reason: str | None = None


def build_policy_provenance(
    *,
    resolved_policy: ResolvedPolicy,
    resolution_path: list[str],
) -> PolicyProvenance:
    source = _to_policy_source(resolved_policy.source)
    rules_hash = hashlib.sha256(
        json.dumps(
            resolved_policy.rules_json,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8"),
    ).hexdigest()
    return PolicyProvenance(
        policy_key=resolved_policy.policy_key,
        source=source,
        tenant_id=resolved_policy.tenant_id,
        dataset_id=resolved_policy.dataset_id,
        governance_policy_id=resolved_policy.policy_id,
        revision=resolved_policy.revision,
        resolved_at=datetime.now(tz=UTC),
        rules_hash=rules_hash,
        resolution_path=resolution_path,
    )


def _to_policy_source(source: str) -> PolicySource:
    normalized = source.strip().lower()
    mapping: dict[str, PolicySource] = {
        "dataset_override": PolicySource.DATASET_OVERRIDE,
        "tenant_default": PolicySource.TENANT_DEFAULT,
        "system_default": PolicySource.SYSTEM_DEFAULT,
    }
    return mapping.get(normalized, PolicySource.SYSTEM_DEFAULT)


def enforcement_allow(
    *,
    policy_key: str,
    policy_source: str | None,
    policy_revision: int | None,
    reason: str | None = None,
) -> PolicyEnforcementContext:
    return PolicyEnforcementContext(
        policy_key=policy_key,
        policy_source=policy_source,
        policy_revision=policy_revision,
        enforcement_time=datetime.now(tz=UTC),
        enforcement_result="ALLOW",
        enforcement_reason=reason,
    )


def enforcement_reject(
    *,
    policy_key: str,
    policy_source: str | None,
    policy_revision: int | None,
    reason: str | None,
) -> PolicyEnforcementContext:
    return PolicyEnforcementContext(
        policy_key=policy_key,
        policy_source=policy_source,
        policy_revision=policy_revision,
        enforcement_time=datetime.now(tz=UTC),
        enforcement_result="REJECT",
        enforcement_reason=reason,
    )


def enforcement_fallback(
    *,
    policy_key: str,
    policy_source: str | None,
    policy_revision: int | None,
    reason: str,
) -> PolicyEnforcementContext:
    return PolicyEnforcementContext(
        policy_key=policy_key,
        policy_source=policy_source,
        policy_revision=policy_revision,
        enforcement_time=datetime.now(tz=UTC),
        enforcement_result="FALLBACK",
        enforcement_reason=reason,
    )
