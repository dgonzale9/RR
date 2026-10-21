"""Authoritative upload lifecycle for server-acquired files."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.utils import timezone

from resource_registry.models import LargeFileUploadSession, LargeFileUploadStatus
from resource_registry.storage.base import StorageProvider
from resource_registry.storage.exceptions import (
    StorageConfigurationError,
    StorageObjectNotFoundError,
    StorageProviderUnsupportedError,
    UnsafeStorageUriError,
)
from resource_registry.storage.factory import DataRegistryStorageFactory
from resource_registry.uploads.contracts import SupportedUploadFormat
from resource_registry.uploads.exceptions import UploadApplicationError
from resource_registry.uploads.format_policy import UploadFileFormatPolicy
from resource_registry.uploads.services import UploadAdmission
from Organization.models import Workspace

_logger = logging.getLogger("app-logger")
_SHA256_HEX_LENGTH = 64


@dataclass(frozen=True, kw_only=True, slots=True)
class MaterializedServerUpload:
    """Return one authoritative completed upload and its replay state."""

    upload: LargeFileUploadSession
    idempotent_replay: bool


class ServerAcquiredUploadService:
    """Own upload persistence and state transitions for trusted server-acquired files."""

    def __init__(
        self,
        *,
        storage_factory: DataRegistryStorageFactory | None = None,
        format_policy: UploadFileFormatPolicy | None = None,
    ) -> None:
        """Initialize provider-neutral upload lifecycle dependencies.

        Args:
            storage_factory: Resource Registry controlled-storage composition boundary.
            format_policy: Canonical upload format policy.
        """
        self._storage_factory = storage_factory or DataRegistryStorageFactory()
        self._format_policy = format_policy or UploadFileFormatPolicy()

    def materialize(  # noqa: PLR0913
        self,
        *,
        admission: UploadAdmission,
        user: User,
        local_path: Path,
        filename: str,
        content_type: str,
        size_bytes: int,
        sha256: str,
        acquisition_provider: str,
        acquisition_reference_hash: str,
        idempotency_key: str,
    ) -> MaterializedServerUpload:
        """Create, store, verify, and complete one server-acquired upload.

        Args:
            admission: Current canonical Workspace and Namespace admission.
            user: Persisted authenticated actor.
            local_path: Controlled provider artifact to copy into registry storage.
            filename: Provider-normalized source filename.
            content_type: Server-derived source content type.
            size_bytes: Server-measured byte size.
            sha256: Server-calculated SHA-256 digest.
            acquisition_provider: Approved provider identifier.
            acquisition_reference_hash: Non-secret opaque reference digest.
            idempotency_key: Caller key scoped by Organization and actor.

        Returns:
            Completed upload session and replay state.

        Raises:
            UploadApplicationError: If validation, idempotency, storage, or state checks fail.
        """
        file_format = self._validate_file(
            filename=filename,
            content_type=content_type,
            size_bytes=size_bytes,
            sha256=sha256,
        )
        fingerprint = self._fingerprint(
            admission=admission,
            filename=filename,
            content_type=content_type,
            size_bytes=size_bytes,
            sha256=sha256,
            acquisition_provider=acquisition_provider,
            acquisition_reference_hash=acquisition_reference_hash,
            idempotency_key=idempotency_key,
        )
        storage_provider = self._storage_factory.create_provider()
        upload, is_replay = self._create_or_claim_upload(
            admission=admission,
            user=user,
            file_format=file_format,
            filename=filename,
            content_type=content_type,
            size_bytes=size_bytes,
            sha256=sha256,
            acquisition_provider=acquisition_provider,
            acquisition_reference_hash=acquisition_reference_hash,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
            storage_provider=storage_provider,
        )
        if is_replay:
            return MaterializedServerUpload(upload=upload, idempotent_replay=True)
        try:
            metadata = storage_provider.put(str(local_path), upload.storage_object_key)
            if metadata.size_bytes != size_bytes:
                raise UploadApplicationError("EXTERNAL_FILE_INTEGRITY_FAILED", 409)
            completed_upload = self._complete_upload(
                upload_id=upload.id,
                size_bytes=size_bytes,
                content_type=content_type,
                sha256=sha256,
                storage_etag=metadata.etag,
                provider_checksum=str(metadata.checksum) if metadata.checksum else None,
            )
        except UploadApplicationError:
            self._compensate_failed_storage(
                storage_provider=storage_provider,
                upload_id=upload.id,
                object_key=upload.storage_object_key,
                failure_code="EXTERNAL_FILE_INTEGRITY_FAILED",
            )
            raise
        except (
            StorageConfigurationError,
            StorageObjectNotFoundError,
            StorageProviderUnsupportedError,
            UnsafeStorageUriError,
            OSError,
        ) as error:
            self._compensate_failed_storage(
                storage_provider=storage_provider,
                upload_id=upload.id,
                object_key=upload.storage_object_key,
                failure_code="EXTERNAL_FILE_STORAGE_FAILED",
            )
            raise UploadApplicationError("EXTERNAL_FILE_STORAGE_FAILED", 503) from error
        except Exception as error:
            self._compensate_failed_storage(
                storage_provider=storage_provider,
                upload_id=upload.id,
                object_key=upload.storage_object_key,
                failure_code="EXTERNAL_FILE_STORAGE_FAILED",
            )
            raise UploadApplicationError("EXTERNAL_FILE_STORAGE_FAILED", 503) from error
        return MaterializedServerUpload(upload=completed_upload, idempotent_replay=False)

    def _create_or_claim_upload(  # noqa: PLR0913
        self,
        *,
        admission: UploadAdmission,
        user: User,
        file_format: SupportedUploadFormat,
        filename: str,
        content_type: str,
        size_bytes: int,
        sha256: str,
        acquisition_provider: str,
        acquisition_reference_hash: str,
        idempotency_key: str,
        fingerprint: str,
        storage_provider: StorageProvider,
    ) -> tuple[LargeFileUploadSession, bool]:
        now = timezone.now()
        try:
            with transaction.atomic():
                type(user).objects.select_for_update().get(pk=admission.actor_id, is_active=True)
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
                    if existing.request_fingerprint != fingerprint:
                        raise UploadApplicationError(
                            "DATASET_REGISTRATION_IDEMPOTENCY_CONFLICT", 409
                        )
                    if existing.status == LargeFileUploadStatus.COMPLETED:
                        return existing, True
                    if (
                        existing.status == LargeFileUploadStatus.FAILED
                        and not existing.cleanup_required
                    ):
                        existing.status = LargeFileUploadStatus.VALIDATING
                        existing.failure_code = None
                        existing.failure_message_sanitized = None
                        existing.save()
                        return existing, False
                    raise UploadApplicationError("DATASET_REGISTRATION_UPLOAD_IN_PROGRESS", 409)
                upload_id = uuid.uuid4()
                suffix = self._format_policy.extension_for(file_format)
                storage_provider_name = str(
                    getattr(
                        storage_provider,
                        "PROVIDER_NAME",
                        getattr(settings, "RESOURCE_REGISTRY_STORAGE_PROVIDER", "local"),
                    )
                ).strip()
                upload = LargeFileUploadSession.objects.create(
                    id=upload_id,
                    organization_id=admission.organization_id,
                    workspace_id=admission.workspace_id,
                    registry_namespace_id=admission.namespace_id,
                    submitted_by=user,
                    original_filename=filename,
                    normalized_filename=filename,
                    resolved_file_format=file_format.value,
                    declared_content_type=content_type,
                    declared_size_bytes=size_bytes,
                    storage_provider=storage_provider_name,
                    storage_object_key=(
                        f"resource-registry/uploads/{admission.organization_id}/"
                        f"{admission.namespace_id}/{upload_id}/source{suffix}"
                    ),
                    acquisition_provider=acquisition_provider,
                    acquisition_reference_hash=acquisition_reference_hash,
                    checksum_algorithm="sha256",
                    declared_checksum=sha256,
                    idempotency_key=idempotency_key,
                    request_fingerprint=fingerprint,
                    upload_authorization_expires_at=now,
                    expires_at=now
                    + timedelta(
                        seconds=max(int(settings.RESOURCE_REGISTRY_UPLOAD_SESSION_TTL_SECONDS), 300)
                    ),
                    status=LargeFileUploadStatus.VALIDATING,
                )
                return upload, False
        except IntegrityError as error:
            raise UploadApplicationError(
                "DATASET_REGISTRATION_IDEMPOTENCY_CONFLICT", 409
            ) from error

    @staticmethod
    def _complete_upload(  # noqa: PLR0913
        *,
        upload_id: uuid.UUID,
        size_bytes: int,
        content_type: str,
        sha256: str,
        storage_etag: str | None,
        provider_checksum: str | None,
    ) -> LargeFileUploadSession:
        with transaction.atomic():
            upload = LargeFileUploadSession.objects.select_for_update().get(
                pk=upload_id,
                status=LargeFileUploadStatus.VALIDATING,
            )
            upload.status = LargeFileUploadStatus.COMPLETED
            upload.actual_size_bytes = size_bytes
            upload.detected_content_type = content_type
            upload.storage_etag = storage_etag
            upload.provider_reported_checksum = provider_checksum
            upload.verified_checksum = sha256
            upload.checksum_verification_status = "VERIFIED"
            upload.uploaded_at = timezone.now()
            upload.completed_at = upload.uploaded_at
            upload.failure_code = None
            upload.failure_message_sanitized = None
            upload.cleanup_required = False
            upload.save()
            return upload

    def _validate_file(
        self,
        *,
        filename: str,
        content_type: str,
        size_bytes: int,
        sha256: str,
    ) -> SupportedUploadFormat:
        if type(size_bytes) is not int or size_bytes <= 0:
            raise UploadApplicationError("EXTERNAL_FILE_EMPTY", 400)
        if size_bytes > int(settings.RESOURCE_REGISTRY_UPLOAD_MAX_FILE_SIZE_BYTES):
            raise UploadApplicationError("EXTERNAL_FILE_TOO_LARGE", 413)
        if len(sha256) != _SHA256_HEX_LENGTH or any(
            character not in "0123456789abcdef" for character in sha256
        ):
            raise UploadApplicationError("EXTERNAL_FILE_CHECKSUM_INVALID", 409)
        return self._format_policy.resolve(
            filename=filename,
            declared_content_type=content_type,
        )

    @staticmethod
    def _fingerprint(  # noqa: PLR0913
        *,
        admission: UploadAdmission,
        filename: str,
        content_type: str,
        size_bytes: int,
        sha256: str,
        acquisition_provider: str,
        acquisition_reference_hash: str,
        idempotency_key: str,
    ) -> str:
        payload = {
            "actor_id": admission.actor_id,
            "organization_id": admission.organization_id,
            "workspace_id": admission.workspace_id,
            "namespace_id": str(admission.namespace_id),
            "provider": acquisition_provider,
            "reference_hash": acquisition_reference_hash,
            "filename": filename,
            "content_type": content_type,
            "size_bytes": size_bytes,
            "sha256": sha256,
            "operation": "dataset_registration",
            "idempotency_key": idempotency_key,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _compensate_failed_storage(
        *,
        storage_provider: StorageProvider,
        upload_id: uuid.UUID,
        object_key: str,
        failure_code: str,
    ) -> None:
        cleanup_failed = False
        try:
            storage_provider.delete(object_key)
        except Exception:
            cleanup_failed = True
            _logger.warning(
                "Server-acquired upload storage cleanup failed.",
                extra={"event_code": "SERVER_ACQUIRED_UPLOAD_CLEANUP_FAILED"},
            )
        LargeFileUploadSession.objects.filter(pk=upload_id).update(
            status=LargeFileUploadStatus.FAILED,
            failure_code=failure_code,
            failure_message_sanitized="External file materialization failed.",
            cleanup_required=cleanup_failed,
        )
