"""Wake durable dataset jobs through the application's bounded background executor."""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import Executor, Future
from functools import partial
from threading import Lock
from typing import TypeAlias
from uuid import UUID

from django.conf import settings
from django.db import transaction

from BotServer.unicornai_threadpool_executor import UnicornAiThreadpoolExecutor
from resource_registry.intake.inspection_service import StagedResourceIntakeInspectionService
from resource_registry.models import DatasetManagementConfiguration
from resource_registry.services.dataset_context_builder import DatasetContextBuilder
from resource_registry.storage.factory import DataRegistryStorageFactory

TaskKey: TypeAlias = tuple[str, UUID]
_pending_tasks: dict[TaskKey, Callable[[], None] | None] = {}
_pending_lock = Lock()
_logger = logging.getLogger(__name__)


def schedule_dataset_preparation(change_id: UUID) -> None:
    """Wake one persisted proposal after the surrounding transaction commits.

    Args:
        change_id: Authorized proposal whose worker revalidates live scope and revisions.

    Side Effects:
        Register a commit callback that submits bounded background work. Rollbacks
        discard the callback; publication remains an explicit user action.
    """
    transaction.on_commit(
        partial(_submit_task, ("change", change_id), partial(_prepare_dataset_change, change_id))
    )


def schedule_intake_inspection(intake_id: UUID, *, organization_id: int) -> None:
    """Wake an owner-authorized Admin upload inspection after commit.

    Args:
        intake_id: Owned intake already admitted by the Admin service.
        organization_id: Trusted host organization with dataset maintenance enabled.

    Side Effects:
        Submit only this intake's durable inspection, without draining other uploads.
    """
    transaction.on_commit(
        partial(
            _submit_task,
            ("intake", intake_id),
            partial(_inspect_intake, intake_id, organization_id),
        )
    )


def _get_executor() -> Executor:
    return UnicornAiThreadpoolExecutor(str(settings.LOW_PRIORITY_EXECUTOR_NAME))


def _submit_task(key: TaskKey, operation: Callable[[], None]) -> None:
    with _pending_lock:
        if key in _pending_tasks:
            # Preserve a wake-up received while an older revision is still preparing.
            _pending_tasks[key] = operation
            return
        if len(_pending_tasks) >= max(1, int(settings.MAX_GLOBAL_WORKERS)):
            # The durable job remains queued; its next authorized progress read retries.
            return
        _pending_tasks[key] = None
    try:
        future = _get_executor().submit(operation)
    except Exception as error:
        # Executor admission must never turn a committed proposal into an HTTP failure.
        with _pending_lock:
            _pending_tasks.pop(key, None)
        _logger.warning(
            "Dataset background submission failed: kind=%s id=%s error=%s",
            *key,
            type(error).__name__,
        )
        return
    future.add_done_callback(partial(_finish_task, key))


def _finish_task(key: TaskKey, future: Future[None]) -> None:
    with _pending_lock:
        reschedule = _pending_tasks.pop(key, None)
    if not future.cancelled() and (error := future.exception()) is not None:
        _logger.warning(
            "Dataset background execution failed: kind=%s id=%s error=%s",
            *key,
            type(error).__name__,
        )
    if reschedule is not None:
        _submit_task(key, reschedule)


def _prepare_dataset_change(change_id: UUID) -> None:
    # The worker reuses DatasetChangeService, which imports this scheduling boundary.
    from resource_registry.management_domain.worker import DatasetChangeWorker  # noqa: PLC0415

    DatasetChangeWorker().run_once(batch_size=1, change_id=change_id)


def _inspect_intake(intake_id: UUID, organization_id: int) -> None:
    if not DatasetManagementConfiguration.objects.filter(
        organization_id=organization_id, enabled=True
    ).exists():
        return
    StagedResourceIntakeInspectionService(
        storage_factory=DataRegistryStorageFactory(), context_builder=DatasetContextBuilder()
    ).run_jobs(batch_size=1, intake_id=intake_id)
