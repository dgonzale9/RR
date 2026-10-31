"""Remove Registry content while preserving identity and shared-workbook evidence."""

from __future__ import annotations

from django.utils import timezone

from resource_registry.cleanup.admission import collect_uuid_values
from resource_registry.cleanup.inventory import get_change_dataset_ids
from resource_registry.cleanup.protection import has_pending_intake_use
from resource_registry.models import (
    Dataset,
    DatasetArtifact,
    DatasetChange,
    DatasetLineage,
    DatasetProfile,
    DatasetRegistrationJob,
    DatasetSchema,
    DatasetWorkbook,
    StagedResourceIntake,
    StagedResourcePublication,
    StagedResourcePublicationDataset,
    WorkbookIngestionGroup,
    WorkbookIngestionJob,
)


def detach_dataset_publications(dataset: Dataset, *, actor_id: int | None, reason: str) -> None:
    """Retire only this dataset's assignments, leaving sibling sheets active."""
    publications = list(
        StagedResourcePublication.objects.filter(dataset_assignments__dataset=dataset)
        .distinct()
        .values_list("id", flat=True)
    )
    now = timezone.now()
    StagedResourcePublicationDataset.objects.filter(
        dataset=dataset, detached_at__isnull=True
    ).update(detached_at=now)
    for publication in StagedResourcePublication.objects.select_for_update().filter(
        pk__in=publications
    ):
        if not publication.dataset_assignments.filter(detached_at__isnull=True).exists():
            publication.assignment_status = "DETACHED"
            publication.detached_at = now
            publication.detached_by_id = actor_id
            publication.detachment_reason = reason
            publication.save(
                update_fields=[
                    "assignment_status",
                    "detached_at",
                    "detached_by",
                    "detachment_reason",
                ]
            )


def redact_dataset_content(dataset: Dataset) -> None:
    """Erase data-bearing payloads without cascading through publication or version identities."""
    DatasetSchema.objects.filter(dataset_version__dataset=dataset).update(schema_json=[])
    DatasetProfile.objects.filter(dataset_version__dataset=dataset).update(metrics_json={})
    dataset.versions.update(notes="")
    DatasetLineage.objects.filter(downstream_dataset_version__dataset=dataset).update(
        metadata_json={}, transformation_name=""
    )
    redact_ingestion_payloads(dataset)
    DatasetArtifact.objects.filter(dataset_version__dataset=dataset).update(metadata_json={})
    field_values: dict[str, dict[str, object]] = {
        "query_runs": {"sql_text": "", "metadata_json": {}},
        "query_jobs": {
            "sql_text": "",
            "result_columns": [],
            "result_rows": [],
            "error_message": "",
            "metadata_json": {},
        },
        "intelligence_jobs": {
            "request_payload_json": {},
            "job_spec_json": {},
            "decision_trace_json": {},
            "logical_result_json": {},
            "result_payload_json": {},
            "error_message": "",
            "metadata_json": {},
        },
        "analysis_artifacts": {
            "title": "Deleted analysis",
            "summary": "",
            "insights_json": [],
            "queries_used_json": [],
            "metadata_json": {},
        },
        "chart_artifacts": {
            "title": "Deleted chart",
            "chart_spec_json": {},
            "source_query_json": {},
            "metadata_json": {},
        },
        "export_plans": {"title": "Deleted export", "export_plan_json": {}, "metadata_json": {}},
        "generated_artifacts": {"filename": "deleted", "metadata_json": {}},
        "external_jobs": {"request_payload_json": {}, "error_message": ""},
    }
    for relation, fields in field_values.items():
        getattr(dataset, relation).update(**fields)
    dataset.management_activity.exclude(action__startswith="PURGE_").update(reason="")
    for change in DatasetChange.objects.filter(namespace_id=dataset.tenant_id):
        identifiers = get_change_dataset_ids(change)
        if str(dataset.id) not in identifiers:
            continue
        other_ids = identifiers - {str(dataset.id)}
        has_other_content = Dataset.objects.filter(
            pk__in=other_ids, purge_started_at__isnull=True
        ).exists()
        if has_other_content:
            for field in ("prepared", "specification"):
                payload = dict(getattr(change, field))
                for collection in ("items", "sheets"):
                    if collection in payload:
                        payload[collection] = [
                            (
                                {"dataset_id": str(dataset.id), "data_deleted": True}
                                if str(entry.get("dataset_id")) == str(dataset.id)
                                else entry
                            )
                            for entry in payload[collection]
                        ]
                setattr(change, field, payload)
            change.prepared["available_sheets"] = [
                name
                for name in change.prepared.get("available_sheets", [])
                if name != dataset.workbook_sheet
            ]
            change.prepared["suggested_sheets"] = [
                entry
                for entry in change.prepared.get("suggested_sheets", [])
                if str(entry.get("dataset_id")) != str(dataset.id)
            ]
        else:
            change.prepared = {}
            change.specification = {"dataset_id": str(dataset.id), "data_deleted": True}
        change.error_message = ""
        change.save(update_fields=["prepared", "specification", "error_message"])
    for intake in StagedResourceIntake.objects.filter(
        publications__dataset_assignments__dataset=dataset
    ).distinct():
        if has_pending_intake_use(intake.id):
            continue
        others = intake.publications.filter(
            dataset_assignments__dataset__purge_started_at__isnull=True
        ).values_list("dataset_assignments__dataset__workbook_sheet", flat=True)
        retained_sheets = set(others)
        if not retained_sheets:
            intake.inspection_json = {}
        elif dataset.workbook_sheet and dataset.workbook_sheet not in retained_sheets:
            intake.inspection_json = {
                **intake.inspection_json,
                "sheets": [
                    entry
                    for entry in intake.inspection_json.get("sheets", [])
                    if entry.get("name") != dataset.workbook_sheet
                ],
            }
        intake.save(update_fields=["inspection_json"])
    DatasetArtifact.objects.filter(dataset_version__dataset=dataset).update(sheet_name="")
    if (
        dataset.workbook_group_id
        and not Dataset.objects.filter(
            workbook_group_id=dataset.workbook_group_id, purge_started_at__isnull=True
        ).exists()
    ):
        WorkbookIngestionGroup.objects.filter(pk=dataset.workbook_group_id).update(
            source_file_name="Deleted workbook", metadata_json={}
        )
    dataset.name = f"Deleted dataset {dataset.id}"
    dataset.slug = f"deleted-{dataset.id}"
    dataset.description = ""
    dataset.owner_team = ""
    dataset.tags = []
    dataset.workbook_sheet = f"deleted-{dataset.id}" if dataset.workbook_id else ""
    dataset.save(
        update_fields=[
            "name",
            "slug",
            "description",
            "owner_team",
            "tags",
            "workbook_sheet",
            "updated_at",
        ]
    )
    if (
        dataset.workbook_id
        and not Dataset.objects.filter(
            workbook_id=dataset.workbook_id, purge_started_at__isnull=True
        ).exists()
    ):
        DatasetWorkbook.objects.filter(pk=dataset.workbook_id).update(
            name=f"Deleted workbook {dataset.workbook_id}"
        )


def redact_owned_payload(payload: object, dataset_id: str) -> object:
    """Redact only a dataset's entries inside a shared preparation or ingestion payload.

    Args:
        payload: Persisted JSON value.
        dataset_id: Dataset whose content is permanently retired.

    Returns:
        A payload preserving other datasets and minimal identity evidence.
    """
    if isinstance(payload, list):
        return [redact_owned_payload(value, dataset_id) for value in payload]
    if not isinstance(payload, dict):
        return payload
    if str(payload.get("dataset_id")) == dataset_id:
        retained = {
            key: value
            for key, value in payload.items()
            if key.endswith("_id")
            or key in {"version_number", "checksum", "content_hash", "size_bytes", "status"}
        }
        return {**retained, "data_deleted": True}
    return {key: redact_owned_payload(value, dataset_id) for key, value in payload.items()}


def redact_ingestion_payloads(dataset: Dataset) -> None:
    """Remove retired dataset payloads while preserving sibling ingestion results.

    Args:
        dataset: Locked dataset entering irreversible cleanup.
    """
    for model in (DatasetRegistrationJob, WorkbookIngestionJob):
        rows = model.objects.filter(tenant_id=dataset.tenant_id)
        for job in rows:
            payload = job.result_payload_json or {}
            identifiers = collect_uuid_values(payload)
            if dataset.id not in identifiers:
                continue
            has_retained = (
                Dataset.objects.filter(pk__in=identifiers, purge_started_at__isnull=True)
                .exclude(pk=dataset.pk)
                .exists()
            )
            job.result_payload_json = redact_owned_payload(payload, str(dataset.id))
            job.request_payload_json = (
                redact_owned_payload(job.request_payload_json, str(dataset.id))
                if has_retained
                else {}
            )
            job.error_message = ""
            job.save(update_fields=["result_payload_json", "request_payload_json", "error_message"])
