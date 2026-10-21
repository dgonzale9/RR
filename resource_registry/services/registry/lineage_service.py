from __future__ import annotations

from typing import Any

from resource_registry.models import DatasetLineage, DatasetVersion


class InvalidLineageError(ValueError):
    """Raised when lineage input violates domain rules."""


class LineageService:
    """Records lineage edges while enforcing lineage rules."""

    def record_lineage(  # noqa: PLR0913
        self,
        *,
        downstream_dataset_version: DatasetVersion,
        lineage_type: str,
        upstream_dataset_version: DatasetVersion | None = None,
        upstream_external_uri: str | None = None,
        transformation_name: str | None = None,
        transformation_hash: str | None = None,
        job_id: str | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> DatasetLineage:
        self._validate_lineage_inputs(
            downstream_dataset_version=downstream_dataset_version,
            upstream_dataset_version=upstream_dataset_version,
            upstream_external_uri=upstream_external_uri,
        )
        return DatasetLineage.objects.create(
            downstream_dataset_version=downstream_dataset_version,
            upstream_dataset_version=upstream_dataset_version,
            upstream_external_uri=upstream_external_uri,
            lineage_type=lineage_type,
            transformation_name=transformation_name,
            transformation_hash=transformation_hash,
            job_id=job_id,
            metadata_json=metadata_json or {},
        )

    def record_lineage_idempotently(  # noqa: PLR0913
        self,
        *,
        downstream_dataset_version: DatasetVersion,
        lineage_type: str,
        upstream_dataset_version: DatasetVersion | None = None,
        upstream_external_uri: str | None = None,
        transformation_name: str | None = None,
        transformation_hash: str | None = None,
        job_id: str | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> DatasetLineage:
        """Record or strictly resolve one identical lineage edge for a durable job."""
        self._validate_lineage_inputs(
            downstream_dataset_version=downstream_dataset_version,
            upstream_dataset_version=upstream_dataset_version,
            upstream_external_uri=upstream_external_uri,
        )
        expected_metadata = metadata_json or {}
        existing = DatasetLineage.objects.filter(
            downstream_dataset_version=downstream_dataset_version,
            upstream_dataset_version=upstream_dataset_version,
            upstream_external_uri=upstream_external_uri,
            job_id=job_id,
        ).first()
        if existing is not None:
            if (
                existing.lineage_type != lineage_type
                or existing.transformation_name != transformation_name
                or existing.transformation_hash != transformation_hash
                or existing.metadata_json != expected_metadata
            ):
                raise InvalidLineageError("Existing lineage does not match registration intent.")
            return existing
        return self.record_lineage(
            downstream_dataset_version=downstream_dataset_version,
            lineage_type=lineage_type,
            upstream_dataset_version=upstream_dataset_version,
            upstream_external_uri=upstream_external_uri,
            transformation_name=transformation_name,
            transformation_hash=transformation_hash,
            job_id=job_id,
            metadata_json=expected_metadata,
        )

    def _validate_lineage_inputs(
        self,
        *,
        downstream_dataset_version: DatasetVersion,
        upstream_dataset_version: DatasetVersion | None,
        upstream_external_uri: str | None,
    ) -> None:
        has_upstream_version = upstream_dataset_version is not None
        has_external_uri = bool(upstream_external_uri)

        if has_upstream_version == has_external_uri:
            raise InvalidLineageError(
                (
                    "Lineage must include exactly one upstream source: internal "
                    "version or external URI."
                ),
            )

        if upstream_dataset_version is not None and (
            upstream_dataset_version == downstream_dataset_version
        ):
            raise InvalidLineageError("Lineage cannot point a dataset version to itself.")

        if upstream_dataset_version is not None:
            upstream_tenant = upstream_dataset_version.dataset.tenant_id
            downstream_tenant = downstream_dataset_version.dataset.tenant_id
            if str(upstream_tenant) != str(downstream_tenant):
                raise InvalidLineageError("Cross-tenant internal lineage is not allowed.")
