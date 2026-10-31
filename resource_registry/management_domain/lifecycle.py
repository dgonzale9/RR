"""Apply atomic, audited dataset metadata and lifecycle operations."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

from Organization.models import Organization
from resource_registry.management_domain.authorization import DatasetManagementAuthorization
from resource_registry.management_domain.errors import DatasetManagementError
from resource_registry.management_domain.source_writes import require_source_write
from resource_registry.models import Dataset, DatasetActivity, DatasetArtifact, DatasetVersion
from resource_registry.storage.factory import DataRegistryStorageFactory


def require_revision(actual: int, expected: int) -> None:
    """Reject stale mutation attempts.

    Args:
        actual: Current persisted revision.
        expected: Revision the caller reviewed.

    Raises:
        DatasetManagementError: If another operation changed the resource.
    """
    if actual != expected:
        raise DatasetManagementError(
            "revision_conflict", "The resource changed; reload and review it."
        )


def record_dataset_activity(dataset: Dataset, actor: User, action: str, reason: str = "") -> None:
    """Append immutable maintenance evidence within the caller's transaction.

    Args:
        dataset: Dataset after its transition.
        actor: Authorized user performing the operation.
        action: Stable action name.
        reason: Optional explanation without storage credentials.
    """
    DatasetActivity.objects.create(
        dataset=dataset,
        actor=actor,
        action=action,
        reason=reason,
        version_id=dataset.current_version_id,
        dataset_revision=dataset.revision,
    )


class DatasetLifecycleService:
    """Perform lifecycle transitions shared by Admin and REST."""

    def __init__(self, user: User, organization: Organization | None) -> None:
        """Initialize current authority.

        Args:
            user: Authenticated actor.
            organization: Host-resolved organization.
        """
        self.authorization: DatasetManagementAuthorization = DatasetManagementAuthorization(
            user, organization
        )
        self.user: User = user

    def get_dataset(self, identifier: UUID | str, *, lock: bool = False) -> Dataset:
        """Resolve an authorized dataset, optionally locking it in a transaction."""
        queryset = self.authorization.datasets(include_archived=True)
        if lock:
            queryset = queryset.select_for_update()
        dataset = queryset.filter(pk=identifier).first()
        if dataset is None:
            raise DatasetManagementError("not_found", "Dataset not found.", 404)
        if dataset.archived_at is not None:
            self.authorization.require_manage(dataset)
        return dataset

    @transaction.atomic
    def update_metadata(
        self, identifier: UUID | str, values: Mapping[str, object], *, expected_revision: int
    ) -> Dataset:
        """Update editable metadata without changing identity or granting access.

        Args:
            identifier: Stable dataset ID.
            values: Name, description, tags, owner team or responsible user ID.
            expected_revision: Revision reviewed by the caller.

        Returns:
            The updated dataset.

        Raises:
            DatasetManagementError: If scope, fields, values or revision are invalid.
        """
        self.authorization.require_enabled()
        dataset = self.get_dataset(identifier, lock=True)
        self.authorization.require_edit(dataset)
        if dataset.purge_started_at is not None:
            raise DatasetManagementError("dataset_purging", "Dataset data is being deleted.", 410)
        require_revision(dataset.revision, expected_revision)
        allowed = {"name", "description", "tags", "owner_team", "responsible_user_id"}
        if set(values) - allowed:
            raise DatasetManagementError(
                "invalid_metadata", "Unknown or immutable metadata field.", 400
            )
        tags = values.get("tags", dataset.tags)
        if (
            not isinstance(tags, list)
            or len(tags) > 100
            or any(not isinstance(tag, str) or len(tag) > 100 for tag in tags)
        ):
            raise DatasetManagementError("invalid_tags", "Provide up to 100 text tags.", 400)
        if "responsible_user_id" in values and values["responsible_user_id"] is not None:
            responsible_id = values["responsible_user_id"]
            if not isinstance(responsible_id, int):
                raise DatasetManagementError(
                    "invalid_responsible_user", "Select a destination user.", 400
                )
            namespace = self.authorization.get_namespace(dataset.tenant_id)
            if not self.authorization.eligible_users(namespace).filter(pk=responsible_id).exists():
                raise DatasetManagementError(
                    "invalid_responsible_user", "Select a user in this destination.", 400
                )
        for field, value in values.items():
            setattr(dataset, field, value)
        dataset.full_clean(exclude=["current_version"])
        dataset.revision += 1
        dataset.save(update_fields=[*values, "revision", "updated_at"])
        record_dataset_activity(dataset, self.user, "METADATA_UPDATED")
        return dataset

    @transaction.atomic
    def archive(self, identifier: UUID | str, *, expected_revision: int, reason: str) -> Dataset:
        """Archive a dataset manually, preserving artifacts and publication evidence."""
        dataset = self._lock_managed_dataset(identifier, expected_revision, reason)
        dataset.status = "ARCHIVED"
        dataset.archived_at = timezone.now()
        dataset.archive_reason = "MANUAL"
        return self._save_transition(dataset, "ARCHIVED", reason)

    def unarchive(self, identifier: UUID | str, *, expected_revision: int, reason: str) -> Dataset:
        """Check physical availability before atomically recovering a manual archive."""
        dataset = self.get_dataset(identifier)
        self.authorization.require_manage(dataset)
        self._require_no_cleanup(dataset)
        self._verify_version_storage(dataset, dataset.current_version_id)
        return self._unarchive_transaction(
            identifier, expected_revision=expected_revision, reason=reason
        )

    @transaction.atomic
    def _unarchive_transaction(
        self, identifier: UUID | str, *, expected_revision: int, reason: str
    ) -> Dataset:
        """Recover a manually archived dataset whose current artifacts still exist."""
        dataset = self._lock_managed_dataset(identifier, expected_revision, reason)
        if dataset.archive_reason != "MANUAL":
            raise DatasetManagementError(
                "destination_detached",
                "Restore the destination publication through its publication workflow.",
            )
        self._require_usable_version(dataset, dataset.current_version_id)
        dataset.status = "ACTIVE"
        dataset.archived_at = None
        dataset.archive_reason = ""
        return self._save_transition(dataset, "UNARCHIVED", reason)

    def restore_version(
        self, identifier: UUID | str, *, version_id: UUID | str, expected_revision: int, reason: str
    ) -> Dataset:
        """Check historical storage before atomically selecting a published version."""
        dataset = self.get_dataset(identifier)
        require_source_write(dataset.external_source_id)
        self.authorization.require_manage(dataset)
        self._verify_version_storage(dataset, version_id)
        return self._restore_version_transaction(
            identifier, version_id=version_id, expected_revision=expected_revision, reason=reason
        )

    @transaction.atomic
    def _restore_version_transaction(
        self, identifier: UUID | str, *, version_id: UUID | str, expected_revision: int, reason: str
    ) -> Dataset:
        """Select a historical published version without altering history or identity."""
        dataset = self._lock_managed_dataset(identifier, expected_revision, reason)
        if dataset.archived_at:
            raise DatasetManagementError(
                "dataset_archived", "Recover the dataset before restoring a version."
            )
        version = self._require_usable_version(dataset, version_id)
        dataset.current_version = version
        return self._save_transition(dataset, "VERSION_RESTORED", reason)

    def _lock_managed_dataset(
        self, identifier: UUID | str, expected_revision: int, reason: str
    ) -> Dataset:
        self.authorization.require_enabled()
        dataset = self.get_dataset(identifier, lock=True)
        self.authorization.require_manage(dataset)
        self._require_no_cleanup(dataset)
        require_revision(dataset.revision, expected_revision)
        if not reason.strip() or len(reason) > 500:
            raise DatasetManagementError(
                "reason_required", "Provide a reason of at most 500 characters.", 400
            )
        return dataset

    @staticmethod
    def _require_no_cleanup(dataset: Dataset) -> None:
        if dataset.purge_started_at is not None:
            raise DatasetManagementError(
                "dataset_purging", "Dataset deletion has started; recovery is unavailable.", 410
            )
        if dataset.cleanup_requests.exclude(status__in=["COMPLETED", "CANCELLED"]).exists():
            raise DatasetManagementError(
                "deletion_pending",
                "Cancel the deletion request before recovering or changing lifecycle.",
            )

    def _save_transition(self, dataset: Dataset, action: str, reason: str) -> Dataset:
        dataset.revision += 1
        dataset.save(
            update_fields=[
                "current_version",
                "status",
                "archived_at",
                "archive_reason",
                "revision",
                "updated_at",
            ]
        )
        record_dataset_activity(dataset, self.user, action, reason)
        return dataset

    @staticmethod
    def _require_usable_version(dataset: Dataset, version_id: UUID | str | None) -> DatasetVersion:
        if version_id is None:
            raise DatasetManagementError(
                "version_unavailable", "No usable published version is selected."
            )
        version = DatasetVersion.objects.filter(
            dataset=dataset, id=version_id, status="READY"
        ).first()
        if (
            version is None
            or not DatasetArtifact.objects.filter(
                dataset_version=version, lifecycle_status="ACTIVE"
            ).exists()
        ):
            raise DatasetManagementError(
                "version_unavailable", "The selected version has no available artifacts."
            )
        return version

    def _verify_version_storage(self, dataset: Dataset, version_id: UUID | str | None) -> None:
        version = self._require_usable_version(dataset, version_id)
        artifacts = version.artifacts.filter(lifecycle_status="ACTIVE")
        canonical = artifacts.filter(metadata_json__is_canonical=True)
        if canonical.exists():
            artifacts = canonical
        for artifact in artifacts.order_by("-created_at"):
            try:
                provider = DataRegistryStorageFactory().create_provider(artifact.storage_provider)
                if provider.exists(artifact.uri):
                    return
            except Exception:
                # Treat provider-specific absence/failure as unavailable without exposing credentials.
                continue
        raise DatasetManagementError(
            "version_unavailable", "The version's stored artifacts are unavailable."
        )
