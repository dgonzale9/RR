"""Verify automatic dataset preparation through the application's background executor."""

from concurrent.futures import Future
from pathlib import Path
from threading import Event
from typing import cast
from uuid import uuid4

import pytest
from django.contrib import admin
from django.db import transaction
from django.test import TestCase
from guardian.shortcuts import assign_perm, remove_perm
from pytest_django.fixtures import SettingsWrapper

from resource_registry.dataset_admin import DatasetAdmin
from resource_registry.management_domain import background
from resource_registry.management_domain.changes import DatasetChangeService
from resource_registry.management_domain.errors import DatasetManagementError
from resource_registry.management_domain.worker import DatasetChangeWorker
from resource_registry.models import (
    Dataset,
    DatasetChange,
    DatasetManagementConfiguration,
    StagedResourceInspectionJob,
)
from resource_registry.tests.background_processing_helpers import (  # noqa: F401
    DeferredExecutor,
    deferred_background_tasks,
)
from resource_registry.tests.test_dataset_changes import create_verified_intake
from resource_registry.tests.test_dataset_maintenance_flows import Scope
from resource_registry.tests.test_dataset_management import management_scope  # noqa: F401
from resource_registry.tests.test_dataset_management_admin import make_admin_request


def test_proposal_creation_schedules_preparation_after_commit(
    management_scope: Scope, tmp_path: Path
) -> None:
    """Arrange automatic preparation while keeping candidates private until publication."""
    manager, _, organization, namespace, dataset = management_scope
    path = tmp_path / "automatic.csv"
    path.write_text("id,amount\n1,10\n")
    with TestCase.captureOnCommitCallbacks() as callbacks:
        change = DatasetChangeService(manager, organization).create(
            namespace_id=namespace.id,
            intake_id=create_verified_intake(manager, path).id,
            idempotency_key="automatic",
            specification={"dataset_id": str(dataset.id), "expected_revision": dataset.revision},
        )
    assert callbacks, "The committed proposal must wake background preparation automatically"
    assert change.status == "QUEUED"
    assert not dataset.versions.exists()


def test_admin_status_wakes_only_the_owned_pending_inspection(
    management_scope: Scope, tmp_path: Path, settings: SettingsWrapper
) -> None:
    """Resume a persisted upload inspection when its authorized owner checks progress."""
    manager, _, organization, _, _ = management_scope
    settings.RESOURCE_REGISTRY_UPLOAD_PROVIDER = "local"
    path = tmp_path / "pending.csv"
    path.write_text("id\n1\n")
    intake = create_verified_intake(manager, path)
    intake.status = "COMPLETED"
    intake.expires_at = None
    intake.save()
    StagedResourceInspectionJob.objects.create(intake=intake)
    model_admin = cast(DatasetAdmin, admin.site._registry[Dataset])
    request = make_admin_request(manager, organization, "/status/")
    with TestCase.captureOnCommitCallbacks() as callbacks:
        response = model_admin.intake_action_view(request, intake.id, "status")
    assert response.status_code == 200
    assert callbacks, "Admin progress must wake the pending inspection without a command"


def _queue_proposal(scope: Scope, path: Path) -> DatasetChange:
    manager, _, organization, namespace, dataset = scope
    path.write_text("id,amount\n1,10\n")
    return DatasetChangeService(manager, organization).create(
        namespace_id=namespace.id,
        intake_id=create_verified_intake(manager, path).id,
        idempotency_key=str(uuid4()),
        specification={"dataset_id": str(dataset.id), "expected_revision": dataset.revision},
    )


def test_rollback_does_not_submit_or_prepare_a_proposal(
    management_scope: Scope, tmp_path: Path, deferred_background_tasks: DeferredExecutor
) -> None:
    """Discard the wake-up together with a rolled-back proposal transaction."""
    with TestCase.captureOnCommitCallbacks(execute=True):
        with pytest.raises(RuntimeError, match="rollback"):
            with transaction.atomic():
                _queue_proposal(management_scope, tmp_path / "rolled-back.csv")
                assert not deferred_background_tasks.tasks
                raise RuntimeError("rollback")
    assert not deferred_background_tasks.tasks
    assert not DatasetChange.objects.exists()


def test_progress_resumes_only_its_persisted_proposal(
    management_scope: Scope, tmp_path: Path, deferred_background_tasks: DeferredExecutor
) -> None:
    """Resume a queued proposal after a lost wake-up without draining unrelated work."""
    manager, _, organization, _, dataset = management_scope
    unrelated = _queue_proposal(management_scope, tmp_path / "unrelated.csv")
    selected = _queue_proposal(management_scope, tmp_path / "selected.csv")
    with TestCase.captureOnCommitCallbacks(execute=True):
        DatasetChangeService(manager, organization).get_change(selected.id)
    assert len(deferred_background_tasks.tasks) == 1
    deferred_background_tasks.run_next()
    unrelated.refresh_from_db()
    selected.refresh_from_db()
    assert unrelated.status == "QUEUED"
    assert selected.status == "READY"
    assert not dataset.versions.exists()


def test_duplicate_wakeups_are_coalesced_and_preserve_revised_selection(
    management_scope: Scope, tmp_path: Path, deferred_background_tasks: DeferredExecutor
) -> None:
    """Bound repeated progress reads and retain a recalculation requested in flight."""
    manager, _, organization, _, _ = management_scope
    service = DatasetChangeService(manager, organization)
    with TestCase.captureOnCommitCallbacks(execute=True):
        change = _queue_proposal(management_scope, tmp_path / "revised.csv")
    with TestCase.captureOnCommitCallbacks(execute=True):
        for _ in range(10):
            service.get_change(change.id)
        service.revise(change.id, {**change.specification, "name": "Reviewed"}, expected_revision=0)
    assert len(deferred_background_tasks.tasks) == 1
    deferred_background_tasks.run_next()
    assert len(deferred_background_tasks.tasks) == 1
    deferred_background_tasks.run_next()
    assert not deferred_background_tasks.tasks
    change.refresh_from_db()
    assert change.status == "READY"
    assert change.revision == 1
    assert change.prepared["items"][0]["name"] == "Reviewed"


def test_saturated_executor_keeps_work_durable_until_next_progress_read(
    management_scope: Scope,
    tmp_path: Path,
    deferred_background_tasks: DeferredExecutor,
    settings: SettingsWrapper,
) -> None:
    """Limit pending submissions without dropping proposals or blocking requests."""
    settings.MAX_GLOBAL_WORKERS = 1
    manager, _, organization, _, _ = management_scope
    with TestCase.captureOnCommitCallbacks(execute=True):
        first = _queue_proposal(management_scope, tmp_path / "first.csv")
        second = _queue_proposal(management_scope, tmp_path / "second.csv")
    assert len(deferred_background_tasks.tasks) == 1
    deferred_background_tasks.run_next()
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.status == "READY"
    assert second.status == "QUEUED"
    with TestCase.captureOnCommitCallbacks(execute=True):
        DatasetChangeService(manager, organization).get_change(second.id)
    deferred_background_tasks.run_next()
    second.refresh_from_db()
    assert second.status == "READY"


def test_executor_admission_failure_preserves_proposal_for_retry(
    management_scope: Scope,
    tmp_path: Path,
    deferred_background_tasks: DeferredExecutor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recover from an executor shutting down without returning an ambiguous HTTP failure."""
    manager, _, organization, _, _ = management_scope

    def unavailable_executor() -> None:
        raise RuntimeError("Executor is shutting down")

    monkeypatch.setattr(background, "_get_executor", unavailable_executor)
    with TestCase.captureOnCommitCallbacks(execute=True):
        change = _queue_proposal(management_scope, tmp_path / "retry.csv")
    assert change.status == "QUEUED"
    assert not background._pending_tasks
    monkeypatch.setattr(background, "_get_executor", lambda: deferred_background_tasks)
    with TestCase.captureOnCommitCallbacks(execute=True):
        DatasetChangeService(manager, organization).get_change(change.id)
    deferred_background_tasks.run_next()
    change.refresh_from_db()
    assert change.status == "READY"


@pytest.mark.parametrize("action", ["revoke", "cancel", "disable"])
def test_background_preparation_obeys_live_authority_and_terminal_state(
    management_scope: Scope,
    tmp_path: Path,
    deferred_background_tasks: DeferredExecutor,
    action: str,
) -> None:
    """Respect revoked editing, pilot rollback and cancellation before a task executes."""
    _, editor, organization, namespace, dataset = management_scope
    assign_perm("resource_registry.change_dataset", editor, dataset)
    path = tmp_path / "guarded.csv"
    path.write_text("id\n1\n")
    service = DatasetChangeService(editor, organization)
    with TestCase.captureOnCommitCallbacks(execute=True):
        change = service.create(
            namespace_id=namespace.id,
            intake_id=create_verified_intake(editor, path).id,
            idempotency_key="guarded",
            specification={"dataset_id": str(dataset.id), "expected_revision": dataset.revision},
        )
    if action == "revoke":
        remove_perm("resource_registry.change_dataset", editor, dataset)
    elif action == "cancel":
        service.cancel(change.id, expected_revision=change.revision)
    else:
        DatasetManagementConfiguration.objects.filter(organization=organization).update(
            enabled=False
        )
    deferred_background_tasks.run_next()
    change.refresh_from_db()
    assert change.status == {"revoke": "FAILED", "cancel": "CANCELLED", "disable": "QUEUED"}[action]
    if action == "revoke":
        assert change.error_code == "edit_denied"
    assert not dataset.versions.exists()


def test_inspection_wakeup_does_not_claim_other_uploads(
    management_scope: Scope,
    tmp_path: Path,
    deferred_background_tasks: DeferredExecutor,
    settings: SettingsWrapper,
) -> None:
    """Inspect only the owned upload selected by the Admin progress request."""
    manager, editor, organization, _, _ = management_scope
    settings.RESOURCE_REGISTRY_UPLOAD_PROVIDER = "local"
    path = tmp_path / "inspection.csv"
    path.write_text("id\n1\n")
    unrelated = create_verified_intake(editor, path)
    selected = create_verified_intake(manager, path)
    for intake in (unrelated, selected):
        intake.status = "COMPLETED"
        intake.expires_at = None
        intake.save()
        StagedResourceInspectionJob.objects.create(intake=intake)
    model_admin = cast(DatasetAdmin, admin.site._registry[Dataset])
    request = make_admin_request(manager, organization, "/status/")
    with TestCase.captureOnCommitCallbacks(execute=True):
        assert model_admin.intake_action_view(request, unrelated.id, "status").status_code == 404
    assert not deferred_background_tasks.tasks
    with TestCase.captureOnCommitCallbacks(execute=True):
        assert model_admin.intake_action_view(request, selected.id, "status").status_code == 200
    deferred_background_tasks.run_next()
    unrelated.refresh_from_db()
    selected.refresh_from_db()
    assert unrelated.status == "COMPLETED"
    assert selected.status == "READY_FOR_PUBLICATION"


def test_retry_automatically_prepares_after_a_recoverable_failure(
    management_scope: Scope,
    tmp_path: Path,
    deferred_background_tasks: DeferredExecutor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep failure evidence and automatically execute an explicitly requested retry."""
    manager, _, organization, _, _ = management_scope
    original_prepare = DatasetChangeWorker._prepare_file

    def unavailable_source(worker: DatasetChangeWorker, change: DatasetChange) -> None:
        raise DatasetManagementError("source_unavailable", "Source is temporarily unavailable.")

    monkeypatch.setattr(DatasetChangeWorker, "_prepare_file", unavailable_source)
    with TestCase.captureOnCommitCallbacks(execute=True):
        change = _queue_proposal(management_scope, tmp_path / "recoverable.csv")
    deferred_background_tasks.run_next()
    change.refresh_from_db()
    assert change.status == "FAILED"
    assert change.error_code == "source_unavailable"
    monkeypatch.setattr(DatasetChangeWorker, "_prepare_file", original_prepare)
    with TestCase.captureOnCommitCallbacks(execute=True):
        DatasetChangeService(manager, organization).retry(
            change.id, expected_revision=change.revision
        )
    assert len(deferred_background_tasks.tasks) == 1
    deferred_background_tasks.run_next()
    change.refresh_from_db()
    assert change.status == "READY"
    assert not management_scope[-1].versions.exists()


@pytest.mark.django_db(transaction=True)
def test_real_executor_prepares_after_request_returns_without_publishing(
    management_scope: Scope, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise real executor threads and PostgreSQL while holding file preparation open."""
    entered = Event()
    release = Event()
    finished = Event()
    original_prepare = DatasetChangeWorker._prepare_file
    original_finish = background._finish_task

    def prepare_after_release(
        worker: DatasetChangeWorker, change: DatasetChange
    ) -> dict[str, object]:
        entered.set()
        assert release.wait(10), "The request must return before file preparation finishes"
        return original_prepare(worker, change)

    def finish_and_signal(key: background.TaskKey, future: Future[None]) -> None:
        original_finish(key, future)
        finished.set()

    monkeypatch.setattr(DatasetChangeWorker, "_prepare_file", prepare_after_release)
    monkeypatch.setattr(background, "_finish_task", finish_and_signal)
    try:
        change = _queue_proposal(management_scope, tmp_path / "threaded.csv")
        assert entered.wait(10)
        change.refresh_from_db()
        assert change.status == "PROCESSING"
    finally:
        release.set()
        assert finished.wait(15)
    change.refresh_from_db()
    assert change.status == "READY"
    assert not management_scope[-1].versions.exists()
