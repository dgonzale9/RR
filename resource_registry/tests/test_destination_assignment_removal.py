"""Regression coverage for logical staged-resource destination removal."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.contrib.auth.models import Group, Permission, User
from django.utils import timezone

from BotServer.util.user_group_management import UserGroupManagement
from Organization.models import Organization, Workspace
from resource_registry.admin import RemoveDestinationsForm, StagedResourceIntakeAdmin
from resource_registry.models import (
    Dataset,
    DatasetRegistrationJob,
    DatasetRegistrationJobStatus,
    DatasetStatus,
    DatasetVersion,
    DatasetVersionStatus,
    DatasetVisibilityScope,
    RegistryNamespace,
    StagedResourceIntake,
    StagedResourceIntakeStatus,
    StagedResourcePublication,
    StagedResourcePublicationDataset,
    StagedResourcePublicationStatus,
    WorkbookIngestionJobStatus,
)
from resource_registry.read.dataset_read_service import DatasetReadService
from resource_registry.services.destination_assignment_removal_service import (
    DestinationAssignmentRemovalService,
)
from resource_registry.uploads.exceptions import UploadApplicationError

pytestmark = pytest.mark.django_db


def test_detaching_one_shared_assignment_preserves_dataset_until_last_assignment() -> None:
    """Archive a Dataset only after its final active publication assignment is detached."""
    owner = User.objects.create_superuser(username="detach-owner", password="password")
    organization = Organization.objects.create(
        name="Detach Organization",
        slug="detach-organization",
        last_modified_by=owner,
    )
    workspace = Workspace.objects.create(
        organization=organization,
        name="Detach Workspace",
        slug="detach-workspace",
        last_modified_by=owner,
    )
    namespace = RegistryNamespace.objects.create(id=uuid4(), workspace=workspace)
    dataset = Dataset.objects.create(
        tenant_id=namespace.id,
        organization=organization,
        workspace=workspace,
        visibility_scope=DatasetVisibilityScope.WORKSPACE,
        slug="detach-dataset",
        name="Detach Dataset",
        source_type="FILE_UPLOAD",
        status=DatasetStatus.ACTIVE,
    )
    version = DatasetVersion.objects.create(
        dataset=dataset,
        version_number=1,
        source_uri="azure://example/detach.xlsx",
        content_hash="detach-hash",
        status=DatasetVersionStatus.READY,
    )
    first_publication = _publication(
        owner=owner,
        organization=organization,
        workspace=workspace,
        namespace=namespace,
        suffix="first",
    )
    second_publication = _publication(
        owner=owner,
        organization=organization,
        workspace=workspace,
        namespace=namespace,
        suffix="second",
    )
    for publication in (first_publication, second_publication):
        StagedResourcePublicationDataset.objects.create(
            publication=publication,
            dataset=dataset,
            dataset_version=version,
        )

    service = DestinationAssignmentRemovalService()
    first_result = service.request_detachment(
        user=owner,
        publication_id=first_publication.id,
        reason="Remove first destination assignment.",
    )
    dataset.refresh_from_db()
    first_publication.refresh_from_db()

    assert first_result.is_pending is False
    assert first_publication.assignment_status == StagedResourcePublicationStatus.DETACHED
    assert dataset.status == DatasetStatus.ACTIVE

    service.request_detachment(
        user=owner,
        publication_id=second_publication.id,
        reason="Remove final destination assignment.",
    )
    dataset.refresh_from_db()
    assert dataset.status == DatasetStatus.ARCHIVED
    assert dataset.archived_at is not None
    assert DatasetReadService().list_datasets(tenant_id=str(namespace.id)) == []


def test_recording_detached_publication_outputs_marks_restored_assignment_detached() -> None:
    """Mark a restored output assignment detached when its publication was already detached."""
    owner = User.objects.create_superuser(username="restored-detach-owner", password="password")
    organization = Organization.objects.create(
        name="Restored Detach Organization",
        slug="restored-detach-organization",
        last_modified_by=owner,
    )
    workspace = Workspace.objects.create(
        organization=organization,
        name="Restored Detach Workspace",
        slug="restored-detach-workspace",
        last_modified_by=owner,
    )
    namespace = RegistryNamespace.objects.create(id=uuid4(), workspace=workspace)
    dataset = Dataset.objects.create(
        tenant_id=namespace.id,
        organization=organization,
        workspace=workspace,
        visibility_scope=DatasetVisibilityScope.WORKSPACE,
        slug="restored-detach-dataset",
        name="Restored Detach Dataset",
        source_type="FILE_UPLOAD",
        status=DatasetStatus.ACTIVE,
    )
    version = DatasetVersion.objects.create(
        dataset=dataset,
        version_number=1,
        source_uri="azure://example/restored-detach.csv",
        content_hash="restored-detach-hash",
        status=DatasetVersionStatus.READY,
    )
    publication = _publication(
        owner=owner,
        organization=organization,
        workspace=workspace,
        namespace=namespace,
        suffix="restored-detach",
    )
    publication.assignment_status = StagedResourcePublicationStatus.DETACHED
    publication.detached_at = timezone.now()
    publication.save(update_fields=["assignment_status", "detached_at"])

    DestinationAssignmentRemovalService().record_job_outputs(
        publication_id=publication.id,
        result_payload={
            "dataset_id": str(dataset.id),
            "dataset_version_id": str(version.id),
        },
    )

    assignment = StagedResourcePublicationDataset.objects.get(publication=publication)
    assert assignment.dataset_id == dataset.id
    assert assignment.dataset_version_id == version.id
    assert assignment.detached_at == publication.detached_at


def test_detachment_requires_superuser_and_reason() -> None:
    """Fail closed when removal authority or audit evidence is missing."""
    owner = User.objects.create_superuser(username="detach-superuser", password="password")
    ordinary_user = User.objects.create_user(username="detach-user", password="password")
    organization = Organization.objects.create(
        name="Authority Organization",
        slug="authority-organization",
        last_modified_by=owner,
    )
    namespace = RegistryNamespace.objects.create(id=uuid4(), organization=organization)
    publication = _publication(
        owner=owner,
        organization=organization,
        workspace=None,
        namespace=namespace,
        suffix="authority",
    )
    service = DestinationAssignmentRemovalService()

    with pytest.raises(UploadApplicationError):
        service.request_detachment(
            user=ordinary_user,
            publication_id=publication.id,
            reason="Unauthorized removal.",
        )
    with pytest.raises(UploadApplicationError):
        service.request_detachment(user=owner, publication_id=publication.id, reason="")


def test_detachment_allows_staff_user_with_intake_management_permission() -> None:
    """Allow the global intake operator to remove an active destination assignment."""
    owner = User.objects.create_superuser(username="detach-owner", password="password")
    operator = User.objects.create_user(
        username="detach-operator",
        password="password",
        is_staff=True,
    )
    operator.user_permissions.add(Permission.objects.get(codename="manage_stagedresourceintake"))
    organization = Organization.objects.create(
        name="Operator Authority Organization",
        slug="operator-authority-organization",
        last_modified_by=owner,
    )
    namespace = RegistryNamespace.objects.create(id=uuid4(), organization=organization)
    publication = _publication(
        owner=owner,
        organization=organization,
        workspace=None,
        namespace=namespace,
        suffix="operator-authority",
    )

    result = DestinationAssignmentRemovalService().request_detachment(
        user=operator,
        publication_id=publication.id,
        reason="Operator removal.",
    )
    publication.refresh_from_db()

    assert result.is_pending is False
    assert publication.assignment_status == StagedResourcePublicationStatus.DETACHED
    assert publication.detached_by == operator


def test_admin_group_can_detach_without_global_intake_permission() -> None:
    """Honor platform admin membership for destination removal without broad model grants."""
    owner = User.objects.create_superuser(username="admin-group-detach-owner", password="password")
    administrator = User.objects.create_user(
        username="admin-group-detach-operator", password="password", is_staff=True
    )
    admin_group, _ = Group.objects.get_or_create(name=UserGroupManagement.GROUP_ADMIN)
    administrator.groups.add(admin_group)
    organization = Organization.objects.create(
        name="Admin Group Detach Organization",
        slug="admin-group-detach-organization",
        last_modified_by=owner,
    )
    namespace = RegistryNamespace.objects.create(id=uuid4(), organization=organization)
    publication = _publication(
        owner=owner,
        organization=organization,
        workspace=None,
        namespace=namespace,
        suffix="admin-group-detach",
    )

    result = DestinationAssignmentRemovalService().request_detachment(
        user=administrator,
        publication_id=publication.id,
        reason="Platform administrator removal.",
    )

    publication.refresh_from_db()
    assert result.is_pending is False
    assert publication.assignment_status == StagedResourcePublicationStatus.DETACHED
    assert publication.detached_by == administrator


def test_scoped_admin_can_detach_only_destination_in_host_organization() -> None:
    """Limit scoped-admin removal to publications in the current host Organization."""
    owner = User.objects.create_superuser(username="scoped-detach-owner", password="password")
    scoped_admin = User.objects.create_user(
        username="scoped-detach-operator", password="password", is_staff=True
    )
    scoped_admin_group, _ = Group.objects.get_or_create(name=UserGroupManagement.GROUP_SCOPED_ADMIN)
    scoped_admin.groups.add(scoped_admin_group)
    host_organization = Organization.objects.create(
        name="Scoped Detach Host",
        slug="scoped-detach-host",
        last_modified_by=owner,
    )
    other_organization = Organization.objects.create(
        name="Scoped Detach Other",
        slug="scoped-detach-other",
        last_modified_by=owner,
    )
    host_workspace = Workspace.objects.create(
        organization=host_organization,
        name="Scoped Detach Workspace",
        slug="scoped-detach-workspace",
        last_modified_by=owner,
    )
    host_namespace = RegistryNamespace.objects.create(id=uuid4(), workspace=host_workspace)
    other_namespace = RegistryNamespace.objects.create(id=uuid4(), organization=other_organization)
    host_publication = _publication(
        owner=owner,
        organization=host_organization,
        workspace=host_workspace,
        namespace=host_namespace,
        suffix="scoped-host",
    )
    other_publication = _publication(
        owner=owner,
        organization=other_organization,
        workspace=None,
        namespace=other_namespace,
        suffix="scoped-other",
    )
    service = DestinationAssignmentRemovalService()

    with pytest.raises(UploadApplicationError) as error:
        service.request_detachment(
            user=scoped_admin,
            organization=host_organization,
            publication_id=other_publication.id,
            reason="Cross-organization removal must fail.",
        )
    assert error.value.code == "AUTHORIZATION_DENIED"

    result = service.request_detachment(
        user=scoped_admin,
        organization=host_organization,
        publication_id=host_publication.id,
        reason="Remove the authorized destination.",
    )
    host_publication.refresh_from_db()
    other_publication.refresh_from_db()
    assert result.is_pending is False
    assert host_publication.assignment_status == StagedResourcePublicationStatus.DETACHED
    assert other_publication.assignment_status == StagedResourcePublicationStatus.ACTIVE


def test_removal_form_requires_explicit_confirmation() -> None:
    """Reject a destination-removal submission without an explicit confirmation."""
    publication_id = str(uuid4())
    form = RemoveDestinationsForm(
        data={
            "publication_ids": [publication_id],
            "reason": "Confirm the removal decision.",
        },
        choices=[(publication_id, "Example Organization — Organization")],
    )

    assert form.is_valid() is False
    assert "confirmation" in form.errors


def test_removal_choices_show_job_state_and_affected_dataset_count() -> None:
    """Describe operational impact before an administrator confirms removal."""
    owner = User.objects.create_superuser(
        username=f"choice-detach-{uuid4().hex}",
        password="password",
    )
    organization = Organization.objects.create(
        name=f"Choice Detach Organization {uuid4().hex}",
        slug=f"choice-detach-{uuid4().hex}",
        last_modified_by=owner,
    )
    namespace = RegistryNamespace.objects.create(id=uuid4(), organization=organization)
    publication = _publication(
        owner=owner,
        organization=organization,
        workspace=None,
        namespace=namespace,
        suffix=f"choice-{uuid4().hex}",
    )
    dataset = Dataset.objects.create(
        tenant_id=namespace.id,
        organization=organization,
        visibility_scope=DatasetVisibilityScope.ORGANIZATION,
        slug=f"choice-dataset-{uuid4().hex}",
        name="Choice Dataset",
        source_type="FILE_UPLOAD",
        status=DatasetStatus.ACTIVE,
    )
    version = DatasetVersion.objects.create(
        dataset=dataset,
        version_number=1,
        source_uri="azure://example/choice.xlsx",
        content_hash=f"choice-hash-{uuid4().hex}",
        status=DatasetVersionStatus.READY,
    )
    StagedResourcePublicationDataset.objects.create(
        publication=publication,
        dataset=dataset,
        dataset_version=version,
    )

    choices = StagedResourceIntakeAdmin._removal_choices(publication.intake)

    assert choices == [
        (
            str(publication.id),
            f"{organization.name} — Organization (attempt 1; Registration pending; 1 Dataset(s))",
        ),
    ]


def test_detaching_completed_publication_without_outputs_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Detach a completed all-skipped workbook without requiring Dataset assignments."""
    owner = User.objects.create_superuser(username="empty-detach-owner", password="password")
    organization = Organization.objects.create(
        name="Empty Detach Organization",
        slug="empty-detach-organization",
        last_modified_by=owner,
    )
    namespace = RegistryNamespace.objects.create(id=uuid4(), organization=organization)
    publication = _publication(
        owner=owner,
        organization=organization,
        workspace=None,
        namespace=namespace,
        suffix="empty",
    )
    service = DestinationAssignmentRemovalService()
    monkeypatch.setattr(
        service,
        "_get_publication_job",
        lambda *, publication: SimpleNamespace(
            status=WorkbookIngestionJobStatus.COMPLETED_WITH_SKIPS,
            result_payload_json={
                "created": [],
                "deduplicated": [],
                "skipped": [{"code": "invalid"}],
            },
        ),
    )

    result = service.request_detachment(
        user=owner,
        publication_id=publication.id,
        reason="Workbook had no publishable sheets.",
    )

    publication.refresh_from_db()
    assert result.is_pending is False
    assert publication.assignment_status == StagedResourcePublicationStatus.DETACHED


def test_detaching_running_csv_registration_waits_for_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep a CSV publication pending until its active registration job is terminal."""
    owner = User.objects.create_superuser(
        username=f"csv-detach-owner-{uuid4().hex}",
        password="password",
    )
    organization = Organization.objects.create(
        name=f"CSV Detach Organization {uuid4().hex}",
        slug=f"csv-detach-{uuid4().hex}",
        last_modified_by=owner,
    )
    namespace = RegistryNamespace.objects.create(id=uuid4(), organization=organization)
    publication = _publication(
        owner=owner,
        organization=organization,
        workspace=None,
        namespace=namespace,
        suffix=f"csv-running-{uuid4().hex}",
    )
    active_job = DatasetRegistrationJob(
        id=uuid4(),
        tenant_id=namespace.id,
        status=DatasetRegistrationJobStatus.RUNNING,
    )
    cancellation_requests: list[tuple[str, str]] = []

    class CsvRegistrationJobService:
        """Capture cancellation requests without persisting an unrelated job fixture."""

        def request_cancellation(self, *, tenant_id: str, registration_job_id: str) -> None:
            """Capture the requested CSV registration cancellation.

            Args:
                tenant_id: Registry namespace identifier owning the job.
                registration_job_id: CSV registration job identifier.
            """
            cancellation_requests.append((tenant_id, registration_job_id))

    service = DestinationAssignmentRemovalService(
        dataset_registration_job_service=CsvRegistrationJobService(),  # type: ignore[arg-type]
    )
    monkeypatch.setattr(service, "_get_publication_job", lambda *, publication: active_job)

    result = service.request_detachment(
        user=owner,
        publication_id=publication.id,
        reason="Cancel the active CSV registration before detaching.",
    )

    publication.refresh_from_db()
    assert result.is_pending is True
    assert publication.assignment_status == StagedResourcePublicationStatus.DETACH_REQUESTED
    assert cancellation_requests == [(str(namespace.id), str(active_job.id))]


@pytest.mark.parametrize(
    "result_payload",
    [None, {}, {"created": [], "deduplicated": "invalid"}, {"created": [{}], "deduplicated": []}],
)
def test_detaching_completed_publication_without_provable_outputs_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    result_payload: object,
) -> None:
    """Keep removal pending when a completed job cannot prove its output set."""
    owner = User.objects.create_superuser(
        username=f"invalid-detach-{uuid4().hex}",
        password="password",
    )
    organization = Organization.objects.create(
        name=f"Invalid Detach Organization {uuid4().hex}",
        slug=f"invalid-detach-{uuid4().hex}",
        last_modified_by=owner,
    )
    namespace = RegistryNamespace.objects.create(id=uuid4(), organization=organization)
    publication = _publication(
        owner=owner,
        organization=organization,
        workspace=None,
        namespace=namespace,
        suffix=f"invalid-{uuid4().hex}",
    )
    service = DestinationAssignmentRemovalService()
    monkeypatch.setattr(
        service,
        "_get_publication_job",
        lambda *, publication: SimpleNamespace(
            status=WorkbookIngestionJobStatus.COMPLETED,
            result_payload_json=result_payload,
        ),
    )

    with pytest.raises(UploadApplicationError) as error:
        service.request_detachment(
            user=owner,
            publication_id=publication.id,
            reason="Result payload is not trustworthy.",
        )

    publication.refresh_from_db()
    assert error.value.code == "PUBLICATION_OUTPUT_RECONCILIATION_REQUIRED"
    assert publication.assignment_status == StagedResourcePublicationStatus.DETACH_REQUESTED


def test_detaching_completed_publication_with_incomplete_output_mapping_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep removal pending unless every completed output has an exact mapping."""
    owner = User.objects.create_superuser(
        username=f"partial-detach-{uuid4().hex}",
        password="password",
    )
    organization = Organization.objects.create(
        name=f"Partial Detach Organization {uuid4().hex}",
        slug=f"partial-detach-{uuid4().hex}",
        last_modified_by=owner,
    )
    namespace = RegistryNamespace.objects.create(id=uuid4(), organization=organization)
    publication = _publication(
        owner=owner,
        organization=organization,
        workspace=None,
        namespace=namespace,
        suffix=f"partial-{uuid4().hex}",
    )
    dataset = Dataset.objects.create(
        tenant_id=namespace.id,
        organization=organization,
        visibility_scope=DatasetVisibilityScope.ORGANIZATION,
        slug=f"partial-dataset-{uuid4().hex}",
        name="Partial Dataset",
        source_type="FILE_UPLOAD",
        status=DatasetStatus.ACTIVE,
    )
    mapped_version = DatasetVersion.objects.create(
        dataset=dataset,
        version_number=1,
        source_uri="azure://example/partial.xlsx",
        content_hash=f"partial-hash-{uuid4().hex}",
        status=DatasetVersionStatus.READY,
    )
    unmapped_version = DatasetVersion.objects.create(
        dataset=dataset,
        version_number=2,
        source_uri="azure://example/partial-v2.xlsx",
        content_hash=f"partial-hash-v2-{uuid4().hex}",
        status=DatasetVersionStatus.READY,
    )
    StagedResourcePublicationDataset.objects.create(
        publication=publication,
        dataset=dataset,
        dataset_version=mapped_version,
    )
    service = DestinationAssignmentRemovalService()
    monkeypatch.setattr(
        service,
        "_get_publication_job",
        lambda *, publication: SimpleNamespace(
            status=WorkbookIngestionJobStatus.COMPLETED,
            result_payload_json={
                "created": [
                    {
                        "dataset_id": str(dataset.id),
                        "dataset_version_id": str(mapped_version.id),
                    },
                    {
                        "dataset_id": str(dataset.id),
                        "dataset_version_id": str(unmapped_version.id),
                    },
                ],
                "deduplicated": [],
            },
        ),
    )

    with pytest.raises(UploadApplicationError) as error:
        service.request_detachment(
            user=owner,
            publication_id=publication.id,
            reason="One output mapping is missing.",
        )

    publication.refresh_from_db()
    assert error.value.code == "PUBLICATION_OUTPUT_RECONCILIATION_REQUIRED"
    assert publication.assignment_status == StagedResourcePublicationStatus.DETACH_REQUESTED


def _publication(
    *,
    owner: User,
    organization: Organization,
    workspace: Workspace | None,
    namespace: RegistryNamespace,
    suffix: str,
) -> StagedResourcePublication:
    """Create one active destination publication with independent intake provenance."""
    intake = StagedResourceIntake.objects.create(
        submitted_by=owner,
        original_filename=f"{suffix}.xlsx",
        normalized_filename=f"{suffix}.xlsx",
        resolved_file_format="XLSX",
        declared_content_type=("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        declared_size_bytes=1,
        storage_provider="local",
        storage_object_key=f"resource-registry/intakes/{suffix}/source.xlsx",
        idempotency_key=f"intake-{suffix}",
        request_fingerprint=f"fingerprint-{suffix}",
        upload_authorization_expires_at=timezone.now() + timedelta(hours=1),
        expires_at=timezone.now() + timedelta(days=1),
        status=StagedResourceIntakeStatus.PUBLISHED,
    )
    return StagedResourcePublication.objects.create(
        intake=intake,
        registry_namespace=namespace,
        organization=organization,
        workspace=workspace,
        visibility_scope=(
            DatasetVisibilityScope.WORKSPACE
            if workspace is not None
            else DatasetVisibilityScope.ORGANIZATION
        ),
        published_by=owner,
    )
