from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from resource_registry.application.contracts import TransformationExecutionRequest


@dataclass(frozen=True, kw_only=True)
class TransformationExecutionCallerContext:
    """Carry trusted caller identity and authorization scopes into command orchestration."""

    tenant_id: str
    actor: str
    principal_kind: str
    permissions: frozenset[str]
    allowed_definition_ids: frozenset[str] | None = None
    allowed_dataset_ids: frozenset[str] | None = None
    created_by_id: int | None = None


@dataclass(frozen=True, kw_only=True)
class TransformationRuntimeInvocationResult:
    """Carry the minimal internal runtime submission result required for DTO mapping."""

    tenant_id: str
    run_id: str
    definition_id: str
    definition_version_id: str
    status: str
    was_created: bool


class TransformationRuntimeInvocationPort(Protocol):
    """Define the sole runtime-composition command used by the application service."""

    def submit_execution(
        self,
        *,
        request: TransformationExecutionRequest,
        caller: TransformationExecutionCallerContext,
    ) -> TransformationRuntimeInvocationResult:
        """Submit one validated request through runtime-owned orchestration."""


class TransformationExecutionGovernancePort(Protocol):
    """Expose tenant governance availability for application entry authorization."""

    def is_available(self, *, tenant_id: str) -> bool:
        """Return whether tenant transformation governance is available."""
