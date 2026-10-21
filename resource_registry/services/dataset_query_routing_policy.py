from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from django.conf import settings

from resource_registry.read.artifact_read_service import DatasetArtifactReadService
from resource_registry.read.dtos import DatasetArtifactSummary
from resource_registry.read.exceptions import ArtifactNotFoundError, DatasetVersionNotFoundError
from resource_registry.read.version_read_service import DatasetVersionReadService

RoutingMode = Literal["sync_allowed", "async_recommended", "async_required"]
RoutingReason = Literal[
    "artifact_size",
    "estimated_rows",
    "estimated_cost",
    "configured_policy",
    "unknown",
]

_TOP_LEVEL_LIMIT_REGEX = re.compile(r"\blimit\s+(\d+)\b", re.IGNORECASE)


@dataclass(frozen=True)
class DatasetQueryRoutingDecision:
    mode: RoutingMode
    reason: RoutingReason
    artifact_size_bytes: int | None
    threshold_bytes: int


class DatasetQueryRoutingPolicy:
    """Decide whether dataset query execution should be synchronous or asynchronous."""

    def __init__(
        self,
        *,
        version_read_service: DatasetVersionReadService | None = None,
        artifact_read_service: DatasetArtifactReadService | None = None,
        max_sync_artifact_bytes: int | None = None,
        max_sync_estimated_rows: int | None = None,
        max_sync_timeout_seconds: float | None = None,
    ) -> None:
        default_sync_artifact_bytes = int(
            getattr(
                settings,
                "RESOURCE_REGISTRY_SYNC_QUERY_MAX_ARTIFACT_BYTES",
                getattr(settings, "RESOURCE_REGISTRY_QUERY_MAX_ARTIFACT_BYTES_SYNC", 100 * 1024 * 1024),
            ),
        )
        self._max_sync_artifact_bytes = max(
            int(max_sync_artifact_bytes or default_sync_artifact_bytes),
            1,
        )
        self._max_sync_estimated_rows = max(
            int(
                max_sync_estimated_rows
                or getattr(settings, "RESOURCE_REGISTRY_SYNC_QUERY_MAX_ESTIMATED_ROWS", 100_000),
            ),
            1,
        )
        self._max_sync_timeout_seconds = max(
            float(
                max_sync_timeout_seconds
                or getattr(settings, "RESOURCE_REGISTRY_SYNC_QUERY_MAX_TIMEOUT_SECONDS", 3.0),
            ),
            0.1,
        )
        self._version_read_service = version_read_service or DatasetVersionReadService()
        self._artifact_read_service = artifact_read_service or DatasetArtifactReadService()

    def evaluate(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        version_id: str | None,
        sql: str,
    ) -> DatasetQueryRoutingDecision:
        artifact = self._resolve_artifact(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            version_id=version_id,
        )
        artifact_size_bytes = self._safe_artifact_size(artifact)
        if (
            artifact_size_bytes is not None
            and artifact_size_bytes > self._max_sync_artifact_bytes
        ):
            return DatasetQueryRoutingDecision(
                mode="async_required",
                reason="artifact_size",
                artifact_size_bytes=artifact_size_bytes,
                threshold_bytes=self._max_sync_artifact_bytes,
            )

        estimated_rows = self._estimate_requested_rows(sql)
        if estimated_rows is not None and estimated_rows > self._max_sync_estimated_rows:
            return DatasetQueryRoutingDecision(
                mode="async_required",
                reason="estimated_rows",
                artifact_size_bytes=artifact_size_bytes,
                threshold_bytes=self._max_sync_artifact_bytes,
            )

        if artifact_size_bytes is None:
            return DatasetQueryRoutingDecision(
                mode="async_recommended",
                reason="unknown",
                artifact_size_bytes=None,
                threshold_bytes=self._max_sync_artifact_bytes,
            )

        if self._is_aggregation_query(sql) and artifact_size_bytes > int(
            self._max_sync_artifact_bytes * 0.5,
        ):
            return DatasetQueryRoutingDecision(
                mode="async_recommended",
                reason="estimated_cost",
                artifact_size_bytes=artifact_size_bytes,
                threshold_bytes=self._max_sync_artifact_bytes,
            )

        _ = self._max_sync_timeout_seconds
        return DatasetQueryRoutingDecision(
            mode="sync_allowed",
            reason="configured_policy",
            artifact_size_bytes=artifact_size_bytes,
            threshold_bytes=self._max_sync_artifact_bytes,
        )

    def _resolve_artifact(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        version_id: str | None,
    ) -> DatasetArtifactSummary | None:
        try:
            selected_version_id = version_id or self._version_read_service.get_latest_version(
                tenant_id=tenant_id,
                dataset_id=dataset_id,
            ).id
            artifacts = self._artifact_read_service.list_artifacts(
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                version_id=selected_version_id,
                limit=100,
                offset=0,
            )
            return self._select_query_artifact(artifacts)
        except (DatasetVersionNotFoundError, ArtifactNotFoundError):
            return None

    def _select_query_artifact(
        self,
        artifacts: list[DatasetArtifactSummary],
    ) -> DatasetArtifactSummary | None:
        if not artifacts:
            return None
        for artifact in artifacts:
            metadata = artifact.metadata_json if isinstance(artifact.metadata_json, dict) else {}
            if (
                artifact.lifecycle_status == "ACTIVE"
                and str(artifact.format).strip().upper() == "PARQUET"
                and bool(metadata.get("is_canonical"))
            ):
                return artifact
        for artifact in artifacts:
            if artifact.lifecycle_status == "ACTIVE" and artifact.artifact_role == "PRIMARY":
                return artifact
        for artifact in artifacts:
            if artifact.lifecycle_status == "ACTIVE":
                return artifact
        return None

    def _safe_artifact_size(self, artifact: DatasetArtifactSummary | None) -> int | None:
        if artifact is None:
            return None
        value = getattr(artifact, "size_bytes", None)
        if value is None:
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    def _estimate_requested_rows(self, sql: str) -> int | None:
        match = _TOP_LEVEL_LIMIT_REGEX.search(str(sql or ""))
        if match is None:
            return None
        try:
            return int(match.group(1))
        except (TypeError, ValueError):
            return None

    def _is_aggregation_query(self, sql: str) -> bool:
        lowered = str(sql or "").lower()
        aggregate_markers = (" group by ", " count(", " sum(", " avg(", " min(", " max(")
        return any(marker in lowered for marker in aggregate_markers)
