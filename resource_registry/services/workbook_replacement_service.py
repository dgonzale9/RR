"""Replace the complete sheet set of one Workspace-owned Workbook group."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from django.db import transaction
from django.utils import timezone

from resource_registry.application.commands import RegisterDatasetCommand
from resource_registry.application.dataset_registration import (
    DatasetRegistrationFacade,
    RegisterDatasetBatchResult,
    RegisterDatasetResult,
)
from resource_registry.application.exceptions import ValidationError
from resource_registry.application.idempotency import DatasetRegistrationIdempotencyService
from resource_registry.models import (
    ArtifactRole,
    Dataset,
    DatasetArtifact,
    DatasetStatus,
    DatasetVersion,
    WorkbookIngestionGroup,
    WorkbookIngestionJob,
)
from resource_registry.registration_submissions.services import RegistrationSubmissionService
from resource_registry.services.excel_dataset_adapter import ExcelDatasetAdapter
from resource_registry.services.workbook_upload_materialization_service import (
    MaterializedWorkbookUpload,
)
from resource_registry.storage.factory import DataRegistryStorageFactory

_logger = logging.getLogger("app-logger")


@dataclass(frozen=True)
class WorkbookReplacementResult:
    """Return replacement outcomes for status and audit without raw sheet data."""

    batch: RegisterDatasetBatchResult
    archived_dataset_ids: tuple[str, ...]
    no_changes_detected: bool


class WorkbookReplacementService:
    """Apply one all-sheet replacement under a stable Workbook group identity."""

    def __init__(
        self,
        *,
        registration_facade: DatasetRegistrationFacade | None = None,
        submission_service: RegistrationSubmissionService | None = None,
        excel_adapter: ExcelDatasetAdapter | None = None,
    ) -> None:
        """Compose the existing registration, authorization, and workbook adapters.

        Args:
            registration_facade: Dataset registration workflow.
            submission_service: Replacement authorization workflow.
            excel_adapter: Worksheet inspection adapter.
        """
        self._registration_facade = registration_facade or DatasetRegistrationFacade()
        self._submission_service = submission_service or RegistrationSubmissionService()
        self._excel_adapter = excel_adapter or ExcelDatasetAdapter()
        self._idempotency_service = DatasetRegistrationIdempotencyService()

    def replace(
        self,
        *,
        job: WorkbookIngestionJob,
        source: MaterializedWorkbookUpload,
    ) -> WorkbookReplacementResult:
        """Register every source sheet and archive removed sheets atomically.

        Args:
            job: Claimed upload-backed Workbook job.
            source: Verified and materialized source Workbook.

        Returns:
            Complete changed, unchanged, and archived Dataset identifiers.

        Raises:
            ValidationError: If the target group, sheet mapping, or content is invalid.
        """
        created_snapshot_uris: list[str] = []
        try:
            return self._replace_atomically(
                job=job,
                source=source,
                created_snapshot_uris=created_snapshot_uris,
            )
        except Exception:
            self._cleanup_rolled_back_snapshots(
                source=source,
                snapshot_uris=created_snapshot_uris,
            )
            raise

    @transaction.atomic
    def _replace_atomically(
        self,
        *,
        job: WorkbookIngestionJob,
        source: MaterializedWorkbookUpload,
        created_snapshot_uris: list[str],
    ) -> WorkbookReplacementResult:
        """Hold the group lock and publish a complete replacement in one transaction."""
        submission = job.registration_submission
        if submission is None or submission.replacement_workbook_group_id is None:
            raise ValidationError(
                "Workbook replacement target is missing.", code="workbook_group_not_found"
            )
        group = (
            WorkbookIngestionGroup.objects.select_for_update()
            .filter(
                pk=submission.replacement_workbook_group_id,
                tenant_id=source.tenant_id,
            )
            .first()
        )
        if group is None:
            raise ValidationError(
                "Workbook replacement group is unavailable.", code="workbook_group_not_found"
            )
        self._submission_service.assert_replacement_execution_authorized(
            submission=submission,
        )
        sheet_names = self._excel_adapter.list_worksheets(source.local_path)
        if not sheet_names:
            raise ValidationError("Workbook has no sheets.", code="empty_workbook")
        datasets_by_sheet = self._get_group_datasets_by_sheet(group=group)
        incoming_sheet_names = set(sheet_names)
        source_workbook_hash = self._idempotency_service.compute_content_hash(source.local_path)
        slug_base = str(group.metadata_json.get("source_slug_base") or "workbook")
        used_slugs: set[str] = set()
        created: list[RegisterDatasetResult] = []
        deduplicated: list[RegisterDatasetResult] = []
        for sheet_name in sheet_names:
            existing = datasets_by_sheet.get(sheet_name)
            slug = self._registration_facade._derive_sheet_slug(
                base_slug=slug_base,
                sheet_name=sheet_name,
                used_slugs=used_slugs,
            )
            if existing is not None:
                slug = existing.slug
                existing = Dataset.objects.select_for_update().get(pk=existing.pk)
            elif Dataset.objects.filter(tenant_id=source.tenant_id, slug=slug).exists():
                raise ValidationError(
                    "Workbook sheet Dataset identity conflicts with an existing Dataset.",
                    status=409,
                    code="replacement_sheet_identity_conflict",
                )
            previous_version = (
                existing.versions.order_by("-version_number").first()
                if existing is not None
                else None
            )
            with self._excel_adapter.materialize_to_csv(
                source.local_path, sheet_name=sheet_name
            ) as normalized_sheet:
                sheet_content_hash = self._idempotency_service.compute_content_hash(
                    normalized_sheet.normalized_path
                )
            content_hash = (
                source_workbook_hash
                if previous_version is not None
                and previous_version.content_hash == source_workbook_hash
                else sheet_content_hash
            )
            command = RegisterDatasetCommand(
                tenant_id=str(source.tenant_id),
                slug=slug,
                name=(
                    existing.name
                    if existing is not None
                    else self._registration_facade._derive_sheet_dataset_name(
                        base_name=Path(source.original_filename).stem,
                        sheet_name=sheet_name,
                        sheet_count=len(sheet_names),
                    )
                ),
                path=source.local_path,
                source_type=(existing.source_type if existing is not None else "FILE_UPLOAD"),
                organization_id=submission.organization_id,
                workspace_id=submission.workspace_id,
                visibility_scope="WORKSPACE",
                created_by=submission.submitted_by,
                storage_provider=(
                    "AZURE_BLOB" if source.storage_provider == "azure_blob" else "LOCAL"
                ),
                storage_mode="LOCAL_REFERENCE",
                mime_type=source.content_type,
                ingest_all_sheets=False,
                sheet_name=sheet_name,
                replacement_dataset_id=(str(existing.id) if existing is not None else None),
            )
            result = self._registration_facade.register(
                command,
                precomputed_content_hash=content_hash,
                workbook_group=group,
                pre_uploaded_source_metadata=source.source_metadata,
                canonical_provider_key=source.storage_provider,
            )
            if previous_version is not None and previous_version.id == result.dataset_version_id:
                deduplicated.append(result)
            else:
                created_snapshot_uris.extend(
                    DatasetArtifact.objects.filter(
                        dataset_version_id=result.dataset_version_id,
                        artifact_role=ArtifactRole.SNAPSHOT,
                    ).values_list("uri", flat=True)
                )
                created.append(result)
        archived_ids: list[str] = []
        for sheet_name, dataset in datasets_by_sheet.items():
            if sheet_name in incoming_sheet_names or dataset.status == DatasetStatus.ARCHIVED:
                continue
            Dataset.objects.select_for_update().get(pk=dataset.pk)
            Dataset.objects.filter(pk=dataset.pk).update(
                status=DatasetStatus.ARCHIVED,
                archived_at=timezone.now(),
            )
            archived_ids.append(str(dataset.id))
        batch = RegisterDatasetBatchResult(
            created=created,
            deduplicated=deduplicated,
            skipped=[],
            total_processed=len(sheet_names),
            total_created=len(created),
            total_deduplicated=len(deduplicated),
            total_skipped=0,
        )
        return WorkbookReplacementResult(
            batch=batch,
            archived_dataset_ids=tuple(archived_ids),
            no_changes_detected=not created and not archived_ids,
        )

    @staticmethod
    def _cleanup_rolled_back_snapshots(
        *,
        source: MaterializedWorkbookUpload,
        snapshot_uris: list[str],
    ) -> None:
        """Delete canonical objects created before a later sheet caused a rollback."""
        if not snapshot_uris:
            return
        try:
            provider = DataRegistryStorageFactory().create_provider(source.storage_provider)
            for uri in snapshot_uris:
                try:
                    provider.delete(uri)
                except Exception:
                    _logger.exception("Workbook replacement snapshot cleanup failed")
        except Exception:
            _logger.exception("Workbook replacement storage provider cleanup failed")

    @staticmethod
    def _get_group_datasets_by_sheet(
        *,
        group: WorkbookIngestionGroup,
    ) -> dict[str, Dataset]:
        """Load current sheet identities in bounded queries independent of sheet count."""
        latest_version_ids = list(
            DatasetVersion.objects.filter(dataset__workbook_group=group)
            .order_by("dataset_id", "-version_number")
            .distinct("dataset_id")
            .values_list("id", flat=True),
        )
        artifacts = DatasetArtifact.objects.filter(
            dataset_version_id__in=latest_version_ids,
            artifact_role=ArtifactRole.PRIMARY,
        ).select_related("dataset_version__dataset")
        datasets: dict[str, Dataset] = {}
        for candidate in artifacts:
            artifact = cast(DatasetArtifact, candidate)
            sheet_name = str(artifact.sheet_name or "")
            if sheet_name in datasets:
                raise ValidationError(
                    "Workbook group has ambiguous sheet ownership.",
                    status=409,
                    code="replacement_sheet_identity_conflict",
                )
            datasets[sheet_name] = artifact.dataset_version.dataset
        return datasets
