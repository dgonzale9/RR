"""Create, inspect, publish, and expire private workbook intakes."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path, PurePath
from tempfile import NamedTemporaryFile

from django.conf import settings
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Min
from django.utils import timezone

from BotServer.util.user_group_management import UserGroupManagement
from Organization.models import Organization, Workspace
from resource_registry.cleanup.inventory import register_object
from resource_registry.cleanup.protection import has_pending_intake_use
from resource_registry.cleanup.storage import reserve_upload_source
from resource_registry.intake.inspection_service import StagedResourceIntakeInspectionService
from resource_registry.intake.permissions import can_manage_staged_resource_intakes
from resource_registry.models import (
    DatasetChangeStatus,
    DatasetVisibilityScope,
    LargeFileUploadSession,
    LargeFileUploadStatus,
    RegistryNamespace,
    ResourceStorageReference,
    StagedResourceInspectionJob,
    StagedResourceIntake,
    StagedResourceIntakeStatus,
    StagedResourcePublication,
    StagedResourcePublicationStatus,
)
from resource_registry.registration_submissions.services import RegistrationSubmissionService
from resource_registry.services.dataset_context_builder import DatasetContextBuilder
from resource_registry.storage.factory import DataRegistryStorageFactory
from resource_registry.uploads.backend_blocks import (
    BackendBlockUploadProvider,
    build_backend_block_sizes,
    get_backend_block_spec,
)
from resource_registry.uploads.contracts import (
    DirectUploadAuthorization,
    LargeFileUploadProvider,
    SupportedUploadFormat,
    UploadedObjectMetadata,
)
from resource_registry.uploads.exceptions import UploadApplicationError
from resource_registry.uploads.format_policy import UploadFileFormatPolicy
from resource_registry.uploads.services import LargeFileUploadAuthorizationService, UploadAdmission

_IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_MAX_FILENAME_LENGTH = 255
_EXPIRABLE_STATUSES = frozenset(
    {
        StagedResourceIntakeStatus.PENDING_UPLOAD,
        StagedResourceIntakeStatus.COMPLETED,
        StagedResourceIntakeStatus.ANALYZING,
        StagedResourceIntakeStatus.READY_FOR_PUBLICATION,
        StagedResourceIntakeStatus.PUBLISHING,
    }
)


@dataclass(frozen=True, kw_only=True)
class IntakeSnapshot:
    """Expose sanitized private intake state."""

    intake_id: uuid.UUID
    status: str
    filename: str
    file_format: str
    declared_size_bytes: int
    actual_size_bytes: int | None
    checksum_verification_status: str
    inspection: dict[str, object]
    expires_at: datetime | None
    failure_code: str | None
    source_available: bool = True
    source_deleted_at: datetime | None = None


@dataclass(frozen=True, kw_only=True)
class CreatedIntake:
    """Return intake state and short-lived direct upload authorization."""

    intake: IntakeSnapshot
    authorization: DirectUploadAuthorization | None
    idempotent_replay: bool
    storage_provider: str


@dataclass(frozen=True, kw_only=True)
class RenewedIntakeUploadAuthorization:
    """Return a refreshed direct-upload authorization for one pending intake."""

    intake: IntakeSnapshot
    authorization: DirectUploadAuthorization


@dataclass(frozen=True, kw_only=True)
class PublishedIntake:
    """Return the existing registration submission created for publication."""

    publication_id: uuid.UUID
    registration_id: uuid.UUID
    namespace_id: uuid.UUID
    idempotent_replay: bool


class StagedResourceIntakeService:
    """Own the isolated staging lifecycle before a Dataset has a destination."""

    def __init__(
        self,
        *,
        provider: LargeFileUploadProvider,
        authorization_service: LargeFileUploadAuthorizationService | None = None,
        registration_submission_service: RegistrationSubmissionService | None = None,
        storage_factory: DataRegistryStorageFactory | None = None,
        format_policy: UploadFileFormatPolicy | None = None,
        context_builder: DatasetContextBuilder | None = None,
    ) -> None:
        self._provider = provider
        self._authorization_service = authorization_service or LargeFileUploadAuthorizationService()
        self._registration_submission_service = (
            registration_submission_service or RegistrationSubmissionService()
        )
        self._storage_factory = storage_factory or DataRegistryStorageFactory()
        self._format_policy = format_policy or UploadFileFormatPolicy()
        self._context_builder = context_builder or DatasetContextBuilder()
        self._inspection_service = StagedResourceIntakeInspectionService(
            storage_factory=self._storage_factory,
            context_builder=self._context_builder,
        )

    def create_intake(
        self,
        *,
        user: User,
        filename: str,
        content_type: str,
        size_bytes: int,
        checksum_algorithm: str | None,
        checksum: str | None,
        idempotency_key: str,
        issue_direct_authorization: bool = True,
    ) -> CreatedIntake:
        """Create private staging state and optionally authorize legacy direct transfer.

        Args:
            user: Authenticated uploader who owns the intake.
            filename: Original filename, normalized before persistence.
            content_type: Declared media type validated against the extension.
            size_bytes: Declared file size subject to quota and size limits.
            checksum_algorithm: Optional declared checksum algorithm.
            checksum: Optional checksum digest paired with the algorithm.
            idempotency_key: Uploader-scoped key for repeat creation requests.
            issue_direct_authorization: Retain direct authorization for internal legacy callers;
                HTTP intake endpoints pass False and never return a storage SAS.

        Returns:
            Sanitized intake state and optional legacy transfer authorization.

        Raises:
            UploadApplicationError: If identity, metadata, quota, or provider admission fails.
        """
        self._assert_active_user(user)
        normalized_filename = self._normalize_filename(filename)
        file_format = self._format_policy.resolve(
            filename=normalized_filename,
            declared_content_type=content_type,
        )
        self._validate_size(size_bytes)
        if not issue_direct_authorization:
            self._get_backend_upload_provider()
            get_backend_block_spec(uuid.UUID(int=0), size_bytes, 0)
        algorithm, normalized_checksum = self._normalize_checksum(checksum_algorithm, checksum)
        if not _IDEMPOTENCY_PATTERN.fullmatch(idempotency_key):
            raise UploadApplicationError("INTAKE_INVALID_IDEMPOTENCY_KEY", 400)
        fingerprint = self._fingerprint(
            user_id=int(user.pk),
            filename=normalized_filename,
            content_type=content_type,
            size_bytes=size_bytes,
            checksum=normalized_checksum,
        )
        now = timezone.now()
        upload_expires_at = now + timedelta(
            seconds=min(
                max(int(settings.RESOURCE_REGISTRY_UPLOAD_AUTHORIZATION_TTL_SECONDS), 60), 3600
            )
        )
        expires_at = now + timedelta(
            seconds=max(int(getattr(settings, "RESOURCE_REGISTRY_INTAKE_TTL_SECONDS", 86400)), 300)
        )
        with transaction.atomic():
            existing = (
                StagedResourceIntake.objects.select_for_update()
                .filter(submitted_by=user, idempotency_key=idempotency_key)
                .first()
            )
            if existing is not None:
                if existing.request_fingerprint != fingerprint or existing.status in {
                    StagedResourceIntakeStatus.CANCELLED,
                    StagedResourceIntakeStatus.EXPIRED,
                }:
                    raise UploadApplicationError("INTAKE_IDEMPOTENCY_CONFLICT", 409)
                intake = existing
                replay = True
            else:
                self._enforce_user_quota(user=user)
                intake_id = uuid.uuid4()
                suffix = self._format_policy.extension_for(file_format)
                intake = StagedResourceIntake.objects.create(
                    id=intake_id,
                    submitted_by=user,
                    original_filename=normalized_filename,
                    normalized_filename=normalized_filename,
                    resolved_file_format=file_format.value,
                    declared_content_type=content_type.strip().lower(),
                    declared_size_bytes=size_bytes,
                    storage_provider=self._provider.provider_name,
                    storage_object_key=(
                        f"resource-registry/intakes/{user.pk}/{intake_id}/source{suffix}"
                    ),
                    checksum_algorithm=algorithm,
                    declared_checksum=normalized_checksum,
                    idempotency_key=idempotency_key,
                    request_fingerprint=fingerprint,
                    upload_authorization_expires_at=upload_expires_at,
                    expires_at=expires_at,
                )
                replay = False

        reserve_upload_source(intake)
        authorization = None
        if (
            issue_direct_authorization
            and intake.status == StagedResourceIntakeStatus.PENDING_UPLOAD
        ):
            authorization = self._provider.create_upload_authorization(
                object_key=intake.storage_object_key,
                content_type=intake.declared_content_type,
                expires_at=upload_expires_at,
                recommended_chunk_size_bytes=int(
                    settings.RESOURCE_REGISTRY_UPLOAD_RECOMMENDED_CHUNK_SIZE_BYTES
                ),
                max_parallel_chunks=int(settings.RESOURCE_REGISTRY_UPLOAD_MAX_PARALLEL_CHUNKS),
            )
        return CreatedIntake(
            intake=self._snapshot(intake),
            authorization=authorization,
            idempotent_replay=replay,
            storage_provider=intake.storage_provider,
        )

    def get_intake(self, *, user: User, intake_id: uuid.UUID) -> IntakeSnapshot:
        """Return one owner-visible intake without exposing its storage address."""
        intake = StagedResourceIntake.objects.filter(pk=intake_id, submitted_by=user).first()
        if intake is None:
            raise UploadApplicationError("INTAKE_NOT_FOUND", 404)
        return self._snapshot(intake)

    def renew_upload_authorization(
        self,
        *,
        user: User,
        intake_id: uuid.UUID,
        allow_administrator_intake_access: bool = False,
    ) -> RenewedIntakeUploadAuthorization:
        """Issue a new direct-upload authorization for one still-pending intake.

        Args:
            user: User requesting the renewed authorization.
            intake_id: Primary key of the intake to renew.
            allow_administrator_intake_access: Allow a global intake operator to renew another
                uploader's intake.

        Returns:
            The current intake snapshot and a new, write-only authorization.

        Raises:
            UploadApplicationError: If the intake is unavailable, expired, or not pending upload.
        """
        self._assert_active_user(user)
        if allow_administrator_intake_access and not can_manage_staged_resource_intakes(user):
            raise UploadApplicationError("AUTHORIZATION_DENIED", 403)
        intake_expired = False
        with transaction.atomic():
            intake = self._lock_intake(
                user=user,
                intake_id=intake_id,
                require_owner=not allow_administrator_intake_access,
            )
            if self._has_intake_expired(intake):
                intake.status = StagedResourceIntakeStatus.EXPIRED
                intake.cleanup_required = True
                intake.failure_code = "INTAKE_EXPIRED"
                intake.save(
                    update_fields=["status", "cleanup_required", "failure_code", "updated_at"]
                )
                intake_expired = True
            elif intake.status != StagedResourceIntakeStatus.PENDING_UPLOAD:
                raise UploadApplicationError("INTAKE_INVALID_STATE", 409)
            else:
                upload_expires_at = timezone.now() + timedelta(
                    seconds=min(
                        max(int(settings.RESOURCE_REGISTRY_UPLOAD_AUTHORIZATION_TTL_SECONDS), 60),
                        3600,
                    )
                )
                intake.upload_authorization_expires_at = upload_expires_at
                intake.save(update_fields=["upload_authorization_expires_at", "updated_at"])

                reserve_upload_source(intake)
                authorization = self._provider.create_upload_authorization(
                    object_key=intake.storage_object_key,
                    content_type=intake.declared_content_type,
                    expires_at=upload_expires_at,
                    recommended_chunk_size_bytes=int(
                        settings.RESOURCE_REGISTRY_UPLOAD_RECOMMENDED_CHUNK_SIZE_BYTES
                    ),
                    max_parallel_chunks=int(settings.RESOURCE_REGISTRY_UPLOAD_MAX_PARALLEL_CHUNKS),
                )
        if intake_expired:
            self._cleanup_intake(intake)
            raise UploadApplicationError("INTAKE_EXPIRED", 409)
        return RenewedIntakeUploadAuthorization(
            intake=self._snapshot(intake), authorization=authorization
        )

    def complete_intake(
        self,
        *,
        user: User,
        intake_id: uuid.UUID,
        expected_etag: str | None,
        commit_backend_blocks: bool = False,
    ) -> IntakeSnapshot:
        """Commit optional server-uploaded blocks and queue verified intake inspection.

        Args:
            user: Authenticated intake owner.
            intake_id: Server-issued intake identifier.
            expected_etag: Optional expected object version from a legacy client.
            commit_backend_blocks: Verify and commit the server-derived block manifest first.

        Returns:
            Sanitized state, including successful replays without duplicate inspection jobs.

        Raises:
            UploadApplicationError: If admission, completeness, metadata, or provider checks fail.
        """
        with transaction.atomic():
            self._assert_active_user(user)
            intake = self._lock_owner_intake(user=user, intake_id=intake_id)
            if intake.status in {
                StagedResourceIntakeStatus.COMPLETED,
                StagedResourceIntakeStatus.ANALYZING,
                StagedResourceIntakeStatus.READY_FOR_PUBLICATION,
                StagedResourceIntakeStatus.PUBLISHED,
            }:
                return self._snapshot(intake)
            if intake.status != StagedResourceIntakeStatus.PENDING_UPLOAD:
                raise UploadApplicationError("INTAKE_INVALID_STATE", 409)
            if self._has_intake_expired(intake):
                intake.status = StagedResourceIntakeStatus.EXPIRED
                intake.cleanup_required = True
                intake.failure_code = "INTAKE_EXPIRED"
                intake.save(
                    update_fields=["status", "cleanup_required", "failure_code", "updated_at"]
                )
                raise UploadApplicationError("INTAKE_EXPIRED", 409)
            if commit_backend_blocks:
                self._assert_backend_upload_pending(intake)
                self._get_backend_upload_provider().commit_upload_blocks(
                    object_key=intake.storage_object_key,
                    block_sizes=build_backend_block_sizes(intake.id, intake.declared_size_bytes),
                    content_type=intake.declared_content_type,
                )
            metadata = self._provider.inspect_uploaded_object(object_key=intake.storage_object_key)
            self._validate_provider_metadata(
                intake=intake, metadata=metadata, expected_etag=expected_etag
            )
            now = timezone.now()
            intake.status = StagedResourceIntakeStatus.COMPLETED
            intake.actual_size_bytes = metadata.size_bytes
            intake.detected_content_type = metadata.content_type
            intake.storage_etag = metadata.etag
            intake.storage_version_id = metadata.version_id
            intake.provider_reported_checksum = metadata.provider_checksum
            intake.checksum_verification_status = (
                "DEFERRED" if intake.declared_checksum else "NOT_PROVIDED"
            )
            intake.uploaded_at = now
            intake.completed_at = now
            intake.failure_code = None
            intake.save()
            StagedResourceInspectionJob.objects.get_or_create(intake_id=intake_id)
        return self.get_intake(user=user, intake_id=intake_id)

    def get_backend_upload(self, *, user: User, intake_id: uuid.UUID) -> IntakeSnapshot:
        """Validate a pending server upload before returning its public state.

        Args:
            user: Authenticated intake owner.
            intake_id: Server-issued intake identifier.

        Returns:
            Sanitized pending intake state without a storage URL or SAS.

        Raises:
            UploadApplicationError: If ownership, state, expiry, or provider checks fail.
        """
        self._assert_active_user(user)
        with transaction.atomic():
            intake = self._lock_owner_intake(user=user, intake_id=intake_id)
            self._assert_backend_upload_pending(intake)
            return self._snapshot(intake)

    def upload_backend_block(
        self,
        *,
        user: User,
        intake_id: uuid.UUID,
        block_index: int,
        read_chunk: Callable[[int], bytes],
    ) -> None:
        """Receive and transfer one bounded block after authorizing its destination.

        Serialize against completion and cancellation using the intake row lock. No
        client-supplied storage address, block ID, or content type is accepted.

        Args:
            user: Authenticated intake owner.
            intake_id: Server-issued intake identifier.
            block_index: Zero-based position in the server-derived manifest.
            read_chunk: Request stream reader that honors the requested byte bound.

        Raises:
            UploadApplicationError: If admission, block size, or provider transfer fails.
        """
        self._assert_active_user(user)
        with transaction.atomic():
            intake = self._lock_owner_intake(user=user, intake_id=intake_id)
            self._assert_backend_upload_pending(intake)
            block_id, expected_size = get_backend_block_spec(
                intake.id, intake.declared_size_bytes, block_index
            )
            content = read_chunk(expected_size + 1)
            if len(content) != expected_size:
                raise UploadApplicationError("UPLOAD_SIZE_MISMATCH", 400)
            self._get_backend_upload_provider().stage_upload_block(
                object_key=intake.storage_object_key, block_id=block_id, content=content
            )

    def _assert_backend_upload_pending(self, intake: StagedResourceIntake) -> None:
        self._get_backend_upload_provider()
        if intake.storage_provider != self._provider.provider_name:
            raise UploadApplicationError("UPLOAD_PROVIDER_UNAVAILABLE", 503)
        if self._has_intake_expired(intake):
            raise UploadApplicationError("INTAKE_EXPIRED", 409)
        if intake.status != StagedResourceIntakeStatus.PENDING_UPLOAD:
            raise UploadApplicationError("INTAKE_INVALID_STATE", 409)

    def _get_backend_upload_provider(self) -> BackendBlockUploadProvider:
        if not isinstance(self._provider, BackendBlockUploadProvider):
            raise UploadApplicationError("UPLOAD_PROVIDER_UNAVAILABLE", 503)
        return self._provider

    def run_inspection_jobs(self, *, batch_size: int = 10) -> int:
        """Run a bounded batch of staged-resource inspection jobs."""
        return self._inspection_service.run_jobs(
            batch_size=batch_size,
            inspect_uploaded_file=self._inspect_uploaded_file,
        )

    def cancel_intake(self, *, user: User, intake_id: uuid.UUID) -> IntakeSnapshot:
        """Cancel an owner-visible intake and delete only its staging object."""
        with transaction.atomic():
            intake = self._lock_owner_intake(user=user, intake_id=intake_id)
            if intake.status in {
                StagedResourceIntakeStatus.CANCELLED,
                StagedResourceIntakeStatus.EXPIRED,
            }:
                return self._snapshot(intake)
            if intake.status in {
                StagedResourceIntakeStatus.PUBLISHING,
                StagedResourceIntakeStatus.PUBLISHED,
            }:
                raise UploadApplicationError("INTAKE_INVALID_STATE", 409)
            intake.status = StagedResourceIntakeStatus.CANCELLED
            intake.cancelled_at = timezone.now()
            intake.cleanup_required = True
            intake.save(update_fields=["status", "cancelled_at", "cleanup_required", "updated_at"])
        self._cleanup_intake(intake)
        return self.get_intake(user=user, intake_id=intake_id)

    def publish_intake(
        self,
        *,
        user: User,
        organization: Organization | None,
        intake_id: uuid.UUID,
        visibility_scope: str,
        workspace_id: int | None,
        _admission_override: UploadAdmission | None = None,
        _allow_administrator_intake_access: bool = False,
    ) -> PublishedIntake:
        """Copy one verified intake into an authorized destination and submit registration."""
        if _admission_override is not None:
            if not _allow_administrator_intake_access or not can_manage_staged_resource_intakes(
                user
            ):
                raise UploadApplicationError("AUTHORIZATION_DENIED", 403)
            admission = _admission_override
        else:
            admission = self._admit_destination(
                user=user,
                organization=organization,
                visibility_scope=visibility_scope,
                workspace_id=workspace_id,
            )
        with transaction.atomic():
            intake = self._lock_intake(
                user=user,
                intake_id=intake_id,
                require_owner=not _allow_administrator_intake_access,
            )
            if intake.status not in {
                StagedResourceIntakeStatus.READY_FOR_PUBLICATION,
                StagedResourceIntakeStatus.PUBLISHED,
            }:
                raise UploadApplicationError("INTAKE_NOT_READY_FOR_PUBLICATION", 409)
            publication = (
                StagedResourcePublication.objects.select_for_update()
                .filter(
                    intake=intake,
                    registry_namespace_id=admission.namespace_id,
                    assignment_status__in=[
                        StagedResourcePublicationStatus.ACTIVE,
                        StagedResourcePublicationStatus.DETACH_REQUESTED,
                    ],
                )
                .first()
            )
            if (
                publication is not None
                and publication.assignment_status
                == StagedResourcePublicationStatus.DETACH_REQUESTED
            ):
                raise UploadApplicationError("PUBLICATION_DETACHMENT_PENDING", 409)
            if publication is not None and publication.registration_submission_id is not None:
                return PublishedIntake(
                    publication_id=publication.id,
                    registration_id=publication.registration_submission_id,
                    namespace_id=admission.namespace_id,
                    idempotent_replay=True,
                )
            if publication is not None and publication.dataset_changes.exists():
                raise UploadApplicationError(
                    "INTAKE_ALREADY_PUBLISHED",
                    409,
                    "This resource is already published to this destination. "
                    "Open its dataset to update it.",
                )
            self._assert_intake_available_for_publication(intake)
            intake.source_last_used_at = timezone.now()
            intake.save(update_fields=["source_last_used_at"])
            if publication is None:
                previous_attempt = (
                    StagedResourcePublication.objects.filter(
                        intake=intake,
                        registry_namespace_id=admission.namespace_id,
                    )
                    .order_by("-publication_attempt")
                    .values_list("publication_attempt", flat=True)
                    .first()
                )
                publication_attempt = (previous_attempt or 0) + 1
                publication = StagedResourcePublication.objects.create(
                    intake=intake,
                    registry_namespace_id=admission.namespace_id,
                    organization_id=admission.organization_id,
                    workspace_id=admission.workspace_id,
                    visibility_scope=visibility_scope,
                    publication_attempt=publication_attempt,
                    published_by=user,
                )
            destination_key = (
                f"resource-registry/uploads/{admission.organization_id}/{admission.namespace_id}/"
                f"intakes/{intake.id}/publications/{publication.id}/"
                f"{uuid.uuid4()}/source{Path(intake.normalized_filename).suffix.lower()}"
            )
            publication.destination_object_key = destination_key
            publication.cleanup_required = False
            publication.save(update_fields=["destination_object_key", "cleanup_required"])
            source_object_key = intake.storage_object_key
            if intake.status == StagedResourceIntakeStatus.PUBLISHED:
                prior_upload = (
                    intake.publications.select_related("upload_session")
                    .exclude(upload_session__isnull=True)
                    .order_by("created_at")
                    .first()
                )
                if prior_upload is None or prior_upload.upload_session is None:
                    if not intake.datasetchange_set.filter(
                        status=DatasetChangeStatus.PUBLISHED
                    ).exists():
                        raise UploadApplicationError("INTAKE_SOURCE_UNAVAILABLE", 409)
                else:
                    source_object_key = prior_upload.upload_session.storage_object_key
            intake.status = StagedResourceIntakeStatus.PUBLISHING
            intake.save(update_fields=["status", "updated_at"])
        try:
            copied = self._provider.copy_object(
                source_object_key=source_object_key,
                destination_object_key=destination_key,
            )
            self._validate_copied_metadata(intake=intake, metadata=copied)
            self._verify_copied_checksum(
                intake=intake,
                destination_object_key=destination_key,
            )
        except Exception:
            self._mark_publication_cleanup_required(publication_id=publication.id)
            self._cleanup_publication_destination(publication_id=publication.id)
            self._restore_after_publication_failure(intake_id=intake_id)
            raise
        try:
            with transaction.atomic():
                intake = StagedResourceIntake.objects.select_for_update().get(pk=intake_id)
                publication = StagedResourcePublication.objects.select_for_update().get(
                    pk=publication.pk
                )
                if (
                    intake.status != StagedResourceIntakeStatus.PUBLISHING
                    or self._has_intake_expired(intake)
                ):
                    raise UploadApplicationError("INTAKE_EXPIRED", 409)
                upload = LargeFileUploadSession.objects.create(
                    organization_id=admission.organization_id,
                    workspace_id=admission.workspace_id,
                    registry_namespace_id=admission.namespace_id,
                    submitted_by=user,
                    original_filename=intake.original_filename,
                    normalized_filename=intake.normalized_filename,
                    resolved_file_format=intake.resolved_file_format,
                    declared_content_type=intake.declared_content_type,
                    detected_content_type=copied.content_type,
                    declared_size_bytes=intake.declared_size_bytes,
                    actual_size_bytes=copied.size_bytes,
                    storage_provider=self._provider.provider_name,
                    storage_object_key=destination_key,
                    storage_etag=copied.etag,
                    storage_version_id=copied.version_id,
                    checksum_algorithm="sha256",
                    declared_checksum=intake.verified_checksum,
                    provider_reported_checksum=copied.provider_checksum,
                    verified_checksum=intake.verified_checksum,
                    checksum_verification_status="VERIFIED",
                    status=LargeFileUploadStatus.COMPLETED,
                    idempotency_key=self._publication_upload_idempotency_key(
                        intake_id=intake.id,
                        namespace_id=admission.namespace_id,
                        publication_attempt=publication.publication_attempt,
                    ),
                    request_fingerprint=hashlib.sha256(
                        (
                            f"{intake.id}:{admission.namespace_id}:"
                            f"{publication.publication_attempt}"
                        ).encode("ascii")
                    ).hexdigest(),
                    upload_authorization_expires_at=timezone.now(),
                    expires_at=timezone.now() + timedelta(days=1),
                    uploaded_at=timezone.now(),
                    completed_at=timezone.now(),
                )
                publication.upload_session = upload
                publication.save(update_fields=["upload_session"])
                if _allow_administrator_intake_access:
                    submission = self._registration_submission_service.submit_pre_authorized(
                        user=user,
                        admission=admission,
                        upload_id=upload.id,
                        visibility_scope=visibility_scope,
                    )
                else:
                    submission = self._registration_submission_service.submit(
                        user=user,
                        organization=organization,
                        upload_id=upload.id,
                        visibility_scope=visibility_scope,
                    )
                publication.registration_submission_id = submission.registration_id
                publication.completed_at = timezone.now()
                publication.save(update_fields=["registration_submission", "completed_at"])
                intake.status = StagedResourceIntakeStatus.PUBLISHED
                intake.published_at = timezone.now()
                intake.expires_at = None
                intake.save(update_fields=["status", "published_at", "expires_at", "updated_at"])
        except Exception:
            self._mark_publication_cleanup_required(publication_id=publication.id)
            self._cleanup_publication_destination(publication_id=publication.id)
            self._restore_after_publication_failure(intake_id=intake_id)
            raise
        self._cleanup_intake(intake)
        return PublishedIntake(
            publication_id=publication.id,
            registration_id=submission.registration_id,
            namespace_id=admission.namespace_id,
            idempotent_replay=False,
        )

    @staticmethod
    @transaction.atomic
    def synchronize_dataset_publication(*, intake_id: uuid.UUID) -> int:
        """Reflect a committed dataset proposal in its source intake without publishing again.

        Args:
            intake_id: Server-selected source of an authorized dataset publication.

        Returns:
            Number of intakes changed (zero or one). Ignore sources without publications and
            preserve an in-flight legacy publication's state and existing publication date.

        Raises:
            StagedResourceIntake.DoesNotExist: If the source intake no longer exists.
        """
        intake = StagedResourceIntake.objects.select_for_update().get(pk=intake_id)
        if intake.status not in {
            StagedResourceIntakeStatus.READY_FOR_PUBLICATION,
            StagedResourceIntakeStatus.PUBLISHED,
        }:
            return 0
        published_at = intake.datasetchange_set.filter(
            status=DatasetChangeStatus.PUBLISHED
        ).aggregate(value=Min("completed_at"))["value"]
        if published_at is None:
            return 0
        if (
            intake.status == StagedResourceIntakeStatus.PUBLISHED
            and intake.published_at is not None
            and intake.expires_at is None
        ):
            return 0
        intake.status = StagedResourceIntakeStatus.PUBLISHED
        intake.published_at = intake.published_at or published_at
        intake.expires_at = None
        intake.save(update_fields=["status", "published_at", "expires_at", "updated_at"])
        return 1

    def publish_intake_to_namespace(
        self,
        *,
        user: User,
        intake_id: uuid.UUID,
        namespace_id: uuid.UUID,
    ) -> PublishedIntake:
        """Publish one intake to an Admin-selected immutable namespace as a global operator."""
        if not can_manage_staged_resource_intakes(user):
            raise UploadApplicationError("AUTHORIZATION_DENIED", 403)
        namespace = (
            RegistryNamespace.objects.select_related("organization", "workspace__organization")
            .filter(pk=namespace_id)
            .first()
        )
        if namespace is None:
            raise UploadApplicationError("REGISTRY_NAMESPACE_NOT_FOUND", 404)
        if namespace.organization_id is not None:
            organization = namespace.organization
            if (
                organization is None
                or not organization.is_active
                or not organization.resource_registry_enabled
            ):
                raise UploadApplicationError("REGISTRY_NAMESPACE_NOT_READY", 409)
            visibility_scope = DatasetVisibilityScope.ORGANIZATION
            workspace_id = None
        else:
            workspace = namespace.workspace
            if workspace is None or not self._is_workspace_namespace_ready(workspace):
                raise UploadApplicationError("REGISTRY_NAMESPACE_NOT_READY", 409)
            organization = workspace.organization
            visibility_scope = DatasetVisibilityScope.WORKSPACE
            workspace_id = workspace.pk
        admission = UploadAdmission(
            organization_id=int(organization.pk),
            workspace_id=workspace_id,
            namespace_id=namespace.id,
            actor_id=int(user.pk),
        )
        return self.publish_intake(
            user=user,
            organization=organization,
            intake_id=intake_id,
            visibility_scope=visibility_scope,
            workspace_id=workspace_id,
            _admission_override=admission,
            _allow_administrator_intake_access=True,
        )

    def expire_intakes(self, *, batch_size: int = 100) -> int:
        """Expire stale intakes and retry exact staging cleanup without altering published data."""
        now = timezone.now()
        with transaction.atomic():
            intakes = tuple(
                StagedResourceIntake.objects.select_for_update(skip_locked=True)
                .filter(status__in=_EXPIRABLE_STATUSES, expires_at__lte=now)
                .exclude(
                    datasetchange__status__in=["QUEUED", "PROCESSING", "READY", "NEEDS_MANAGER"]
                )
                .order_by("expires_at", "id")[: max(int(batch_size), 1)]
            )
            for intake in intakes:
                intake.status = StagedResourceIntakeStatus.EXPIRED
                intake.cleanup_required = True
                intake.failure_code = "INTAKE_EXPIRED"
                intake.save(
                    update_fields=["status", "cleanup_required", "failure_code", "updated_at"]
                )
        for intake in intakes:
            self._cleanup_intake(intake)
        retry_intakes = tuple(
            StagedResourceIntake.objects.filter(
                cleanup_required=True,
                status__in=(
                    StagedResourceIntakeStatus.CANCELLED,
                    StagedResourceIntakeStatus.EXPIRED,
                    StagedResourceIntakeStatus.PUBLISHED,
                ),
            ).order_by("updated_at", "id")[: max(int(batch_size) - len(intakes), 0)]
        )
        for intake in retry_intakes:
            self._cleanup_intake(intake)
        retry_publications = tuple(
            StagedResourcePublication.objects.filter(
                cleanup_required=True,
                registration_submission__isnull=True,
            ).order_by("created_at", "id")[: max(int(batch_size) - len(intakes), 0)]
        )
        for publication in retry_publications:
            self._cleanup_publication_destination(publication_id=publication.id)
        return len(intakes)

    def _inspect_uploaded_file(self, *, intake: StagedResourceIntake) -> dict[str, object]:
        """Delegate safe file inspection while retaining the established test seam."""
        return self._inspection_service.inspect_uploaded_file(intake=intake)

    def _admit_destination(
        self,
        *,
        user: User,
        organization: Organization | None,
        visibility_scope: str,
        workspace_id: int | None,
    ) -> UploadAdmission:
        if not self._is_destination_administrator(user):
            raise UploadApplicationError("AUTHORIZATION_DENIED", 403)
        if visibility_scope == DatasetVisibilityScope.WORKSPACE:
            if workspace_id is None:
                raise UploadApplicationError("DATASET_WORKSPACE_REQUIRED", 400)
            return self._authorization_service.admit(
                user=user,
                organization=organization,
                workspace_id=workspace_id,
                require_mutation=True,
            )
        if visibility_scope != DatasetVisibilityScope.ORGANIZATION or workspace_id is not None:
            raise UploadApplicationError("DATASET_WORKSPACE_FORBIDDEN", 400)
        return self._authorization_service.admit_organization(
            user=user,
            organization=organization,
            require_mutation=True,
        )

    @staticmethod
    def _is_destination_administrator(user: User) -> bool:
        return bool(
            user.is_superuser
            or UserGroupManagement.is_user_admin(user)  # type: ignore[no-untyped-call]
            or UserGroupManagement.is_user_scoped_admin(user)  # type: ignore[no-untyped-call]
        )

    def _lock_owner_intake(self, *, user: User, intake_id: uuid.UUID) -> StagedResourceIntake:
        intake = (
            StagedResourceIntake.objects.select_for_update()
            .filter(pk=intake_id, submitted_by=user)
            .first()
        )
        if intake is None:
            raise UploadApplicationError("INTAKE_NOT_FOUND", 404)
        return intake

    def _lock_intake(
        self,
        *,
        user: User,
        intake_id: uuid.UUID,
        require_owner: bool,
    ) -> StagedResourceIntake:
        """Lock an intake for its uploader or for an explicitly privileged administrator."""
        queryset = StagedResourceIntake.objects.select_for_update().filter(pk=intake_id)
        if require_owner:
            queryset = queryset.filter(submitted_by=user)
        intake = queryset.first()
        if intake is None:
            raise UploadApplicationError("INTAKE_NOT_FOUND", 404)
        return intake

    @staticmethod
    def _is_workspace_namespace_ready(workspace: Workspace) -> bool:
        """Return whether a Workspace destination remains enabled under its Organization."""
        return bool(
            workspace.pk is not None
            and workspace.is_active
            and workspace.resource_registry_enabled
            and workspace.organization.is_active
            and workspace.organization.resource_registry_enabled
        )

    def _validate_provider_metadata(
        self,
        *,
        intake: StagedResourceIntake,
        metadata: UploadedObjectMetadata,
        expected_etag: str | None,
    ) -> None:
        if metadata.object_key != intake.storage_object_key:
            raise UploadApplicationError("UPLOAD_PROVIDER_PATH_MISMATCH", 409)
        if metadata.size_bytes != intake.declared_size_bytes:
            raise UploadApplicationError("UPLOAD_SIZE_MISMATCH", 409)
        if expected_etag and metadata.etag != expected_etag:
            raise UploadApplicationError("UPLOAD_ETAG_MISMATCH", 409)
        self._format_policy.resolve(
            filename=intake.normalized_filename,
            declared_content_type=metadata.content_type,
        )
        if (
            intake.resolved_file_format == SupportedUploadFormat.XLSX.value
            and not metadata.initial_bytes.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"))
        ):
            raise UploadApplicationError("UPLOAD_FORMAT_EVIDENCE_MISMATCH", 409)
        if (
            intake.resolved_file_format == SupportedUploadFormat.PARQUET.value
            and not metadata.initial_bytes.startswith(b"PAR1")
        ):
            raise UploadApplicationError("UPLOAD_FORMAT_EVIDENCE_MISMATCH", 409)

    @staticmethod
    def _validate_copied_metadata(
        *,
        intake: StagedResourceIntake,
        metadata: UploadedObjectMetadata,
    ) -> None:
        if metadata.size_bytes != intake.actual_size_bytes:
            raise UploadApplicationError("INTAKE_COPY_SIZE_MISMATCH", 409)

    def _verify_copied_checksum(
        self,
        *,
        intake: StagedResourceIntake,
        destination_object_key: str,
    ) -> None:
        """Verify copied bytes against the inspected intake SHA-256 digest."""
        provider = self._storage_factory.create_provider(intake.storage_provider)
        destination_uri = provider.get_uri(destination_object_key)
        with NamedTemporaryFile(delete=True) as handle:
            provider.download_to_file(destination_uri, handle.name)
            if self._sha256_file(Path(handle.name)) != intake.verified_checksum:
                raise UploadApplicationError("INTAKE_COPY_CHECKSUM_MISMATCH", 409)

    def _cleanup_intake(self, intake: StagedResourceIntake) -> None:

        if has_pending_intake_use(intake.id) or intake.source_deleted_at:
            return
        if intake.status == StagedResourceIntakeStatus.PUBLISHED:
            # Proposals still use this verified source for retries and other destinations.
            StagedResourceIntake.objects.filter(pk=intake.pk).update(cleanup_required=False)
            return
        # The periodic coordinator owns deletion, retention, simulation and retry accounting.
        StagedResourceIntake.objects.filter(pk=intake.pk).update(cleanup_required=True)

    @staticmethod
    def _mark_publication_cleanup_required(*, publication_id: uuid.UUID) -> None:
        """Record durable cleanup work for one unpublished copied object."""
        StagedResourcePublication.objects.filter(
            pk=publication_id,
            registration_submission__isnull=True,
        ).update(cleanup_required=True)

    def _cleanup_publication_destination(self, *, publication_id: uuid.UUID) -> None:
        """Register failed copied data for automatic policy cleanup after its retention period."""
        publication = StagedResourcePublication.objects.filter(
            pk=publication_id,
            registration_submission__isnull=True,
        ).first()
        if publication is None or not publication.destination_object_key:
            return

        storage = self._storage_factory.create_provider(publication.intake.storage_provider)
        stored = register_object(
            provider=publication.intake.storage_provider,
            uri=storage.get_uri(publication.destination_object_key),
            owner_kind="PUBLICATION",
            owner_id=publication.id,
            verified=True,
        )
        if stored is not None:
            ResourceStorageReference.objects.filter(
                storage_object=stored, owner_kind="PUBLICATION", owner_id=publication.id
            ).update(namespace_id=publication.registry_namespace_id)

    @staticmethod
    @transaction.atomic
    def _restore_after_publication_failure(*, intake_id: uuid.UUID) -> None:
        intake = StagedResourceIntake.objects.select_for_update().filter(pk=intake_id).first()
        if intake is None:
            return
        if intake.status in {
            StagedResourceIntakeStatus.CANCELLED,
            StagedResourceIntakeStatus.EXPIRED,
        }:
            return
        if StagedResourceIntakeService._has_intake_expired(intake):
            StagedResourceIntake.objects.filter(pk=intake_id).update(
                status=StagedResourceIntakeStatus.EXPIRED,
                cleanup_required=True,
                failure_code="INTAKE_EXPIRED",
            )
            return
        restored_status = (
            StagedResourceIntakeStatus.PUBLISHED
            if intake.publications.filter(registration_submission__isnull=False).exists()
            or intake.datasetchange_set.filter(status=DatasetChangeStatus.PUBLISHED).exists()
            else StagedResourceIntakeStatus.READY_FOR_PUBLICATION
        )
        StagedResourceIntake.objects.filter(pk=intake_id).update(status=restored_status)
        StagedResourceIntakeService.synchronize_dataset_publication(intake_id=intake_id)

    @staticmethod
    def _snapshot(intake: StagedResourceIntake) -> IntakeSnapshot:
        return IntakeSnapshot(
            intake_id=intake.id,
            status=str(intake.status),
            filename=intake.normalized_filename,
            file_format=intake.resolved_file_format,
            declared_size_bytes=int(intake.declared_size_bytes),
            actual_size_bytes=(int(intake.actual_size_bytes) if intake.actual_size_bytes else None),
            checksum_verification_status=intake.checksum_verification_status,
            inspection=dict(intake.inspection_json or {}),
            expires_at=intake.expires_at,
            failure_code=intake.failure_code,
            source_available=intake.source_deleted_at is None
            and intake.source_cleanup_started_at is None,
            source_deleted_at=intake.source_deleted_at,
        )

    @staticmethod
    def _has_intake_expired(intake: StagedResourceIntake) -> bool:
        """Return whether an active staging intake passed its retention deadline."""
        return intake.expires_at is not None and intake.expires_at <= timezone.now()

    def _assert_intake_available_for_publication(self, intake: StagedResourceIntake) -> None:
        """Reject an unpublished intake whose staging retention deadline elapsed."""
        if intake.source_deleted_at is not None or intake.source_cleanup_started_at is not None:
            raise UploadApplicationError("INTAKE_SOURCE_DELETED", 410)
        if intake.status != StagedResourceIntakeStatus.PUBLISHED and self._has_intake_expired(
            intake
        ):
            raise UploadApplicationError("INTAKE_EXPIRED", 409)

    @staticmethod
    def _assert_active_user(user: User) -> None:
        if not user.is_authenticated or not user.is_active or user.pk is None:
            raise UploadApplicationError("INTAKE_AUTHENTICATION_REQUIRED", 401)

    @staticmethod
    def _normalize_filename(filename: str) -> str:
        normalized = PurePath(str(filename or "").strip().replace("\\", "/")).name.strip()
        if not normalized or len(normalized) > _MAX_FILENAME_LENGTH:
            raise UploadApplicationError("UPLOAD_INVALID_FILENAME", 400)
        return normalized

    @staticmethod
    def _validate_size(size_bytes: int) -> None:
        if type(size_bytes) is not int or size_bytes <= 0:
            raise UploadApplicationError("UPLOAD_EMPTY_FILE", 400)
        if size_bytes > int(settings.RESOURCE_REGISTRY_UPLOAD_MAX_FILE_SIZE_BYTES):
            raise UploadApplicationError("UPLOAD_FILE_TOO_LARGE", 413)

    @staticmethod
    def _normalize_checksum(
        algorithm: str | None,
        checksum: str | None,
    ) -> tuple[str | None, str | None]:
        if algorithm is None and checksum is None:
            return None, None
        if str(algorithm).lower() != "sha256" or not re.fullmatch(
            r"[0-9a-fA-F]{64}", str(checksum)
        ):
            raise UploadApplicationError("UPLOAD_INVALID_CHECKSUM", 400)
        return "sha256", str(checksum).lower()

    @staticmethod
    def _fingerprint(
        *, user_id: int, filename: str, content_type: str, size_bytes: int, checksum: str | None
    ) -> str:
        payload = json.dumps(
            {
                "user": user_id,
                "filename": filename,
                "content_type": content_type.strip().lower(),
                "size": size_bytes,
                "checksum": checksum,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _publication_upload_idempotency_key(
        *, intake_id: uuid.UUID, namespace_id: uuid.UUID, publication_attempt: int
    ) -> str:
        """Scope completed-upload idempotency to one immutable publication attempt."""
        return f"intake:{intake_id}:{namespace_id}:{publication_attempt}"

    @staticmethod
    def _sha256_file(path: Path) -> str:
        """Calculate a SHA-256 digest used to verify a destination copy."""
        return StagedResourceIntakeInspectionService.calculate_sha256_file(path)

    @staticmethod
    def _enforce_user_quota(*, user: User) -> None:
        pending = StagedResourceIntake.objects.filter(
            submitted_by=user,
            status__in=_EXPIRABLE_STATUSES,
        ).count()
        if pending >= int(settings.RESOURCE_REGISTRY_UPLOAD_MAX_PENDING_PER_USER):
            raise UploadApplicationError("INTAKE_USER_QUOTA_EXCEEDED", 429)
