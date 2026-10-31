"""Exercise policy automation and explicit cleanup contracts."""

from pathlib import Path
from uuid import uuid4

import pytest
from django.utils import timezone
from pytest_django.fixtures import SettingsWrapper

from resource_registry.cleanup.api import DeletionRequestSerializer
from resource_registry.cleanup.runtime import run_cleanup_cycle
from resource_registry.models import DatasetManagementConfiguration, ResourceCleanupRequest
from resource_registry.tests.test_dataset_maintenance_flows import Scope
from resource_registry.tests.test_dataset_management import management_scope  # noqa: F401
from resource_registry.tests.test_resource_cleanup import (
    create_archived_dataset_file,
    request_dataset_cleanup,
)


@pytest.mark.parametrize("field", ["uri", "organization_id", "storage_path", "execute_after"])
def test_deletion_body_rejects_client_storage_and_policy_overrides(field: str) -> None:
    """Accept only the manager's reviewed intent, never storage or scheduling authority."""
    serializer = DeletionRequestSerializer(
        data={
            "expected_revision": 0,
            "inventory_fingerprint": "0" * 64,
            "reason": "Retired",
            "idempotency_key": str(uuid4()),
            "confirmed": True,
            field: "client-controlled",
        }
    )
    assert not serializer.is_valid()


@pytest.mark.django_db(transaction=True)
def test_integrated_cycle_preserves_simulation_and_resumes_durable_work(
    management_scope: Scope, tmp_path: Path, settings: SettingsWrapper
) -> None:
    """Resume stored deletion intent through the same callback used by the web coordinator."""
    settings.RESOURCE_REGISTRY_STORAGE_PROVIDER = "local"
    settings.RESOURCE_REGISTRY_LOCAL_STORAGE_DIR = str(tmp_path)
    dataset, path = create_archived_dataset_file(management_scope, tmp_path)
    request = request_dataset_cleanup(management_scope, dataset)
    DatasetManagementConfiguration.objects.update(cleanup_mode="SIMULATE")
    run_cleanup_cycle()
    assert path.exists()
    dataset.refresh_from_db()
    assert dataset.purge_started_at is None
    DatasetManagementConfiguration.objects.update(cleanup_mode="EXECUTE")
    ResourceCleanupRequest.objects.filter(pk=request.pk).update(next_attempt_at=timezone.now())
    run_cleanup_cycle()
    request.refresh_from_db()
    assert request.status == "COMPLETED"
    assert not path.exists()
