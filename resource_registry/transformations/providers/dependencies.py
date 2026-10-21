from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol

from resource_registry.services.duckdb_service import DuckDbConnectionProvider
from resource_registry.transformations.providers.base import TransformationOperationProvider
from resource_registry.transformations.providers.contracts import TransformationProviderInputReference


@dataclass(frozen=True)
class ProviderMaterializedInput:
    """Expose one verified local input for the duration of a materialization context."""

    local_path: str


@dataclass(frozen=True)
class ProviderOutputTarget:
    """Identify one provider-neutral local output staging target."""

    local_path: str
    destination_key: str
    workspace_lease_id: str | None = None
    workspace_lease_generation: int | None = None


@dataclass(frozen=True)
class ProviderPublishedOutput:
    """Describe one durable workspace object without exposing its storage implementation."""

    materialized_reference: str
    checksum: str
    size_bytes: int


@dataclass(frozen=True)
class ProviderOperationalLimits:
    """Expose immutable execution limits without settings or ORM coupling."""

    maximum_input_bytes: int
    maximum_output_bytes: int
    maximum_row_count: int
    maximum_source_columns: int
    maximum_selected_columns: int
    provider_timeout_seconds: float
    materialization_timeout_seconds: float


class ProviderInputMaterializationPort(Protocol):
    """Materialize and verify immutable provider input references."""

    def materialize(
        self,
        *,
        reference: TransformationProviderInputReference,
    ) -> AbstractContextManager[ProviderMaterializedInput]:
        """Return a context containing one verified local input path."""


class ProviderOutputWorkspacePort(Protocol):
    """Prepare, publish, verify, and clean provider output workspace objects."""

    def prepare_output(
        self,
        *,
        run_id: str,
        attempt_idempotency_token: str,
        logical_name: str,
        suffix: str,
    ) -> ProviderOutputTarget:
        """Prepare a deterministic durable destination with a temporary local path."""

    def publish_output(self, *, target: ProviderOutputTarget) -> ProviderPublishedOutput:
        """Publish and independently verify one completed local output."""

    def discard_local_output(self, *, target: ProviderOutputTarget) -> None:
        """Remove a local staging file without deleting a published workspace object."""


@dataclass(frozen=True)
class ProviderDependencies:
    """Contain the immutable runtime ports approved for built-in providers."""

    input_materializer: ProviderInputMaterializationPort
    output_workspace: ProviderOutputWorkspacePort
    duckdb_connections: DuckDbConnectionProvider
    operational_limits: ProviderOperationalLimits | None = None


class ProviderFactory(Protocol):
    """Construct one provider from the runtime-owned dependency graph."""

    def __call__(self, dependencies: ProviderDependencies) -> TransformationOperationProvider:
        """Return one fully injected provider instance."""


class ProviderConstructor(Protocol):
    """Construct one concrete provider class through explicit keyword injection."""

    def __call__(
        self,
        *,
        dependencies: ProviderDependencies,
    ) -> TransformationOperationProvider:
        """Return one provider whose runtime ports are fully injected."""
