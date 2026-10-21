from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RegisterDatasetCommand:
    tenant_id: str
    slug: str
    name: str
    path: str
    source_type: str
    organization_id: int | None = None
    workspace_id: int | None = None
    visibility_scope: str = "WORKSPACE"
    created_by: Any | None = None
    description: str | None = None
    tags: list[str] = field(default_factory=list)
    owner_team: str | None = None
    artifact_role: str = "PRIMARY"
    storage_provider: str | None = None
    storage_mode: str = "LOCAL_REFERENCE"
    preview_limit: int = 10
    sheet_name: str | None = None
    mime_type: str | None = None
    ingest_all_sheets: bool = True
    allow_low_confidence_schema: bool = False
    replacement_dataset_id: str | None = None


@dataclass(frozen=True)
class IngestExternalJobResultCommand:
    tenant_id: str
    dataset_id: str
    external_dataset_job_id: str
    provider_name: str
    actor: Any | None = None
    correlation_id: str | None = None
    preview_limit: int = 10
    lineage_type: str = "INGESTED_FROM"
    transformation_name: str | None = None
    transformation_hash: str | None = None
