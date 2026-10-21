from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from resource_registry.models import Dataset, DatasetArtifact, DatasetVersion, WorkbookIngestionGroup
from resource_registry.services.duckdb_service import (
    DuckDbConnectionProvider,
    InMemoryDuckDbConnectionProvider,
)
from resource_registry.storage.factory import DataRegistryStorageFactory
from resource_registry.storage.read_adapters import (
    ArtifactReadAdapter,
    LocalArtifactReadAdapter,
    StorageProviderArtifactReadAdapter,
    TemporaryArtifact,
)


class WorkbookConsolidationError(RuntimeError):
    """Raised when workbook consolidation cannot be completed safely."""


@dataclass
class WorkbookConsolidationResult:
    local_path: str
    row_count: int
    columns: list[str]
    sheets: list[str] = field(default_factory=list)
    _temporary_artifacts: list[TemporaryArtifact] = field(default_factory=list, repr=False)
    _temporary_directory: str | None = field(default=None, repr=False)

    def cleanup(self) -> None:
        for artifact in self._temporary_artifacts:
            try:
                artifact.cleanup()
            except Exception:
                continue
        if self._temporary_directory:
            shutil.rmtree(self._temporary_directory, ignore_errors=True)

    def __enter__(self) -> WorkbookConsolidationResult:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.cleanup()


class WorkbookConsolidationService:
    """Build temporary consolidated parquet from workbook sheet datasets."""

    def __init__(
        self,
        *,
        connection_provider: DuckDbConnectionProvider | None = None,
        storage_factory: DataRegistryStorageFactory | None = None,
        adapters: list[ArtifactReadAdapter] | None = None,
    ) -> None:
        self._connection_provider = connection_provider or InMemoryDuckDbConnectionProvider()
        self._storage_factory = storage_factory or DataRegistryStorageFactory()
        self._adapters = adapters

    def materialize_consolidated_parquet(
        self,
        *,
        tenant_id: str,
        workbook_group_id: str,
        max_rows_per_sheet: int | None = None,
    ) -> WorkbookConsolidationResult:
        group = WorkbookIngestionGroup.objects.filter(
            id=workbook_group_id,
            tenant_id=tenant_id,
        ).first()
        if group is None:
            raise WorkbookConsolidationError("Workbook ingestion group not found for tenant.")

        datasets = list(
            Dataset.objects.filter(
                tenant_id=tenant_id,
                workbook_group_id=workbook_group_id,
            ).order_by("slug", "id"),
        )
        if not datasets:
            raise WorkbookConsolidationError("No datasets found for workbook group.")

        dataset_ids = [dataset.id for dataset in datasets]
        latest_versions = self._latest_versions_by_dataset(dataset_ids)
        if not latest_versions:
            raise WorkbookConsolidationError("No dataset versions available for workbook group.")
        version_ids = [version.id for version in latest_versions.values()]
        artifacts_by_version = self._best_artifacts_by_version(version_ids)

        temp_directory = tempfile.mkdtemp(prefix="workbook-consolidation-")
        temporary_artifacts: list[TemporaryArtifact] = []
        consolidated_path = str(Path(temp_directory) / "workbook_consolidated.parquet")
        sheet_names: list[str] = []

        try:
            with self._connection_provider.connect() as connection:
                view_selects: list[str] = []
                for index, dataset in enumerate(datasets, start=1):
                    latest_version = latest_versions.get(str(dataset.id))
                    if latest_version is None:
                        continue
                    artifact = artifacts_by_version.get(str(latest_version.id))
                    if artifact is None:
                        continue
                    adapter = self._resolve_adapter(artifact_uri=str(artifact.uri))
                    materialized = adapter.materialize_to_temp(
                        str(artifact.uri),
                        temp_directory=temp_directory,
                    )
                    temporary_artifacts.append(materialized)
                    sheet_name = str(artifact.sheet_name or dataset.slug or f"sheet_{index}")
                    sheet_names.append(sheet_name)
                    safe_path = str(materialized.local_path).replace("'", "''")
                    source_select = f"SELECT * FROM read_parquet('{safe_path}')"
                    if max_rows_per_sheet is not None and int(max_rows_per_sheet) > 0:
                        source_select = (
                            f"SELECT * FROM (SELECT * FROM read_parquet('{safe_path}') LIMIT "
                            f"{int(max_rows_per_sheet)})"
                        )
                    safe_sheet_name = sheet_name.replace("'", "''")
                    safe_dataset_id = str(dataset.id).replace("'", "''")
                    safe_dataset_slug = str(dataset.slug).replace("'", "''")
                    view_selects.append(
                        "("
                        f"SELECT source_data.*, "
                        f"'{safe_sheet_name}' AS __sheet_name, "
                        f"'{safe_dataset_id}' AS __dataset_id, "
                        f"'{safe_dataset_slug}' AS __dataset_slug "
                        f"FROM ({source_select}) AS source_data"
                        ")",
                    )

                if not view_selects:
                    raise WorkbookConsolidationError(
                        "No active artifacts available for workbook consolidation.",
                    )

                union_sql = " UNION ALL BY NAME ".join(view_selects)
                connection.execute(
                    f"CREATE OR REPLACE TEMP VIEW workbook_consolidated AS {union_sql}",
                )
                safe_output_path = consolidated_path.replace("'", "''")
                connection.execute(
                    f"COPY workbook_consolidated TO '{safe_output_path}' (FORMAT PARQUET)",
                )
                row_count = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM read_parquet(?)",
                        [consolidated_path],
                    ).fetchone()[0],
                )
                columns = [
                    str(row[0])
                    for row in connection.execute(
                        "DESCRIBE SELECT * FROM read_parquet(?)",
                        [consolidated_path],
                    ).fetchall()
                ]
            return WorkbookConsolidationResult(
                local_path=consolidated_path,
                row_count=row_count,
                columns=columns,
                sheets=sheet_names,
                _temporary_artifacts=temporary_artifacts,
                _temporary_directory=temp_directory,
            )
        except Exception as error:
            for artifact in temporary_artifacts:
                try:
                    artifact.cleanup()
                except Exception:
                    continue
            shutil.rmtree(temp_directory, ignore_errors=True)
            if isinstance(error, WorkbookConsolidationError):
                raise
            raise WorkbookConsolidationError(
                "Unable to materialize consolidated workbook parquet.",
            ) from error

    def _latest_versions_by_dataset(self, dataset_ids: list[str]) -> dict[str, DatasetVersion]:
        versions = list(
            DatasetVersion.objects.filter(dataset_id__in=dataset_ids).order_by(
                "dataset_id",
                "-version_number",
                "-created_at",
            ),
        )
        latest: dict[str, DatasetVersion] = {}
        for version in versions:
            key = str(version.dataset_id)
            if key not in latest:
                latest[key] = version
        return latest

    def _best_artifacts_by_version(self, version_ids: list[str]) -> dict[str, DatasetArtifact]:
        artifacts = list(
            DatasetArtifact.objects.filter(
                dataset_version_id__in=version_ids,
                lifecycle_status="ACTIVE",
            ).order_by("dataset_version_id", "-created_at"),
        )
        result: dict[str, DatasetArtifact] = {}
        for artifact in artifacts:
            version_key = str(artifact.dataset_version_id)
            existing = result.get(version_key)
            if existing is None:
                result[version_key] = artifact
                continue
            existing_is_canonical = self._is_canonical(existing)
            current_is_canonical = self._is_canonical(artifact)
            if current_is_canonical and not existing_is_canonical:
                result[version_key] = artifact
        return result

    def _is_canonical(self, artifact: DatasetArtifact) -> bool:
        metadata = artifact.metadata_json if isinstance(artifact.metadata_json, dict) else {}
        return (
            str(artifact.format).upper() == "PARQUET"
            and bool(metadata.get("is_canonical"))
        )

    def _resolve_adapter(self, *, artifact_uri: str) -> ArtifactReadAdapter:
        adapters = self._adapters or self._build_default_adapters()
        for adapter in adapters:
            if adapter.can_handle(artifact_uri):
                return adapter
        raise WorkbookConsolidationError("No artifact adapter available for consolidation.")

    def _build_default_adapters(self) -> list[ArtifactReadAdapter]:
        adapters: list[ArtifactReadAdapter] = [
            LocalArtifactReadAdapter(
                storage_provider=self._storage_factory.create_provider("local"),
            ),
        ]
        try:
            adapters.append(
                StorageProviderArtifactReadAdapter(
                    storage_provider=self._storage_factory.create_provider("azure_blob"),
                    schemes=("azure://", "https://"),
                ),
            )
        except Exception:
            pass
        return adapters
