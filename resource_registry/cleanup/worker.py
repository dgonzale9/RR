"""Execute durable cleanup with short transactions and restart-safe per-object results."""

from __future__ import annotations

import hashlib
from datetime import timedelta
from tempfile import NamedTemporaryFile
from uuid import UUID, uuid4

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from resource_registry.cleanup.inventory import (
    dataset_inventory_fingerprint,
    location_fingerprint,
    register_dataset_objects,
    release_dataset_references,
)
from resource_registry.cleanup.protection import get_dataset_blocker, get_object_blocker
from resource_registry.cleanup.redaction import detach_dataset_publications, redact_dataset_content
from resource_registry.cleanup.storage import deletion_authority
from resource_registry.cleanup.sweeper import prepare_automatic_request
from resource_registry.management_domain.authorization import DatasetManagementAuthorization
from resource_registry.management_domain.errors import DatasetManagementError
from resource_registry.management_domain.lifecycle import record_dataset_activity
from resource_registry.models import (
    Dataset,
    DatasetArtifact,
    DatasetChange,
    DatasetManagementConfiguration,
    ResourceCleanupItem,
    ResourceCleanupRequest,
    ResourceStorageObject,
    ResourceStorageReference,
    StagedResourceIntake,
    StagedResourcePublication,
)
from resource_registry.storage.exceptions import StorageObjectNotFoundError
from resource_registry.storage.factory import DataRegistryStorageFactory


class CleanupWorker:
    """Delete only reviewed, unprotected managed objects; preserve durable partial progress."""

    def run_once(self, *, batch_size: int = 100) -> int:
        """Process at most batch_size objects, skipping simulations and active worker leases."""
        now = timezone.now()
        identifiers = list(
            ResourceCleanupRequest.objects.filter(
                status__in=["SCHEDULED", "BLOCKED", "RUNNING", "PARTIAL"],
                execute_after__lte=now,
                next_attempt_at__lte=now,
            )
            .filter(Q(lease_expires_at__isnull=True) | Q(lease_expires_at__lte=now))
            .order_by("next_attempt_at", "created_at")
            .values_list("id", flat=True)[:batch_size]
        )
        processed = 0
        for identifier in identifiers:
            if processed >= batch_size:
                break
            pending = ResourceCleanupRequest.objects.select_related(
                "intake", "namespace__workspace"
            ).get(pk=identifier)
            if not self._is_execution_enabled(pending):
                ResourceCleanupRequest.objects.filter(
                    pk=identifier, started_at__isnull=True
                ).update(next_attempt_at=now + timedelta(minutes=15))
                continue
            if pending.intake_id:
                blocker = self._check_published_copies(pending)
                if blocker:
                    with transaction.atomic():
                        pending = ResourceCleanupRequest.objects.select_for_update().get(
                            pk=identifier
                        )
                        if pending.started_at is None and pending.status in {
                            "SCHEDULED",
                            "BLOCKED",
                        }:
                            self._block_request(pending, blocker)
                    continue
            token = self._claim_request(identifier)
            if token is None:
                continue
            items = list(
                ResourceCleanupItem.objects.filter(request_id=identifier)
                .exclude(status="DELETED")
                .order_by("id")
                .values_list("id", flat=True)[: max(0, batch_size - processed)]
            )
            for item_id in items:
                if not self._can_continue(identifier, token):
                    break
                self._delete_item(item_id, token)
                processed += 1
            self._finish_request(identifier, token)
        return processed

    @staticmethod
    def _check_published_copies(request: ResourceCleanupRequest) -> str:
        """Verify independent published bytes outside database transactions before source cleanup."""
        intake = request.intake
        if intake is None or intake.status != "PUBLISHED":
            return ""
        source = (
            DataRegistryStorageFactory()
            .create_provider(intake.storage_provider)
            .get_uri(intake.storage_object_key)
        )
        assignments = (
            intake.publications.filter(dataset_assignments__dataset__purge_started_at__isnull=True)
            .values_list("dataset_assignments__dataset_version_id", flat=True)
            .distinct()
        )
        for version_id in assignments:
            if version_id is None:
                return "published_copy_unavailable"
            verified = False
            for artifact in DatasetArtifact.objects.filter(
                dataset_version_id=version_id, lifecycle_status="ACTIVE"
            ).exclude(uri=source):
                try:
                    provider = DataRegistryStorageFactory().create_provider(
                        artifact.storage_provider
                    )
                    metadata = provider.head(artifact.uri)
                    if metadata.size_bytes != artifact.size_bytes:
                        continue
                    if artifact.checksum and metadata.checksum != artifact.checksum:
                        # Azure may expose binary MD5 while the artifact records SHA-256.
                        with NamedTemporaryFile() as temporary:
                            provider.download_to_file(artifact.uri, temporary.name)
                            with open(temporary.name, "rb") as stream:
                                checksum = hashlib.file_digest(stream, "sha256").hexdigest()
                        if checksum != artifact.checksum:
                            continue
                    verified = True
                    break
                except Exception:
                    continue
            if not verified:
                return "published_copy_unavailable"
        return ""

    @staticmethod
    def _is_execution_enabled(request: ResourceCleanupRequest) -> bool:
        if not getattr(settings, "RESOURCE_REGISTRY_CLEANUP_COORDINATOR_ENABLED", True):
            return False
        if request.namespace_id is None:
            return (
                getattr(settings, "RESOURCE_REGISTRY_PRIVATE_CLEANUP_MODE", "SIMULATE") == "EXECUTE"
            )
        namespace = request.namespace
        if namespace is None:
            return False
        organization_id = namespace.organization_id or (
            namespace.workspace.organization_id if namespace.workspace else None
        )
        if organization_id is None:
            return False
        return DatasetManagementConfiguration.objects.filter(
            organization_id=organization_id, enabled=True, cleanup_mode="EXECUTE"
        ).exists()

    @transaction.atomic
    def _claim_request(self, identifier: UUID) -> UUID | None:
        request = ResourceCleanupRequest.objects.select_for_update().get(pk=identifier)
        now = timezone.now()
        if (
            request.status not in {"SCHEDULED", "BLOCKED", "RUNNING", "PARTIAL"}
            or (request.lease_expires_at and request.lease_expires_at > now)
            or not self._is_execution_enabled(request)
        ):
            return None
        if request.dataset_id:
            dataset = Dataset.objects.select_for_update().get(pk=request.dataset_id)
            if request.started_at is None:
                register_dataset_objects(dataset)
                try:
                    if request.requested_by is None or not request.requested_by.is_active:
                        raise DatasetManagementError(
                            "authority_revoked", "A manager must review this deletion."
                        )
                    DatasetManagementAuthorization(
                        request.requested_by, dataset.organization
                    ).require_manage(dataset)
                except DatasetManagementError:
                    self._block_request(request, "authority_revoked")
                    return None
                if (
                    dataset.revision != request.expected_revision
                    or dataset_inventory_fingerprint(dataset) != request.inventory_fingerprint
                ):
                    request.status = "NEEDS_REVIEW"
                    request.error_code = "inventory_changed"
                    request.save(update_fields=["status", "error_code", "updated_at"])
                    return None
                blocker = get_dataset_blocker(dataset)
                if blocker:
                    self._block_request(request, blocker)
                    return None
                configuration = DatasetManagementConfiguration.objects.get(
                    organization=dataset.organization
                )
                if (
                    dataset.archived_at is None
                    or dataset.archived_at
                    + timedelta(days=max(30, configuration.dataset_recovery_days))
                    > now
                ):
                    self._block_request(request, "recovery_period")
                    return None
                dataset.purge_started_at = now
                dataset.revision += 1
                dataset.save(update_fields=["purge_started_at", "revision", "updated_at"])
                detach_dataset_publications(
                    dataset, actor_id=request.requested_by_id, reason=request.reason
                )
                release_dataset_references(dataset)
                redact_dataset_content(dataset)
                record_dataset_activity(
                    dataset, request.requested_by, "PURGE_STARTED", request.reason
                )
            else:
                release_dataset_references(dataset)
        else:
            if request.kind == "INVENTORY":
                self._block_request(request, "ownership_unverified")
                return None

            blocker = prepare_automatic_request(request)
            if blocker:
                self._block_request(request, blocker)
                return None
        request.started_at = request.started_at or now
        request.status = "RUNNING"
        request.lease_token = uuid4()
        request.lease_expires_at = now + timedelta(minutes=5)
        request.error_code = ""
        request.save()
        return request.lease_token

    @staticmethod
    def _block_request(request: ResourceCleanupRequest, blocker: str) -> None:
        request.status = "BLOCKED"
        request.error_code = blocker
        request.next_attempt_at = timezone.now() + timedelta(minutes=15)
        request.save(update_fields=["status", "error_code", "next_attempt_at", "updated_at"])

    def _can_continue(self, identifier: UUID, token: UUID) -> bool:
        request = ResourceCleanupRequest.objects.get(pk=identifier)
        if request.lease_token != token or not self._is_execution_enabled(request):
            return False
        ResourceCleanupRequest.objects.filter(pk=identifier, lease_token=token).update(
            lease_expires_at=timezone.now() + timedelta(minutes=5)
        )
        return True

    def _delete_item(self, identifier: int, token: UUID) -> None:
        with transaction.atomic():
            item = (
                ResourceCleanupItem.objects.select_for_update()
                .select_related("request")
                .get(pk=identifier)
            )
            if item.request.lease_token != token or item.status == "DELETED":
                return
            storage_object = ResourceStorageObject.objects.select_for_update().get(
                pk=item.storage_object_id
            )
            if storage_object.state == "DELETED":
                item.status = "DELETED"
                item.deleted_at = storage_object.deleted_at
                item.save(update_fields=["status", "deleted_at"])
                return
            if (
                storage_object.deletion_token
                and storage_object.deletion_token != token
                and storage_object.deletion_lease_expires_at
                and storage_object.deletion_lease_expires_at > timezone.now()
            ):
                item.status = "BLOCKED"
                item.block_reason = "cleanup_in_progress"
                item.save(update_fields=["status", "block_reason"])
                return
            blocker = get_object_blocker(storage_object)
            if blocker:
                item.status = "BLOCKED"
                item.block_reason = blocker
                item.save(update_fields=["status", "block_reason"])
                return
            if item.attempts >= 5:
                item.status = "FAILED"
                item.save(update_fields=["status"])
                return
            storage_object.state = "DELETING"
            storage_object.deletion_token = token
            storage_object.deletion_lease_expires_at = timezone.now() + timedelta(minutes=15)
            storage_object.save(
                update_fields=["state", "deletion_token", "deletion_lease_expires_at"]
            )
            item.attempts += 1
            item.save(update_fields=["attempts"])
        try:
            self._delete_physical_object(storage_object)
        except Exception as error:
            code = (
                error.code if isinstance(error, DatasetManagementError) else "storage_delete_failed"
            )
            ResourceCleanupItem.objects.filter(pk=identifier).update(
                status="FAILED", block_reason=code
            )
            ResourceStorageObject.objects.filter(pk=storage_object.pk, deletion_token=token).update(
                deletion_token=None, deletion_lease_expires_at=None
            )
            return
        with transaction.atomic():
            updated = ResourceStorageObject.objects.filter(
                pk=storage_object.pk, deletion_token=token
            ).update(
                state="DELETED",
                deleted_at=timezone.now(),
                deletion_token=None,
                deletion_lease_expires_at=None,
            )
            if not updated:
                return
            ResourceCleanupItem.objects.filter(pk=identifier).update(
                status="DELETED", deleted_at=timezone.now(), block_reason=""
            )
            DatasetArtifact.objects.filter(
                uri=storage_object.uri, storage_provider=storage_object.provider
            ).update(lifecycle_status="PHYSICAL_DELETED")

    @staticmethod
    def _delete_physical_object(storage_object: ResourceStorageObject) -> None:
        if storage_object.location_fingerprint != location_fingerprint(storage_object.provider):
            raise DatasetManagementError(
                "storage_location_changed",
                "The configured storage location changed; review is required.",
            )
        provider = DataRegistryStorageFactory().create_provider(storage_object.provider)
        try:
            metadata = provider.head(storage_object.uri)
        except StorageObjectNotFoundError:
            return
        if storage_object.etag and metadata.etag != storage_object.etag:
            raise DatasetManagementError("object_changed", "Stored object identity changed.")
        if storage_object.checksum:
            if storage_object.provider in {"local", "dataset_cache"}:
                checksum = metadata.checksum
            elif not storage_object.etag:
                with NamedTemporaryFile() as temporary:
                    with deletion_authority(storage_object.id):
                        provider.download_to_file(storage_object.uri, temporary.name)
                    digest = hashlib.sha256()
                    with open(temporary.name, "rb") as source:
                        for chunk in iter(lambda: source.read(1024 * 1024), b""):
                            digest.update(chunk)
                    checksum = digest.hexdigest()
            else:
                checksum = storage_object.checksum
            if checksum != storage_object.checksum:
                raise DatasetManagementError("object_changed", "Stored object identity changed.")
        elif not storage_object.etag:
            if storage_object.references.exclude(
                owner_kind__in=["CHANGE", "INTAKE", "PUBLICATION", "INTAKE_PROVENANCE"]
            ).exists() or not (metadata.etag or metadata.checksum):
                raise DatasetManagementError(
                    "identity_unverified", "Review the object's missing identity evidence."
                )
            ResourceStorageObject.objects.filter(pk=storage_object.pk, state="DELETING").update(
                checksum=metadata.checksum if storage_object.provider == "local" else "",
                etag=metadata.etag or "",
            )

        with deletion_authority(storage_object.id):
            provider.delete_checked(
                storage_object.uri, etag=metadata.etag, checksum=metadata.checksum
            )

    @transaction.atomic
    def _finish_request(self, identifier: UUID, token: UUID) -> None:
        request = ResourceCleanupRequest.objects.select_for_update().get(pk=identifier)
        if request.lease_token != token:
            return
        items = list(request.items.all())
        remaining = [item for item in items if item.status != "DELETED"]
        failures = [item for item in remaining if item.status == "FAILED"]
        if not remaining:
            request.status = "COMPLETED"
            request.completed_at = timezone.now()
            if request.dataset_id:
                dataset = Dataset.objects.select_for_update().get(pk=request.dataset_id)
                dataset.purged_at = timezone.now()
                dataset.save(update_fields=["purged_at"])
                if request.requested_by:
                    record_dataset_activity(dataset, request.requested_by, "PURGE_COMPLETED")
            if request.intake_id:
                StagedResourceIntake.objects.filter(pk=request.intake_id).update(
                    source_deleted_at=timezone.now(),
                    inspection_json={},
                    original_filename="Deleted source",
                    normalized_filename="Deleted source",
                    cleanup_required=False,
                )
            if request.kind == "CANDIDATE":
                publications = ResourceStorageReference.objects.filter(
                    storage_object__cleanup_items__request=request, owner_kind="PUBLICATION"
                ).values_list("owner_id", flat=True)
                StagedResourcePublication.objects.filter(pk__in=publications).update(
                    cleanup_required=False
                )
                references = ResourceStorageReference.objects.filter(
                    storage_object__cleanup_items__request=request, owner_kind="CHANGE"
                )
                for reference in references:
                    DatasetChange.objects.filter(
                        pk=reference.owner_id,
                        revision=reference.owner_revision,
                        status__in=["CANCELLED", "FAILED", "NO_CHANGES"],
                    ).update(prepared={})
        elif failures and any(item.attempts >= 5 for item in failures):
            request.status = "FAILED"
        else:
            request.status = "PARTIAL" if request.started_at is not None else "BLOCKED"
        request.error_code = failures[0].block_reason if failures else ""
        attempts = max((item.attempts for item in failures), default=0)
        request.next_attempt_at = timezone.now() + timedelta(minutes=min(15 * 2**attempts, 240))
        request.lease_token = None
        request.lease_expires_at = None
        request.save()
