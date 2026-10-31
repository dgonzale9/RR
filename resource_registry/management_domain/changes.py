"""Own durable, explicit dataset change proposals and their publication boundary."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import UUID, uuid4

from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from guardian.shortcuts import assign_perm

from Organization.models import Organization
from resource_registry.cleanup.inventory import register_dataset_objects
from resource_registry.intake.services import StagedResourceIntakeService
from resource_registry.management_domain.authorization import DatasetManagementAuthorization
from resource_registry.management_domain.background import schedule_dataset_preparation
from resource_registry.management_domain.errors import DatasetManagementError
from resource_registry.management_domain.external_sources import (
    PENDING_STATES,
    lock_change_source,
    normalize_source_reference,
    require_source_authority,
)
from resource_registry.management_domain.lifecycle import (
    DatasetLifecycleService,
    record_dataset_activity,
    require_revision,
)
from resource_registry.management_domain.publications import (
    reconcile_dataset_publication,
    record_dataset_publication,
    reserve_dataset_publication,
)
from resource_registry.management_domain.source_writes import source_publication
from resource_registry.models import (
    Dataset,
    DatasetChange,
    DatasetChangeStatus,
    DatasetExternalSource,
    DatasetWorkbook,
    LargeFileUploadSession,
    RegistryNamespace,
    StagedResourceIntake,
    WorkbookIngestionGroup,
)
from resource_registry.services.registry.dataset_registry_service import DatasetRegistryService
from resource_registry.services.registry.workbook_group_service import WorkbookIngestionGroupService
from resource_registry.storage.base import StorageObjectMetadata, StorageProvider
from resource_registry.storage.factory import DataRegistryStorageFactory

# Django JSONField and the existing context builder expose third-party/untyped JSON values.
JsonObject = dict[str, Any]


class DatasetChangeService:
    """Authorize, revise and atomically publish durable dataset proposals."""

    def __init__(self, user: User, organization: Organization | None) -> None:
        """Initialize host-bound authority.

        Args:
            user: Authenticated actor.
            organization: Organization resolved from the request host.
        """
        self.user: User = user
        self.organization: Organization | None = organization
        self.authorization: DatasetManagementAuthorization = DatasetManagementAuthorization(
            user, organization
        )

    def get_change(self, identifier: UUID | str, *, lock: bool = False) -> DatasetChange:
        """Return an owned proposal or one visible to its destination manager."""
        queryset = DatasetChange.objects.filter(namespace_id__in=self.authorization.namespace_ids)
        if lock:
            queryset = queryset.select_for_update()
        change = queryset.filter(pk=identifier).first()
        if change is None or not (
            change.submitted_by_id == self.user.pk
            or self.authorization.can_manage_namespace(
                self.authorization.get_namespace(change.namespace_id)
            )
        ):
            raise DatasetManagementError("not_found", "Update not found.", 404)
        if not lock and change.status in {"QUEUED", "PROCESSING"}:
            schedule_dataset_preparation(change.id)
        return change

    @transaction.atomic
    def create(
        self,
        *,
        namespace_id: UUID | str,
        idempotency_key: str,
        specification: Mapping[str, object],
        intake_id: UUID | str | None = None,
        upload_id: UUID | str | None = None,
        source_reference: Mapping[str, object] | None = None,
        expected_source_revision: int | None = None,
    ) -> DatasetChange:
        """Queue one proposal using a server-verified file owned by the caller.

        Args:
            namespace_id: Explicit destination, checked against current admission.
            idempotency_key: Actor- and destination-scoped request identity.
            specification: Explicit dataset/workbook targets and sheet mapping.
            intake_id: Optional owned verified private intake.
            upload_id: Optional completed owned upload in the destination.
            source_reference: Optional immutable provider, external ID and opaque version.
            expected_source_revision: Source control revision, initially zero.

        Returns:
            The existing identical request or a new queued proposal.

        Raises:
            DatasetManagementError: If ownership, scope, identity or targets are invalid.
        """
        self.authorization.require_enabled()
        namespace = self.authorization.get_namespace(namespace_id)
        if not idempotency_key.strip() or len(idempotency_key) > 128:
            raise DatasetManagementError(
                "invalid_idempotency_key", "Provide a request key up to 128 characters.", 400
            )
        if bool(intake_id) == bool(upload_id):
            raise DatasetManagementError(
                "invalid_source", "Select exactly one verified upload or intake.", 400
            )
        normalized = self.validate_specification(specification)
        reference = (
            normalize_source_reference(source_reference) if source_reference is not None else None
        )
        if (reference is None) != (expected_source_revision is None):
            raise DatasetManagementError(
                "invalid_source_reference",
                "Source reference and revision are required together.",
                400,
            )
        external_source = None
        if reference is not None:
            if type(expected_source_revision) is not int or expected_source_revision < 0:
                raise DatasetManagementError(
                    "invalid_revision", "Provide a nonnegative source revision.", 400
                )
            require_source_authority(self.authorization, namespace)
            external_source, _ = DatasetExternalSource.objects.get_or_create(
                namespace=namespace,
                provider=reference["provider"],
                external_id=reference["external_id"],
                defaults={"created_by": self.user},
            )
            external_source = DatasetExternalSource.objects.select_for_update().get(
                pk=external_source.pk
            )
        fingerprint_data: JsonObject = {
            "intake": str(intake_id),
            "upload": str(upload_id),
            "specification": normalized,
        }
        if reference is not None:
            fingerprint_data.update(
                source_reference=reference, expected_source_revision=expected_source_revision
            )
        fingerprint = hashlib.sha256(
            json.dumps(
                fingerprint_data,
                sort_keys=True,
            ).encode()
        ).hexdigest()
        existing = DatasetChange.objects.filter(
            namespace=namespace, submitted_by=self.user, idempotency_key=idempotency_key
        ).first()
        if existing:
            if existing.request_fingerprint != fingerprint:
                raise DatasetManagementError(
                    "idempotency_conflict", "The request key was used for different content."
                )
            if external_source:
                self.validate_external_replay(existing)
            else:
                self.validate_targets(namespace, normalized, check_revision=False)
            if existing.status in {"QUEUED", "PROCESSING"}:
                schedule_dataset_preparation(existing.id)
            return existing
        if external_source:
            assert expected_source_revision is not None
            require_revision(external_source.revision, expected_source_revision)
            if external_source.changes.filter(status__in=PENDING_STATES).exists():
                raise DatasetManagementError(
                    "source_change_pending", "Resolve the existing source proposal first."
                )
        self.validate_targets(namespace, normalized, external_source=external_source)
        source: StagedResourceIntake | LargeFileUploadSession | None
        if intake_id:
            source = (
                StagedResourceIntake.objects.select_for_update()
                .filter(
                    pk=intake_id,
                    submitted_by=self.user,
                    status__in=["READY_FOR_PUBLICATION", "PUBLISHED"],
                )
                .first()
            )
        else:
            source = (
                LargeFileUploadSession.objects.select_for_update()
                .filter(
                    pk=str(upload_id),
                    submitted_by=self.user,
                    registry_namespace=namespace,
                    status="COMPLETED",
                )
                .first()
            )
        if source is None:
            raise DatasetManagementError("source_unavailable", "Verified source not found.", 404)
        if isinstance(source, StagedResourceIntake):
            source = self.record_intake_source_use(source.pk)
        self.validate_source_mapping(normalized, file_format=source.resolved_file_format)
        if (
            source.expires_at
            and source.expires_at <= timezone.now()
            and source.status != "PUBLISHED"
        ):
            raise DatasetManagementError(
                "source_expired", "Upload the file again; this source expired."
            )
        change, _ = DatasetChange.objects.get_or_create(
            namespace=namespace,
            submitted_by=self.user,
            idempotency_key=idempotency_key,
            defaults={
                "request_fingerprint": fingerprint,
                "specification": normalized,
                "intake_id": intake_id,
                "upload_id": upload_id,
                "workbook_id": normalized.get("workbook_id"),
                "external_source": external_source,
                "source_reference": reference or {},
                "expected_source_revision": (
                    external_source.revision + 1 if external_source else None
                ),
            },
        )
        if change.request_fingerprint != fingerprint:
            raise DatasetManagementError(
                "idempotency_conflict", "The request key was used for different content."
            )
        if external_source:
            external_source.revision += 1
            external_source.save(update_fields=["revision", "updated_at"])
        if intake_id:
            source.expires_at = None
            source.save(update_fields=["expires_at"])
        schedule_dataset_preparation(change.id)
        return change

    @transaction.atomic
    def revise(
        self, identifier: UUID | str, specification: Mapping[str, object], *, expected_revision: int
    ) -> DatasetChange:
        """Invalidate previous preparation and queue the revised explicit mapping."""
        self.authorization.require_enabled()
        external_source = lock_change_source(identifier)
        change = self.get_change(identifier, lock=True)
        self.validate_external_change(change, external_source)
        require_revision(change.revision, expected_revision)
        self._require_mutable(change)
        normalized = self.validate_specification(specification)
        namespace = self.authorization.get_namespace(change.namespace_id)
        self.validate_targets(namespace, normalized, external_source=external_source)
        if change.intake_id:
            self.record_intake_source_use(change.intake_id)
        change.specification = normalized
        change.workbook_id = normalized.get("workbook_id")
        change.prepared = {}
        change.status = DatasetChangeStatus.QUEUED
        change.error_code = ""
        change.error_message = ""
        change.revision += 1
        change.started_at = None
        change.save()
        schedule_dataset_preparation(change.id)
        return change

    @staticmethod
    @transaction.atomic
    def record_intake_source_use(intake_id: UUID) -> StagedResourceIntake:
        """Renew source retention while holding the same lock used by cleanup admission.

        Args:
            intake_id: Server-resolved intake needed by an authorized proposal.

        Returns:
            The available intake with its persisted last-use timestamp refreshed.

        Raises:
            DatasetManagementError: If cleanup already claimed or removed the source.
            StagedResourceIntake.DoesNotExist: If the intake no longer exists.
        """
        intake = StagedResourceIntake.objects.select_for_update().get(pk=intake_id)
        if intake.source_deleted_at is not None or intake.source_cleanup_started_at is not None:
            raise DatasetManagementError(
                "source_deleted", "Source file deleted. Upload it again.", 410
            )
        intake.source_last_used_at = timezone.now()
        intake.save(update_fields=["source_last_used_at"])
        return intake

    @transaction.atomic
    def cancel(self, identifier: UUID | str, *, expected_revision: int) -> DatasetChange:
        """Cancel a proposal and fence off any worker still preparing its old revision."""
        self.authorization.require_enabled()
        external_source = lock_change_source(identifier)
        change = self.get_change(identifier, lock=True)
        self.validate_external_change(change, external_source)
        require_revision(change.revision, expected_revision)
        self._require_mutable(change)
        if external_source:
            external_source.revision += 1
            external_source.save(update_fields=["revision", "updated_at"])
        change.status = DatasetChangeStatus.CANCELLED
        change.revision += 1
        change.completed_at = timezone.now()
        change.save()
        return change

    def retry(self, identifier: UUID | str, *, expected_revision: int) -> DatasetChange:
        """Requeue a failed proposal after revalidating its targets and revisions."""
        change = self.get_change(identifier)
        if change.status != DatasetChangeStatus.FAILED:
            raise DatasetManagementError(
                "retry_not_available", "Only failed updates can be retried."
            )
        return self.revise(identifier, change.specification, expected_revision=expected_revision)

    def publish(self, identifier: UUID | str, *, expected_revision: int) -> DatasetChange:
        """Verify prepared storage outside locks, then publish the reviewed revision atomically."""
        self.authorization.require_enabled()
        change = self.get_change(identifier)
        completed_external = change.external_source_id and change.status in {
            "PUBLISHED",
            "NO_CHANGES",
        }
        if change.status != DatasetChangeStatus.PUBLISHED and not completed_external:
            self.verify_external_versions(change, change.prepared)
            require_revision(change.revision, expected_revision)
            for item in change.prepared.get("items", []):
                provider = DataRegistryStorageFactory().create_provider(item["provider"])
                try:
                    metadata = provider.head(item["uri"])
                    changed = self._has_candidate_changed(provider, metadata, item)
                except Exception as error:
                    # Storage adapters expose provider-specific failures at this application boundary.
                    self._record_publication_failure(change, "candidate_unavailable")
                    raise DatasetManagementError(
                        "candidate_unavailable",
                        "Prepared data is unavailable. Recalculate this update.",
                    ) from error
                if changed:
                    self._record_publication_failure(change, "candidate_changed")
                    raise DatasetManagementError(
                        "candidate_changed", "Prepared data changed. Recalculate this update."
                    )
        try:
            return self._publish_transaction(identifier, expected_revision=expected_revision)
        except DatasetManagementError:
            raise
        except Exception as error:
            # Record a failed atomic publication only after its transaction has rolled back.
            DatasetChange.objects.filter(
                pk=identifier, revision=expected_revision, status__in=["READY", "NEEDS_MANAGER"]
            ).update(
                status="FAILED",
                error_code="publication_failed",
                error_message="Publication failed. The current version was preserved; retry this update.",
                updated_at=timezone.now(),
            )
            raise DatasetManagementError(
                "publication_failed",
                "Publication failed. The current version was preserved; retry this update.",
                503,
            ) from error

    @staticmethod
    def _has_candidate_changed(
        provider: StorageProvider, metadata: StorageObjectMetadata, item: JsonObject
    ) -> bool:
        """Compare storage identity without confusing provider MD5 with canonical SHA-256.

        Newly prepared remote objects retain the ETag returned after uploading their
        canonical bytes. Local metadata already carries SHA-256. Older remote proposals
        without an ETag require a streamed download and SHA-256 verification outside
        publication locks; an absent provider checksum never bypasses validation.
        """
        expected_size = int(item["size_bytes"])
        expected_checksum = str(item["checksum"])
        if metadata.size_bytes != expected_size:
            return True
        if expected_etag := item.get("storage_etag"):
            return metadata.etag != str(expected_etag)
        if metadata.provider == "local":
            return metadata.checksum != expected_checksum
        with TemporaryDirectory(prefix="dataset-candidate-verification-") as directory:
            path = Path(directory) / "candidate.parquet"
            provider.download_to_file(item["uri"], str(path))
            with path.open("rb") as stream:
                checksum = hashlib.file_digest(stream, "sha256").hexdigest()
            return path.stat().st_size != expected_size or checksum != expected_checksum

    @staticmethod
    def _record_publication_failure(change: DatasetChange, code: str) -> None:
        DatasetChange.objects.filter(
            pk=change.pk, revision=change.revision, status__in=["READY", "NEEDS_MANAGER"]
        ).update(
            status="FAILED",
            error_code=code,
            error_message="Prepared data is unavailable or changed. Retry this update.",
            updated_at=timezone.now(),
        )

    @transaction.atomic
    def _publish_transaction(
        self, identifier: UUID | str, *, expected_revision: int
    ) -> DatasetChange:
        """Serialize source operations before proposal/publication/dataset locks."""
        external_source = lock_change_source(identifier)
        with source_publication(external_source.id if external_source else None):
            return self._publish_locked(
                identifier, expected_revision=expected_revision, external_source=external_source
            )

    def _publish_locked(
        self,
        identifier: UUID | str,
        *,
        expected_revision: int,
        external_source: DatasetExternalSource | None = None,
        no_changes: bool = False,
    ) -> DatasetChange:
        """Atomically publish all selected prepared sheets and their current versions."""
        self.authorization = DatasetManagementAuthorization(self.user, self.organization)
        self.authorization.require_enabled()
        change = self.get_change(identifier, lock=True)
        namespace = self.authorization.get_namespace(change.namespace_id)
        if change.external_source_id and change.status in {"PUBLISHED", "NO_CHANGES"}:
            self.validate_external_replay(change)
            return change
        self.validate_external_change(change, external_source, allow_manager=True)
        if change.status == DatasetChangeStatus.PUBLISHED:
            self.validate_targets(namespace, change.specification, check_revision=False)
            reconcile_dataset_publication(change)
            if change.intake_id:
                StagedResourceIntakeService.synchronize_dataset_publication(
                    intake_id=change.intake_id
                )
            return change
        require_revision(change.revision, expected_revision)
        if change.status not in {DatasetChangeStatus.READY, DatasetChangeStatus.NEEDS_MANAGER}:
            raise DatasetManagementError("update_not_ready", "The update is not ready to publish.")
        if (
            change.status == DatasetChangeStatus.NEEDS_MANAGER
            and not self.authorization.can_manage_namespace(namespace)
        ):
            raise DatasetManagementError(
                "manager_required", "A destination manager must publish these changes.", 403
            )
        # Destination removal locks publications before datasets; keep the same order here.
        publication = reserve_dataset_publication(change, self.user)
        self.validate_targets(
            namespace, change.specification, lock=True, external_source=external_source
        )
        prepared = change.prepared
        items = prepared.get("items", [])
        if not items:
            raise DatasetManagementError(
                "sheets_required", "Select at least one valid sheet before publishing.", 400
            )
        self._require_matching_prepared_targets(change, namespace)
        if not external_source and any(item.get("historical_version_id") for item in items):
            raise DatasetManagementError(
                "historical_content",
                "Restore the indicated historical version or revise the selection.",
            )
        workbook = change.workbook
        if prepared.get("is_workbook") and workbook is None:
            workbook = DatasetWorkbook.objects.create(
                namespace=namespace, name=change.specification.get("name") or prepared["filename"]
            )
            change.workbook = workbook
        if external_source and any(item.get("historical_version_id") for item in items):
            for item in items:
                if item.get("historical_version_id"):
                    target = Dataset.objects.get(pk=item["dataset_id"])
                    DatasetLifecycleService._require_usable_version(
                        target, item["historical_version_id"]
                    )
        output_ids: list[str] = []
        result_items: list[JsonObject] = []
        output_versions: list[dict[str, str]] = []
        registry = DatasetRegistryService()
        group = None
        if workbook:
            group_service = WorkbookIngestionGroupService()
            group = group_service.get_or_create_group(
                tenant_id=str(namespace.id),
                source_uri=prepared["source_uri"],
                source_hash=prepared["source_hash"],
                source_file_name=prepared["filename"],
                source_storage_provider=items[0]["provider"],
                metadata_json={"change_id": str(change.id), "workbook_id": str(workbook.id)},
            )
            group_service.mark_completed(
                group=group,
                total_sheets=len(prepared["available_sheets"]),
                total_created=sum(not item.get("unchanged") for item in items),
                total_deduplicated=sum(bool(item.get("unchanged")) for item in items),
                total_skipped=len(prepared["available_sheets"]) - len(items),
            )
        for item in items:
            was_created = not item.get("dataset_id")
            dataset = self._publish_item(change, namespace, workbook, registry, item, group)
            output_ids.append(str(dataset.id))
            result_items.append(
                {
                    "dataset_id": str(dataset.id),
                    "dataset_version_id": str(dataset.current_version_id),
                    "dataset_revision": dataset.revision,
                    "sheet_name": dataset.workbook_sheet or None,
                    "outcome": (
                        "created"
                        if was_created
                        else (
                            "restored"
                            if item.get("historical_version_id")
                            else "unchanged" if item.get("unchanged") else "updated"
                        )
                    ),
                }
            )
            output_versions.append(
                {
                    "dataset_id": str(dataset.id),
                    "dataset_version_id": str(dataset.current_version_id),
                }
            )
        for entry in change.specification.get("archive", []):
            dataset = Dataset.objects.get(pk=entry["dataset_id"])
            dataset.status = "ARCHIVED"
            dataset.archived_at = timezone.now()
            dataset.archive_reason = "SOURCE_REMOVED" if external_source else "MANUAL"
            dataset.revision += 1
            dataset.save()
            record_dataset_activity(
                dataset, self.user, "ARCHIVED", "Worksheet removed from workbook"
            )
            result_items.append(
                {
                    "dataset_id": str(dataset.id),
                    "dataset_version_id": str(dataset.current_version_id),
                    "dataset_revision": dataset.revision,
                    "sheet_name": dataset.workbook_sheet or None,
                    "outcome": "archived",
                }
            )
        if workbook:
            workbook.revision += 1
            workbook.save(update_fields=["revision"])
        change.result = {
            "dataset_ids": output_ids,
            "workbook_id": str(workbook.id) if workbook else None,
        }
        change.result.update(
            items=result_items, workbook_revision=workbook.revision if workbook else None
        )
        if external_source:
            external_source.workbook = workbook
            external_source.status = "ACTIVE"
            external_source.last_applied_version = change.source_reference["version"]
            external_source.revision += 1
            external_source.save(
                update_fields=[
                    "workbook",
                    "status",
                    "last_applied_version",
                    "revision",
                    "updated_at",
                ]
            )
            change.result.update(
                source_id=str(external_source.id),
                source_revision=external_source.revision,
                source_reference=change.source_reference,
            )
        change.status = (
            DatasetChangeStatus.NO_CHANGES if no_changes else DatasetChangeStatus.PUBLISHED
        )
        change.revision += 1
        change.completed_at = timezone.now()
        record_dataset_publication(change, publication, output_versions)
        change.save()
        if change.intake_id:
            StagedResourceIntakeService.synchronize_dataset_publication(intake_id=change.intake_id)
        return change

    def _require_matching_prepared_targets(
        self, change: DatasetChange, namespace: RegistryNamespace
    ) -> None:
        """Bind effective publication operations to the reviewed, authorized selection."""
        source = change.intake if change.intake_id else change.upload
        if source is None:
            raise DatasetManagementError("source_unavailable", "Verified source not found.", 404)
        self.validate_source_mapping(change.specification, file_format=source.resolved_file_format)
        is_workbook = source.resolved_file_format.lower() == "xlsx"
        expected = (
            [
                entry
                for entry in change.specification.get("sheets", [])
                if entry.get("selected", True)
            ]
            if is_workbook
            else [{"dataset_id": change.specification.get("dataset_id")}]
        )
        items = change.prepared.get("items", [])
        expected_targets = sorted(
            (str(entry.get("sheet_name") or ""), str(entry.get("dataset_id") or ""))
            for entry in expected
        )
        prepared_targets = sorted(
            (str(item.get("sheet_name") or ""), str(item.get("dataset_id") or "")) for item in items
        )
        if (
            change.prepared.get("is_workbook") != is_workbook
            or prepared_targets != expected_targets
        ):
            raise DatasetManagementError(
                "prepared_target_mismatch",
                "Prepared operations differ from the reviewed targets. Recalculate this update.",
            )
        if any(not item.get("dataset_id") for item in items):
            self.authorization.require_create(namespace)

    def _publish_item(
        self,
        change: DatasetChange,
        namespace: RegistryNamespace,
        workbook: DatasetWorkbook | None,
        registry: DatasetRegistryService,
        item: JsonObject,
        group: WorkbookIngestionGroup | None,
    ) -> Dataset:
        dataset = (
            Dataset.objects.get(pk=item["dataset_id"])
            if item.get("dataset_id")
            else Dataset.objects.create(
                tenant_id=namespace.id,
                organization=self.organization,
                workspace_id=namespace.workspace_id,
                visibility_scope="WORKSPACE" if namespace.workspace_id else "ORGANIZATION",
                slug=f"dataset-{uuid4().hex}",
                name=item["name"],
                source_type="FILE_UPLOAD",
                created_by=change.submitted_by,
                responsible_user=change.submitted_by,
                external_source_id=change.external_source_id,
            )
        )
        if not item.get("dataset_id"):
            assign_perm("resource_registry.change_dataset", change.submitted_by, dataset)
        if change.external_source_id and item.get("historical_version_id"):
            dataset.current_version_id = item["historical_version_id"]
        elif not item.get("unchanged"):
            number = (dataset.versions.aggregate(value=Max("version_number"))["value"] or 0) + 1
            version = registry.create_version_bundle_from_context(
                dataset=dataset,
                version_number=number,
                content_hash=item["content_hash"],
                context=item["context"],
            ).dataset_version
            registry.attach_artifact(
                dataset_version=version,
                artifact_role="PRIMARY",
                uri=item["uri"],
                format="PARQUET",
                size_bytes=item["size_bytes"],
                checksum=item["checksum"],
                storage_provider=item["provider"],
                sheet_name=item.get("sheet_name"),
                workbook_group=group,
                metadata_json={
                    "is_canonical": True,
                    "change_id": str(change.id),
                    "source_uri": item["context"]["source"]["path"],
                },
            )
            dataset.current_version = version
        if workbook:
            dataset.workbook = workbook
            dataset.workbook_group = group
            dataset.workbook_sheet = item.get("sheet_name") or ""
        dataset.status = "ACTIVE"
        if dataset.archive_reason == "SOURCE_REMOVED" and change.external_source_id:
            dataset.archived_at = None
            dataset.archive_reason = ""
        dataset.revision += 1
        dataset.save()

        register_dataset_objects(dataset)
        record_dataset_activity(
            dataset,
            self.user,
            "WORKSHEET_MAPPING_UPDATED" if item.get("unchanged") else "VERSION_PUBLISHED",
        )
        return dataset

    def validate_targets(
        self,
        namespace: RegistryNamespace,
        specification: JsonObject,
        *,
        lock: bool = False,
        check_revision: bool = True,
        external_source: DatasetExternalSource | None = None,
    ) -> None:
        """Require explicit editable targets and protect whole-workbook publication."""
        workbook_id = specification.get("workbook_id")
        if workbook_id:
            workbooks = DatasetWorkbook.objects.filter(namespace=namespace)
            if lock:
                workbooks = workbooks.select_for_update()
            workbook = workbooks.filter(pk=workbook_id).first()
            if workbook is None:
                raise DatasetManagementError("not_found", "Workbook not found.", 404)
            if check_revision:
                require_revision(workbook.revision, specification.get("workbook_revision", -1))
        if external_source:
            if external_source.workbook_id and str(external_source.workbook_id) != workbook_id:
                raise DatasetManagementError(
                    "source_identity_conflict", "Use the source's complete workbook."
                )
            if workbook_id and external_source.workbook_id is None:
                raise DatasetManagementError(
                    "source_identity_conflict", "Existing workbooks cannot be adopted."
                )
            if (
                external_source.datasets.exists()
                and not external_source.workbook_id
                and not specification.get("dataset_id")
            ):
                raise DatasetManagementError(
                    "source_identity_conflict", "Select the source's existing dataset."
                )
        elif workbook_id and DatasetExternalSource.objects.filter(workbook_id=workbook_id).exists():
            raise DatasetManagementError(
                "source_managed_dataset", "Content is managed by its external source."
            )
        targets = self.target_entries(specification)
        if (not targets and not workbook_id) or any(
            not entry.get("dataset_id") for entry in targets
        ):
            self.authorization.require_create(namespace)
        if not targets and workbook_id and not self.authorization.can_manage_namespace(namespace):
            if not any(
                self.user.has_perm("resource_registry.change_dataset", dataset)
                for dataset in Dataset.objects.filter(workbook_id=workbook_id)
            ):
                raise DatasetManagementError(
                    "edit_denied", "No assigned datasets in this workbook.", 403
                )
        identifiers = sorted({entry["dataset_id"] for entry in targets if entry.get("dataset_id")})
        if workbook_id:
            selected_names = [
                entry["sheet_name"]
                for entry in specification.get("sheets", [])
                if entry.get("selected", True)
            ]
            if (
                Dataset.objects.filter(workbook_id=workbook_id, workbook_sheet__in=selected_names)
                .exclude(pk__in=identifiers)
                .exists()
            ):
                raise DatasetManagementError(
                    "sheet_mapping_conflict",
                    "A worksheet is already linked to another dataset. Review its mapping.",
                )
        queryset = Dataset.objects.filter(
            tenant_id=namespace.id, organization=self.organization, id__in=identifiers
        )
        if lock:
            queryset = queryset.select_for_update()
        datasets = {str(dataset.id): dataset for dataset in queryset.order_by("id")}
        for entry in targets:
            if not entry.get("dataset_id"):
                continue
            dataset = datasets.get(entry["dataset_id"])
            if dataset is None:
                raise DatasetManagementError("not_found", "Dataset not found.", 404)
            self.authorization.require_edit(dataset)
            if dataset.external_source_id and external_source is None:
                raise DatasetManagementError(
                    "source_managed_dataset", "Content is managed by its external source."
                )
            if external_source and dataset.external_source_id != external_source.id:
                raise DatasetManagementError(
                    "source_identity_conflict",
                    "The target belongs to another source or is manually managed.",
                )
            if dataset.purge_started_at:
                raise DatasetManagementError(
                    "dataset_purging", "Dataset data is being deleted.", 410
                )
            recoverable = external_source is not None and dataset.archive_reason == "SOURCE_REMOVED"
            if dataset.source_type != "FILE_UPLOAD" or (dataset.archived_at and not recoverable):
                raise DatasetManagementError(
                    "target_not_editable", "Select an active file-upload dataset."
                )
            if check_revision and dataset.workbook_id and not workbook_id:
                raise DatasetManagementError(
                    "workbook_required", "Update this dataset through its complete workbook."
                )
            if workbook_id and str(dataset.workbook_id) != workbook_id:
                raise DatasetManagementError(
                    "workbook_mismatch", "The selected dataset belongs to another workbook."
                )
            if check_revision:
                require_revision(dataset.revision, entry.get("expected_revision", -1))
        if (
            specification.get("archive")
            and external_source is None
            and not self.authorization.can_manage_namespace(namespace)
        ):
            raise DatasetManagementError(
                "manager_required", "A manager must archive removed worksheets.", 403
            )

    @staticmethod
    def target_entries(specification: JsonObject) -> list[JsonObject]:
        """Return selected publication and archival targets from a validated specification."""
        entries = [item for item in specification.get("sheets", []) if item.get("selected", True)]
        if specification.get("dataset_id"):
            entries.append(
                {
                    "dataset_id": specification["dataset_id"],
                    "expected_revision": specification.get("expected_revision", -1),
                }
            )
        return [*entries, *specification.get("archive", [])]

    @staticmethod
    def validate_source_mapping(specification: Mapping[str, object], *, file_format: str) -> None:
        """Reject worksheet operations for a non-workbook verified source.

        Args:
            specification: Explicit targets reviewed by the actor.
            file_format: File format established by the server's upload inspection.

        Raises:
            DatasetManagementError: If the source cannot represent the requested operations.
        """
        if file_format.lower() != "xlsx" and (
            specification.get("sheets")
            or specification.get("workbook_id")
            or specification.get("archive")
        ):
            raise DatasetManagementError(
                "invalid_source_mapping",
                "Worksheet mappings require a complete Excel workbook.",
                400,
            )

    @staticmethod
    def validate_specification(specification: Mapping[str, object]) -> JsonObject:
        """Validate a bounded JSON mapping without accepting storage coordinates."""
        allowed = {
            "name",
            "dataset_id",
            "expected_revision",
            "workbook_id",
            "workbook_revision",
            "sheets",
            "archive",
        }
        if set(specification) - allowed:
            raise DatasetManagementError("invalid_specification", "Unknown update field.", 400)
        result: JsonObject = dict(specification)
        if result.get("name") is not None and (
            not isinstance(result["name"], str) or len(result["name"]) > 255
        ):
            raise DatasetManagementError(
                "invalid_name", "Provide a name up to 255 characters.", 400
            )
        for key in ("expected_revision", "workbook_revision"):
            if key in result and (type(result[key]) is not int or result[key] < 0):
                raise DatasetManagementError(
                    "invalid_revision", "Provide a nonnegative integer revision.", 400
                )
        if result.get("dataset_id") and result.get("workbook_id"):
            raise DatasetManagementError(
                "invalid_target", "Select a dataset or a workbook, not both.", 400
            )
        if result.get("dataset_id") and result.get("sheets"):
            raise DatasetManagementError(
                "invalid_target", "Select a dataset target or worksheet mappings, not both.", 400
            )
        for key in ("dataset_id", "workbook_id"):
            if result.get(key):
                try:
                    result[key] = str(UUID(str(result[key])))
                except ValueError as error:
                    raise DatasetManagementError(
                        "invalid_target", "Invalid target identifier.", 400
                    ) from error
        for key in ("sheets", "archive"):
            entries = result.get(key, [])
            if not isinstance(entries, list) or len(entries) > 500:
                raise DatasetManagementError(
                    "invalid_sheets", "Provide a bounded sheet selection.", 400
                )
            for entry in entries:
                if not isinstance(entry, dict) or set(entry) - {
                    "sheet_name",
                    "dataset_id",
                    "expected_revision",
                    "selected",
                    "name",
                }:
                    raise DatasetManagementError("invalid_sheet", "Invalid sheet mapping.", 400)
                if key == "sheets" and (
                    not isinstance(entry.get("sheet_name"), str)
                    or not entry["sheet_name"]
                    or len(entry["sheet_name"]) > 255
                ):
                    raise DatasetManagementError("invalid_sheet", "Select a named worksheet.", 400)
                if "selected" in entry and type(entry["selected"]) is not bool:
                    raise DatasetManagementError(
                        "invalid_sheet", "Worksheet selection must be boolean.", 400
                    )
                if "expected_revision" in entry and (
                    type(entry["expected_revision"]) is not int or entry["expected_revision"] < 0
                ):
                    raise DatasetManagementError(
                        "invalid_revision", "Provide a nonnegative integer revision.", 400
                    )
                if entry.get("name") and (
                    not isinstance(entry["name"], str) or len(entry["name"]) > 255
                ):
                    raise DatasetManagementError(
                        "invalid_name", "Provide a name up to 255 characters.", 400
                    )
                if key == "archive" and (
                    not result.get("workbook_id") or not entry.get("dataset_id")
                ):
                    raise DatasetManagementError(
                        "invalid_archive", "Select an existing workbook dataset to archive.", 400
                    )
                if entry.get("dataset_id"):
                    try:
                        entry["dataset_id"] = str(UUID(str(entry["dataset_id"])))
                    except ValueError as error:
                        raise DatasetManagementError(
                            "invalid_target", "Invalid dataset identifier.", 400
                        ) from error
            result[key] = entries
        sheet_names = [entry["sheet_name"] for entry in result["sheets"]]
        if len(sheet_names) != len(set(sheet_names)):
            raise DatasetManagementError(
                "duplicate_sheet", "A worksheet can appear only once.", 400
            )
        entries = DatasetChangeService.target_entries(result)
        identifiers = [entry["dataset_id"] for entry in entries if entry.get("dataset_id")]
        if len(identifiers) != len(set(identifiers)):
            raise DatasetManagementError(
                "duplicate_target", "A dataset can appear only once in an update.", 400
            )
        return result

    @staticmethod
    def _require_mutable(change: DatasetChange) -> None:
        if change.status in {
            DatasetChangeStatus.PUBLISHED,
            DatasetChangeStatus.CANCELLED,
            DatasetChangeStatus.NO_CHANGES,
        }:
            raise DatasetManagementError("update_terminal", "This update is already complete.")

    def validate_external_change(
        self,
        change: DatasetChange,
        source: DatasetExternalSource | None,
        *,
        allow_manager: bool = False,
    ) -> None:
        """Revalidate source revision, live admission and the sole pending proposal.

        Args:
            change: Proposal whose immutable source is being checked.
            source: Current source, locked for state-changing operations.
            allow_manager: Admit a manager approving a prepared proposal.

        Raises:
            DatasetManagementError: If admission, identity or concurrency checks fail.
        """
        if change.external_source_id is None:
            return
        if source is None or source.id != change.external_source_id:
            raise DatasetManagementError("source_identity_conflict", "Source identity changed.")
        namespace = self.authorization.get_namespace(change.namespace_id)
        require_source_authority(self.authorization, namespace, allow_manager=allow_manager)
        if change.expected_source_revision is None:
            raise DatasetManagementError(
                "invalid_source_reference", "The proposal has no source revision."
            )
        require_revision(source.revision, change.expected_source_revision)
        if source.changes.filter(status__in=PENDING_STATES).exclude(pk=change.pk).exists():
            raise DatasetManagementError(
                "source_change_pending", "Resolve the existing source proposal first."
            )

    def validate_external_replay(self, change: DatasetChange) -> None:
        """Authorize immutable completed evidence without repeating lifecycle effects.

        Args:
            change: Previously accepted external proposal.

        Raises:
            DatasetManagementError: If current source or dataset authority has been revoked.
        """
        namespace = self.authorization.get_namespace(change.namespace_id)
        require_source_authority(self.authorization, namespace, allow_manager=True)
        for dataset in Dataset.objects.filter(external_source_id=change.external_source_id):
            self.authorization.require_edit(dataset)

    @transaction.atomic
    def complete_external_preparation(
        self, identifier: UUID, *, expected_revision: int, prepared: JsonObject, status: str
    ) -> None:
        """Fence prepared results and atomically commit verified external no-ops.

        Args:
            identifier: Prepared proposal UUID.
            expected_revision: Proposal revision claimed by the worker.
            prepared: Private, verified candidate evidence.
            status: Preparation outcome determined by schema/content comparison.

        Raises:
            DatasetManagementError: If live admission, revisions or publication fail.

        Side Effects:
            Persist prepared results, or atomically record a verified source no-op.
            Ignore completion from a cancelled or superseded worker.
        """
        source = lock_change_source(identifier)
        change = self.get_change(identifier, lock=True)
        if change.status != "PROCESSING" or change.revision != expected_revision:
            return
        self.validate_external_change(change, source)
        namespace = self.authorization.get_namespace(change.namespace_id)
        self.validate_targets(namespace, change.specification, external_source=source)
        change.prepared = prepared
        change.status = "READY" if status == "NO_CHANGES" else status
        change.error_code = ""
        change.error_message = ""
        change.save(
            update_fields=["prepared", "status", "error_code", "error_message", "updated_at"]
        )
        if status == "NO_CHANGES":
            with source_publication(source.id if source else None):
                self._publish_locked(
                    identifier,
                    expected_revision=expected_revision,
                    external_source=source,
                    no_changes=True,
                )

    def verify_external_versions(self, change: DatasetChange, prepared: JsonObject) -> None:
        """Check retained artifacts before reusing versions, outside write locks.

        Args:
            change: External proposal admitted for preparation or publication.
            prepared: Candidate comparison evidence identifying reused versions.

        Raises:
            DatasetManagementError: If a retained version has no physically usable artifact.
        """
        if not change.external_source_id:
            return
        lifecycle = DatasetLifecycleService(self.user, self.organization)
        for item in prepared.get("items", []):
            if item.get("historical_version_id") or item.get("unchanged"):
                dataset = Dataset.objects.get(pk=item["dataset_id"])
                lifecycle._verify_version_storage(
                    dataset, item.get("historical_version_id") or dataset.current_version_id
                )
