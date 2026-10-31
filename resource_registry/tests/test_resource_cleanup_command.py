"""Verify the scheduled cleanup entrypoint without touching real storage."""

from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone
from pytest_django.fixtures import SettingsWrapper

from resource_registry.models import DatasetManagementConfiguration, ResourceCleanupRequest
from resource_registry.tests.test_dataset_maintenance_flows import Scope
from resource_registry.tests.test_dataset_management import management_scope  # noqa: F401
from resource_registry.tests.test_resource_cleanup import (
    create_archived_dataset_file,
    request_dataset_cleanup,
)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("mode", ["DISABLED", "SIMULATE", "EXECUTE"])
def test_cleanup_command_preserves_organization_policy(
    management_scope: Scope, tmp_path: Path, settings: SettingsWrapper, mode: str
) -> None:
    """Run confirmed cleanup only when the organization enables EXECUTE mode.

    Args:
        management_scope: Authorized local dataset fixture.
        tmp_path: Disposable test storage.
        settings: Isolated Django settings overrides.
        mode: Organization cleanup policy under test.
    """
    settings.RESOURCE_REGISTRY_STORAGE_PROVIDER = "local"
    settings.RESOURCE_REGISTRY_LOCAL_STORAGE_DIR = str(tmp_path)
    settings.RESOURCE_REGISTRY_CLEANUP_COORDINATOR_ENABLED = True
    dataset, path = create_archived_dataset_file(management_scope, tmp_path)
    request = request_dataset_cleanup(management_scope, dataset)
    DatasetManagementConfiguration.objects.update(cleanup_mode=mode)
    ResourceCleanupRequest.objects.filter(pk=request.pk).update(next_attempt_at=timezone.now())

    call_command("run_resource_cleanup", batch_size=1, stdout=StringIO())

    dataset.refresh_from_db()
    request.refresh_from_db()
    if mode == "EXECUTE":
        assert not path.exists()
        assert dataset.purged_at is not None
        assert request.status == "COMPLETED"
    else:
        assert path.exists()
        assert dataset.purge_started_at is None
        assert request.status != "COMPLETED"


@pytest.mark.parametrize("batch_size", [0, -1, 101])
def test_cleanup_command_rejects_unbounded_batches(batch_size: int) -> None:
    """Reject invalid limits before performing any cleanup work.

    Args:
        batch_size: Invalid requested work limit.
    """
    with pytest.raises(CommandError, match="between 1 and 100"):
        call_command("run_resource_cleanup", batch_size=batch_size)


def test_cleanup_command_reports_cycle_failure() -> None:
    """Return failure to the scheduler when discovery or execution raises."""
    with patch(
        "resource_registry.cleanup.runtime.reconcile_historical_inventory",
        side_effect=RuntimeError("provider unavailable"),
    ):
        with pytest.raises(CommandError, match="Resource cleanup cycle failed"):
            call_command("run_resource_cleanup", batch_size=1)


def test_cleanup_command_respects_global_disable(settings: SettingsWrapper) -> None:
    """Leave discovery and deletion idle when cleanup is globally disabled.

    Args:
        settings: Isolated Django settings overrides.
    """
    settings.RESOURCE_REGISTRY_CLEANUP_COORDINATOR_ENABLED = False
    with patch("resource_registry.cleanup.runtime.reconcile_historical_inventory") as discover:
        call_command("run_resource_cleanup", batch_size=1, stdout=StringIO())
    discover.assert_not_called()
