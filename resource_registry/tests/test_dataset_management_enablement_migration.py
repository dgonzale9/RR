"""Verify automatic enablement against the historical PostgreSQL migration state."""

from collections.abc import Iterator
from importlib import import_module
from uuid import UUID, uuid4

import pytest
from django.apps.registry import Apps
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

PREVIOUS = ("resource_registry", "0013_distinguish_upload_authorization_leases")
TARGET = ("resource_registry", "0014_dataset_external_sources")


@pytest.fixture
def historical_apps(transactional_db: None) -> Iterator[Apps]:
    """Restore the previous schema and return its models without runtime signals."""
    assert connection.vendor == "postgresql"
    executor = MigrationExecutor(connection)
    executor.migrate([PREVIOUS])
    apps = executor.loader.project_state([PREVIOUS]).apps
    try:
        yield apps
    finally:
        for model_name in ("DatasetRegistrationJob", "WorkbookIngestionJob"):
            apps.get_model("resource_registry", model_name).objects.update(status="succeeded")
        MigrationExecutor(connection).migrate([TARGET])


def create_scope(apps: Apps, *, enabled: bool = True, workspace: bool = False) -> tuple[int, UUID]:
    """Create an organization and namespace using historical models only."""
    identity = str(uuid4())
    user = apps.get_model("auth", "User").objects.create(username=identity)
    organization = apps.get_model("organization", "Organization").objects.create(
        name=identity,
        slug=identity,
        resource_registry_enabled=enabled,
        last_modified_by_id=user.pk,
        created=timezone.now(),
        modified=timezone.now(),
    )
    namespace_model = apps.get_model("resource_registry", "RegistryNamespace")
    if workspace:
        owner = apps.get_model("organization", "Workspace").objects.create(
            organization_id=organization.pk,
            name=identity,
            slug=identity,
            resource_registry_enabled=True,
            last_modified_by_id=user.pk,
            created=timezone.now(),
            modified=timezone.now(),
        )
        namespace = namespace_model.objects.create(id=uuid4(), workspace_id=owner.pk)
    else:
        namespace = namespace_model.objects.create(id=uuid4(), organization_id=organization.pk)
    return organization.pk, namespace.pk


def create_legacy_job(apps: Apps, namespace_id: UUID, model_name: str, status: str) -> None:
    """Persist either legacy job type without contacting upload or storage providers."""
    job_model = apps.get_model("resource_registry", model_name)
    if model_name == "WorkbookIngestionJob":
        job_model.objects.create(tenant_id=namespace_id, status=status)
        return
    namespace = apps.get_model("resource_registry", "RegistryNamespace").objects.get(
        pk=namespace_id
    )
    organization_id = namespace.organization_id or namespace.workspace.organization_id
    upload = apps.get_model("resource_registry", "LargeFileUploadSession").objects.create(
        organization_id=organization_id,
        workspace_id=namespace.workspace_id,
        registry_namespace_id=namespace_id,
        declared_size_bytes=1,
        storage_object_key=str(uuid4()),
        expires_at=timezone.now(),
        upload_authorization_expires_at=timezone.now(),
    )
    submission = apps.get_model("resource_registry", "RegistrationSubmission").objects.create(
        upload_session_id=upload.pk,
        organization_id=organization_id,
        workspace_id=namespace.workspace_id,
        registry_namespace_id=namespace_id,
        job_type="dataset_registration",
    )
    job_model.objects.create(
        tenant_id=namespace_id, registration_submission_id=submission.pk, status=status
    )


def test_migration_enables_only_resource_registry_organizations(historical_apps: Apps) -> None:
    """Create configurations for both destination types without enabling other tenants."""
    organization_id, _ = create_scope(historical_apps)
    workspace_organization_id, _ = create_scope(historical_apps, workspace=True)
    disabled_id, _ = create_scope(historical_apps, enabled=False)
    MigrationExecutor(connection).migrate([TARGET])
    configuration = historical_apps.get_model("resource_registry", "DatasetManagementConfiguration")
    for scope_id in (organization_id, workspace_organization_id):
        result = configuration.objects.get(organization_id=scope_id)
        assert result.enabled is True
        assert result.legacy_updates_blocked is True
        assert result.cleanup_mode == "SIMULATE"
        assert result.dataset_recovery_days == 30
        assert result.intake_source_retention_days == 30
        assert result.discarded_candidate_retention_days == 7
    assert not configuration.objects.filter(organization_id=disabled_id).exists()


@pytest.mark.parametrize("cleanup_mode", ["DISABLED", "SIMULATE", "EXECUTE"])
def test_migration_preserves_existing_policy(historical_apps: Apps, cleanup_mode: str) -> None:
    """Enable existing disabled configurations without changing cleanup or retention."""
    organization_id, _ = create_scope(historical_apps)
    configuration = historical_apps.get_model("resource_registry", "DatasetManagementConfiguration")
    existing = configuration.objects.create(
        organization_id=organization_id,
        enabled=False,
        cleanup_mode=cleanup_mode,
        dataset_recovery_days=90,
        intake_source_retention_days=60,
        discarded_candidate_retention_days=14,
    )
    disabled_id, _ = create_scope(historical_apps, enabled=False)
    excluded = configuration.objects.create(organization_id=disabled_id, enabled=False)
    MigrationExecutor(connection).migrate([TARGET])
    existing.refresh_from_db()
    excluded.refresh_from_db()
    assert existing.enabled is True
    assert existing.legacy_updates_blocked is True
    assert existing.cleanup_mode == cleanup_mode
    assert existing.dataset_recovery_days == 90
    assert existing.intake_source_retention_days == 60
    assert existing.discarded_candidate_retention_days == 14
    assert excluded.enabled is False
    assert excluded.legacy_updates_blocked is False


@pytest.mark.parametrize("model_name", ["DatasetRegistrationJob", "WorkbookIngestionJob"])
@pytest.mark.parametrize("status", ["submitted", "queued", "running"])
@pytest.mark.parametrize("workspace", [False, True])
def test_pending_legacy_jobs_abort_atomically_and_allow_retry(
    historical_apps: Apps, model_name: str, status: str, workspace: bool
) -> None:
    """Refuse all enablement until legacy jobs finish, then migrate successfully."""
    idle_id, _ = create_scope(historical_apps)
    busy_id, namespace_id = create_scope(historical_apps, workspace=workspace)
    configuration = historical_apps.get_model("resource_registry", "DatasetManagementConfiguration")
    existing = configuration.objects.create(organization_id=idle_id, enabled=False)
    create_legacy_job(historical_apps, namespace_id, model_name, status)
    with pytest.raises(RuntimeError, match="Drain legacy registration and workbook jobs"):
        MigrationExecutor(connection).migrate([TARGET])
    existing.refresh_from_db()
    assert existing.enabled is False
    assert existing.legacy_updates_blocked is False
    assert not configuration.objects.filter(organization_id=busy_id).exists()
    assert TARGET not in MigrationExecutor(connection).loader.applied_migrations
    assert "resource_registry_datasetexternalsource" not in connection.introspection.table_names()
    historical_apps.get_model("resource_registry", model_name).objects.filter(
        tenant_id=namespace_id
    ).update(status="succeeded")
    MigrationExecutor(connection).migrate([TARGET])
    assert (
        configuration.objects.filter(
            organization_id__in=[idle_id, busy_id], enabled=True, legacy_updates_blocked=True
        ).count()
        == 2
    )


@pytest.mark.parametrize("model_name", ["DatasetRegistrationJob", "WorkbookIngestionJob"])
def test_finished_and_excluded_jobs_do_not_block_enablement(
    historical_apps: Apps, model_name: str
) -> None:
    """Ignore terminal jobs and jobs belonging to organizations without Resource Registry."""
    organization_id, namespace_id = create_scope(historical_apps)
    _, disabled_namespace_id = create_scope(historical_apps, enabled=False, workspace=True)
    for status in ("succeeded", "failed", "cancelled"):
        create_legacy_job(historical_apps, namespace_id, model_name, status)
    create_legacy_job(historical_apps, disabled_namespace_id, model_name, "running")
    MigrationExecutor(connection).migrate([TARGET])
    configuration = historical_apps.get_model("resource_registry", "DatasetManagementConfiguration")
    assert configuration.objects.get(organization_id=organization_id).enabled is True


def test_reapplication_is_idempotent_and_reversal_preserves_protection(
    historical_apps: Apps,
) -> None:
    """Keep one policy and the legacy write barrier through reversal and reapplication."""
    organization_id, _ = create_scope(historical_apps)
    configuration = historical_apps.get_model("resource_registry", "DatasetManagementConfiguration")
    MigrationExecutor(connection).migrate([TARGET])
    original = configuration.objects.get(organization_id=organization_id)
    MigrationExecutor(connection).migrate([PREVIOUS])
    original.refresh_from_db()
    assert original.enabled is True
    assert original.legacy_updates_blocked is True
    MigrationExecutor(connection).migrate([TARGET])
    assert list(
        configuration.objects.filter(organization_id=organization_id).values_list("pk", flat=True)
    ) == [original.pk]


def test_enablement_batches_queries_across_organizations(historical_apps: Apps) -> None:
    """Keep database round trips bounded instead of issuing queries for every organization."""
    migration = import_module("resource_registry.migrations.0014_dataset_external_sources")
    create_scope(historical_apps)
    with connection.schema_editor() as schema_editor:
        with CaptureQueriesContext(connection) as small:
            migration.enable_dataset_management(historical_apps, schema_editor)
        for _ in range(15):
            create_scope(historical_apps)
        with CaptureQueriesContext(connection) as large:
            migration.enable_dataset_management(historical_apps, schema_editor)
    assert len(large) <= len(small) + 1
