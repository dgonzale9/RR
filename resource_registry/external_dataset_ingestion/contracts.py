"""Immutable contracts for backend external Dataset file ingestion."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, kw_only=True, slots=True)
class ExternalFileDescriptor:
    """Describe one server-controlled external file materialization."""

    provider: str
    reference_hash: str
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    local_path: Path


class ExternalDatasetFileProvider(Protocol):
    """Acquire one external file without owning registration or authorization."""

    provider_name: str

    def acquire(self) -> ExternalFileDescriptor:
        """Materialize one trusted file into a controlled temporary artifact."""

    def cleanup(self, descriptor: ExternalFileDescriptor) -> None:
        """Remove the controlled temporary artifact idempotently."""


@dataclass(frozen=True, kw_only=True, slots=True)
class ExternalDatasetIngestionResult:
    """Return one safe asynchronous registration acceptance result."""

    registration_id: UUID
    workspace_id: int | None
    namespace_id: UUID
    state: str
    job_type: str
    idempotent_replay: bool


@dataclass(frozen=True, kw_only=True, slots=True)
class ExternalDatasetIngestionStatus:
    """Return one scoped registration status and safe completed Dataset metadata."""

    registration_id: UUID
    workspace_id: int | None
    namespace_id: UUID
    state: str
    terminal: bool
    successful: bool
    job_type: str
    error_code: str | None
    datasets: tuple[dict[str, object], ...]
