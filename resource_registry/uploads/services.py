"""Application services for authenticated direct-to-storage uploads."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import timedelta
from pathlib import PurePath
from typing import Never

from django.conf import settings
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.db.models.query import QuerySet
from django.utils import timezone

from Organization.models import Organization, Workspace
from resource_registry.authorization.exceptions import RegistryAuthorizationContextError
from resource_registry.authorization.registry_authorization_context_service import (
    RegistryAuthorizationContextService,
)
from resource_registry.models import (
    LargeFileUploadSession,
    LargeFileUploadStatus,
)
from resource_registry.uploads.contracts import (
    DirectUploadAuthorization,
    LargeFileUploadProvider,
    SupportedUploadFormat,
    UploadedObjectMetadata,
)
from resource_registry.uploads.exceptions import UploadApplicationError
from resource_registry.uploads.format_policy import UploadFileFormatPolicy

_logger = logging.getLogger("app-logger")
_ACTIVE_STATUSES = (
    LargeFileUploadStatus.PENDING_UPLOAD,
    LargeFileUploadStatus.VALIDATING,
    LargeFileUploadStatus.FAILED,
)
_IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_MAX_FILENAME_LENGTH = 255
_CONTROL_CHARACTER_LIMIT = 32


@dataclass(frozen=True, kw_only=True)
class UploadAdmission:
    """Carry one current canonical Workspace or Organization upload authorization."""

    organization_id: int
    workspace_id: int | None
    namespace_id: uuid.UUID
    actor_id: int


@dataclass(frozen=True, kw_only=True)
class UploadSessionSnapshot:
    """Expose sanitized upload state without storage coordinates."""

    upload_id: uuid.UUID
    status: str
    filename: str
    file_format: str
    declared_content_type: str
    detected_content_type: str | None
    declared_size_bytes: int
    actual_size_bytes: int | None
    checksum_verification_status: str
    created_at: object
    expires_at: object
    completed_at: object | None
    cancelled_at: object | None
    failure_code: str | None
    ready_for_registration: bool


@dataclass(frozen=True, kw_only=True)
class CreatedUploadSession:
    """Return a sanitized session snapshot plus ephemeral upload authorization."""

    session: UploadSessionSnapshot
    authorization: DirectUploadAuthorization | None
    idempotent_replay: bool
    max_file_size_bytes: int
    storage_provider: str


class LargeFileUploadAuthorizationService:
    """Authorize current user access through Organization, Workspace, and namespace mapping."""

    permission_name = "resource_registry.create_large_file_upload"

    def __init__(
        self,
        *,
        context_service: RegistryAuthorizationContextService | None = None,
    ) -> None:
        self._context_service = context_service or RegistryAuthorizationContextService()

    def admit_existing_upload(
        self,
        *,
        user: User,
        organization: Organization | None,
        upload_id: uuid.UUID,
        require_mutation: bool,
    ) -> UploadAdmission:
        """Admit the persisted upload owner without disclosing a foreign upload."""
        upload_owner = (
            LargeFileUploadSession.objects.filter(pk=upload_id)
            .values_list("workspace_id", "registry_namespace_id")
            .first()
        )
        if upload_owner is None:
            self._deny("UPLOAD_NOT_FOUND", 404)
        workspace_id, namespace_id = upload_owner
        if workspace_id is None:
            return self.admit_organization(
                user=user,
                organization=organization,
                require_mutation=require_mutation,
                expected_namespace_id=namespace_id,
            )
        return self.admit(
            user=user,
            organization=organization,
            workspace_id=workspace_id,
            require_mutation=require_mutation,
        )

    def admit(
        self,
        *,
        user: User,
        organization: Organization | None,
        workspace_id: int,
        require_mutation: bool,
    ) -> UploadAdmission:
        """Return canonical provenance for one active authorized Workspace."""
        actor_id = getattr(user, "pk", None)
        if (
            organization is None
            or organization.pk is None
            or not organization.is_active
            or not organization.resource_registry_enabled
            or not getattr(user, "is_authenticated", False)
            or not isinstance(actor_id, int)
            or not getattr(user, "is_active", False)
            or not User.objects.filter(pk=actor_id, is_active=True).exists()
        ):
            self._deny("UPLOAD_NOT_FOUND", 404)
        try:
            context = self._context_service.build_context(user=user, organization=organization)
        except RegistryAuthorizationContextError:
            self._deny("UPLOAD_NOT_FOUND", 404)
        if not context.authorized_workspaces.contains_workspace(
            workspace_id
        ) or not context.authorized_namespaces.contains_workspace(workspace_id):
            self._deny("UPLOAD_NOT_FOUND", 404)
        if not Workspace.objects.filter(
            pk=workspace_id,
            organization_id=organization.pk,
            is_active=True,
            resource_registry_enabled=True,
        ).exists():
            self._deny("UPLOAD_NOT_FOUND", 404)
        if require_mutation and not user.has_perm(self.permission_name):
            self._deny("UPLOAD_CAPABILITY_DENIED", 403)
        namespace_id = next(
            mapping.namespace_id
            for mapping in context.authorized_namespaces.mappings
            if mapping.workspace_id == workspace_id
        )
        return UploadAdmission(
            organization_id=int(organization.pk),
            workspace_id=workspace_id,
            namespace_id=namespace_id,
            actor_id=actor_id,
        )

    def admit_organization(
        self,
        *,
        user: User,
        organization: Organization | None,
        require_mutation: bool,
        expected_namespace_id: uuid.UUID | None = None,
    ) -> UploadAdmission:
        """Return canonical provenance for one administrator-authorized Organization.

        Args:
            user: Authenticated persisted caller.
            organization: Trusted active Organization.
            require_mutation: Whether the caller needs upload mutation capability.
            expected_namespace_id: Optional persisted namespace assertion.

        Returns:
            Organization-owned upload admission with no Workspace selector.

        Raises:
            UploadApplicationError: If Organization authorization or namespace resolution fails.
        """
        actor_id = getattr(user, "pk", None)
        if (
            organization is None
            or organization.pk is None
            or not organization.is_active
            or not organization.resource_registry_enabled
            or not getattr(user, "is_authenticated", False)
            or not isinstance(actor_id, int)
            or not getattr(user, "is_active", False)
        ):
            self._deny("UPLOAD_NOT_FOUND", 404)
        try:
            context = self._context_service.build_context(user=user, organization=organization)
        except RegistryAuthorizationContextError:
            self._deny("UPLOAD_NOT_FOUND", 404)
        organization_mapping = next(
            (
                mapping
                for mapping in context.authorized_namespaces.mappings
                if mapping.workspace_id is None
            ),
            None,
        )
        if organization_mapping is None or (
            expected_namespace_id is not None
            and organization_mapping.namespace_id != expected_namespace_id
        ):
            self._deny("UPLOAD_NOT_FOUND", 404)
        if require_mutation and not user.has_perm(self.permission_name):
            self._deny("UPLOAD_CAPABILITY_DENIED", 403)
        return UploadAdmission(
            organization_id=int(organization.pk),
            workspace_id=None,
            namespace_id=organization_mapping.namespace_id,
            actor_id=actor_id,
        )

    @staticmethod
    def _deny(code: str, http_status: int) -> Never:
        _logger.info(
            "Resource Registry upload authorization denied.",
            extra={
                "event_code": "UPLOAD_AUTHORIZATION_DENIED",
                "failure_code": code,
            },
        )
        raise UploadApplicationError(code, http_status)


class LargeFileUploadService:
    """Own upload creation, verification, cancellation, status, and expiration transitions."""

    def __init__(
        self,
        *,
        provider: LargeFileUploadProvider,
        format_policy: UploadFileFormatPolicy | None = None,
    ) -> None:
        self._provider = provider
        self._format_policy = format_policy or UploadFileFormatPolicy()

    def create_session(
        self,
        *,
        admission: UploadAdmission,
        user: User,
        filename: str,
        content_type: str,
        size_bytes: int,
        checksum_algorithm: str | None,
        checksum: str | None,
        idempotency_key: str,
    ) -> CreatedUploadSession:
        """Create or idempotently resolve one bounded upload session."""
        normalized_filename = self._normalize_filename(filename)
        file_format = self._format_policy.resolve(
            filename=normalized_filename,
            declared_content_type=content_type,
        )
        self._validate_size(size_bytes)
        normalized_algorithm, normalized_checksum = self._normalize_checksum(
            checksum_algorithm,
            checksum,
        )
        if not _IDEMPOTENCY_PATTERN.fullmatch(idempotency_key):
            raise UploadApplicationError("UPLOAD_INVALID_IDEMPOTENCY_KEY", 400)
        fingerprint = self._fingerprint(
            admission=admission,
            filename=normalized_filename,
            file_format=file_format,
            content_type=content_type,
            size_bytes=size_bytes,
            checksum_algorithm=normalized_algorithm,
            checksum=normalized_checksum,
        )
        now = timezone.now()
        authorization_ttl_seconds = min(
            max(int(settings.RESOURCE_REGISTRY_UPLOAD_AUTHORIZATION_TTL_SECONDS), 60),
            3600,
        )
        authorization_expires_at = now + timedelta(seconds=authorization_ttl_seconds)
        expires_at = now + timedelta(
            seconds=max(int(settings.RESOURCE_REGISTRY_UPLOAD_SESSION_TTL_SECONDS), 300),
        )
        idempotent_replay = False
        try:
            with transaction.atomic():
                User.objects.select_for_update().get(pk=admission.actor_id)
                if admission.workspace_id is not None:
                    Workspace.objects.select_for_update().get(pk=admission.workspace_id)
                existing = (
                    LargeFileUploadSession.objects.select_for_update()
                    .filter(
                        organization_id=admission.organization_id,
                        submitted_by_id=admission.actor_id,
                        idempotency_key=idempotency_key,
                    )
                    .first()
                )
                if existing is not None:
                    if existing.request_fingerprint != fingerprint or existing.status in {
                        LargeFileUploadStatus.CANCELLED,
                        LargeFileUploadStatus.EXPIRED,
                    }:
                        self._audit_rejection("UPLOAD_IDEMPOTENCY_CONFLICT")
                        raise UploadApplicationError("UPLOAD_IDEMPOTENCY_CONFLICT", 409)
                    session = existing
                    idempotent_replay = True
                else:
                    self._enforce_quotas(admission=admission, size_bytes=size_bytes)
                    upload_id = uuid.uuid4()
                    suffix = self._format_policy.extension_for(file_format)
                    object_key = (
                        f"resource-registry/uploads/{admission.organization_id}/"
                        f"{admission.namespace_id}/{upload_id}/source{suffix}"
                    )
                    session = LargeFileUploadSession.objects.create(
                        id=upload_id,
                        organization_id=admission.organization_id,
                        workspace_id=admission.workspace_id,
                        registry_namespace_id=admission.namespace_id,
                        submitted_by=user,
                        original_filename=normalized_filename,
                        normalized_filename=normalized_filename,
                        resolved_file_format=file_format.value,
                        declared_content_type=content_type.strip().lower(),
                        declared_size_bytes=size_bytes,
                        storage_provider=self._provider.provider_name,
                        storage_object_key=object_key,
                        checksum_algorithm=normalized_algorithm,
                        declared_checksum=normalized_checksum,
                        idempotency_key=idempotency_key,
                        request_fingerprint=fingerprint,
                        upload_authorization_expires_at=authorization_expires_at,
                        expires_at=expires_at,
                    )
        except IntegrityError:
            concurrent_session = LargeFileUploadSession.objects.filter(
                organization_id=admission.organization_id,
                submitted_by_id=admission.actor_id,
                idempotency_key=idempotency_key,
            ).first()
            if concurrent_session is None or concurrent_session.request_fingerprint != fingerprint:
                self._audit_rejection("UPLOAD_IDEMPOTENCY_CONFLICT")
                raise UploadApplicationError("UPLOAD_IDEMPOTENCY_CONFLICT", 409) from None
            session = concurrent_session
            idempotent_replay = True
        authorization: DirectUploadAuthorization | None = None
        if idempotent_replay and session.status == LargeFileUploadStatus.COMPLETED:
            self._audit("UPLOAD_SESSION_REUSED", session)
            return CreatedUploadSession(
                session=self._snapshot(session),
                authorization=None,
                idempotent_replay=True,
                max_file_size_bytes=int(settings.RESOURCE_REGISTRY_UPLOAD_MAX_FILE_SIZE_BYTES),
                storage_provider=session.storage_provider,
            )
        if session.status not in {
            LargeFileUploadStatus.PENDING_UPLOAD,
            LargeFileUploadStatus.FAILED,
        }:
            self._audit_rejection("UPLOAD_INVALID_STATE")
            raise UploadApplicationError("UPLOAD_INVALID_STATE", 409)
        if session.expires_at <= now:
            self._audit_rejection("UPLOAD_EXPIRED")
            raise UploadApplicationError("UPLOAD_EXPIRED", 409)
        try:
            authorization = self._provider.create_upload_authorization(
                object_key=session.storage_object_key,
                content_type=session.declared_content_type,
                expires_at=authorization_expires_at,
                recommended_chunk_size_bytes=int(
                    settings.RESOURCE_REGISTRY_UPLOAD_RECOMMENDED_CHUNK_SIZE_BYTES
                ),
                max_parallel_chunks=int(settings.RESOURCE_REGISTRY_UPLOAD_MAX_PARALLEL_CHUNKS),
            )
        except UploadApplicationError as error:
            LargeFileUploadSession.objects.filter(pk=session.pk).update(
                status=LargeFileUploadStatus.FAILED,
                failure_code=error.code,
                failure_message_sanitized="Upload provider authorization failed.",
            )
            raise
        authorization_recorded = LargeFileUploadSession.objects.filter(
            pk=session.pk,
            status__in=(
                LargeFileUploadStatus.PENDING_UPLOAD,
                LargeFileUploadStatus.FAILED,
            ),
            expires_at__gt=timezone.now(),
        ).update(upload_authorization_expires_at=authorization_expires_at)
        if authorization_recorded != 1:
            self._audit_rejection("UPLOAD_INVALID_STATE")
            raise UploadApplicationError("UPLOAD_INVALID_STATE", 409)
        session.upload_authorization_expires_at = authorization_expires_at
        self._audit(
            "UPLOAD_SESSION_REUSED" if idempotent_replay else "UPLOAD_SESSION_CREATED",
            session,
        )
        self._audit("UPLOAD_FILE_FORMAT_RESOLVED", session)
        self._audit("UPLOAD_AUTHORIZATION_ISSUED", session)
        return CreatedUploadSession(
            session=self._snapshot(session),
            authorization=authorization,
            idempotent_replay=idempotent_replay,
            max_file_size_bytes=int(settings.RESOURCE_REGISTRY_UPLOAD_MAX_FILE_SIZE_BYTES),
            storage_provider=session.storage_provider,
        )

    def get_session(
        self,
        *,
        upload_id: uuid.UUID,
        admission: UploadAdmission,
    ) -> UploadSessionSnapshot:
        """Return one currently authorized sanitized upload snapshot."""
        session = self._authorized_queryset(admission).filter(pk=upload_id).first()
        if session is None:
            raise UploadApplicationError("UPLOAD_NOT_FOUND", 404)
        return self._snapshot(session)

    def complete_session(
        self,
        *,
        upload_id: uuid.UUID,
        admission: UploadAdmission,
        expected_etag: str | None = None,
    ) -> UploadSessionSnapshot:
        """Verify provider metadata and atomically complete one upload."""
        failure: UploadApplicationError | None = None
        with transaction.atomic():
            session = self._locked_authorized_session(upload_id=upload_id, admission=admission)
            if session.status == LargeFileUploadStatus.COMPLETED:
                return self._snapshot(session)
            if session.status not in {
                LargeFileUploadStatus.PENDING_UPLOAD,
                LargeFileUploadStatus.FAILED,
            }:
                raise UploadApplicationError("UPLOAD_INVALID_STATE", 409)
            if session.expires_at <= timezone.now():
                session.status = LargeFileUploadStatus.EXPIRED
                session.failure_code = "UPLOAD_EXPIRED"
                session.cleanup_required = True
                session.save()
                failure = UploadApplicationError("UPLOAD_EXPIRED", 409)
            else:
                session.status = LargeFileUploadStatus.VALIDATING
                session.failure_code = None
                session.failure_message_sanitized = None
                session.save()
                self._audit("UPLOAD_VALIDATION_STARTED", session)
                try:
                    metadata = self._provider.inspect_uploaded_object(
                        object_key=session.storage_object_key,
                    )
                    self._validate_provider_metadata(
                        session=session,
                        metadata=metadata,
                        expected_etag=expected_etag,
                    )
                except UploadApplicationError as error:
                    session.status = LargeFileUploadStatus.FAILED
                    session.failure_code = error.code
                    session.failure_message_sanitized = "Upload validation failed."
                    session.cleanup_required = False
                    session.save()
                    if error.code == "UPLOAD_FORMAT_EVIDENCE_MISMATCH":
                        self._audit("UPLOAD_FORMAT_SIGNATURE_MISMATCH", session)
                    failure = error
                else:
                    self._apply_completed_metadata(session=session, metadata=metadata)
        if failure is not None:
            self._audit("UPLOAD_COMPLETION_FAILED", session)
            raise failure
        self._audit("UPLOAD_COMPLETED", session)
        return self._snapshot(session)

    def cancel_session(
        self,
        *,
        upload_id: uuid.UUID,
        admission: UploadAdmission,
    ) -> UploadSessionSnapshot:
        """Idempotently cancel an active upload and clean exactly its object."""
        with transaction.atomic():
            session = self._locked_authorized_session(upload_id=upload_id, admission=admission)
            if session.status == LargeFileUploadStatus.CANCELLED:
                return self._snapshot(session)
            if session.status == LargeFileUploadStatus.COMPLETED:
                raise UploadApplicationError("UPLOAD_INVALID_STATE", 409)
            if session.status == LargeFileUploadStatus.EXPIRED:
                raise UploadApplicationError("UPLOAD_EXPIRED", 409)
            session.status = LargeFileUploadStatus.CANCELLED
            session.cancelled_at = timezone.now()
            session.cleanup_required = True
            session.save()
        self._cleanup(session)
        self._audit("UPLOAD_CANCELLED", session)
        return self._snapshot(session)

    def expire_sessions(self, *, batch_size: int) -> int:
        """Expire a bounded batch of abandoned sessions and attempt exact-object cleanup."""
        now = timezone.now()
        limit = max(int(batch_size), 1)
        with transaction.atomic():
            sessions = tuple(
                LargeFileUploadSession.objects.select_for_update(skip_locked=True)
                .filter(status__in=_ACTIVE_STATUSES, expires_at__lt=now)
                .order_by("expires_at", "id")[:limit]
            )
            for session in sessions:
                session.status = LargeFileUploadStatus.EXPIRED
                session.failure_code = "UPLOAD_EXPIRED"
                session.cleanup_required = True
                session.save()
            retry_cleanup = tuple(
                LargeFileUploadSession.objects.select_for_update(skip_locked=True)
                .filter(
                    cleanup_required=True,
                )
                .filter(
                    status__in=(
                        LargeFileUploadStatus.CANCELLED,
                        LargeFileUploadStatus.EXPIRED,
                    ),
                    upload_authorization_expires_at__lte=now,
                )
                .exclude(pk__in=tuple(session.pk for session in sessions))
                .order_by("updated_at", "id")[: max(limit - len(sessions), 0)]
            )
        for session in (*sessions, *retry_cleanup):
            self._cleanup(session)
            self._audit(
                "UPLOAD_EXPIRED" if session in sessions else "UPLOAD_CLEANUP_RETRIED",
                session,
            )
        return len(sessions)

    def _validate_provider_metadata(
        self,
        *,
        session: LargeFileUploadSession,
        metadata: UploadedObjectMetadata,
        expected_etag: str | None,
    ) -> None:
        if metadata.object_key != session.storage_object_key:
            raise UploadApplicationError("UPLOAD_PROVIDER_PATH_MISMATCH", 409)
        if metadata.size_bytes <= 0:
            raise UploadApplicationError("UPLOAD_EMPTY_FILE", 409)
        if metadata.size_bytes != session.declared_size_bytes:
            raise UploadApplicationError("UPLOAD_SIZE_MISMATCH", 409)
        if metadata.size_bytes > int(settings.RESOURCE_REGISTRY_UPLOAD_MAX_FILE_SIZE_BYTES):
            raise UploadApplicationError("UPLOAD_FILE_TOO_LARGE", 413)
        self._format_policy.resolve(
            filename=session.normalized_filename,
            declared_content_type=metadata.content_type,
        )
        if expected_etag and metadata.etag != expected_etag:
            raise UploadApplicationError("UPLOAD_ETAG_MISMATCH", 409)
        file_format = SupportedUploadFormat(session.resolved_file_format)
        if file_format == SupportedUploadFormat.XLSX and not metadata.initial_bytes.startswith(
            (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
        ):
            raise UploadApplicationError("UPLOAD_FORMAT_EVIDENCE_MISMATCH", 409)
        if file_format == SupportedUploadFormat.CSV:
            sample = metadata.initial_bytes
            if not sample:
                raise UploadApplicationError("UPLOAD_FORMAT_EVIDENCE_MISMATCH", 409)
            has_unicode_bom = sample.startswith(
                (b"\xff\xfe", b"\xfe\xff", b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")
            )
            if sample and not has_unicode_bom and sample.count(b"\x00") * 5 >= len(sample):
                raise UploadApplicationError("UPLOAD_FORMAT_EVIDENCE_MISMATCH", 409)
        if file_format == SupportedUploadFormat.PARQUET:
            if not metadata.initial_bytes.startswith(b"PAR1"):
                raise UploadApplicationError("UPLOAD_FORMAT_EVIDENCE_MISMATCH", 409)
        # JSONL is validated by the streaming reader during asynchronous inspection.
        # A bounded prefix cannot prove that a full first record is present.
        if file_format == SupportedUploadFormat.JSON:
            sample = metadata.initial_bytes.lstrip(b"\xef\xbb\xbf").lstrip()
            if not sample.startswith(b"["):
                raise UploadApplicationError("UPLOAD_FORMAT_EVIDENCE_MISMATCH", 409)

    @staticmethod
    def _apply_completed_metadata(
        *,
        session: LargeFileUploadSession,
        metadata: UploadedObjectMetadata,
    ) -> None:
        session.status = LargeFileUploadStatus.COMPLETED
        session.actual_size_bytes = metadata.size_bytes
        session.detected_content_type = metadata.content_type
        session.storage_etag = metadata.etag
        session.storage_version_id = metadata.version_id
        session.provider_reported_checksum = metadata.provider_checksum
        session.checksum_verification_status = (
            "DEFERRED" if session.declared_checksum else "NOT_PROVIDED"
        )
        session.uploaded_at = timezone.now()
        session.completed_at = session.uploaded_at
        session.cleanup_required = False
        session.save()

    def _cleanup(self, session: LargeFileUploadSession) -> None:
        try:
            self._provider.delete_object(object_key=session.storage_object_key)
        except UploadApplicationError:
            session.cleanup_required = True
            session.failure_code = "UPLOAD_PROVIDER_CLEANUP_FAILED"
            session.failure_message_sanitized = "Upload object cleanup failed."
            LargeFileUploadSession.objects.filter(pk=session.pk).update(
                cleanup_required=True,
                failure_code=session.failure_code,
                failure_message_sanitized=session.failure_message_sanitized,
            )
            self._audit("UPLOAD_CLEANUP_FAILED", session)
            return
        cleanup_required = (
            session.status
            in {
                LargeFileUploadStatus.CANCELLED,
                LargeFileUploadStatus.EXPIRED,
            }
            and session.upload_authorization_expires_at > timezone.now()
        )
        update_fields: dict[str, object] = {"cleanup_required": cleanup_required}
        session.cleanup_required = cleanup_required
        if session.failure_code == "UPLOAD_PROVIDER_CLEANUP_FAILED":
            session.failure_code = None
            session.failure_message_sanitized = None
            update_fields.update(failure_code=None, failure_message_sanitized=None)
        LargeFileUploadSession.objects.filter(pk=session.pk).update(**update_fields)

    def _enforce_quotas(self, *, admission: UploadAdmission, size_bytes: int) -> None:
        active = LargeFileUploadSession.objects.filter(status__in=_ACTIVE_STATUSES)
        if active.filter(submitted_by_id=admission.actor_id).count() >= int(
            settings.RESOURCE_REGISTRY_UPLOAD_MAX_PENDING_PER_USER
        ):
            self._audit_rejection("UPLOAD_USER_QUOTA_EXCEEDED")
            raise UploadApplicationError("UPLOAD_USER_QUOTA_EXCEEDED", 429)
        workspace_active = active.filter(workspace_id=admission.workspace_id)
        if workspace_active.count() >= int(
            settings.RESOURCE_REGISTRY_UPLOAD_MAX_PENDING_PER_WORKSPACE
        ):
            self._audit_rejection("UPLOAD_WORKSPACE_QUOTA_EXCEEDED")
            raise UploadApplicationError("UPLOAD_WORKSPACE_QUOTA_EXCEEDED", 429)
        pending_bytes = workspace_active.aggregate(total=Sum("declared_size_bytes"))["total"] or 0
        if pending_bytes + size_bytes > int(
            settings.RESOURCE_REGISTRY_UPLOAD_MAX_PENDING_BYTES_PER_WORKSPACE
        ):
            self._audit_rejection("UPLOAD_BYTES_QUOTA_EXCEEDED")
            raise UploadApplicationError("UPLOAD_BYTES_QUOTA_EXCEEDED", 429)

    @staticmethod
    def _normalize_filename(filename: str) -> str:
        candidate = str(filename or "").strip().replace("\\", "/")
        normalized = PurePath(candidate).name.strip()
        if not normalized:
            raise UploadApplicationError("UPLOAD_EMPTY_FILENAME", 400)
        if len(normalized) > _MAX_FILENAME_LENGTH or any(
            ord(character) < _CONTROL_CHARACTER_LIMIT for character in normalized
        ):
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
        if str(algorithm or "").lower() != "sha256" or not re.fullmatch(
            r"[0-9a-fA-F]{64}",
            str(checksum or ""),
        ):
            raise UploadApplicationError("UPLOAD_INVALID_CHECKSUM", 400)
        return "sha256", str(checksum).lower()

    @staticmethod
    def _fingerprint(
        *,
        admission: UploadAdmission,
        filename: str,
        file_format: SupportedUploadFormat,
        content_type: str,
        size_bytes: int,
        checksum_algorithm: str | None,
        checksum: str | None,
    ) -> str:
        payload = {
            "actor": admission.actor_id,
            "organization": admission.organization_id,
            "workspace": admission.workspace_id,
            "namespace": str(admission.namespace_id),
            "operation": "resource_registry.upload.create",
            "filename": filename,
            "file_format": file_format.value,
            "content_type": content_type.strip().lower(),
            "size_bytes": size_bytes,
            "checksum_algorithm": checksum_algorithm,
            "checksum": checksum,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _snapshot(session: LargeFileUploadSession) -> UploadSessionSnapshot:
        return UploadSessionSnapshot(
            upload_id=session.id,
            status=str(session.status),
            filename=session.normalized_filename,
            file_format=session.resolved_file_format,
            declared_content_type=session.declared_content_type,
            detected_content_type=session.detected_content_type,
            declared_size_bytes=int(session.declared_size_bytes),
            actual_size_bytes=(
                int(session.actual_size_bytes) if session.actual_size_bytes is not None else None
            ),
            checksum_verification_status=session.checksum_verification_status,
            created_at=session.created_at,
            expires_at=session.expires_at,
            completed_at=session.completed_at,
            cancelled_at=session.cancelled_at,
            failure_code=session.failure_code,
            ready_for_registration=session.status == LargeFileUploadStatus.COMPLETED,
        )

    @staticmethod
    def _authorized_queryset(
        admission: UploadAdmission,
    ) -> QuerySet[LargeFileUploadSession]:
        return LargeFileUploadSession.objects.filter(
            organization_id=admission.organization_id,
            workspace_id=admission.workspace_id,
            registry_namespace_id=admission.namespace_id,
        )

    def _locked_authorized_session(
        self,
        *,
        upload_id: uuid.UUID,
        admission: UploadAdmission,
    ) -> LargeFileUploadSession:
        session = (
            self._authorized_queryset(admission).select_for_update().filter(pk=upload_id).first()
        )
        if session is None:
            raise UploadApplicationError("UPLOAD_NOT_FOUND", 404)
        return session

    @staticmethod
    def _audit(event_code: str, session: LargeFileUploadSession) -> None:
        _logger.info(
            "Resource Registry upload lifecycle event.",
            extra={
                "event_code": event_code,
                "upload_id": str(session.id),
                "upload_status": str(session.status),
                "file_format": session.resolved_file_format,
                "storage_provider": session.storage_provider,
                "failure_code": str(session.failure_code or ""),
                "size_bucket": LargeFileUploadService._size_bucket(
                    int(session.actual_size_bytes or session.declared_size_bytes)
                ),
            },
        )

    @staticmethod
    def _audit_rejection(failure_code: str) -> None:
        _logger.info(
            "Resource Registry upload request rejected.",
            extra={
                "event_code": "UPLOAD_REQUEST_REJECTED",
                "failure_code": failure_code,
            },
        )

    @staticmethod
    def _size_bucket(size_bytes: int) -> str:
        if size_bytes < 1024**2:
            return "lt_1_mib"
        if size_bytes < 1024**3:
            return "lt_1_gib"
        if size_bytes < 10 * 1024**3:
            return "lt_10_gib"
        return "gte_10_gib"
