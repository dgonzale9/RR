"""Application service for authorized completed-upload registration submission."""

from __future__ import annotations

import logging
import uuid

from django.contrib.auth.models import User
from django.db import transaction
from guardian.shortcuts import get_objects_for_user

from resource_registry.models import (
    Dataset,
    DatasetStatus,
    DatasetVisibilityScope,
    LargeFileUploadSession,
    LargeFileUploadStatus,
    RegistrationSubmission,
    RegistrationSubmissionStatus,
    WorkbookIngestionGroup,
)
from resource_registry.registration_submissions.contracts import (
    RegistrationJobKind,
    RegistrationSubmissionResult,
)
from resource_registry.registration_submissions.format_resolver import (
    RegistrationSubmissionFormatResolver,
)
from resource_registry.registration_submissions.job_factory import RegistrationSubmissionJobFactory
from resource_registry.uploads.exceptions import UploadApplicationError
from resource_registry.uploads.services import LargeFileUploadAuthorizationService, UploadAdmission
from Organization.models import Organization

_logger = logging.getLogger("app-logger")


class RegistrationSubmissionService:
    """Authorize and persist one idempotent registration request and async job."""

    permission_name = "resource_registry.submit_upload_registration"

    def submit_replacement(
        self,
        *,
        user: User,
        organization: Organization | None,
        namespace_id: uuid.UUID,
        upload_id: uuid.UUID,
        dataset_id: uuid.UUID | None = None,
        workbook_group_id: uuid.UUID | None = None,
    ) -> RegistrationSubmissionResult:
        """Queue an exact Dataset or workbook replacement from an owned completed upload.

        Args:
            user: Current authenticated actor.
            organization: Trusted request Organization.
            namespace_id: Canonical Workspace namespace admitted for this invocation.
            upload_id: Completed upload owned by the actor.
            dataset_id: Exact non-workbook Dataset target.
            workbook_group_id: Exact multi-sheet Workbook target.

        Returns:
            Stable asynchronous registration submission.

        Raises:
            UploadApplicationError: If selection, authorization, or upload state is invalid.
        """
        if (dataset_id is None) == (workbook_group_id is None):
            raise UploadApplicationError("REPLACEMENT_TARGET_INVALID", 400)
        admission = self._authorization_service.admit_existing_upload(
            user=user,
            organization=organization,
            upload_id=upload_id,
            require_mutation=True,
        )
        if admission.workspace_id is None or admission.namespace_id != namespace_id:
            raise UploadApplicationError("AUTHORIZATION_DENIED", 403)
        if not user.has_perm(self.permission_name):
            raise UploadApplicationError("AUTHORIZATION_DENIED", 403)
        with transaction.atomic():
            upload = self._get_locked_upload(upload_id=upload_id, admission=admission)
            self._validate_upload_state(upload)
            if upload.submitted_by_id != admission.actor_id:
                raise UploadApplicationError("AUTHORIZATION_DENIED", 403)
            job_kind = self._format_resolver.resolve(
                resolved_file_format=upload.resolved_file_format,
            )
            if (job_kind == RegistrationJobKind.WORKBOOK_INGESTION) != (
                workbook_group_id is not None
            ):
                raise UploadApplicationError("REPLACEMENT_TARGET_INVALID", 400)
            target_dataset = None
            target_group = None
            if dataset_id is not None:
                target_dataset = self._get_authorized_replacement_dataset(
                    user=user,
                    admission=admission,
                    dataset_id=dataset_id,
                )
            else:
                target_group = self._get_authorized_replacement_workbook_group(
                    user=user,
                    admission=admission,
                    workbook_group_id=workbook_group_id,
                )
            existing = (
                RegistrationSubmission.objects.select_for_update()
                .filter(upload_session=upload)
                .first()
            )
            if existing is not None:
                if (
                    existing.replacement_dataset_id != dataset_id
                    or existing.replacement_workbook_group_id != workbook_group_id
                ):
                    raise UploadApplicationError("UPLOAD_ALREADY_REGISTERED", 409)
                self._job_factory.get_job_reference(submission=existing, job_kind=job_kind)
                return self._result(existing, idempotent_replay=True)
            submission = RegistrationSubmission.objects.create(
                upload_session=upload,
                replacement_dataset=target_dataset,
                replacement_workbook_group=target_group,
                organization_id=admission.organization_id,
                workspace_id=admission.workspace_id,
                registry_namespace_id=admission.namespace_id,
                submitted_by=user,
                status=RegistrationSubmissionStatus.SUBMITTED,
                job_type=job_kind.value,
                visibility_scope=DatasetVisibilityScope.WORKSPACE,
            )
            self._job_factory.create_job(
                job_kind=job_kind,
                submission=submission,
                upload=upload,
                actor=user,
            )
            return self._result(submission, idempotent_replay=False)

    def assert_replacement_execution_authorized(
        self,
        *,
        submission: RegistrationSubmission,
    ) -> None:
        """Recheck a queued replacement against current actor and Workspace grants.

        Args:
            submission: Persisted target and upload provenance.

        Raises:
            UploadApplicationError: If a grant or target ownership has changed.
        """
        user = submission.submitted_by
        if user is None or not user.is_active:
            raise UploadApplicationError("AUTHORIZATION_DENIED", 403)
        admission = self._authorization_service.admit_existing_upload(
            user=user,
            organization=submission.organization,
            upload_id=submission.upload_session_id,
            require_mutation=True,
        )
        if (
            admission.workspace_id != submission.workspace_id
            or admission.namespace_id != submission.registry_namespace_id
            or admission.workspace_id is None
            or not user.has_perm(self.permission_name)
            or submission.upload_session.submitted_by_id != user.pk
        ):
            raise UploadApplicationError("AUTHORIZATION_DENIED", 403)
        if submission.replacement_dataset_id is not None:
            self._get_authorized_replacement_dataset(
                user=user,
                admission=admission,
                dataset_id=submission.replacement_dataset_id,
            )
        elif submission.replacement_workbook_group_id is not None:
            self._get_authorized_replacement_workbook_group(
                user=user,
                admission=admission,
                workbook_group_id=submission.replacement_workbook_group_id,
            )

    @staticmethod
    def _get_authorized_replacement_dataset(
        *,
        user: User,
        admission: UploadAdmission,
        dataset_id: uuid.UUID,
    ) -> Dataset:
        """Resolve one exact Workspace-owned Dataset with write permission."""
        dataset = Dataset.objects.filter(pk=dataset_id).first()
        if dataset is None:
            raise UploadApplicationError("DATASET_NOT_FOUND", 404)
        if (
            dataset.tenant_id != admission.namespace_id
            or dataset.organization_id != admission.organization_id
            or dataset.workspace_id != admission.workspace_id
            or dataset.visibility_scope != DatasetVisibilityScope.WORKSPACE
            or dataset.status == DatasetStatus.ARCHIVED
            or dataset.workbook_group_id is not None
        ):
            raise UploadApplicationError("AUTHORIZATION_DENIED", 403)
        if not (
            user.has_perm("resource_registry.change_dataset")
            or user.has_perm("resource_registry.change_dataset", dataset)
        ):
            raise UploadApplicationError("AUTHORIZATION_DENIED", 403)
        return dataset

    @staticmethod
    def _get_authorized_replacement_workbook_group(
        *,
        user: User,
        admission: UploadAdmission,
        workbook_group_id: uuid.UUID | None,
    ) -> WorkbookIngestionGroup:
        """Resolve a Workbook only when every linked Dataset is writable."""
        if workbook_group_id is None:
            raise UploadApplicationError("WORKBOOK_GROUP_NOT_FOUND", 404)
        group = WorkbookIngestionGroup.objects.filter(pk=workbook_group_id).first()
        if group is None:
            raise UploadApplicationError("WORKBOOK_GROUP_NOT_FOUND", 404)
        datasets = list(Dataset.objects.filter(workbook_group=group))
        if (
            group.tenant_id != admission.namespace_id
            or not datasets
            or not user.has_perm("resource_registry.add_dataset")
        ):
            raise UploadApplicationError("AUTHORIZATION_DENIED", 403)
        if any(
            dataset.tenant_id != admission.namespace_id
            or dataset.organization_id != admission.organization_id
            or dataset.workspace_id != admission.workspace_id
            or dataset.visibility_scope != DatasetVisibilityScope.WORKSPACE
            for dataset in datasets
        ):
            raise UploadApplicationError("AUTHORIZATION_DENIED", 403)
        if not user.has_perm("resource_registry.change_dataset"):
            permitted_ids = set(
                get_objects_for_user(
                    user,
                    "resource_registry.change_dataset",
                    klass=Dataset,
                )
                .filter(pk__in=[dataset.pk for dataset in datasets])
                .values_list("pk", flat=True),
            )
            if any(dataset.pk not in permitted_ids for dataset in datasets):
                raise UploadApplicationError("AUTHORIZATION_DENIED", 403)
        return group

    def __init__(
        self,
        *,
        authorization_service: LargeFileUploadAuthorizationService | None = None,
        format_resolver: RegistrationSubmissionFormatResolver | None = None,
        job_factory: RegistrationSubmissionJobFactory | None = None,
    ) -> None:
        self._authorization_service = authorization_service or LargeFileUploadAuthorizationService()
        self._format_resolver = format_resolver or RegistrationSubmissionFormatResolver()
        self._job_factory = job_factory or RegistrationSubmissionJobFactory()

    def submit(
        self,
        *,
        user: User,
        organization: Organization | None,
        upload_id: uuid.UUID,
        visibility_scope: str = DatasetVisibilityScope.WORKSPACE,
    ) -> RegistrationSubmissionResult:
        """Submit one completed upload for later format-specific worker execution.

        Args:
            user: Current authenticated actor.
            organization: Trusted request Organization.
            upload_id: Immutable upload identifier from the URL.
            visibility_scope: Requested Dataset visibility within the admitted Organization.

        Returns:
            Stable idempotent registration submission result.

        Raises:
            UploadApplicationError: If authorization, state, format, or ownership is invalid.
        """
        admission = self._authorization_service.admit_existing_upload(
            user=user,
            organization=organization,
            upload_id=upload_id,
            require_mutation=False,
        )
        if visibility_scope not in DatasetVisibilityScope.values:
            raise UploadApplicationError("DATASET_VISIBILITY_SCOPE_INVALID", 400)
        if not user.has_perm(self.permission_name):
            self._audit_denial("AUTHORIZATION_DENIED")
            raise UploadApplicationError("AUTHORIZATION_DENIED", 403)
        with transaction.atomic():
            upload = self._get_locked_upload(upload_id=upload_id, admission=admission)
            self._validate_upload_state(upload)
            job_kind = self._format_resolver.resolve(
                resolved_file_format=upload.resolved_file_format,
            )
            existing = (
                RegistrationSubmission.objects.select_for_update()
                .filter(upload_session=upload)
                .first()
            )
            if existing is not None:
                persisted_kind = RegistrationJobKind(existing.job_type)
                if persisted_kind != job_kind:
                    raise UploadApplicationError("UPLOAD_ALREADY_REGISTERED", 409)
                if existing.visibility_scope != visibility_scope:
                    raise UploadApplicationError("UPLOAD_ALREADY_REGISTERED", 409)
                self._job_factory.get_job_reference(
                    submission=existing,
                    job_kind=persisted_kind,
                )
                result = self._result(existing, idempotent_replay=True)
            else:
                submission = RegistrationSubmission.objects.create(
                    upload_session=upload,
                    organization_id=admission.organization_id,
                    workspace_id=admission.workspace_id,
                    registry_namespace_id=admission.namespace_id,
                    submitted_by=user,
                    status=RegistrationSubmissionStatus.SUBMITTED,
                    job_type=job_kind.value,
                    visibility_scope=visibility_scope,
                )
                self._job_factory.create_job(
                    job_kind=job_kind,
                    submission=submission,
                    upload=upload,
                    actor=user,
                )
                result = self._result(submission, idempotent_replay=False)
        self._audit_submission(result)
        return result

    def submit_pre_authorized(
        self,
        *,
        user: User,
        admission: UploadAdmission,
        upload_id: uuid.UUID,
        visibility_scope: str,
    ) -> RegistrationSubmissionResult:
        """Submit a completed upload admitted by the superuser-only Admin workflow."""
        if not user.is_superuser:
            raise UploadApplicationError("AUTHORIZATION_DENIED", 403)
        if visibility_scope not in DatasetVisibilityScope.values:
            raise UploadApplicationError("DATASET_VISIBILITY_SCOPE_INVALID", 400)
        with transaction.atomic():
            upload = self._get_locked_upload(upload_id=upload_id, admission=admission)
            self._validate_upload_state(upload)
            job_kind = self._format_resolver.resolve(
                resolved_file_format=upload.resolved_file_format
            )
            existing = (
                RegistrationSubmission.objects.select_for_update()
                .filter(upload_session=upload)
                .first()
            )
            if existing is not None:
                persisted_kind = RegistrationJobKind(existing.job_type)
                if persisted_kind != job_kind or existing.visibility_scope != visibility_scope:
                    raise UploadApplicationError("UPLOAD_ALREADY_REGISTERED", 409)
                self._job_factory.get_job_reference(submission=existing, job_kind=persisted_kind)
                result = self._result(existing, idempotent_replay=True)
            else:
                submission = RegistrationSubmission.objects.create(
                    upload_session=upload,
                    organization_id=admission.organization_id,
                    workspace_id=admission.workspace_id,
                    registry_namespace_id=admission.namespace_id,
                    submitted_by=user,
                    status=RegistrationSubmissionStatus.SUBMITTED,
                    job_type=job_kind.value,
                    visibility_scope=visibility_scope,
                )
                self._job_factory.create_job(
                    job_kind=job_kind, submission=submission, upload=upload, actor=user
                )
                result = self._result(submission, idempotent_replay=False)
        self._audit_submission(result)
        return result

    @staticmethod
    def _get_locked_upload(
        *,
        upload_id: uuid.UUID,
        admission: UploadAdmission,
    ) -> LargeFileUploadSession:
        uploads = LargeFileUploadSession.objects.select_for_update().filter(
            pk=upload_id,
            organization_id=admission.organization_id,
            registry_namespace_id=admission.namespace_id,
        )
        if admission.workspace_id is None:
            uploads = uploads.filter(workspace__isnull=True)
        else:
            uploads = uploads.filter(workspace_id=admission.workspace_id)
        upload = uploads.first()
        if upload is None:
            raise UploadApplicationError("UPLOAD_NOT_FOUND", 404)
        return upload

    @staticmethod
    def _validate_upload_state(upload: LargeFileUploadSession) -> None:
        if upload.status == LargeFileUploadStatus.COMPLETED:
            return
        if upload.status == LargeFileUploadStatus.EXPIRED:
            raise UploadApplicationError("UPLOAD_EXPIRED", 409)
        if upload.status == LargeFileUploadStatus.CANCELLED:
            raise UploadApplicationError("UPLOAD_CANCELLED", 409)
        raise UploadApplicationError("UPLOAD_NOT_COMPLETED", 409)

    @staticmethod
    def _result(
        submission: RegistrationSubmission,
        *,
        idempotent_replay: bool,
    ) -> RegistrationSubmissionResult:
        return RegistrationSubmissionResult(
            registration_id=submission.id,
            status=str(submission.status),
            job_type=RegistrationJobKind(submission.job_type),
            idempotent_replay=idempotent_replay,
        )

    @staticmethod
    def _audit_submission(result: RegistrationSubmissionResult) -> None:
        _logger.info(
            "Resource Registry registration submission event.",
            extra={
                "event_code": (
                    "REGISTRATION_SUBMISSION_REUSED"
                    if result.idempotent_replay
                    else "REGISTRATION_SUBMITTED"
                ),
                "registration_id": str(result.registration_id),
                "registration_status": result.status,
                "job_type": result.job_type.value,
            },
        )

    @staticmethod
    def _audit_denial(failure_code: str) -> None:
        _logger.info(
            "Resource Registry registration submission denied.",
            extra={
                "event_code": "REGISTRATION_SUBMISSION_DENIED",
                "failure_code": failure_code,
            },
        )
