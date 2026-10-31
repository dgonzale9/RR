"""Share manager-authorized cleanup review and lifecycle actions across Admin and REST."""

from __future__ import annotations

import hashlib
from datetime import timedelta
from uuid import UUID

from django.conf import settings
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Q, QuerySet
from django.utils import timezone

from Organization.models import Organization
from resource_registry.cleanup.inventory import (
    dataset_inventory_fingerprint,
    register_dataset_objects,
)
from resource_registry.cleanup.messages import describe_cleanup_reason
from resource_registry.cleanup.protection import get_dataset_blocker
from resource_registry.management_domain.authorization import DatasetManagementAuthorization
from resource_registry.management_domain.errors import DatasetManagementError
from resource_registry.management_domain.lifecycle import record_dataset_activity, require_revision
from resource_registry.models import (
    Dataset,
    DatasetManagementConfiguration,
    ResourceCleanupItem,
    ResourceCleanupRequest,
    ResourceStorageObject,
    ResourceStorageReference,
)


class DatasetCleanupService:
    """Review and persist cleanup intent without performing irreversible storage operations."""

    def __init__(self, user: User, organization: Organization | None) -> None:
        """Bind cleanup to an authenticated actor and the trusted host organization."""
        self.authorization: DatasetManagementAuthorization = DatasetManagementAuthorization(
            user, organization
        )
        self.user: User = user

    def get_dataset(self, identifier: UUID | str, *, lock: bool = False) -> Dataset:
        """Resolve an authorized manager's dataset without requiring intake operator access."""
        rows = self.authorization.datasets(include_archived=True)
        if lock:
            rows = rows.select_for_update()
        dataset = rows.filter(pk=identifier).first()
        if dataset is None:
            raise DatasetManagementError("not_found", "Dataset not found.", 404)
        self.authorization.require_manage(dataset)
        return dataset

    def queryset(self) -> QuerySet[ResourceCleanupRequest]:
        """Filter admission before counting, searching or paginating cleanup requests."""
        namespaces = [
            namespace.id
            for namespace in self.authorization.namespaces()
            if self.authorization.can_manage_namespace(namespace)
        ]
        admitted = Q(namespace_id__in=namespaces)
        if self.user.is_superuser:
            admitted |= Q(namespace__isnull=True)
        elif self.user.has_perm("resource_registry.manage_stagedresourceintake"):
            admitted |= Q(namespace__isnull=True, intake__isnull=False)
        return (
            ResourceCleanupRequest.objects.filter(admitted)
            .select_related("dataset", "requested_by", "namespace")
            .annotate(item_count=Count("items"))
        )

    def get_request(self, identifier: UUID | str, *, lock: bool = False) -> ResourceCleanupRequest:
        """Resolve one scoped request, hiding other destinations."""
        identifiers = self.queryset().values("id")
        rows = ResourceCleanupRequest.objects.filter(pk__in=identifiers)
        if lock:
            rows = rows.select_for_update()
        request = rows.filter(pk=identifier).first()
        if request is None:
            raise DatasetManagementError("not_found", "Cleanup request not found.", 404)
        return request

    def preview(
        self, identifier: UUID | str, *, offset: int = 0, limit: int = 100
    ) -> dict[str, object]:
        """Build a server-owned inventory, sizes and sanitized blockers for review."""
        dataset = self.get_dataset(identifier)
        register_dataset_objects(dataset)
        other_references = ResourceStorageReference.objects.filter(
            storage_object_id=OuterRef("pk"), released_at__isnull=True
        ).exclude(dataset=dataset)
        objects = list(
            ResourceStorageObject.objects.filter(references__dataset=dataset)
            .distinct()
            .annotate(is_shared=Exists(other_references))
        )
        configuration = DatasetManagementConfiguration.objects.filter(
            organization=dataset.organization
        ).first()
        days = max(30, configuration.dataset_recovery_days if configuration else 30)
        earliest = (dataset.archived_at or timezone.now()) + timedelta(days=days)
        shared = {obj.id for obj in objects if getattr(obj, "is_shared", False)}
        available = [obj for obj in objects if obj.state != "DELETED"]
        offset, limit = max(0, offset), min(100, max(1, limit))
        blocker = get_dataset_blocker(dataset)
        return {
            "dataset_id": str(dataset.id),
            "revision": dataset.revision,
            "inventory_fingerprint": dataset_inventory_fingerprint(dataset),
            "execute_after": earliest.isoformat(),
            "mode": configuration.cleanup_mode if configuration else "SIMULATE",
            "exclusive_bytes": sum(obj.size_bytes for obj in available if obj.id not in shared),
            "shared_bytes": sum(obj.size_bytes for obj in available if obj.id in shared),
            "estimated_recoverable_bytes": sum(
                obj.size_bytes
                for obj in available
                if obj.id not in shared and obj.ownership_verified
            ),
            "object_count": len(objects),
            "next_offset": offset + limit if offset + limit < len(objects) else None,
            "previous_offset": max(0, offset - limit) if offset else None,
            "publication_count": dataset.staged_resource_publication_assignments.values(
                "publication_id"
            )
            .distinct()
            .count(),
            "derivatives": {
                relation: getattr(dataset, relation).count()
                for relation in (
                    "query_runs",
                    "query_jobs",
                    "analysis_artifacts",
                    "chart_artifacts",
                    "export_plans",
                    "generated_artifacts",
                )
            },
            "blocker": blocker,
            "blocker_message": describe_cleanup_reason(blocker),
            "versions": list(
                dataset.versions.order_by("version_number").values_list("version_number", flat=True)
            ),
            "objects": [
                {
                    "id": str(obj.id),
                    "size_bytes": obj.size_bytes,
                    "shared": obj.id in shared,
                    "state": obj.state,
                    "ownership_verified": obj.ownership_verified,
                }
                for obj in objects[offset : offset + limit]
            ],
            "recoverable": dataset.purge_started_at is None,
        }

    @transaction.atomic
    def request_deletion(
        self,
        identifier: UUID | str,
        *,
        expected_revision: int,
        inventory_fingerprint: str,
        reason: str,
        idempotency_key: str,
    ) -> ResourceCleanupRequest:
        """Confirm a reviewed inventory and deadline; repeated identical requests reuse it."""
        self.authorization.require_enabled()
        self._require_cleanup_enabled()
        dataset = self.get_dataset(identifier, lock=True)
        if (
            not reason.strip()
            or len(reason) > 500
            or not idempotency_key.strip()
            or len(idempotency_key) > 128
        ):
            raise DatasetManagementError(
                "invalid_request", "Provide a reason and request key.", 400
            )
        key = hashlib.sha256(
            f"dataset:{dataset.pk}:{self.user.pk}:{idempotency_key}".encode()
        ).hexdigest()
        existing = ResourceCleanupRequest.objects.filter(idempotency_key=key).first()
        if existing:
            if (
                existing.inventory_fingerprint != inventory_fingerprint
                or existing.reason != reason.strip()
            ):
                raise DatasetManagementError(
                    "idempotency_conflict", "The request key was used for another review."
                )
            return existing
        if dataset.status != "ARCHIVED" or dataset.archived_at is None:
            raise DatasetManagementError(
                "archive_required", "Archive the dataset before requesting deletion."
            )
        if dataset.purge_started_at is not None:
            raise DatasetManagementError(
                "dataset_purging", "Dataset deletion has already started.", 410
            )
        if dataset.cleanup_requests.exclude(status__in=["COMPLETED", "CANCELLED"]).exists():
            raise DatasetManagementError("deletion_pending", "A deletion request already exists.")
        require_revision(dataset.revision, expected_revision)
        register_dataset_objects(dataset)
        if dataset_inventory_fingerprint(dataset) != inventory_fingerprint:
            raise DatasetManagementError(
                "inventory_changed", "Review the changed deletion inventory."
            )
        configuration = DatasetManagementConfiguration.objects.get(
            organization=dataset.organization
        )
        if configuration.cleanup_mode == "DISABLED":
            raise DatasetManagementError(
                "cleanup_disabled", "Cleanup is disabled for this organization."
            )
        request = ResourceCleanupRequest.objects.create(
            dataset=dataset,
            namespace_id=dataset.tenant_id,
            requested_by=self.user,
            idempotency_key=key,
            reason=reason.strip(),
            expected_revision=dataset.revision,
            inventory_fingerprint=inventory_fingerprint,
            execute_after=max(
                timezone.now(),
                dataset.archived_at + timedelta(days=max(30, configuration.dataset_recovery_days)),
            ),
        )
        self._set_items(request, dataset)
        record_dataset_activity(dataset, self.user, "PURGE_REQUESTED", reason.strip())
        return request

    @transaction.atomic
    def cancel(self, identifier: UUID | str) -> ResourceCleanupRequest:
        """Cancel a request only before any worker begins irreversible cleanup."""
        request = self.get_request(identifier, lock=True)
        if request.started_at is not None or request.status == "COMPLETED":
            raise DatasetManagementError(
                "deletion_started", "Deletion has started and cannot be cancelled."
            )
        if request.status == "CANCELLED":
            return request
        request.status = "CANCELLED"
        request.save(update_fields=["status", "updated_at"])
        if request.dataset is not None:
            record_dataset_activity(request.dataset, self.user, "PURGE_CANCELLED", request.reason)
        return request

    @transaction.atomic
    def confirm(
        self, identifier: UUID | str, *, expected_revision: int, inventory_fingerprint: str
    ) -> ResourceCleanupRequest:
        """Adopt and reconfirm a changed inventory using current manager authority."""
        self.authorization.require_enabled()
        self._require_cleanup_enabled()
        request = self.get_request(identifier, lock=True)
        if (
            request.started_at is not None
            or request.dataset_id is None
            or request.status not in {"NEEDS_REVIEW", "BLOCKED"}
        ):
            raise DatasetManagementError(
                "review_unavailable", "This deletion cannot be reconfirmed."
            )
        dataset = self.get_dataset(request.dataset_id, lock=True)
        require_revision(dataset.revision, expected_revision)
        register_dataset_objects(dataset)
        if dataset_inventory_fingerprint(dataset) != inventory_fingerprint:
            raise DatasetManagementError(
                "inventory_changed", "Review the changed deletion inventory."
            )
        request.expected_revision = dataset.revision
        request.inventory_fingerprint = inventory_fingerprint
        request.requested_by = self.user
        request.status = "SCHEDULED"
        request.error_code = ""
        request.next_attempt_at = timezone.now()
        request.save()
        self._set_items(request, dataset)
        record_dataset_activity(dataset, self.user, "PURGE_CONFIRMED", request.reason)
        return request

    @transaction.atomic
    def retry(self, identifier: UUID | str) -> ResourceCleanupRequest:
        """Reset a failed operation without repeating confirmed item deletions."""
        self.authorization.require_enabled()
        self._require_cleanup_enabled()
        request = self.get_request(identifier, lock=True)
        if request.status != "FAILED":
            raise DatasetManagementError(
                "retry_unavailable", "Only failed cleanup requests can be retried."
            )
        request.items.filter(status="FAILED").update(status="PENDING", attempts=0, block_reason="")
        request.status = "SCHEDULED"
        request.next_attempt_at = timezone.now()
        request.requested_by = self.user
        request.save()
        if request.dataset is not None:
            record_dataset_activity(request.dataset, self.user, "PURGE_RETRIED", request.reason)
        return request

    def _require_cleanup_enabled(self) -> None:
        if not getattr(settings, "RESOURCE_REGISTRY_CLEANUP_COORDINATOR_ENABLED", True):
            raise DatasetManagementError("cleanup_disabled", "Cleanup is disabled.")
        if DatasetManagementConfiguration.objects.filter(
            organization=self.authorization.organization, cleanup_mode="DISABLED"
        ).exists():
            raise DatasetManagementError(
                "cleanup_disabled", "Cleanup is disabled for this organization."
            )

    @staticmethod
    def _set_items(request: ResourceCleanupRequest, dataset: Dataset) -> None:
        identifiers = (
            ResourceStorageObject.objects.filter(references__dataset=dataset)
            .values_list("id", flat=True)
            .distinct()
        )
        ResourceCleanupItem.objects.bulk_create(
            [
                ResourceCleanupItem(request=request, storage_object_id=identifier)
                for identifier in identifiers
            ],
            ignore_conflicts=True,
        )


def cleanup_payload(request: ResourceCleanupRequest) -> dict[str, object]:
    """Expose progress without storage locations or foreign destination information."""
    return {
        "id": str(request.pk),
        "dataset_id": str(request.dataset_id) if request.dataset_id else None,
        "kind": request.kind,
        "status": request.status,
        "status_label": request.get_status_display(),
        "execute_after": request.execute_after.isoformat(),
        "started_at": request.started_at.isoformat() if request.started_at else None,
        "error_code": request.error_code,
        "error_message": request.error_message,
        "completed_at": request.completed_at.isoformat() if request.completed_at else None,
        "requested_by_id": request.requested_by_id,
        "recoverable": request.started_at is None,
        "inventory_fingerprint": request.inventory_fingerprint,
    }
