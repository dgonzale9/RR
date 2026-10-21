from __future__ import annotations

import uuid
from collections.abc import Collection, Iterable
from typing import ClassVar

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.db.models.base import ModelBase

from resource_registry.exceptions import (
    RegistryNamespaceDeletionNotAllowedError,
    RegistryNamespaceImmutableFieldError,
)
from resource_registry.utils.json_checksum import calculate_transformation_specification_checksum


class DatasetSourceType(models.TextChoices):
    FILE_UPLOAD = "FILE_UPLOAD", "File Upload"
    EXTERNAL_PROVIDER = "EXTERNAL_PROVIDER", "External Provider"
    MANUAL_REFERENCE = "MANUAL_REFERENCE", "Manual Reference"
    TRANSFORMATION = "TRANSFORMATION", "Transformation"


class DatasetStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    ACTIVE = "ACTIVE", "Active"
    ARCHIVED = "ARCHIVED", "Archived"


class DatasetVisibilityScope(models.TextChoices):
    """Identify the Organization visibility boundary of a Dataset."""

    WORKSPACE = "WORKSPACE", "Workspace"
    ORGANIZATION = "ORGANIZATION", "Organization"


class DatasetVersionStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    READY = "READY", "Ready"
    FAILED = "FAILED", "Failed"


class OutboxEntryStatus(models.TextChoices):
    """Identify the local publication state of an immutable integration event."""

    PENDING = "PENDING", "Pending"
    CLAIMED = "CLAIMED", "Claimed"
    PUBLISHED = "PUBLISHED", "Published"


class LargeFileUploadStatus(models.TextChoices):
    PENDING_UPLOAD = "PENDING_UPLOAD", "Pending upload"
    VALIDATING = "VALIDATING", "Validating"
    COMPLETED = "COMPLETED", "Completed"
    FAILED = "FAILED", "Failed"
    CANCELLED = "CANCELLED", "Cancelled"
    EXPIRED = "EXPIRED", "Expired"


class StagedResourceIntakeStatus(models.TextChoices):
    """Identify the lifecycle of a private unassigned workbook intake."""

    PENDING_UPLOAD = "PENDING_UPLOAD", "Pending upload"
    COMPLETED = "COMPLETED", "Completed"
    ANALYZING = "ANALYZING", "Analyzing"
    READY_FOR_PUBLICATION = "READY_FOR_PUBLICATION", "Ready for publication"
    PUBLISHING = "PUBLISHING", "Publishing"
    PUBLISHED = "PUBLISHED", "Published"
    CANCELLED = "CANCELLED", "Cancelled"
    EXPIRED = "EXPIRED", "Expired"
    FAILED = "FAILED", "Failed"


class StagedResourceInspectionJobStatus(models.TextChoices):
    """Identify the durable worker lifecycle for one intake inspection."""

    QUEUED = "QUEUED", "Queued"
    RUNNING = "RUNNING", "Running"
    COMPLETED = "COMPLETED", "Completed"
    FAILED = "FAILED", "Failed"


class StagedResourcePublicationStatus(models.TextChoices):
    """Identify the independently managed assignment of one intake to one destination."""

    ACTIVE = "ACTIVE", "Active"
    DETACH_REQUESTED = "DETACH_REQUESTED", "Detach Requested"
    DETACHED = "DETACHED", "Detached"


class TransformationDefinitionVersionStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    PUBLISHED = "PUBLISHED", "Published"
    DEPRECATED = "DEPRECATED", "Deprecated"


class TransformationRunStatus(models.TextChoices):
    QUEUED = "QUEUED", "Queued"
    RUNNING = "RUNNING", "Running"
    OUTPUT_PENDING = "OUTPUT_PENDING", "Output Pending"
    SUCCEEDED = "SUCCEEDED", "Succeeded"
    FAILED = "FAILED", "Failed"
    CANCELLED = "CANCELLED", "Cancelled"


class TransformationRunStepAttemptStatus(models.TextChoices):
    QUEUED = "QUEUED", "Queued"
    RUNNING = "RUNNING", "Running"
    SUCCEEDED = "SUCCEEDED", "Succeeded"
    FAILED = "FAILED", "Failed"
    CANCELLED = "CANCELLED", "Cancelled"


class TransformationRunStepSideEffectDisposition(models.TextChoices):
    NOT_STARTED = "NOT_STARTED", "Not Started"
    APPLIED = "APPLIED", "Applied"
    UNKNOWN = "UNKNOWN", "Unknown"


class TransformationRunOutputStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    MATERIALIZING = "MATERIALIZING", "Materializing"
    MATERIALIZED = "MATERIALIZED", "Materialized"
    REGISTERED = "REGISTERED", "Registered"
    CLEANUP_PENDING = "CLEANUP_PENDING", "Cleanup Pending"
    FAILED = "FAILED", "Failed"


class TransformationRunOutputKind(models.TextChoices):
    STANDALONE = "STANDALONE", "Standalone"
    DATASET_BACKED = "DATASET_BACKED", "Dataset Backed"


class TransformationRunOutputPolicyKind(models.TextChoices):
    STANDALONE = "STANDALONE", "Standalone"
    NEW_DATASET = "NEW_DATASET", "New Dataset"
    NEW_DATASET_VERSION = "NEW_DATASET_VERSION", "New Dataset Version"


class TransformationWorkspaceLeaseState(models.TextChoices):
    """Identify one durable provider workspace lease lifecycle state."""

    ACTIVE = "ACTIVE", "Active"
    COMMITTED = "COMMITTED", "Committed"
    EXPIRED = "EXPIRED", "Expired"
    CLEANUP_PENDING = "CLEANUP_PENDING", "Cleanup Pending"
    RELEASED = "RELEASED", "Released"


class ArtifactRole(models.TextChoices):
    PRIMARY = "PRIMARY", "Primary"
    SNAPSHOT = "SNAPSHOT", "Snapshot"
    SAMPLE = "SAMPLE", "Sample"


class ArtifactFormat(models.TextChoices):
    CSV = "CSV", "CSV"
    PARQUET = "PARQUET", "Parquet"
    JSON = "JSON", "JSON"
    JSONL = "JSONL", "JSON Lines"
    OTHER = "OTHER", "Other"


class ArtifactLifecycleStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "Active"
    DEPRECATED = "DEPRECATED", "Deprecated"
    DELETED_REFERENCE = "DELETED_REFERENCE", "Deleted Reference"
    PHYSICAL_DELETED = "PHYSICAL_DELETED", "Physical Deleted"


class ProfileType(models.TextChoices):
    BASIC = "BASIC", "Basic"
    STATISTICAL = "STATISTICAL", "Statistical"
    DATA_QUALITY = "DATA_QUALITY", "Data Quality"


class LineageType(models.TextChoices):
    DERIVED_FROM = "DERIVED_FROM", "Derived From"
    COPIED_FROM = "COPIED_FROM", "Copied From"
    INGESTED_FROM = "INGESTED_FROM", "Ingested From"


class GovernancePolicyType(models.TextChoices):
    RETENTION = "RETENTION", "Retention"
    CLASSIFICATION = "CLASSIFICATION", "Classification"
    ACCESS = "ACCESS", "Access"
    CUSTOM = "CUSTOM", "Custom"


class DatasetPolicyAssignmentType(models.TextChoices):
    OVERRIDE = "OVERRIDE", "Override"
    ADDITIVE = "ADDITIVE", "Additive"


class ExternalDatasetJobType(models.TextChoices):
    INGEST = "INGEST", "Ingest"
    SYNC = "SYNC", "Sync"
    PROFILE = "PROFILE", "Profile"
    LINEAGE = "LINEAGE", "Lineage"


class ExternalDatasetJobStatus(models.TextChoices):
    QUEUED = "QUEUED", "Queued"
    RUNNING = "RUNNING", "Running"
    SUCCEEDED = "SUCCEEDED", "Succeeded"
    FAILED = "FAILED", "Failed"
    CANCELLED = "CANCELLED", "Cancelled"


class DatasetIntelligenceStatus(models.TextChoices):
    SUCCEEDED = "SUCCEEDED", "Succeeded"
    FAILED = "FAILED", "Failed"


class DatasetQueryJobStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


class DatasetIntelligenceJobStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


class DatasetIntelligenceJobOperation(models.TextChoices):
    ANALYSIS = "analysis", "Analysis"
    CHART = "chart", "Chart"
    EXPORT_PLAN = "export_plan", "Export Plan"
    ARTIFACT_GENERATION = "artifact_generation", "Artifact Generation"
    TRANSFORMATION = "transformation", "Transformation"


class WorkbookIngestionJobStatus(models.TextChoices):
    SUBMITTED = "submitted", "Submitted"
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    COMPLETED = "completed", "Completed"
    COMPLETED_WITH_SKIPS = "completed_with_skips", "Completed With Skips"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


class RegistrationSubmissionStatus(models.TextChoices):
    """Identify authenticated registration-submission lifecycle states."""

    SUBMITTED = "SUBMITTED", "Submitted"


class RegistrationJobType(models.TextChoices):
    """Identify the persisted async job selected from the upload format."""

    WORKBOOK_INGESTION = "WorkbookIngestionJob", "Workbook ingestion job"
    DATASET_REGISTRATION = "DatasetRegistrationJob", "Dataset registration job"


class DatasetRegistrationJobStatus(models.TextChoices):
    """Identify CSV registration jobs awaiting future worker activation."""

    SUBMITTED = "submitted", "Submitted"
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


class WorkbookIngestionStatus(models.TextChoices):
    STARTED = "STARTED", "Started"
    COMPLETED = "COMPLETED", "Completed"
    COMPLETED_WITH_SKIPS = "COMPLETED_WITH_SKIPS", "Completed With Skips"
    FAILED = "FAILED", "Failed"


class RegistryNamespaceQuerySet(models.QuerySet["RegistryNamespace"]):
    """Prevent ordinary bulk mutation of Registry Namespace aggregate state."""

    _IMMUTABLE_FIELDS = frozenset(
        {"id", "pk", "workspace", "workspace_id", "organization", "organization_id"},
    )

    def update(self, **kwargs: object) -> int:
        """Reject bulk mutation when it targets namespace identity or ownership."""
        protected_fields = self._IMMUTABLE_FIELDS.intersection(kwargs)
        if protected_fields:
            raise RegistryNamespaceImmutableFieldError(
                "Registry Namespace identity and Workspace ownership are immutable.",
            )
        return super().update(**kwargs)

    def bulk_update(
        self,
        objs: Iterable[RegistryNamespace],
        fields: Iterable[str],
        batch_size: int | None = None,
    ) -> int:
        """Reject bulk mutation when it targets namespace identity or ownership."""
        normalized_fields = tuple(fields)
        protected_fields = self._IMMUTABLE_FIELDS.intersection(normalized_fields)
        if protected_fields:
            raise RegistryNamespaceImmutableFieldError(
                "Registry Namespace identity and Workspace ownership are immutable.",
            )
        return super().bulk_update(objs, normalized_fields, batch_size=batch_size)

    def bulk_create(
        self,
        objs: Iterable[RegistryNamespace],
        batch_size: int | None = None,
        ignore_conflicts: bool = False,
        update_conflicts: bool = False,
        update_fields: Iterable[str] | None = None,
        unique_fields: Iterable[str] | None = None,
    ) -> list[RegistryNamespace]:
        """Reject conflict updates that target namespace identity or ownership."""
        normalized_update_fields = tuple(update_fields or ())
        protected_fields = self._IMMUTABLE_FIELDS.intersection(normalized_update_fields)
        if update_conflicts and protected_fields:
            raise RegistryNamespaceImmutableFieldError(
                "Registry Namespace identity and Workspace ownership are immutable.",
            )
        return super().bulk_create(
            objs,
            batch_size=batch_size,
            ignore_conflicts=ignore_conflicts,
            update_conflicts=update_conflicts,
            update_fields=normalized_update_fields or None,
            unique_fields=tuple(unique_fields) if unique_fields is not None else None,
        )

    def delete(self) -> tuple[int, dict[str, int]]:
        """Reject ordinary bulk deletion of Registry Namespace aggregates."""
        raise RegistryNamespaceDeletionNotAllowedError(
            "Registry Namespace deletion requires an explicitly approved migration.",
        )


class RegistryNamespace(models.Model):
    """Represent an immutable Resource Registry namespace owned by a Workspace or Organization."""

    id = models.UUIDField(primary_key=True, editable=False)
    workspace = models.OneToOneField(
        "organization.Workspace",
        on_delete=models.PROTECT,
        related_name="resource_registry_namespace",
        null=True,
        blank=True,
    )
    organization = models.OneToOneField(
        "organization.Organization",
        on_delete=models.PROTECT,
        related_name="resource_registry_organization_namespace",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = RegistryNamespaceQuerySet.as_manager()

    _persisted_id: uuid.UUID | None = None
    _persisted_workspace_id: int | None = None
    _persisted_organization_id: int | None = None

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=(
                    Q(workspace__isnull=False, organization__isnull=True)
                    | Q(workspace__isnull=True, organization__isnull=False)
                ),
                name="registry_namespace_exactly_one_owner",
            ),
        ]

    @classmethod
    def from_db(
        cls,
        db: str | None,
        field_names: Collection[str],
        values: Collection[object],
    ) -> RegistryNamespace:
        """Restore persisted identity snapshots when hydrating an aggregate."""
        instance = super().from_db(db, field_names, values)
        instance._capture_persisted_identity()
        return instance

    def save(
        self,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        """Persist a new namespace or reject mutation of persisted aggregate identity."""
        self._assert_identity_and_ownership_are_immutable()
        super().save(
            force_insert=force_insert,
            force_update=force_update,
            using=using,
            update_fields=update_fields,
        )
        self._capture_persisted_identity()

    def delete(
        self,
        using: str | None = None,
        keep_parents: bool = False,
    ) -> tuple[int, dict[str, int]]:
        """Reject ordinary deletion of a Registry Namespace aggregate."""
        _ = using, keep_parents
        raise RegistryNamespaceDeletionNotAllowedError(
            "Registry Namespace deletion requires an explicitly approved migration.",
        )

    def _assert_identity_and_ownership_are_immutable(self) -> None:
        if self._persisted_id is not None and self.id != self._persisted_id:
            raise RegistryNamespaceImmutableFieldError(
                "Registry Namespace identity is immutable.",
            )
        if self._persisted_id is not None and self.workspace_id != self._persisted_workspace_id:
            raise RegistryNamespaceImmutableFieldError(
                "Registry Namespace Workspace ownership is immutable.",
            )
        if (
            self._persisted_id is not None
            and self.organization_id != self._persisted_organization_id
        ):
            raise RegistryNamespaceImmutableFieldError(
                "Registry Namespace Organization ownership is immutable.",
            )
        if self._state.adding and self.pk is not None:
            persisted_workspace_id = (
                type(self).objects.filter(pk=self.pk).values_list("workspace_id", flat=True).first()
            )
            if persisted_workspace_id is not None:
                raise RegistryNamespaceImmutableFieldError(
                    "A persisted Registry Namespace cannot be replaced through ordinary save.",
                )

    def _capture_persisted_identity(self) -> None:
        self._persisted_id = self.id
        # Django's deletion collector can hydrate this aggregate with only its
        # primary key. Accessing a deferred foreign-key descriptor here triggers
        # a refresh that re-enters ``from_db`` recursively.
        self._persisted_workspace_id = self.__dict__.get("workspace_id")
        self._persisted_organization_id = self.__dict__.get("organization_id")

    def __str__(self) -> str:
        """Return the canonical namespace and immutable owner identifier."""
        owner_id = self.workspace_id if self.workspace_id is not None else self.organization_id
        return f"{self.id} ({owner_id})"


class LargeFileUploadSession(models.Model):
    """Persist one canonically authorized direct-to-storage upload session."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "organization.Organization",
        on_delete=models.PROTECT,
        related_name="resource_registry_upload_sessions",
    )
    workspace = models.ForeignKey(
        "organization.Workspace",
        on_delete=models.PROTECT,
        related_name="resource_registry_upload_sessions",
        null=True,
        blank=True,
    )
    registry_namespace = models.ForeignKey(
        RegistryNamespace,
        on_delete=models.PROTECT,
        related_name="+",
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="resource_registry_upload_sessions",
        null=True,
    )
    original_filename = models.CharField(max_length=255)
    normalized_filename = models.CharField(max_length=255)
    resolved_file_format = models.CharField(max_length=16)
    declared_content_type = models.CharField(max_length=255)
    detected_content_type = models.CharField(max_length=255, blank=True, null=True)
    declared_size_bytes = models.PositiveBigIntegerField()
    actual_size_bytes = models.PositiveBigIntegerField(blank=True, null=True)
    storage_provider = models.CharField(max_length=64)
    storage_object_key = models.CharField(max_length=1024, unique=True)
    acquisition_provider = models.CharField(max_length=64, blank=True, null=True)
    acquisition_reference_hash = models.CharField(max_length=64, blank=True, null=True)
    storage_etag = models.CharField(max_length=255, blank=True, null=True)
    storage_version_id = models.CharField(max_length=255, blank=True, null=True)
    checksum_algorithm = models.CharField(max_length=16, blank=True, null=True)
    declared_checksum = models.CharField(max_length=128, blank=True, null=True)
    provider_reported_checksum = models.CharField(max_length=128, blank=True, null=True)
    verified_checksum = models.CharField(max_length=128, blank=True, null=True)
    checksum_verification_status = models.CharField(max_length=16, default="NOT_PROVIDED")
    status = models.CharField(
        max_length=32,
        choices=LargeFileUploadStatus.choices,
        default=LargeFileUploadStatus.PENDING_UPLOAD,
    )
    failure_code = models.CharField(max_length=100, blank=True, null=True)
    failure_message_sanitized = models.CharField(max_length=255, blank=True, null=True)
    cleanup_required = models.BooleanField(default=False)
    idempotency_key = models.CharField(max_length=128)
    request_fingerprint = models.CharField(max_length=64)
    upload_authorization_expires_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    uploaded_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    cancelled_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        permissions = (("create_large_file_upload", "Can create large file uploads"),)
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["status", "expires_at"], name="upload_status_expiry_idx"),
            models.Index(fields=["workspace", "status"], name="upload_workspace_status_idx"),
            models.Index(fields=["organization", "status"], name="upload_organization_status_idx"),
            models.Index(fields=["submitted_by", "status"], name="upload_actor_status_idx"),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["organization", "submitted_by", "idempotency_key"],
                name="upload_actor_idempotency_uniq",
            ),
            models.CheckConstraint(
                condition=Q(declared_size_bytes__gt=0),
                name="upload_declared_size_positive",
            ),
        ]


class StagedResourceIntake(models.Model):
    """Persist one private, temporary upload that has no Registry owner yet."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="resource_registry_staged_resource_intakes",
    )
    original_filename = models.CharField(max_length=255)
    normalized_filename = models.CharField(max_length=255)
    resolved_file_format = models.CharField(max_length=16)
    declared_content_type = models.CharField(max_length=255)
    detected_content_type = models.CharField(max_length=255, blank=True, null=True)
    declared_size_bytes = models.PositiveBigIntegerField()
    actual_size_bytes = models.PositiveBigIntegerField(blank=True, null=True)
    storage_provider = models.CharField(max_length=64)
    storage_object_key = models.CharField(max_length=1024, unique=True)
    storage_etag = models.CharField(max_length=255, blank=True, null=True)
    storage_version_id = models.CharField(max_length=255, blank=True, null=True)
    checksum_algorithm = models.CharField(max_length=16, blank=True, null=True)
    declared_checksum = models.CharField(max_length=128, blank=True, null=True)
    provider_reported_checksum = models.CharField(max_length=128, blank=True, null=True)
    verified_checksum = models.CharField(max_length=128, blank=True, null=True)
    checksum_verification_status = models.CharField(max_length=16, default="NOT_PROVIDED")
    inspection_json = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=32,
        choices=StagedResourceIntakeStatus.choices,
        default=StagedResourceIntakeStatus.PENDING_UPLOAD,
    )
    failure_code = models.CharField(max_length=100, blank=True, null=True)
    cleanup_required = models.BooleanField(default=False)
    idempotency_key = models.CharField(max_length=128)
    request_fingerprint = models.CharField(max_length=64)
    upload_authorization_expires_at = models.DateTimeField()
    expires_at = models.DateTimeField(blank=True, null=True)
    uploaded_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    published_at = models.DateTimeField(blank=True, null=True)
    cancelled_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        permissions = (("manage_stagedresourceintake", "Can manage staged resource intakes"),)
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["submitted_by", "idempotency_key"],
                name="staged_intake_actor_idempotency_uniq",
            ),
            models.CheckConstraint(
                condition=Q(declared_size_bytes__gt=0),
                name="staged_intake_declared_size_positive",
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["submitted_by", "status"], name="staged_intake_actor_status_idx"),
            models.Index(fields=["status", "expires_at"], name="staged_intake_expiry_idx"),
        ]


class StagedResourcePublication(models.Model):
    """Bind one verified intake to one immutable destination namespace."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    intake = models.ForeignKey(
        StagedResourceIntake,
        on_delete=models.PROTECT,
        related_name="publications",
    )
    registry_namespace = models.ForeignKey(
        RegistryNamespace,
        on_delete=models.PROTECT,
        related_name="staged_resource_publications",
    )
    organization = models.ForeignKey(
        "organization.Organization",
        on_delete=models.PROTECT,
        related_name="resource_registry_staged_resource_publications",
    )
    workspace = models.ForeignKey(
        "organization.Workspace",
        on_delete=models.PROTECT,
        related_name="resource_registry_staged_resource_publications",
        null=True,
        blank=True,
    )
    visibility_scope = models.CharField(max_length=32, choices=DatasetVisibilityScope.choices)
    publication_attempt = models.PositiveSmallIntegerField(default=1)
    assignment_status = models.CharField(
        max_length=32,
        choices=StagedResourcePublicationStatus.choices,
        default=StagedResourcePublicationStatus.ACTIVE,
    )
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="resource_registry_staged_resource_publications",
    )
    upload_session = models.OneToOneField(
        LargeFileUploadSession,
        on_delete=models.PROTECT,
        related_name="staged_resource_publication",
        null=True,
        blank=True,
    )
    registration_submission = models.OneToOneField(
        "RegistrationSubmission",
        on_delete=models.PROTECT,
        related_name="staged_resource_publication",
        null=True,
        blank=True,
    )
    destination_object_key = models.CharField(max_length=1024, blank=True, null=True)
    cleanup_required = models.BooleanField(default=False)
    detachment_requested_at = models.DateTimeField(blank=True, null=True)
    detached_at = models.DateTimeField(blank=True, null=True)
    detached_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="resource_registry_detached_workbook_publications",
        null=True,
        blank=True,
    )
    detachment_reason = models.CharField(max_length=500, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(blank=True, null=True)

    _persisted_destination: tuple[int, int, int, int | None, str] | None = None

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["intake", "registry_namespace", "publication_attempt"],
                name="staged_publication_attempt_uniq",
            ),
            models.UniqueConstraint(
                fields=["intake", "registry_namespace"],
                condition=Q(
                    assignment_status__in=[
                        StagedResourcePublicationStatus.ACTIVE,
                        StagedResourcePublicationStatus.DETACH_REQUESTED,
                    ]
                ),
                name="staged_publication_active_destination_uniq",
            ),
            models.CheckConstraint(
                condition=Q(
                    Q(
                        visibility_scope=DatasetVisibilityScope.WORKSPACE,
                        workspace__isnull=False,
                    )
                    | Q(
                        visibility_scope=DatasetVisibilityScope.ORGANIZATION,
                        workspace__isnull=True,
                    )
                ),
                name="staged_publication_visibility_owner_valid",
            ),
        ]

    @classmethod
    def from_db(
        cls,
        db: str | None,
        field_names: Collection[str],
        values: Collection[object],
    ) -> StagedResourcePublication:
        """Capture immutable destination fields when hydrating a publication."""
        instance = super().from_db(db, field_names, values)
        instance._persisted_destination = instance._destination_snapshot()
        return instance

    def save(self, *args: object, **kwargs: object) -> None:
        """Persist linkage fields while rejecting destination reassignment."""
        if (
            self._persisted_destination is not None
            and self._persisted_destination != self._destination_snapshot()
        ):
            raise ValidationError("Staged resource publication destination is immutable.")
        super().save(*args, **kwargs)
        self._persisted_destination = self._destination_snapshot()

    def _destination_snapshot(self) -> tuple[int, int, int, int | None, str]:
        return (
            self.intake_id,
            self.registry_namespace_id,
            self.organization_id,
            self.workspace_id,
            str(self.visibility_scope),
        )


class StagedResourcePublicationDataset(models.Model):
    """Record one exact Dataset version made visible by a destination publication."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    publication = models.ForeignKey(
        StagedResourcePublication,
        on_delete=models.PROTECT,
        related_name="dataset_assignments",
    )
    dataset = models.ForeignKey(
        "Dataset",
        on_delete=models.PROTECT,
        related_name="staged_resource_publication_assignments",
    )
    dataset_version = models.ForeignKey(
        "DatasetVersion",
        on_delete=models.PROTECT,
        related_name="staged_resource_publication_assignments",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    detached_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["publication", "dataset_version"],
                name="staged_publication_version_uniq",
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=["dataset", "detached_at"],
                name="staged_pub_dataset_active_idx",
            ),
        ]


class StagedResourceInspectionJob(models.Model):
    """Persist one retryable inspection job without creating durable Datasets."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    intake = models.OneToOneField(
        StagedResourceIntake,
        on_delete=models.PROTECT,
        related_name="inspection_job",
    )
    status = models.CharField(
        max_length=16,
        choices=StagedResourceInspectionJobStatus.choices,
        default=StagedResourceInspectionJobStatus.QUEUED,
    )
    error_code = models.CharField(max_length=100, blank=True, null=True)
    retry_count = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["status", "created_at"], name="staged_inspection_queue_idx"),
        ]


class RegistrationSubmission(models.Model):
    """Persist one authorized request to register a completed upload."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    upload_session = models.OneToOneField(
        LargeFileUploadSession,
        on_delete=models.PROTECT,
        related_name="registration_submission",
    )
    replacement_dataset = models.ForeignKey(
        "Dataset",
        on_delete=models.PROTECT,
        related_name="replacement_submissions",
        null=True,
        blank=True,
    )
    replacement_workbook_group = models.ForeignKey(
        "WorkbookIngestionGroup",
        on_delete=models.PROTECT,
        related_name="replacement_submissions",
        null=True,
        blank=True,
    )
    organization = models.ForeignKey(
        "organization.Organization",
        on_delete=models.PROTECT,
        related_name="resource_registry_registration_submissions",
    )
    workspace = models.ForeignKey(
        "organization.Workspace",
        on_delete=models.PROTECT,
        related_name="resource_registry_registration_submissions",
        null=True,
        blank=True,
    )
    registry_namespace = models.ForeignKey(
        RegistryNamespace,
        on_delete=models.PROTECT,
        related_name="+",
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="resource_registry_registration_submissions",
        null=True,
    )
    status = models.CharField(
        max_length=32,
        choices=RegistrationSubmissionStatus.choices,
        default=RegistrationSubmissionStatus.SUBMITTED,
    )
    job_type = models.CharField(max_length=64, choices=RegistrationJobType.choices)
    visibility_scope = models.CharField(
        max_length=32,
        choices=DatasetVisibilityScope.choices,
        default=DatasetVisibilityScope.WORKSPACE,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        permissions = (
            ("submit_upload_registration", "Can submit completed uploads for registration"),
        )
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["workspace", "status"], name="regsub_workspace_status_idx"),
            models.Index(fields=["organization", "status"], name="regsub_organization_status_idx"),
            models.Index(fields=["status", "created_at"], name="regsub_status_created_idx"),
        ]


class DatasetRegistrationJob(models.Model):
    """Persist one upload-backed CSV registration execution lifecycle."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    registration_submission = models.OneToOneField(
        RegistrationSubmission,
        on_delete=models.PROTECT,
        related_name="dataset_registration_job",
    )
    tenant_id = models.UUIDField(db_index=True)
    status = models.CharField(
        max_length=32,
        choices=DatasetRegistrationJobStatus.choices,
        default=DatasetRegistrationJobStatus.SUBMITTED,
    )
    request_payload_json = models.JSONField(default=dict)
    result_payload_json = models.JSONField(default=dict, blank=True, null=True)
    error_code = models.CharField(max_length=100, blank=True, null=True)
    error_message = models.CharField(max_length=255, blank=True, null=True)
    retry_count = models.PositiveIntegerField(default=0)
    cancellation_requested_at = models.DateTimeField(blank=True, null=True, default=None)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="dataset_registration_jobs",
        null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    started_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    execution_ms = models.PositiveIntegerField(blank=True, null=True)

    class Meta:
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["status", "created_at"], name="drj_status_created_idx"),
            models.Index(fields=["tenant_id", "status"], name="drj_tenant_status_idx"),
        ]


class WorkbookIngestionGroup(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.UUIDField(db_index=True)
    source_uri = models.CharField(max_length=2048)
    source_hash = models.CharField(max_length=128)
    source_file_name = models.CharField(max_length=255, blank=True, null=True)
    source_storage_provider = models.CharField(max_length=100, blank=True, null=True)
    status = models.CharField(
        max_length=32,
        choices=WorkbookIngestionStatus.choices,
        default=WorkbookIngestionStatus.STARTED,
    )
    total_sheets = models.PositiveIntegerField(default=0)
    total_created = models.PositiveIntegerField(default=0)
    total_deduplicated = models.PositiveIntegerField(default=0)
    total_skipped = models.PositiveIntegerField(default=0)
    metadata_json = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["tenant_id", "-created_at"], name="workbook_group_recent_idx"),
            models.Index(fields=["tenant_id", "source_hash"], name="workbook_group_hash_idx"),
            models.Index(fields=["status"], name="workbook_group_status_idx"),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["tenant_id", "source_hash"],
                name="workbook_group_tenant_hash_uniq",
            ),
        ]


class Dataset(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.UUIDField(db_index=True)
    organization = models.ForeignKey(
        "organization.Organization",
        on_delete=models.PROTECT,
        related_name="resource_registry_datasets",
        null=True,
        blank=True,
    )
    workspace = models.ForeignKey(
        "organization.Workspace",
        on_delete=models.PROTECT,
        related_name="resource_registry_datasets",
        null=True,
        blank=True,
    )
    visibility_scope = models.CharField(
        max_length=32,
        choices=DatasetVisibilityScope.choices,
        default=DatasetVisibilityScope.WORKSPACE,
    )
    slug = models.SlugField(max_length=120)
    name = models.CharField(max_length=255)
    source_type = models.CharField(max_length=32, choices=DatasetSourceType.choices)
    status = models.CharField(
        max_length=20,
        choices=DatasetStatus.choices,
        default=DatasetStatus.DRAFT,
    )
    description = models.TextField(blank=True, null=True)
    owner_team = models.CharField(max_length=255, blank=True, null=True)
    tags = models.JSONField(default=list, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="datasets_created",
        null=True,
        blank=True,
    )
    workbook_group = models.ForeignKey(
        WorkbookIngestionGroup,
        on_delete=models.SET_NULL,
        related_name="datasets",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    archived_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["tenant_id", "status"], name="dataset_tenant_status_idx"),
            models.Index(fields=["tenant_id", "source_type"], name="dataset_tenant_source_idx"),
            models.Index(
                fields=["organization", "workspace", "visibility_scope"],
                name="dataset_visibility_idx",
            ),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["tenant_id", "slug"],
                name="dataset_tenant_slug_uniq",
            ),
            models.CheckConstraint(
                condition=Q(
                    Q(("organization__isnull", True), ("workspace__isnull", True)),
                    Q(
                        ("organization__isnull", False),
                        ("visibility_scope", DatasetVisibilityScope.WORKSPACE),
                        ("workspace__isnull", False),
                    ),
                    Q(
                        ("organization__isnull", False),
                        ("visibility_scope", DatasetVisibilityScope.ORGANIZATION),
                        ("workspace__isnull", True),
                    ),
                    _connector="OR",
                ),
                name="dataset_visibility_ownership_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.tenant_id})"


class DatasetVersion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="versions")
    version_number = models.PositiveIntegerField()
    source_uri = models.CharField(max_length=2048)
    content_hash = models.CharField(max_length=128)
    status = models.CharField(
        max_length=20,
        choices=DatasetVersionStatus.choices,
        default=DatasetVersionStatus.PENDING,
    )
    source_etag = models.CharField(max_length=255, blank=True, null=True)
    source_last_modified_at = models.DateTimeField(blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["dataset", "-created_at"], name="dataset_version_recent_idx"),
            models.Index(fields=["dataset", "content_hash"], name="dataset_version_hash_idx"),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["dataset", "version_number"],
                name="dataset_version_number_uniq",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding and self.pk:
            existing = DatasetVersion.objects.get(pk=self.pk)
            if existing.status == DatasetVersionStatus.READY:
                raise ValidationError("DatasetVersion is immutable once status is READY.")
        return super().save(*args, **kwargs)


class TransformationDefinition(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.UUIDField(db_index=True)
    definition_key = models.SlugField(max_length=120)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="transformation_definitions_created",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    archived_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=["tenant_id", "definition_key"],
                name="td_tenant_key_idx",
            ),
            models.Index(
                fields=["tenant_id", "archived_at"],
                name="td_tenant_archived_idx",
            ),
            models.Index(fields=["tenant_id", "-created_at"], name="td_tenant_recent_idx"),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["tenant_id", "definition_key"],
                name="td_tenant_key_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.tenant_id})"


class TransformationDefinitionVersion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    definition = models.ForeignKey(
        TransformationDefinition,
        on_delete=models.CASCADE,
        related_name="versions",
    )
    version_number = models.PositiveIntegerField()
    status = models.CharField(
        max_length=20,
        choices=TransformationDefinitionVersionStatus.choices,
        default=TransformationDefinitionVersionStatus.DRAFT,
    )
    specification_json = models.JSONField(default=dict)
    specification_checksum = models.CharField(max_length=64, editable=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="transformation_definition_versions_created",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=["definition", "version_number"],
                name="tdv_definition_version_idx",
            ),
            models.Index(fields=["definition", "status"], name="tdv_definition_status_idx"),
            models.Index(fields=["definition", "-created_at"], name="tdv_definition_recent_idx"),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["definition", "version_number"],
                name="tdv_definition_version_uniq",
            ),
            models.CheckConstraint(
                condition=Q(specification_checksum__regex=r"^[0-9a-f]{64}$"),
                name="tdv_spec_checksum_hex_chk",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.definition.definition_key} v{self.version_number}"

    def calculate_specification_checksum(self) -> str:
        """Calculate the canonical checksum for this version's specification JSON.

        Returns:
            Lowercase 64-character SHA-256 hexadecimal digest.

        Raises:
            TransformationSpecificationChecksumError: If the specification is not compatible with
                the approved canonical JSON contract.
        """
        return calculate_transformation_specification_checksum(self.specification_json)

    def refresh_specification_checksum(self) -> None:
        """Assign the canonical specification checksum without persisting the model.

        Side effects:
            Updates the in-memory `specification_checksum` attribute only.
        """
        self.specification_checksum = self.calculate_specification_checksum()


class DatasetArtifact(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset_version = models.ForeignKey(
        DatasetVersion,
        on_delete=models.CASCADE,
        related_name="artifacts",
    )
    artifact_role = models.CharField(max_length=20, choices=ArtifactRole.choices)
    uri = models.CharField(max_length=2048)
    format = models.CharField(max_length=20, choices=ArtifactFormat.choices)
    size_bytes = models.BigIntegerField(validators=[MinValueValidator(0)])
    compression = models.CharField(max_length=50, blank=True, null=True)
    checksum = models.CharField(max_length=128, blank=True, null=True)
    storage_provider = models.CharField(max_length=100, blank=True, null=True)
    lifecycle_status = models.CharField(
        max_length=32,
        choices=ArtifactLifecycleStatus.choices,
        default=ArtifactLifecycleStatus.ACTIVE,
    )
    workbook_group = models.ForeignKey(
        WorkbookIngestionGroup,
        on_delete=models.SET_NULL,
        related_name="artifacts",
        null=True,
        blank=True,
    )
    sheet_name = models.CharField(max_length=255, blank=True, null=True)
    source_artifact = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        related_name="derived_artifacts",
        null=True,
        blank=True,
    )
    metadata_json = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=["dataset_version", "artifact_role"],
                name="dataset_artifact_role_idx",
            ),
            models.Index(fields=["format"], name="dataset_artifact_format_idx"),
            models.Index(fields=["lifecycle_status"], name="dataset_artifact_lifecycle_idx"),
            models.Index(fields=["workbook_group"], name="dataset_artifact_workbook_idx"),
            models.Index(fields=["sheet_name"], name="dataset_artifact_sheet_idx"),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["dataset_version", "artifact_role", "uri"],
                name="dataset_artifact_role_uri_uniq",
            ),
        ]


class DatasetSchema(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset_version = models.ForeignKey(
        DatasetVersion,
        on_delete=models.CASCADE,
        related_name="schemas",
    )
    schema_hash = models.CharField(max_length=128)
    column_count = models.PositiveIntegerField()
    schema_json = models.JSONField(default=list)
    inference_engine = models.CharField(max_length=100, blank=True, null=True)
    inference_version = models.CharField(max_length=100, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["dataset_version"], name="dataset_schema_version_idx"),
            models.Index(fields=["schema_hash"], name="dataset_schema_hash_idx"),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["dataset_version", "schema_hash"],
                name="dataset_schema_version_hash_uniq",
            ),
        ]


class DatasetProfile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset_version = models.ForeignKey(
        DatasetVersion,
        on_delete=models.CASCADE,
        related_name="profiles",
    )
    profile_type = models.CharField(max_length=30, choices=ProfileType.choices)
    profile_hash = models.CharField(max_length=128)
    metrics_json = models.JSONField(default=dict)
    sample_size = models.PositiveIntegerField(blank=True, null=True)
    runtime_ms = models.PositiveIntegerField(blank=True, null=True)
    profiler_version = models.CharField(max_length=100, blank=True, null=True)
    computed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=["dataset_version", "-computed_at"],
                name="dataset_profile_recent_idx",
            ),
            models.Index(fields=["profile_type"], name="dataset_profile_type_idx"),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["dataset_version", "profile_type", "profile_hash"],
                name="dataset_profile_hash_uniq",
            ),
        ]


class OutboxEntry(models.Model):
    """Persist one Dataset-owned integration event awaiting later publication."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event_id = models.CharField(max_length=128, unique=True, editable=False)
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="outbox_entries")
    namespace_id = models.UUIDField(editable=False)
    sequence = models.PositiveBigIntegerField(editable=False)
    event_type = models.CharField(max_length=128, editable=False)
    event_version = models.PositiveIntegerField(editable=False)
    source = models.CharField(max_length=255, editable=False)
    subject = models.JSONField(editable=False)
    payload = models.JSONField(editable=False)
    canonical_event = models.TextField(editable=False)
    checksum = models.CharField(max_length=64, editable=False)
    status = models.CharField(
        max_length=16,
        choices=OutboxEntryStatus.choices,
        default=OutboxEntryStatus.PENDING,
    )
    claim_owner = models.CharField(max_length=128, blank=True, null=True)
    claim_token = models.UUIDField(blank=True, null=True)
    lease_expires_at = models.DateTimeField(blank=True, null=True)
    attempt_count = models.PositiveIntegerField(default=0)
    published_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["namespace_id", "dataset", "sequence"],
                name="dataset_outbox_stream_sequence_uniq",
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["namespace_id", "dataset", "sequence"]),
            models.Index(fields=["status", "created_at"]),
        ]


class EventPublication(models.Model):
    """Record one append-only dispatcher publication acknowledgement."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    outbox_entry = models.ForeignKey(
        OutboxEntry, on_delete=models.PROTECT, related_name="publications"
    )
    claim_token = models.UUIDField(editable=False)
    broker_position = models.CharField(max_length=255, blank=True, null=True)
    archive_uri = models.CharField(max_length=1024, editable=False)
    archive_checksum = models.CharField(max_length=64, editable=False)
    archived_at = models.DateTimeField(editable=False)
    acknowledged_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["outbox_entry", "claim_token"], name="event_publication_claim_uniq"
            )
        ]


class DatasetLineage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    downstream_dataset_version = models.ForeignKey(
        DatasetVersion,
        on_delete=models.CASCADE,
        related_name="downstream_lineage",
    )
    upstream_dataset_version = models.ForeignKey(
        DatasetVersion,
        on_delete=models.CASCADE,
        related_name="upstream_lineage",
        blank=True,
        null=True,
    )
    upstream_external_uri = models.CharField(max_length=2048, blank=True, null=True)
    lineage_type = models.CharField(max_length=30, choices=LineageType.choices)
    transformation_name = models.CharField(max_length=255, blank=True, null=True)
    transformation_hash = models.CharField(max_length=128, blank=True, null=True)
    job_id = models.UUIDField(blank=True, null=True)
    metadata_json = models.JSONField(default=dict, blank=True)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=["downstream_dataset_version"],
                name="dataset_lineage_downstream_idx",
            ),
            models.Index(fields=["upstream_dataset_version"], name="dataset_lineage_upstream_idx"),
            models.Index(fields=["-recorded_at"], name="dataset_lineage_recent_idx"),
            models.Index(fields=["job_id"], name="dataset_lineage_job_idx"),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=(
                    Q(
                        upstream_dataset_version__isnull=False,
                        upstream_external_uri__isnull=True,
                    )
                    | Q(
                        upstream_dataset_version__isnull=True,
                        upstream_external_uri__isnull=False,
                    )
                ),
                name="dataset_lineage_exact_upstream",
            ),
            models.UniqueConstraint(
                fields=[
                    "downstream_dataset_version",
                    "upstream_dataset_version",
                    "transformation_hash",
                ],
                name="dataset_lineage_edge_uniq",
            ),
            models.UniqueConstraint(
                fields=[
                    "downstream_dataset_version",
                    "upstream_dataset_version",
                    "job_id",
                ],
                condition=Q(
                    upstream_dataset_version__isnull=False,
                    job_id__isnull=False,
                ),
                name="dataset_lineage_run_edge_uniq",
            ),
        ]


class GovernancePolicy(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.UUIDField(db_index=True)
    policy_key = models.SlugField(max_length=120)
    revision = models.PositiveIntegerField()
    policy_type = models.CharField(max_length=30, choices=GovernancePolicyType.choices)
    rules_json = models.JSONField(default=dict)
    rules_checksum = models.CharField(max_length=64, blank=True, null=True, editable=False)
    is_active = models.BooleanField(default=True)
    is_tenant_default = models.BooleanField(default=False)
    description = models.TextField(blank=True, null=True)
    effective_from = models.DateTimeField(blank=True, null=True)
    effective_to = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["tenant_id", "policy_key"], name="governance_policy_key_idx"),
            models.Index(fields=["tenant_id", "is_active"], name="governance_policy_active_idx"),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["tenant_id", "policy_key", "revision"],
                name="governance_policy_revision_uniq",
            ),
            models.UniqueConstraint(
                fields=["tenant_id", "policy_key"],
                condition=Q(is_active=True, is_tenant_default=True),
                name="governance_tenant_default_active_uniq",
            ),
            models.CheckConstraint(
                condition=Q(rules_checksum__isnull=True)
                | Q(rules_checksum__regex=r"^[0-9a-f]{64}$"),
                name="governance_rules_checksum_chk",
            ),
        ]


class DatasetPolicyAssignment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(
        Dataset,
        on_delete=models.CASCADE,
        related_name="policy_assignments",
    )
    governance_policy = models.ForeignKey(
        GovernancePolicy,
        on_delete=models.CASCADE,
        related_name="dataset_assignments",
    )
    assignment_type = models.CharField(
        max_length=20,
        choices=DatasetPolicyAssignmentType.choices,
        default=DatasetPolicyAssignmentType.OVERRIDE,
    )
    assigned_at = models.DateTimeField(auto_now_add=True)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="dataset_policy_assignments",
        null=True,
        blank=True,
    )

    class Meta:
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["dataset"], name="dataset_policy_dataset_idx"),
            models.Index(fields=["governance_policy"], name="dataset_policy_policy_idx"),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["dataset", "governance_policy"],
                name="dataset_policy_assignment_uniq",
            ),
        ]


class ExternalDatasetJob(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="external_jobs")
    job_type = models.CharField(max_length=20, choices=ExternalDatasetJobType.choices)
    status = models.CharField(
        max_length=20,
        choices=ExternalDatasetJobStatus.choices,
        default=ExternalDatasetJobStatus.QUEUED,
    )
    provider = models.CharField(max_length=100, blank=True, null=True)
    provider_job_ref = models.CharField(max_length=255, blank=True, null=True)
    requested_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(blank=True, null=True)
    finished_at = models.DateTimeField(blank=True, null=True)
    retry_count = models.PositiveIntegerField(default=0)
    error_code = models.CharField(max_length=100, blank=True, null=True)
    error_message = models.TextField(blank=True, null=True)
    request_payload_json = models.JSONField(default=dict, blank=True)
    output_dataset_version = models.ForeignKey(
        DatasetVersion,
        on_delete=models.SET_NULL,
        related_name="generated_by_jobs",
        blank=True,
        null=True,
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["dataset", "status"], name="extjob_dataset_status_idx"),
            models.Index(fields=["job_type", "status"], name="extjob_type_status_idx"),
            models.Index(fields=["-requested_at"], name="extjob_requested_idx"),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["provider", "provider_job_ref"],
                condition=Q(provider_job_ref__isnull=False),
                name="external_job_provider_ref_uniq",
            ),
        ]


class DatasetQueryRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.UUIDField(db_index=True)
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="query_runs")
    version = models.ForeignKey(
        DatasetVersion,
        on_delete=models.SET_NULL,
        related_name="query_runs",
        blank=True,
        null=True,
    )
    sql_hash = models.CharField(max_length=128)
    sql_text = models.TextField()
    status = models.CharField(
        max_length=20,
        choices=DatasetIntelligenceStatus.choices,
        default=DatasetIntelligenceStatus.SUCCEEDED,
    )
    row_count = models.PositiveIntegerField(default=0)
    truncated = models.BooleanField(default=False)
    execution_ms = models.PositiveIntegerField(blank=True, null=True)
    error_code = models.CharField(max_length=100, blank=True, null=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="dataset_query_runs",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    metadata_json = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["tenant_id", "-created_at"], name="dqr_tenant_recent_idx"),
            models.Index(fields=["dataset", "-created_at"], name="dqr_dataset_recent_idx"),
            models.Index(fields=["version", "-created_at"], name="dqr_version_recent_idx"),
            models.Index(fields=["status", "-created_at"], name="dqr_status_recent_idx"),
        ]


class DatasetQueryJob(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.UUIDField(db_index=True)
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="query_jobs")
    version = models.ForeignKey(
        DatasetVersion,
        on_delete=models.SET_NULL,
        related_name="query_jobs",
        blank=True,
        null=True,
    )
    sql_hash = models.CharField(max_length=128)
    sql_text = models.TextField()
    status = models.CharField(
        max_length=20,
        choices=DatasetQueryJobStatus.choices,
        default=DatasetQueryJobStatus.QUEUED,
    )
    result_columns = models.JSONField(default=list, blank=True, null=True)
    result_rows = models.JSONField(default=list, blank=True, null=True)
    row_count = models.PositiveIntegerField(blank=True, null=True)
    truncated = models.BooleanField(default=False)
    error_code = models.CharField(max_length=100, blank=True, null=True)
    error_message = models.TextField(blank=True, null=True)
    retry_count = models.PositiveIntegerField(default=0)
    cancellation_requested_at = models.DateTimeField(blank=True, null=True, default=None)
    idempotency_key = models.CharField(max_length=128, blank=True, null=True, db_index=True)
    request_fingerprint = models.CharField(max_length=64, blank=True, null=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="dataset_query_jobs",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    execution_ms = models.PositiveIntegerField(blank=True, null=True)
    metadata_json = models.JSONField(default=dict, blank=True, null=True)

    class Meta:
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["tenant_id", "-created_at"], name="dqj_tenant_recent_idx"),
            models.Index(fields=["dataset", "-created_at"], name="dqj_dataset_recent_idx"),
            models.Index(fields=["version", "-created_at"], name="dqj_version_recent_idx"),
            models.Index(fields=["status", "-created_at"], name="dqj_status_recent_idx"),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["tenant_id", "request_fingerprint"],
                condition=Q(
                    request_fingerprint__isnull=False,
                    status__in=[
                        DatasetQueryJobStatus.QUEUED,
                        DatasetQueryJobStatus.RUNNING,
                    ],
                ),
                name="dqj_active_fingerprint_uniq",
            ),
        ]


class DatasetIntelligenceJob(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.UUIDField(db_index=True)
    operation = models.CharField(
        max_length=40,
        choices=DatasetIntelligenceJobOperation.choices,
    )
    dataset = models.ForeignKey(
        Dataset,
        on_delete=models.SET_NULL,
        related_name="intelligence_jobs",
        blank=True,
        null=True,
    )
    version = models.ForeignKey(
        DatasetVersion,
        on_delete=models.SET_NULL,
        related_name="intelligence_jobs",
        blank=True,
        null=True,
    )
    export_plan = models.ForeignKey(
        "DatasetExportPlan",
        on_delete=models.SET_NULL,
        related_name="intelligence_jobs",
        blank=True,
        null=True,
    )
    status = models.CharField(
        max_length=20,
        choices=DatasetIntelligenceJobStatus.choices,
        default=DatasetIntelligenceJobStatus.QUEUED,
    )
    request_payload_json = models.JSONField(default=dict, blank=True)
    job_spec_json = models.JSONField(default=dict, blank=True, null=True)
    decision_trace_json = models.JSONField(default=dict, blank=True, null=True)
    logical_result_json = models.JSONField(default=dict, blank=True, null=True)
    result_payload_json = models.JSONField(default=dict, blank=True, null=True)
    error_code = models.CharField(max_length=100, blank=True, null=True)
    error_message = models.TextField(blank=True, null=True)
    retry_count = models.PositiveIntegerField(default=0)
    cancellation_requested_at = models.DateTimeField(blank=True, null=True, default=None)
    idempotency_key = models.CharField(max_length=128, blank=True, null=True, db_index=True)
    request_fingerprint = models.CharField(max_length=64, blank=True, null=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="dataset_intelligence_jobs",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    execution_ms = models.PositiveIntegerField(blank=True, null=True)
    metadata_json = models.JSONField(default=dict, blank=True, null=True)

    class Meta:
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["tenant_id", "-created_at"], name="dij_tenant_recent_idx"),
            models.Index(fields=["operation", "-created_at"], name="dij_operation_recent_idx"),
            models.Index(fields=["status", "-created_at"], name="dij_status_recent_idx"),
            models.Index(fields=["dataset", "-created_at"], name="dij_dataset_recent_idx"),
            models.Index(fields=["version", "-created_at"], name="dij_version_recent_idx"),
            models.Index(fields=["export_plan", "-created_at"], name="dij_export_plan_recent_idx"),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["tenant_id", "request_fingerprint"],
                condition=Q(
                    request_fingerprint__isnull=False,
                    status__in=[
                        DatasetIntelligenceJobStatus.QUEUED,
                        DatasetIntelligenceJobStatus.RUNNING,
                    ],
                ),
                name="dij_active_fingerprint_uniq",
            ),
        ]


class WorkbookIngestionJob(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    registration_submission = models.OneToOneField(
        RegistrationSubmission,
        on_delete=models.PROTECT,
        related_name="workbook_ingestion_job",
        blank=True,
        null=True,
    )
    tenant_id = models.UUIDField(db_index=True)
    workbook_group = models.ForeignKey(
        WorkbookIngestionGroup,
        on_delete=models.SET_NULL,
        related_name="ingestion_jobs",
        blank=True,
        null=True,
    )
    status = models.CharField(
        max_length=40,
        choices=WorkbookIngestionJobStatus.choices,
        default=WorkbookIngestionJobStatus.QUEUED,
    )
    request_payload_json = models.JSONField(default=dict, blank=True)
    result_payload_json = models.JSONField(default=dict, blank=True, null=True)
    error_code = models.CharField(max_length=100, blank=True, null=True)
    error_message = models.TextField(blank=True, null=True)
    retry_count = models.PositiveIntegerField(default=0)
    cancellation_requested_at = models.DateTimeField(blank=True, null=True, default=None)
    idempotency_key = models.CharField(max_length=128, blank=True, null=True, db_index=True)
    request_fingerprint = models.CharField(max_length=64, blank=True, null=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="dataset_workbook_ingestion_jobs",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    execution_ms = models.PositiveIntegerField(blank=True, null=True)
    metadata_json = models.JSONField(default=dict, blank=True, null=True)

    class Meta:
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["tenant_id", "-created_at"], name="wbij_tenant_recent_idx"),
            models.Index(fields=["status", "-created_at"], name="wbij_status_recent_idx"),
            models.Index(fields=["workbook_group", "-created_at"], name="wbij_group_recent_idx"),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["tenant_id", "request_fingerprint"],
                condition=Q(
                    request_fingerprint__isnull=False,
                    status__in=[
                        WorkbookIngestionJobStatus.QUEUED,
                        WorkbookIngestionJobStatus.RUNNING,
                    ],
                ),
                name="wbij_active_fprint_uniq",
            ),
        ]


class DatasetAnalysisArtifact(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.UUIDField(db_index=True)
    dataset = models.ForeignKey(
        Dataset,
        on_delete=models.CASCADE,
        related_name="analysis_artifacts",
    )
    version = models.ForeignKey(
        DatasetVersion,
        on_delete=models.SET_NULL,
        related_name="analysis_artifacts",
        blank=True,
        null=True,
    )
    analysis_type = models.CharField(max_length=100)
    title = models.CharField(max_length=255)
    summary = models.TextField(blank=True, null=True)
    insights_json = models.JSONField(default=list, blank=True)
    queries_used_json = models.JSONField(default=list, blank=True)
    status = models.CharField(
        max_length=20,
        choices=DatasetIntelligenceStatus.choices,
        default=DatasetIntelligenceStatus.SUCCEEDED,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="dataset_analysis_artifacts",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    metadata_json = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["tenant_id", "-created_at"], name="daa_tenant_recent_idx"),
            models.Index(fields=["dataset", "-created_at"], name="daa_dataset_recent_idx"),
            models.Index(fields=["version", "-created_at"], name="daa_version_recent_idx"),
            models.Index(fields=["analysis_type", "-created_at"], name="daa_type_recent_idx"),
        ]


class DatasetChartArtifact(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.UUIDField(db_index=True)
    dataset = models.ForeignKey(
        Dataset,
        on_delete=models.CASCADE,
        related_name="chart_artifacts",
    )
    version = models.ForeignKey(
        DatasetVersion,
        on_delete=models.SET_NULL,
        related_name="chart_artifacts",
        blank=True,
        null=True,
    )
    chart_type = models.CharField(max_length=50)
    title = models.CharField(max_length=255)
    chart_spec_json = models.JSONField(default=dict)
    source_query_json = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=20,
        choices=DatasetIntelligenceStatus.choices,
        default=DatasetIntelligenceStatus.SUCCEEDED,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="dataset_chart_artifacts",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    metadata_json = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["tenant_id", "-created_at"], name="dca_tenant_recent_idx"),
            models.Index(fields=["dataset", "-created_at"], name="dca_dataset_recent_idx"),
            models.Index(fields=["version", "-created_at"], name="dca_version_recent_idx"),
            models.Index(fields=["chart_type", "-created_at"], name="dca_type_recent_idx"),
        ]


class DatasetExportPlan(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.UUIDField(db_index=True)
    dataset = models.ForeignKey(
        Dataset,
        on_delete=models.CASCADE,
        related_name="export_plans",
    )
    version = models.ForeignKey(
        DatasetVersion,
        on_delete=models.SET_NULL,
        related_name="export_plans",
        blank=True,
        null=True,
    )
    artifact_type = models.CharField(max_length=20)
    title = models.CharField(max_length=255)
    export_plan_json = models.JSONField(default=dict)
    status = models.CharField(
        max_length=20,
        choices=DatasetIntelligenceStatus.choices,
        default=DatasetIntelligenceStatus.SUCCEEDED,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="dataset_export_plans",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    metadata_json = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["tenant_id", "-created_at"], name="dep_tenant_recent_idx"),
            models.Index(fields=["dataset", "-created_at"], name="dep_dataset_recent_idx"),
            models.Index(fields=["version", "-created_at"], name="dep_version_recent_idx"),
            models.Index(fields=["artifact_type", "-created_at"], name="dep_type_recent_idx"),
        ]


class DatasetGeneratedArtifact(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.UUIDField(db_index=True)
    dataset = models.ForeignKey(
        Dataset,
        on_delete=models.CASCADE,
        related_name="generated_artifacts",
    )
    version = models.ForeignKey(
        DatasetVersion,
        on_delete=models.SET_NULL,
        related_name="generated_artifacts",
        blank=True,
        null=True,
    )
    export_plan = models.ForeignKey(
        DatasetExportPlan,
        on_delete=models.CASCADE,
        related_name="generated_artifacts",
    )
    artifact_type = models.CharField(max_length=20)
    filename = models.CharField(max_length=255)
    storage_uri = models.CharField(max_length=2048)
    mime_type = models.CharField(max_length=255)
    file_size_bytes = models.BigIntegerField(validators=[MinValueValidator(0)])
    checksum = models.CharField(max_length=128, blank=True, null=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="dataset_generated_artifacts",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    metadata_json = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["tenant_id", "-created_at"], name="dga_tenant_recent_idx"),
            models.Index(fields=["dataset", "-created_at"], name="dga_dataset_recent_idx"),
            models.Index(fields=["version", "-created_at"], name="dga_version_recent_idx"),
            models.Index(fields=["export_plan", "-created_at"], name="dga_plan_recent_idx"),
            models.Index(fields=["artifact_type", "-created_at"], name="dga_type_recent_idx"),
        ]


class TransformationRun(models.Model):
    """Persist one transformation execution request as its durable asynchronous job."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.UUIDField(db_index=True)
    definition = models.ForeignKey(
        TransformationDefinition,
        on_delete=models.PROTECT,
        related_name="runs",
    )
    definition_version = models.ForeignKey(
        TransformationDefinitionVersion,
        on_delete=models.PROTECT,
        related_name="runs",
    )
    specification_checksum = models.CharField(max_length=64, editable=False)
    plan_contract_version = models.CharField(max_length=32, editable=False)
    dsl_version = models.CharField(max_length=32, editable=False)
    execution_plan_snapshot_json = models.JSONField(editable=False)
    execution_plan_checksum = models.CharField(max_length=64, editable=False)
    authorization_contract_version = models.CharField(
        max_length=64,
        blank=True,
        null=True,
        editable=False,
    )
    authorization_snapshot_json = models.JSONField(blank=True, null=True, editable=False)
    authorization_checksum = models.CharField(
        max_length=64,
        blank=True,
        null=True,
        editable=False,
    )
    authorization_policy_revision = models.PositiveIntegerField(
        blank=True,
        null=True,
        editable=False,
    )
    governance_policy_revision = models.PositiveIntegerField(
        blank=True,
        null=True,
        editable=False,
    )
    authorized_actor = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        editable=False,
    )
    authorized_at = models.DateTimeField(blank=True, null=True, editable=False)
    request_fingerprint = models.CharField(max_length=64, db_index=True, editable=False)
    idempotency_key = models.CharField(
        max_length=128,
        blank=True,
        null=True,
        editable=False,
    )
    status = models.CharField(
        max_length=20,
        choices=TransformationRunStatus.choices,
        default=TransformationRunStatus.QUEUED,
    )
    latest_attempt_number = models.PositiveIntegerField(default=0)
    current_attempt_token = models.UUIDField(blank=True, null=True, editable=False)
    claim_owner = models.CharField(max_length=128, blank=True, null=True, editable=False)
    claimed_at = models.DateTimeField(blank=True, null=True, editable=False)
    heartbeat_at = models.DateTimeField(blank=True, null=True, editable=False)
    lease_expires_at = models.DateTimeField(blank=True, null=True, editable=False)
    attempt_deadline_at = models.DateTimeField(blank=True, null=True, editable=False)
    retry_count = models.PositiveIntegerField(default=0)
    current_finalization_token = models.UUIDField(blank=True, null=True, editable=False)
    finalization_owner = models.CharField(
        max_length=128,
        blank=True,
        null=True,
        editable=False,
    )
    finalization_claimed_at = models.DateTimeField(blank=True, null=True, editable=False)
    finalization_heartbeat_at = models.DateTimeField(blank=True, null=True, editable=False)
    finalization_lease_expires_at = models.DateTimeField(blank=True, null=True, editable=False)
    finalization_deadline_at = models.DateTimeField(blank=True, null=True, editable=False)
    finalization_attempt_number = models.PositiveIntegerField(default=0)
    finalization_retry_count = models.PositiveIntegerField(default=0)
    finalization_tenant_slot = models.PositiveIntegerField(blank=True, null=True, editable=False)
    cancellation_requested_at = models.DateTimeField(blank=True, null=True, default=None)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="transformation_runs",
        null=True,
        blank=True,
        editable=False,
    )
    correlation_id = models.CharField(max_length=128, db_index=True, editable=False)
    error_code = models.CharField(max_length=100, blank=True, null=True)
    error_message = models.TextField(blank=True, null=True)
    error_metadata_json = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    started_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    execution_ms = models.PositiveBigIntegerField(blank=True, null=True)

    class Meta:
        permissions: ClassVar[list[tuple[str, str]]] = [
            ("execute_transformation", "Can execute a transformation"),
            ("cancel_transformationrun", "Can cancel a transformation run"),
            ("use_transformation_provider", "Can use transformation providers"),
            ("operate_transformationrun", "Can operate transformation runs"),
            ("view_transformation_diagnostics", "Can view transformation diagnostics"),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=["tenant_id", "status", "created_at"],
                name="tr_tenant_status_created_idx",
            ),
            models.Index(fields=["status", "created_at"], name="tr_status_created_idx"),
            models.Index(
                fields=["status", "lease_expires_at"],
                name="tr_status_lease_idx",
            ),
            models.Index(fields=["claim_owner", "status"], name="tr_owner_status_idx"),
            models.Index(
                fields=["status", "finalization_lease_expires_at"],
                name="tr_fin_status_lease_idx",
            ),
            models.Index(
                fields=["finalization_owner", "status"],
                name="tr_fin_owner_status_idx",
            ),
            models.Index(
                fields=["definition", "definition_version", "created_at"],
                name="tr_definition_recent_idx",
            ),
            models.Index(
                fields=["tenant_id", "correlation_id"],
                name="tr_tenant_correlation_idx",
            ),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["tenant_id", "request_fingerprint"],
                condition=Q(
                    status__in=[
                        TransformationRunStatus.QUEUED,
                        TransformationRunStatus.RUNNING,
                        TransformationRunStatus.OUTPUT_PENDING,
                    ],
                ),
                name="tr_active_fingerprint_uniq",
            ),
            models.UniqueConstraint(
                fields=["tenant_id", "idempotency_key"],
                condition=Q(idempotency_key__isnull=False),
                name="tr_tenant_idempotency_uniq",
            ),
            models.CheckConstraint(
                condition=Q(specification_checksum__regex=r"^[0-9a-f]{64}$"),
                name="tr_spec_checksum_hex_chk",
            ),
            models.CheckConstraint(
                condition=Q(execution_plan_checksum__regex=r"^[0-9a-f]{64}$"),
                name="tr_plan_checksum_hex_chk",
            ),
            models.CheckConstraint(
                condition=Q(request_fingerprint__regex=r"^[0-9a-f]{64}$"),
                name="tr_request_fingerprint_hex_chk",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        authorization_contract_version__isnull=True,
                        authorization_snapshot_json__isnull=True,
                        authorization_checksum__isnull=True,
                        authorization_policy_revision__isnull=True,
                        governance_policy_revision__isnull=True,
                        authorized_actor__isnull=True,
                        authorized_at__isnull=True,
                    )
                    | Q(
                        authorization_contract_version__isnull=False,
                        authorization_snapshot_json__isnull=False,
                        authorization_checksum__isnull=False,
                        authorization_policy_revision__isnull=False,
                        governance_policy_revision__isnull=False,
                        authorized_actor__isnull=False,
                        authorized_at__isnull=False,
                    )
                ),
                name="tr_authorization_bundle_chk",
            ),
            models.CheckConstraint(
                condition=Q(authorization_checksum__isnull=True)
                | Q(authorization_checksum__regex=r"^[0-9a-f]{64}$"),
                name="tr_authorization_checksum_chk",
            ),
            models.CheckConstraint(
                condition=Q(retry_count__lte=models.F("latest_attempt_number")),
                name="tr_retry_attempt_count_chk",
            ),
            models.CheckConstraint(
                condition=~Q(status=TransformationRunStatus.RUNNING)
                | Q(latest_attempt_number__gte=1),
                name="tr_running_attempt_chk",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        status=TransformationRunStatus.RUNNING,
                        current_attempt_token__isnull=False,
                    )
                    | Q(
                        status__in=[
                            TransformationRunStatus.QUEUED,
                            TransformationRunStatus.SUCCEEDED,
                            TransformationRunStatus.OUTPUT_PENDING,
                            TransformationRunStatus.FAILED,
                            TransformationRunStatus.CANCELLED,
                        ],
                        current_attempt_token__isnull=True,
                    )
                ),
                name="tr_attempt_token_state_chk",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        status=TransformationRunStatus.RUNNING,
                        claim_owner__isnull=False,
                        claimed_at__isnull=False,
                        heartbeat_at__isnull=False,
                        lease_expires_at__isnull=False,
                    )
                    | Q(
                        status__in=[
                            TransformationRunStatus.QUEUED,
                            TransformationRunStatus.OUTPUT_PENDING,
                            TransformationRunStatus.SUCCEEDED,
                            TransformationRunStatus.FAILED,
                            TransformationRunStatus.CANCELLED,
                        ],
                        claim_owner__isnull=True,
                        claimed_at__isnull=True,
                        heartbeat_at__isnull=True,
                        lease_expires_at__isnull=True,
                        attempt_deadline_at__isnull=True,
                    )
                ),
                name="tr_claim_state_chk",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        claimed_at__isnull=True,
                        heartbeat_at__isnull=True,
                        lease_expires_at__isnull=True,
                    )
                    | Q(
                        claimed_at__lte=models.F("heartbeat_at"),
                        heartbeat_at__lt=models.F("lease_expires_at"),
                    )
                ),
                name="tr_claim_timestamps_chk",
            ),
            models.CheckConstraint(
                condition=Q(claim_owner__isnull=True) | Q(claim_owner__regex=r"^\S(?:.*\S)?$"),
                name="tr_claim_owner_shape_chk",
            ),
            models.CheckConstraint(
                condition=Q(attempt_deadline_at__isnull=True)
                | Q(attempt_deadline_at__gt=models.F("claimed_at")),
                name="tr_attempt_deadline_chk",
            ),
            models.CheckConstraint(
                condition=~Q(status=TransformationRunStatus.OUTPUT_PENDING)
                | Q(latest_attempt_number__gte=1),
                name="tr_output_pending_attempt_chk",
            ),
            models.CheckConstraint(
                condition=Q(finalization_retry_count__lte=models.F("finalization_attempt_number")),
                name="tr_fin_retry_attempt_chk",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        status=TransformationRunStatus.OUTPUT_PENDING,
                        current_finalization_token__isnull=False,
                        finalization_owner__isnull=False,
                        finalization_claimed_at__isnull=False,
                        finalization_heartbeat_at__isnull=False,
                        finalization_lease_expires_at__isnull=False,
                        finalization_deadline_at__isnull=False,
                        finalization_tenant_slot__isnull=False,
                        finalization_attempt_number__gte=1,
                    )
                    | Q(
                        current_finalization_token__isnull=True,
                        finalization_owner__isnull=True,
                        finalization_claimed_at__isnull=True,
                        finalization_heartbeat_at__isnull=True,
                        finalization_lease_expires_at__isnull=True,
                        finalization_deadline_at__isnull=True,
                        finalization_tenant_slot__isnull=True,
                    )
                ),
                name="tr_fin_claim_state_chk",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        finalization_claimed_at__isnull=True,
                        finalization_heartbeat_at__isnull=True,
                        finalization_lease_expires_at__isnull=True,
                    )
                    | Q(
                        finalization_claimed_at__lte=models.F("finalization_heartbeat_at"),
                        finalization_heartbeat_at__lt=models.F(
                            "finalization_lease_expires_at",
                        ),
                        finalization_lease_expires_at__lte=models.F(
                            "finalization_deadline_at",
                        ),
                    )
                ),
                name="tr_fin_claim_times_chk",
            ),
            models.CheckConstraint(
                condition=Q(finalization_owner__isnull=True)
                | Q(finalization_owner__regex=r"^\S(?:.*\S)?$"),
                name="tr_fin_owner_shape_chk",
            ),
            models.UniqueConstraint(
                fields=["tenant_id", "finalization_tenant_slot"],
                condition=Q(current_finalization_token__isnull=False),
                name="tr_fin_tenant_slot_uniq",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        status__in=[
                            TransformationRunStatus.QUEUED,
                            TransformationRunStatus.RUNNING,
                            TransformationRunStatus.OUTPUT_PENDING,
                        ],
                        completed_at__isnull=True,
                    )
                    | Q(
                        status__in=[
                            TransformationRunStatus.SUCCEEDED,
                            TransformationRunStatus.FAILED,
                            TransformationRunStatus.CANCELLED,
                        ],
                        completed_at__isnull=False,
                    )
                ),
                name="tr_status_completion_chk",
            ),
        ]


class TransformationRunStepAttempt(models.Model):
    """Persist one plan step within one real transformation run attempt."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(
        TransformationRun,
        on_delete=models.CASCADE,
        related_name="step_attempts",
    )
    run_attempt_number = models.PositiveIntegerField()
    run_attempt_token = models.UUIDField(db_index=True)
    step_index = models.PositiveIntegerField()
    step_id = models.CharField(max_length=255)
    provider_key = models.CharField(max_length=255)
    provider_version = models.CharField(max_length=255)
    operation_key = models.CharField(max_length=255)
    operation_version = models.CharField(max_length=255)
    status = models.CharField(
        max_length=20,
        choices=TransformationRunStepAttemptStatus.choices,
        default=TransformationRunStepAttemptStatus.QUEUED,
    )
    checkpoint_inputs_json = models.JSONField(default=list, blank=True)
    checkpoint_outputs_json = models.JSONField(default=list, blank=True)
    checkpoint_contract_version = models.CharField(max_length=16, default="1.0", editable=False)
    plan_checksum = models.CharField(max_length=64, editable=False)
    capability_checksum = models.CharField(max_length=64, editable=False)
    checkpoint_input_checksum = models.CharField(max_length=64, blank=True, null=True)
    checkpoint_output_checksum = models.CharField(max_length=64, blank=True, null=True)
    checkpoint_checksum = models.CharField(max_length=64, blank=True, null=True)
    attempt_idempotency_token = models.CharField(max_length=64, editable=False)
    side_effect_disposition = models.CharField(
        max_length=20,
        choices=TransformationRunStepSideEffectDisposition.choices,
        default=TransformationRunStepSideEffectDisposition.NOT_STARTED,
    )
    reused_from_step_attempt = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        related_name="reuse_attempts",
        blank=True,
        null=True,
    )
    invocation_may_have_started = models.BooleanField(default=False)
    is_retryable = models.BooleanField(blank=True, null=True)
    error_code = models.CharField(max_length=100, blank=True, null=True)
    error_classification = models.CharField(max_length=100, blank=True, null=True)
    error_message = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    execution_ms = models.PositiveBigIntegerField(blank=True, null=True)

    class Meta:
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=["run", "run_attempt_number", "status"],
                name="trsa_run_attempt_status_idx",
            ),
            models.Index(
                fields=["run", "step_id", "-run_attempt_number"],
                name="trsa_run_step_recent_idx",
            ),
            models.Index(fields=["status", "created_at"], name="trsa_status_created_idx"),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["run", "run_attempt_number", "step_index"],
                name="trsa_run_attempt_index_uniq",
            ),
            models.UniqueConstraint(
                fields=["run", "run_attempt_number", "step_id"],
                name="trsa_run_attempt_step_uniq",
            ),
            models.CheckConstraint(
                condition=Q(run_attempt_number__gte=1),
                name="trsa_attempt_positive_chk",
            ),
            models.CheckConstraint(
                condition=Q(checkpoint_checksum__isnull=True)
                | Q(checkpoint_checksum__regex=r"^[0-9a-f]{64}$"),
                name="trsa_checkpoint_hex_chk",
            ),
            models.CheckConstraint(
                condition=Q(plan_checksum__regex=r"^[0-9a-f]{64}$"),
                name="trsa_plan_checksum_hex_chk",
            ),
            models.CheckConstraint(
                condition=Q(capability_checksum__regex=r"^[0-9a-f]{64}$"),
                name="trsa_cap_checksum_hex_chk",
            ),
            models.CheckConstraint(
                condition=Q(checkpoint_input_checksum__isnull=True)
                | Q(checkpoint_input_checksum__regex=r"^[0-9a-f]{64}$"),
                name="trsa_input_checksum_hex_chk",
            ),
            models.CheckConstraint(
                condition=Q(checkpoint_output_checksum__isnull=True)
                | Q(checkpoint_output_checksum__regex=r"^[0-9a-f]{64}$"),
                name="trsa_output_checksum_hex_chk",
            ),
            models.CheckConstraint(
                condition=Q(attempt_idempotency_token__regex=r"^[0-9a-f]{64}$"),
                name="trsa_idempotency_hex_chk",
            ),
            models.CheckConstraint(
                condition=~Q(status=TransformationRunStepAttemptStatus.SUCCEEDED)
                | Q(
                    checkpoint_input_checksum__isnull=False,
                    checkpoint_output_checksum__isnull=False,
                    checkpoint_checksum__isnull=False,
                    completed_at__isnull=False,
                ),
                name="trsa_succeeded_checkpoint_chk",
            ),
        ]


class TransformationRunOutput(models.Model):
    """Persist one declared final-output slot and its future durable reference."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(
        TransformationRun,
        on_delete=models.CASCADE,
        related_name="outputs",
    )
    output_index = models.PositiveIntegerField()
    logical_name = models.CharField(max_length=255)
    source_symbol = models.CharField(max_length=255)
    producer_step_id = models.CharField(max_length=255)
    producer_step_index = models.PositiveIntegerField()
    producer_output_index = models.PositiveIntegerField()
    data_format = models.CharField(max_length=100)
    declaration_metadata_json = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=20,
        choices=TransformationRunOutputStatus.choices,
        default=TransformationRunOutputStatus.PENDING,
    )
    output_kind = models.CharField(
        max_length=20,
        choices=TransformationRunOutputKind.choices,
        blank=True,
        null=True,
    )
    output_policy_kind = models.CharField(
        max_length=32,
        choices=TransformationRunOutputPolicyKind.choices,
        blank=True,
        null=True,
        editable=False,
    )
    output_policy_json = models.JSONField(blank=True, null=True, editable=False)
    output_policy_checksum = models.CharField(
        max_length=64,
        blank=True,
        null=True,
        editable=False,
    )
    output_policy_contract_version = models.CharField(
        max_length=64,
        blank=True,
        null=True,
        editable=False,
    )
    destination_storage_provider = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        editable=False,
    )
    destination_object_key = models.CharField(
        max_length=2048,
        blank=True,
        null=True,
        editable=False,
    )
    target_dataset = models.ForeignKey(
        Dataset,
        on_delete=models.PROTECT,
        related_name="targeted_transformation_outputs",
        blank=True,
        null=True,
        editable=False,
    )
    producing_step_attempt = models.ForeignKey(
        TransformationRunStepAttempt,
        on_delete=models.SET_NULL,
        related_name="produced_outputs",
        blank=True,
        null=True,
    )
    canonical_uri = models.CharField(max_length=2048, blank=True, null=True)
    checksum_algorithm = models.CharField(max_length=32, blank=True, null=True)
    checksum_value = models.CharField(max_length=128, blank=True, null=True)
    size_bytes = models.PositiveBigIntegerField(blank=True, null=True)
    dataset = models.ForeignKey(
        Dataset,
        on_delete=models.SET_NULL,
        related_name="transformation_run_outputs",
        blank=True,
        null=True,
    )
    dataset_version = models.ForeignKey(
        DatasetVersion,
        on_delete=models.SET_NULL,
        related_name="transformation_run_outputs",
        blank=True,
        null=True,
    )
    dataset_artifact = models.ForeignKey(
        DatasetArtifact,
        on_delete=models.SET_NULL,
        related_name="transformation_run_outputs",
        blank=True,
        null=True,
    )
    error_code = models.CharField(max_length=100, blank=True, null=True)
    error_message = models.TextField(blank=True, null=True)
    finalization_attempt_count = models.PositiveIntegerField(default=0)
    materialization_started_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["run", "status"], name="tro_run_status_idx"),
            models.Index(fields=["status", "created_at"], name="tro_status_created_idx"),
            models.Index(fields=["status", "updated_at"], name="tro_status_updated_idx"),
            models.Index(
                fields=["dataset_version", "status"],
                name="tro_version_status_idx",
            ),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["run", "output_index"],
                name="tro_run_output_index_uniq",
            ),
            models.UniqueConstraint(
                fields=["run", "logical_name"],
                name="tro_run_logical_name_uniq",
            ),
            models.UniqueConstraint(
                fields=["destination_storage_provider", "destination_object_key"],
                condition=Q(
                    destination_storage_provider__isnull=False,
                    destination_object_key__isnull=False,
                ),
                name="tro_destination_uniq",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        output_policy_kind__isnull=True,
                        output_policy_json__isnull=True,
                        output_policy_checksum__isnull=True,
                        output_policy_contract_version__isnull=True,
                        destination_storage_provider__isnull=True,
                        destination_object_key__isnull=True,
                    )
                    | Q(
                        output_policy_kind__isnull=False,
                        output_policy_json__isnull=False,
                        output_policy_checksum__isnull=False,
                        output_policy_contract_version__isnull=False,
                        destination_storage_provider__isnull=False,
                        destination_object_key__isnull=False,
                    )
                ),
                name="tro_policy_bundle_chk",
            ),
            models.CheckConstraint(
                condition=Q(output_policy_checksum__isnull=True)
                | Q(output_policy_checksum__regex=r"^[0-9a-f]{64}$"),
                name="tro_policy_checksum_chk",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        output_policy_kind=TransformationRunOutputPolicyKind.NEW_DATASET_VERSION,
                        target_dataset__isnull=False,
                    )
                    | Q(
                        output_policy_kind__in=[
                            TransformationRunOutputPolicyKind.STANDALONE,
                            TransformationRunOutputPolicyKind.NEW_DATASET,
                        ],
                        target_dataset__isnull=True,
                    )
                    | Q(output_policy_kind__isnull=True, target_dataset__isnull=True)
                ),
                name="tro_policy_target_chk",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        output_policy_kind=TransformationRunOutputPolicyKind.STANDALONE,
                        output_kind=TransformationRunOutputKind.STANDALONE,
                    )
                    | Q(
                        output_policy_kind__in=[
                            TransformationRunOutputPolicyKind.NEW_DATASET,
                            TransformationRunOutputPolicyKind.NEW_DATASET_VERSION,
                        ],
                        output_kind=TransformationRunOutputKind.DATASET_BACKED,
                    )
                    | Q(output_policy_kind__isnull=True, output_kind__isnull=True)
                ),
                name="tro_policy_kind_chk",
            ),
            models.CheckConstraint(
                condition=(
                    Q(checksum_algorithm__isnull=True, checksum_value__isnull=True)
                    | Q(checksum_algorithm__isnull=False, checksum_value__isnull=False)
                ),
                name="tro_checksum_pair_chk",
            ),
            models.CheckConstraint(
                condition=(
                    ~Q(
                        status__in=[
                            TransformationRunOutputStatus.MATERIALIZING,
                            TransformationRunOutputStatus.MATERIALIZED,
                            TransformationRunOutputStatus.REGISTERED,
                        ],
                    )
                    | Q(
                        checksum_algorithm__isnull=False,
                        checksum_value__isnull=False,
                        size_bytes__isnull=False,
                        producing_step_attempt__isnull=False,
                    )
                ),
                name="tro_integrity_shape_chk",
            ),
            models.CheckConstraint(
                condition=(
                    ~Q(
                        status__in=[
                            TransformationRunOutputStatus.MATERIALIZED,
                            TransformationRunOutputStatus.REGISTERED,
                        ],
                    )
                    | Q(canonical_uri__isnull=False)
                ),
                name="tro_materialized_shape_chk",
            ),
            models.CheckConstraint(
                condition=(
                    ~Q(status=TransformationRunOutputStatus.REGISTERED)
                    | Q(
                        output_kind=TransformationRunOutputKind.DATASET_BACKED,
                        canonical_uri__isnull=False,
                        producing_step_attempt__isnull=False,
                        dataset__isnull=False,
                        dataset_version__isnull=False,
                        dataset_artifact__isnull=False,
                    )
                ),
                name="tro_registered_shape_chk",
            ),
            models.CheckConstraint(
                condition=(
                    ~Q(status=TransformationRunOutputStatus.CLEANUP_PENDING)
                    | Q(
                        canonical_uri__isnull=False,
                        destination_storage_provider__isnull=False,
                        destination_object_key__isnull=False,
                    )
                ),
                name="tro_cleanup_shape_chk",
            ),
        ]


class TransformationWorkspaceLease(models.Model):
    """Persist runtime ownership of one provider workspace object."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.UUIDField(db_index=True, editable=False)
    run = models.ForeignKey(
        TransformationRun,
        on_delete=models.PROTECT,
        related_name="workspace_leases",
    )
    step_attempt = models.ForeignKey(
        TransformationRunStepAttempt,
        on_delete=models.PROTECT,
        related_name="workspace_leases",
    )
    run_attempt_number = models.PositiveIntegerField(editable=False)
    step_id = models.CharField(max_length=255, editable=False)
    output_logical_name = models.CharField(max_length=255, editable=False)
    storage_provider = models.CharField(max_length=100, editable=False)
    workspace_key = models.CharField(max_length=2048, editable=False)
    lease_generation = models.PositiveIntegerField(default=1, editable=False)
    state = models.CharField(
        max_length=20,
        choices=TransformationWorkspaceLeaseState.choices,
        default=TransformationWorkspaceLeaseState.ACTIVE,
    )
    checksum = models.CharField(max_length=64, blank=True, null=True, editable=False)
    size_bytes = models.PositiveBigIntegerField(blank=True, null=True, editable=False)
    maximum_bytes = models.PositiveBigIntegerField(editable=False)
    cleanup_owner = models.CharField(max_length=128, blank=True, null=True, editable=False)
    cleanup_retry_count = models.PositiveIntegerField(default=0)
    last_error_code = models.CharField(max_length=100, blank=True, null=True)
    expires_at = models.DateTimeField(editable=False)
    committed_at = models.DateTimeField(blank=True, null=True, editable=False)
    cleanup_requested_at = models.DateTimeField(blank=True, null=True, editable=False)
    released_at = models.DateTimeField(blank=True, null=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["state", "expires_at"], name="twl_state_expiry_idx"),
            models.Index(fields=["run", "state"], name="twl_run_state_idx"),
            models.Index(fields=["tenant_id", "state"], name="twl_tenant_state_idx"),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["storage_provider", "workspace_key"],
                name="twl_provider_key_uniq",
            ),
            models.UniqueConstraint(
                fields=["step_attempt", "output_logical_name"],
                name="twl_step_output_uniq",
            ),
            models.CheckConstraint(
                condition=Q(
                    run_attempt_number__gte=1, lease_generation__gte=1, maximum_bytes__gte=1
                ),
                name="twl_positive_values_chk",
            ),
            models.CheckConstraint(
                condition=Q(checksum__isnull=True) | Q(checksum__regex=r"^[0-9a-f]{64}$"),
                name="twl_checksum_shape_chk",
            ),
            models.CheckConstraint(
                condition=(
                    ~Q(state=TransformationWorkspaceLeaseState.COMMITTED)
                    | Q(
                        checksum__isnull=False,
                        size_bytes__isnull=False,
                        committed_at__isnull=False,
                        released_at__isnull=True,
                    )
                ),
                name="twl_committed_shape_chk",
            ),
            models.CheckConstraint(
                condition=(
                    ~Q(state=TransformationWorkspaceLeaseState.CLEANUP_PENDING)
                    | Q(cleanup_requested_at__isnull=False, released_at__isnull=True)
                ),
                name="twl_cleanup_shape_chk",
            ),
            models.CheckConstraint(
                condition=(
                    ~Q(state=TransformationWorkspaceLeaseState.RELEASED)
                    | Q(released_at__isnull=False)
                ),
                name="twl_released_shape_chk",
            ),
        ]


class MetadataSchemaPublicationRecord(models.Model):
    """Persist one immutable canonical metadata schema publication snapshot."""

    schema_namespace = models.CharField(max_length=128, editable=False)
    schema_name = models.CharField(max_length=64, editable=False)
    schema_version = models.CharField(max_length=255, editable=False)
    publication_status = models.CharField(max_length=16, default="PUBLISHED", editable=False)
    publication_sequence = models.PositiveBigIntegerField(editable=False)
    definition_snapshot_json = models.JSONField(editable=False)
    trusted_actor_reference = models.CharField(
        max_length=255, blank=True, null=True, editable=False
    )
    correlation_identifier = models.CharField(max_length=255, blank=True, null=True, editable=False)
    published_at = models.DateTimeField(auto_now_add=True, editable=False)
    recorded_at = models.DateTimeField(auto_now_add=True, editable=False)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["schema_namespace", "schema_name", "schema_version"],
                name="mspr_schema_version_uniq",
            ),
            models.UniqueConstraint(
                fields=["schema_namespace", "schema_name", "publication_sequence"],
                name="mspr_schema_sequence_uniq",
            ),
            models.CheckConstraint(
                condition=Q(publication_sequence__gte=1), name="mspr_positive_sequence_chk"
            ),
            models.CheckConstraint(
                condition=Q(publication_status="PUBLISHED"), name="mspr_published_status_chk"
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=["schema_namespace", "schema_name", "publication_sequence"],
                name="mspr_schema_history_idx",
            ),
        ]


class MetadataSchemaPublicationSequence(models.Model):
    """Serialize committed publication-sequence allocation for one exact schema."""

    schema_namespace = models.CharField(max_length=128, editable=False)
    schema_name = models.CharField(max_length=64, editable=False)
    last_committed_sequence = models.PositiveBigIntegerField(default=0, editable=False)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["schema_namespace", "schema_name"], name="msps_schema_uniq"
            ),
        ]


class MetadataSchemaActiveSelectionRecord(models.Model):
    """Persist the one mutable active-selection projection for one exact schema."""

    schema_namespace = models.CharField(max_length=128, editable=False)
    schema_name = models.CharField(max_length=64, editable=False)
    publication = models.ForeignKey(
        MetadataSchemaPublicationRecord,
        on_delete=models.PROTECT,
        related_name="active_selection_references",
        editable=False,
    )
    schema_version = models.CharField(max_length=255, editable=False)
    revision = models.PositiveBigIntegerField(editable=False)
    trusted_actor_reference = models.CharField(
        max_length=255, blank=True, null=True, editable=False
    )
    correlation_identifier = models.CharField(max_length=255, blank=True, null=True, editable=False)
    activated_at = models.DateTimeField(auto_now_add=True, editable=False)
    recorded_at = models.DateTimeField(auto_now_add=True, editable=False)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["schema_namespace", "schema_name"], name="msas_schema_uniq"
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["schema_namespace", "schema_name"], name="msas_schema_idx"),
        ]
