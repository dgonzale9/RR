"""Import one current chat attachment as a new or replacement Workspace Dataset."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import cast
from uuid import UUID

from django.contrib.auth.models import User
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.db.models import Q
from django.utils.text import slugify

from Organization.models import Organization, Workspace
from resource_registry.models import (
    Dataset,
    DatasetRegistrationJob,
    DatasetRegistrationJobStatus,
    DatasetSourceType,
    DatasetStatus,
    DatasetVisibilityScope,
    RegistrationSubmission,
    WorkbookIngestionGroup,
    WorkbookIngestionJob,
    WorkbookIngestionJobStatus,
)
from resource_registry.registration_submissions.services import RegistrationSubmissionService
from resource_registry.uploads.exceptions import UploadApplicationError
from resource_registry.uploads.format_policy import UploadFileFormatPolicy
from resource_registry.uploads.server_acquisition_service import ServerAcquiredUploadService
from resource_registry.uploads.services import LargeFileUploadAuthorizationService, UploadAdmission

CHAT_DATASET_IMPORT_MAX_FILE_SIZE_BYTES = 300 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ChatDatasetImportResult:
    """Describe one accepted chat-driven registration or replacement.

    Args:
        registration_id: Durable identifier of the queued operation.
        status: Persisted submission state.
        job_type: Dataset or Workbook worker family.
        action: Register for a first file, or replace for an existing target.
        dataset_id: Existing flat Dataset identifier when replacing.
        workbook_group_id: Existing Workbook identifier when replacing.
        idempotent_replay: Whether this request returned an earlier submission.
    """

    registration_id: UUID
    status: str
    job_type: str
    action: str
    dataset_id: UUID | None
    workbook_group_id: UUID | None
    idempotent_replay: bool


class ChatDatasetImportService:
    """Select a Workspace target and submit a verified current-turn attachment."""

    def __init__(
        self,
        *,
        authorization_service: LargeFileUploadAuthorizationService | None = None,
        server_upload_service: ServerAcquiredUploadService | None = None,
        submission_service: RegistrationSubmissionService | None = None,
    ) -> None:
        """Compose existing authorization, upload, and submission boundaries.

        Args:
            authorization_service: Workspace upload authorization service.
            server_upload_service: Server-side verified Registry upload service.
            submission_service: Async registration and replacement service.
        """
        self._authorization_service = authorization_service or LargeFileUploadAuthorizationService()
        self._server_upload_service = server_upload_service or ServerAcquiredUploadService()
        self._submission_service = submission_service or RegistrationSubmissionService()

    def submit(  # noqa: PLR0913
        self,
        *,
        user: User,
        organization: Organization | None,
        workspace_id: int,
        namespace_id: UUID,
        uploaded_file: UploadedFile,
        idempotency_key: str,
        expected_dataset_id: UUID | None = None,
        expected_workbook_group_id: UUID | None = None,
    ) -> ChatDatasetImportResult:
        """Queue a first registration or replacement from a chat attachment.

        Args:
            user: Authenticated actor who attached the file.
            organization: Authenticated Organization from the request.
            workspace_id: Current middleware-selected Workspace.
            namespace_id: Expected canonical Workspace Namespace.
            uploaded_file: File from the current chat request, not a stored filename.
            idempotency_key: Stable key for repeated BIF calls in the same request.
            expected_dataset_id: Optional discovered Dataset assertion from the agent.
            expected_workbook_group_id: Optional discovered Workbook assertion.

        Returns:
            Accepted async operation and its safe target reference.

        Raises:
            UploadApplicationError: If authorization, format, identity, or storage fails.
        """
        admission = self._authorization_service.admit(
            user=user,
            organization=organization,
            workspace_id=workspace_id,
            require_mutation=True,
        )
        if admission.namespace_id != namespace_id or admission.workspace_id is None:
            raise UploadApplicationError("AUTHORIZATION_DENIED", 403)
        if not user.has_perm(RegistrationSubmissionService.permission_name):
            raise UploadApplicationError("AUTHORIZATION_DENIED", 403)
        filename = str(uploaded_file.name or "")
        if (
            not filename
            or len(filename) > 255
            or filename != Path(filename).name
            or "\\" in filename
            or any(ord(character) < 32 for character in filename)
        ):
            raise UploadApplicationError("UPLOAD_INVALID_FILENAME", 400)
        content_type = str(uploaded_file.content_type or "")
        UploadFileFormatPolicy().resolve(
            filename=filename,
            declared_content_type=content_type,
        )
        if (
            uploaded_file.size is not None
            and uploaded_file.size > CHAT_DATASET_IMPORT_MAX_FILE_SIZE_BYTES
        ):
            raise UploadApplicationError("EXTERNAL_FILE_TOO_LARGE", 413)
        dataset, group = self._resolve_authorized_target(
            user=user,
            admission=admission,
            filename=filename,
        )
        self._validate_expected_target(
            dataset=dataset,
            group=group,
            expected_dataset_id=expected_dataset_id,
            expected_workbook_group_id=expected_workbook_group_id,
        )
        if dataset is None and group is None:
            if not user.has_perm("resource_registry.add_dataset"):
                raise UploadApplicationError("AUTHORIZATION_DENIED", 403)
        try:
            with NamedTemporaryFile(suffix=Path(filename).suffix) as temporary_file:
                file_hash = hashlib.sha256()
                size_bytes = 0
                for chunk in uploaded_file.chunks():
                    size_bytes += len(chunk)
                    if size_bytes > CHAT_DATASET_IMPORT_MAX_FILE_SIZE_BYTES:
                        raise UploadApplicationError("EXTERNAL_FILE_TOO_LARGE", 413)
                    temporary_file.write(chunk)
                    file_hash.update(chunk)
                temporary_file.flush()
                checksum = file_hash.hexdigest()
                reference_hash = hashlib.sha256(
                    f"{namespace_id}:{idempotency_key}:{filename}".encode("utf-8")
                ).hexdigest()
                materialized = self._server_upload_service.materialize(
                    admission=admission,
                    user=user,
                    local_path=Path(temporary_file.name),
                    filename=filename,
                    content_type=content_type,
                    size_bytes=size_bytes,
                    sha256=checksum,
                    acquisition_provider="chat_attachment",
                    acquisition_reference_hash=reference_hash,
                    idempotency_key=idempotency_key,
                )
        except OSError as error:
            raise UploadApplicationError("CHAT_FILE_TEMPORARY_STORAGE_FAILED", 503) from error
        existing = RegistrationSubmission.objects.filter(
            upload_session=materialized.upload,
            submitted_by=user,
            registry_namespace_id=namespace_id,
        ).first()
        if existing is not None and materialized.idempotent_replay:
            return self._to_result(existing, idempotent_replay=True)
        with transaction.atomic():
            Workspace.objects.select_for_update().get(pk=workspace_id)
            dataset, group = self._resolve_authorized_target(
                user=user,
                admission=admission,
                filename=filename,
            )
            self._validate_expected_target(
                dataset=dataset,
                group=group,
                expected_dataset_id=expected_dataset_id,
                expected_workbook_group_id=expected_workbook_group_id,
            )
            if dataset is not None or group is not None:
                submission = self._submission_service.submit_replacement(
                    user=user,
                    organization=organization,
                    namespace_id=namespace_id,
                    upload_id=materialized.upload.id,
                    dataset_id=dataset.id if dataset is not None else None,
                    workbook_group_id=group.id if group is not None else None,
                )
            else:
                if not user.has_perm("resource_registry.add_dataset"):
                    raise UploadApplicationError("AUTHORIZATION_DENIED", 403)
                self._assert_no_pending_first_import(admission=admission, filename=filename)
                submission = self._submission_service.submit(
                    user=user,
                    organization=organization,
                    upload_id=materialized.upload.id,
                )
        return ChatDatasetImportResult(
            registration_id=submission.registration_id,
            status=submission.status,
            job_type=submission.job_type.value,
            action="replace" if dataset is not None or group is not None else "register",
            dataset_id=dataset.id if dataset is not None else None,
            workbook_group_id=group.id if group is not None else None,
            idempotent_replay=submission.idempotent_replay,
        )

    @staticmethod
    def _validate_expected_target(
        *,
        dataset: Dataset | None,
        group: WorkbookIngestionGroup | None,
        expected_dataset_id: UUID | None,
        expected_workbook_group_id: UUID | None,
    ) -> None:
        """Require optional agent-discovered IDs to match the authorized file identity.

        Args:
            dataset: Matching authorized flat Dataset, if present.
            group: Matching authorized Workbook group, if present.
            expected_dataset_id: Agent-discovered flat Dataset ID, if supplied.
            expected_workbook_group_id: Agent-discovered Workbook group ID, if supplied.

        Raises:
            UploadApplicationError: If selectors conflict or name matching found another target.
        """
        if expected_dataset_id is not None and expected_workbook_group_id is not None:
            raise UploadApplicationError("REPLACEMENT_TARGET_INVALID", 400)
        if expected_dataset_id is not None and (
            dataset is None or dataset.id != expected_dataset_id
        ):
            raise UploadApplicationError("REPLACEMENT_TARGET_MISMATCH", 409)
        if expected_workbook_group_id is not None and (
            group is None or group.id != expected_workbook_group_id
        ):
            raise UploadApplicationError("REPLACEMENT_TARGET_MISMATCH", 409)

    def _resolve_authorized_target(
        self, *, user: User, admission: UploadAdmission, filename: str
    ) -> tuple[Dataset | None, WorkbookIngestionGroup | None]:
        """Find one exact Workspace file identity or leave the file for first registration."""
        if admission.workspace_id is None:
            raise UploadApplicationError("AUTHORIZATION_DENIED", 403)
        suffix = Path(filename).suffix.lower()
        if suffix == ".xlsx":
            groups = list(
                WorkbookIngestionGroup.objects.filter(
                    tenant_id=admission.namespace_id,
                    source_file_name__iexact=filename,
                    datasets__organization_id=admission.organization_id,
                    datasets__workspace_id=admission.workspace_id,
                    datasets__visibility_scope=DatasetVisibilityScope.WORKSPACE,
                    datasets__status=DatasetStatus.ACTIVE,
                ).distinct()[:2]
            )
            if len(groups) > 1:
                raise UploadApplicationError("DATASET_MATCH_AMBIGUOUS", 409)
            if groups:
                group = cast(WorkbookIngestionGroup, groups[0])
                self._submission_service._get_authorized_replacement_workbook_group(
                    user=user, admission=admission, workbook_group_id=group.id
                )
                return None, group
            return None, None
        base_slug = slugify(Path(filename).stem.strip()) or "dataset"
        format_suffix = f"-{suffix.lstrip('.')}"
        formatted_slug = f"{base_slug[: 120 - len(format_suffix)]}{format_suffix}"
        datasets = list(
            Dataset.objects.filter(
                tenant_id=admission.namespace_id,
                organization_id=admission.organization_id,
                workspace_id=admission.workspace_id,
                visibility_scope=DatasetVisibilityScope.WORKSPACE,
                source_type=DatasetSourceType.FILE_UPLOAD,
                workbook_group__isnull=True,
            )
            .exclude(status=DatasetStatus.ARCHIVED)
            .filter(
                Q(name__iexact=filename)
                | Q(slug=formatted_slug)
                | Q(slug=base_slug[:120], name__iendswith=suffix)
            )[:2]
        )
        if len(datasets) > 1:
            raise UploadApplicationError("DATASET_MATCH_AMBIGUOUS", 409)
        if datasets:
            dataset = cast(Dataset, datasets[0])
            self._submission_service._get_authorized_replacement_dataset(
                user=user, admission=admission, dataset_id=dataset.id
            )
            return dataset, None
        return None, None

    @staticmethod
    def _assert_no_pending_first_import(*, admission: UploadAdmission, filename: str) -> None:
        """Avoid queueing two first registrations for the same Workspace file identity."""
        base_filter = {
            "registration_submission__organization_id": admission.organization_id,
            "registration_submission__workspace_id": admission.workspace_id,
            "registration_submission__registry_namespace_id": admission.namespace_id,
            "registration_submission__replacement_dataset__isnull": True,
            "registration_submission__replacement_workbook_group__isnull": True,
            "registration_submission__upload_session__normalized_filename__iexact": filename,
        }
        if Path(filename).suffix.lower() == ".xlsx":
            has_pending = WorkbookIngestionJob.objects.filter(
                **base_filter,
                status__in=(
                    WorkbookIngestionJobStatus.SUBMITTED,
                    WorkbookIngestionJobStatus.QUEUED,
                    WorkbookIngestionJobStatus.RUNNING,
                ),
            ).exists()
        else:
            has_pending = DatasetRegistrationJob.objects.filter(
                **base_filter,
                status__in=(
                    DatasetRegistrationJobStatus.SUBMITTED,
                    DatasetRegistrationJobStatus.QUEUED,
                    DatasetRegistrationJobStatus.RUNNING,
                ),
            ).exists()
        if has_pending:
            raise UploadApplicationError("DATASET_IMPORT_IN_PROGRESS", 409)

    @staticmethod
    def _to_result(
        submission: RegistrationSubmission, *, idempotent_replay: bool
    ) -> ChatDatasetImportResult:
        """Describe an existing actor-owned submission without changing its target."""
        return ChatDatasetImportResult(
            registration_id=submission.id,
            status=submission.status,
            job_type=submission.job_type,
            action=(
                "replace"
                if submission.replacement_dataset_id or submission.replacement_workbook_group_id
                else "register"
            ),
            dataset_id=submission.replacement_dataset_id,
            workbook_group_id=submission.replacement_workbook_group_id,
            idempotent_replay=idempotent_replay,
        )
