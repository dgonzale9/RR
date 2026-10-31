"""Verify Azure deletion preconditions with simulated blob I/O only."""

from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock
from urllib.parse import urlparse

from azure.core import MatchConditions
from django.utils import timezone
from pytest_django.fixtures import SettingsWrapper

from resource_registry.cleanup.worker import CleanupWorker
from resource_registry.management_domain.changes import DatasetChangeService
from resource_registry.models import DatasetManagementConfiguration
from resource_registry.tests.test_dataset_candidate_integrity import (  # noqa: F401
    AzureCandidateStorage,
    _prepare_azure_candidate,
    azure_candidate_storage,
)
from resource_registry.tests.test_dataset_maintenance_flows import Scope
from resource_registry.tests.test_dataset_management import management_scope  # noqa: F401
from resource_registry.tests.test_resource_cleanup import request_dataset_cleanup


def test_azure_purge_uses_exact_etags_and_retains_recent_original(
    management_scope: Scope,
    tmp_path: Path,
    settings: SettingsWrapper,
    azure_candidate_storage: AzureCandidateStorage,
) -> None:
    """Delete only reviewed dataset copies using conditional requests to the simulated provider."""
    settings.RESOURCE_REGISTRY_LOCAL_STORAGE_DIR = str(tmp_path)
    manager, _, organization, _, dataset = management_scope
    storage = azure_candidate_storage
    change = _prepare_azure_candidate(management_scope, tmp_path / "source.csv", storage)
    DatasetChangeService(manager, organization).publish(
        change.pk, expected_revision=change.revision
    )
    dataset.refresh_from_db()
    dataset.status = "ARCHIVED"
    dataset.archived_at = timezone.now() - timedelta(days=31)
    dataset.save()
    DatasetManagementConfiguration.objects.update(cleanup_mode="EXECUTE")
    client = storage.provider._blob_service_client.get_container_client.return_value
    get_client = client.get_blob_client.side_effect
    deleted = []

    def conditional_client(key: str) -> MagicMock:
        blob = get_client(key)

        def delete_blob(*, etag: str, match_condition: MatchConditions, timeout: int) -> None:
            assert etag == storage.etags[key]
            assert match_condition == MatchConditions.IfNotModified
            assert timeout == 60
            storage.objects.pop(key)
            deleted.append(key)

        blob.delete_blob.side_effect = delete_blob
        return blob

    client.get_blob_client.side_effect = conditional_client
    request = request_dataset_cleanup(management_scope, dataset)
    CleanupWorker().run_once()
    request.refresh_from_db()
    assert request.status == "PARTIAL"
    assert len(deleted) == 2
    for item in change.prepared["items"]:
        assert urlparse(item["uri"]).path.lstrip("/") not in storage.objects
    assert change.intake.storage_object_key in storage.objects
