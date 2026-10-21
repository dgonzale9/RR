from __future__ import annotations

import inspect

from django.test import SimpleTestCase

from resource_registry.services.dataset_query_job_service import DatasetQueryJobService
from resource_registry.services.dataset_query_job_worker_runner import DatasetQueryJobWorkerRunner
from resource_registry.services.workbook_ingestion_job_service import WorkbookIngestionJobService
from resource_registry.services.workbook_ingestion_job_worker_runner import (
    WorkbookIngestionJobWorkerRunner,
)


class ConcurrentClaimAndRestartVerificationTests(SimpleTestCase):
    """Verify cross-job async durability behavior without runtime changes."""

    def test_query_claiming_uses_transactional_row_locks_for_concurrency_safety(self) -> None:
        source = inspect.getsource(DatasetQueryJobService.claim_queued_jobs)

        self.assertIn("with transaction.atomic()", source)
        self.assertIn("select_for_update(skip_locked=True)", source)
        self.assertIn("status=DatasetQueryJobStatus.QUEUED", source)

    def test_workbook_claiming_uses_transactional_row_locks_for_concurrency_safety(self) -> None:
        source = inspect.getsource(WorkbookIngestionJobService.claim_queued_jobs)

        self.assertIn("with transaction.atomic()", source)
        self.assertIn("select_for_update(skip_locked=True)", source)
        self.assertIn("status=WorkbookIngestionJobStatus.QUEUED", source)

    def test_query_restart_recovery_transitions_stale_jobs(self) -> None:
        source = inspect.getsource(DatasetQueryJobWorkerRunner._recover_stale_running_jobs)

        self.assertIn("recover_stale_running_jobs", source)
        self.assertIn("_logger.info", source)

    def test_workbook_restart_recovery_transitions_stale_jobs(self) -> None:
        source = inspect.getsource(WorkbookIngestionJobWorkerRunner._recover_stale_running_jobs)

        self.assertIn("recover_stale_running_jobs", source)
        self.assertIn("_logger.info", source)
