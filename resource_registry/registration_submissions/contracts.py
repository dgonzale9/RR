"""Immutable contracts for registration submission routing and results."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any
from uuid import UUID


class RegistrationJobKind(StrEnum):
    """Identify the async job family selected from a trusted upload format."""

    WORKBOOK_INGESTION = "WorkbookIngestionJob"
    DATASET_REGISTRATION = "DatasetRegistrationJob"


@dataclass(frozen=True, kw_only=True)
class RegistrationJobReference:
    """Reference one persisted format-specific registration job."""

    job_id: UUID
    job_type: RegistrationJobKind


@dataclass(frozen=True, kw_only=True)
class RegistrationSubmissionResult:
    """Return the stable result of an authorized registration submission."""

    registration_id: UUID
    status: str
    job_type: RegistrationJobKind
    idempotent_replay: bool


@dataclass(frozen=True, kw_only=True)
class RegistrationJobStatusSnapshot:
    """Expose one scoped registration job without leaking ORM objects."""

    registration_id: UUID
    job_type: RegistrationJobKind
    status: str
    result_payload: Mapping[str, Any]
    error_code: str | None

    def __post_init__(self) -> None:
        """Freeze the result payload at the persistence boundary."""
        object.__setattr__(self, "result_payload", MappingProxyType(dict(self.result_payload)))
