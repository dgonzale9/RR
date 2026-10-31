"""Own generic external-file identity, admission, and reversible source removal."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

from Organization.models import Organization
from resource_registry.management_domain.authorization import DatasetManagementAuthorization
from resource_registry.management_domain.errors import DatasetManagementError
from resource_registry.management_domain.lifecycle import record_dataset_activity, require_revision
from resource_registry.models import (
    DatasetChange,
    DatasetExternalSource,
    DatasetSourceOperation,
    RegistryNamespace,
    StagedResourcePublication,
    StagedResourcePublicationDataset,
)

# Django's persisted JSON representations cross an existing untyped application boundary.
JsonObject = dict[str, Any]
PENDING_STATES = ("QUEUED", "PROCESSING", "READY", "NEEDS_MANAGER")


def normalize_source_reference(value: Mapping[str, object]) -> dict[str, str]:
    """Validate opaque identity without interpreting provider semantics.

    Args:
        value: Provider, external ID and version supplied by the integration.

    Returns:
        Normalized immutable reference fields.

    Raises:
        DatasetManagementError: If fields, lengths or identifier syntax are invalid.
    """
    if set(value) != {"provider", "external_id", "version"}:
        raise DatasetManagementError(
            "invalid_source_reference", "Provide provider, external_id and version.", 400
        )
    result: dict[str, str] = {}
    for key, limit in (("provider", 64), ("external_id", 512), ("version", 512)):
        item = value[key]
        if (
            not isinstance(item, str)
            or not item.strip()
            or len(item) > limit
            or any(ord(char) < 32 for char in item)
        ):
            raise DatasetManagementError(
                "invalid_source_reference", "Invalid external source reference.", 400
            )
        result[key] = item.strip()
    if not re.fullmatch(r"[a-z][a-z0-9_-]*", result["provider"]):
        raise DatasetManagementError(
            "invalid_source_reference", "Invalid provider identifier.", 400
        )
    return result


def require_source_authority(
    authority: DatasetManagementAuthorization,
    namespace: RegistryNamespace,
    *,
    allow_manager: bool = False,
) -> None:
    """Require synchronization authority independently of staff admission.

    Args:
        authority: Live host-bound actor admission.
        namespace: Requested destination.
        allow_manager: Whether this operation permits manager review authority.

    Raises:
        DatasetManagementError: If the current actor lacks scoped authority.
    """
    if namespace.id not in authority.namespace_ids or not (
        authority.user.has_perm("resource_registry.synchronize_dataset_sources", namespace)
        or (allow_manager and authority.can_manage_namespace(namespace))
    ):
        raise DatasetManagementError(
            "source_sync_denied", "External source synchronization is not permitted.", 403
        )


def lock_change_source(change_id: UUID | str) -> DatasetExternalSource | None:
    """Acquire source before proposal locks to serialize lifecycle changes.

    Args:
        change_id: Proposal whose immutable source association should be locked.

    Returns:
        Locked source or None for a manual/missing proposal.

    Side Effects:
        Hold the source row lock until the caller's transaction completes.
    """
    source_id = (
        DatasetChange.objects.filter(pk=change_id)
        .values_list("external_source_id", flat=True)
        .first()
    )
    return (
        DatasetExternalSource.objects.select_for_update().get(pk=source_id) if source_id else None
    )


def source_payload(source: DatasetExternalSource) -> JsonObject:
    """Return control metadata without exposing archived contents or storage paths.

    Args:
        source: Already authorized source and its output associations.

    Returns:
        Source identity, control revision and exact dataset/worksheet mappings.
    """
    return {
        "id": str(source.id),
        "namespace_id": str(source.namespace_id),
        "provider": source.provider,
        "external_id": source.external_id,
        "revision": source.revision,
        "status": source.status,
        "last_applied_version": source.last_applied_version,
        "workbook_id": str(source.workbook_id) if source.workbook_id else None,
        "workbook_revision": source.workbook.revision if source.workbook else None,
        "items": [
            {
                "dataset_id": str(dataset.id),
                "dataset_revision": dataset.revision,
                "dataset_version_id": (
                    str(dataset.current_version_id) if dataset.current_version_id else None
                ),
                "sheet_name": dataset.workbook_sheet or None,
                "name": dataset.name,
                "status": dataset.status,
                "archive_reason": dataset.archive_reason,
                "data_available": dataset.purge_started_at is None,
            }
            for dataset in source.datasets.order_by("id")
        ],
    }


class DatasetExternalSourceService:
    """Authorize source control reads and atomic logical removal within one destination."""

    def __init__(self, user: User, organization: Organization | None) -> None:
        """Bind source operations to a persisted actor and trusted host.

        Args:
            user: Authenticated actor.
            organization: Organization established by trusted middleware.
        """
        self.user: User = user
        self.authorization: DatasetManagementAuthorization = DatasetManagementAuthorization(
            user, organization
        )

    def get_source(self, identifier: UUID | str, *, lock: bool = False) -> DatasetExternalSource:
        """Resolve a source after namespace and source authority are admitted.

        Args:
            identifier: Source UUID.
            lock: Whether to lock the source inside the caller's transaction.

        Returns:
            Authorized persisted source.

        Raises:
            DatasetManagementError: If the source is unavailable or authority is absent.
        """
        rows = DatasetExternalSource.objects.filter(
            namespace_id__in=self.authorization.namespace_ids
        )
        if lock:
            rows = rows.select_for_update()
        source = rows.filter(pk=identifier).first()
        if source is None:
            raise DatasetManagementError("not_found", "External source not found.", 404)
        namespace = self.authorization.get_namespace(source.namespace_id)
        require_source_authority(self.authorization, namespace, allow_manager=True)
        return source

    @transaction.atomic
    def deactivate(
        self, identifier: UUID | str, *, expected_revision: int, reason: str, idempotency_key: str
    ) -> JsonObject:
        """Detach source assignments and archive unprotected outputs atomically.

        Args:
            identifier: Source UUID.
            expected_revision: Source control revision reviewed by the caller.
            reason: Human-readable removal explanation.
            idempotency_key: Persisted actor/source-scoped request identity.

        Returns:
            Immutable removal result, including per-dataset outcomes.

        Raises:
            DatasetManagementError: If authority, revision, identity or lifecycle checks fail.

        Side Effects:
            Cancel pending proposals, detach owned assignments and record logical archives.
            Retain physical content and all historical operation evidence.
        """
        self.authorization.require_enabled()
        source = self.get_source(identifier, lock=True)
        if (
            not reason.strip()
            or len(reason) > 500
            or not idempotency_key.strip()
            or len(idempotency_key) > 128
        ):
            raise DatasetManagementError(
                "invalid_request", "Provide a reason and idempotency key.", 400
            )
        fingerprint = hashlib.sha256(
            json.dumps(
                {"expected_revision": expected_revision, "reason": reason.strip()}, sort_keys=True
            ).encode()
        ).hexdigest()
        previous = source.operations.filter(
            actor=self.user, idempotency_key=idempotency_key
        ).first()
        if previous:
            if previous.request_fingerprint != fingerprint:
                raise DatasetManagementError(
                    "idempotency_conflict", "The request key was used for different content."
                )
            return dict(previous.result)
        require_revision(source.revision, expected_revision)
        changes = list(source.changes.select_for_update().order_by("id"))
        publications = list(
            StagedResourcePublication.objects.select_for_update()
            .filter(pk__in=[change.publication_id for change in changes if change.publication_id])
            .order_by("id")
        )
        datasets = list(source.datasets.select_for_update().order_by("id"))
        for dataset in datasets:
            self.authorization.require_edit(dataset)
            if dataset.purge_started_at:
                raise DatasetManagementError(
                    "dataset_purging", "Dataset data is being deleted.", 410
                )
        now = timezone.now()
        for change in changes:
            if change.status in PENDING_STATES:
                change.status = "CANCELLED"
                change.revision += 1
                change.completed_at = now
                change.save(update_fields=["status", "revision", "completed_at", "updated_at"])
        dataset_ids = [dataset.id for dataset in datasets]
        for publication in publications:
            publication.dataset_assignments.filter(
                dataset_id__in=dataset_ids, detached_at__isnull=True
            ).update(detached_at=now)
            if (
                not publication.dataset_assignments.filter(detached_at__isnull=True).exists()
                and publication.assignment_status == "ACTIVE"
            ):
                publication.assignment_status = "DETACHED"
                publication.detached_at = now
                publication.detached_by = self.user
                publication.detachment_reason = reason.strip()
                publication.save(
                    update_fields=[
                        "assignment_status",
                        "detached_at",
                        "detached_by",
                        "detachment_reason",
                    ]
                )
        protected = set(
            StagedResourcePublicationDataset.objects.filter(
                dataset_id__in=dataset_ids,
                detached_at__isnull=True,
                publication__assignment_status__in=["ACTIVE", "DETACH_REQUESTED"],
            ).values_list("dataset_id", flat=True)
        )
        outcomes: list[JsonObject] = []
        for dataset in datasets:
            outcome = "already_archived" if dataset.archived_at else "retained"
            if not dataset.archived_at and dataset.id not in protected:
                dataset.status = "ARCHIVED"
                dataset.archived_at = now
                dataset.archive_reason = "SOURCE_REMOVED"
                dataset.revision += 1
                dataset.save(
                    update_fields=[
                        "status",
                        "archived_at",
                        "archive_reason",
                        "revision",
                        "updated_at",
                    ]
                )
                record_dataset_activity(dataset, self.user, "SOURCE_REMOVED", reason.strip())
                outcome = "archived"
            outcomes.append(
                {
                    "dataset_id": str(dataset.id),
                    "dataset_revision": dataset.revision,
                    "outcome": outcome,
                }
            )
        source.status = "INACTIVE"
        source.revision += 1
        source.save(update_fields=["status", "revision", "updated_at"])
        result = {
            "source_id": str(source.id),
            "source_revision": source.revision,
            "status": source.status,
            "items": outcomes,
        }
        DatasetSourceOperation.objects.create(
            source=source,
            actor=self.user,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            result=result,
        )
        return result
