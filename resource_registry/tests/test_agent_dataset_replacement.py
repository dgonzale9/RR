"""Integration checks for agent-initiated replacement of registered datasets."""

from __future__ import annotations

import json
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from django.conf import settings
from django.contrib.auth.models import Permission, User
from django.db import close_old_connections
from django.utils import timezone
from django.utils.functional import SimpleLazyObject
from openpyxl import Workbook  # type: ignore[import-untyped]  # Third-party stubs unavailable.

from BotServer.builtin_functions import resource_registry_functions
from Organization.models import Organization, Workspace
from resource_registry.application.dataset_registration import DatasetRegistrationFacade
from resource_registry.application.exceptions import ValidationError
from resource_registry.bifs.read_only_namespace_admission import AdmittedBifNamespace
from resource_registry.models import (
    Dataset,
    DatasetRegistrationJob,
    DatasetVersion,
    LargeFileUploadSession,
    LargeFileUploadStatus,
    RegistrationSubmission,
    WorkbookIngestionJob,
)
from resource_registry.registration_submissions.services import RegistrationSubmissionService
from resource_registry.services.csv_upload_materialization_service import (
    CsvUploadMaterializationService,
)
from resource_registry.services.dataset_registration_job_execution_service import (
    DatasetRegistrationJobExecutionService,
)
from resource_registry.services.dataset_registration_job_service import (
    DatasetRegistrationJobService,
)
from resource_registry.services.workbook_ingestion_job_worker_runner import (
    WorkbookIngestionJobWorkerRunner,
)
from resource_registry.tests.test_csv_registration_worker_execution import _environment, _runner
from resource_registry.uploads.exceptions import UploadApplicationError

pytestmark = pytest.mark.django_db(transaction=True)


def _create_upload(
    *,
    environment: object,
    storage_root: Path,
    content: bytes,
    filename: str = "replacement.csv",
    content_type: str = "text/csv",
) -> LargeFileUploadSession:
    """Create a completed, owned Registry upload with a real local object."""
    upload_id = uuid4()
    object_key = f"uploads/{upload_id}/{filename}"
    source_path = storage_root / object_key
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(content)
    now = timezone.now()
    return LargeFileUploadSession.objects.create(
        organization=environment.organization,
        workspace=environment.workspace,
        registry_namespace=environment.namespace,
        submitted_by=environment.user,
        original_filename=filename,
        normalized_filename=filename,
        resolved_file_format=Path(filename).suffix.removeprefix(".").upper(),
        declared_content_type=content_type,
        detected_content_type=content_type,
        declared_size_bytes=len(content),
        actual_size_bytes=len(content),
        storage_provider="local",
        storage_object_key=object_key,
        status=LargeFileUploadStatus.COMPLETED,
        idempotency_key=f"replacement-{upload_id}",
        request_fingerprint="a" * 64,
        upload_authorization_expires_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(hours=1),
        uploaded_at=now,
        completed_at=now,
    )


def _grant_replacement_permissions(environment: object) -> None:
    """Grant the permissions required to submit an owned replacement."""
    environment.user.user_permissions.add(
        Permission.objects.get(codename="create_large_file_upload"),
        Permission.objects.get(codename="change_dataset"),
        Permission.objects.get(codename="add_dataset"),
    )


def _workbook_bytes(
    *,
    path: Path,
    sheet_names: tuple[str, ...],
    empty_sheet: str | None = None,
    values: Mapping[str, int] | None = None,
) -> bytes:
    """Write a small Workbook fixture and return its uploaded bytes."""
    workbook = Workbook()
    workbook.remove(workbook.active)
    for sheet_name in sheet_names:
        sheet = workbook.create_sheet(sheet_name)
        sheet.append(["name", "value"])
        if sheet_name != empty_sheet:
            sheet.append([sheet_name, (values or {}).get(sheet_name, 1)])
    workbook.save(path)
    return path.read_bytes()


def test_replacement_versions_exact_dataset_and_reports_no_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserve old versions and the exact Dataset ID across changing uploads."""
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(settings, "RESOURCE_REGISTRY_LOCAL_STORAGE_DIR", str(storage_root))
    environment = _environment()
    _grant_replacement_permissions(environment)
    dataset = Dataset.objects.create(
        tenant_id=environment.namespace.id,
        organization=environment.organization,
        workspace=environment.workspace,
        visibility_scope="WORKSPACE",
        slug="original-name",
        name="Original name",
        source_type="FILE_UPLOAD",
        status="ACTIVE",
    )
    submission_service = RegistrationSubmissionService()
    outcomes: list[dict[str, object]] = []
    for content in (
        b"name,value\nA,1\n",
        b"name,value\nA,2\n",
        b"name,value\nA,2\n",
        b"name,value\nA,1\n",
    ):
        upload = _create_upload(
            environment=environment,
            storage_root=storage_root,
            content=content,
        )
        submission = submission_service.submit_replacement(
            user=environment.user,
            organization=environment.organization,
            namespace_id=environment.namespace.id,
            upload_id=upload.id,
            dataset_id=dataset.id,
        )
        assert _runner(tmp_path / "temporary").run_once() == 1
        job = DatasetRegistrationJob.objects.get(
            registration_submission_id=submission.registration_id,
        )
        assert job.result_payload_json["dataset_id"] == str(dataset.id)
        outcomes.append(job.result_payload_json)
    assert list(
        DatasetVersion.objects.filter(dataset=dataset)
        .order_by("version_number")
        .values_list("version_number", flat=True)
    ) == [1, 2, 3]
    assert outcomes[2]["no_changes_detected"] is True
    assert outcomes[3]["no_changes_detected"] is False
    versions = list(DatasetVersion.objects.filter(dataset=dataset).order_by("version_number"))
    assert versions[0].content_hash == versions[2].content_hash
    assert versions[0].id != versions[2].id
    assert Dataset.objects.filter(pk=dataset.pk, slug="original-name").exists()


def test_replacement_rejects_missing_foreign_and_unowned_targets(tmp_path: Path) -> None:
    """Reject unknown targets and targets outside the current Workspace."""
    environment = _environment()
    _grant_replacement_permissions(environment)
    upload = _create_upload(
        environment=environment,
        storage_root=tmp_path,
        content=b"name\nA\n",
    )
    service = RegistrationSubmissionService()
    with pytest.raises(UploadApplicationError) as missing:
        service.submit_replacement(
            user=environment.user,
            organization=environment.organization,
            namespace_id=environment.namespace.id,
            upload_id=upload.id,
            dataset_id=uuid4(),
        )
    assert missing.value.code == "DATASET_NOT_FOUND"
    foreign = Dataset.objects.create(
        tenant_id=environment.namespace.id,
        organization=environment.organization,
        visibility_scope="ORGANIZATION",
        slug="foreign-dataset",
        name="Foreign dataset",
        source_type="FILE_UPLOAD",
    )
    with pytest.raises(UploadApplicationError) as denied:
        service.submit_replacement(
            user=environment.user,
            organization=environment.organization,
            namespace_id=environment.namespace.id,
            upload_id=upload.id,
            dataset_id=foreign.id,
        )
    assert denied.value.http_status == 403
    assert not DatasetRegistrationJob.objects.exists()


def test_replacement_rejects_target_without_write_permission(tmp_path: Path) -> None:
    """Workspace access alone does not confer dataset replacement permission."""
    environment = _environment()
    environment.user.user_permissions.add(
        Permission.objects.get(codename="create_large_file_upload"),
    )
    target = Dataset.objects.create(
        tenant_id=environment.namespace.id,
        organization=environment.organization,
        workspace=environment.workspace,
        visibility_scope="WORKSPACE",
        slug="protected",
        name="Protected",
        source_type="FILE_UPLOAD",
    )
    upload = _create_upload(
        environment=environment,
        storage_root=tmp_path,
        content=b"name\nA\n",
    )
    with pytest.raises(UploadApplicationError) as denied:
        RegistrationSubmissionService().submit_replacement(
            user=environment.user,
            organization=environment.organization,
            namespace_id=environment.namespace.id,
            upload_id=upload.id,
            dataset_id=target.id,
        )
    assert denied.value.http_status == 403


def test_concurrent_replacement_submission_creates_one_job(tmp_path: Path) -> None:
    """Serialize duplicate requests for one verified upload and exact target."""
    environment = _environment()
    _grant_replacement_permissions(environment)
    target = Dataset.objects.create(
        tenant_id=environment.namespace.id,
        organization=environment.organization,
        workspace=environment.workspace,
        visibility_scope="WORKSPACE",
        slug="concurrent-target",
        name="Concurrent target",
        source_type="FILE_UPLOAD",
    )
    upload = _create_upload(
        environment=environment,
        storage_root=tmp_path,
        content=b"name\nA\n",
    )
    barrier = Barrier(2)

    def submit_from_fresh_connection() -> UUID:
        close_old_connections()
        try:
            user = User.objects.get(pk=environment.user.pk)
            organization = Organization.objects.get(pk=environment.organization.pk)
            barrier.wait()
            return (
                RegistrationSubmissionService()
                .submit_replacement(
                    user=user,
                    organization=organization,
                    namespace_id=environment.namespace.id,
                    upload_id=upload.id,
                    dataset_id=target.id,
                )
                .registration_id
            )
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        registration_ids = tuple(executor.map(lambda _: submit_from_fresh_connection(), range(2)))

    assert len(set(registration_ids)) == 1
    assert RegistrationSubmission.objects.filter(upload_session=upload).count() == 1
    assert (
        DatasetRegistrationJob.objects.filter(
            registration_submission__upload_session=upload
        ).count()
        == 1
    )

    different_target = Dataset.objects.create(
        tenant_id=environment.namespace.id,
        organization=environment.organization,
        workspace=environment.workspace,
        visibility_scope="WORKSPACE",
        slug="other-target",
        name="Other target",
        source_type="FILE_UPLOAD",
    )
    with pytest.raises(UploadApplicationError) as conflict:
        RegistrationSubmissionService().submit_replacement(
            user=environment.user,
            organization=environment.organization,
            namespace_id=environment.namespace.id,
            upload_id=upload.id,
            dataset_id=different_target.id,
        )
    assert conflict.value.http_status == 409


def test_concurrent_replacement_jobs_serialize_versions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Publish both different uploads against one Dataset with distinct version numbers."""
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(settings, "RESOURCE_REGISTRY_LOCAL_STORAGE_DIR", str(storage_root))
    environment = _environment()
    _grant_replacement_permissions(environment)
    target = Dataset.objects.create(
        tenant_id=environment.namespace.id,
        organization=environment.organization,
        workspace=environment.workspace,
        visibility_scope="WORKSPACE",
        slug="parallel-replacement",
        name="Parallel replacement",
        source_type="FILE_UPLOAD",
    )
    submission_service = RegistrationSubmissionService()
    for value in (1, 2):
        upload = _create_upload(
            environment=environment,
            storage_root=storage_root,
            content=f"name,value\nA,{value}\n".encode(),
        )
        submission_service.submit_replacement(
            user=environment.user,
            organization=environment.organization,
            namespace_id=environment.namespace.id,
            upload_id=upload.id,
            dataset_id=target.id,
        )
    job_service = DatasetRegistrationJobService()
    assert job_service.activate_submitted_jobs(batch_size=2) == 2
    claimed = job_service.claim_queued_jobs(batch_size=2)
    assert len(claimed) == 2
    barrier = Barrier(2)

    def execute_from_fresh_connection(job_id: UUID) -> str:
        close_old_connections()
        try:
            barrier.wait()
            executed = DatasetRegistrationJobExecutionService(
                materialization_service=CsvUploadMaterializationService(
                    temp_directory=tmp_path / str(job_id)
                )
            ).execute_job(
                tenant_id=str(environment.namespace.id),
                registration_job_id=str(job_id),
            )
            return str(executed.status)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = tuple(executor.map(execute_from_fresh_connection, [job.id for job in claimed]))

    assert statuses == ("completed", "completed")
    assert list(
        DatasetVersion.objects.filter(dataset=target)
        .order_by("version_number")
        .values_list("version_number", flat=True)
    ) == [1, 2]


def test_replacement_job_rechecks_revoked_write_permission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refuse queued mutation when the actor loses write permission."""
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(settings, "RESOURCE_REGISTRY_LOCAL_STORAGE_DIR", str(storage_root))
    environment = _environment()
    _grant_replacement_permissions(environment)
    target = Dataset.objects.create(
        tenant_id=environment.namespace.id,
        organization=environment.organization,
        workspace=environment.workspace,
        visibility_scope="WORKSPACE",
        slug="revoked",
        name="Revoked",
        source_type="FILE_UPLOAD",
    )
    upload = _create_upload(
        environment=environment,
        storage_root=storage_root,
        content=b"name\nA\n",
    )
    result = RegistrationSubmissionService().submit_replacement(
        user=environment.user,
        organization=environment.organization,
        namespace_id=environment.namespace.id,
        upload_id=upload.id,
        dataset_id=target.id,
    )
    environment.user.user_permissions.remove(Permission.objects.get(codename="change_dataset"))
    assert _runner(tmp_path / "temporary").run_once() == 1
    job = DatasetRegistrationJob.objects.get(registration_submission_id=result.registration_id)
    assert job.status == "failed"
    assert DatasetVersion.objects.filter(dataset=target).count() == 0


def test_replacement_job_rechecks_disabled_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject a queued replacement after Registry access is disabled for the Workspace."""
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(settings, "RESOURCE_REGISTRY_LOCAL_STORAGE_DIR", str(storage_root))
    environment = _environment()
    _grant_replacement_permissions(environment)
    target = Dataset.objects.create(
        tenant_id=environment.namespace.id,
        organization=environment.organization,
        workspace=environment.workspace,
        visibility_scope="WORKSPACE",
        slug="workspace-revoked",
        name="Workspace revoked",
        source_type="FILE_UPLOAD",
    )
    upload = _create_upload(
        environment=environment,
        storage_root=storage_root,
        content=b"name\nA\n",
    )
    submission = RegistrationSubmissionService().submit_replacement(
        user=environment.user,
        organization=environment.organization,
        namespace_id=environment.namespace.id,
        upload_id=upload.id,
        dataset_id=target.id,
    )
    environment.workspace.resource_registry_enabled = False
    environment.workspace.save(update_fields=["resource_registry_enabled"])
    assert _runner(tmp_path / "temporary").run_once() == 1
    job = DatasetRegistrationJob.objects.get(registration_submission_id=submission.registration_id)
    assert job.status == "failed"
    assert DatasetVersion.objects.filter(dataset=target).count() == 0


def test_replacement_status_bif_returns_owned_job_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Expose a completed replacement result only to its submitting actor."""
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(settings, "RESOURCE_REGISTRY_LOCAL_STORAGE_DIR", str(storage_root))
    environment = _environment()
    _grant_replacement_permissions(environment)
    target = Dataset.objects.create(
        tenant_id=environment.namespace.id,
        organization=environment.organization,
        workspace=environment.workspace,
        visibility_scope="WORKSPACE",
        slug="status-target",
        name="Status target",
        source_type="FILE_UPLOAD",
    )
    upload = _create_upload(
        environment=environment,
        storage_root=storage_root,
        content=b"name\nA\n",
    )
    submission = RegistrationSubmissionService().submit_replacement(
        user=environment.user,
        organization=environment.organization,
        namespace_id=environment.namespace.id,
        upload_id=upload.id,
        dataset_id=target.id,
    )
    assert _runner(tmp_path / "temporary").run_once() == 1
    monkeypatch.setattr(
        resource_registry_functions,
        "_admit_current_workspace_for_replacement",
        lambda **kwargs: AdmittedBifNamespace(
            namespace_id=str(environment.namespace.id),
            organization_id=environment.organization.id,
            workspace_id=environment.workspace.id,
            actor_id=environment.user.id,
        ),
    )
    monkeypatch.setattr(
        resource_registry_functions, "_assert_capability", lambda *args, **kwargs: None
    )
    request = SimpleNamespace(user=environment.user, organization=environment.organization)
    response = json.loads(
        resource_registry_functions.resource_registry_get_replacement_status(
            request, None, str(environment.namespace.id), str(submission.registration_id)
        )
    )
    assert response["success"] is True
    assert response["data"]["result"]["dataset_id"] == str(target.id)

    other_user = User.objects.create_user(username=f"other-{uuid4().hex}")
    monkeypatch.setattr(
        resource_registry_functions,
        "_admit_current_workspace_for_replacement",
        lambda **kwargs: AdmittedBifNamespace(
            namespace_id=str(environment.namespace.id),
            organization_id=environment.organization.id,
            workspace_id=environment.workspace.id,
            actor_id=other_user.id,
        ),
    )
    foreign_response = json.loads(
        resource_registry_functions.resource_registry_get_replacement_status(
            SimpleNamespace(user=other_user, organization=environment.organization),
            None,
            str(environment.namespace.id),
            str(submission.registration_id),
        )
    )
    assert foreign_response["code"] == "registration_not_found"


@pytest.mark.parametrize("scope_policy", ("observe", "strict"))
def test_replacement_bif_uses_current_session_workspace_without_namespace_argument(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scope_policy: str,
) -> None:
    """Derive the replacement namespace from the authenticated current Workspace."""
    monkeypatch.setenv("RESOURCE_REGISTRY_SCOPE_POLICY", scope_policy)
    environment = _environment()
    _grant_replacement_permissions(environment)
    target = Dataset.objects.create(
        tenant_id=environment.namespace.id,
        organization=environment.organization,
        workspace=environment.workspace,
        visibility_scope="WORKSPACE",
        slug="session-target",
        name="Session target",
        source_type="FILE_UPLOAD",
    )
    upload = _create_upload(
        environment=environment,
        storage_root=tmp_path,
        content=b"name\nA\n",
    )
    monkeypatch.setattr(
        resource_registry_functions, "_assert_capability", lambda *args, **kwargs: None
    )
    request = SimpleNamespace(
        user=SimpleLazyObject(lambda: environment.user),
        organization=environment.organization,
        workspace=environment.workspace,
        session={"resource_registry_workspace_id": environment.workspace.id},
    )
    accepted = json.loads(
        resource_registry_functions.resource_registry_replace_dataset(
            request,
            None,
            upload_id=str(upload.id),
            dataset_id=str(target.id),
        )
    )
    assert accepted["success"] is True
    assert (
        RegistrationSubmission.objects.get(pk=accepted["data"]["registration_id"]).workspace_id
        == environment.workspace.id
    )
    status_response = json.loads(
        resource_registry_functions.resource_registry_get_replacement_status(
            request,
            None,
            registration_id=accepted["data"]["registration_id"],
        )
    )
    assert status_response["success"] is True
    assert status_response["data"]["registration_id"] == accepted["data"]["registration_id"]

    alternate_workspace = Workspace.objects.create(
        organization=environment.organization,
        name="Alternate Workspace",
        slug=f"alternate-{uuid4().hex}",
        last_modified_by=environment.user,
        resource_registry_enabled=True,
    )
    denied = json.loads(
        resource_registry_functions.resource_registry_replace_dataset(
            request,
            None,
            tenant_id=str(alternate_workspace.resource_registry_namespace.id),
            upload_id=str(upload.id),
            dataset_id=str(target.id),
        )
    )
    assert denied["code"] == "tenant_selection_conflict"


def test_workbook_replacement_updates_adds_and_archives_sheets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replace an entire Workbook group without dropping historical versions."""
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(settings, "RESOURCE_REGISTRY_LOCAL_STORAGE_DIR", str(storage_root))
    environment = _environment()
    _grant_replacement_permissions(environment)

    first_upload = _create_upload(
        environment=environment,
        storage_root=storage_root,
        content=_workbook_bytes(path=tmp_path / "first.xlsx", sheet_names=("Sales", "Costs")),
        filename="source.xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    RegistrationSubmissionService().submit(
        user=environment.user,
        organization=environment.organization,
        upload_id=first_upload.id,
    )
    assert WorkbookIngestionJobWorkerRunner(batch_size=1).run_once() == 1
    group_id = Dataset.objects.get(slug__endswith="sales").workbook_group_id
    old_versions = set(DatasetVersion.objects.values_list("id", flat=True))
    assert group_id is not None
    updated_bytes = _workbook_bytes(
        path=tmp_path / "updated.xlsx",
        sheet_names=("Sales", "Forecast"),
    )
    replacement_upload = _create_upload(
        environment=environment,
        storage_root=storage_root,
        content=updated_bytes,
        filename="updated.xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    result = RegistrationSubmissionService().submit_replacement(
        user=environment.user,
        organization=environment.organization,
        namespace_id=environment.namespace.id,
        upload_id=replacement_upload.id,
        workbook_group_id=group_id,
    )
    assert WorkbookIngestionJobWorkerRunner(batch_size=1).run_once() == 1
    job = WorkbookIngestionJob.objects.get(registration_submission_id=result.registration_id)
    assert job.result_payload_json["no_changes_detected"] is False
    assert Dataset.objects.get(slug__endswith="costs").status == "ARCHIVED"
    assert Dataset.objects.get(slug__endswith="sales").versions.count() == 2
    assert Dataset.objects.get(slug__endswith="forecast").status == "ACTIVE"
    assert set(DatasetVersion.objects.values_list("id", flat=True)) >= old_versions

    unchanged_upload = _create_upload(
        environment=environment,
        storage_root=storage_root,
        content=updated_bytes,
        filename="same.xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    unchanged = RegistrationSubmissionService().submit_replacement(
        user=environment.user,
        organization=environment.organization,
        namespace_id=environment.namespace.id,
        upload_id=unchanged_upload.id,
        workbook_group_id=group_id,
    )
    version_count = DatasetVersion.objects.count()
    assert WorkbookIngestionJobWorkerRunner(batch_size=1).run_once() == 1
    unchanged_job = WorkbookIngestionJob.objects.get(
        registration_submission_id=unchanged.registration_id,
    )
    assert unchanged_job.result_payload_json["no_changes_detected"] is True
    assert DatasetVersion.objects.count() == version_count

    one_sheet_changed_upload = _create_upload(
        environment=environment,
        storage_root=storage_root,
        content=_workbook_bytes(
            path=tmp_path / "one-sheet-changed.xlsx",
            sheet_names=("Sales", "Forecast"),
            values={"Forecast": 2},
        ),
        filename="one-sheet-changed.xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    RegistrationSubmissionService().submit_replacement(
        user=environment.user,
        organization=environment.organization,
        namespace_id=environment.namespace.id,
        upload_id=one_sheet_changed_upload.id,
        workbook_group_id=group_id,
    )
    assert WorkbookIngestionJobWorkerRunner(batch_size=1).run_once() == 1
    assert Dataset.objects.get(slug__endswith="sales").versions.count() == 2
    assert Dataset.objects.get(slug__endswith="forecast").versions.count() == 2


def test_workbook_replacement_rolls_back_every_sheet_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A later sheet failure cannot publish an earlier sheet's new version."""
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(settings, "RESOURCE_REGISTRY_LOCAL_STORAGE_DIR", str(storage_root))
    environment = _environment()
    _grant_replacement_permissions(environment)
    initial_upload = _create_upload(
        environment=environment,
        storage_root=storage_root,
        content=_workbook_bytes(
            path=tmp_path / "initial.xlsx",
            sheet_names=("Sales", "Costs"),
        ),
        filename="source.xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    RegistrationSubmissionService().submit(
        user=environment.user,
        organization=environment.organization,
        upload_id=initial_upload.id,
    )
    assert WorkbookIngestionJobWorkerRunner(batch_size=1).run_once() == 1
    group_id = Dataset.objects.get(slug__endswith="sales").workbook_group_id
    original_snapshots = set(
        (storage_root / "resource_registry" / "canonical").rglob("dataset.parquet")
    )
    replacement_upload = _create_upload(
        environment=environment,
        storage_root=storage_root,
        content=_workbook_bytes(
            path=tmp_path / "changed.xlsx",
            sheet_names=("Sales", "Forecast"),
        ),
        filename="changed.xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    submission = RegistrationSubmissionService().submit_replacement(
        user=environment.user,
        organization=environment.organization,
        namespace_id=environment.namespace.id,
        upload_id=replacement_upload.id,
        workbook_group_id=group_id,
    )
    original_register = DatasetRegistrationFacade.register

    def fail_forecast_registration(
        self: DatasetRegistrationFacade, command: object, **kwargs: object
    ):
        if command.sheet_name == "Forecast":
            raise ValidationError("Invalid sheet.", code="invalid_sheet")
        return original_register(self, command, **kwargs)

    monkeypatch.setattr(DatasetRegistrationFacade, "register", fail_forecast_registration)
    assert WorkbookIngestionJobWorkerRunner(batch_size=1).run_once() == 1
    job = WorkbookIngestionJob.objects.get(registration_submission_id=submission.registration_id)
    assert job.status == "failed"
    assert DatasetVersion.objects.count() == 2
    assert not Dataset.objects.filter(slug__endswith="forecast").exists()
    assert Dataset.objects.get(slug__endswith="costs").status == "ACTIVE"
    assert (
        set((storage_root / "resource_registry" / "canonical").rglob("dataset.parquet"))
        == original_snapshots
    )
