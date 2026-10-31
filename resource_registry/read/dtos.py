from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SENSITIVE_KEYS = {
    "preview",
    "raw_content",
    "rows",
    "credentials",
    "token",
    "password",
    "secret",
}


@dataclass(frozen=True)
class RegistryNamespaceSummary:
    """Describe one immutable Workspace- or Organization-owned Registry namespace."""

    id: str
    workspace_id: int | None
    organization_id: int
    created_at: str


@dataclass(frozen=True)
class DatasetSummary:
    id: str
    tenant_id: str
    slug: str
    name: str
    source_type: str
    status: str
    created_at: str
    updated_at: str
    sheet_name: str | None = None
    current_version_id: str | None = None


@dataclass(frozen=True)
class DatasetDetails:
    id: str
    tenant_id: str
    slug: str
    name: str
    source_type: str
    status: str
    description: str | None
    owner_team: str | None
    tags: list[str]
    created_at: str
    updated_at: str
    archived_at: str | None
    current_version_id: str | None = None


@dataclass(frozen=True)
class DatasetVersionSummary:
    id: str
    dataset_id: str
    tenant_id: str
    version_number: int
    status: str
    source_uri: str
    row_count: int | None
    column_count: int | None
    created_at: str
    content_hash: str = ""
    is_current: bool = False


@dataclass(frozen=True)
class DatasetArtifactSummary:
    id: str
    dataset_version_id: str
    tenant_id: str
    artifact_role: str
    uri: str
    format: str
    size_bytes: int
    checksum: str | None
    storage_provider: str | None
    lifecycle_status: str
    created_at: str
    metadata_json: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DatasetSchemaSummary:
    id: str
    dataset_version_id: str
    tenant_id: str
    schema_hash: str
    column_count: int
    schema_json: list[dict[str, Any]]
    created_at: str


@dataclass(frozen=True)
class DatasetProfileSummary:
    id: str
    dataset_version_id: str
    tenant_id: str
    profile_type: str
    profile_hash: str
    metrics_json: dict[str, Any]
    computed_at: str


@dataclass(frozen=True)
class ExternalDatasetJobSummary:
    id: str
    dataset_id: str
    tenant_id: str
    job_type: str
    status: str
    provider: str | None
    provider_job_ref: str | None
    retry_count: int
    error_code: str | None
    requested_at: str
    started_at: str | None
    finished_at: str | None
    updated_at: str
    output_dataset_version_id: str | None


@dataclass(frozen=True)
class TransformationDefinitionSummary:
    id: str
    tenant_id: str
    definition_key: str
    name: str
    archived_at: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class TransformationDefinitionDetails:
    id: str
    tenant_id: str
    definition_key: str
    name: str
    description: str | None
    created_by_id: str | None
    archived_at: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class TransformationDefinitionVersionSummary:
    id: str
    definition_id: str
    version_number: int
    status: str
    specification_checksum: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class TransformationDefinitionVersionDetails:
    id: str
    definition_id: str
    version_number: int
    status: str
    specification_checksum: str
    specification_json: dict[str, Any] | list[Any]
    created_by_id: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class DatasetContextSummary:
    dataset: DatasetDetails
    latest_version: DatasetVersionSummary | None
    schema: DatasetSchemaSummary | None
    profile: DatasetProfileSummary | None
    artifact: DatasetArtifactSummary | None
    policy_provenance: dict[str, Any] | None = None


@dataclass(frozen=True)
class WorkbookSheetContextSummary:
    dataset_id: str
    slug: str
    name: str
    sheet_name: str | None
    latest_version_id: str | None
    row_count: int | None
    column_count: int | None
    schema_columns: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class WorkbookContextSummary:
    workbook_group_id: str
    tenant_id: str
    status: str
    source_file_name: str | None
    source_uri: str
    source_hash: str
    total_sheets: int
    total_created: int
    total_deduplicated: int
    total_skipped: int
    sheets: list[WorkbookSheetContextSummary] = field(default_factory=list)
    created_at: str | None = None
    updated_at: str | None = None


def to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def sanitize_uri(uri: str) -> str:
    lower = uri.lower()
    if "sig=" not in lower:
        return uri
    if "://" not in uri:
        return uri.split("?", 1)[0]

    parsed = urlsplit(uri)
    safe_query_items = [(key, val) for key, val in parse_qsl(parsed.query) if key.lower() != "sig"]
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(safe_query_items), ""),
    )


def sanitize_json(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, nested in value.items():
            if key.lower() in SENSITIVE_KEYS:
                continue
            cleaned[key] = sanitize_json(nested)
        return cleaned
    if isinstance(value, list):
        return [sanitize_json(item) for item in value]
    return value
