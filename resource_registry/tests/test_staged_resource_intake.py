"""Focused contracts for private unassigned resource intake."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from resource_registry.intake.services import StagedResourceIntakeService
from resource_registry.models import (
    StagedResourceInspectionJob,
    StagedResourceInspectionJobStatus,
    StagedResourceIntake,
    StagedResourceIntakeStatus,
)
from resource_registry.services.json_dataset import JsonDatasetValidationError
from resource_registry.uploads.contracts import DirectUploadAuthorization, UploadedObjectMetadata

pytestmark = pytest.mark.django_db(transaction=True)


@dataclass
class FakeIntakeUploadProvider:
    """Provide a deterministic direct-upload provider for intake tests."""

    provider_name: str = "azure_blob"
    deleted_keys: tuple[str, ...] = ()

    def create_upload_authorization(
        self,
        *,
        object_key: str,
        content_type: str,
        expires_at: datetime,
        recommended_chunk_size_bytes: int,
        max_parallel_chunks: int,
    ) -> DirectUploadAuthorization:
        """Return a non-secret deterministic upload authorization."""
        _ = object_key
        return DirectUploadAuthorization(
            upload_url="https://storage.invalid/upload",
            expires_at=expires_at,
            upload_method="block_blob",
            recommended_chunk_size_bytes=recommended_chunk_size_bytes,
            max_parallel_chunks=max_parallel_chunks,
            required_headers={"Content-Type": content_type},
        )

    def inspect_uploaded_object(self, *, object_key: str) -> UploadedObjectMetadata:
        """Return synthetic metadata for protocol completeness."""
        return UploadedObjectMetadata(
            object_key=object_key,
            size_bytes=8,
            content_type="text/csv",
            initial_bytes=b"name\nrow\n",
        )

    def delete_object(self, *, object_key: str) -> None:
        """Record exact-object cleanup."""
        self.deleted_keys = (*self.deleted_keys, object_key)

    def copy_object(
        self,
        *,
        source_object_key: str,
        destination_object_key: str,
    ) -> UploadedObjectMetadata:
        """Return synthetic destination metadata for protocol completeness."""
        _ = source_object_key
        return UploadedObjectMetadata(
            object_key=destination_object_key,
            size_bytes=8,
            content_type="text/csv",
        )


def test_create_intake_has_no_registry_owner_and_is_idempotent() -> None:
    """Create private staging state without Organization or Workspace provenance."""
    user = User.objects.create_user(username="intake-user", password="password")
    provider = FakeIntakeUploadProvider()
    service = StagedResourceIntakeService(provider=provider)

    first = service.create_intake(
        user=user,
        filename="financials.csv",
        content_type="text/csv",
        size_bytes=8,
        checksum_algorithm=None,
        checksum=None,
        idempotency_key="intake-001",
    )
    replay = service.create_intake(
        user=user,
        filename="financials.csv",
        content_type="text/csv",
        size_bytes=8,
        checksum_algorithm=None,
        checksum=None,
        idempotency_key="intake-001",
    )

    intake = StagedResourceIntake.objects.get(pk=first.intake.intake_id)
    assert replay.idempotent_replay is True
    assert intake.status == StagedResourceIntakeStatus.PENDING_UPLOAD
    assert not {"organization", "workspace", "registry_namespace"} & {
        field.name for field in StagedResourceIntake._meta.get_fields()
    }


def test_expire_intake_deletes_only_its_staging_object() -> None:
    """Expire a private intake and request cleanup for only its server-selected key."""
    user = User.objects.create_user(username="expired-intake", password="password")
    provider = FakeIntakeUploadProvider()
    service = StagedResourceIntakeService(provider=provider)
    result = service.create_intake(
        user=user,
        filename="financials.csv",
        content_type="text/csv",
        size_bytes=8,
        checksum_algorithm=None,
        checksum=None,
        idempotency_key="intake-expire-001",
    )
    intake = StagedResourceIntake.objects.get(pk=result.intake.intake_id)
    intake.expires_at = timezone.now() - timedelta(seconds=1)
    intake.save(update_fields=["expires_at"])

    assert service.expire_intakes() == 1
    intake.refresh_from_db()
    assert intake.status == StagedResourceIntakeStatus.EXPIRED
    assert provider.deleted_keys == (intake.storage_object_key,)


def test_complete_intake_queues_inspection_without_creating_datasets() -> None:
    """Queue durable inspection after verification instead of parsing in the HTTP boundary."""
    user = User.objects.create_user(username="queued-inspection", password="password")
    service = StagedResourceIntakeService(provider=FakeIntakeUploadProvider())
    created = service.create_intake(
        user=user,
        filename="financials.csv",
        content_type="text/csv",
        size_bytes=8,
        checksum_algorithm=None,
        checksum=None,
        idempotency_key="intake-queue-001",
    )

    completed = service.complete_intake(
        user=user,
        intake_id=created.intake.intake_id,
        expected_etag=None,
    )

    assert completed.status == StagedResourceIntakeStatus.COMPLETED
    job = StagedResourceInspectionJob.objects.get(intake_id=created.intake.intake_id)
    assert job.status == StagedResourceInspectionJobStatus.QUEUED


def test_inspection_does_not_restore_an_expired_intake_to_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserve expiration when the intake expires while a worker is inspecting it."""
    user = User.objects.create_user(username="inspection-expiry", password="password")
    service = StagedResourceIntakeService(provider=FakeIntakeUploadProvider())
    created = service.create_intake(
        user=user,
        filename="financials.csv",
        content_type="text/csv",
        size_bytes=8,
        checksum_algorithm=None,
        checksum=None,
        idempotency_key="intake-inspection-expiry-001",
    )
    intake = StagedResourceIntake.objects.get(pk=created.intake.intake_id)
    intake.status = StagedResourceIntakeStatus.COMPLETED
    intake.actual_size_bytes = 8
    intake.save(update_fields=["status", "actual_size_bytes"])
    StagedResourceInspectionJob.objects.create(intake=intake)

    def expire_intake_during_inspection(*, intake: StagedResourceIntake) -> dict[str, object]:
        """Simulate expiry after the worker releases its claim transaction."""
        StagedResourceIntake.objects.filter(pk=intake.pk).update(
            status=StagedResourceIntakeStatus.EXPIRED,
            cleanup_required=True,
            failure_code="INTAKE_EXPIRED",
        )
        return {"content_hash": "digest", "sheets": []}

    monkeypatch.setattr(service, "_inspect_uploaded_file", expire_intake_during_inspection)

    assert service.run_inspection_jobs() == 0
    intake.refresh_from_db()
    job = StagedResourceInspectionJob.objects.get(intake=intake)
    assert intake.status == StagedResourceIntakeStatus.EXPIRED
    assert job.status == StagedResourceInspectionJobStatus.FAILED
    assert job.error_code == "INTAKE_EXPIRED"


def test_inspection_failure_stops_the_current_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stop after one failed inspection to contain provider-wide failures."""
    user = User.objects.create_user(username="inspection-batch-failure", password="password")
    service = StagedResourceIntakeService(provider=FakeIntakeUploadProvider())
    first = service.create_intake(
        user=user,
        filename="first.csv",
        content_type="text/csv",
        size_bytes=8,
        checksum_algorithm=None,
        checksum=None,
        idempotency_key="intake-batch-failure-001",
    )
    second = service.create_intake(
        user=user,
        filename="second.csv",
        content_type="text/csv",
        size_bytes=8,
        checksum_algorithm=None,
        checksum=None,
        idempotency_key="intake-batch-failure-002",
    )
    first_intake = StagedResourceIntake.objects.get(pk=first.intake.intake_id)
    second_intake = StagedResourceIntake.objects.get(pk=second.intake.intake_id)
    for intake in (first_intake, second_intake):
        intake.status = StagedResourceIntakeStatus.COMPLETED
        intake.actual_size_bytes = 8
        intake.save(update_fields=["status", "actual_size_bytes"])
        StagedResourceInspectionJob.objects.create(intake=intake)

    def raise_provider_error(*, intake: StagedResourceIntake) -> dict[str, object]:
        """Simulate a provider failure for the first claimed inspection."""
        _ = intake
        raise OSError("storage unavailable")

    monkeypatch.setattr(service, "_inspect_uploaded_file", raise_provider_error)

    assert service.run_inspection_jobs(batch_size=2) == 0

    jobs = tuple(
        StagedResourceInspectionJob.objects.filter(intake__in=(first_intake, second_intake))
    )
    assert all(job.status == StagedResourceInspectionJobStatus.QUEUED for job in jobs)
    assert sorted(job.retry_count for job in jobs) == [0, 1]


def test_json_validation_failure_is_terminal_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Persist a safe JSON validation code without consuming retry attempts."""
    user = User.objects.create_user(username="json-validation-failure", password="password")
    service = StagedResourceIntakeService(provider=FakeIntakeUploadProvider())
    first = service.create_intake(
        user=user,
        filename="first.json",
        content_type="application/json",
        size_bytes=8,
        checksum_algorithm=None,
        checksum=None,
        idempotency_key="json-validation-failure-001",
    )
    second = service.create_intake(
        user=user,
        filename="second.json",
        content_type="application/json",
        size_bytes=8,
        checksum_algorithm=None,
        checksum=None,
        idempotency_key="json-validation-failure-002",
    )
    first_intake = StagedResourceIntake.objects.get(pk=first.intake.intake_id)
    second_intake = StagedResourceIntake.objects.get(pk=second.intake.intake_id)
    for intake in (first_intake, second_intake):
        intake.status = StagedResourceIntakeStatus.COMPLETED
        intake.actual_size_bytes = 8
        intake.save(update_fields=["status", "actual_size_bytes"])
        StagedResourceInspectionJob.objects.create(intake=intake)

    def raise_json_validation_error(*, intake: StagedResourceIntake) -> dict[str, object]:
        """Simulate a deterministic JSON dataset validation failure."""
        _ = intake
        raise JsonDatasetValidationError("JSON_ROOT_MUST_BE_NONEMPTY_ARRAY")

    monkeypatch.setattr(service, "_inspect_uploaded_file", raise_json_validation_error)

    assert service.run_inspection_jobs(batch_size=2) == 0

    first_intake.refresh_from_db()
    second_intake.refresh_from_db()
    first_job = StagedResourceInspectionJob.objects.get(intake=first_intake)
    second_job = StagedResourceInspectionJob.objects.get(intake=second_intake)
    assert first_intake.status == StagedResourceIntakeStatus.FAILED
    assert first_intake.failure_code == "JSON_ROOT_MUST_BE_NONEMPTY_ARRAY"
    assert first_job.status == StagedResourceInspectionJobStatus.FAILED
    assert first_job.error_code == "JSON_ROOT_MUST_BE_NONEMPTY_ARRAY"
    assert first_job.retry_count == 0
    assert second_job.status == StagedResourceInspectionJobStatus.QUEUED
    assert second_job.retry_count == 0


def test_expiration_worker_retries_staging_cleanup_after_publication() -> None:
    """Retry failed staging cleanup without changing a published intake's lifecycle."""
    user = User.objects.create_user(username="published-intake-cleanup", password="password")
    provider = FakeIntakeUploadProvider()
    service = StagedResourceIntakeService(provider=provider)
    created = service.create_intake(
        user=user,
        filename="financials.csv",
        content_type="text/csv",
        size_bytes=8,
        checksum_algorithm=None,
        checksum=None,
        idempotency_key="intake-published-cleanup-001",
    )
    intake = StagedResourceIntake.objects.get(pk=created.intake.intake_id)
    intake.status = StagedResourceIntakeStatus.PUBLISHED
    intake.cleanup_required = True
    intake.save(update_fields=["status", "cleanup_required"])

    assert service.expire_intakes() == 0
    intake.refresh_from_db()
    assert intake.status == StagedResourceIntakeStatus.PUBLISHED
    assert intake.cleanup_required is False
    assert provider.deleted_keys == (intake.storage_object_key,)


def test_published_intake_ignores_its_legacy_staging_deadline() -> None:
    """Allow published-intake publication retries after the former staging deadline."""
    user = User.objects.create_user(username="published-intake-retry", password="password")
    service = StagedResourceIntakeService(provider=FakeIntakeUploadProvider())
    created = service.create_intake(
        user=user,
        filename="financials.csv",
        content_type="text/csv",
        size_bytes=8,
        checksum_algorithm=None,
        checksum=None,
        idempotency_key="intake-published-retry-001",
    )
    intake = StagedResourceIntake.objects.get(pk=created.intake.intake_id)
    intake.status = StagedResourceIntakeStatus.PUBLISHED
    intake.expires_at = timezone.now() - timedelta(seconds=1)
    intake.save(update_fields=["status", "expires_at"])

    service._assert_intake_available_for_publication(intake)


def test_published_intake_has_no_staging_deadline_in_snapshot() -> None:
    """Hide the finished staging deadline from the published intake state."""
    user = User.objects.create_user(username="published-intake-deadline", password="password")
    service = StagedResourceIntakeService(provider=FakeIntakeUploadProvider())
    created = service.create_intake(
        user=user,
        filename="financials.csv",
        content_type="text/csv",
        size_bytes=8,
        checksum_algorithm=None,
        checksum=None,
        idempotency_key="intake-published-deadline-001",
    )
    intake = StagedResourceIntake.objects.get(pk=created.intake.intake_id)
    intake.status = StagedResourceIntakeStatus.PUBLISHED
    intake.expires_at = None
    intake.save(update_fields=["status", "expires_at"])

    snapshot = service.get_intake(user=user, intake_id=intake.id)

    assert snapshot.expires_at is None


def test_publication_upload_idempotency_key_is_unique_per_publication_attempt() -> None:
    """Allow a detached destination to be published again without reusing its upload session."""
    intake_id = uuid4()
    namespace_id = uuid4()

    first_attempt = StagedResourceIntakeService._publication_upload_idempotency_key(
        intake_id=intake_id,
        namespace_id=namespace_id,
        publication_attempt=1,
    )
    second_attempt = StagedResourceIntakeService._publication_upload_idempotency_key(
        intake_id=intake_id,
        namespace_id=namespace_id,
        publication_attempt=2,
    )

    assert first_attempt != second_attempt
    assert first_attempt.endswith(":1")
    assert second_attempt.endswith(":2")
