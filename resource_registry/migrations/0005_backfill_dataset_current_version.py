"""Initialize current versions without granting new permissions or rewriting history."""

from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor


def initialize_dataset_management(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Backfill usable current versions and valid responsible users in bounded batches."""
    dataset_model = apps.get_model("resource_registry", "Dataset")
    version_model = apps.get_model("resource_registry", "DatasetVersion")
    membership_model = apps.get_model("organization", "WorkspaceMembership")
    organization_membership = apps.get_model("organization", "OrganizationMembership")
    assignment_model = apps.get_model("resource_registry", "StagedResourcePublicationDataset")
    alias = schema_editor.connection.alias
    for dataset in dataset_model.objects.using(alias).iterator(chunk_size=500):
        current_id = (
            version_model.objects.using(alias)
            .filter(
                dataset_id=dataset.pk,
                status="READY",
                artifacts__lifecycle_status="ACTIVE",
            )
            .order_by("-version_number")
            .values_list("id", flat=True)
            .first()
        )
        responsible_id = None
        if dataset.created_by_id:
            memberships = (
                membership_model.objects.using(alias).filter(
                    workspace_id=dataset.workspace_id,
                    user_id=dataset.created_by_id,
                    user__is_active=True,
                )
                if dataset.workspace_id
                else organization_membership.objects.using(alias).filter(
                    organization_id=dataset.organization_id,
                    user_id=dataset.created_by_id,
                    user__is_active=True,
                )
            )
            if memberships.exists():
                responsible_id = dataset.created_by_id
        archive_reason = ""
        if dataset.archived_at:
            assignments = assignment_model.objects.using(alias).filter(dataset_id=dataset.pk)
            archive_reason = (
                "PUBLICATION"
                if assignments.exists()
                and not assignments.filter(
                    detached_at__isnull=True, publication__assignment_status="ACTIVE"
                ).exists()
                else "MANUAL"
            )
        dataset_model.objects.using(alias).filter(pk=dataset.pk).update(
            current_version_id=current_id,
            responsible_user_id=responsible_id,
            archive_reason=archive_reason,
        )
    initialize_unambiguous_workbooks(apps, schema_editor)


def initialize_unambiguous_workbooks(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Link historical sheets only when current artifacts prove a single distinct mapping.

    Args:
        apps: Migration-state model registry.
        schema_editor: Database alias and schema context.
    """
    dataset_model = apps.get_model("resource_registry", "Dataset")
    group_model = apps.get_model("resource_registry", "WorkbookIngestionGroup")
    workbook_model = apps.get_model("resource_registry", "DatasetWorkbook")
    artifact_model = apps.get_model("resource_registry", "DatasetArtifact")
    namespace_model = apps.get_model("resource_registry", "RegistryNamespace")
    alias = schema_editor.connection.alias
    for group in group_model.objects.using(alias).iterator(chunk_size=200):
        namespace = namespace_model.objects.using(alias).filter(pk=group.tenant_id).first()
        if namespace is None:
            continue
        datasets = list(dataset_model.objects.using(alias).filter(workbook_group_id=group.pk))
        mapping = {}
        for dataset in datasets:
            if (
                dataset.tenant_id != group.tenant_id
                or dataset.source_type != "FILE_UPLOAD"
                or dataset.current_version_id is None
                or dataset.workspace_id != namespace.workspace_id
            ):
                break
            names = list(
                artifact_model.objects.using(alias)
                .filter(dataset_version_id=dataset.current_version_id, workbook_group_id=group.pk)
                .exclude(sheet_name__isnull=True)
                .exclude(sheet_name="")
                .values_list("sheet_name", flat=True)
                .distinct()
            )
            if len(names) != 1 or names[0] in mapping:
                break
            mapping[names[0]] = dataset.pk
        else:
            if mapping:
                workbook = workbook_model.objects.using(alias).create(
                    namespace_id=namespace.pk, name=group.source_file_name or "Historical workbook"
                )
                for sheet_name, dataset_id in mapping.items():
                    dataset_model.objects.using(alias).filter(pk=dataset_id).update(
                        workbook_id=workbook.pk, workbook_sheet=sheet_name
                    )


class Migration(migrations.Migration):
    dependencies = [("resource_registry", "0004_alter_registrynamespace_options_and_more")]
    operations = [migrations.RunPython(initialize_dataset_management, migrations.RunPython.noop)]
