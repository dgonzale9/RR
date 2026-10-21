"""End-to-end contracts for importing a chat attachment into the Registry."""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.conf import settings
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from openpyxl import Workbook  # type: ignore[import-untyped]  # Third-party stubs unavailable.

from BotServer.builtin_functions import resource_registry_functions
from resource_registry.bifs.read_only_namespace_admission import AdmittedBifNamespace
from resource_registry.models import (
    Dataset,
    DatasetRegistrationJob,
    DatasetVersion,
    LargeFileUploadSession,
    WorkbookIngestionGroup,
    WorkbookIngestionJob,
)
from resource_registry.services.chat_dataset_import_service import ChatDatasetImportService
from resource_registry.tests.test_csv_registration_worker_execution import _environment, _runner
from resource_registry.uploads.exceptions import UploadApplicationError

pytestmark = pytest.mark.django_db(transaction=True)


def _grant_import_permissions(user: object) -> None:
    """Grant the upload, registration, and replacement capabilities."""
    user.user_permissions.add(
        Permission.objects.get(codename="create_large_file_upload"),
        Permission.objects.get(codename="add_dataset"),
        Permission.objects.get(codename="change_dataset"),
    )


def _submit_chat_json(environment: object, content: bytes) -> object:
    """Submit one real JSON attachment through the public import service."""
    return ChatDatasetImportService().submit(
        user=environment.user,
        organization=environment.organization,
        workspace_id=environment.workspace.id,
        namespace_id=environment.namespace.id,
        uploaded_file=SimpleUploadedFile(
            "testing_sales_10000.json", content, content_type="application/json"
        ),
        idempotency_key=f"chat:{uuid4()}",
    )


def test_chat_json_registers_first_file_then_replaces_and_detects_no_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep one Dataset identity and old versions across three chat uploads."""
    monkeypatch.setattr(settings, "RESOURCE_REGISTRY_STORAGE_PROVIDER", "local")
    monkeypatch.setattr(settings, "RESOURCE_REGISTRY_LOCAL_STORAGE_DIR", str(tmp_path / "storage"))
    environment = _environment()
    _grant_import_permissions(environment.user)
    contents = (
        b'[{"sale_id":"TEST-010000","amount":1}]',
        b'[{"sale_id":"TEST-010000","amount":1},{"sale_id":"TEST-010001","amount":2}]',
        b'[{"sale_id":"TEST-010000","amount":1},{"sale_id":"TEST-010001","amount":2}]',
    )
    outcomes = []
    for content in contents:
        submission = _submit_chat_json(environment, content)
        outcomes.append(submission)
        assert _runner(tmp_path / "temporary").run_once() == 1
    assert [outcome.action for outcome in outcomes] == ["register", "replace", "replace"]
    assert Dataset.objects.filter(workspace=environment.workspace).count() == 1
    dataset = Dataset.objects.get(workspace=environment.workspace)
    assert list(
        dataset.versions.order_by("version_number").values_list("version_number", flat=True)
    ) == [1, 2]
    jobs = list(
        DatasetRegistrationJob.objects.filter(
            registration_submission_id__in=[outcome.registration_id for outcome in outcomes]
        ).order_by("created_at")
    )
    assert jobs[-1].result_payload_json["no_changes_detected"] is True
    assert DatasetVersion.objects.filter(dataset=dataset).count() == 2
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
    status = resource_registry_functions.resource_registry_get_replacement_status(
        SimpleNamespace(user=environment.user, organization=environment.organization),
        None,
        registration_id=str(outcomes[0].registration_id),
    )
    assert json.loads(status)["data"]["result"]["dataset_id"] == str(dataset.id)


def test_chat_import_rejects_ambiguous_filename_before_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Do not select one of two same-name targets in the active Workspace."""
    monkeypatch.setattr(settings, "RESOURCE_REGISTRY_STORAGE_PROVIDER", "local")
    monkeypatch.setattr(settings, "RESOURCE_REGISTRY_LOCAL_STORAGE_DIR", str(tmp_path))
    environment = _environment()
    _grant_import_permissions(environment.user)
    for slug in ("sales-original", "sales-copy"):
        Dataset.objects.create(
            tenant_id=environment.namespace.id,
            organization=environment.organization,
            workspace=environment.workspace,
            visibility_scope="WORKSPACE",
            slug=slug,
            name="testing_sales_10000.json",
            source_type="FILE_UPLOAD",
            status="ACTIVE",
        )
    with pytest.raises(UploadApplicationError) as error:
        _submit_chat_json(environment, b'[{"sale_id":"TEST-010001"}]')
    assert error.value.code == "DATASET_MATCH_AMBIGUOUS"
    assert not DatasetRegistrationJob.objects.exists()


def test_chat_import_rejects_discovered_target_that_does_not_match_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reject a stale model-provided Dataset ID before creating a Registry upload."""
    monkeypatch.setattr(settings, "RESOURCE_REGISTRY_STORAGE_PROVIDER", "local")
    monkeypatch.setattr(settings, "RESOURCE_REGISTRY_LOCAL_STORAGE_DIR", str(tmp_path))
    environment = _environment()
    _grant_import_permissions(environment.user)
    matching_dataset = Dataset.objects.create(
        tenant_id=environment.namespace.id,
        organization=environment.organization,
        workspace=environment.workspace,
        visibility_scope="WORKSPACE",
        slug="testing-sales-10000-json",
        name="testing_sales_10000.json",
        source_type="FILE_UPLOAD",
        status="ACTIVE",
    )

    with pytest.raises(UploadApplicationError) as error:
        ChatDatasetImportService().submit(
            user=environment.user,
            organization=environment.organization,
            workspace_id=environment.workspace.id,
            namespace_id=environment.namespace.id,
            uploaded_file=SimpleUploadedFile(
                "testing_sales_10000.json",
                b'[{"sale_id":"TEST-010001"}]',
                content_type="application/json",
            ),
            idempotency_key="chat:wrong-target",
            expected_dataset_id=uuid4(),
        )

    assert matching_dataset.id is not None
    assert error.value.code == "REPLACEMENT_TARGET_MISMATCH"
    assert not LargeFileUploadSession.objects.exists()

    matching_submission = ChatDatasetImportService().submit(
        user=environment.user,
        organization=environment.organization,
        workspace_id=environment.workspace.id,
        namespace_id=environment.namespace.id,
        uploaded_file=SimpleUploadedFile(
            "testing_sales_10000.json",
            b'[{"sale_id":"TEST-010001"}]',
            content_type="application/json",
        ),
        idempotency_key="chat:matching-target",
        expected_dataset_id=matching_dataset.id,
    )
    assert matching_submission.action == "replace"
    assert matching_submission.dataset_id == matching_dataset.id


def test_chat_import_rejects_parallel_first_registration_and_replays_same_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Queue one first registration per filename and replay a repeated tool call."""
    monkeypatch.setattr(settings, "RESOURCE_REGISTRY_STORAGE_PROVIDER", "local")
    monkeypatch.setattr(settings, "RESOURCE_REGISTRY_LOCAL_STORAGE_DIR", str(tmp_path))
    environment = _environment()
    _grant_import_permissions(environment.user)
    service = ChatDatasetImportService()
    arguments = {
        "user": environment.user,
        "organization": environment.organization,
        "workspace_id": environment.workspace.id,
        "namespace_id": environment.namespace.id,
    }
    content = b'[{"sale_id":"TEST-010001"}]'
    first = service.submit(
        **arguments,
        uploaded_file=SimpleUploadedFile("sales.json", content, content_type="application/json"),
        idempotency_key="chat:one-request",
    )
    replay = service.submit(
        **arguments,
        uploaded_file=SimpleUploadedFile("sales.json", content, content_type="application/json"),
        idempotency_key="chat:one-request",
    )
    assert replay.registration_id == first.registration_id
    assert replay.idempotent_replay is True
    with pytest.raises(UploadApplicationError) as error:
        service.submit(
            **arguments,
            uploaded_file=SimpleUploadedFile(
                "sales.json", content, content_type="application/json"
            ),
            idempotency_key="chat:another-request",
        )
    assert error.value.code == "DATASET_IMPORT_IN_PROGRESS"
    assert DatasetRegistrationJob.objects.count() == 1


def test_chat_import_denies_unwritable_match_before_storing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Do not create a second Dataset when an exact existing match is protected."""
    monkeypatch.setattr(settings, "RESOURCE_REGISTRY_STORAGE_PROVIDER", "local")
    monkeypatch.setattr(settings, "RESOURCE_REGISTRY_LOCAL_STORAGE_DIR", str(tmp_path))
    environment = _environment()
    environment.user.user_permissions.add(
        Permission.objects.get(codename="create_large_file_upload"),
        Permission.objects.get(codename="add_dataset"),
    )
    Dataset.objects.create(
        tenant_id=environment.namespace.id,
        organization=environment.organization,
        workspace=environment.workspace,
        visibility_scope="WORKSPACE",
        slug="sales-json",
        name="sales.json",
        source_type="FILE_UPLOAD",
        status="ACTIVE",
    )
    with pytest.raises(UploadApplicationError) as error:
        ChatDatasetImportService().submit(
            user=environment.user,
            organization=environment.organization,
            workspace_id=environment.workspace.id,
            namespace_id=environment.namespace.id,
            uploaded_file=SimpleUploadedFile("sales.json", b"[]", content_type="application/json"),
            idempotency_key="chat:unwritable",
        )
    assert error.value.http_status == 403
    assert not LargeFileUploadSession.objects.exists()


def test_chat_workbook_matches_existing_group_without_identifiers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Choose the existing Workbook group from its source filename."""
    monkeypatch.setattr(settings, "RESOURCE_REGISTRY_STORAGE_PROVIDER", "local")
    monkeypatch.setattr(settings, "RESOURCE_REGISTRY_LOCAL_STORAGE_DIR", str(tmp_path))
    environment = _environment()
    _grant_import_permissions(environment.user)
    group = WorkbookIngestionGroup.objects.create(
        tenant_id=environment.namespace.id,
        source_uri="local://existing-sales.xlsx",
        source_hash=uuid4().hex,
        source_file_name="sales.xlsx",
        status="COMPLETED",
    )
    Dataset.objects.create(
        tenant_id=environment.namespace.id,
        organization=environment.organization,
        workspace=environment.workspace,
        visibility_scope="WORKSPACE",
        slug="sales-sheet",
        name="Sales",
        source_type="FILE_UPLOAD",
        status="ACTIVE",
        workbook_group=group,
    )
    workbook = Workbook()
    workbook.active.title = "Sales"
    workbook.active.append(["sale_id", "amount"])
    workbook.active.append(["TEST-010001", 149.5])
    buffer = BytesIO()
    workbook.save(buffer)
    result = ChatDatasetImportService().submit(
        user=environment.user,
        organization=environment.organization,
        workspace_id=environment.workspace.id,
        namespace_id=environment.namespace.id,
        uploaded_file=SimpleUploadedFile(
            "sales.xlsx",
            buffer.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
        idempotency_key="chat:workbook",
    )
    assert result.action == "replace"
    assert result.workbook_group_id == group.id
    assert (
        WorkbookIngestionJob.objects.get(registration_submission_id=result.registration_id).status
        == "submitted"
    )


def test_chat_import_rejects_conflicting_workspace_namespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never write a file when the BIF's Workspace Namespace assertion differs."""
    monkeypatch.setattr(settings, "RESOURCE_REGISTRY_STORAGE_PROVIDER", "local")
    monkeypatch.setattr(settings, "RESOURCE_REGISTRY_LOCAL_STORAGE_DIR", str(tmp_path))
    environment = _environment()
    _grant_import_permissions(environment.user)
    with pytest.raises(UploadApplicationError) as error:
        ChatDatasetImportService().submit(
            user=environment.user,
            organization=environment.organization,
            workspace_id=environment.workspace.id,
            namespace_id=uuid4(),
            uploaded_file=SimpleUploadedFile("sales.json", b"[]", content_type="application/json"),
            idempotency_key="chat:wrong-workspace",
        )
    assert error.value.http_status == 403
    assert not LargeFileUploadSession.objects.exists()
