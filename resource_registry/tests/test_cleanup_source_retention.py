"""Keep published originals recoverable for a full retention period after reuse."""

from contextlib import nullcontext
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from django.utils import timezone
from pytest_django.fixtures import SettingsWrapper

from resource_registry.cleanup.sweeper import discover_cleanup_candidates
from resource_registry.cleanup.worker import CleanupWorker
from resource_registry.management_domain.changes import DatasetChangeService
from resource_registry.management_domain.errors import DatasetManagementError
from resource_registry.management_domain.worker import DatasetChangeWorker
from resource_registry.models import (
    DatasetChange,
    DatasetManagementConfiguration,
    ResourceCleanupRequest,
    StagedResourceIntake,
    StagedResourcePublication,
)
from resource_registry.tests.test_dataset_maintenance_flows import Scope, prepare_csv
from resource_registry.tests.test_dataset_management import management_scope  # noqa: F401


@pytest.mark.parametrize("operation", ["revise", "retry"])
@pytest.mark.parametrize("preplanned", [False, True])
@pytest.mark.parametrize("preparation_fails", [False, True])
def test_recalculation_renews_source_retention_at_actual_worker_use(
    management_scope: Scope,
    tmp_path: Path,
    settings: SettingsWrapper,
    operation: str,
    preplanned: bool,
    preparation_fails: bool,
) -> None:
    """Recheck discovery and existing cleanup plans against the latest preparation use."""
    settings.RESOURCE_REGISTRY_LOCAL_STORAGE_DIR = str(tmp_path)
    _, editor, organization, namespace, dataset = management_scope
    published = prepare_csv(management_scope, tmp_path / "source.csv", "id,amount\n1,10\n")
    service = DatasetChangeService(editor, organization)
    service.publish(published.pk, expected_revision=published.revision)
    dataset.refresh_from_db()
    change = service.create(
        namespace_id=namespace.pk,
        intake_id=published.intake_id,
        idempotency_key="reused-source",
        specification={"dataset_id": str(dataset.pk), "expected_revision": dataset.revision},
    )
    DatasetManagementConfiguration.objects.update(cleanup_mode="EXECUTE")
    old = timezone.now() - timedelta(days=31)
    StagedResourceIntake.objects.filter(pk=published.intake_id).update(
        created_at=old, published_at=old, source_last_used_at=old
    )
    StagedResourcePublication.objects.update(completed_at=old)
    DatasetChange.objects.filter(pk=change.pk).update(status="FAILED")
    if preplanned:
        discover_cleanup_candidates()
        assert ResourceCleanupRequest.objects.filter(intake_id=published.intake_id).exists()
    if operation == "revise":
        DatasetChange.objects.filter(pk=change.pk).update(status="READY")
        service.revise(change.pk, change.specification, expected_revision=change.revision)
    else:
        service.retry(change.pk, expected_revision=change.revision)

    # A worker can start later than the HTTP request; the actual read renews the deadline.
    used_at = timezone.now() + timedelta(days=2)
    failure = (
        patch.object(DatasetChangeWorker, "_checksum", side_effect=ValueError("invalid file"))
        if preparation_fails
        else nullcontext()
    )
    with patch("django.utils.timezone.now", return_value=used_at), failure:
        DatasetChangeWorker().run_once(change_id=change.pk)
    change.refresh_from_db()
    assert change.status == ("FAILED" if preparation_fails else "NO_CHANGES")
    intake = StagedResourceIntake.objects.get(pk=published.intake_id)
    assert intake.source_last_used_at == used_at
    source_path = tmp_path / intake.storage_object_key

    with patch("django.utils.timezone.now", return_value=used_at + timedelta(days=29)):
        discover_cleanup_candidates()
        if not preplanned:
            assert not ResourceCleanupRequest.objects.filter(intake=intake).exists()
        CleanupWorker().run_once()
    intake.refresh_from_db()
    assert intake.source_deleted_at is None
    assert source_path.exists()

    with patch("django.utils.timezone.now", return_value=used_at + timedelta(days=31)):
        discover_cleanup_candidates()
        CleanupWorker().run_once()
    intake.refresh_from_db()
    assert intake.source_deleted_at is not None
    assert intake.status == "PUBLISHED"
    assert not source_path.exists()


@pytest.mark.parametrize("field", ["source_cleanup_started_at", "source_deleted_at"])
def test_recalculation_rejects_original_already_claimed_for_cleanup(
    management_scope: Scope, tmp_path: Path, settings: SettingsWrapper, field: str
) -> None:
    """Reject new use once cleanup owns the original, without requeueing the update."""
    settings.RESOURCE_REGISTRY_LOCAL_STORAGE_DIR = str(tmp_path)
    _, editor, organization, _, _ = management_scope
    change = prepare_csv(management_scope, tmp_path / "source.csv", "id,amount\n1,10\n")
    StagedResourceIntake.objects.filter(pk=change.intake_id).update(**{field: timezone.now()})
    with pytest.raises(DatasetManagementError, match="Upload"):
        DatasetChangeService(editor, organization).revise(
            change.pk, change.specification, expected_revision=change.revision
        )
    change.refresh_from_db()
    assert change.status == "READY"
