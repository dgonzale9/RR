"""Exercise actual PostgreSQL object claims across independent worker connections."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Event
from unittest.mock import patch

import pytest
from django.db import close_old_connections
from django.utils import timezone
from pytest_django.fixtures import SettingsWrapper

from resource_registry.cleanup.worker import CleanupWorker
from resource_registry.models import Dataset, DatasetArtifact, DatasetVersion
from resource_registry.tests.test_dataset_maintenance_flows import Scope
from resource_registry.tests.test_dataset_management import management_scope  # noqa: F401
from resource_registry.tests.test_resource_cleanup import (
    create_archived_dataset_file,
    request_dataset_cleanup,
)


@pytest.mark.django_db(transaction=True)
def test_workers_share_one_physical_deletion_claim(
    management_scope: Scope, tmp_path: Path, settings: SettingsWrapper
) -> None:
    """Prevent two dataset requests from concurrently deleting a shared generation."""
    settings.RESOURCE_REGISTRY_LOCAL_STORAGE_DIR = str(tmp_path)
    dataset, path = create_archived_dataset_file(management_scope, tmp_path)
    sibling = Dataset.objects.create(
        tenant_id=dataset.tenant_id,
        organization=dataset.organization,
        workspace=dataset.workspace,
        name="Sibling",
        slug="sibling-concurrent",
        source_type="FILE_UPLOAD",
        status="ARCHIVED",
        archived_at=timezone.now() - timedelta(days=31),
    )
    version = DatasetVersion.objects.create(
        dataset=sibling,
        version_number=1,
        content_hash="sibling",
        source_uri=f"local://{path.name}",
        status="READY",
    )
    DatasetArtifact.objects.create(
        dataset_version=version,
        artifact_role="PRIMARY",
        uri=f"local://{path.name}",
        format="PARQUET",
        storage_provider="local",
        size_bytes=path.stat().st_size,
    )
    first = request_dataset_cleanup(management_scope, dataset)
    second = request_dataset_cleanup(management_scope, sibling)
    worker = CleanupWorker()
    first_token = worker._claim_request(first.id)
    second_token = worker._claim_request(second.id)
    assert first_token is not None and second_token is not None
    entered, release = Event(), Event()
    original_delete = CleanupWorker._delete_physical_object

    def delayed_delete(storage_object: object) -> None:
        entered.set()
        assert release.wait(10)
        original_delete(storage_object)

    def run_first() -> None:
        close_old_connections()
        try:
            worker._delete_item(first.items.get().id, first_token)
        finally:
            close_old_connections()

    with (
        patch.object(
            CleanupWorker, "_delete_physical_object", side_effect=delayed_delete
        ) as deletion,
        ThreadPoolExecutor(max_workers=1) as executor,
    ):
        future = executor.submit(run_first)
        try:
            assert entered.wait(10)
            worker._delete_item(second.items.get().id, second_token)
            assert second.items.get().block_reason == "cleanup_in_progress"
            assert deletion.call_count == 1
        finally:
            release.set()
        future.result(timeout=10)
    worker._delete_item(second.items.get().id, second_token)
    assert first.items.get().status == second.items.get().status == "DELETED"
    assert not path.exists()
