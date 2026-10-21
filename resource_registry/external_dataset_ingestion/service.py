"""Application orchestration for backend external Dataset ingestion."""

from __future__ import annotations

import logging
import re
import uuid

from django.contrib.auth.models import User

from BotServer.util.user_group_management import UserGroupManagement
from resource_registry.models import DatasetVisibilityScope, RegistrationSubmission
from resource_registry.read.exceptions import DataRegistryReadError
from resource_registry.read_auth.context import ReadAuthorizationContext
from resource_registry.read_auth.exceptions import DataRegistryAuthorizationError
from resource_registry.read_auth.policy import AuthorizedDataRegistryReadFacade
from resource_registry.registration_submissions.contracts import RegistrationJobKind
from resource_registry.registration_submissions.services import RegistrationSubmissionService
from resource_registry.registration_submissions.status_service import (
    RegistrationStatusNotFoundError,
    RegistrationSubmissionStatusReadService,
)
from resource_registry.external_dataset_ingestion.contracts import (
    ExternalDatasetFileProvider,
    ExternalDatasetIngestionResult,
    ExternalDatasetIngestionStatus,
)
from resource_registry.external_dataset_ingestion.exceptions import ExternalDatasetIngestionError
from resource_registry.uploads.exceptions import UploadApplicationError
from resource_registry.uploads.server_acquisition_service import ServerAcquiredUploadService
from resource_registry.uploads.services import LargeFileUploadAuthorizationService
from Organization.models import Organization

_logger = logging.getLogger("app-logger")
_IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_COMPLETED_STATUSES = frozenset({"completed", "completed_with_skips"})
_TERMINAL_STATUSES = frozenset({*_COMPLETED_STATUSES, "failed", "cancelled"})


class ExternalDatasetIngestionService:
    """Authorize, acquire, materialize, and submit one external Dataset file."""

    def __init__(
        self,
        *,
        authorization_service: LargeFileUploadAuthorizationService | None = None,
        server_upload_service: ServerAcquiredUploadService | None = None,
        submission_service: RegistrationSubmissionService | None = None,
        status_service: RegistrationSubmissionStatusReadService | None = None,
        read_facade: AuthorizedDataRegistryReadFacade | None = None,
    ) -> None:
        """Initialize application dependencies.

        Args:
            authorization_service: Canonical Workspace and Namespace authorization boundary.
            server_upload_service: Authoritative server-acquired upload lifecycle boundary.
            submission_service: Existing asynchronous registration submission boundary.
            status_service: Existing scoped registration status read boundary.
            read_facade: Existing authorized Dataset read boundary.
        """
        self._authorization_service = authorization_service or LargeFileUploadAuthorizationService()
        self._server_upload_service = server_upload_service or ServerAcquiredUploadService()
        self._submission_service = submission_service or RegistrationSubmissionService()
        self._status_service = status_service or RegistrationSubmissionStatusReadService()
        self._read_facade = read_facade or AuthorizedDataRegistryReadFacade()

    def register(  # noqa: PLR0913
        self,
        *,
        user: User,
        organization: Organization | None,
        workspace_id: int | None,
        provider: ExternalDatasetFileProvider,
        idempotency_key: str,
        visibility_scope: str = DatasetVisibilityScope.WORKSPACE,
    ) -> ExternalDatasetIngestionResult:
        """Submit one provider-acquired file through existing registration infrastructure.

        Args:
            user: Authenticated persisted caller.
            organization: Trusted request Organization.
            workspace_id: Workspace selector for WORKSPACE visibility; omitted for ORGANIZATION.
            provider: Approved external file acquisition adapter.
            idempotency_key: Caller key scoped by Organization and actor.
            visibility_scope: Workspace-only or Organization-wide Dataset visibility.

        Returns:
            Safe asynchronous registration result.

        Raises:
            UploadApplicationError: If Workspace, Namespace, or Django permissions deny access.
            ExternalDatasetIngestionError: If acquisition or storage materialization fails safely.
        """
        if not _IDEMPOTENCY_PATTERN.fullmatch(idempotency_key):
            raise ExternalDatasetIngestionError(
                "DATASET_REGISTRATION_INVALID_IDEMPOTENCY_KEY",
                400,
                "The idempotency key is invalid.",
            )
        if visibility_scope not in DatasetVisibilityScope.values:
            raise ExternalDatasetIngestionError(
                "DATASET_VISIBILITY_SCOPE_INVALID",
                400,
                "The dataset visibility scope is invalid.",
            )
        if (
            visibility_scope == DatasetVisibilityScope.ORGANIZATION
            and "admin" not in self._roles_for_user(user)
        ):
            raise UploadApplicationError("AUTHORIZATION_DENIED", 403)
        if visibility_scope == DatasetVisibilityScope.WORKSPACE:
            if workspace_id is None:
                raise ExternalDatasetIngestionError(
                    "DATASET_WORKSPACE_REQUIRED",
                    400,
                    "A Workspace is required for this visibility.",
                )
            admission = self._authorization_service.admit(
                user=user,
                organization=organization,
                workspace_id=workspace_id,
                require_mutation=True,
            )
        else:
            if workspace_id is not None:
                raise ExternalDatasetIngestionError(
                    "DATASET_WORKSPACE_FORBIDDEN",
                    400,
                    "A Workspace is not valid for Organization visibility.",
                )
            admission = self._authorization_service.admit_organization(
                user=user,
                organization=organization,
                require_mutation=True,
            )
        if not user.has_perm(RegistrationSubmissionService.permission_name):
            raise UploadApplicationError("AUTHORIZATION_DENIED", 403)
        try:
            descriptor = provider.acquire()
        except ExternalDatasetIngestionError:
            raise
        except Exception as error:
            raise ExternalDatasetIngestionError(
                "EXTERNAL_FILE_ACQUISITION_FAILED",
                503,
                "The external file could not be acquired.",
            ) from error
        try:
            materialized = self._server_upload_service.materialize(
                admission=admission,
                user=user,
                local_path=descriptor.local_path,
                filename=descriptor.filename,
                content_type=descriptor.content_type,
                size_bytes=descriptor.size_bytes,
                sha256=descriptor.sha256,
                acquisition_provider=descriptor.provider,
                acquisition_reference_hash=descriptor.reference_hash,
                idempotency_key=idempotency_key,
            )
            submission = self._submission_service.submit(
                user=user,
                organization=organization,
                upload_id=materialized.upload.id,
                visibility_scope=visibility_scope,
            )
        finally:
            provider.cleanup(descriptor)
        _logger.info(
            "Dataset registration accepted.",
            extra={
                "event_code": "DATASET_REGISTRATION_ACCEPTED",
                "provider": descriptor.provider,
                "reference_hash": descriptor.reference_hash,
                "workspace_id": admission.workspace_id,
                "namespace_id": str(admission.namespace_id),
                "registration_id": str(submission.registration_id),
            },
        )
        return ExternalDatasetIngestionResult(
            registration_id=submission.registration_id,
            workspace_id=admission.workspace_id,
            namespace_id=admission.namespace_id,
            state="queued",
            job_type=submission.job_type.value,
            idempotent_replay=materialized.idempotent_replay or submission.idempotent_replay,
        )

    def get_status(
        self,
        *,
        user: User,
        organization: Organization | None,
        workspace_id: int | None,
        registration_id: uuid.UUID,
    ) -> ExternalDatasetIngestionStatus:
        """Return one persisted-namespace-scoped status with authorized Dataset metadata."""
        persisted_workspace_id = (
            RegistrationSubmission.objects.filter(pk=registration_id)
            .values_list("workspace_id", flat=True)
            .first()
        )
        if persisted_workspace_id is None:
            admission = self._authorization_service.admit_organization(
                user=user,
                organization=organization,
                require_mutation=False,
            )
        else:
            admission = self._authorization_service.admit(
                user=user,
                organization=organization,
                workspace_id=int(persisted_workspace_id),
                require_mutation=False,
            )
        if workspace_id is not None and workspace_id != admission.workspace_id:
            raise ExternalDatasetIngestionError(
                "REGISTRATION_NOT_FOUND", 404, "Registration was not found."
            )
        try:
            snapshot = self._status_service.get_status(
                organization_id=admission.organization_id,
                workspace_id=admission.workspace_id,
                namespace_id=admission.namespace_id,
                registration_id=registration_id,
            )
        except RegistrationStatusNotFoundError as error:
            raise ExternalDatasetIngestionError(
                "REGISTRATION_NOT_FOUND", 404, "Registration was not found."
            ) from error
        normalized_status = snapshot.status.strip().lower()
        if normalized_status not in {
            "submitted",
            "queued",
            "running",
            "completed",
            "completed_with_skips",
            "failed",
            "cancelled",
        }:
            raise ExternalDatasetIngestionError(
                "REGISTRATION_STATUS_INVALID", 500, "Registration status is unavailable."
            )
        successful = normalized_status in _COMPLETED_STATUSES
        try:
            datasets = (
                self._resolve_completed_datasets(
                    user=user,
                    namespace_id=admission.namespace_id,
                    job_type=snapshot.job_type,
                    result_payload=dict(snapshot.result_payload),
                )
                if successful
                else ()
            )
        except (DataRegistryAuthorizationError, DataRegistryReadError) as error:
            raise ExternalDatasetIngestionError(
                "REGISTRATION_RESULT_UNAVAILABLE",
                500,
                "Registration result is unavailable.",
            ) from error
        return ExternalDatasetIngestionStatus(
            registration_id=registration_id,
            workspace_id=admission.workspace_id,
            namespace_id=admission.namespace_id,
            state=normalized_status,
            terminal=normalized_status in _TERMINAL_STATUSES,
            successful=successful,
            job_type=snapshot.job_type.value,
            error_code=snapshot.error_code,
            datasets=datasets,
        )

    def _resolve_completed_datasets(
        self,
        *,
        user: User,
        namespace_id: uuid.UUID,
        job_type: RegistrationJobKind,
        result_payload: dict[str, object],
    ) -> tuple[dict[str, object], ...]:
        identifiers = self._extract_dataset_ids(job_type=job_type, result_payload=result_payload)
        context = ReadAuthorizationContext(
            tenant_id=str(namespace_id),
            actor=user.username,
            roles=self._roles_for_user(user),
        )
        results: list[dict[str, object]] = []
        for dataset_id in identifiers:
            dataset = self._read_facade.get_dataset(context, dataset_id)
            versions = self._read_facade.list_versions(context, dataset_id, limit=1, offset=0)
            if not versions:
                raise ExternalDatasetIngestionError(
                    "REGISTRATION_RESULT_INVALID", 500, "Registration result is unavailable."
                )
            version = versions[0]
            results.append(
                {
                    "dataset_id": dataset.id,
                    "name": dataset.name,
                    "status": dataset.status,
                    "dataset_version_id": version.id,
                    "version_status": version.status,
                    "row_count": version.row_count,
                    "column_count": version.column_count,
                }
            )
        return tuple(results)

    @staticmethod
    def _extract_dataset_ids(
        *,
        job_type: RegistrationJobKind,
        result_payload: dict[str, object],
    ) -> tuple[str, ...]:
        raw_values: list[object] = []
        if job_type == RegistrationJobKind.DATASET_REGISTRATION:
            raw_values.append(result_payload.get("dataset_id"))
        else:
            for key in ("created", "deduplicated"):
                values = result_payload.get(key, [])
                if isinstance(values, list):
                    raw_values.extend(
                        value.get("dataset_id") for value in values if isinstance(value, dict)
                    )
        identifiers: list[str] = []
        for raw_value in raw_values:
            try:
                identifier = str(uuid.UUID(str(raw_value)))
            except (TypeError, ValueError) as error:
                raise ExternalDatasetIngestionError(
                    "REGISTRATION_RESULT_INVALID", 500, "Registration result is unavailable."
                ) from error
            if identifier not in identifiers:
                identifiers.append(identifier)
        return tuple(identifiers)

    @staticmethod
    def _roles_for_user(user: User) -> tuple[str, ...]:
        if (
            user.is_superuser
            or UserGroupManagement.is_user_admin(user)  # type: ignore[no-untyped-call]
        ):
            return ("admin",)
        return ("reader",)
